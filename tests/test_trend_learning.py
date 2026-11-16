"""Regression tests for research-only historical outcome learning."""
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from trend_learning import build_report, classify, read_reference, render_markdown

UTC = timezone.utc
NOW = datetime(2026, 11, 17, 0, tzinfo=UTC)
ID_A, ID_B, ID_C, ID_D = ["a" * 24, "b" * 24, "c" * 24, "d" * 24]


class TrendLearningTest(unittest.TestCase):
    def store(self, root):
        data = root / "data"
        (data / "events").mkdir(parents=True)
        (data / "observations").mkdir(parents=True)
        events = []
        observations = []
        for event_id, title, readings in [
            (ID_A, "Game | New", [("2026-11-16T10:00:00+00:00", "100+"),
                                  ("2026-11-16T10:45:00+00:00", "500+")]),
            (ID_B, "Trend B", [("2026-11-16T10:00:00+00:00", "500+"),
                                ("2026-11-16T10:40:00+00:00", "200+")]),
            (ID_C, "Trend C", [("2026-11-16T10:00:00+00:00", "200+"),
                                ("2026-11-16T10:40:00+00:00", "200+")]),
            (ID_D, "Trend D", [("2026-11-16T10:00:00+00:00", "100+"),
                                ("2026-11-16T10:05:00+00:00", "500+")]),
        ]:
            events.append({
                "kind": "trends", "event_id": event_id, "title": title,
                "first_seen_at": readings[0][0], "source": "google_trends_jp",
            })
            for stamp, traffic in readings:
                observations.append({"event_id": event_id, "observed_at": stamp,
                                     "approx_traffic": traffic, "source": "google_trends_jp"})
        (data / "events/2026-11-16.jsonl").write_text(
            "\n".join(map(json.dumps, events)) + "\n", encoding="utf-8"
        )
        (data / "observations/2026-11-16.jsonl").write_text(
            "\n".join(map(json.dumps, observations)) + "\n", encoding="utf-8"
        )
        reference = root / "cases.json"
        reference.write_text(json.dumps({
            "schema_version": 1, "year": 2025, "published_at": "2025-12-04",
            "source_url": "https://blog.google/official",
            "cases": [{"topic": "Sample verified ranking", "rank": 1,
                       "ranking_group": "general", "pattern_hypothesis": "scheduled",
                       "ex_ante_evidence": None}],
        }), encoding="utf-8")
        return data, reference

    def test_four_outcomes_and_missing_earlier_forecast(self):
        with tempfile.TemporaryDirectory() as temp:
            data, reference = self.store(Path(temp))
            report = build_report(data, reference, NOW)
            stats = report["rss_observations"]
            self.assertEqual(stats["topic_count"], 4)
            self.assertEqual(stats["growth_observed"], 1)
            self.assertEqual(stats["decline_observed"], 1)
            self.assertEqual(stats["same_or_mixed_bucket"], 1)
            self.assertEqual(stats["insufficient_observations"], 1)
            self.assertFalse(report["prediction_accuracy_claim"])
            self.assertFalse(stats["examples"]["growth"][0]["forecast_was_issued"])
            self.assertEqual(stats["examples"]["growth"][0]["title"], "Game | New")
            self.assertEqual(report["prospective_three_hour_evaluation"]["status"],
                             "insufficient_data")

    def test_30_minimum_elapsed_guards_sampling_noise(self):
        cutoff = datetime(2026, 11, 16, 10, tzinfo=UTC)
        self.assertEqual(classify([(cutoff, 100), (cutoff + timedelta(minutes=3), 1000)]),
                         "insufficient_observations")
        self.assertEqual(classify([(cutoff, 100), (cutoff + timedelta(minutes=30), 200)]),
                         "growth_observed")
        self.assertEqual(classify([(cutoff, 200), (cutoff + timedelta(minutes=30), 100)]),
                         "decline_observed")

    def test_unsafe_reference_attempt_to_backfill_signal_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            _, path = self.store(Path(temp))
            record = json.loads(path.read_text())
            record["cases"][0]["ex_ante_evidence"] = "retrospectively assumed"
            path.write_text(json.dumps(record))
            with self.assertRaises(ValueError):
                read_reference(path)

    def test_empty_observations_do_not_fake_accuracy(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _, reference = self.store(root)
            report = build_report(root / "nonexistent", reference, NOW)
            self.assertEqual(report["rss_observations"]["topic_count"], 0)
            self.assertEqual(report["rss_observations"]["collection_freshness"],
                             "no_observations")
            self.assertIsNone(report["prospective_three_hour_evaluation"]["precision_at_3_momentum"])

    def test_report_escapes_markdown_table_and_mentions_no_prediction(self):
        with tempfile.TemporaryDirectory() as temp:
            data, reference = self.store(Path(temp))
            markdown = render_markdown(build_report(data, reference, NOW))
            self.assertIn("Game \\| New", markdown)
            self.assertIn("事前予測の的中ではありません", markdown)
            self.assertIn("未来値欠測", markdown)

    def test_reject_naive_time(self):
        with tempfile.TemporaryDirectory() as temp:
            data, reference = self.store(Path(temp))
            with self.assertRaises(ValueError):
                build_report(data, reference, datetime(2026, 11, 17))


if __name__ == "__main__":
    unittest.main()
