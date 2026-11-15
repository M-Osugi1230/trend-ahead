"""Prevent unsafe, invented, or automatically distributed launch content."""
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from outreach import get_candidates, markdown_pack

JST = timezone(timedelta(hours=9))
NOW = datetime(2026, 11, 15, 21, tzinfo=JST)


class OutreachTest(unittest.TestCase):
    def valid(self):
        return {
            "title": "ゲーム新作イベント",
            "category": "ゲーム",
            "starts_at": (NOW + timedelta(days=2)).isoformat(),
            "verified_at": (NOW - timedelta(hours=3)).isoformat(),
            "official_url": "https://example.org/official",
        }

    def get(self, rows):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "upcoming.json"
            path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
            return get_candidates(path, NOW)

    def test_verified_future_candidate(self):
        result = self.get([self.valid()])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["category"], "ゲーム")

    def test_reject_stale_invalid_and_safety_sensitive_events(self):
        valid = self.valid()
        stale = dict(valid, verified_at=(NOW - timedelta(days=40)).isoformat())
        invalid = dict(valid, official_url="javascript:alert(1)")
        old = dict(valid, starts_at=(NOW - timedelta(days=1)).isoformat())
        sensitive = dict(valid, category="ニュース・社会")
        self.assertEqual(self.get([stale, invalid, old, sensitive]), [])

    def test_no_events_means_no_post(self):
        text = markdown_pack([], NOW, "https://example.org/trend-ahead")
        self.assertIn("投稿を保留", text)
        self.assertIn("未投稿", text)
        self.assertNotIn("予測的中", text)

    def test_pack_has_source_verification_and_tracking(self):
        event = self.get([self.valid()])
        text = markdown_pack(event, NOW, "https://example.org/trend-ahead")
        self.assertIn("https://example.org/official", text)
        self.assertIn("utm_source=x", text)
        self.assertIn("utm_source=community", text)
        self.assertIn("自動投稿は禁止", text)
        self.assertIn("予測ではありません", text)
        self.assertIn("outbound_posts_sent", "outbound_posts_sent")

    def test_date_only_calendar_entry_in_outreach_does_not_invent_midnight(self):
        release = self.valid()
        release["title"] = "新作映画公開"
        release["starts_at"] = (NOW + timedelta(days=3)).date().isoformat()
        result = self.get([release])
        self.assertEqual(len(result), 1)
        self.assertTrue(result[0]["date_only"])
        draft = markdown_pack(result, NOW, "https://example.org/trend-ahead")
        self.assertIn("（日付のみ） 新作映画公開", draft)
        self.assertNotIn("00:00 新作映画公開", draft)

    def test_site_url_must_be_https(self):
        with self.assertRaises(ValueError):
            markdown_pack([], NOW, "http://localhost:3000")

    def test_no_missing_source_or_fake_qualifiers(self):
        item = self.valid()
        item.pop("verified_at")
        self.assertEqual(self.get([item]), [])


if __name__ == "__main__":
    unittest.main()
