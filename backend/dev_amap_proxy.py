"""Local Windows development proxy for AMap Web and JS API requests.

Reads the AMap JavaScript security code from the local, git-ignored Nginx
configuration and listens only on 127.0.0.1. Production deployments should
continue to use Nginx.
"""
from __future__ import annotations

import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

import httpx


BACKEND_DIR = Path(__file__).resolve().parent
PROXY_CONFIG = BACKEND_DIR / "deploy" / "nginx" / "amap-proxy.conf"
LISTEN_HOST = "127.0.0.1"
LISTEN_PORT = 8080
ALLOWED_ORIGINS = {"http://localhost:5173", "http://127.0.0.1:5173"}


def read_security_code() -> str:
    if not PROXY_CONFIG.is_file():
        raise RuntimeError("Local AMap proxy config not found: deploy/nginx/amap-proxy.conf")
    content = PROXY_CONFIG.read_text(encoding="utf-8")
    match = re.search(r"jscode=([^\";\s]+)", content)
    if not match or match.group(1).startswith("REPLACE_"):
        raise RuntimeError("Set the AMap security code in the local Nginx proxy config first.")
    return match.group(1)


SECURITY_CODE = read_security_code()


class AMapProxyHandler(BaseHTTPRequestHandler):
    def _cors_headers(self) -> None:
        origin = self.headers.get("Origin", "")
        if origin in ALLOWED_ORIGINS:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "*")

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._cors_headers()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlsplit(self.path)
        prefix = "/_AMapService/"
        if not parsed.path.startswith(prefix):
            self._send_json(404, {"error": "Not found"})
            return

        api_path = parsed.path[len(prefix):]
        host = "webapi.amap.com" if api_path.startswith("v4/map/styles") else "restapi.amap.com"
        params = dict(parse_qsl(parsed.query, keep_blank_values=True))
        params["jscode"] = SECURITY_CODE
        try:
            response = httpx.get(
                f"https://{host}/{api_path}",
                params=params,
                timeout=httpx.Timeout(20.0, connect=5.0),
            )
            body = response.content
            content_type = response.headers.get("content-type", "application/json")
            self.send_response(response.status_code)
        except httpx.HTTPError:
            body = json.dumps({"status": "0", "info": "AMap proxy upstream unavailable", "infocode": "LOCAL_PROXY_UPSTREAM_ERROR"}).encode()
            content_type = "application/json"
            self.send_response(502)

        self._cors_headers()
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, status_code: int, payload: dict[str, str]) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status_code)
        self._cors_headers()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        # Request URLs contain browser-visible AMap keys; never log query strings.
        return


if __name__ == "__main__":
    server = ThreadingHTTPServer((LISTEN_HOST, LISTEN_PORT), AMapProxyHandler)
    print(f"AMap local proxy listening on http://{LISTEN_HOST}:{LISTEN_PORT}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
