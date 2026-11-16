"""Officially announced months must never become fabricated calendar dates."""
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from announced_windows import load_announced_windows
from build_site import build

JST = timezone(timedelta(hours=9))
NOW = datetime(2026, 11, 17, 1, 0, tzinfo=JST)
SITE = "https://m-osugi1230.github.io/trend-ahead"


class MonthOnlyAnnouncementsTest(unittest.TestCase):
    def sample(self, name="アニメ新作"):
        return {
            "title": name, "period": "2027-01", "kind": "anime_broadcast",
            "category": "映画・アニメ",
            "official_url": "https://www.tv-tokyo.co.jp/anime/example/",
            "verified_at": (NOW - timedelta(hours=1)).isoformat(),
        }

    def load(self, records, now=NOW):
        with tempfile.TemporaryDirectory() as temp:
            file = Path(temp) / "announced_windows.json"
            file.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
            return load_announced_windows(file, now)

    def test_month_only_announcements_remain_month_precision(self):
        data = self.load([self.sample()])
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["period"], "2027-01")
        self.assertEqual(data[0]["period_label"], "2027年1月")
        self.assertNotIn("starts_at", data[0])
        self.assertNotIn("start_dt", data[0])
        self.assertIn("日付", data[0]["note"])

    def test_stale_bad_host_invalid_dates_and_fake_day_rejected(self):
        base = self.sample()
        cases = [
            dict(base, official_url="javascript:alert(1)"),
            dict(base, official_url="https://user:password@tv-tokyo.co.jp/"),
            dict(base, period="2027-13"),
            dict(base, period="2027-01-01"),
            dict(base, category="なんとなく"),
            dict(base, kind="made_up"),
            dict(base, starts_at="2027-01-01T00:00:00+09:00"),
            dict(base, verified_at=(NOW - timedelta(days=32)).isoformat()),
            dict(base, verified_at=(NOW + timedelta(minutes=1)).isoformat()),
        ]
        for row in cases:
            with self.subTest(row=row):
                self.assertEqual(self.load([row]), [])

    def test_past_and_too_far_windows_excluded(self):
        self.assertEqual(self.load([dict(self.sample(), period="2026-10")]), [])
        self.assertEqual(self.load([dict(self.sample(), period="2027-12")]), [])

    def test_duplicate_title_month_does_not_duplicate_promotion(self):
        item = self.sample()
        self.assertEqual(len(self.load([item, item])), 1)

    def test_home_preview_is_present_but_not_on_timed_calendars(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            data = root / "data"
            data.mkdir()
            (data / "announced_windows.json").write_text(
                json.dumps([self.sample("アオハル・ワンスモア"),
                            self.sample("ペロペロ★スパ〜クルズ")],
                           ensure_ascii=False), encoding="utf-8")
            report = build(data, root / "dist", NOW, SITE)
            home = (root / "dist/index.html").read_text(encoding="utf-8")
            calendar = (root / "dist/calendar/upcoming.ics").read_text(encoding="utf-8")
            week = (root / "dist/week/index.html").read_text(encoding="utf-8")
            payload = json.loads((root / "dist/announcements.json").read_text(encoding="utf-8"))
            meta = json.loads((root / "dist/build-meta.json").read_text(encoding="utf-8"))
            self.assertEqual(report["upcoming_cards"], 0)
            self.assertEqual(report["announced_month_only"], 2)
            self.assertEqual(meta["announced_month_only"], 2)
            self.assertEqual(payload["precision"], "month_only")
            self.assertEqual(len(payload["announcements"]), 2)
            self.assertIn('id="announcements"', home)
            self.assertIn("2027年1月", home)
            self.assertIn("アオハル・ワンスモア", home)
            self.assertIn("具体的な日付・開始時刻", payload["announcements"][0]["note"])
            self.assertNotIn("2027-01-01", home)
            self.assertNotIn("アオハル・ワンスモア", calendar)
            self.assertNotIn("アオハル・ワンスモア", week)
            self.assertIn("noindex,follow", home)

    def test_no_unverified_section_when_data_is_absent(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            result = build(root / "data", root / "dist", NOW, SITE)
            home = (root / "dist/index.html").read_text()
            self.assertEqual(result["announced_month_only"], 0)
            self.assertNotIn('id="announcements"', home)


if __name__ == "__main__":
    unittest.main()
