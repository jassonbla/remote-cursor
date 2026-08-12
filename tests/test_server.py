from __future__ import annotations

import http.client
import json
import threading
import unittest

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


class ServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = RemoteCursorServer(("127.0.0.1", 0), FakeStore())
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
