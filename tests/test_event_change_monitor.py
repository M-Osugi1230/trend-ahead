"""Change monitoring must not mistake source disappearance for confirmed cancellation."""
import unittest
from datetime import datetime, timedelta, timezone

from event_change_monitor import compare, markdown, snapshot

JST = timezone(timedelta(hours=9))
NOW = datetime(2026, 11, 17, 8, tzinfo=JST)


def event(title, start, source="venue", event_type="concert"):
    return {
        "candidate_id": "dummy", "source_id": source, "title": title,
        "starts_at": start, "event_type": event_type,
    }


def report(rows, when=NOW, source_status="ok"):
    return {
        "generated_at": when.isoformat(),
        "status": "ok", "publication": "manual_editorial_review_only",
        "sources": [{"source_id": "venue", "status": source_status}],
        "candidates": [],
        "observed": rows,
    }


class EventChangeMonitorTest(unittest.TestCase):
    def test_first_run_only_creates_baseline(self):
        current = report([event("年末ライブ", "2026-12-12")])
        result = compare(None, current, NOW)
        self.assertEqual(result["status"], "baseline_only")
        self.assertEqual(result["date_changes_to_check"], [])
        self.assertEqual(result["no_longer_seen_to_check"], [])
        self.assertEqual(snapshot(current)["observed"], current["observed"])

    def test_same_title_change_date_is_only_a_review_lead(self):
        previous = snapshot(report([event("年末ライブ", "2026-12-12")],
                                   NOW - timedelta(days=1)))
        current = report([event("年末ライブ", "2026-12-19")])
        result = compare(previous, current, NOW)
        self.assertEqual(result["status"], "attention")
        self.assertEqual(result["date_changes_to_check"][0]["previous"], "2026-12-12")
        self.assertEqual(result["date_changes_to_check"][0]["current"], "2026-12-19")
        self.assertNotIn("verified_at", result["date_changes_to_check"][0])
        self.assertIn("延期・中止情報ではありません", markdown(result))

    def test_unseen_is_not_cancelled_and_new_item_is_only_a_sighting(self):
        old = snapshot(report([event("旧ライブ", "2026-12-03")],
                              NOW - timedelta(hours=23)))
        current = report([event("新ライブ", "2026-12-04")])
        result = compare(old, current, NOW)
        self.assertEqual(len(result["no_longer_seen_to_check"]), 1)
        self.assertEqual(len(result["new_sightings"]), 1)
        self.assertIn("not_proof_of_cancellation",
                      result["no_longer_seen_to_check"][0]["reason"])
        self.assertEqual(result["publication"], "manual_editorial_review_only")

    def test_failed_source_is_never_interpreted_as_cancellation(self):
        old = snapshot(report([event("年末ライブ", "2026-12-03")],
                              NOW - timedelta(days=1)))
        failed = report([], source_status="fetch_or_parse_error")
        result = compare(old, failed, NOW)
        self.assertEqual(result["status"], "no_comparable_sources")
        self.assertEqual(result["no_longer_seen_to_check"], [])
        self.assertEqual(len(result["source_issues"]), 1)

    def test_partial_source_failure_flags_attention_without_false_cancellation(self):
        previous = snapshot(report(
            [event("予定A", "2026-12-03"), event("予定B", "2026-12-05", source="other")],
            NOW - timedelta(hours=18)))
        previous["sources"].append({"source_id": "other", "status": "ok"})
        current = report([event("予定A", "2026-12-03")])
        current["sources"].append({"source_id": "other",
                                   "status": "fetch_or_parse_error"})
        result = compare(previous, current, NOW)
        self.assertEqual(result["status"], "attention")
        self.assertEqual(result["no_longer_seen_to_check"], [])
        self.assertEqual(result["comparison_sources"], ["venue"])
        self.assertEqual(result["source_issues"][0]["source_id"], "other")

    def test_stale_baseline_never_causes_change_alert(self):
        old = snapshot(report([event("年末ライブ", "2026-12-03")],
                              NOW - timedelta(days=9)))
        result = compare(old, report([]), NOW)
        self.assertEqual(result["status"], "stale_baseline")
        self.assertEqual(result["no_longer_seen_to_check"], [])

    def test_multi_day_performances_are_ambiguous_not_forced_reschedule(self):
        old = snapshot(report([
            event("フェス", "2026-12-04"),
            event("フェス", "2026-12-05"),
        ], NOW - timedelta(days=1)))
        result = compare(old, report([
            event("フェス", "2026-12-04"),
            event("フェス", "2026-12-06"),
        ]), NOW)
        self.assertEqual(result["date_changes_to_check"], [])
        self.assertEqual(len(result["ambiguous_groups"]), 1)

    def test_past_events_do_not_create_false_alerts(self):
        old = snapshot(report([event("終了した祭", "2026-11-16")],
                              NOW - timedelta(hours=20)))
        result = compare(old, report([]), NOW)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["no_longer_seen_to_check"], [])

    def test_same_event_already_curated_is_still_monitored(self):
        # Such an item was omitted from unapproved candidates but saved
        # among the raw first-party sightings to enable source-side monitoring.
        old = report([event("公式発売作品", "2026-12-25")],
                     NOW - timedelta(days=1))
        old["candidates"] = []
        current = report([])
        result = compare(snapshot(old), current, NOW)
        self.assertEqual(len(result["no_longer_seen_to_check"]), 1)


if __name__ == "__main__":
    unittest.main()
