import importlib.util
import json
import os
from pathlib import Path
import ssl
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.error import URLError


OPS = Path(__file__).parents[1] / "ops"


def load_script(name):
    path = OPS / f"{name}.py"
    if not path.is_file():
        raise AssertionError(f"{name} tooling has not been implemented")
    spec = importlib.util.spec_from_file_location(f"shopagent_{name}", path)
    module = importlib.util.module_from_spec(spec)
    with patch.object(sys, "path", [str(OPS), *sys.path]):
        spec.loader.exec_module(module)
    return module


class SmokeTests(unittest.TestCase):
    def test_tls_failure_cannot_pass_smoke(self):
        smoke = load_script("smoke")
        with patch.object(smoke, "urlopen", side_effect=URLError(ssl.SSLCertVerificationError("invalid cert"))), patch.object(smoke, "check_websocket", return_value={"name": "websocket_auth", "ok": False, "detail": "tls_failure"}):
            report = smoke.run_checks("https://example.test")
        self.assertFalse(report["ok"])
        self.assertFalse(next(check for check in report["checks"] if check["name"] == "ready")["ok"])

    def test_public_plain_http_is_rejected(self):
        smoke = load_script("smoke")
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            smoke.run_checks("http://example.test")

    def test_missing_assets_cannot_pass_smoke(self):
        smoke = load_script("smoke")
        def response(url):
            if url.endswith("/api/health"):
                return 200, b'{"status":"ok","service":"e-commerce-api"}', "application/json"
            if url.endswith("/api/ready"):
                return 200, b'{"status":"ready"}', "application/json"
            if url.endswith("/api/auth/status"):
                return 200, b'{"setup_required":false}', "application/json"
            if url.endswith("/api/products"):
                return 401, b'{}', "application/json"
            if url.endswith("/"):
                return 200, b'<html><script type="module" src="/assets/app.js"></script></html>', "text/html"
            return 404, b'missing', "text/plain"
        with patch.object(smoke, "fetch", side_effect=response), patch.object(smoke, "check_websocket", return_value={"name": "websocket_auth", "ok": True, "detail": "HTTP 403"}):
            report = smoke.run_checks("https://example.test")
        self.assertFalse(report["ok"])
        self.assertFalse(next(check for check in report["checks"] if check["name"] == "asset:/assets/app.js")["ok"])


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.monitor = load_script("monitor")
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def backup(self, age=0):
        import hashlib
        directory = self.root / "backups" / "automatic"
        directory.mkdir(parents=True, exist_ok=True)
        archive = directory / ("shopagent-auto-20261119T021500Z-" + "a" * 32 + ".tar.gz")
        archive.write_bytes(b"verified archive")
        Path(str(archive) + ".sha256").write_text(hashlib.sha256(archive.read_bytes()).hexdigest() + "\n")
        os.utime(archive, (time.time() - age, time.time() - age))
        return archive

    def test_stale_backup_is_unhealthy(self):
        self.backup(age=31 * 3600)
        self.assertFalse(self.monitor.check_backup(self.root)["ok"])

    def test_incomplete_or_tampered_backup_is_unhealthy(self):
        archive = self.backup()
        archive.write_bytes(b"tampered")
        self.assertFalse(self.monitor.check_backup(self.root)["ok"])
        Path(str(archive) + ".sha256").unlink()
        self.assertFalse(self.monitor.check_backup(self.root)["ok"])

    def test_fresh_verified_backup_is_healthy(self):
        self.backup()
        self.assertTrue(self.monitor.check_backup(self.root)["ok"])

    def test_certificate_failure_is_unhealthy(self):
        with patch.object(self.monitor.socket, "create_connection", side_effect=ssl.SSLCertVerificationError("untrusted")):
            self.assertFalse(self.monitor.check_certificate("https://example.test")["ok"])

    def test_expiring_certificate_is_unhealthy(self):
        with patch.object(self.monitor, "certificate_expiry", return_value=time.time() + 47 * 3600):
            self.assertFalse(self.monitor.check_certificate("https://example.test")["ok"])

    def test_health_state_replaces_existing_file_and_keeps_private_mode(self):
        path = self.root / "state" / "health.json"
        self.monitor.write_state(path, {"ok": True})
        self.monitor.write_state(path, {"ok": False})
        self.assertEqual(json.loads(path.read_text()), {"ok": False})
        self.assertEqual(list(path.parent.glob("*.tmp")), [])
        if os.name != "nt":
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
