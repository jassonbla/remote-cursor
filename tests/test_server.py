from __future__ import annotations

import http.client
import json
import threading
import unittest
from pathlib import Path

from remote_cursor.server import RemoteCursorServer


class FakeStore:
    def health(self):
        return {"ok": True, "readOnly": True, "phase": 1}

    def list_conversations(self, **_kwargs):
        return {"conversations": [], "count": 0, "readOnly": True}

    def get_conversation(self, conversation_id):
        return {"id": conversation_id, "messages": [], "readOnly": True}

    def profile(self):
        return {
            "email": "owner@example.com",
            "displayName": "Owner",
            "plan": "Pro+ Plan",
            "hasAvatar": True,
            "readOnly": True,
        }

    def profile_avatar(self):
        return b"avatar-bytes", "image/png"

    def change_token(self):
        return "stable"


class FakeDesktopBridge:
    def snapshot(self):
        return {
            "available": True,
            "reason": None,
            "threads": [
                {
                    "id": "agent-1",
                    "source": "local",
                    "status": "running",
                    "lastUpdatedAt": 1234,
                }
            ],
        }


class FakeStoreWithPaths(FakeStore):
    class Paths:
        global_state = Path("/tmp/state.vscdb")
        search_db = Path("/tmp/conversation-search.db")
        projects_dir = Path("/tmp/projects")

    paths = Paths()

    def _get_transcript_index(self):
        return {"agent-1": Path("/tmp/projects/project/agent-transcripts/agent-1/agent-1.jsonl")}


class ServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = RemoteCursorServer(("127.0.0.1", 0), FakeStore(), FakeDesktopBridge())
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.port = cls.server.server_port

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def request(self, method: str, path: str):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=2)
        connection.request(method, path)
        response = connection.getresponse()
        body = response.read()
        headers = dict(response.getheaders())
        connection.close()
        return response.status, headers, body

    def request_with_headers(self, method: str, path: str, headers: dict[str, str]):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=2)
        connection.request(method, path, headers=headers)
        response = connection.getresponse()
        body = response.read()
        connection.close()
        return response.status, body

    def test_health_endpoint_and_security_headers(self) -> None:
        status, headers, body = self.request("GET", "/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["phase"], 1)
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")

    def test_static_app_is_served(self) -> None:
        status, _, body = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"Remote Cursor", body)

        status, headers, body = self.request("GET", "/vendor/codicons.svg")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "image/svg+xml")
        self.assertIn(b'id="layout-sidebar-left"', body)

    def test_profile_and_avatar_endpoints(self) -> None:
        status, _, body = self.request("GET", "/api/profile")
        self.assertEqual(status, 200)
        profile = json.loads(body)
        self.assertEqual(profile["email"], "owner@example.com")
        self.assertNotIn("accessToken", profile)

        status, headers, body = self.request("GET", "/api/profile/avatar")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "image/png")
        self.assertEqual(body, b"avatar-bytes")

    def test_agent_status_endpoint(self) -> None:
        status, headers, body = self.request("GET", "/api/agent-status")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Cache-Control"], "no-store")
        payload = json.loads(body)
        self.assertTrue(payload["available"])
        self.assertEqual(payload["threads"][0]["id"], "agent-1")
        self.assertEqual(payload["threads"][0]["status"], "running")

    def test_watches_existing_transcript_file_not_only_its_directory(self) -> None:
        server = RemoteCursorServer(("127.0.0.1", 0), FakeStoreWithPaths(), FakeDesktopBridge())
        try:
            watched = server._watch_paths()
            self.assertIn(Path("/tmp/projects/project/agent-transcripts/agent-1/agent-1.jsonl"), watched)
            self.assertNotIn(Path("/tmp/projects/project/agent-transcripts/agent-1"), watched)
        finally:
            server.server_close()

    def test_sse_delivers_a_broker_change_to_a_connected_client(self) -> None:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=2)
        connection.request("GET", "/api/events")
        response = connection.getresponse()
        self.assertEqual(response.status, 200)
        self.assertEqual(response.getheader("Content-Type"), "text/event-stream")

        # Drain the ready event sent before the handler starts waiting on the broker.
        self.assertEqual(response.fp.readline(), b"retry: 2000\n")
        self.assertEqual(response.fp.readline(), b"event: ready\n")
        self.assertIn(b'"version":2', response.fp.readline())
        self.assertEqual(response.fp.readline(), b"\n")
        self.assertEqual(response.fp.readline(), b"event: change\n")
        self.assertEqual(response.fp.readline(), b'data: {"reason":"connected"}\n')
        self.assertEqual(response.fp.readline(), b"\n")

        published = self.server.events.publish("data.changed", {"source": "test"})
        self.assertEqual(response.fp.readline(), f"id: {published.event_id}\n".encode())
        self.assertEqual(response.fp.readline(), b"event: data.changed\n")
        self.assertEqual(response.fp.readline(), b'data: {"source":"test"}\n')
        self.assertEqual(response.fp.readline(), b"\n")
        connection.close()

    def test_writes_are_rejected(self) -> None:
        status, headers, body = self.request("POST", "/api/conversations/agent-1")
        self.assertEqual(status, 405)
        self.assertEqual(headers["Allow"], "GET")
        self.assertIn("read-only", json.loads(body)["error"])

    def test_tailscale_identity_allowlist(self) -> None:
        self.server.allowed_users = {"owner@example.com"}
        try:
            status, _ = self.request_with_headers("GET", "/api/health", {})
            self.assertEqual(status, 403)
            status, body = self.request_with_headers(
                "GET", "/api/health", {"Tailscale-User-Login": "owner@example.com"}
            )
            self.assertEqual(status, 200)
            self.assertTrue(json.loads(body)["ok"])
        finally:
            self.server.allowed_users = set()


if __name__ == "__main__":
    unittest.main()
