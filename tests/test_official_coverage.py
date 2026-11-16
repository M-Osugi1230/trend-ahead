"""JFA women's schedule and evidence-bounded official coverage regression tests."""
import unittest
from datetime import datetime, timedelta, timezone

from event_discovery import discover, extract_jfa_schedule_table, match_curated
from official_coverage import evaluate, markdown

JST = timezone(timedelta(hours=9))
NOW = datetime(2026, 11, 17, 8, tzinfo=JST)
WOMEN = {
    "id": "jfa_nadeshiko_2026",
    "url": "https://www.jfa.jp/nadeshikojapan/schedule_result/2026.html",
    "kind": "jfa_schedule_table",
    "category": "スポーツ",
    "team_name": "なでしこジャパン",
    "season_year": 2026,
    "max_candidates": 30,
}
MEN = {
    **WOMEN,
    "id": "jfa_samuraiblue_2026",
    "url": "https://www.jfa.jp/samuraiblue/schedule_result/2026.html",
    "team_name": "サッカー日本代表",
}
PAGE = """<html><table>
<tr><th>開催日</th><th>試合名</th><th>スコア</th><th>対戦チーム</th><th>会場</th><th>公式記録</th></tr>
<tr><td>10/14(水)</td><td>国際親善試合</td><td>●1-2</td><td>ドイツ</td><td>ドイツ</td><td></td></tr>
<tr><td>11/29(日)</td><td>MIZUHO BLUE CHALLENGE NADESHIKO 2026 青さで、挑め。</td><td>-</td><td>ブラジル</td><td>広島</td><td></td></tr>
<tr><td>12/5(土)</td><td>MIZUHO BLUE CHALLENGE NADESHIKO 2026 青さで、挑め。</td><td>-</td><td>ブラジル</td><td>岡山</td><td></td></tr>
<tr><td>12/15(火)</td><td>中止 国際親善試合</td><td>-</td><td>未定</td><td>東京</td><td></td></tr>
</table></html>"""


class NadeshikoTest(unittest.TestCase):
    def test_nadeshiko_brazil_twice_no_old_or_cancelled_entries(self):
        rows = extract_jfa_schedule_table(PAGE, WOMEN, NOW)
        self.assertEqual(len(rows), 2)
        self.assertEqual([r["starts_at"] for r in rows],
                         ["2026-11-29", "2026-12-05"])
        self.assertTrue(all("なでしこジャパン vs ブラジル" in r["title"] for r in rows))
        self.assertTrue(all(r["team_name"] == "なでしこジャパン" for r in rows))
        self.assertTrue(all("verified_at" not in r for r in rows))

    def test_women_and_men_are_never_deduped_together(self):
        rows = extract_jfa_schedule_table(PAGE, WOMEN, NOW)
        same_day_men = [{
            "title": "日本代表 vs ブラジル代表（国際親善試合）",
            "starts_at": "2026-11-29T19:00:00+09:00",
            "category": "スポーツ",
        }]
        self.assertIsNone(match_curated(rows[0], same_day_men))
        same_day_women = [{
            "title": "なでしこジャパン vs ブラジル代表",
            "starts_at": "2026-11-29T19:00:00+09:00",
            "category": "スポーツ",
        }]
        self.assertEqual(match_curated(rows[0], same_day_women), "exact_curated")

    def test_nadeshiko_discovery_queues_unknown_kickoff(self):
        report = discover([WOMEN], [], NOW, lambda _: PAGE)
        self.assertEqual(report["sources_ok"], 1)
        self.assertEqual(report["queued"], 2)
        self.assertEqual(len(report["official_exclusions"]), 1)
        self.assertTrue(all(":" not in x["starts_at"] for x in report["candidates"]))
        self.assertTrue(all("verified_at" not in x for x in report["candidates"]))


class OfficialCoverageTest(unittest.TestCase):
    def setUp(self):
        self.sources = [WOMEN, MEN]
        self.curated = [{
            "title": "なでしこジャパン vs ブラジル代表",
            "starts_at": "2026-11-29T19:00:00+09:00",
            "category": "スポーツ",
        }]
        self.discovery = discover([WOMEN], self.curated, NOW, lambda _: PAGE)

    def test_coverage_exact_missing_and_unavailable_are_distinct(self):
        report = evaluate(self.discovery, self.curated, self.sources, NOW)
        self.assertEqual(report["official_events_observed"], 2)
        self.assertEqual(report["already_in_approved_calendar"], 1)
        self.assertEqual(report["unlisted_official_candidates"], 1)
        self.assertEqual(report["observed_catalog_match_fraction"], 0.5)
        self.assertEqual(report["sources_not_reported"], ["jfa_samuraiblue_2026"])
        self.assertEqual(report["sources_parseable"], 1)
        self.assertEqual(report["status"], "needs_review")
        self.assertEqual(report["review_queue"][0]["starts_at"], "2026-12-05")
        self.assertIn("公式イベント", markdown(report))

    def test_failed_source_does_not_count_as_no_events(self):
        failed = {
            "sources": [{"source_id": "jfa_nadeshiko_2026",
                         "status": "no_parseable_items"}],
            "observed": [],
            "official_exclusions": [],
        }
        report = evaluate(failed, self.curated, [WOMEN], NOW)
        self.assertIsNone(report["observed_catalog_match_fraction"])
        self.assertEqual(report["official_events_observed"], 0)
        self.assertEqual(report["sources_parseable"], 0)
        self.assertEqual(report["sources_failed_or_unparseable"][0]["status"],
                         "no_parseable_items")

    def test_no_source_report_cannot_be_called_healthy(self):
        report = evaluate({}, self.curated, [WOMEN], NOW)
        self.assertEqual(report["status"], "no_source_evidence")
        self.assertEqual(report["sources_not_reported"], ["jfa_nadeshiko_2026"])

    def test_duplicated_source_observations_do_not_inflate_fraction(self):
        copy = dict(self.discovery)
        copy["observed"] = copy["observed"] + copy["observed"]
        report = evaluate(copy, self.curated, [WOMEN], NOW)
        self.assertEqual(report["official_events_observed"], 2)

    def test_no_historical_or_unverified_rows_are_backfilled(self):
        old = dict(self.discovery)
        old["observed"] = [
            {"candidate_id": "x", "source_id": WOMEN["id"],
             "starts_at": "2026-01-01", "title": "終了試合",
             "event_type": "national_team_match"}
        ]
        report = evaluate(old, self.curated, [WOMEN], NOW)
        self.assertEqual(report["official_events_observed"], 0)
        self.assertIsNone(report["observed_catalog_match_fraction"])


if __name__ == "__main__":
    unittest.main()
