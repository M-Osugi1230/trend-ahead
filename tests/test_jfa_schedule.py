"""JFA fixtures: no invented kickoff, no cancelled games, no duplicates."""
import unittest
from datetime import datetime, timedelta, timezone

from event_discovery import (
    discover,
    extract_jfa_schedule_table,
    extract_jfa_schedule_exclusions,
    match_curated,
    review_markdown,
)

JST = timezone(timedelta(hours=9))
NOW = datetime(2026, 11, 17, 8, tzinfo=JST)
SOURCE_2026 = {
    "id": "jfa_samuraiblue_2026",
    "url": "https://www.jfa.jp/samuraiblue/schedule_result/2026.html",
    "kind": "jfa_schedule_table",
    "category": "スポーツ",
    "season_year": 2026,
    "max_candidates": 30,
}
SOURCE_2027 = {
    **SOURCE_2026,
    "id": "jfa_samuraiblue_2027",
    "url": "https://www.jfa.jp/samuraiblue/schedule_result/2027.html",
    "season_year": 2027,
}
HTML_2026 = """<html><table>
<tr><th>開催日</th><th>試合名</th><th>スコア</th><th>対戦チーム</th><th>会場</th><th>公式記録</th></tr>
<tr><td>11/14(土)</td><td>MIZUHO BLUE CHALLENGE THE KALLANG FOOTBALL SERIES SINGAPORE</td><td>〇3-2</td><td>ブラジル</td><td>カラン</td><td></td></tr>
<tr><td>11/17(火)</td><td>MIZUHO BLUE CHALLENGE THE KALLANG FOOTBALL SERIES SINGAPORE</td><td>-</td><td>パラグアイ</td><td>シンガポールナショナルスタジアム</td><td></td></tr>
<tr><td>12/30(水)</td><td>〖中止※2〗キリンチャレンジカップ2026</td><td>-</td><td>未定</td><td>大阪</td><td></td></tr>
<tr><td>2027/1/11(月)</td><td>〖延期※1〗AFC アジアカップ サウジアラビア 2027</td><td>-</td><td>インドネシア</td><td>ジェッダ</td><td></td></tr>
</table></html>"""
HTML_2027 = """<main><table>
<tr><th>開催日</th><th>試合名</th><th>スコア</th><th>対戦チーム</th><th>会場</th><th>公式記録</th></tr>
<tr><td>3/25(木)</td><td>キリンチャレンジカップ2027</td><td>-</td><td>未定</td><td>広島</td><td></td></tr>
<tr><td>3/30(火)</td><td>キリンチャレンジカップ2027</td><td>-</td><td>未定</td><td>東京</td><td></td></tr>
<tr><td>12/23(木)-2028/1/21(金)</td><td>AFC アジアカップ サウジアラビア 2027</td><td>-</td><td>インドネシア、タイ、カタール</td><td>サウジアラビア</td><td></td></tr>
</table></main>"""


class JfaFixtureTest(unittest.TestCase):
    def test_current_future_is_found_and_score_result_is_ignored(self):
        items = extract_jfa_schedule_table(HTML_2026, SOURCE_2026, NOW)
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item["starts_at"], "2026-11-17")
        self.assertEqual(item["opponent"], "パラグアイ")
        self.assertEqual(item["event_type"], "national_team_match")
        self.assertEqual(item["category"], "スポーツ")
        self.assertEqual(item["date_claim"], "jfa_schedule_date_only_jst")
        self.assertNotIn("verified_at", item)
        self.assertNotIn("22:10", str(item))
        self.assertIn("日本代表 vs パラグアイ", item["title"])

    def test_cancel_and_postpone_are_audit_flags_not_new_fixtures(self):
        excluded = extract_jfa_schedule_exclusions(HTML_2026, SOURCE_2026, NOW)
        self.assertEqual(len(excluded), 2)
        self.assertEqual({x["starts_at"] for x in excluded},
                         {"2026-12-30", "2027-01-11"})
        self.assertTrue(all("review" in x["status"] for x in excluded))
        report = discover([SOURCE_2026], [], NOW, lambda url: HTML_2026)
        self.assertEqual(report["queued"], 1)
        self.assertEqual(len(report["official_exclusions"]), 2)
        self.assertEqual(report["sources_ok"], 1)
        self.assertIn("中止・延期", review_markdown(report))

    def test_same_day_opponent_matches_existing_approved_fixture(self):
        candidate = extract_jfa_schedule_table(HTML_2026, SOURCE_2026, NOW)[0]
        curated = [{
            "title": "日本代表 vs パラグアイ代表（サッカー国際親善試合）",
            "category": "スポーツ",
            "starts_at": "2026-11-17T22:10:00+09:00",
            "official_url": "https://www.jfa.jp/samuraiblue/20261117/tv.html",
        }]
        self.assertEqual(match_curated(candidate, curated), "exact_curated")
        report = discover([SOURCE_2026], curated, NOW, lambda _: HTML_2026)
        self.assertEqual(report["queued"], 0)
        self.assertEqual(len(report["observed"]), 1)

    def test_unknown_opponent_remains_unknown_no_kickoff(self):
        near = datetime(2027, 3, 1, 8, tzinfo=JST)
        fixtures = extract_jfa_schedule_table(HTML_2027, SOURCE_2027, near)
        self.assertEqual(len(fixtures), 2)
        self.assertEqual(fixtures[0]["starts_at"], "2027-03-25")
        self.assertTrue(all("対戦相手未定" in x["title"] for x in fixtures))
        self.assertTrue(all("vs" not in x["title"] for x in fixtures))
        self.assertTrue(all("verified_at" not in x for x in fixtures))
        self.assertTrue(all(":" not in x["starts_at"] for x in fixtures))

    def test_next_year_without_nearby_fixtures_is_not_fetch_failure(self):
        report = discover([SOURCE_2027], [], NOW, lambda _: HTML_2027)
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["sources"][0]["status"], "ok_no_upcoming_in_horizon")
        self.assertEqual(report["queued"], 0)

    def test_blank_or_malformed_html_never_passes_as_success(self):
        report = discover([SOURCE_2026], [], NOW, lambda _: "<main>JavaScript required</main>")
        self.assertEqual(report["sources_ok"], 0)
        self.assertEqual(report["sources"][0]["status"], "no_parseable_items")

    def test_event_date_changes_do_not_create_false_exact_match(self):
        old = [{
            "title": "日本代表 vs パラグアイ代表（サッカー国際親善試合）",
            "category": "スポーツ",
            "starts_at": "2026-11-18",
        }]
        candidate = extract_jfa_schedule_table(HTML_2026, SOURCE_2026, NOW)[0]
        self.assertIsNone(match_curated(candidate, old))

    def test_duplicate_undecided_opponents_are_distinct_dates(self):
        near = datetime(2027, 3, 1, 8, tzinfo=JST)
        rows = extract_jfa_schedule_table(HTML_2027, SOURCE_2027, near)
        report = discover([SOURCE_2027], [], near, lambda _: HTML_2027)
        self.assertEqual(len(rows), 2)
        self.assertEqual(report["queued"], 2)
        self.assertEqual(report["observed"][0]["starts_at"], "2027-03-25")
        self.assertEqual(report["observed"][1]["starts_at"], "2027-03-30")


if __name__ == "__main__":
    unittest.main()
