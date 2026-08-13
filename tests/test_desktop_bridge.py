from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from remote_cursor.desktop_bridge import DesktopBridgeClient


class DesktopBridgeClientTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_directory.cleanup)
        self.discovery_dir = Path(self.temp_directory.name)
        (self.discovery_dir / "cursor.json").write_text("{}", encoding="utf-8")

    def test_returns_normalized_live_thread_statuses(self) -> None:
        calls: list[list[str]] = []

        def runner(command, **_kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps(
                    [
                        {
                            "id": "56e6602e-9397-4435-8036-f4d1df87e7e8",
                            "title": "Private title is not exposed",
                            "source": "local",
                            "status": "running",
                            "lastUpdatedAt": 1234,
                            "windowId": 7,
                            "instance": {"label": "Cursor"},
                        }
                    ]
                ),
                stderr="",
            )

        client = DesktopBridgeClient(cli_path="/cursor", discovery_dir=self.discovery_dir, runner=runner)
        snapshot = client.snapshot()

        self.assertTrue(snapshot["available"])
        self.assertEqual(
            snapshot["threads"],
            [
                {
                    "id": "56e6602e-9397-4435-8036-f4d1df87e7e8",
                    "source": "local",
                    "status": "running",
                    "lastUpdatedAt": 1234,
                }
            ],
        )
        self.assertEqual(calls, [["/cursor", "desktop", "ls", "--json"]])

    def test_caches_the_cli_probe(self) -> None:
        calls = 0

        def runner(command, **_kwargs):
            nonlocal calls
            calls += 1
            return subprocess.CompletedProcess(command, 0, stdout="[]", stderr="")

        client = DesktopBridgeClient(
            cli_path="/cursor",
            discovery_dir=self.discovery_dir,
            runner=runner,
            monotonic=lambda: 10.0,
        )

        self.assertTrue(client.snapshot()["available"])
        self.assertTrue(client.snapshot()["available"])
        self.assertEqual(calls, 1)

    def test_safely_degrades_on_an_unsupported_response(self) -> None:
        def runner(command, **_kwargs):
            return subprocess.CompletedProcess(
                command,
                0,
                stdout='[{"id":"thread","source":"local","status":"busy","lastUpdatedAt":1}]',
                stderr="",
            )

        snapshot = DesktopBridgeClient(
            cli_path="/cursor",
            discovery_dir=self.discovery_dir,
            runner=runner,
        ).snapshot()

        self.assertFalse(snapshot["available"])
        self.assertEqual(snapshot["reason"], "unsupported-response")
        self.assertEqual(snapshot["threads"], [])

    def test_does_not_expose_cli_error_output(self) -> None:
        def runner(command, **_kwargs):
            return subprocess.CompletedProcess(
                command,
                1,
                stdout='{"status":"error","message":"token-shaped private detail"}',
                stderr="private stderr",
            )

        snapshot = DesktopBridgeClient(
            cli_path="/cursor",
            discovery_dir=self.discovery_dir,
            runner=runner,
        ).snapshot()

        self.assertEqual(
            snapshot,
            {"available": False, "reason": "bridge-unavailable", "threads": []},
        )

    def test_does_not_launch_cursor_without_bridge_discovery(self) -> None:
        calls = 0

        def runner(command, **_kwargs):
            nonlocal calls
            calls += 1
            return subprocess.CompletedProcess(command, 0, stdout="[]", stderr="")

        snapshot = DesktopBridgeClient(
            cli_path="/cursor",
            discovery_dir=self.discovery_dir / "missing",
            runner=runner,
        ).snapshot()

        self.assertEqual(snapshot["reason"], "bridge-disabled")
        self.assertEqual(calls, 0)


if __name__ == "__main__":
    unittest.main()
