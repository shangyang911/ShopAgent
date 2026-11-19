#!/usr/bin/env python3
"""Local MySQL/config/uploads snapshots and isolated restore drills (stdlib only)."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import subprocess
import tarfile
import tempfile
import uuid


ARCHIVE_NAME = re.compile(r"shopagent-auto-\d{8}T\d{6}Z-[a-f0-9]{32}\.tar\.gz\Z")
CONFIG_FILES = ("backend.env", "compose.env", "nginx.conf")
DATABASE = "shopagent"
CONTAINER = "shopagent-db-1"


def sha256(path):
    with path.open("rb") as source:
        return stream_sha256(source)


def stream_sha256(source):
    digest = hashlib.sha256()
    for block in iter(lambda: source.read(1024 * 1024), b""):
        digest.update(block)
    return digest.hexdigest()


def private_directory(path):
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink():
        raise ValueError("Backup directory must not be a symlink")
    path.chmod(0o700)
    return path


def read_password(root):
    # Compose .env values are data; never source them through a shell.
    for line in (root / "secrets" / "compose.env").read_text().splitlines():
        key, separator, value = line.partition("=")
        if separator and key.strip() == "MYSQL_ROOT_PASSWORD":
            value = value.strip()
            if value[:1] in ("'", '"') and value[-1:] == value[:1]:
                value = value[1:-1]
            if value:
                return value
    raise ValueError("MYSQL_ROOT_PASSWORD is missing from secrets/compose.env")


class Database:
    def __init__(self, root):
        self.password = read_password(root)

    def run(self, arguments, password, **kwargs):
        environment = os.environ.copy()
        environment["MYSQL_PWD"] = password
        command = ["docker", "exec", "-i", "--env", "MYSQL_PWD", CONTAINER, *arguments]
        try:
            return subprocess.run(command, env=environment, stderr=subprocess.PIPE,
                                  check=True, timeout=1800, **kwargs)
        except (subprocess.SubprocessError, OSError):
            # Do not include argv, SQL, stderr, or environment in an exception.
            raise RuntimeError("Database backup/restore command failed; credentials and SQL redacted") from None

    def mysql(self, sql=None, *, source=None, database=None, user="root", password=None):
        arguments = ["mysql", "--protocol=socket", "--default-character-set=utf8mb4",
                     "--batch", "--raw", "--skip-column-names", "--binary-mode=1", f"--user={user}"]
        if database:
            arguments.append(f"--database={database}")
        options = {"stdout": subprocess.PIPE}
        if source is not None:
            with source.open("rb") as handle:
                result = self.run(arguments, password or self.password, stdin=handle, **options)
        else:
            result = self.run(arguments, password or self.password,
                              input=sql.encode("utf-8"), **options)
        return result.stdout.decode("utf-8")

    def dump(self, path):
        with path.open("wb") as target:
            self.run(["mysqldump", "--user=root", "--single-transaction", "--quick",
                      "--no-tablespaces", "--set-gtid-purged=OFF", "--hex-blob",
                      "--default-character-set=utf8mb4", DATABASE], self.password,
                     stdout=target)
        if path.stat().st_size == 0:
            raise ValueError("Database dump was empty")

    def snapshot(self, database, user, password):
        def query(sql):
            return self.mysql(sql, database=database, user=user, password=password)

        # Compare actual definitions and exact counts, not approximate TABLE_ROWS.
        names = query("SELECT JSON_ARRAY(TABLE_NAME,TABLE_TYPE,ENGINE) FROM "
                      "information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() ORDER BY TABLE_NAME")
        tables = {}
        for line in names.splitlines():
            name, kind, engine = json.loads(line)
            quoted = "`" + name.replace("`", "``") + "`"
            columns = query("SELECT JSON_ARRAY(COLUMN_NAME,COLUMN_TYPE,IS_NULLABLE,COLUMN_DEFAULT,"
                            "EXTRA,COLLATION_NAME,GENERATION_EXPRESSION) FROM information_schema.COLUMNS "
                            "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME="
                            "CONVERT(0x" + name.encode().hex() + " USING utf8mb4) ORDER BY ORDINAL_POSITION")
            indexes = query("SELECT JSON_ARRAY(INDEX_NAME,NON_UNIQUE,SEQ_IN_INDEX,COLUMN_NAME,"
                            "SUB_PART,INDEX_TYPE,EXPRESSION) FROM information_schema.STATISTICS "
                            "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME="
                            "CONVERT(0x" + name.encode().hex() + " USING utf8mb4) "
                            "ORDER BY INDEX_NAME,SEQ_IN_INDEX")
            tables[name] = {"kind": kind, "engine": engine,
                            "columns": [json.loads(row) for row in columns.splitlines()],
                            "indexes": [json.loads(row) for row in indexes.splitlines()],
                            "rows": int(query(f"SELECT COUNT(*) FROM {quoted}").strip())}
        return {"tables": tables}

    def restore_snapshot(self, path, expected=None):
        database = "shopagent_restorecheck_" + uuid.uuid4().hex
        user = "sa_verify_" + uuid.uuid4().hex[:20]
        password = secrets.token_hex(24)
        try:
            self.mysql(f"CREATE DATABASE `{database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci")
            self.mysql(f"CREATE USER '{user}'@'localhost' IDENTIFIED BY '{password}'")
            self.mysql(f"GRANT ALL PRIVILEGES ON `{database}`.* TO '{user}'@'localhost'")
            # Import with a temporary scoped account. Even malicious SQL cannot
            # select or mutate production tables or escape into server files.
            self.mysql(source=path, database=database, user=user, password=password)
            actual = self.snapshot(database, user, password)
            if expected is not None and actual != expected:
                raise ValueError("Restored database schema/row-count snapshot does not match metadata")
            return actual
        finally:
            # Both cleanups are attempted even if dropping the database fails.
            try:
                self.mysql(f"DROP DATABASE IF EXISTS `{database}`")
            finally:
                self.mysql(f"DROP USER IF EXISTS '{user}'@'localhost'")


def automatic_archives(directory):
    return sorted(path for path in directory.iterdir()
                  if ARCHIVE_NAME.fullmatch(path.name) and path.is_file() and not path.is_symlink())


def prune_backups(directory, keep=14):
    for archive in automatic_archives(directory)[:-keep]:
        archive.unlink()
        digest = archive.with_suffix(archive.suffix + ".sha256")
        if digest.is_file() and not digest.is_symlink():
            digest.unlink()


def create_backup(root, db=None):
    root = Path(root).resolve()
    private_directory(root / "backups")
    directory = private_directory(root / "backups" / "automatic")
    db = db or Database(root)
    timestamp = datetime.now(timezone.utc)
    name = f"shopagent-auto-{timestamp:%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex}.tar.gz"
    final = directory / name
    with tempfile.TemporaryDirectory(prefix=".snapshot-", dir=directory) as temporary:
        stage = Path(temporary)
        stage.chmod(0o700)
        sql = stage / "database.sql"
        db.dump(sql)
        # Derive metadata from the dump's transaction snapshot, not a second live
        # query racing application writes. This also proves the dump can restore.
        snapshot = db.restore_snapshot(sql)
        (stage / "config").mkdir()
        for config in CONFIG_FILES:
            source = root / "secrets" / config
            if source.is_symlink() or not source.is_file():
                raise ValueError("Configuration must be a regular file: " + config)
            shutil.copyfile(source, stage / "config" / config)
        uploads = root / "data" / "uploads"
        if not uploads.is_dir() or uploads.is_symlink():
            raise ValueError("Uploads directory is missing or is a symlink")
        for source in uploads.rglob("*"):
            if source.is_symlink() or not (source.is_file() or source.is_dir()):
                raise ValueError("Uploads must contain only regular files/directories")
            target = stage / "uploads" / source.relative_to(uploads)
            if source.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
        metadata = {"format": 1, "created_at": timestamp.isoformat(),
                    "database": DATABASE, "snapshot": snapshot}
        (stage / "metadata.json").write_text(json.dumps(metadata, sort_keys=True), encoding="utf-8")
        payload = sorted(path for path in stage.rglob("*") if path.is_file())
        manifest = {path.relative_to(stage).as_posix(): sha256(path) for path in payload}
        manifest_file = stage / "manifest.json"
        manifest_file.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
        pending = stage / "archive.tar.gz"
        with pending.open("xb") as target:
            pending.chmod(0o600)
            with tarfile.open(fileobj=target, mode="w:gz", compresslevel=1) as archive:
                for path in [*payload, manifest_file]:
                    archive.add(path, arcname=path.relative_to(stage).as_posix(), recursive=False)
            target.flush()
            os.fsync(target.fileno())
        with tarfile.open(pending, "r:gz") as archive:
            safe_members(archive)
        digest = sha256(pending)
        sidecar = stage / "archive.sha256"
        sidecar.write_text(digest + "\n", encoding="ascii")
        sidecar.chmod(0o600)
        # Publish the archive last; readers never observe a partially written tar.
        os.replace(sidecar, final.with_suffix(final.suffix + ".sha256"))
        os.replace(pending, final)
    prune_backups(directory)
    return {"path": str(final), "sha256": digest}


def safe_members(archive):
    members = {}
    for member in archive:
        path = PurePosixPath(member.name)
        allowed = (member.name in {"database.sql", "metadata.json", "manifest.json"}
                   or member.name in {"config/" + name for name in CONFIG_FILES}
                   or (len(path.parts) > 1 and path.parts[0] == "uploads"))
        if (not member.isfile() or not allowed or member.name in members
                or "\\" in member.name or ":" in member.name
                or path.is_absolute() or ".." in path.parts
                or path.as_posix() != member.name or member.size < 0):
            raise ValueError("Unsafe, duplicate, or unexpected archive member")
        members[member.name] = member
    required = {"database.sql", "metadata.json", "manifest.json", *("config/" + name for name in CONFIG_FILES)}
    if not required.issubset(members):
        raise ValueError("Archive is missing required members")
    return members


def read_json_member(archive, member, limit):
    if member.size > limit:
        raise ValueError("Archive JSON member is too large")
    with archive.extractfile(member) as source:
        return json.load(source)


def verify_archive(path, root, db=None):
    path = Path(path).resolve()
    root = Path(root).resolve()
    digest = sha256(path)
    sidecar = path.with_suffix(path.suffix + ".sha256")
    if sidecar.exists() and sidecar.read_text(encoding="ascii").strip() != digest:
        raise ValueError("Archive SHA256 hash does not match sidecar")
    with tempfile.TemporaryDirectory(prefix="shopagent-restorecheck-") as temporary:
        stage = Path(temporary)
        stage.chmod(0o700)
        sql = stage / "database.sql"
        with tarfile.open(path, "r:gz") as archive:
            members = safe_members(archive)
            manifest = read_json_member(archive, members["manifest.json"], 16 * 1024 * 1024)
            if not isinstance(manifest, dict) or set(manifest) != set(members) - {"manifest.json"}:
                raise ValueError("Archive manifest does not cover exactly every payload member")
            for name, expected in manifest.items():
                with archive.extractfile(members[name]) as source:
                    actual = stream_sha256(source)
                if actual != expected:
                    raise ValueError("Archive member hash mismatch: " + name)
            metadata = read_json_member(archive, members["metadata.json"], 8 * 1024 * 1024)
            if (not isinstance(metadata, dict) or metadata.get("format") != 1
                    or metadata.get("database") != DATABASE
                    or not isinstance(metadata.get("snapshot"), dict)):
                raise ValueError("Unsupported backup metadata")
            # No extract/extractall: only the validated SQL stream is copied to
            # a private, fixed filename. Uploads and secrets are hashed in place.
            with archive.extractfile(members["database.sql"]) as source, sql.open("wb") as target:
                shutil.copyfileobj(source, target)
        (db or Database(root)).restore_snapshot(sql, metadata["snapshot"])
    return {"verified": True, "path": str(path), "sha256": digest, "files": len(manifest)}


def status(root):
    directory = Path(root).resolve() / "backups" / "automatic"
    archives = automatic_archives(directory) if directory.is_dir() else []
    if not archives:
        return {"available": False, "offsite": False}
    latest = archives[-1]
    return {"available": True, "offsite": False, "path": str(latest),
            "sha256": sha256(latest), "size_bytes": latest.stat().st_size,
            "created_at": datetime.fromtimestamp(latest.stat().st_mtime, timezone.utc).isoformat(),
            "age_seconds": max(0, int(datetime.now(timezone.utc).timestamp() - latest.stat().st_mtime))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("create", "verify", "status"):
        command = commands.add_parser(name)
        command.add_argument("--root", type=Path, default=Path("/opt/shopagent"))
        if name == "verify":
            command.add_argument("--archive", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "create":
            result = create_backup(args.root)
        elif args.command == "verify":
            result = verify_archive(args.archive, args.root)
        else:
            result = status(args.root)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (OSError, ValueError, RuntimeError, tarfile.TarError):
        # Files may contain secrets, including configuration parsing errors.
        print(json.dumps({"error": "Backup operation failed; check prerequisites or archive integrity", "operation": args.command}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
