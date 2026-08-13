from __future__ import annotations

import os
import select
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ChangeEvent:
    """A small, replayable server-to-browser data invalidation event."""

    event_id: int
    name: str
    payload: dict[str, Any]


class EventBroker:
    """Fan out change notifications without making every SSE client poll Cursor files."""

    def __init__(self, *, history_size: int = 128) -> None:
        self._events: deque[ChangeEvent] = deque(maxlen=history_size)
        self._next_id = 1
        self._condition = threading.Condition()

    def publish(self, name: str, payload: dict[str, Any] | None = None) -> ChangeEvent:
        with self._condition:
            event = ChangeEvent(self._next_id, name, payload or {})
            self._next_id += 1
            self._events.append(event)
            self._condition.notify_all()
            return event

    def after(self, event_id: int, *, timeout: float) -> list[ChangeEvent]:
        """Wait for events newer than ``event_id``; timeout produces no synthetic event."""
        deadline = time.monotonic() + timeout
        with self._condition:
            while not any(event.event_id > event_id for event in self._events):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return []
                self._condition.wait(remaining)
            return [event for event in self._events if event.event_id > event_id]


class CursorChangeMonitor:
    """One process-wide Cursor watcher.

    macOS uses kqueue and blocks in the kernel while idle. Other platforms retain a
    conservative metadata fallback so the package remains usable without dependencies.
    The fallback is intentionally owned by one monitor, never by each browser client.
    """

    def __init__(
        self,
        paths: list[Path],
        on_change: Callable[[], None],
        *,
        fallback_interval: float = 5.0,
    ) -> None:
        self._paths = paths
        self._on_change = on_change
        self._fallback_interval = fallback_interval
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None
        self._fallback_stamp: tuple[tuple[str, int, int], ...] | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="remote-cursor-watch", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.5)
            self._thread = None

    def wait_until_ready(self, timeout: float = 1.0) -> bool:
        """Allow startup callers and tests to avoid a missed first filesystem write."""
        return self._ready.wait(timeout)

    def _run(self) -> None:
        if hasattr(select, "kqueue") and os.name == "posix":
            try:
                self._run_kqueue()
                return
            except OSError:
                # Network volumes and stripped Python builds can lack vnode support.
                pass
        self._run_fallback()

    def _run_kqueue(self) -> None:
        kqueue = select.kqueue()
        descriptors: list[int] = []
        try:
            flags = os.O_RDONLY | getattr(os, "O_EVTONLY", 0)
            for path in self._paths:
                target = path if path.exists() else path.parent
                if not target.exists():
                    continue
                descriptor = os.open(target, flags)
                descriptors.append(descriptor)
                kqueue.control(
                    [
                        select.kevent(
                            descriptor,
                            filter=select.KQ_FILTER_VNODE,
                            flags=select.KQ_EV_ADD | select.KQ_EV_CLEAR,
                            fflags=(
                                select.KQ_NOTE_WRITE
                                | select.KQ_NOTE_EXTEND
                                | select.KQ_NOTE_ATTRIB
                                | select.KQ_NOTE_RENAME
                                | select.KQ_NOTE_DELETE
                            ),
                        )
                    ],
                    0,
                    0,
                )
            if not descriptors:
                self._ready.set()
                while not self._stop.wait(1.0):
                    pass
                return
            self._ready.set()
            while not self._stop.is_set():
                changed = kqueue.control(None, len(descriptors), 1.0)
                if changed:
                    self._on_change()
        finally:
            for descriptor in descriptors:
                os.close(descriptor)
            kqueue.close()

    def _stamp(self) -> tuple[tuple[str, int, int], ...]:
        result: list[tuple[str, int, int]] = []
        for path in self._paths:
            target = path if path.exists() else path.parent
            try:
                stat = target.stat()
                result.append((str(target), stat.st_mtime_ns, stat.st_size))
            except OSError:
                result.append((str(target), 0, 0))
        return tuple(result)

    def _run_fallback(self) -> None:
        self._fallback_stamp = self._stamp()
        self._ready.set()
        while not self._stop.wait(self._fallback_interval):
            stamp = self._stamp()
            if stamp != self._fallback_stamp:
                self._fallback_stamp = stamp
                self._on_change()
