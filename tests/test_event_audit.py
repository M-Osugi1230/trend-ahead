"""Daily catalog audit must not invent future events or self-refresh verification."""
import json
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path

from event_audit import audit

JST = timezone(timedelta(hours=9))
NOW = datetime(2026, 11, 16, 8, tzinfo=JST)


class AuditTest(unittest.TestCase):
    def entry(self, title, offset=3, date_only=False, checked_days_ago=0):
        day = NOW + timedelta(days=offset)
        starts_at = day.date().isoformat() if date_only else day.isoformat()
        return {"title": title, "category": "ゲーム", "starts_at": starts_at,
                "verified_at": (NOW - timedelta(days=checked_days_ago)).isoformat(),
                "official_url": "https://example.org/official"}

    def check(self, entries):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.json"
            path.write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")
            return audit(path, NOW)

    def test_counts_and_missing_genres_are_observable(self):
        events = [self.entry(f"event{i}", i%15+1, date_only=i%2==0) for i in range(30)]
        result = self.check(events)
        self.assertEqual(result["total_curated"], 30)
        self.assertEqual(result["future_30_days"], 30)
        self.assertEqual(result["status"], "ok")
        self.assertIn("音楽・ライブ", result["missing_categories_30d"])
        self.assertEqual(result["category_counts_30d"]["ゲーム"], 30)

    def test_expired_source_check_is_not_automatically_refreshed(self):
        old = self.entry("review due", checked_days_ago=31)
        result = self.check([old])
        self.assertEqual(result["future_30_days"], 0)
        self.assertEqual(result["source_review_due"][0]["reason"], "verification_expired")
        self.assertEqual(old["verified_at"], self.entry("review due", checked_days_ago=31)["verified_at"])

    def test_duplicates_and_invalid_source_counted(self):
        valid = self.entry("dup")
        invalid = self.entry("invalid")
        invalid["official_url"] = "javascript:alert(1)"
        result = self.check([valid, valid, invalid])
        self.assertEqual(len(result["duplicates"]), 1)
        self.assertEqual(len(result["errors"]), 1)
        self.assertEqual(result["future_30_days"], 1)

    def test_day_only_today_still_counts(self):
        row = self.entry("movie today", offset=0, date_only=True)
        result = self.check([row])
        self.assertEqual(result["future_7_days"], 1)

    def test_missing_catalog_reports_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = audit(Path(tmp) / "missing.json", NOW)
            self.assertEqual(result["status"], "invalid_catalog")


if __name__ == "__main__":
    unittest.main()
