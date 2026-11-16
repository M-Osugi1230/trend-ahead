"""Review queue approval must be explicit, fresh, and atomic."""
import copy
import unittest
from datetime import datetime, timedelta, timezone

from review_apply import prepare

NOW = datetime(2026, 11, 16, 9, tzinfo=timezone(timedelta(hours=9)))
CANDIDATE = {
    "candidate_id": "a" * 20, "title": "公式展示会 2026",
    "starts_at": "2026-11-22", "category": "季節・イベント",
    "status": "needs_review", "official_url": "https://www.bigsight.jp/visitor/event/"
}
QUEUE = {"publication": "manual_editorial_review_only", "candidates": [CANDIDATE]}


class ReviewApplyTest(unittest.TestCase):
    def approved(self):
        return {
            "candidate_id": CANDIDATE["candidate_id"], "title": CANDIDATE["title"],
            "starts_at": CANDIDATE["starts_at"], "category": CANDIDATE["category"],
            "description": "主催者による開催日を確認済み", "official_url": "https://example.org/official",
            "verified_at": NOW.isoformat(), "reviewer": "editor_1",
            "review_note": "公式の開催日を目視確認した", "status": "approved",
            "source_confirmed": True,
        }

    def test_valid_editorial_approval_prepares_not_auto_writes(self):
        original = [{"title": "既存の予定", "starts_at": "2026-12-01",
                     "official_url": "https://example.org/already"}]
        frozen = copy.deepcopy(original)
        result, stats = prepare(QUEUE, [self.approved()], original, NOW)
        self.assertEqual(stats["new"], 1)
        self.assertEqual(stats["errors"], [])
        self.assertEqual(original, frozen)
        self.assertEqual(len(result), 2)
        self.assertNotIn("source_confirmed", result[0])
        self.assertNotIn("reviewer", result[0])

    def test_unapproved_or_unchecked_never_merges(self):
        for patch in (
            {"source_confirmed": False},
            {"status": "needs_review"},
            {"verified_at": (NOW - timedelta(days=3)).isoformat()},
            {"starts_at": "2026-11-23"},
            {"official_url": "http://example.org"},
            {"candidate_id": "f" * 20},
            {"title": "書き換えた別のイベント"},
        ):
            item = dict(self.approved(), **patch)
            result, stats = prepare(QUEUE, [item], [], NOW)
            self.assertEqual(stats["new"], 0, patch)
            self.assertEqual(len(stats["errors"]), 1, patch)
            self.assertEqual(result, [], patch)

    def test_duplicate_event_is_rejected(self):
        existing = [{k: self.approved()[k] for k in
                     ("title", "starts_at", "category", "description", "official_url", "verified_at")}]
        result, stats = prepare(QUEUE, [self.approved()], existing, NOW)
        self.assertEqual(stats["duplicates_rejected"], 1)
        self.assertTrue(stats["errors"])
        self.assertEqual(result, existing)

    def test_youtube_scheduled_time_can_be_approved_only_exactly(self):
        youtube = {
            "candidate_id": "youtube_candidate_0001", "title": "ゲーム公式生配信のお知らせ",
            "starts_at": "2026-11-22T19:00:00+09:00",
            "category": "ゲーム", "status": "needs_review",
            "official_url": "https://www.youtube.com/watch?v=ABCDEFGHIJK"
        }
        queue = {"publication": "manual_editorial_review_only", "candidates": [youtube]}
        approval = {
            "candidate_id": youtube["candidate_id"],
            "title": youtube["title"],
            "starts_at": youtube["starts_at"],
            "category": youtube["category"],
            "description": "公式チャンネルで配信日時を確認",
            "official_url": youtube["official_url"],
            "verified_at": NOW.isoformat(),
            "reviewer": "editor_1",
            "review_note": "動画ページの配信予約を目視確認",
            "status": "approved", "source_confirmed": True,
        }
        result, stats = prepare(queue, [approval], [], NOW)
        self.assertEqual(stats["errors"], [])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["starts_at"], youtube["starts_at"])

        for altered in ("2026-11-22", "2026-11-22T19:30:00+09:00"):
            result, stats = prepare(queue, [dict(approval, starts_at=altered)], [], NOW)
            self.assertEqual(result, [])
            self.assertEqual(len(stats["errors"]), 1)

    def test_missing_review_origin_is_rejected(self):
        with self.assertRaises(ValueError):
            prepare({"candidates": [CANDIDATE]}, [self.approved()], [], NOW)

    def test_exact_time_must_have_timezone(self):
        item = dict(self.approved(), starts_at="2026-11-22T11:30:00")
        result, stats = prepare(QUEUE, [item], [], NOW)
        self.assertEqual(stats["new"], 0)
        self.assertTrue(stats["errors"])


if __name__ == "__main__":
    unittest.main()
