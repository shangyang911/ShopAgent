#!/usr/bin/env python3
"""Local ShopAgent health state only; never sends notifications or restarts services."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import ssl
import subprocess
import tempfile
import time
from urllib.parse import urlsplit

import smoke


def command(args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=20)
    if result.returncode:
        raise RuntimeError("command failed")
    return result.stdout.strip()


def certificate_expiry(base_url):
    parsed = urlsplit(base_url)
    with socket.create_connection((parsed.hostname, parsed.port or 443), timeout=10) as raw:
        with ssl.create_default_context().wrap_socket(raw, server_hostname=parsed.hostname) as stream:
            return ssl.cert_time_to_seconds(stream.getpeercert()["notAfter"])


def check_certificate(base_url):
    try:
        remaining = certificate_expiry(base_url) - time.time()
        return smoke.check("certificate", remaining > 48 * 3600, f"{int(remaining)} seconds remaining")
    except Exception as error:
        return smoke.check("certificate", False, type(error).__name__)


def check_backup(root):
    try:
        pattern = re.compile(r"shopagent-auto-\d{8}T\d{6}Z-[a-f0-9]{32}\.tar\.gz")
        archives = [path for path in (root / "backups" / "automatic").glob("*.tar.gz")
                    if path.is_file() and pattern.fullmatch(path.name)]
        if not archives:
            return smoke.check("backup", False, "No completed automatic backup")
        newest = max(archives, key=lambda path: path.stat().st_mtime)
        age = time.time() - newest.stat().st_mtime
        expected = Path(str(newest) + ".sha256").read_text(encoding="ascii").strip()
        if not re.fullmatch(r"[a-f0-9]{64}", expected):
            return smoke.check("backup", False, "Invalid SHA256 sidecar")
        digest = hashlib.sha256()
        with newest.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        ok = 0 <= age < 30 * 3600 and newest.stat().st_size > 0 and digest.hexdigest() == expected
        return smoke.check("backup", ok, f"{newest.name}; age {int(age)} seconds; SHA256 {'valid' if digest.hexdigest() == expected else 'invalid'}")
    except Exception as error:
        return smoke.check("backup", False, type(error).__name__)


def check_containers():
    checks = []
    for service in ("api", "web", "db"):
        try:
            state = json.loads(command(["docker", "inspect", "--format", "{{json .State}}", f"shopagent-{service}-1"]))
            health = state.get("Health", {}).get("Status", "missing")
            ok = state.get("Running") is True and health == "healthy"
            checks.append(smoke.check("container:" + service, ok, f"running={state.get('Running')}; health={health}"))
        except Exception as error:
            checks.append(smoke.check("container:" + service, False, type(error).__name__))
    return checks


def check_certbot():
    try:
        timer = "shopagent-certbot-renew.timer"
        enabled = command(["systemctl", "is-enabled", timer]) == "enabled"
        active = command(["systemctl", "is-active", timer]) == "active"
        state = command(["systemctl", "show", "shopagent-certbot-renew.service",
                         "--property=Result,ExecMainStatus,ExecMainExitTimestampMonotonic"])
        values = dict(line.split("=", 1) for line in state.splitlines() if "=" in line)
        passed = (values.get("Result") == "success" and values.get("ExecMainStatus") == "0"
                  and int(values.get("ExecMainExitTimestampMonotonic", "0")) > 0)
        return smoke.check("certbot", enabled and active and passed,
                           f"enabled={enabled}; active={active}; last_run_success={passed}")
    except Exception as error:
        return smoke.check("certbot", False, type(error).__name__)


def write_state(path, report):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=".health-", suffix=".tmp", dir=path.parent)
    try:
        os.chmod(temporary, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def run_checks(root, base_url):
    try:
        checks = smoke.run_checks(base_url)["checks"]
    except Exception as error:
        checks = [smoke.check("smoke", False, type(error).__name__)]
    checks.extend(check_containers())
    try:
        usage = shutil.disk_usage(root)
        fraction = usage.free / usage.total
        checks.append(smoke.check("disk", fraction > 0.15, f"{fraction:.1%} free"))
    except Exception as error:
        checks.append(smoke.check("disk", False, type(error).__name__))
    checks.extend((check_certificate(base_url), check_certbot(), check_backup(root)))
    return {"ok": all(item["ok"] for item in checks), "checked_at": datetime.now(timezone.utc).isoformat(),
            "checks": checks, "notifications": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("/opt/shopagent"))
    parser.add_argument("--base-url", default="https://123.57.2.244")
    args = parser.parse_args()
    report = run_checks(args.root, args.base_url)
    try:
        write_state(args.root / "state" / "health.json", report)
    except Exception as error:
        report["checks"].append(smoke.check("state_write", False, type(error).__name__))
        report["ok"] = False
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
