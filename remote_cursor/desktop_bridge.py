from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable


THREAD_STATUSES = {"idle", "running", "completed", "error", "unknown"}
THREAD_SOURCES = {"local", "cloud", "draft", "claude-code"}


class DesktopBridgeClient:
    """Read live Cursor Agent thread status through Cursor's local CLI bridge."""

    def __init__(
        self,
        *,
        cli_path: str | None = None,
        discovery_dir: str | Path | None = None,
        cache_seconds: float = 1.5,
        timeout_seconds: float = 5.0,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.cli_path = cli_path
        self.discovery_dir = Path(discovery_dir).expanduser() if discovery_dir else self._default_discovery_dir()
        self.cache_seconds = cache_seconds
        self.timeout_seconds = timeout_seconds
        self.runner = runner
        self.monotonic = monotonic
        self._lock = threading.Lock()
        self._cached_at = float("-inf")
        self._cached: dict[str, Any] | None = None

    def snapshot(self) -> dict[str, Any]:
        now = self.monotonic()
        with self._lock:
            if self._cached is not None and now - self._cached_at < self.cache_seconds:
                return copy.deepcopy(self._cached)
            snapshot = self._probe()
            self._cached = snapshot
            self._cached_at = now
            return copy.deepcopy(snapshot)

    def _probe(self) -> dict[str, Any]:
        if not self._has_discovery_file():
            return self._unavailable("bridge-disabled")

        cli_path = self.cli_path or self._find_cli()
        if cli_path is None:
            return self._unavailable("cli-not-found")

        try:
            result = self.runner(
                [cli_path, "desktop", "ls", "--json"],
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return self._unavailable("bridge-unavailable")

        try:
            payload = json.loads(result.stdout)
        except (json.JSONDecodeError, TypeError):
            return self._unavailable("bridge-unavailable")

        if result.returncode != 0 or not isinstance(payload, list):
            return self._unavailable("bridge-unavailable")

        threads: list[dict[str, Any]] = []
        for raw in payload:
            thread = self._normalize_thread(raw)
            if thread is None:
                return self._unavailable("unsupported-response")
            threads.append(thread)

        return {
            "available": True,
            "reason": None,
            "threads": threads,
        }

    @staticmethod
    def _normalize_thread(raw: Any) -> dict[str, Any] | None:
        if not isinstance(raw, dict):
            return None
        thread_id = raw.get("id")
        status = raw.get("status")
        source = raw.get("source")
        updated_at = raw.get("lastUpdatedAt")
        if not isinstance(thread_id, str) or not thread_id:
            return None
        if status not in THREAD_STATUSES or source not in THREAD_SOURCES:
            return None
        if not isinstance(updated_at, (int, float)) or isinstance(updated_at, bool):
            return None
        return {
            "id": thread_id,
            "status": status,
            "source": source,
            "lastUpdatedAt": updated_at,
        }

    @staticmethod
    def _unavailable(reason: str) -> dict[str, Any]:
        return {"available": False, "reason": reason, "threads": []}

    def _has_discovery_file(self) -> bool:
        try:
            return any(path.is_file() for path in self.discovery_dir.glob("*.json"))
        except OSError:
            return False

    @staticmethod
    def _default_discovery_dir() -> Path:
        override = os.environ.get("CURSOR_DESKTOP_BRIDGE_DIR")
        return Path(override).expanduser() if override else Path.home() / ".cursor" / "desktop-bridge"

    @staticmethod
    def _find_cli() -> str | None:
        override = os.environ.get("REMOTE_CURSOR_CLI")
        if override:
            candidate = Path(override).expanduser()
            return str(candidate) if candidate.is_file() else None

        if discovered := shutil.which("cursor"):
            return discovered

        candidates: list[Path] = []
        if sys.platform == "darwin":
            candidates.append(Path("/Applications/Cursor.app/Contents/Resources/app/bin/cursor"))
        elif sys.platform == "win32":
            local_app_data = os.environ.get("LOCALAPPDATA")
            if local_app_data:
                candidates.append(
                    Path(local_app_data) / "Programs" / "cursor" / "resources" / "app" / "bin" / "cursor.cmd"
                )
        else:
            candidates.extend(
                [
                    Path("/usr/share/cursor/resources/app/bin/cursor"),
                    Path("/opt/Cursor/resources/app/bin/cursor"),
                ]
            )

        return next((str(candidate) for candidate in candidates if candidate.is_file()), None)
