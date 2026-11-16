"""Prospective forecast journal: frozen inputs, idempotency and censored outcomes."""
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from forecast_ledger import (
    build_forecasts, evaluate_forecasts, issue_forecasts, read_forecasts,
)

UTC = timezone.utc
ID_A = "a" * 24
ID_B = "b" * 24
CUTOFF = datetime(2026, 11, 17, 0, 0, tzinfo=UTC)


class ForecastLedgerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name)
        (self.data / "observations").mkdir()
        (self.data / "runs").mkdir()
        (self.data / "predictions").mkdir()
        self.observations_file = self.data / "observations/2026-11-17.jsonl"
        self.run_file = self.data / "runs/2026-11.jsonl"

    def add_obs(self, event_id, at, volume):
        with self.observations_file.open("a", encoding="utf-8") as out:
            out.write(json.dumps({
                "event_id": event_id,
                "observed_at": at.isoformat(),
                "approx_traffic": f"{volume}+",
                "source": "google_trends_jp",
            }) + "\n")

    def add_run(self, at):
        with self.run_file.open("a") as out:
            out.write(json.dumps({
                "ran_at": at.isoformat(),
                "feeds_ok": 14,
                "observations_added": 2
            }) + "\n")

    def seed(self):
        for event_id, before, latest in ((ID_A, 100, 300), (ID_B, 200, 200)):
            self.add_obs(event_id, CUTOFF - timedelta(minutes=60), before)
            self.add_obs(event_id, CUTOFF, latest)
        self.add_run(CUTOFF)

    def test_issues_only_at_current_run_and_never_backfills(self):
        self.seed()
        stale = build_forecasts(self.data, CUTOFF + timedelta(hours=1),
                                last_run_at=CUTOFF)
        self.assertEqual(stale, [])
        result = issue_forecasts(self.data, CUTOFF + timedelta(minutes=1))
        self.assertEqual(result["issued"], 2)
        predictions = read_forecasts(self.data, CUTOFF + timedelta(minutes=1))
        self.assertEqual(len(predictions), 2)
        self.assertEqual({p["forecast_positive"] for p in predictions}, {True, False})
        self.assertEqual({p["current_observed_at"] for p in predictions},
                         {CUTOFF.isoformat()})
        # Re-running a collector post-step never duplicates historical forecasts.
        self.assertEqual(issue_forecasts(self.data, CUTOFF + timedelta(minutes=2))["issued"], 0)

    def test_future_evidence_does_not_change_frozen_prediction(self):
        self.seed()
        issue_forecasts(self.data, CUTOFF + timedelta(minutes=1))
        snapshot = read_forecasts(self.data, CUTOFF + timedelta(minutes=2))
        self.add_obs(ID_A, CUTOFF + timedelta(hours=3, minutes=1), 600)
        self.add_obs(ID_B, CUTOFF + timedelta(hours=3, minutes=1), 500)
        later = CUTOFF + timedelta(hours=4)
        frozen = read_forecasts(self.data, later)
        self.assertEqual(snapshot, frozen)
        result = evaluate_forecasts(self.data, later)
        self.assertEqual(result["mature_labeled"], 2)
        self.assertEqual(result["confusion_counts"]["true_positive"], 1)
        self.assertEqual(result["confusion_counts"]["false_negative"], 1)
        self.assertIsNone(result["precision_on_observed_rss"])

    def test_missing_future_is_not_failure(self):
        self.seed()
        issue_forecasts(self.data, CUTOFF + timedelta(minutes=1))
        early = evaluate_forecasts(self.data, CUTOFF + timedelta(hours=3))
        self.assertEqual(early["pending"], 2)
        late = evaluate_forecasts(self.data, CUTOFF + timedelta(hours=5))
        self.assertEqual(late["missing_future"], 2)
        self.assertEqual(late["mature_labeled"], 0)
        self.assertEqual(late["confusion_counts"]["false_positive"], 0)

    def test_no_history_no_issue(self):
        self.add_obs(ID_A, CUTOFF, 100)
        self.add_run(CUTOFF)
        self.assertEqual(issue_forecasts(self.data, CUTOFF + timedelta(minutes=1))["issued"], 0)
        self.assertEqual(list((self.data / "predictions").glob("*.jsonl")), [])

    def test_no_invented_forecasts_without_fresh_run(self):
        self.seed()
        result = issue_forecasts(self.data, CUTOFF + timedelta(hours=1))
        self.assertEqual(result["issued"], 0)
        self.assertEqual(list((self.data / "predictions").glob("*.jsonl")), [])

    def test_outcome_uses_issuance_not_stale_observation_time(self):
        self.seed()
        issued = issue_forecasts(self.data, CUTOFF + timedelta(minutes=15))
        self.assertEqual(issued["issued"], 2)
        # The target is 03:15, not 03:00.
        self.add_obs(ID_A, CUTOFF + timedelta(hours=2, minutes=20), 500)
        # 02:20 is 55 min before the issued target and cannot label an outcome.
        result = evaluate_forecasts(self.data, CUTOFF + timedelta(hours=5))
        self.assertEqual(result["missing_future"], 2)


if __name__ == "__main__":
    unittest.main()
