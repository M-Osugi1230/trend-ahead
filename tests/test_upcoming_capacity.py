"""Prevent verified future events being silently truncated to 60 listings."""
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from build_site import load_upcoming, MAX_UPCOMING_EVENTS

JST = timezone(timedelta(hours=9))
NOW = datetime(2026, 11, 16, 16, tzinfo=JST)


class UpcomingCapacityTests(unittest.TestCase):
    def test_more_than_60_future_events_are_retained(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "upcoming.json"
            events = [{
                "title": "未来イベント " + str(i),
                "category": "ゲーム",
                "starts_at": (NOW + timedelta(days=1, minutes=i)).isoformat(),
                "verified_at": (NOW - timedelta(hours=1)).isoformat(),
                "official_url": "https://example.org/official",
                "description": "確認済み",
            } for i in range(85)]
            path.write_text(json.dumps(events, ensure_ascii=False), encoding="utf-8")
            items = load_upcoming(path, NOW)
            self.assertEqual(len(items), 85)
            self.assertEqual(items[0]["title"], "未来イベント 0")
            self.assertEqual(items[-1]["title"], "未来イベント 84")
            self.assertGreaterEqual(MAX_UPCOMING_EVENTS, 150)


if __name__ == "__main__":
    unittest.main()
