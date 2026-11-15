"""Regression checks for accurate future-date semantics and unbiased signal coverage."""
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from build_site import build, load_upcoming, make_ics, make_calendar_feed, calendar_id

JST = timezone(timedelta(hours=9))
NOW = datetime(2026, 11, 16, 9, tzinfo=JST)
ID = "a" * 24


class VerifiedDateTests(unittest.TestCase):
    def date_row(self):
        return {
            "title": "映画公開日", "category": "映画・アニメ",
            "description": "公式公開日が確定、上映時刻は映画館で確認",
            "starts_at": "2026-11-20",
            "official_url": "https://example.org/movie",
            "verified_at": "2026-11-16T08:00:00+09:00",
        }

    def test_date_only_ics_has_all_day_but_no_invented_alarm(self):
        with tempfile.TemporaryDirectory() as temp:
            data = Path(temp) / "upcoming.json"
            data.write_text(json.dumps([self.date_row()], ensure_ascii=False), encoding="utf-8")
            items = load_upcoming(data, NOW)
            self.assertEqual(len(items), 1)
            self.assertTrue(items[0]["date_only"])
            self.assertEqual(items[0]["start_dt"].utcoffset(), timedelta(hours=9))
            individual = make_ics(items[0], calendar_id(items[0]), NOW)
            combined = make_calendar_feed(items, NOW)
            self.assertIn("DTSTART;VALUE=DATE:20261120", individual)
            self.assertIn("DTEND;VALUE=DATE:20261121", individual)
            self.assertIn("DTSTART;VALUE=DATE:20261120", combined)
            self.assertNotIn("BEGIN:VALARM", individual)
            self.assertNotIn("TRIGGER:-PT30M", individual)

    def test_day_only_stays_visible_during_its_calendar_day(self):
        with tempfile.TemporaryDirectory() as temp:
            data = Path(temp) / "upcoming.json"
            data.write_text(json.dumps([self.date_row()], ensure_ascii=False), encoding="utf-8")
            during = datetime(2026, 11, 20, 17, tzinfo=JST)
            self.assertEqual(len(load_upcoming(data, during)), 1)
            expired = datetime(2026, 11, 21, 0, 0, tzinfo=JST)
            self.assertEqual(load_upcoming(data, expired), [])

    def test_html_displays_date_only_not_midnight(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            data = root / "data"
            data.mkdir()
            (data / "upcoming.json").write_text(
                json.dumps([self.date_row()], ensure_ascii=False), encoding="utf-8"
            )
            build(data, root / "dist", NOW, "https://example.org/test")
            html = (root / "dist/index.html").read_text(encoding="utf-8")
            week = (root / "dist/week/index.html").read_text(encoding="utf-8")
            self.assertIn('>11/20(金)</time>', html)
            self.assertNotIn('11/20(金) 00:00', html)
            self.assertIn("映画公開日", html)
            self.assertIn("日付のみ", html)
            self.assertIn("カレンダーに追加", week)
            self.assertNotIn("30分前通知をカレンダーに追加", week)
            self.assertIn('data-starts-at="2026-11-20T00:00:00+09:00"', html)

    def test_rising_topic_is_not_crowded_out_by_news(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            events = root / "data/events"
            obs = root / "data/observations"
            events.mkdir(parents=True)
            obs.mkdir()
            stamp = NOW.astimezone(timezone.utc)
            rows = [{
                "event_id": ID, "kind": "trends", "category": "全般",
                "title": "検索に動きがある話題",
                "first_seen_at": (stamp - timedelta(minutes=45)).isoformat(),
                "url": "https://trends.google.com/trending/rss?geo=JP",
            }]
            for i in range(125):
                rows.append({
                    "event_id": f"{i+100:024x}", "kind": "news",
                    "title": f"ニュース{i}", "category": "全般",
                    "first_seen_at": (stamp - timedelta(minutes=5)).isoformat(),
                    "url": f"https://example.org/news/{i}",
                })
            (events / "2026-11-16.jsonl").write_text(
                "\n".join(json.dumps(x, ensure_ascii=False) for x in rows) + "\n", encoding="utf-8"
            )
            observations = [
                {"event_id": ID, "observed_at": (stamp-timedelta(minutes=40)).isoformat(), "approx_traffic": "200+"},
                {"event_id": ID, "observed_at": (stamp-timedelta(minutes=10)).isoformat(), "approx_traffic": "500+"}
            ]
            (obs / "2026-11-16.jsonl").write_text(
                "\n".join(json.dumps(x) for x in observations) + "\n", encoding="utf-8"
            )
            report = build(root / "data", root / "dist", stamp, "https://example.org/test")
            self.assertEqual(report["observed_cards"], 100)
            self.assertEqual(report["signals_count"], 1)
            self.assertTrue((root / f"dist/topics/{ID}/index.html").exists())
            self.assertIn("検索に動きがある話題", (root / "dist/index.html").read_text())


if __name__ == "__main__":
    unittest.main()
