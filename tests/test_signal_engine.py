"""Trend signals are observational and reproducible, not ML predictions."""
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from signal_engine import detect_signals, parse_traffic

NOW = datetime(2026, 11, 15, 14, 0, tzinfo=timezone.utc)
ID = "a" * 24


class SignalsTest(unittest.TestCase):
    def test_traffic_parsing(self):
        self.assertEqual(parse_traffic("1,000+"), 1000)
        self.assertEqual(parse_traffic("2万+"), 20000)
        self.assertEqual(parse_traffic("1.5K+"), 1500)
        for text in (None, "", "unknown", "javascript:1", "-100", "100億+", "0+"):
            self.assertIsNone(parse_traffic(text))

    def _run(self, observations, now=NOW):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            folder = data / "observations"
            folder.mkdir()
            (folder / "2026-11-15.jsonl").write_text(
                "\n".join(json.dumps(o) for o in observations) + "\n", encoding="utf-8"
            )
            rows = [{"event_id": ID, "kind": "trends", "title": "Test", "category": "ゲーム"}]
            return detect_signals(data, rows, now)

    def sample(self, minutes_before, traffic, id=ID):
        return {"event_id": id, "observed_at": (NOW - timedelta(minutes=minutes_before)).isoformat(),
                "approx_traffic": traffic, "source": "google_trends_jp"}

    def test_increase_has_evidence_not_probability(self):
        result = self._run([self.sample(30, "200+"), self.sample(0, "500+")])
        self.assertEqual(len(result), 1)
        self.assertTrue(result[0]["growing"])
        self.assertIn("200+ → 500+", result[0]["evidence"])
        self.assertNotIn("probability", result[0])

    def test_repeated_equal_bucket_is_not_called_increase(self):
        result = self._run([self.sample(30, "1000+"), self.sample(5, "1000+")])
        self.assertEqual(result[0]["label"], "複数回観測")
        self.assertFalse(result[0]["growing"])

    def test_single_stale_decreasing_and_malformed_are_excluded(self):
        self.assertEqual(self._run([self.sample(0, "100+")]), [])
        self.assertEqual(self._run([self.sample(35, "2000+"), self.sample(0, "200+")]), [])
        self.assertEqual(self._run([self.sample(180, "200+"), self.sample(150, "500+")]), [])
        self.assertEqual(self._run([self.sample(15, "200+"), self.sample(0, "N/A")]), [])

    def test_same_timestamp_not_independent(self):
        self.assertEqual(self._run([self.sample(0, "200+"), self.sample(0, "500+")]), [])

    def test_future_timestamps_are_excluded(self):
        self.assertEqual(self._run([self.sample(30, "200+"), self.sample(-20, "500+")]), [])

    def test_static_page_shows_evidence_and_json(self):
        from build_site import build
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            events = root / "data/events"
            obs = root / "data/observations"
            events.mkdir(parents=True)
            obs.mkdir(parents=True)
            item = {"event_id": ID, "kind": "trends", "title": "A & B 速報",
                    "category": "全般", "url": "https://example.org/trends",
                    "first_seen_at": (NOW - timedelta(minutes=30)).isoformat()}
            (events / "2026-11-15.jsonl").write_text(json.dumps(item, ensure_ascii=False) + "\n")
            (obs / "2026-11-15.jsonl").write_text(
                "\n".join(json.dumps(x) for x in [self.sample(30, "200+"), self.sample(0, "500+")]) + "\n")
            report = build(root / "data", root / "dist", NOW, "https://example.org/trend-ahead")
            home = (root / "dist/index.html").read_text()
            payload = json.loads((root / "dist/signals.json").read_text())
            self.assertEqual(report["signals_count"], 1)
            self.assertIn("動き始めた話題", home)
            self.assertIn("RSS推定検索量 200+ → 500+", home)
            self.assertIn("A &amp; B", home)
            self.assertIn("予測確率ではありません", home)
            self.assertEqual(payload["signals"][0]["estimate_last"], 500)
            self.assertTrue((root / f"dist/topics/{ID}/index.html").exists())
            self.assertIn("noindex,follow", home)

    def test_news_not_scored_as_search_momentum(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(detect_signals(Path(tmp), [{"event_id": ID, "kind": "news"}], NOW), [])


if __name__ == "__main__":
    unittest.main()
