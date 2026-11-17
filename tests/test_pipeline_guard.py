"""Independent monitoring of committed RSS ingestion and forecast issue decisions."""
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pipeline_guard import inspect, markdown
from forecast_ledger import issue_forecasts

UTC = timezone.utc
NOW = datetime(2026, 11, 17, 9, tzinfo=UTC)
TREND_ID = "a" * 24


class PipelineGuardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "runs").mkdir()

    def runs(self, times, *, partial=False, obs=10):
        path = self.root / "runs/2026-11.jsonl"
        with path.open("w") as handle:
            for index, when in enumerate(times):
                row = {
                    "ran_at": when.isoformat(), "feeds_ok": 10 if partial and index == len(times)-1 else 14,
                    "feeds_total": 14, "observations_added": obs, "errors": [],
                }
                handle.write(json.dumps(row) + "\n")

    def audit(self, time, reason="insufficient_history_or_no_recent_collection", issued=0):
        path = self.root / "forecast_runs/2026-11.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as handle:
            handle.write(json.dumps({
                "checked_at": time.isoformat(), "last_run_at": time.isoformat(),
                "issued": issued, "reason": reason,
            }) + "\n")

    def test_missing_run_is_critical_not_healthy(self):
        report = inspect(self.root, NOW)
        self.assertEqual(report["status"], "critical_collection_stale")
        self.assertIsNone(report["collection"]["last_run_at"])

    def test_old_success_is_critical_even_when_all_sources_passed(self):
        self.runs([NOW - timedelta(hours=5)])
        report = inspect(self.root, NOW)
        self.assertEqual(report["status"], "critical_collection_stale")
        self.assertGreater(report["collection"]["minutes_since_last_run"], 120)
        self.assertIn("check", markdown(report).lower())

    def test_fresh_but_sparse_runs_are_degraded(self):
        recent = NOW - timedelta(minutes=5)
        self.runs([recent])
        self.audit(recent)
        report = inspect(self.root, NOW)
        self.assertEqual(report["status"], "degraded_collection")
        self.assertTrue(report["forecast_audit"]["is_current_for_last_saved_run"])

    def test_dense_complete_runs_are_healthy_with_issue_audit(self):
        times = [NOW - timedelta(minutes=30 * i) for i in range(47, -1, -1)]
        self.runs(times)
        self.audit(NOW)
        report = inspect(self.root, NOW)
        self.assertEqual(report["status"], "recent_collection")
        self.assertEqual(report["collection"]["observed_run_records_24h"], 48)
        self.assertEqual(report["forecast_audit"]["state"], "insufficient_history_or_no_recent_collection")

    def test_missing_forecast_issue_audit_is_visible(self):
        times = [NOW - timedelta(minutes=30 * i) for i in range(47, -1, -1)]
        self.runs(times)
        report = inspect(self.root, NOW)
        self.assertEqual(report["status"], "degraded_forecast_audit")
        self.assertEqual(report["forecast_audit"]["state"], "not_reported")

    def test_partial_failure_never_marked_healthy(self):
        times = [NOW - timedelta(minutes=30 * i) for i in range(47, -1, -1)]
        self.runs(times, partial=True)
        self.audit(NOW)
        report = inspect(self.root, NOW)
        self.assertEqual(report["status"], "degraded_collection")

    def test_forecast_decision_recorded_when_no_history(self):
        run_time = NOW - timedelta(minutes=2)
        self.runs([run_time], obs=0)
        result = issue_forecasts(self.root, NOW)
        self.assertEqual(result["issued"], 0)
        self.assertEqual(result["reason"], "no_successful_trend_observations")
        path = self.root / "forecast_runs/2026-11.jsonl"
        entries = [json.loads(line) for line in path.read_text().splitlines()]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["checked_at"], NOW.isoformat())
        self.assertEqual(entries[0]["last_run_at"], run_time.isoformat())

    def test_forecast_journal_written_on_nonpositive_observations(self):
        (self.root / "observations").mkdir()
        t = NOW - timedelta(minutes=2)
        self.runs([t])
        row = {
            "event_id": TREND_ID, "observed_at": t.isoformat(),
            "approx_traffic": "200+", "source": "google_trends_jp",
        }
        (self.root / "observations/2026-11-17.jsonl").write_text(json.dumps(row)+"\n")
        result = issue_forecasts(self.root, NOW)
        self.assertEqual(result["issued"], 0)
        lines = (self.root / "forecast_runs/2026-11.jsonl").read_text().splitlines()
        self.assertEqual(json.loads(lines[-1])["reason"],
                         "insufficient_history_or_no_recent_collection")


if __name__ == "__main__":
    unittest.main()
