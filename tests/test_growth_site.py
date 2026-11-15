"""Regression tests for organic sharing UX under a GitHub Pages project path."""
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from build_site import build

NOW = datetime(2026, 11, 15, 15, tzinfo=timezone.utc)
SITE = "https://m-osugi1230.github.io/trend-ahead"


class SharingSiteTest(unittest.TestCase):
    def test_home_and_week_have_share_controls_and_project_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = build(root / "data", root / "dist", NOW, SITE)
            home = (root / "dist/index.html").read_text(encoding="utf-8")
            week = (root / "dist/week/index.html").read_text(encoding="utf-8")
            self.assertIn('data-share="https://m-osugi1230.github.io/trend-ahead/"', home)
            self.assertIn('data-share="https://m-osugi1230.github.io/trend-ahead/week/"', week)
            self.assertIn('href="/trend-ahead/week/"', home)
            self.assertIn('href="/trend-ahead/calendar/upcoming.ics"', week)
            self.assertIn("noindex,follow", home)
            self.assertFalse((root / "dist/sitemap.xml").exists())
            self.assertEqual(result["observed_cards"], 0)

    def test_no_fake_waitlist_or_automatic_outreach(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            build(root / "data", root / "dist", NOW, SITE)
            home = (root / "dist/index.html").read_text(encoding="utf-8")
            self.assertNotIn("今すぐ登録して通知を受け取る", home)
            self.assertIn("登録不要", home)


if __name__ == "__main__":
    unittest.main()
