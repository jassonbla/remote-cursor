from __future__ import annotations

import threading
import time
import tempfile
import unittest
from pathlib import Path

from remote_cursor.events import CursorChangeMonitor, EventBroker


class EventBrokerTest(unittest.TestCase):
    def test_returns_only_events_after_cursor(self) -> None:
        broker = EventBroker()
        first = broker.publish("data.changed")
        second = broker.publish("data.changed", {"source": "cursor"})

        events = broker.after(first.event_id, timeout=0)

        self.assertEqual(events, [second])
        self.assertEqual(events[0].payload, {"source": "cursor"})

    def test_waits_without_polling_until_an_event_arrives(self) -> None:
        broker = EventBroker()
        received = []

        def wait_for_event() -> None:
            received.extend(broker.after(0, timeout=1))

        waiter = threading.Thread(target=wait_for_event)
        waiter.start()
        time.sleep(0.02)
        broker.publish("data.changed")
        waiter.join(timeout=1)

        self.assertFalse(waiter.is_alive())
        self.assertEqual([event.name for event in received], ["data.changed"])


@unittest.skipUnless(hasattr(__import__("select"), "kqueue"), "requires macOS/BSD kqueue")
class CursorChangeMonitorTest(unittest.TestCase):
    def test_kqueue_waiter_reports_a_file_change(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "cursor.jsonl"
            target.write_text("first\n", encoding="utf-8")
            changed = threading.Event()
            monitor = CursorChangeMonitor([target], changed.set)
            monitor.start()
            try:
                self.assertTrue(monitor.wait_until_ready())
                with target.open("a", encoding="utf-8") as handle:
                    handle.write("second\n")
                self.assertTrue(changed.wait(2), "kqueue did not report the transcript append")
            finally:
                monitor.stop()


if __name__ == "__main__":
    unittest.main()
