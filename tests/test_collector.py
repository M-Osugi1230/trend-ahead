import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from collector import collect, normalize, parse_rss

TRENDS = b'''<?xml version="1.0" encoding="utf-8"?>
<rss xmlns:ht="https://trends.google.com/trending/rss" version="2.0"><channel>
<item><title>Example Trend</title><link>https://example.org/t</link>
<pubDate>Sun, 15 Nov 2026 02:00:00 GMT</pubDate>
<ht:approx_traffic>2,000+</ht:approx_traffic></item>
</channel></rss>'''
NEWS = b'''<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0"><channel><item><title>Example News</title>
<link>https://example.org/n</link><guid>abc</guid></item></channel></rss>'''
SOURCES = [
    {"id": "trends", "kind": "trends", "category": "全般", "url": "https://example.org/t"},
    {"id": "news", "kind": "news", "category": "ニュース", "url": "https://example.org/n"},
]
NOW = datetime(2026, 11, 15, 9, 0, tzinfo=timezone.utc)


class CollectorTest(unittest.TestCase):
    def test_unicode_normalization(self):
        self.assertEqual(normalize("ＡＢＣ　NEWS"), "abc news")

    def test_parse_trends_metric(self):
        result = parse_rss(TRENDS, SOURCES[0])
        self.assertEqual(result[0]["approx_traffic"], "2,000+")
        self.assertEqual(result[0]["kind"], "trends")

    def test_first_run_and_duplicate_run(self):
        def fake_fetch(url):
            return TRENDS if url.endswith("/t") else NEWS
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            first = collect(SOURCES, path, NOW, fake_fetch)
            second = collect(SOURCES, path, NOW, fake_fetch)
            self.assertEqual(first["events_added"], 2)
            self.assertEqual(second["events_added"], 0)
            self.assertEqual(first["observations_added"], 1)
            self.assertEqual(second["observations_added"], 1)
            events = (path / "events" / "2026-11-15.jsonl").read_text().splitlines()
            observations = (path / "observations" / "2026-11-15.jsonl").read_text().splitlines()
            self.assertEqual(len(events), 2)
            self.assertEqual(len(observations), 2)
            self.assertEqual(json.loads(events[0])["status"], "detected")

    def test_partial_feed_failure_is_logged(self):
        def fake_fetch(url):
            if url.endswith("/n"):
                raise RuntimeError("temporary failure")
            return TRENDS
        with tempfile.TemporaryDirectory() as tmp:
            result = collect(SOURCES, Path(tmp), NOW, fake_fetch)
            self.assertEqual(result["feeds_ok"], 1)
            self.assertEqual(result["feeds_failed"], 1)
            self.assertEqual(result["events_added"], 1)
            self.assertEqual(len(result["errors"]), 1)

    def test_all_failures_log_without_state(self):
        def fail(_):
            raise TimeoutError("offline")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            result = collect(SOURCES, path, NOW, fail)
            self.assertEqual(result["feeds_ok"], 0)
            self.assertFalse((path / "state.json").exists())
            self.assertTrue((path / "runs" / "2026-11.jsonl").exists())


if __name__ == "__main__":
    unittest.main()

