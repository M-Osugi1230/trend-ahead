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
    extract_pokemon_goods_json,
    extract_tcg_product_list,
    extract_yugioh_products,
    extract_k_arena_dates, extract_zepp_schedule,
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

    def test_k_arena_date_and_start_time_not_invented(self):
        src = {"id": "k_arena", "url": "https://k-arena.com/schedule/",
               "category": "音楽・ライブ", "kind": "k_arena_dates"}
        html = """<ul><li>2026.11.24.Tue. 取扱公演
        IRON MAIDEN RUN FOR YOUR LIVES WORLD TOUR 2026
        OPEN 17:30 / START 19:00</li>
        <li>2026.11.25.Wed. IRON MAIDEN LIVE
        OPEN 17:30 / START 19:00</li>
        <li>2026.11.14.Sat. 過去の公演 START 20:00</li></ul>"""
        rows = extract_k_arena_dates(html, src, NOW)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["starts_at"], "2026-11-24")
        self.assertNotIn("19:00", rows[0]["title"])
        self.assertEqual(rows[0]["event_type"], "concert")
        self.assertNotIn("verified_at", rows[0])

    def test_zepp_official_listing_takes_date_not_door_open(self):
        src = {"id": "zepp_shinjuku",
               "url": "https://www.zepp.co.jp/hall/shinjuku/schedule/",
               "category": "音楽・ライブ", "kind": "zepp_schedule"}
        html = """<main>2026 11.17 TUE 出演者多数 sound stock Vol.22
        [OPEN] 14:00 [START] 14:30 [PRICE] 4500 READ MORE
        2026 11.18 WED WAAARZ Road to Zepp Shinjuku TOUR FINAL
        [OPEN] 17:30 [START] 18:30 [PRICE] 12000 READ MORE
        2026 11.15 SUN 昨日のコンサート [OPEN] 17:30</main>"""
        rows = extract_zepp_schedule(html, src, NOW)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["starts_at"], "2026-11-17")
        self.assertEqual(rows[1]["starts_at"], "2026-11-18")
        self.assertEqual(rows[0]["event_type"], "concert")
        self.assertNotIn("verified_at", rows[0])

    def test_movie_release_dedupe_ignores_display_wrappers(self):
        candidate = {
            "title": "魔法使いの夜", "starts_at": "2026-11-20",
            "event_type": "movie_release", "category": "映画・アニメ",
        }
        curated = [{
            "title": "映画『魔法使いの夜』公開",
            "starts_at": "2026-11-20", "category": "映画・アニメ",
        }]
        self.assertEqual(match_curated(candidate, curated), "exact_curated")
        self.assertIsNone(match_curated(
            dict(candidate, starts_at="2026-11-21"), curated
        ))
        self.assertEqual(match_curated({
            "title": "君の話", "starts_at": "2026-11-27",
            "event_type": "movie_release",
        }, [{
            "title": "映画『君の話』公開", "starts_at": "2026-11-27",
            "category": "映画・アニメ"
        }]), "exact_curated")

    def test_jra_visible_schedule_without_table_is_staged(self):
        source = {
            "id": "jra_g1", "url": "https://www.jra.go.jp/datafile/seiseki/replay/2026/g1.html",
            "kind": "jra_g1_table", "category": "スポーツ",
            "season_year": 2026, "max_candidates": 20,
        }
        html = """<main><div>11月22日日曜</div><div>GⅠマイルチャンピオンシップ</div>
        <span>京都</span><div>12月20日日曜</div><div>GⅠ朝日杯フューチュリティS</div>
        <span>阪神</span></main>"""
        result = extract_jra_g1_dates(html, source, NOW)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["starts_at"], "2026-11-22")
        self.assertEqual(result[1]["starts_at"], "2026-12-20")

    def test_empty_extractor_does_not_report_success(self):
        source = {
            "id": "nintendo_switch2",
            "url": "https://www.nintendo.com/jp/games/switch2/lineup/index.html",
            "kind": "game_title_date", "category": "ゲーム", "max_candidates": 20,
        }
        report = discover([source], [], NOW, lambda _: "<html><body>JavaScript required</body></html>")
        self.assertEqual(report["sources_ok"], 0)
        self.assertEqual(report["status"], "unavailable")
        self.assertEqual(report["sources"][0]["status"], "no_parseable_items")

    def test_pokemon_goods_calendar_stays_unverified(self):
        source = {
            "id": "pokemon_goods_calendar",
            "url": "https://www.pokemon.co.jp/api/info_goods/calender/",
            "category": "グルメ・商品",
            "kind": "pokemon_goods_json",
            "max_candidates": 20,
        }
        payload = {
            "results": [
                {
                    "model": "goods", "term": "goods",
                    "event_title": "ポケモン新作グッズ発売の予定",
                    "event_date_start": "2026-11-27",
                    "full_uniq": "https://www.pokemon.co.jp/goods/2026/11/item.html",
                },
                {
                    "model": "goods", "term": "goods",
                    "event_title": "もう販売済みのアイテム",
                    "event_date_start": "2026-11-01",
                },
                {
                    "model": "info", "term": "campaign",
                    "event_title": "キャンペーン告知",
                    "event_date_start": "2026-12-20",
                },
                {
                    "model": "goods", "term": "goods",
                    "event_title": "日付が無効な商品",
                    "event_date_start": "2026-02-30",
                },
            ]
        }
        import json
        rows = extract_pokemon_goods_json(json.dumps(payload, ensure_ascii=False), source, NOW)
        self.assertEqual(len(rows), 1)
        item = rows[0]
        self.assertEqual(item["starts_at"], "2026-11-27")
        self.assertEqual(item["category"], "グルメ・商品")
        self.assertEqual(item["event_type"], "product_or_campaign_candidate")
        self.assertEqual(item["date_claim"], "calendar_metadata_unverified")
        self.assertEqual(item["status"], "needs_review")
        self.assertNotIn("verified_at", item)
        self.assertIn("発売日・応募締切・開始時刻とは限らない", item["notes"])
        self.assertEqual(item["official_url"], source["url"])

        report = discover([source], [], NOW, lambda _: json.dumps(payload, ensure_ascii=False))
        self.assertEqual(report["sources_ok"], 1)
        self.assertEqual(report["queued"], 1)
        self.assertEqual(report["publication"], "manual_editorial_review_only")

    def test_malformed_official_json_never_creates_events(self):
        source = {
            "id": "pokemon_goods_calendar",
            "url": "https://www.pokemon.co.jp/api/info_goods/calender/",
            "category": "グルメ・商品", "kind": "pokemon_goods_json",
        }
        self.assertEqual(extract_pokemon_goods_json("<html>not JSON</html>", source, NOW), [])
        self.assertEqual(extract_pokemon_goods_json('{"results":{}}', source, NOW), [])
        report = discover([source], [], NOW, lambda _: "<html>not JSON</html>")
        self.assertEqual(report["sources_ok"], 0)
        self.assertEqual(report["sources"][0]["status"], "no_parseable_items")

    def test_official_tcg_products_need_explicit_release_date_and_review(self):
        source = {"id": "weiss_schwarz_products", "url": "https://ws-tcg.com/products/",
                  "category": "グルメ・商品", "kind": "tcg_product_list",
                  "max_candidates": 25}
        html = """<ul>
        <li>ブースターパック Persona 30th Anniversary 発売日：2026年11月27日(金)</li>
        <li>プレミアムブースター 超かぐや姫！ 発売日：2026年12月18日(金)</li>
        <li>トライアルデッキ 過去作品 発売日：2025年12月12日(金)</li>
        <li>商品ニュース ブースターパック 発売記念企画 2027年1月12日</li>
        </ul>"""
        rows = extract_tcg_product_list(html, source, NOW)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["starts_at"], "2026-11-27")
        self.assertIn("Persona 30th Anniversary", rows[0]["title"])
        self.assertEqual(rows[1]["starts_at"], "2026-12-18")
        self.assertEqual(rows[0]["event_type"], "tcg_release")
        self.assertTrue(all(row["status"] == "needs_review" for row in rows))
        self.assertTrue(all("verified_at" not in row for row in rows))
        report = discover([source], [], NOW, lambda _: html)
        self.assertEqual(report["sources_ok"], 1)
        self.assertEqual(report["queued"], 2)

    def test_tcg_publisher_does_not_infer_release_from_post_date(self):
        source = {"id": "weiss_schwarz_products", "url": "https://ws-tcg.com/products/",
                  "category": "グルメ・商品", "kind": "tcg_product_list"}
        html = "<ul><li>2026年12月1日　新商品のお知らせ ブースターパック</li></ul>"
        self.assertEqual(extract_tcg_product_list(html, source, NOW), [])

    def test_tcg_release_has_probable_duplicate_not_false_exact(self):
        item = {
            "title": "ブースターパック Persona 30th Anniversary 発売",
            "starts_at": "2026-11-27", "category": "グルメ・商品",
            "event_type": "tcg_release",
        }
        curated = [{
            "title": "ヴァイスシュヴァルツ『Persona 30th Anniversary』発売",
            "starts_at": "2026-11-27", "category": "グルメ・商品",
        }]
        self.assertEqual(match_curated(item, curated), "possible_curated")
        curated[0]["title"] = "ヴァイスシュヴァルツ ブースターパック Persona 30th Anniversary 発売"
        self.assertEqual(match_curated(item, curated), "exact_curated")
        other = dict(item, starts_at="2026-11-28")
        self.assertIsNone(match_curated(other, curated))

    def test_review_pack_puts_near_term_first_and_marks_product_dates(self):
        base = {
            "source_id": "sample_official", "category": "グルメ・商品",
            "official_url": "https://example.org/official",
            "evidence": "掲載日：2026年11月20日",
            "status": "needs_review",
        }
        report = {
            "generated_at": NOW.isoformat(),
            "sources_total": 1, "sources_ok": 1, "queued": 3,
            "sources": [{"source_id": "sample_official", "status": "ok",
                         "extracted": 3, "queued": 3}],
            "candidates": [
                dict(base, candidate_id="later", starts_at="2027-01-12",
                     title="ずっと先の発売", match_curated=None),
                dict(base, candidate_id="urgent", starts_at="2026-11-20",
                     title="発売日が未確定のグッズ", match_curated=None,
                     date_claim="calendar_metadata_unverified",
                     review_reference_url="https://example.org/product"),
                dict(base, candidate_id="duplicate", starts_at="2026-11-21",
                     title="重複の可能性がある商品", match_curated="possible_curated"),
            ],
        }
        markdown = review_markdown(report)
        self.assertIn("優先審査：1件", markdown)
        self.assertIn("後日審査：1件", markdown)
        self.assertIn("重複疑い：1件", markdown)
        self.assertLess(markdown.index("発売日が未確定のグッズ"),
                        markdown.index("ずっと先の発売"))
        self.assertLess(markdown.index("ずっと先の発売"),
                        markdown.index("重複の可能性がある商品"))
        self.assertIn("実際の発売日とは限りません", markdown)
        self.assertIn("未確認・未公開", markdown)

    def test_yugioh_official_products_require_exact_date(self):
        source = {"id": "yugioh_products",
                  "url": "https://www.yugioh-card.com/japan/products/",
                  "category": "グルメ・商品",
                  "kind": "yugioh_products", "max_candidates": 20}
        html = """<ul>
        <li>ジャンプフェスタ限定
        コンセプトパック PREMIUM PACK 2027 2026年12月19日(土)
        1ボックス 2,530円</li>
        <li>構築済みデッキ THE CHRONICLES DECK DX マギストス
        2026年12月5日(土) 1,980円</li>
        <li>デュエリストアイテム YU-GI-OH! ART EXPANSION BOX
        2026年12月下旬 11,000円</li>
        <li>基本パック OLDER CARD PACK 2025年10月20日(月)</li>
        </ul>"""
        rows = extract_yugioh_products(html, source, NOW)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["title"], "PREMIUM PACK 2027 発売")
        self.assertEqual(rows[0]["starts_at"], "2026-12-19")
        self.assertEqual(rows[0]["availability_hint"],
                         "limited_distribution_review_required")
        self.assertEqual(rows[1]["starts_at"], "2026-12-05")
        self.assertTrue(all("verified_at" not in row for row in rows))
        self.assertTrue(all(row["status"] == "needs_review" for row in rows))
        report = discover([source], [], NOW, lambda _: html)
        self.assertEqual(report["queued"], 2)
        self.assertEqual(len(report["observed"]), 2)

    def test_observed_includes_already_curated_items(self):
        source = {"id": "yugioh_products",
                  "url": "https://www.yugioh-card.com/japan/products/",
                  "category": "グルメ・商品",
                  "kind": "yugioh_products"}
        html = """<ul><li>構築済みデッキ THE CHRONICLES DECK DX マギストス
        2026年12月5日(土)</li></ul>"""
        match = extract_yugioh_products(html, source, NOW)[0]
        curated = [{"title": match["title"], "starts_at": match["starts_at"],
                    "category": "グルメ・商品"}]
        report = discover([source], curated, NOW, lambda _: html)
        self.assertEqual(report["queued"], 0)
        self.assertEqual(len(report["observed"]), 1)

    def test_normalization_is_deterministic(self):
        self.assertEqual(normalize("JAPAN TEX 2026"), normalize("Japan-Tex 2026"))


if __name__ == "__main__":
    unittest.main()
