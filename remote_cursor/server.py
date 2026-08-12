from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import sqlite3
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from .store import CursorStore


STATIC_DIR = Path(__file__).with_name("static")
ID_PATTERN = re.compile(r"^[A-Za-z0-9:_{}\-.,\"/]+$")


class RemoteCursorServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], store: CursorStore) -> None:
        super().__init__(address, RemoteCursorHandler)
        self.store = store
        self.allowed_users = {
            value.strip().casefold()
            for value in os.environ.get("REMOTE_CURSOR_ALLOWED_USERS", "").split(",")
            if value.strip()
        }


class RemoteCursorHandler(BaseHTTPRequestHandler):
    server: RemoteCursorServer
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:  # noqa: N802
        if not self._authorized():
            return self._json({"error": "This Tailscale identity is not allowed."}, status=HTTPStatus.FORBIDDEN)
        parsed = urlparse(self.path)
        if parsed.path == "/api/health":
            return self._json(self.server.store.health())
        if parsed.path == "/api/profile":
            return self._json(self.server.store.profile())
        if parsed.path == "/api/profile/avatar":
            avatar = self.server.store.profile_avatar()
            if avatar is None:
                return self._json({"error": "Profile avatar not found"}, status=HTTPStatus.NOT_FOUND)
            body, content_type = avatar
            return self._bytes(body, content_type=content_type)
        if parsed.path == "/api/conversations":
            params = parse_qs(parsed.query)
            try:
                payload = self.server.store.list_conversations(
                    query=params.get("q", [""])[0],
                    archived=params.get("archived", ["0"])[0] == "1",
                    limit=int(params.get("limit", ["500"])[0]),
                )
            except (sqlite3.Error, ValueError) as error:
                return self._json({"error": str(error)}, status=HTTPStatus.SERVICE_UNAVAILABLE)
            return self._json(payload)
        if parsed.path.startswith("/api/conversations/"):
            conversation_id = unquote(parsed.path.removeprefix("/api/conversations/"))
            if not conversation_id or not ID_PATTERN.fullmatch(conversation_id):
                return self._json({"error": "Invalid conversation id"}, status=HTTPStatus.BAD_REQUEST)
            try:
                payload = self.server.store.get_conversation(conversation_id)
            except sqlite3.Error as error:
                return self._json({"error": str(error)}, status=HTTPStatus.SERVICE_UNAVAILABLE)
            if payload is None:
                return self._json({"error": "Conversation not found"}, status=HTTPStatus.NOT_FOUND)
            return self._json(payload)
        if parsed.path == "/api/events":
            return self._events()
        return self._static(parsed.path)

    def do_POST(self) -> None:  # noqa: N802
        if not self._authorized():
            return self._json({"error": "This Tailscale identity is not allowed."}, status=HTTPStatus.FORBIDDEN)
        self._json(
            {"error": "Phase 1 is read-only. Message sending is intentionally unavailable."},
            status=HTTPStatus.METHOD_NOT_ALLOWED,
            extra_headers={"Allow": "GET"},
        )

    def do_PUT(self) -> None:  # noqa: N802
        self.do_POST()

    def do_DELETE(self) -> None:  # noqa: N802
        self.do_POST()

    def _events(self) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self._security_headers()
        self.end_headers()
        last_token = self.server.store.change_token()
        last_heartbeat = time.monotonic()
        try:
            self.wfile.write(b"retry: 2000\nevent: ready\ndata: {}\n\n")
            self.wfile.flush()
            while True:
                time.sleep(1)
                token = self.server.store.change_token()
                if token != last_token:
                    payload = json.dumps({"token": token}, separators=(",", ":")).encode()
                    self.wfile.write(b"event: change\ndata: " + payload + b"\n\n")
                    self.wfile.flush()
                    last_token = token
                elif time.monotonic() - last_heartbeat >= 15:
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
                    last_heartbeat = time.monotonic()
        except (BrokenPipeError, ConnectionResetError):
            return
        finally:
            self.close_connection = True

    def _static(self, requested_path: str) -> None:
        relative = requested_path.lstrip("/") or "index.html"
        candidate = (STATIC_DIR / relative).resolve()
        if STATIC_DIR.resolve() not in candidate.parents and candidate != STATIC_DIR.resolve():
            return self.send_error(HTTPStatus.NOT_FOUND)
        if not candidate.is_file():
            candidate = STATIC_DIR / "index.html"
        try:
            body = candidate.read_bytes()
        except OSError:
            return self.send_error(HTTPStatus.NOT_FOUND)
        mime_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", f"{mime_type}; charset=utf-8" if mime_type.startswith("text/") else mime_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache" if candidate.name == "index.html" else "public, max-age=3600")
        self._security_headers()
        self.end_headers()
        self.wfile.write(body)

    def _json(
        self,
        payload: Any,
        *,
        status: HTTPStatus = HTTPStatus.OK,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self._security_headers()
        self.end_headers()
        self.wfile.write(body)

    def _bytes(self, body: bytes, *, content_type: str) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "private, max-age=3600")
        self._security_headers()
        self.end_headers()
        self.wfile.write(body)

    def _security_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
        )

    def _authorized(self) -> bool:
        allowed = self.server.allowed_users
        if not allowed:
            return True
        login = self.headers.get("Tailscale-User-Login", "").strip().casefold()
        return bool(login and login in allowed)

    def log_message(self, format_string: str, *args: Any) -> None:
        print(f"[{self.log_date_time_string()}] {format_string % args}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only Cursor Agent Window web mirror")
    parser.add_argument("--host", default=os.environ.get("REMOTE_CURSOR_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("REMOTE_CURSOR_PORT", "4310")))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    store = CursorStore()
    health = store.health()
    if not health["ok"]:
        print("Warning: Cursor data is incomplete:")
        for missing in health["missing"]:
            print(f"  - {missing}")
    server = RemoteCursorServer((args.host, args.port), store)
    print(f"Remote Cursor Phase 1 listening at http://{args.host}:{args.port}")
    print("Read-only: no endpoint can send messages or mutate Cursor data.")
    if server.allowed_users:
        print(f"Tailscale identity allowlist enabled for {len(server.allowed_users)} user(s).")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        threading.Thread(target=server.shutdown, daemon=True).start()
        server.server_close()


if __name__ == "__main__":
    main()
