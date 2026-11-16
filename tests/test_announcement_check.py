"""Verify month-only official updates without pretending page absence is cancellation."""
import unittest
from datetime import datetime, timezone, timedelta

from announcement_check import extract_months, check, markdown

NOW = datetime(2026, 11, 17, 1, 0, tzinfo=timezone(timedelta(hours=9)))
ITEMS = [{
    "title": "アオハル・ワンスモア", "period": "2027-01",
    "official_url": "https://www.tv-tokyo.co.jp/anime/aoharuoncemore/",
    "verified_at": (NOW - timedelta(days=2)).isoformat(),
}]


class MonthCheckTest(unittest.TestCase):
    def test_reads_visible_text_and_image_alt_but_not_js(self):
        html = """<html><head><script>2027年2月放送開始</script>
        <style>.other{content:'2027年3月'}</style></head>
        <body><h2>2027年1月放送決定</h2>
        <img alt="放送開始は2027年01月" src="/a.jpg">
        </body></html>"""
        self.assertEqual(extract_months(html), ["2027-01"])

    def test_same_period_is_not_a_change_alert(self):
        result = check(ITEMS, NOW, lambda url: "<p>2027年1月から放送！</p>")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["month_still_visible"], 1)
        self.assertEqual(result["review_required"], 0)
        self.assertEqual(result["publication"], "manual_editorial_review_only")

    def test_other_month_is_only_review_not_an_automatic_postponement(self):
        result = check(ITEMS, NOW, lambda url: "<p>2027年2月放送予定</p>")
        self.assertEqual(result["status"], "review_required")
        self.assertEqual(result["items"][0]["status"], "different_month_visible_review")
        self.assertNotIn("verified_at", result["items"][0])
        self.assertIn("中止・変更確定の判定ではなく", markdown(result))

    def test_unreadable_or_absent_is_not_cancellation(self):
        for fetcher in (
            lambda url: "<p>Coming soon</p>",
            lambda url: (_ for _ in ()).throw(OSError("robots disallows")),
            lambda url: (_ for _ in ()).throw(ValueError("redirect blocked")),
        ):
            result = check(ITEMS, NOW, fetcher)
            self.assertEqual(result["review_required"], 1)
            self.assertEqual(result["status"], "review_required")
            self.assertNotIn("cancelled", str(result))

    def test_old_editorial_check_is_never_silently_trusted(self):
        old = dict(ITEMS[0],
                   verified_at=(NOW - timedelta(days=35)).isoformat())
        result = check([old], NOW, lambda url: "<p>2027年1月放送予定</p>")
        self.assertEqual(result["status"], "review_required")
        self.assertEqual(result["review_required"], 1)
        self.assertEqual(result["items"][0]["status"],
                         "editor_verification_older_than_30_days")
        self.assertTrue(result["items"][0]["verification_stale"])
        self.assertEqual(result["items"][0]["observed_months"], ["2027-01"])

    def test_missing_catalog_means_zero_checks(self):
        result = check([], NOW, lambda _: (_ for _ in ()).throw(AssertionError("no network")))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["checked"], 0)


if __name__ == "__main__":
    unittest.main()
