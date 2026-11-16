"""Official YouTube API discovery must stay opt-in, channel-scoped and review-only."""
import unittest
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

from youtube_upcoming import discover, review_markdown, validate_channels

NOW = datetime(2026, 11, 17, 0, 10, tzinfo=timezone(timedelta(hours=9)))
CHANNEL_ID = "UC" + "A" * 22
OTHER_CHANNEL = "UC" + "B" * 22
VIDEO_ID = "ABCDEFGHIJK"
EXTRA_ID = "LMNOPQRSTUV"
OLD_ID = "WXYZ1234567"


class YoutubeReviewTest(unittest.TestCase):
    def channel(self):
        return {
            "channel_id": CHANNEL_ID, "name": "公式ゲーム配信チャンネル",
            "category": "ゲーム", "official_confirmed": True,
        }

    def test_no_key_does_not_contact_google(self):
        def forbidden(url):
            raise AssertionError("network must be disabled without a key")
        report = discover([self.channel()], "", NOW, forbidden)
        self.assertEqual(report["status"], "not_configured")
        self.assertEqual(report["candidates"], [])
        self.assertIn("APIキーが未設定", review_markdown(report))

    def test_only_explicit_approved_official_channels(self):
        self.assertEqual(validate_channels([dict(self.channel(), official_confirmed=False)]), [])
        self.assertEqual(validate_channels([dict(self.channel(), channel_id="fakeID")]), [])
        self.assertEqual(len(validate_channels([self.channel(), self.channel()])), 1)
        report = discover([dict(self.channel(), official_confirmed=False)], "dummy", NOW,
                          lambda url: (_ for _ in ()).throw(AssertionError("should not call")))
        self.assertEqual(report["status"], "no_approved_channels")

    def test_exact_scheduled_start_and_channel_scope(self):
        start = NOW + timedelta(days=2)
        calls = []
        def fake_get(url):
            calls.append(url)
            q = parse_qs(urlsplit(url).query)
            self.assertEqual(q.get("key"), ["secret-in-memory"])
            if urlsplit(url).path.endswith("/search"):
                self.assertEqual(q["eventType"], ["upcoming"])
                self.assertEqual(q["channelId"], [CHANNEL_ID])
                return {"items": [
                    {"id": {"videoId": VIDEO_ID},
                     "snippet": {"channelId": CHANNEL_ID, "liveBroadcastContent": "upcoming"}},
                    {"id": {"videoId": EXTRA_ID},
                     "snippet": {"channelId": CHANNEL_ID, "liveBroadcastContent": "upcoming"}},
                    {"id": {"videoId": OLD_ID},
                     "snippet": {"channelId": OTHER_CHANNEL, "liveBroadcastContent": "upcoming"}},
                ]}
            self.assertTrue(urlsplit(url).path.endswith("/videos"))
            return {"items": [
                {"id": VIDEO_ID, "snippet": {
                    "channelId": CHANNEL_ID, "liveBroadcastContent": "upcoming",
                    "title": "公式ゲームの重大発表配信",
                }, "liveStreamingDetails": {
                    "scheduledStartTime": start.isoformat(),
                }},
                {"id": EXTRA_ID, "snippet": {
                    "channelId": CHANNEL_ID, "liveBroadcastContent": "upcoming",
                    "title": "開始済みのライブ番組",
                }, "liveStreamingDetails": {
                    "scheduledStartTime": start.isoformat(),
                    "actualStartTime": NOW.isoformat(),
                }},
                {"id": OLD_ID, "snippet": {
                    "channelId": OTHER_CHANNEL, "liveBroadcastContent": "upcoming",
                    "title": "他社の動画",
                }, "liveStreamingDetails": {"scheduledStartTime": start.isoformat()}},
            ]}
        report = discover([self.channel()], "secret-in-memory", NOW, fake_get)
        self.assertEqual(len(calls), 2)
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["queued"], 1)
        item = report["candidates"][0]
        self.assertEqual(item["title"], "公式ゲームの重大発表配信")
        self.assertEqual(item["starts_at"], start.isoformat())
        self.assertEqual(item["official_url"], "https://www.youtube.com/watch?v=" + VIDEO_ID)
        self.assertEqual(item["status"], "needs_review")
        self.assertEqual(item["event_type"], "official_scheduled_livestream")
        self.assertNotIn("verified_at", item)
        self.assertNotIn("secret-in-memory", str(report))
        self.assertNotIn("secret-in-memory", review_markdown(report))

    def test_missing_start_or_outside_30_days_is_rejected(self):
        def fake_get(url):
            if urlsplit(url).path.endswith("/search"):
                return {"items": [
                    {"id": {"videoId": VIDEO_ID},
                     "snippet": {"channelId": CHANNEL_ID, "liveBroadcastContent": "upcoming"}},
                    {"id": {"videoId": EXTRA_ID},
                     "snippet": {"channelId": CHANNEL_ID, "liveBroadcastContent": "upcoming"}},
                ]}
            return {"items": [
                {"id": VIDEO_ID, "snippet": {
                    "channelId": CHANNEL_ID, "liveBroadcastContent": "upcoming", "title": "日程未定の配信",
                }, "liveStreamingDetails": {}},
                {"id": EXTRA_ID, "snippet": {
                    "channelId": CHANNEL_ID, "liveBroadcastContent": "upcoming", "title": "ずっと先の公式配信",
                }, "liveStreamingDetails": {
                    "scheduledStartTime": (NOW + timedelta(days=50)).isoformat()
                }},
            ]}
        report = discover([self.channel()], "dummy", NOW, fake_get)
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["queued"], 0)

    def test_http_errors_are_scrubbed(self):
        def fail(url):
            raise RuntimeError("test API error with dangerous key in URL")
        report = discover([self.channel()], "do-not-log-key", NOW, fail)
        self.assertEqual(report["status"], "unavailable")
        self.assertEqual(report["errors"][0]["error"], "youtube_api_or_response_error")
        self.assertNotIn("do-not-log-key", str(report))


if __name__ == "__main__":
    unittest.main()
