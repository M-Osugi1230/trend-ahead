"""A first RSS sighting is never mistaken for a rising multi-sample trend."""
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from build_site import build, recent_new_trends

NOW = datetime(2026, 11, 16, 0, tzinfo=timezone.utc)
ID = "b" * 24


class FirstSightingTest(unittest.TestCase):
    def row(self, minutes=15):
        return {
            "event_id": ID, "kind": "trends", "category": "全般",
            "title": "新しく見つけた検索",
            "first_seen_at": (NOW - timedelta(minutes=minutes)).isoformat(),
            "url": "https://trends.google.com/trending/rss?geo=JP",
        }

    def test_fresh_single_sighting_is_labeled_unconfirmed(self):
        result = recent_new_trends([self.row()], NOW)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["label"], "新着検索")
        self.assertIn("継続上昇は未確認", result[0]["evidence"])
        self.assertNotIn("score", result[0])

    def test_old_and_news_are_excluded(self):
        self.assertEqual(recent_new_trends([self.row(minutes=600)], NOW), [])
        news = dict(self.row(), kind="news")
        self.assertEqual(recent_new_trends([news], NOW), [])

    def test_site_exports_first_sightings_separately_from_signals(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / "data/events"
            folder.mkdir(parents=True)
            (folder / "2026-11-16.jsonl").write_text(
                json.dumps(self.row(), ensure_ascii=False) + "\n", encoding="utf-8"
            )
            result = build(root / "data", root / "dist", NOW,
                           "https://m-osugi1230.github.io/trend-ahead")
            payload = json.loads((root / "dist/signals.json").read_text())
            html = (root / "dist/index.html").read_text()
            self.assertEqual(result["signals_count"], 0)
            self.assertEqual(result["new_arrivals_count"], 1)
            self.assertEqual(payload["signals"], [])
            self.assertEqual(len(payload["new_arrivals"]), 1)
            self.assertIn("継続上昇は未確認", html)
            self.assertIn("新着検索", html)
            self.assertTrue((root / f"dist/topics/{ID}/index.html").exists())


if __name__ == "__main__":
    unittest.main()
