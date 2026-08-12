from __future__ import annotations

import json
import os
import sqlite3
import threading
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CursorPaths:
    home: Path
    user_data: Path

    @classmethod
    def discover(cls) -> "CursorPaths":
        home = Path(os.environ.get("CURSOR_HOME", Path.home() / ".cursor")).expanduser()
        if override := os.environ.get("CURSOR_USER_DATA"):
            user_data = Path(override).expanduser()
        elif sys_platform() == "darwin":
            user_data = Path.home() / "Library/Application Support/Cursor/User"
        elif os.name == "nt":
            user_data = Path(os.environ.get("APPDATA", Path.home())) / "Cursor/User"
        else:
            user_data = Path.home() / ".config/Cursor/User"
        return cls(home=home, user_data=user_data)

    @property
    def global_state(self) -> Path:
        return self.user_data / "globalStorage/state.vscdb"

    @property
    def search_db(self) -> Path:
        return self.user_data / "globalStorage/conversation-search.db"

    @property
    def projects_dir(self) -> Path:
        return self.home / "projects"


def sys_platform() -> str:
    import sys

    return sys.platform


class CursorStore:
    """Combines Cursor's indexes and transcript files without writing to them."""

    def __init__(self, paths: CursorPaths | None = None) -> None:
        self.paths = paths or CursorPaths.discover()
        self._transcript_index: dict[str, Path] = {}
        self._transcript_stamp: tuple[int, int, int] | None = None
        self._transcript_lock = threading.Lock()
        self._profile_avatar_cache: tuple[bytes, str] | None | bool = False

    def health(self) -> dict[str, Any]:
        missing = [
            str(path)
            for path in (self.paths.global_state, self.paths.search_db, self.paths.projects_dir)
            if not path.exists()
        ]
        return {
            "ok": not missing,
            "readOnly": True,
            "phase": 1,
            "missing": missing,
            "cursorHome": str(self.paths.home),
            "cursorUserData": str(self.paths.user_data),
        }

    def profile(self) -> dict[str, Any]:
        email = self._item("cursorAuth/cachedEmail") or ""
        scoped_profile = self._json_item("cursorAuth/cachedScopedProfile", {})
        if not isinstance(scoped_profile, dict):
            scoped_profile = {}
        display_name = scoped_profile.get("displayName")
        picture_url = scoped_profile.get("pictureUrl")
        membership_type = self._item("cursorAuth/stripeMembershipType") or ""
        plan_labels = {
            "free": "Free Plan",
            "pro": "Pro Plan",
            "pro_plus": "Pro+ Plan",
            "business": "Business Plan",
            "enterprise": "Enterprise Plan",
        }
        return {
            "email": email if isinstance(email, str) else "",
            "displayName": display_name if isinstance(display_name, str) else "",
            "plan": plan_labels.get(membership_type, membership_type.replace("_", " ").title()),
            "hasAvatar": bool(isinstance(picture_url, str) and picture_url and self.profile_avatar()),
            "readOnly": True,
        }

    def profile_avatar(self) -> tuple[bytes, str] | None:
        if self._profile_avatar_cache is not False:
            return self._profile_avatar_cache
        scoped_profile = self._json_item("cursorAuth/cachedScopedProfile", {})
        picture_url = scoped_profile.get("pictureUrl") if isinstance(scoped_profile, dict) else None
        if not isinstance(picture_url, str) or not picture_url:
            self._profile_avatar_cache = None
            return None

        cache_dir = self.paths.user_data.parent / "Cache/Cache_Data"
        try:
            candidates = cache_dir.iterdir()
            url_bytes = picture_url.encode("utf-8")
            for path in candidates:
                if not path.is_file():
                    continue
                try:
                    data = path.read_bytes()
                except OSError:
                    continue
                url_position = data.find(url_bytes)
                if url_position < 0:
                    continue
                extracted = self._extract_image(data, url_position + len(url_bytes))
                if extracted is not None:
                    self._profile_avatar_cache = extracted
                    return extracted
        except OSError:
            pass
        self._profile_avatar_cache = None
        return None

    @staticmethod
    def _extract_image(data: bytes, start_at: int) -> tuple[bytes, str] | None:
        png_signature = b"\x89PNG\r\n\x1a\n"
        png_start = data.find(png_signature, start_at)
        if png_start >= 0:
            position = png_start + len(png_signature)
            while position + 12 <= len(data):
                length = int.from_bytes(data[position : position + 4], "big")
                chunk_end = position + 12 + length
                if chunk_end > len(data):
                    break
                chunk_type = data[position + 4 : position + 8]
                position = chunk_end
                if chunk_type == b"IEND":
                    return data[png_start:position], "image/png"

        jpeg_start = data.find(b"\xff\xd8\xff", start_at)
        if jpeg_start >= 0:
            jpeg_end = data.find(b"\xff\xd9", jpeg_start + 3)
            if jpeg_end >= 0:
                return data[jpeg_start : jpeg_end + 2], "image/jpeg"

        webp_start = data.find(b"RIFF", start_at)
        if webp_start >= 0 and data[webp_start + 8 : webp_start + 12] == b"WEBP":
            webp_size = int.from_bytes(data[webp_start + 4 : webp_start + 8], "little") + 8
            webp_end = webp_start + webp_size
            if webp_end <= len(data):
                return data[webp_start:webp_end], "image/webp"
        return None

    def list_conversations(
        self,
        *,
        query: str = "",
        archived: bool = False,
        limit: int = 500,
    ) -> dict[str, Any]:
        local_projects = self._projects("glass.localAgentProjects.v1")
        cloud_projects = self._projects("glass.cloudAgentProjects.v1")
        memberships = {
            **self._json_item("glass.localAgentProjectMembership.v1", {}),
            **self._json_item("glass.cloudAgentProjectMembership.v1", {}),
        }
        selected_id = self._item("cursor/glass.selectedAgent")

        rows = self._search_conversations(query=query, archived=archived, limit=limit)
        transcript_index = self._get_transcript_index()
        projects = {**cloud_projects, **local_projects}

        conversations: list[dict[str, Any]] = []
        for row in rows:
            conversation_id = row["id"]
            project_id = memberships.get(conversation_id)
            project = projects.get(project_id, {})
            transcript = transcript_index.get(conversation_id)
            conversations.append(
                {
                    "id": conversation_id,
                    "title": row["title"] or "Untitled agent",
                    "updatedAt": row["updated_at"],
                    "archived": bool(row["is_archived"]),
                    "source": row["source"],
                    "selected": conversation_id == selected_id,
                    "project": {
                        "id": project_id,
                        "name": project.get("name") or self._project_name_from_transcript(transcript),
                        "path": project.get("workspace", {}).get("uri", {}).get("fsPath"),
                    },
                    "hasTranscript": transcript is not None,
                }
            )

        return {
            "conversations": conversations,
            "count": len(conversations),
            "selectedId": selected_id,
            "readOnly": True,
        }

    def get_conversation(self, conversation_id: str) -> dict[str, Any] | None:
        transcript = self._get_transcript_index().get(conversation_id)
        metadata = self._conversation_metadata(conversation_id)
        if transcript is None and metadata is None:
            return None

        messages: list[dict[str, Any]] = []
        if transcript is not None:
            try:
                with transcript.open("r", encoding="utf-8") as handle:
                    for line_number, line in enumerate(handle, start=1):
                        try:
                            raw = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        normalized = self._normalize_message(raw, line_number)
                        if normalized is not None:
                            messages.append(normalized)
            except OSError:
                transcript = None

        meta_file = self._chat_meta(conversation_id)
        tab_metadata = self._session_tab_metadata(conversation_id)
        title = (metadata or {}).get("title") or meta_file.get("title") or "Untitled agent"
        return {
            "id": conversation_id,
            "title": title,
            "updatedAt": (metadata or {}).get("updated_at") or meta_file.get("updatedAtMs"),
            "source": (metadata or {}).get("source", "local"),
            "archived": bool((metadata or {}).get("is_archived", False)),
            "messages": messages,
            "messageCount": len(messages),
            "hasTranscript": transcript is not None,
            "transcriptPath": str(transcript) if transcript else None,
            "branch": tab_metadata.get("branchName"),
            "readOnly": True,
        }

    def change_token(self) -> str:
        paths = [
            self.paths.global_state,
            self.paths.global_state.with_name(self.paths.global_state.name + "-wal"),
            self.paths.search_db,
            self.paths.search_db.with_name(self.paths.search_db.name + "-wal"),
        ]
        parts: list[str] = []
        for path in paths:
            try:
                stat = path.stat()
                parts.append(f"{stat.st_mtime_ns}:{stat.st_size}")
            except OSError:
                parts.append("0:0")
        transcript_paths, stamp = self._transcript_snapshot()
        parts.append(f"{stamp[0]}:{stamp[1]}:{stamp[2]}")
        return "|".join(parts)

    def _connect(self, path: Path) -> sqlite3.Connection:
        uri = f"file:{path}?mode=ro"
        connection = sqlite3.connect(uri, uri=True, timeout=1)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        return connection

    def _search_conversations(self, *, query: str, archived: bool, limit: int) -> list[sqlite3.Row]:
        if not self.paths.search_db.exists():
            return []
        limit = max(1, min(limit, 2_000))
        with closing(self._connect(self.paths.search_db)) as connection:
            if query.strip():
                safe_query = " ".join(f'"{part.replace(chr(34), chr(34) * 2)}"*' for part in query.split())
                return connection.execute(
                    """
                    SELECT c.source, c.id, c.title, c.updated_at, c.is_archived
                    FROM conversation_fts f
                    JOIN conversations c ON c.fts_rowid = f.rowid
                    WHERE conversation_fts MATCH ? AND c.is_archived = ?
                    ORDER BY c.updated_at DESC
                    LIMIT ?
                    """,
                    (safe_query, int(archived), limit),
                ).fetchall()
            return connection.execute(
                """
                SELECT source, id, title, updated_at, is_archived
                FROM conversations
                WHERE is_archived = ?
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (int(archived), limit),
            ).fetchall()

    def _conversation_metadata(self, conversation_id: str) -> dict[str, Any] | None:
        if not self.paths.search_db.exists():
            return None
        with closing(self._connect(self.paths.search_db)) as connection:
            row = connection.execute(
                "SELECT source, id, title, updated_at, is_archived FROM conversations WHERE id = ? ORDER BY source = 'local' DESC LIMIT 1",
                (conversation_id,),
            ).fetchone()
        return dict(row) if row else None

    def _item(self, key: str) -> str | None:
        if not self.paths.global_state.exists():
            return None
        with closing(self._connect(self.paths.global_state)) as connection:
            row = connection.execute("SELECT value FROM ItemTable WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def _json_item(self, key: str, default: Any) -> Any:
        value = self._item(key)
        if value is None:
            return default
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return default

    def _session_tab_metadata(self, conversation_id: str) -> dict[str, Any]:
        """Return the most recently active Cursor tab metadata for an agent."""
        if not self.paths.global_state.exists():
            return {}

        matches: list[tuple[int, dict[str, Any]]] = []
        with closing(self._connect(self.paths.global_state)) as connection:
            rows = connection.execute(
                "SELECT value FROM ItemTable WHERE key LIKE 'cursor/glass.tabs.v2/%/state.json'"
            ).fetchall()

        for row in rows:
            try:
                state = json.loads(row["value"])
            except (json.JSONDecodeError, TypeError):
                continue
            workspace_tabs = state.get("workspaceTabs", {}) if isinstance(state, dict) else {}
            if isinstance(workspace_tabs, dict):
                tabs = workspace_tabs.values()
            elif isinstance(workspace_tabs, list):
                tabs = workspace_tabs
            else:
                continue
            for tab in tabs:
                if not isinstance(tab, dict):
                    continue
                props = tab.get("props")
                if not isinstance(props, dict) or props.get("ownerAgentId") != conversation_id:
                    continue
                branch_name = props.get("branchName")
                if not isinstance(branch_name, str) or not branch_name.strip():
                    continue
                last_active = tab.get("lastActiveTime")
                matches.append((last_active if isinstance(last_active, int) else 0, props))

        if not matches:
            return {}
        return max(matches, key=lambda item: item[0])[1]

    def _projects(self, key: str) -> dict[str, dict[str, Any]]:
        values = self._json_item(key, [])
        if not isinstance(values, list):
            return {}
        return {item["id"]: item for item in values if isinstance(item, dict) and item.get("id")}

    def _get_transcript_index(self) -> dict[str, Path]:
        paths, stamp = self._transcript_snapshot()
        with self._transcript_lock:
            if self._transcript_index and self._transcript_stamp == stamp:
                return dict(self._transcript_index)

            index: dict[str, Path] = {}
            mtimes: dict[str, int] = {}
            for path in paths:
                conversation_id = path.stem
                try:
                    mtime = path.stat().st_mtime_ns
                except OSError:
                    continue
                if mtime > mtimes.get(conversation_id, -1):
                    index[conversation_id] = path
                    mtimes[conversation_id] = mtime
            self._transcript_index = index
            self._transcript_stamp = stamp
            return dict(index)

    def _transcript_snapshot(self) -> tuple[list[Path], tuple[int, int, int]]:
        paths: list[Path] = []
        newest_mtime = 0
        total_size = 0
        try:
            candidates = self.paths.projects_dir.glob("*/agent-transcripts/*/*.jsonl")
            for path in candidates:
                try:
                    stat = path.stat()
                except OSError:
                    continue
                paths.append(path)
                newest_mtime = max(newest_mtime, stat.st_mtime_ns)
                total_size += stat.st_size
        except OSError:
            pass
        return paths, (len(paths), newest_mtime, total_size)

    def _project_name_from_transcript(self, transcript: Path | None) -> str:
        if transcript is None:
            return "Other"
        project_slug = transcript.parents[2].name
        return project_slug.removeprefix("Users-").replace("-Github-", "/").replace("-", " ")

    def _chat_meta(self, conversation_id: str) -> dict[str, Any]:
        for path in self.paths.home.glob(f"chats/*/{conversation_id}/meta.json"):
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return {}
        return {}

    @staticmethod
    def _normalize_message(raw: Any, line_number: int) -> dict[str, Any] | None:
        if not isinstance(raw, dict) or "role" not in raw:
            return None
        message = raw.get("message", raw)
        content = message.get("content", []) if isinstance(message, dict) else []
        if isinstance(content, str):
            content = [{"type": "text", "text": content}]
        if not isinstance(content, list):
            content = [{"type": "unknown", "value": content}]
        return {
            "line": line_number,
            "role": raw.get("role") or (message.get("role") if isinstance(message, dict) else None) or "unknown",
            "content": content,
            "raw": raw,
        }
