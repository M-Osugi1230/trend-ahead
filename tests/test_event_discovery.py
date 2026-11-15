"""The event discovery pipeline must stage, not publish, official event candidates."""
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from event_discovery import (
    allowed_https_url, candidate_from_heading, discover, extract_heading_dates,
    match_curated, normalize, review_markdown,
)

NOW = datetime(2026, 11, 16, 8, tzinfo=timezone(timedelta(hours=9)))
ANIME = {
    "id": "tokyo_anime_center", "url": "https://tokyoanimecenter.jp/event/",
    "category": "映画・アニメ", "kind": "heading_dates", "max_candidates": 25,
}
VENUE = {
    "id": "tokyo_big_sight", "url": "https://www.bigsight.jp/visitor/event/",
    "category": "季節・イベント", "kind": "heading_dates", "max_candidates": 25,
}
ANIME_HTML = """<html><head><script>2026.11.30 BAD</script></head><body>
<h2>展示一覧</h2>
<h4>TIGER ＆ BUNNY 企画展 ずっと相棒。</h4>
<p>2027.01.22-2027.02.28</p>
<h4>GRANRODEO POP-UP “Chaos in Paradise” VR LIVE</h4>
<p>2026.12.24-2026.12.30</p>
<h4>過去の企画</h4><span>2026.10.30-2026.11.08</span>
</body></html>"""
VENUE_HTML = """<main>
<h3>Japan Home Show &amp; Building Show 2026</h3>
<div>日本最大級建築総合展示会</div>
<div>開催期間：2026年11月18日（水）～2026年11月20日（金）</div>
<h3>JAPANTEX 2026</h3><div>開催期間：2026年11月18日（水）</div>
</main>"""


class DiscoveryTests(unittest.TestCase):
    def test_calendar_heading_dates_are_staged_only(self):
        events = extract_heading_dates(ANIME_HTML, ANIME, NOW)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["title"], "TIGER ＆ BUNNY 企画展 ずっと相棒。")
        self.assertEqual(events[0]["starts_at"], "2027-01-22")
        self.assertEqual(events[1]["starts_at"], "2026-12-24")
        self.assertTrue(all(x["status"] == "needs_review" for x in events))
        self.assertTrue(all("verified_at" not in x for x in events))
        self.assertEqual(events[0]["official_url"], ANIME["url"])

    def test_venue_calendar_dates_and_entity_unescaping(self):
        rows = extract_heading_dates(VENUE_HTML, VENUE, NOW)
        self.assertEqual(len(rows), 2)
        self.assertIn("Home Show & Building Show", rows[0]["title"])
        self.assertEqual(rows[0]["starts_at"], "2026-11-18")
        self.assertEqual(rows[1]["starts_at"], "2026-11-18")

    def test_no_cross_heading_date_leakage(self):
        html = "<h3>開始日が未発表のイベント</h3><p>準備中</p>" \
               "<h3>日付が確定したイベント</h3><p>2026年12月20日から</p>"
        rows = extract_heading_dates(html, VENUE, NOW)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], "日付が確定したイベント")

    def test_missing_and_invalid_dates_fail_closed(self):
        self.assertIsNone(candidate_from_heading("未来イベント", "詳細未定", VENUE, NOW))
        self.assertIsNone(candidate_from_heading("架空イベント", "2026.02.30", VENUE, NOW))
        self.assertIsNone(candidate_from_heading("過去イベント", "2026.08.15", VENUE, NOW))

    def test_never_cross_host_or_downgrade_http(self):
        self.assertTrue(allowed_https_url(ANIME["url"], "tokyoanimecenter.jp"))
        self.assertFalse(allowed_https_url("http://tokyoanimecenter.jp/event/", "tokyoanimecenter.jp"))
        self.assertFalse(allowed_https_url("https://evil.example/", "tokyoanimecenter.jp"))
        self.assertFalse(allowed_https_url("https://tokyoanimecenter.jp.evil.example/", "tokyoanimecenter.jp"))
        self.assertFalse(allowed_https_url("https://user:pass@tokyoanimecenter.jp/", "tokyoanimecenter.jp"))

    def test_curated_match_and_no_verification_refresh(self):
        row = extract_heading_dates(VENUE_HTML, VENUE, NOW)[0]
        curated = [{"title": row["title"], "starts_at": row["starts_at"],
                    "verified_at": "2026-11-01T09:00:00+09:00"}]
        report = discover([VENUE], curated, NOW, lambda _: VENUE_HTML)
        self.assertEqual(report["sources_ok"], 1)
        self.assertEqual(report["queued"], 1)
        self.assertEqual(curated[0]["verified_at"], "2026-11-01T09:00:00+09:00")
        self.assertIn("verified_at", curated[0])
        self.assertNotIn("verified_at", report["candidates"][0])
        self.assertEqual(match_curated(row, curated), "exact_curated")

    def test_fetch_error_does_not_stop_other_sources(self):
        def fetch(url):
            if "bigsight" in url:
                raise OSError("temporary network failure")
            return ANIME_HTML
        report = discover([VENUE, ANIME], [], NOW, fetch)
        self.assertEqual(report["status"], "partial")
        self.assertEqual(report["sources_ok"], 1)
        self.assertEqual(report["queued"], 2)
        markdown = review_markdown(report)
        self.assertIn("編集審査キュー", markdown)
        self.assertIn("未確認・未公開", markdown)
        self.assertIn("temporary network failure", markdown)

    def test_normalization_is_deterministic(self):
        self.assertEqual(normalize("JAPAN TEX 2026"), normalize("Japan-Tex 2026"))


if __name__ == "__main__":
    unittest.main()
