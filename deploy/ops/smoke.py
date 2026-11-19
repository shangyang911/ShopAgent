#!/usr/bin/env python3
"""Read-only public HTTPS deployment checks. No login or provider calls."""
import argparse
import base64
from datetime import datetime, timezone
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import socket
import ssl
from urllib.error import HTTPError
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, urlopen


TIMEOUT = 10


def check(name, ok, detail):
    return {"name": name, "ok": bool(ok), "detail": detail}


def fetch(url):
    request = Request(url, headers={"User-Agent": "ShopAgent-Smoke/1", "Accept": "*/*"})
    try:
        response = urlopen(request, timeout=TIMEOUT, context=ssl.create_default_context())
    except HTTPError as error:
        response = error
    with response:
        final = urlsplit(response.geturl())
        original = urlsplit(url)
        if final.scheme != "https" or final.netloc != original.netloc:
            raise ValueError("Unexpected redirect origin")
        return response.code, response.read(4 * 1024 * 1024), response.headers.get("Content-Type", "")


class Assets(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls = set()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get("src"):
            self.urls.add(attrs["src"])
        if tag == "link" and attrs.get("href"):
            self.urls.add(attrs["href"])


def check_websocket(base_url):
    """Exercise reverse-proxy WSS upgrade and anonymous rejection, without a turn."""
    parsed = urlsplit(base_url)
    try:
        with socket.create_connection((parsed.hostname, parsed.port or 443), timeout=TIMEOUT) as raw:
            with ssl.create_default_context().wrap_socket(raw, server_hostname=parsed.hostname) as stream:
                key = base64.b64encode(os.urandom(16)).decode("ascii")
                request = (
                    "GET /api/agent/ws HTTP/1.1\r\n"
                    f"Host: {parsed.netloc}\r\nOrigin: {base_url}\r\n"
                    "Connection: Upgrade\r\nUpgrade: websocket\r\n"
                    f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
                )
                stream.sendall(request.encode("ascii"))
                with stream.makefile("rb") as response:
                    line = response.readline(4096).decode("ascii", errors="replace").split()
                status = int(line[1]) if len(line) > 1 else 0
        return check("websocket_auth", status == 403, f"HTTP {status}")
    except Exception as error:
        return check("websocket_auth", False, type(error).__name__)


def run_checks(base_url):
    parsed = urlsplit(base_url)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.path not in ("", "/") or parsed.query or parsed.fragment):
        raise ValueError("Use an HTTPS origin without credentials, path, query or fragment")
    base_url = base_url.rstrip("/")
    checks = []
    contracts = (
        ("health", "/api/health", {"status": "ok", "service": "e-commerce-api"}),
        ("ready", "/api/ready", {"status": "ready"}),
        ("admin_initialized", "/api/auth/status", {"setup_required": False}),
    )
    for name, path, expected in contracts:
        try:
            status, body, content_type = fetch(base_url + path)
            payload = json.loads(body)
            ok = (status == 200 and "application/json" in content_type
                  and isinstance(payload, dict)
                  and all(payload.get(key) == value and type(payload.get(key)) is type(value)
                          for key, value in expected.items()))
            checks.append(check(name, ok, f"HTTP {status}; JSON contract {'valid' if ok else 'invalid'}"))
        except Exception as error:
            checks.append(check(name, False, type(error).__name__))
    try:
        status, _, _ = fetch(base_url + "/api/products")
        checks.append(check("anonymous_products", status == 401, f"HTTP {status}"))
    except Exception as error:
        checks.append(check("anonymous_products", False, type(error).__name__))
    try:
        status, body, content_type = fetch(base_url + "/")
        ok = status == 200 and "text/html" in content_type
        checks.append(check("index", ok, f"HTTP {status}"))
        assets = Assets()
        if ok:
            assets.feed(body.decode("utf-8"))
        urls = sorted({urljoin(base_url + "/", value) for value in assets.urls
                       if urlsplit(urljoin(base_url + "/", value)).netloc == parsed.netloc
                       and urlsplit(urljoin(base_url + "/", value)).scheme == "https"})
        checks.append(check("index_assets", bool(urls), f"{len(urls)} same-origin assets"))
        for url in urls:
            try:
                status, _, kind = fetch(url)
                checks.append(check("asset:" + urlsplit(url).path, status == 200 and "text/html" not in kind, f"HTTP {status}"))
            except Exception as error:
                checks.append(check("asset:" + urlsplit(url).path, False, type(error).__name__))
    except Exception as error:
        checks.append(check("index", False, type(error).__name__))
    checks.append(check_websocket(base_url))
    return {"ok": all(item["ok"] for item in checks),
            "checked_at": datetime.now(timezone.utc).isoformat(), "checks": checks}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        report = run_checks(args.base_url)
    except Exception as error:
        report = {"ok": False, "checks": [check("smoke", False, type(error).__name__)]}
    output = json.dumps(report, ensure_ascii=False, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output + "\n", encoding="utf-8")
    print(output)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
