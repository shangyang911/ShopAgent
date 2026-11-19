import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).parents[1] / "ops" / "backup.py"


def load_backup():
    if not SCRIPT.is_file():
        raise AssertionError("Backup tooling has not been implemented")
    spec = importlib.util.spec_from_file_location("shopagent_backup", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def archive_fixture(path, extra=None, corrupted=False):
    payload = {
        "database.sql": b"CREATE TABLE orders (id INT);\n",
        "metadata.json": json.dumps({"format": 1, "database": "shopagent", "snapshot": {"tables": {}}}).encode(),
        "config/backend.env": b"SECRET=private\n",
        "config/compose.env": b"MYSQL_ROOT_PASSWORD=private\n",
        "config/nginx.conf": b"server {}\n",
        "uploads/example.txt": b"hello",
    }
    if extra:
        payload.update(extra)
    manifest = {name: hashlib.sha256(data).hexdigest() for name, data in payload.items()}
    payload["manifest.json"] = json.dumps(manifest).encode()
    if corrupted:
        payload["uploads/example.txt"] = b"changed"
    with tarfile.open(path, "w:gz") as tar:
        for name, data in payload.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.backup = load_backup()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.archive = self.root / "fixture.tar.gz"

    def test_valid_archive_verifies_every_payload_and_restores_snapshot(self):
        archive_fixture(self.archive)
        class Database:
            def restore_snapshot(inner, sql_path, expected=None):
                self.assertEqual(sql_path.read_bytes(), b"CREATE TABLE orders (id INT);\n")
                self.assertEqual(expected, {"tables": {}})
                return expected
        result = self.backup.verify_archive(self.archive, self.root, Database())
        self.assertTrue(result["verified"])
        self.assertEqual(result["files"], 6)

    def test_traversal_is_rejected_before_database_access(self):
        archive_fixture(self.archive, {"../escape": b"unsafe"})
        with self.assertRaisesRegex(ValueError, "member"):
            self.backup.verify_archive(self.archive, self.root, object())
        self.assertFalse((self.root.parent / "escape").exists())

    def test_symlink_member_is_rejected(self):
        archive_fixture(self.archive)
        with tarfile.open(self.archive, "r:gz") as source:
            members = [(info, source.extractfile(info).read()) for info in source.getmembers()]
        with tarfile.open(self.archive, "w:gz") as tar:
            for info, data in members:
                tar.addfile(info, io.BytesIO(data))
            link = tarfile.TarInfo("uploads/link")
            link.type = tarfile.SYMTYPE
            link.linkname = "/etc/shadow"
            tar.addfile(link)
        with self.assertRaisesRegex(ValueError, "member"):
            self.backup.verify_archive(self.archive, self.root, object())

    def test_corrupt_payload_is_rejected_before_database_access(self):
        archive_fixture(self.archive, corrupted=True)
        with self.assertRaisesRegex(ValueError, "hash"):
            self.backup.verify_archive(self.archive, self.root, object())

    def test_hashing_works_on_python310_without_file_digest(self):
        sample = self.root / "sample"
        sample.write_bytes(b"hello")
        with patch.object(hashlib, "file_digest", None, create=True):
            self.assertEqual(self.backup.sha256(sample), "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824")

    def test_manifest_must_cover_all_files(self):
        archive_fixture(self.archive)
        with tarfile.open(self.archive, "r:gz") as source:
            payload = {info.name: source.extractfile(info).read() for info in source.getmembers()}
        manifest = json.loads(payload["manifest.json"])
        del manifest["uploads/example.txt"]
        payload["manifest.json"] = json.dumps(manifest).encode()
        with tarfile.open(self.archive, "w:gz") as tar:
            for name, data in payload.items():
                info = tarfile.TarInfo(name)
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
        with self.assertRaisesRegex(ValueError, "manifest"):
            self.backup.verify_archive(self.archive, self.root, object())

    def test_metadata_must_be_an_object(self):
        archive_fixture(self.archive, {"metadata.json": b"[]"})
        with self.assertRaisesRegex(ValueError, "metadata"):
            self.backup.verify_archive(self.archive, self.root, object())

    def test_duplicate_members_are_rejected(self):
        archive_fixture(self.archive)
        data = self.archive.read_bytes()
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as source:
            members = [(info, source.extractfile(info).read()) for info in source.getmembers()]
        with tarfile.open(self.archive, "w:gz") as tar:
            for info, content in members + members[:1]:
                tar.addfile(info, io.BytesIO(content))
        with self.assertRaisesRegex(ValueError, "member"):
            self.backup.verify_archive(self.archive, self.root, object())

    def test_retention_never_removes_manual_or_unrecognized_names(self):
        directory = self.root / "backups" / "automatic"
        directory.mkdir(parents=True)
        for day in range(1, 17):
            (directory / f"shopagent-auto-202611{day:02}T021500Z-{'a' * 32}.tar.gz").write_bytes(b"backup")
        manual = directory / "manual-predeploy.tar.gz"
        manual.write_bytes(b"manual")
        strange = directory / "shopagent-auto-important.tar.gz"
        strange.write_bytes(b"manual")
        self.backup.prune_backups(directory)
        self.assertTrue(manual.exists())
        self.assertTrue(strange.exists())
        self.assertEqual(len(list(directory.glob("shopagent-auto-2026*.tar.gz"))), 14)

    def test_create_archive_contains_snapshot_config_uploads_and_digest(self):
        secrets = self.root / "secrets"
        secrets.mkdir()
        for name in ("backend.env", "compose.env", "nginx.conf"):
            (secrets / name).write_text("private\n")
        uploads = self.root / "data" / "uploads"
        uploads.mkdir(parents=True)
        (uploads / "photo.jpg").write_bytes(b"photo")
        class Database:
            def dump(inner, path):
                path.write_bytes(b"snapshot SQL")
            def restore_snapshot(inner, path, expected=None):
                return {"tables": {"orders": {"rows": 12}}}
        result = self.backup.create_backup(self.root, Database())
        output = Path(result["path"])
        self.assertEqual(result["sha256"], hashlib.sha256(output.read_bytes()).hexdigest())
        with tarfile.open(output) as tar:
            metadata = json.load(tar.extractfile("metadata.json"))
            self.assertEqual(metadata["snapshot"]["tables"]["orders"]["rows"], 12)
            self.assertEqual(tar.extractfile("uploads/photo.jpg").read(), b"photo")


class RestoreTests(unittest.TestCase):
    def setUp(self):
        self.backup = load_backup()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "secrets").mkdir()
        (self.root / "secrets" / "compose.env").write_text("MYSQL_ROOT_PASSWORD=do-not-print\n")
        self.sql = self.root / "dump.sql"
        self.sql.write_text("CREATE TABLE orders (id INT);")

    def test_import_failure_always_drops_isolated_database_and_user(self):
        db = self.backup.Database(self.root)
        commands = []
        def mysql(sql=None, **kwargs):
            commands.append((sql, kwargs))
            if kwargs.get("source"):
                self.assertNotEqual(kwargs["user"], "root")
                raise RuntimeError("import failed")
            return ""
        with patch.object(db, "mysql", side_effect=mysql):
            with self.assertRaisesRegex(RuntimeError, "import failed"):
                db.restore_snapshot(self.sql)
        statements = [sql for sql, _ in commands if sql]
        self.assertTrue(any(sql.startswith("DROP DATABASE IF EXISTS `shopagent_restorecheck_") for sql in statements))
        self.assertTrue(any(sql.startswith("DROP USER IF EXISTS 'sa_verify_") for sql in statements))
        self.assertFalse(any(sql.startswith("DROP DATABASE IF EXISTS `shopagent`") for sql in statements))

    def test_schema_or_count_mismatch_still_cleans_up(self):
        db = self.backup.Database(self.root)
        statements = []
        with patch.object(db, "mysql", side_effect=lambda sql=None, **kw: statements.append(sql) or ""), patch.object(db, "snapshot", return_value={"tables": {"orders": {"rows": 11}}}):
            with self.assertRaisesRegex(ValueError, "snapshot"):
                db.restore_snapshot(self.sql, {"tables": {"orders": {"rows": 12}}})
        self.assertTrue(any(sql and sql.startswith("DROP DATABASE") for sql in statements))
        self.assertTrue(any(sql and sql.startswith("DROP USER") for sql in statements))

    def test_drop_user_is_attempted_even_when_drop_database_fails(self):
        db = self.backup.Database(self.root)
        statements = []
        def mysql(sql=None, **kwargs):
            statements.append(sql)
            if sql and sql.startswith("DROP DATABASE"):
                raise RuntimeError("drop failed")
            return ""
        with patch.object(db, "mysql", side_effect=mysql), patch.object(db, "snapshot", return_value={"tables": {}}):
            with self.assertRaisesRegex(RuntimeError, "drop failed"):
                db.restore_snapshot(self.sql)
        self.assertTrue(any(sql and sql.startswith("DROP USER") for sql in statements))

    def test_mysql_password_is_environment_only_and_subprocess_errors_redacted(self):
        db = self.backup.Database(self.root)
        import subprocess
        def failed(command, **kwargs):
            self.assertNotIn("do-not-print", " ".join(command))
            self.assertEqual(kwargs["env"]["MYSQL_PWD"], "do-not-print")
            raise subprocess.CalledProcessError(1, command, stderr=b"do-not-print")
        with patch.object(self.backup.subprocess, "run", side_effect=failed):
            with self.assertRaises(RuntimeError) as caught:
                db.mysql("SELECT 1")
        self.assertNotIn("do-not-print", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
