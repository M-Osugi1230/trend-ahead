"""Category-filtered calendar feeds remain truthful, including empty subscriptions."""
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from build_site import build, CALENDAR_FEEDS

JST = timezone(timedelta(hours=9))
NOW = datetime(2026, 11, 16, 10, tzinfo=JST)
SITE = "https://m-osugi1230.github.io/trend-ahead"


class SubscriptionTests(unittest.TestCase):
    def test_category_feeds_split_without_leaking_other_events(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            data = root / "data"
            data.mkdir()
            items = [
                {"title": "映画A", "category": "映画・アニメ",
                 "starts_at": "2026-11-20", "verified_at": NOW.isoformat(),
                 "official_url": "https://example.org/film"},
                {"title": "ゲームB", "category": "ゲーム",
                 "starts_at": "2026-11-21T19:00:00+09:00", "verified_at": NOW.isoformat(),
                 "official_url": "https://example.org/game"},
            ]
            (data / "upcoming.json").write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
            build(data, root / "dist", NOW, SITE)
            anime = (root / "dist/calendar/anime.ics").read_text(encoding="utf-8")
            games = (root / "dist/calendar/games.ics").read_text(encoding="utf-8")
            music = (root / "dist/calendar/music.ics").read_text(encoding="utf-8")
            page = (root / "dist/subscribe/index.html").read_text(encoding="utf-8")
            home = (root / "dist/index.html").read_text(encoding="utf-8")
            self.assertIn("映画A", anime)
            self.assertNotIn("ゲームB", anime)
            self.assertIn("ゲームB", games)
            self.assertNotIn("映画A", games)
            self.assertNotIn("BEGIN:VEVENT", music)
            self.assertEqual(len(list((root / "dist/calendar").glob("*.ics"))), len(CALENDAR_FEEDS) + 3)
            self.assertIn("webcal://m-osugi1230.github.io/trend-ahead/calendar/anime.ics", page)
            self.assertIn('href="/trend-ahead/calendar/anime.ics"', page)
            self.assertIn("即時プッシュ通知ではありません", page)
            self.assertIn('href="/trend-ahead/subscribe/"', home)
            self.assertIn("noindex,follow", page)
            self.assertIn("DTSTART;VALUE=DATE:20261120", anime)

    def test_all_feeds_are_created_even_if_empty(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            build(root / "data", root / "dist", NOW, SITE)
            for slug in CALENDAR_FEEDS.values():
                content = (root / f"dist/calendar/{slug}.ics").read_text(encoding="utf-8")
                self.assertIn("BEGIN:VCALENDAR", content)
                self.assertNotIn("BEGIN:VEVENT", content)


if __name__ == "__main__":
    unittest.main()
