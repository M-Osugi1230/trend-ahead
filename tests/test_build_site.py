"""Regression tests for the public-site build and SEO safety gates."""
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from build_site import build, load_upcoming, safe_url, calendar_id, make_ics, make_calendar_feed, collection_status

NOW = datetime(2026, 11, 15, 6, 0, tzinfo=timezone.utc)


class PublicSiteTest(unittest.TestCase):
    def test_empty_site_explains_data_gap_without_fake_trends(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = build(root / 'data', root / 'dist', NOW)
            home = (root / 'dist/index.html').read_text(encoding='utf-8')
            self.assertEqual(report['observed_cards'], 0)
            self.assertIn('表示できる観測候補はありません', home)
            self.assertIn('noindex,follow', home)
            self.assertFalse((root / 'dist/sitemap.xml').exists())
            self.assertTrue((root / 'dist/robots.txt').exists())

    def test_live_build_with_one_observation_escapes_html(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / 'data/events'
            folder.mkdir(parents=True)
            event_id = 'a' * 24
            content = {
                'event_id': event_id,
                'first_seen_at': NOW.isoformat(),
                'kind': 'trends',
                'category': '全般',
                'title': '<script>alert("x")</script> 話題',
                'url': 'https://example.com/news',
                'approx_traffic': '10000+',
            }
            (folder / '2026-11-15.jsonl').write_text(json.dumps(content, ensure_ascii=False) + '\n', encoding='utf-8')
            stats = build(root / 'data', root / 'dist', NOW,
                          'https://example.github.io/trend-ahead')
            self.assertEqual(stats['observed_cards'], 1)
            self.assertEqual(stats['detail_pages'], 1)
            home = (root / 'dist/index.html').read_text(encoding='utf-8')
            detail = (root / f'dist/topics/{event_id}/index.html').read_text(encoding='utf-8')
            self.assertNotIn('<script>alert', home)
            self.assertIn('&lt;script&gt;alert', home)
            self.assertIn('href="/trend-ahead/topics/', home)
            self.assertIn('src="/trend-ahead/assets/app.js"', home)
            self.assertIn('noindex,follow', detail)
            self.assertIn('noindex,follow', home)
            self.assertFalse((root / 'dist/sitemap.xml').exists())
            stats = build(root / 'data', root / 'dist', NOW,
                          'https://example.github.io/trend-ahead',
                          publish_ready=True, operator_contact='public@example.org')
            sitemap = (root / 'dist/sitemap.xml').read_text(encoding='utf-8')
            self.assertIn('https://example.github.io/trend-ahead/', sitemap)
            self.assertNotIn('/topics/', sitemap)
            self.assertIn('/topics/', home)

    def test_collection_health_reports_last_success_not_build_timestamp(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'data'
            folder = root / 'runs'
            folder.mkdir(parents=True)
            entries = [
                {'ran_at': (NOW - timedelta(hours=4)).isoformat(), 'feeds_ok': 2, 'feeds_total': 14},
                {'ran_at': (NOW - timedelta(minutes=5)).isoformat(), 'feeds_ok': 0, 'feeds_total': 14},
            ]
            (folder / '2026-11.jsonl').write_text('\n'.join(json.dumps(x) for x in entries) + '\n', encoding='utf-8')
            result = collection_status(root, NOW)
            self.assertEqual(result['status'], 'stale')
            report = build(root, Path(tmp) / 'dist', NOW)
            home = (Path(tmp) / 'dist/index.html').read_text(encoding='utf-8')
            self.assertIn('データ更新が遅れています', home)
            self.assertEqual(report['collection']['last_feeds_ok'], 0)

    def test_partial_rss_failures_are_not_displayed_as_healthy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'data'
            (root / 'runs').mkdir(parents=True)
            (root / 'runs/2026-11.jsonl').write_text(
                json.dumps({
                    'ran_at': (NOW - timedelta(minutes=10)).isoformat(),
                    'feeds_ok': 11, 'feeds_total': 14,
                    'feeds_failed': 3,
                }) + '\n', encoding='utf-8')
            report = collection_status(root, NOW)
            self.assertEqual(report['status'], 'degraded')
            build(root, Path(tmp) / 'dist', NOW)
            home = (Path(tmp) / 'dist/index.html').read_text(encoding='utf-8')
            self.assertIn('11/14', home)
            self.assertIn('一部または全部の情報源で失敗', home)

    def test_calendar_feed_is_valid_with_all_verified_events(self):
        event = {'title': 'Official Event', 'official_url': 'https://example.org/event',
                 'description': 'Details', 'starts_at': '2026-11-22T13:00:00+09:00',
                 'start_dt': datetime.fromisoformat('2026-11-22T13:00:00+09:00')}
        content = make_calendar_feed([event], NOW)
        self.assertEqual(content.count('BEGIN:VEVENT'), 1)
        self.assertIn('DTSTART:20261122T040000Z', content)
        self.assertIn('END:VCALENDAR\r\n', content)

    def test_week_page_only_indexed_when_publish_ready_and_verified(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / 'data'
            folder.mkdir()
            payload = [{'title': 'Confirmed Event', 'category': 'ゲーム',
                        'official_url': 'https://example.org/event',
                        'verified_at': NOW.isoformat(),
                        'starts_at': (NOW + timedelta(days=3)).isoformat()}]
            (folder / 'upcoming.json').write_text(json.dumps(payload), encoding='utf-8')
            build(folder, root / 'dist', NOW, 'https://example.org')
            preview = (root / 'dist/week/index.html').read_text(encoding='utf-8')
            self.assertIn('noindex,follow', preview)
            self.assertTrue((root / 'dist/calendar/upcoming.ics').exists())
            build(folder, root / 'dist', NOW, 'https://example.org',
                  publish_ready=True, operator_contact='owner@example.org')
            ready = (root / 'dist/week/index.html').read_text(encoding='utf-8')
            self.assertIn('index,follow', ready)
            self.assertIn('/week/', (root / 'dist/sitemap.xml').read_text())
            self.assertIn('Event', ready)

    def test_interest_controls_present_on_home_and_data_category_on_upcoming(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / 'data'
            folder.mkdir()
            payload = [{'title': 'Official', 'category': 'ゲーム',
                        'official_url': 'https://example.org/event',
                        'verified_at': NOW.isoformat(),
                        'starts_at': (NOW + timedelta(days=1)).isoformat()}]
            (folder / 'upcoming.json').write_text(json.dumps(payload), encoding='utf-8')
            build(folder, root / 'dist', NOW)
            home = (root / 'dist/index.html').read_text(encoding='utf-8')
            self.assertIn('id="personalized-toggle"', home)
            self.assertIn('name="interest"', home)
            self.assertIn('data-category="ゲーム"', home)

    def test_reject_unsafe_url_and_event_id(self):
        self.assertEqual(safe_url('javascript:alert(1)'), '')
        self.assertEqual(safe_url('http://example.com/'), '')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / 'data/events'
            folder.mkdir(parents=True)
            item = {'event_id': '../unsafe', 'first_seen_at': NOW.isoformat(),
                    'kind': 'news', 'title': 'unsafe', 'url': 'https://example.com/'}
            (folder / '2026-11-15.jsonl').write_text(json.dumps(item) + '\n')
            stats = build(root / 'data', root / 'dist', NOW)
            self.assertEqual(stats['observed_cards'], 0)

    def test_upcoming_requires_verified_official_url_and_future_date(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'upcoming.json'
            data = [
                {'title': 'Verified plan', 'official_url': 'https://example.org/plan',
                 'verified_at': NOW.isoformat(),
                 'starts_at': (NOW + timedelta(days=5)).isoformat()},
                {'title': 'Unverified plan', 'official_url': 'https://example.org/x',
                 'starts_at': (NOW + timedelta(days=5)).isoformat()},
                {'title': 'Unsafe link', 'official_url': 'javascript:bad',
                 'verified_at': NOW.isoformat(),
                 'starts_at': (NOW + timedelta(days=5)).isoformat()},
            ]
            path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
            result = load_upcoming(path, NOW)
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0]['title'], 'Verified plan')

    def test_ics_calendar_reminder_has_utc_and_alarm(self):
        event = {'title': 'Official Event', 'official_url': 'https://example.org/event',
                 'description': 'Details', 'starts_at': '2026-11-22T13:00:00+09:00',
                 'start_dt': datetime.fromisoformat('2026-11-22T13:00:00+09:00')}
        text = make_ics(event, calendar_id(event), NOW)
        self.assertIn('DTSTART:20261122T040000Z', text)
        self.assertIn('TRIGGER:-PT30M', text)
        self.assertIn('BEGIN:VALARM', text)
        self.assertTrue(text.endswith('END:VCALENDAR\r\n'))

    def test_no_old_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / 'data/events'
            folder.mkdir(parents=True)
            old = {'event_id': 'f' * 24, 'first_seen_at': (NOW - timedelta(days=10)).isoformat(),
                   'kind': 'news', 'title': 'Old item', 'url': 'https://example.com/'}
            (folder / '2026-11-05.jsonl').write_text(json.dumps(old) + '\n')
            report = build(root / 'data', root / 'dist', NOW)
            self.assertEqual(report['observed_cards'], 0)


if __name__ == '__main__':
    unittest.main()

