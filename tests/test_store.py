from __future__ import annotations

import json
import sqlite3
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path

from remote_cursor.store import CursorPaths, CursorStore


class CursorStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.home = root / ".cursor"
        self.user_data = root / "Cursor/User"
        global_storage = self.user_data / "globalStorage"
        global_storage.mkdir(parents=True)
        self.home.mkdir()
        (self.home / "projects").mkdir()
        self.paths = CursorPaths(home=self.home, user_data=self.user_data)
        self._create_state_db()
        self._create_search_db()
        self.store = CursorStore(self.paths)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _create_state_db(self) -> None:
        with closing(sqlite3.connect(self.paths.global_state)) as connection:
            connection.execute("CREATE TABLE ItemTable (key TEXT PRIMARY KEY, value BLOB)")
            project = {
                "id": "project-1",
                "name": "Mirror Project",
                "workspace": {"uri": {"fsPath": "/workspace/mirror"}},
                "createdAt": 100,
                "lastUpdatedAt": 200,
                "isArchived": False,
            }
            values = {
                "glass.localAgentProjects.v1": json.dumps([project]),
                "glass.cloudAgentProjects.v1": "[]",
                "glass.localAgentProjectMembership.v1": json.dumps({"agent-1": "project-1"}),
                "glass.cloudAgentProjectMembership.v1": "{}",
                "cursor/glass.selectedAgent": "agent-1",
                "cursorAuth/cachedEmail": "owner@example.com",
                "cursorAuth/cachedScopedProfile": json.dumps(
                    {"displayName": "Owner", "pictureUrl": "https://cdn.example/avatar"}
                ),
                "cursorAuth/stripeMembershipType": "pro_plus",
                "cursor/glass.tabs.v2/workspace/state.json": json.dumps(
                    {
                        "workspaceTabs": {
                            "tab-1": {
                                "lastActiveTime": 300,
                                "props": {
                                    "ownerAgentId": "agent-1",
                                    "branchName": "feat/remote-branch",
                                    "prUrl": "https://github.com/example/mirror/pull/42",
                                    "prTitle": "Ship the mobile mirror",
                                    "prStatusIcon": "git-merge",
                                },
                            }
                        }
                    }
                ),
            }
            connection.executemany("INSERT INTO ItemTable VALUES (?, ?)", values.items())
            connection.commit()

    def _create_search_db(self) -> None:
        with closing(sqlite3.connect(self.paths.search_db)) as connection:
            connection.executescript(
                """
                CREATE TABLE conversations (
                    fts_rowid INTEGER PRIMARY KEY,
                    source TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    updated_at INTEGER NOT NULL,
                    is_archived INTEGER NOT NULL,
                    root_fingerprint TEXT,
                    cache_fingerprint TEXT
                );
                CREATE VIRTUAL TABLE conversation_fts USING fts5(title, body);
                """
            )
            connection.execute(
                "INSERT INTO conversations VALUES (1, 'local', '', 'agent-1', 'Build the mirror', 200, 0, 'root', NULL)"
            )
            connection.execute("INSERT INTO conversation_fts(rowid, title, body) VALUES (1, 'Build the mirror', 'hello cursor')")
            connection.commit()

    def _write_transcript(self) -> Path:
        directory = self.home / "projects/workspace/agent-transcripts/agent-1"
        directory.mkdir(parents=True)
        path = directory / "agent-1.jsonl"
        rows = [
            {
                "role": "user",
                "message": {
                    "content": [
                        {
                            "type": "text",
                            "text": "Review https://github.com/example/mirror/pull/42",
                        }
                    ]
                },
            },
            {
                "role": "assistant",
                "message": {
                    "content": [
                        {"type": "text", "text": "Hi"},
                        {"type": "tool_use", "name": "Read", "input": {"path": "README.md"}},
                    ]
                },
            },
            {"type": "turn_ended", "status": "success"},
        ]
        path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
        return path

    def test_health_is_read_only(self) -> None:
        health = self.store.health()
        self.assertTrue(health["ok"])
        self.assertTrue(health["readOnly"])
        self.assertEqual(health["phase"], 1)

    def test_profile_exposes_only_safe_fields_and_cached_avatar(self) -> None:
        cache_dir = self.user_data.parent / "Cache/Cache_Data"
        cache_dir.mkdir(parents=True)
        png = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\x00IEND\xaeB`\x82"
        (cache_dir / "avatar-cache").write_bytes(b"cache:https://cdn.example/avatar" + png + b"trailer")

        profile = self.store.profile()
        self.assertEqual(profile["email"], "owner@example.com")
        self.assertEqual(profile["displayName"], "Owner")
        self.assertEqual(profile["plan"], "Pro+ Plan")
        self.assertTrue(profile["hasAvatar"])
        self.assertNotIn("pictureUrl", profile)
        self.assertEqual(self.store.profile_avatar(), (png, "image/png"))

    def test_combines_sidebar_metadata_and_transcript(self) -> None:
        self._write_transcript()
        payload = self.store.list_conversations()
        self.assertEqual(payload["count"], 1)
        conversation = payload["conversations"][0]
        self.assertEqual(conversation["title"], "Build the mirror")
        self.assertEqual(conversation["project"]["name"], "Mirror Project")
        self.assertTrue(conversation["selected"])
        self.assertTrue(conversation["hasTranscript"])

    def test_returns_full_message_blocks(self) -> None:
        self._write_transcript()
        conversation = self.store.get_conversation("agent-1")
        self.assertIsNotNone(conversation)
        assert conversation is not None
        self.assertEqual(conversation["messageCount"], 2)
        self.assertEqual(conversation["messages"][1]["content"][1]["name"], "Read")
        self.assertTrue(conversation["messages"][1]["isFinal"])
        self.assertEqual(conversation["messages"][1]["turnStatus"], "success")
        self.assertEqual(conversation["branch"], "feat/remote-branch")
        self.assertEqual(
            conversation["pullRequests"],
            [
                {
                    "url": "https://github.com/example/mirror/pull/42",
                    "number": 42,
                    "repository": "example/mirror",
                    "title": "Ship the mobile mirror",
                    "status": "merged",
                }
            ],
        )
        self.assertTrue(conversation["readOnly"])

    def test_does_not_guess_pr_status_when_multiple_links_are_ambiguous(self) -> None:
        messages = [
            {
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "https://github.com/example/a/pull/1 "
                            "https://github.com/example/b/pull/2"
                        ),
                    }
                ]
            }
        ]
        records = [{"url": None, "statusIcon": "git-merge", "lastActiveTime": 300}]

        pull_requests = self.store._pull_requests(messages, records)

        self.assertEqual([item["status"] for item in pull_requests], ["unknown", "unknown"])

    def test_search_uses_fts_index(self) -> None:
        self._write_transcript()
        self.assertEqual(self.store.list_conversations(query="mirror")["count"], 1)
        self.assertEqual(self.store.list_conversations(query="missing")["count"], 0)

    def test_change_token_tracks_transcript_appends(self) -> None:
        transcript = self._write_transcript()
        before = self.store.change_token()
        time.sleep(0.002)
        with transcript.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"role": "assistant", "message": {"content": [{"type": "text", "text": "Update"}]}}) + "\n")
        after = self.store.change_token()
        self.assertNotEqual(before, after)

    def test_missing_transcript_is_explicit(self) -> None:
        payload = self.store.list_conversations()
        self.assertFalse(payload["conversations"][0]["hasTranscript"])
        conversation = self.store.get_conversation("agent-1")
        assert conversation is not None
        self.assertEqual(conversation["messages"], [])
        self.assertFalse(conversation["hasTranscript"])

    def test_hides_mcp_meta_tool_injection_but_preserves_normal_mentions(self) -> None:
        hidden = CursorStore._normalize_message(
            {
                "role": "user",
                "message": {
                    "content": [
                        {
                            "type": "text",
                            "text": "<mcp_meta_tools>\nInternal tool schema\n</mcp_meta_tools>",
                        }
                    ]
                },
            },
            1,
        )
        visible = CursorStore._normalize_message(
            {
                "role": "user",
                "message": {
                    "content": [
                        {
                            "type": "text",
                            "text": "Why is <mcp_meta_tools> visible?",
                        }
                    ]
                },
            },
            2,
        )

        self.assertIsNone(hidden)
        self.assertIsNotNone(visible)
        assert visible is not None
        self.assertEqual(visible["content"][0]["text"], "Why is <mcp_meta_tools> visible?")


if __name__ == "__main__":
    unittest.main()
