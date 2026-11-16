"""Collection monitor and per-source HTTP 429 recovery tests."""
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError

from collection_health import audit
from collector import collect

UTC = timezone.utc
NOW = datetime(2026, 11, 17, 0, tzinfo=UTC)
TRENDS = b'<rss version="2.0"><channel><item><title>Test</title><link>https://example.org/1</link></item></channel></rss>'
SOURCES = [
    {"id": "trends", "kind": "trends", "category": "全般", "url": "https://example.org/t"},
    {"id": "news", "kind": "news", "category": "全般", "url": "https://example.org/n"},
]


class CollectionHealthTest(unittest.TestCase):
    def test_no_recent_records_are_not_called_healthy(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = audit(Path(tmp), NOW)
            self.assertEqual(result["status"], "no_recent_runs")
            self.assertEqual(result["observed_run_records_24h"], 0)

    def test_gap_and_429_are_visible(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "runs").mkdir()
            rows = [
                {"ran_at": (NOW - timedelta(hours=5)).isoformat(),
                 "feeds_ok": 14, "feeds_total": 14, "errors": []},
                {"ran_at": (NOW - timedelta(minutes=10)).isoformat(),
                 "feeds_ok": 13, "feeds_total": 14,
                 "errors": [{"source": "news", "error": "HTTPError: HTTP Error 429"}]},
            ]
            (root / "runs/2026-11.jsonl").write_text(
                "\n".join(json.dumps(r) for r in rows) + "\n"
            )
            report = audit(root, NOW)
            self.assertEqual(report["status"], "degraded")
            self.assertEqual(report["observed_run_records_24h"], 2)
            self.assertEqual(report["runs_with_429_24h"], 1)
            self.assertGreater(report["max_gap_minutes"], 240)
            self.assertEqual(report["last_feeds_ok"], 13)

    def test_429_one_hour_skip_then_retry_without_immediate_hammering(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            attempts = {"news": 0, "trends": 0}

            def fetch(url):
                name = "news" if url.endswith("/n") else "trends"
                attempts[name] += 1
                if name == "news":
                    raise HTTPError(url, 429, "Too Many Requests", {}, None)
                return TRENDS

            first = collect(SOURCES, path, NOW, fetch)
            self.assertEqual(first["feeds_ok"], 1)
            self.assertEqual(first["feeds_failed"], 1)
            self.assertEqual(first["feeds_skipped_rate_limit"], 0)
            health = json.loads((path / "source_health.json").read_text())
            self.assertEqual(health["news"]["consecutive_429"], 1)
            second = collect(SOURCES, path, NOW + timedelta(minutes=30), fetch)
            self.assertEqual(second["feeds_ok"], 1)
            self.assertEqual(second["feeds_failed"], 0)
            self.assertEqual(second["feeds_skipped_rate_limit"], 1)
            self.assertEqual(attempts["news"], 1)
            third = collect(SOURCES, path, NOW + timedelta(hours=1), fetch)
            self.assertEqual(third["feeds_failed"], 1)
            self.assertEqual(attempts["news"], 2)
            health = json.loads((path / "source_health.json").read_text())
            self.assertEqual(health["news"]["consecutive_429"], 2)

    def test_non_429_errors_do_not_silently_enter_cooldown(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            def bad(url):
                if url.endswith("/n"):
                    raise TimeoutError("network unavailable")
                return TRENDS

            collect(SOURCES, root, NOW, bad)
            second = collect(SOURCES, root, NOW + timedelta(minutes=30), bad)
            self.assertEqual(second["feeds_skipped_rate_limit"], 0)
            self.assertEqual(second["feeds_failed"], 1)


if __name__ == "__main__":
    unittest.main()
