"""The event discovery pipeline must stage, not publish, official event candidates."""
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from event_discovery import (
    allowed_https_url, candidate_from_heading, discover, extract_heading_dates,
    match_curated, normalize, review_markdown, extract_date_title_rows,
    extract_game_title_date,
    extract_jra_g1_dates,
    extract_movie_title_date, extract_movie_date_title, extract_arena_month_day,
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

    def test_date_before_title_venue_listing(self):
        source = {
            "id": "makuhari_messe", "url": "https://www.m-messe.co.jp/event/",
            "category": "季節・イベント", "kind": "date_then_title",
            "max_candidates": 25,
        }
        page = """<ul><li>音楽イベント
        2026.11.22(日) 〜 2026.11.23(月)
        CHILL CLASSIC CONCERT -Winter Illumination- 対象一般</li>
        <li>展示会・見本市 2026.11.18(水) 〜 2026.11.20(金)
        Inter BEE 2026 対象関係者、一般</li></ul>"""
        rows = extract_date_title_rows(page, source, NOW)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["title"], "CHILL CLASSIC CONCERT -Winter Illumination-")
        self.assertEqual(rows[0]["starts_at"], "2026-11-22")
        self.assertEqual(rows[1]["title"], "Inter BEE 2026")
        self.assertFalse(any("verified_at" in row for row in rows))

    def test_toho_official_title_before_date(self):
        source = {"id": "toho_movies", "url": "https://www.toho.co.jp/movie/lineup/",
                  "category": "映画・アニメ", "kind": "movie_title_date"}
        html = """<ul><li><a>魔法使いの夜</a><span>2026年11月20日（金）公開</span></li>
        <li><a>映画「SUKIYAKI 上を向いて歩こう」</a><p>2026年12月25日（金）公開</p></li>
        <li><a>すでに公開済みの映画</a><p>2026年10月2日公開</p></li>
        <li><a>公開日未定の作品</a><p>2027年公開</p></li></ul>"""
        rows = extract_movie_title_date(html, source, NOW)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["title"], "魔法使いの夜")
        self.assertEqual(rows[0]["starts_at"], "2026-11-20")
        self.assertEqual(rows[1]["starts_at"], "2026-12-25")
        self.assertEqual(rows[0]["event_type"], "movie_release")
        self.assertNotIn("verified_at", rows[0])

    def test_warner_official_date_before_title(self):
        source = {"id": "warner_movies", "url": "https://www.warnerbros.co.jp/movie/",
                  "category": "映画・アニメ", "kind": "movie_date_title"}
        html = """<ul>
          <li>2026年11月27日（金）公開映画『君の話』近日公開</li>
          <li>2026年12月25日（金）公開映画『キャット・イン・ザ・ハット』近日公開</li>
          <li>2026年9月30日（水）公開映画『旧作』公開中</li>
          <li>映画『未発表作品』近日公開</li>
        </ul>"""
        rows = extract_movie_date_title(html, source, NOW)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["title"], "君の話")
        self.assertEqual(rows[1]["starts_at"], "2026-12-25")
        self.assertEqual(rows[1]["event_type"], "movie_release")

    def test_arena_calendar_month_day_and_private_entries(self):
        source = {"id": "pia_arena", "url": "https://pia-arena-mm.jp/event/index.html",
                  "category": "音楽・ライブ", "kind": "arena_month_day"}
        html = """<h2>2026</h2><ul>
        <li>11.18 WED TOMOO TOMOO Arena Tour 2026</li>
        <li>11.21 SAT M!LK ARENA TOUR 2026-2027 「シャカリキレボリューション」</li>
        <li>11.22 SUN PRIVATE</li>
        <li>12.04 FRI 米津玄師 YONEZU LIVE TOUR</li>
        <li>01.02 SAT 2027年 新年ライブ</li>
        <li>08.20 THU 過去公演</li>
        </ul>"""
        rows = extract_arena_month_day(html, source, NOW)
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[0]["title"], "TOMOO TOMOO Arena Tour 2026")
        self.assertEqual(rows[0]["starts_at"], "2026-11-18")
        self.assertEqual(rows[2]["starts_at"], "2026-12-04")
        self.assertEqual(rows[3]["starts_at"], "2027-01-02")
        self.assertEqual(rows[0]["event_type"], "concert")
        self.assertNotIn("verified_at", rows[0])

    def test_five_source_kinds_run_through_review_queue(self):
        sources = [
            {"id": "toho_movies", "url": "https://www.toho.co.jp/movie/lineup/",
             "category": "映画・アニメ", "kind": "movie_title_date"},
            {"id": "pia_arena", "url": "https://pia-arena-mm.jp/event/index.html",
             "category": "音楽・ライブ", "kind": "arena_month_day"},
        ]
        def fixture_fetch(url):
            if "toho" in url:
                return "<ul><li>話題の新作映画公開 2026年12月20日（日）公開</li></ul>"
            return "<ul><li>11.22 SUN LIVE MUSIC CONCERT 2026</li></ul>"
        report = discover(sources, [], NOW, fixture_fetch)
        self.assertEqual(report["sources_ok"], 2)
        self.assertEqual(report["queued"], 2)
        self.assertTrue(all(x["status"] == "needs_review" for x in report["candidates"]))
        self.assertTrue(all("verified_at" not in x for x in report["candidates"]))

    def test_nintendo_upcoming_game_release_dates_are_not_auto_approved(self):
        source = {
            "id": "nintendo_switch2",
            "url": "https://www.nintendo.com/jp/games/switch2/lineup/index.html?category=edition",
            "category": "ゲーム", "kind": "game_title_date", "max_candidates": 20,
        }
        html = """<main><ul><li>ピクミン4 Nintendo Switch 2 Edition 2026.11.12（木） 任天堂</li>
        <li>ゼノブレイド3 Nintendo Switch 2 Edition 2026.12.3（木） 任天堂</li>
        <li>メトロイド ラヴェナス 2027.1.28（木） 任天堂</li>
        <li>新作 未定 2027年春 発売予定</li>
        </ul></main>"""
        rows = extract_game_title_date(html, source, NOW)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["starts_at"], "2026-12-03")
        self.assertEqual(rows[1]["starts_at"], "2027-01-28")
        self.assertEqual(rows[0]["event_type"], "game_release")
        self.assertTrue(all(row["status"] == "needs_review" for row in rows))
        self.assertTrue(all("verified_at" not in row for row in rows))

    def test_jra_grade_one_rows_only_and_date_precision(self):
        source = {
            "id": "jra_g1",
            "url": "https://www.jra.go.jp/datafile/seiseki/replay/2026/g1.html",
            "kind": "jra_g1_table", "category": "スポーツ",
            "season_year": 2026, "max_candidates": 20,
        }
        html = """<table>
        <tr><th>11月22日日曜</th><td>GⅠマイルチャンピオンシップ</td><td>京都</td></tr>
        <tr><th>12月20日日曜</th><td>GⅠ朝日杯フューチュリティS</td><td>阪神</td></tr>
        <tr><th>12月26日土曜</th><td>J・GⅠ中山大障害</td><td>中山</td></tr>
        <tr><th>12月27日日曜</th><td>GⅡ例外レース</td><td>東京</td></tr>
        <tr><th>12月28日月曜</th><td>GⅢ対象外</td><td>東京</td></tr>
        </table>"""
        found = extract_jra_g1_dates(html, source, NOW)
        self.assertEqual(len(found), 3)
        self.assertEqual(found[0]["starts_at"], "2026-11-22")
        self.assertEqual(found[1]["starts_at"], "2026-12-20")
        self.assertEqual(found[2]["starts_at"], "2026-12-26")
        self.assertTrue(all(x["event_type"] == "horse_race" for x in found))
        self.assertTrue(all("verified_at" not in x for x in found))

    def test_normalization_is_deterministic(self):
        self.assertEqual(normalize("JAPAN TEX 2026"), normalize("Japan-Tex 2026"))


if __name__ == "__main__":
    unittest.main()
