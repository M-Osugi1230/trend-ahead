"""Information-first landing page regressions: immediate future events and filters."""
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from build_site import build

NOW = datetime(2026, 11, 16, 1, 0, tzinfo=timezone.utc)
SITE = "https://m-osugi1230.github.io/trend-ahead"


class FutureFirstViewTest(unittest.TestCase):
    def make_event(self, title, category, delay_days):
        return {
            "title": title,
            "category": category,
            "description": "公式日時を確認済み。開催前にチェック。",
            "starts_at": (NOW + timedelta(days=delay_days)).isoformat(),
            "verified_at": (NOW - timedelta(hours=1)).isoformat(),
            "official_url": "https://example.org/official",
        }

    def test_future_cards_are_first_and_filterable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = root / "data"
            data.mkdir()
            events = [
                self.make_event("ゲーム発表会", "ゲーム", 2),
                self.make_event("サッカー決勝", "スポーツ", 12),
                self.make_event("アニメイベント", "映画・アニメ", 35),
            ]
            (data / "upcoming.json").write_text(
                json.dumps(events, ensure_ascii=False), encoding="utf-8"
            )
            result = build(data, root / "dist", NOW, SITE)
            html = (root / "dist/index.html").read_text(encoding="utf-8")
            self.assertEqual(result["upcoming_cards"], 3)
            self.assertLess(html.index('id="upcoming"'), html.index('id="now"'))
            self.assertLess(html.index('id="future-list"'), html.index('id="signals"'))
            self.assertLess(html.index("ゲーム発表会"), html.index("サッカー決勝"))
            self.assertIn('id="future-search"', html)
            self.assertIn('id="future-category"', html)
            for value in ("all", "7", "30"):
                self.assertIn('data-future-window="' + value + '"', html)
            self.assertIn('data-starts-at="', html)
            self.assertIn('data-search="', html)
            self.assertIn('id="future-result-status"', html)
            self.assertIn('href="/trend-ahead/week/"', html)
            self.assertNotIn('<section class="hero">', html)
            self.assertIn('noindex,follow', html)

    def test_empty_future_panel_does_not_create_dummy_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = build(root / "data", root / "dist", NOW, SITE)
            html = (root / "dist/index.html").read_text(encoding="utf-8")
            self.assertEqual(result["upcoming_cards"], 0)
            self.assertIn("確認済みの予定イベントは準備中", html)
            self.assertIn('id="future-search"', html)
            self.assertNotIn("2026 F1", html)

    def test_frontend_includes_all_filter_wiring_and_no_telemetry(self):
        script = (Path(__file__).resolve().parents[1] / "web/assets/app.js").read_text(encoding="utf-8")
        self.assertIn("function filterFuture()", script)
        self.assertIn("futureSearch?.addEventListener('input', filterFuture)", script)
        self.assertIn("futureCategory?.addEventListener('change', filterFuture)", script)
        self.assertIn("option.setAttribute('aria-pressed', String(active))", script)
        self.assertIn("card.hidden = !show", script)
        self.assertNotIn("fetch('/tracking", script)


if __name__ == "__main__":
    unittest.main()
