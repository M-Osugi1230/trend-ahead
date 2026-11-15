"""v0.7 regression: users see events before explanatory or promotional content."""
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from build_site import build

UTC = timezone.utc
NOW = datetime(2026, 11, 16, 9, tzinfo=UTC)
SITE = "https://m-osugi1230.github.io/trend-ahead"


class ContentDensityTest(unittest.TestCase):
    def _build(self, root):
        data = root / "data"
        data.mkdir()
        rows = []
        for n, category in enumerate(("ゲーム", "スポーツ", "映画・アニメ")):
            rows.append({
                "title": "注目イベント" + str(n + 1),
                "description": "この文章は詳細ページだけに表示する説明です。" * 2,
                "starts_at": (NOW + timedelta(days=n + 2)).isoformat(),
                "verified_at": (NOW - timedelta(hours=1)).isoformat(),
                "official_url": "https://example.org/event-" + str(n + 1),
                "category": category,
            })
        (data / "upcoming.json").write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
        build(data, root / "dist", NOW, SITE)
        return ((root / "dist/index.html").read_text(encoding="utf-8"),
                (root / "dist/week/index.html").read_text(encoding="utf-8"))

    def test_first_screen_contains_scannable_events_without_promotional_sections(self):
        with tempfile.TemporaryDirectory() as tmp:
            home, _ = self._build(Path(tmp))
            self.assertIn('<h1>これからの予定</h1>', home)
            self.assertNotIn('<section class="discovery-heading">', home)
            self.assertNotIn('<section class="section wrap feature-section">', home)
            self.assertNotIn('<section class="section wrap faq">', home)
            self.assertEqual(home.count('class="upcoming-card upcoming-card-compact"'), 3)
            self.assertLess(home.index('id="future-list"'), home.index('id="signals"'))
            self.assertLess(home.index('id="upcoming"'), home.index('id="now"'))
            self.assertLess(home.index('注目イベント1'), home.index('注目イベント2'))

    def test_event_cards_are_short_but_sources_and_calendar_actions_remain(self):
        with tempfile.TemporaryDirectory() as tmp:
            home, week = self._build(Path(tmp))
            self.assertIn('<time datetime="', home)
            self.assertIn('event-category', home)
            self.assertIn('>公式情報 ↗</a>', home)
            self.assertIn('>予定に追加 ＋</a>', home)
            self.assertNotIn('<p>この文章は詳細ページだけに表示する説明です。', home)
            self.assertIn('<p>この文章は詳細ページだけに表示する説明です。', week)
            self.assertIn('data-search="', home)
            self.assertIn('href="/trend-ahead/calendar/', home)
            self.assertIn('href="https://example.org/event-1"', home)

    def test_search_and_safety_stay_accessible(self):
        with tempfile.TemporaryDirectory() as tmp:
            home, _ = self._build(Path(tmp))
            for needle in ('id="future-search"', 'id="future-category"', 'data-future-window="7"',
                           'id="topic-search"', 'id="interest-panel"', 'id="signals"',
                           'class="front-data-status"', 'noindex,follow', '登録不要'):
                self.assertIn(needle, home)
            self.assertIn('<details class="signal-method">', home)
            self.assertIn('予測確率ではありません', home)
            self.assertIn('data-share="https://m-osugi1230.github.io/trend-ahead/"', home)


if __name__ == "__main__":
    unittest.main()
