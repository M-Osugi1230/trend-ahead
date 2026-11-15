"""Checks leakage boundaries and missing-outcome handling in the research backtest."""
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backtest import eligible_case, evaluate, load_samples

UTC = timezone.utc
JST = timezone(timedelta(hours=9))
NOW = datetime(2026, 11, 16, 15, tzinfo=UTC)
ID_A, ID_B = "a" * 24, "b" * 24


class BacktestTest(unittest.TestCase):
    def _row(self, event_id, dt, traffic):
        return {"event_id": event_id, "observed_at": dt.isoformat(),
                "approx_traffic": traffic, "source": "google_trends_jp"}

    def _store(self, root, rows):
        observations = root / "observations"
        observations.mkdir(parents=True, exist_ok=True)
        (observations / "2026-11-15.jsonl").write_text(
            "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8"
        )

    def test_no_history_or_no_future_means_unknown(self):
        cutoff = datetime(2026, 11, 15, 1, tzinfo=UTC)
        series = [(cutoff - timedelta(minutes=45), 200),
                  (cutoff - timedelta(minutes=15), 300)]
        self.assertIsNone(eligible_case(series[:1], cutoff))
        candidate = eligible_case(series, cutoff)
        self.assertIsNotNone(candidate)
        self.assertIsNone(candidate["outcome"])

    def test_future_cannot_be_used_as_prior_feature(self):
        cutoff = datetime(2026, 11, 15, 1, tzinfo=UTC)
        series = [(cutoff - timedelta(minutes=45), 100),
                  (cutoff - timedelta(minutes=15), 200),
                  (cutoff + timedelta(hours=3), 500)]
        candidate = eligible_case(series, cutoff)
        self.assertEqual(candidate["current_volume"], 200)
        self.assertEqual(candidate["outcome"], 1)
        series[-1] = (cutoff + timedelta(hours=3), 200)
        modified = eligible_case(series, cutoff)
        self.assertEqual(candidate["momentum_score"], modified["momentum_score"])
        self.assertEqual(modified["outcome"], 0)

    def test_future_only_timestamp_does_not_create_history(self):
        cutoff = datetime(2026, 11, 15, 1, tzinfo=UTC)
        series = [(cutoff - timedelta(minutes=20), 200),
                  (cutoff + timedelta(minutes=3), 500),
                  (cutoff + timedelta(hours=3), 600)]
        self.assertIsNone(eligible_case(series, cutoff))

    def test_missing_outcomes_do_not_count_as_negative(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            local = datetime(2026, 11, 15, 10, tzinfo=JST)
            samples = [self._row(ID_A, local - timedelta(minutes=45), "200+"),
                       self._row(ID_A, local - timedelta(minutes=15), "300+")]
            self._store(root, samples)
            report = evaluate(root, NOW, minimum_cases=1, minimum_days=1)
            self.assertEqual(report["labeled_cases"], 0)
            self.assertGreaterEqual(report["missing_future_outcomes"], 1)
            self.assertEqual(report["status"], "insufficient_data")
            self.assertIsNone(report["precision_at_3_momentum"])

    def test_duplicate_observation_at_same_time_collapsed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._store(root, [self._row(ID_A, NOW, "100+"),
                               self._row(ID_A, NOW, "200+")])
            self.assertEqual(len(load_samples(root, NOW)[ID_A]), 1)

    def test_synthetic_multi_day_cohorts_are_exploratory_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rows = []
            for day in (13, 14, 15):
                local = datetime(2026, 11, day, 10, tzinfo=JST)
                for event_id in (ID_A, ID_B):
                    rows.extend([
                        self._row(event_id, local - timedelta(minutes=45), "100+"),
                        self._row(event_id, local - timedelta(minutes=15), "200+"),
                        self._row(event_id, local + timedelta(hours=3), "500+"),
                    ])
            self._store(root, rows)
            report = evaluate(root, NOW, minimum_cases=5, minimum_days=3)
            self.assertEqual(report["status"], "exploratory_evaluation_available")
            self.assertGreaterEqual(report["labeled_cases"], 6)
            self.assertEqual(report["positive_cases"], report["labeled_cases"])
            self.assertEqual(report["precision_at_3_momentum"], 1.0)
            self.assertEqual(report["precision_at_3_volume_baseline"], 1.0)

    def test_empty_dataset_is_insufficient(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = evaluate(Path(tmp), NOW)
            self.assertEqual(report["labeled_cases"], 0)
            self.assertEqual(report["status"], "insufficient_data")
            self.assertIn("Missing future", " ".join(report["caveats"]))


if __name__ == "__main__":
    unittest.main()
