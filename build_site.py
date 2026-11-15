#!/usr/bin/env python3
"""Build the Trend Ahead public, privacy-first static website.

Observed RSS items are never presented as predictions and are noindexed individually.
Only manually verified, original upcoming stories may be indexed (opt-in).
"""
from __future__ import annotations

import argparse
import html
import hashlib
import json
import os
import re
import shutil
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from xml.etree import ElementTree as ET
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from signal_engine import detect_signals

JST = ZoneInfo('Asia/Tokyo')
CATEGORIES = [
    'SNS・ネットミーム', 'テレビ・芸能', 'ゲーム', '音楽・ライブ', 'スポーツ',
    '映画・アニメ', '配信・YouTuber', 'グルメ・商品', '季節・イベント',
    'テクノロジー', 'ニュース・社会', 'その他の新現象', '全般',
]
MAX_CARDS = 100
MAX_DETAIL_PAGES = 60


def esc(value):
    return html.escape(str(value if value is not None else ''), quote=True)


def safe_url(value):
    if not isinstance(value, str):
        return ''
    try:
        parsed = urlparse(value)
        return value if parsed.scheme == 'https' and parsed.netloc and not any(ord(c) < 32 for c in value) else ''
    except ValueError:
        return ''


def clean_site_url(value):
    parsed = urlparse(value or '')
    if parsed.scheme != 'https' or not parsed.netloc or parsed.query or parsed.fragment:
        return ''
    return value.rstrip('/')


def read_jsonl(folder, since):
    if not folder.exists():
        return []
    rows = []
    for file in sorted(folder.glob('*.jsonl'), reverse=True)[:14]:
        with file.open(encoding='utf-8') as stream:
            for line in stream:
                try:
                    obj = json.loads(line)
                    when = datetime.fromisoformat(obj.get('first_seen_at', ''))
                    if when.tzinfo is not None and when >= since:
                        rows.append(obj)
                except (ValueError, TypeError, json.JSONDecodeError):
                    continue
    return rows


def load_upcoming(path, now):
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except (ValueError, OSError):
        return []
    if not isinstance(payload, list):
        return []
    results = []
    for row in payload:
        if not isinstance(row, dict):
            continue
        url = safe_url(row.get('official_url'))
        if not url or not row.get('verified_at') or not row.get('title'):
            continue
        try:
            raw_start = row.get('starts_at')
            date_only = isinstance(raw_start, str) and bool(re.fullmatch(r'\d{4}-\d{2}-\d{2}', raw_start))
            start = datetime.fromisoformat(raw_start)
            if date_only:
                start = start.replace(tzinfo=JST)
            verified = datetime.fromisoformat(row.get('verified_at'))
            if not start.tzinfo or not verified.tzinfo:
                continue
            expires = start + (timedelta(days=1) if date_only else timedelta(hours=2))
            if expires <= now or start > now + timedelta(days=90):
                continue
            if verified > now or verified < now - timedelta(days=30):
                continue
        except (ValueError, TypeError):
            continue
        results.append({**row, 'official_url': url, 'start_dt': start, 'date_only': date_only})
    return sorted(results, key=lambda r: r['start_dt'])[:60]


def collection_status(data_dir, now):
    """Report actual collection health, independent from static build time."""
    run_files = sorted((data_dir / 'runs').glob('*.jsonl'), reverse=True)[:2] if (data_dir / 'runs').exists() else []
    latest, last_success = None, None
    for path in run_files:
        with path.open(encoding='utf-8') as stream:
            for line in stream:
                try:
                    row = json.loads(line)
                    date = datetime.fromisoformat(row.get('ran_at', ''))
                    if date.tzinfo is None or date > now + timedelta(minutes=5):
                        continue
                    if latest is None or date > latest[0]:
                        latest = (date, row)
                    if row.get('feeds_ok', 0) > 0 and (last_success is None or date > last_success[0]):
                        last_success = (date, row)
                except (ValueError, TypeError, json.JSONDecodeError):
                    continue
    age_minutes = round((now - last_success[0]).total_seconds() / 60) if last_success else None
    if age_minutes is None:
        status = 'not_started'
    elif age_minutes > 180:
        status = 'stale'
    elif latest and latest[1].get('feeds_ok', 0) == 0:
        status = 'degraded'
    else:
        status = 'ok'
    return {'status': status,
            'last_success_at': last_success[0].isoformat() if last_success else None,
            'last_run_at': latest[0].isoformat() if latest else None,
            'last_feeds_ok': latest[1].get('feeds_ok', 0) if latest else 0,
            'last_feeds_total': latest[1].get('feeds_total', 0) if latest else 0}


def health_message(health):
    if health['status'] == 'not_started':
        return '自動収集はまだ開始されていません。現在表示される予定情報は編集時点で公式確認したものです。'
    when = fmt_date(health['last_success_at'])
    if health['status'] == 'stale':
        return f'データ更新が遅れています。直近の収集成功：{when} JST。古い情報にはご注意ください。'
    if health['status'] == 'degraded':
        return f'直近のデータ収集に失敗しました。最後の成功：{when} JST。'
    return f'直近の収集成功：{when} JST（最終実行 {health["last_feeds_ok"]}/{health["last_feeds_total"]} ソース取得成功）'


def fmt_date(iso):
    try:
        return datetime.fromisoformat(iso).astimezone(JST).strftime('%m/%d %H:%M')
    except (ValueError, AttributeError):
        return '日時不明'


def observed_freshness(rows, data_dir, now):
    """Keep genuinely re-observed topics visible and disclose last observation time.

    The collector tracks the last sighting in a rolling seven-day state file.
    Legacy event files without state retain the historical 72-hour policy.
    """
    state = {}
    state_path = data_dir / 'state.json'
    if state_path.exists():
        try:
            raw = json.loads(state_path.read_text(encoding='utf-8'))
            if isinstance(raw, dict):
                for entry in raw.values():
                    if isinstance(entry, dict) and entry.get('event_id') and entry.get('last_seen_at'):
                        when = datetime.fromisoformat(entry['last_seen_at'])
                        if when.tzinfo and when <= now + timedelta(minutes=10):
                            key = entry['event_id']
                            state[key] = max(state.get(key, when), when)
        except (ValueError, OSError, TypeError, KeyError):
            pass
    newest = now - timedelta(hours=72)
    result = []
    for row in rows:
        try:
            first = datetime.fromisoformat(row['first_seen_at'])
            if not first.tzinfo or first > now + timedelta(minutes=10):
                continue
            observed = state.get(row.get('event_id'))
            if observed is None:
                observed = first
            if observed < newest:
                continue
            result.append({**row, 'last_seen_at': observed.isoformat()})
        except (TypeError, ValueError, KeyError):
            continue
    return result


def make_rss(title, description, items, site_url, now):
    """Create a standards-compatible, source-attributed RSS 2.0 feed."""
    root = ET.Element('rss', {'version': '2.0'})
    ch = ET.SubElement(root, 'channel')
    for tag, value in [('title', title), ('link', site_url + '/'),
                       ('description', description), ('language', 'ja'),
                       ('lastBuildDate', format_datetime(now.astimezone(timezone.utc)) )]:
        ET.SubElement(ch, tag).text = value
    for item in items:
        entry = ET.SubElement(ch, 'item')
        for tag in ('title', 'link', 'guid', 'description', 'pubDate'):
            if item.get(tag):
                attributes = {'isPermaLink': 'false'} if tag == 'guid' else {}
                ET.SubElement(entry, tag, attributes).text = str(item[tag])
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding='unicode') + '\n'


def create_subscription_feeds(output, rows, upcoming, now, site_url):
    """Only publish feed URLs when an actual canonical HTTPS origin is set."""
    if not site_url:
        return
    today_items = []
    for row in rows[:60]:
        dt = datetime.fromisoformat(row['last_seen_at']).astimezone(timezone.utc)
        today_items.append({
            'title': row['title'],
            'link': site_url + '/topics/' + row['event_id'] + '/',
            'guid': 'trend-ahead:observation:' + row['event_id'],
            'description': label_for(row['kind']) + '。原典: ' + row['url'] + '。予測の確定を示しません。',
            'pubDate': format_datetime(dt),
        })
    upcoming_items = []
    for row in upcoming:
        upcoming_items.append({
            'title': row['title'],
            'link': row['official_url'],
            'guid': 'trend-ahead:upcoming:' + calendar_id(row),
            'description': (str(row.get('description', '')) + ' 開催予定: ' +
                            row['start_dt'].astimezone(JST).strftime('%Y/%m/%d %H:%M JST')),
            'pubDate': format_datetime(datetime.fromisoformat(row['verified_at'])),
        })
    write(output / 'feeds' / 'signals.xml', make_rss(
        'Trend Ahead｜観測された話題',
        '検索急上昇とニュースに掲載された候補。予測確率を表すものではありません。',
        today_items, site_url, now))
    write(output / 'feeds' / 'upcoming.xml', make_rss(
        'Trend Ahead｜これからの公式イベント',
        '公式発表で確認済みの開催予定。時刻変更は公式ページをご確認ください。',
        upcoming_items, site_url, now))


def label_for(kind):
    return '検索急上昇を検知' if kind == 'trends' else 'ニュースに掲載'


def short_text(text, limit=66):
    text = re.sub(r'\s+', ' ', str(text or '')).strip()
    return text[:limit] + '…' if len(text) > limit else text


def format_page(title, description, body, *, canonical='', noindex=False, stylesheet='/assets/style.css', script='/assets/app.js', structured_data=None):
    meta_robots = '<meta name="robots" content="noindex,follow">' if noindex else '<meta name="robots" content="index,follow,max-image-preview:large">'
    canon = f'<link rel="canonical" href="{esc(canonical)}">' if canonical else ''
    og_url = f'<meta property="og:url" content="{esc(canonical)}">' if canonical else ''
    feed_links = ''
    if canonical:
        from urllib.parse import urlparse
        origin_path = urlparse(canonical).path.rstrip('/')
        # A canonical URL may point to a subpage; only site root is passed for home.
        if structured_data and structured_data.get('@type') == 'WebSite':
            feed_links = f'<link rel="alternate" type="application/rss+xml" title="注目トピック RSS" href="{esc(origin_path + "/feeds/signals.xml")}">\n<link rel="alternate" type="application/rss+xml" title="今後のイベント RSS" href="{esc(origin_path + "/feeds/upcoming.xml")}">'
    ld = ''
    if structured_data:
        encoded = json.dumps(structured_data, ensure_ascii=False).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
        ld = f'<script type="application/ld+json">{encoded}</script>'
    return f'''<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
<meta name="description" content="{esc(description)}">
{meta_robots}
{canon}
{feed_links}
<meta property="og:type" content="website">
<meta property="og:locale" content="ja_JP">
<meta property="og:title" content="{esc(title)}">
<meta property="og:description" content="{esc(description)}">
{og_url}
<meta name="theme-color" content="#111827">
<link rel="stylesheet" href="{esc(stylesheet)}">
{ld}
<script defer src="{esc(script)}"></script>
</head>
<body>
{body}
</body>
</html>
'''


def header():
    return '''<header class="topbar"><div class="wrap navwrap">
<a class="brand" href="/" aria-label="Trend Ahead ホーム"><span class="brandmark">↗</span> Trend<span class="brand-light">Ahead</span></a>
<nav aria-label="メインナビゲーション"><a href="/#upcoming">これから</a><a href="/#signals">兆候</a><a href="/#now">いまの話題</a><a href="/week/">今週</a></nav>
</div></header>'''


def footer():
    return '''<footer class="footer"><div class="wrap foot-inner"><div><strong>Trend Ahead</strong><p>明日の話題を、今日知る。</p></div>
<div class="footer-links"><a href="/how/">使い方</a><a href="/methodology/">検知方法と透明性</a><a href="/privacy/">プライバシー</a></div>
<p class="legal">自動収集した情報は事実の確定や未来の予測を保証しません。正確な情報は一次ソースをご確認ください。</p></div></footer>'''


def card(row):
    key = esc(row['event_id'])
    title = esc(short_text(row['title'], 82))
    category = esc(row.get('category', '全般'))
    kind = esc(label_for(row.get('kind')))
    detected = esc(fmt_date(row.get('first_seen_at', '')))
    href = f'/topics/{key}/'
    return f'''<article class="topic-card" data-category="{category}" data-id="{key}" data-search="{esc(row['title'].casefold())}">
<div class="card-top"><span class="pill">{category}</span><span class="dot"></span><span class="status">{kind}</span></div>
<h3><a href="{href}">{title}</a></h3>
<div class="card-bottom"><span>初回検知 {detected}</span><div class="actions">
<button class="save-btn" type="button" data-save="{key}" aria-label="保存する" aria-pressed="false">☆ 保存</button>
<a class="mini-link" href="{href}" aria-label="詳細を見る">詳細 ↗</a></div></div></article>'''


def calendar_id(row):
    return hashlib.sha256((str(row['title']) + '|' + str(row['starts_at'])).encode('utf-8')).hexdigest()[:20]


def ics_escape(value):
    value = re.sub(r'[\r\n]+', ' ', str(value or ''))
    return value.replace('\\', '\\\\').replace(';', '\\;').replace(',', '\\,')


def make_ics(row, uid, now):
    stamp = now.astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    if row.get('date_only'):
        start_date = row['start_dt'].astimezone(JST).strftime('%Y%m%d')
        next_date = (row['start_dt'] + timedelta(days=1)).astimezone(JST).strftime('%Y%m%d')
        starts = [f'DTSTART;VALUE=DATE:{start_date}', f'DTEND;VALUE=DATE:{next_date}']
        alarm = []
    else:
        start = row['start_dt'].astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        starts = [f'DTSTART:{start}']
        alarm = ['BEGIN:VALARM', 'ACTION:DISPLAY', 'TRIGGER:-PT30M',
                 'DESCRIPTION:開催30分前です。公式ページで最新情報を確認してください。',
                 'END:VALARM']
    lines = [
        'BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//Trend Ahead//Upcoming Reminder//JA',
        'CALSCALE:GREGORIAN', 'METHOD:PUBLISH', 'BEGIN:VEVENT',
        f'UID:{uid}@trend-ahead', f'DTSTAMP:{stamp}', *starts,
        f'SUMMARY:{ics_escape(row["title"])}',
        f'DESCRIPTION:{ics_escape(row.get("description", ""))}',
        f'URL:{row["official_url"]}', 'TRANSP:TRANSPARENT',
        *alarm, 'END:VEVENT', 'END:VCALENDAR',
    ]
    result = []
    for line in lines:
        # RFC 5545: each content line must be folded at 75 octets.
        fragments = []
        current = ''
        for ch in line:
            if len((current + ch).encode('utf-8')) > 74:
                fragments.append(current)
                current = ' ' + ch
            else:
                current += ch
        fragments.append(current)
        result.extend(fragments)
    return '\r\n'.join(result) + '\r\n'


def make_calendar_feed(upcoming, now):
    """Downloadable/subscribable calendar of verified future events."""
    stamp = now.astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    lines = ['BEGIN:VCALENDAR', 'VERSION:2.0',
             'PRODID:-//Trend Ahead//Verified Upcoming Events//JA',
             'CALSCALE:GREGORIAN', 'METHOD:PUBLISH',
             'X-WR-CALNAME:Trend Ahead 公式確認済みイベント']
    for event in upcoming:
        uid = calendar_id(event)
        if event.get('date_only'):
            day = event['start_dt'].astimezone(JST).strftime('%Y%m%d')
            next_day = (event['start_dt'] + timedelta(days=1)).astimezone(JST).strftime('%Y%m%d')
            starts = [f'DTSTART;VALUE=DATE:{day}', f'DTEND;VALUE=DATE:{next_day}']
        else:
            start = event['start_dt'].astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
            starts = [f'DTSTART:{start}']
        lines.extend(['BEGIN:VEVENT', f'UID:{uid}@trend-ahead',
                      f'DTSTAMP:{stamp}', *starts,
                      f'SUMMARY:{ics_escape(event["title"])}',
                      f'DESCRIPTION:{ics_escape(event.get("description", ""))}',
                      f'URL:{event["official_url"]}', 'TRANSP:TRANSPARENT',
                      'END:VEVENT'])
    lines.append('END:VCALENDAR')
    folded = []
    for line in lines:
        current = ''
        for ch in line:
            if len((current + ch).encode('utf-8')) > 74:
                folded.append(current)
                current = ' ' + ch
            else:
                current += ch
        folded.append(current)
    return '\r\n'.join(folded) + '\r\n'


def week_page(upcoming, now, site_url, indexing_enabled=False):
    end = now + timedelta(days=7)
    week = [x for x in upcoming if x['start_dt'] <= end and x['start_dt'] + (timedelta(days=1) if x.get('date_only') else timedelta(hours=2)) > now]
    rows = '\n'.join(upcoming_card(x) for x in week)
    if not week:
        rows = '<div class="empty">今週の公式確認済みイベントはまだありません。情報が追加され次第掲載します。</div>'
    summary = '直近7日間に開催される公式確認済みイベント。日時・根拠URL・カレンダー登録を一度に確認できます。'
    content = f"""{header()}<main class="content-page wrap"><a class="back" href="/">← ホームに戻る</a>
<span class="kicker">THIS WEEK</span><h1>今週の注目予定</h1><p class="intro">{summary}</p>
<p class="week-note">掲載基準：公式情報に日時と出典を確認できたもの。SNSで話題になる確率を推定したランキングではありません。</p>
<div class="upcoming-list">{rows}</div>
<p><a class="ghost-btn" href="/calendar/upcoming.ics" download>予定をまとめてカレンダーに追加 →</a> <button type="button" class="ghost-btn" data-share="{esc((site_url or '') + '/week/')}" data-title="今週の注目予定｜Trend Ahead">この予定を友達に共有 ↗</button></p>
</main>{footer()}"""
    structured = {'@context': 'https://schema.org', '@type': 'ItemList',
                  'name': '今週の公式確認済みイベント',
                  'itemListElement': [{'@type': 'ListItem', 'position': i,
                                       'item': {'@type': 'Event', 'name': x['title'],
                                                'startDate': x['starts_at'],
                                                'url': x['official_url']}}
                                      for i, x in enumerate(week, 1)]}
    return format_page('今週の注目予定｜Trend Ahead', summary, content,
                       canonical=site_url + '/week/' if site_url else '',
                       noindex=(not indexing_enabled or not bool(week)),
                       structured_data=structured if week else None)


def upcoming_card(row, compact=False):
    """Compact on the homepage; keep expanded description on the weekly page."""
    title = esc(short_text(row['title'], 85))
    event_date = row['start_dt'].astimezone(JST)
    day = event_date.strftime('%m/%d') + '(' + '月火水木金土日'[event_date.weekday()] + ')'
    date = esc(day + ('' if row.get('date_only') else event_date.strftime(' %H:%M')))
    category = esc(row.get('category', '予定イベント'))
    summary = esc(short_text(row.get('description', ''), 150))
    source = esc(row['official_url'])
    ics = '/calendar/' + calendar_id(row) + '.ics'
    attributes = (f'data-category="{category}" data-starts-at="{esc(row["start_dt"].isoformat())}" '
                  f'data-search="{esc((row["title"] + " " + row.get("description", "")).casefold())}"')
    reminder_label = 'カレンダーに登録（日付のみ）' if row.get('date_only') else '30分前通知付きでカレンダーに登録'
    extended_label = 'カレンダーに追加 ↗' if row.get('date_only') else '30分前通知をカレンダーに追加 ↗'
    if compact:
        return f'''<article class="upcoming-card upcoming-card-compact" {attributes}>
<div class="event-topline"><time datetime="{esc(row['starts_at'])}">{date}</time><span class="event-category">{category}</span></div>
<h3>{title}</h3>
<div class="upcoming-actions"><a href="{source}" target="_blank" rel="noopener noreferrer" aria-label="{title}の公式情報を見る">公式情報 ↗</a>
<a href="{ics}" download aria-label="{title}を{reminder_label}">予定に追加 ＋</a></div></article>'''
    return f'''<article class="upcoming-card" {attributes}><div class="upcoming-date">{date}<small>日本時間</small></div><div class="upcoming-info">
<span class="pill verified">公式情報で確認済み</span> <span class="light-text">{category}</span>
<h3>{title}</h3><p>{summary}</p><div class="upcoming-actions"><a href="{source}" target="_blank" rel="noopener noreferrer">公式情報を見る ↗</a><a href="{ics}" download>{extended_label}</a></div></div></article>'''


def recent_new_trends(rows, now, limit=3):
    """Separate unconfirmed single sightings from repeat-observation signals."""
    output = []
    for row in rows:
        if row.get('kind') != 'trends':
            continue
        try:
            when = datetime.fromisoformat(row['first_seen_at'])
            if not when.tzinfo or not timedelta(0) <= now - when <= timedelta(hours=8):
                continue
        except (ValueError, TypeError, KeyError):
            continue
        output.append({
            'event_id': row['event_id'], 'title': row['title'],
            'category': row.get('category', '全般'),
            'label': '新着検索', 'first_observed_at': when.isoformat(),
            'evidence': '初回検知 ' + fmt_date(when.isoformat()) + ' JST／継続上昇は未確認',
        })
    output.sort(key=lambda r: r['first_observed_at'], reverse=True)
    return output[:limit]


def signal_section(signals, new_arrivals=None):
    """Compact observed-signal column on the first screen."""
    if signals:
        articles = []
        for signal in signals:
            url = '/topics/' + esc(signal['event_id']) + '/'
            articles.append(f'''<article class="front-signal-card">
<div class="front-signal-meta"><span class="signal-status">{esc(signal['label'])}</span><span>{signal['samples']}回の観測</span></div>
<h3><a href="{url}">{esc(short_text(signal['title'], 67))} →</a></h3>
<p>{esc(signal['evidence'])}</p></article>''')
        content = '<div class="front-signal-list">' + '\n'.join(articles) + '</div>'
    elif new_arrivals:
        articles = []
        for entry in new_arrivals:
            url = '/topics/' + esc(entry['event_id']) + '/'
            articles.append(f'''<article class="front-signal-card">
<div class="front-signal-meta"><span class="signal-status">{esc(entry['label'])}</span><span>初回観測</span></div>
<h3><a href="{url}">{esc(short_text(entry['title'], 67))} →</a></h3>
<p>{esc(entry['evidence'])}</p></article>''')
        content = '<div class="front-signal-list">' + '\n'.join(articles) + '</div>'
    else:
        content = '<p class="signal-empty">観測データを収集中</p>'
    return f'''<aside id="signals" class="front-signals" aria-label="急上昇の兆候">
<div class="front-aside-head"><h2>動き始めた話題 <span class="kicker">SIGNAL</span></h2></div>
{content}<details class="signal-method"><summary>判定方法</summary><p>RSSの推定検索量を繰り返し観測しています。スコアは予測確率ではありません。</p></details><a class="front-more" href="/#now">もっと見る →</a></aside>'''


def homepage(rows, upcoming, now, site_url, health, indexing_enabled=False, signals=None, new_arrivals=None):
    cards = '\n'.join(card(row) for row in rows) if rows else '<div class="empty">現在、表示できる観測候補はありません。</div>'
    future = '\n'.join(upcoming_card(x, compact=True) for x in upcoming) if upcoming else '<div class="empty upcoming-empty">確認済みの予定イベントは準備中です。</div>'
    updated = esc(now.astimezone(JST).strftime('%m/%d %H:%M'))
    health_text = esc(health_message(health))
    state = health['status']
    if state == 'ok':
        health_short = '収集：' + esc(fmt_date(health['last_success_at'])) + ' JST'
    elif state == 'stale':
        health_short = 'データ更新が遅れています'
    elif state == 'degraded':
        health_short = '収集エラー'
    else:
        health_short = '収集準備中'
    interest_options = ''.join(f'<label class="interest-choice"><input type="checkbox" name="interest" value="{esc(c)}">{esc(c)}</label>' for c in CATEGORIES if c != '全般')
    rss_buttons = (f'<div class="rss-links"><a href="/feeds/signals.xml">話題のRSS ↗</a>'
                   f'<a href="/feeds/upcoming.xml">予定のRSS ↗</a></div>') if site_url else ''
    page = f'''{header()}<main>
<section id="upcoming" class="front-discovery"><div class="wrap">
<div class="front-discovery-head"><div class="front-titlebar"><h1>これからの予定</h1>
<span class="discovery-free">登録不要</span><span class="source-caption">公式確認済み・日本時間</span></div>
<a class="front-calendar-link" href="/calendar/upcoming.ics" download>一括カレンダー登録 ↗</a></div>
<div class="front-filters" aria-label="これからの予定を絞り込む">
<div class="future-range" role="group" aria-label="開催日までの期間">
<button class="future-range-button is-active" type="button" data-future-window="all" aria-pressed="true">すべて</button>
<button class="future-range-button" type="button" data-future-window="7" aria-pressed="false">7日</button>
<button class="future-range-button" type="button" data-future-window="30" aria-pressed="false">30日</button></div>
<label class="future-query"><span aria-hidden="true">⌕</span><input id="future-search" type="search" placeholder="予定を検索" aria-label="予定をキーワードで検索"></label>
<select id="future-category" aria-label="予定のジャンルで絞り込む"><option value="all">全ジャンル</option>{''.join(f'<option value="{esc(c)}">{esc(c)}</option>' for c in CATEGORIES if c != '全般')}</select>
</div>
<div class="front-layout"><div class="front-main">
<p id="future-result-status" class="future-result-status" role="status" aria-live="polite">{len(upcoming)}件</p>
<div id="future-list" class="front-event-list">{future}</div>
<p id="future-empty" class="empty" hidden>該当する予定はありません。</p>
<div class="front-bottom-links"><a href="/week/">今週の予定 →</a>
<a href="/calendar/upcoming.ics" download>カレンダーに追加 →</a>
<button type="button" class="front-share-button" data-share="{esc((site_url or '') + '/')}" data-title="Trend Ahead｜明日の話題を、今日知る。">共有 ↗</button></div>
</div>{signal_section(signals or [], new_arrivals or [])}</div>
<details class="front-data-status" data-status="{esc(state)}"><summary>{health_short}</summary>
<p>{health_text} <a href="/methodology/">データと検知方法</a></p></details>
</div></section>
<section id="now" class="section wrap now-section">
<div class="section-header"><h2>いまの話題</h2><span class="updated">掲載更新 {updated} JST</span></div>
<div class="filter-toolbar"><label class="searchbox"><span aria-hidden="true">⌕</span><input id="topic-search" type="search" placeholder="話題を検索" aria-label="トピックを検索"></label>
<select id="category-filter" aria-label="ジャンルで絞り込む"><option value="all">全ジャンル</option>{''.join(f'<option value="{esc(c)}">{esc(c)}</option>' for c in CATEGORIES)}</select>
<button type="button" id="saved-toggle" class="filter-btn" aria-pressed="false">☆ 保存した話題</button></div>
<p id="result-count" class="result-count" aria-live="polite"></p><div class="cards" id="topic-list">{cards}</div>
<p id="filter-empty" class="empty" hidden>該当する話題はありません。</p>
<details class="interest-panel" id="interest-panel"><summary>興味ジャンルで並べ替え ＋</summary>
<div id="interest-categories" class="interest-options">{interest_options}</div>
<button type="button" id="personalized-toggle" class="filter-btn" aria-pressed="false">おすすめ順</button>
<p id="interest-status" class="interest-status" aria-live="polite"></p></details>
{rss_buttons}
</section></main>{footer()}'''
    structured = {'@context': 'https://schema.org', '@type': 'WebSite', 'name': 'Trend Ahead', 'description': '明日の話題を、今日知る。話題の初期兆候と確認済みイベントを見つけるサービス。'}
    if site_url:
        structured['url'] = site_url + '/'
    return format_page('Trend Ahead｜明日の話題を、今日知る。', '見逃したくない話題を、盛り上がる前に。トレンドの初期兆候と確認済みの予定を無料で発見。', page,
                       canonical=(site_url + '/' if site_url else ''), noindex=not indexing_enabled, structured_data=structured)


def topic_page(row, site_url):
    title = esc(row['title'])
    url = esc(row['url'])
    detected = esc(fmt_date(row['first_seen_at']))
    kind = esc(label_for(row['kind']))
    category = esc(row.get('category', '全般'))
    count = esc(row.get('approx_traffic') or '未取得')
    url_path = f'/topics/{row["event_id"]}/'
    share = esc((site_url or '') + url_path)
    content = f'''{header()}<main class="detail-main wrap"><a class="back" href="/">← トレンド一覧へ</a>
<div class="detail-eyebrow"><span class="pill">{category}</span><span class="status">{kind}</span></div>
<h1>{title}</h1><p class="detail-subtitle">これは自動収集された観測候補です。今後の流行を保証する予測ではありません。</p>
<div class="detail-meta"><div><span>初回検知日時</span><strong>{detected} JST</strong></div><div><span>情報の分類</span><strong>{kind}</strong></div><div><span>Google Trends推定検索ボリューム</span><strong>{count if row['kind'] == 'trends' else '対象外'}</strong></div></div>
<div class="detail-action"><a class="primary-btn" href="{url}" target="_blank" rel="noopener noreferrer">情報源を確認する ↗</a>
<button type="button" class="ghost-outline" data-share="{share}" data-title="{title}">この話題を共有 ↗</button>
<button class="ghost-outline save-btn" data-save="{esc(row['event_id'])}" type="button" aria-pressed="false">☆ 保存</button></div>
<section class="detail-info"><h2>このページについて</h2><p>当ページは、RSSで観測された情報を初回検知日時とともに表示しています。元記事や公式情報を確認するための入口であり、事実関係や将来の盛り上がりを独自検証した記事ではありません。</p>
<p>観測候補は検索エンジンの評価を目的に量産しているものではなく、個別ページは検索エンジンへのインデックス登録対象にしていません。</p>
<a href="/methodology/">検知方法・情報の取り扱いを見る →</a></section></main>{footer()}'''
    return format_page(f'{row["title"]}｜Trend Ahead', f'{row["title"]}に関する初回検知日時と元情報へのリンク。', content,
                       canonical=(site_url + url_path if site_url else ''), noindex=True)


def simple_page(slug, site_url, indexing_enabled=False, operator_contact=''):
    pages = {
      'how': ('使い方', 'Trend Aheadは「いま注目され始めた話題」と「公式確認済みの予定」を分けて案内します。', '''<h2>1. いまの注目を見る</h2><p>RSSから観測した話題を表示します。収集＝未来の予測ではありません。</p><h2>2. ジャンルやキーワードで探す</h2><p>12ジャンルから選べます。興味のあるジャンルを選ぶとおすすめ順で優先表示しますが、未知のジャンルも一部残します。</p><h2>3. 気になる話題を保存する</h2><p>☆保存ボタンで、同じブラウザの中に気になるトピックを記録できます。アカウント登録は必要ありません。ブラウザのデータを削除すると保存情報も消えます。</p><h2>4. 原典へ移動する</h2><p>内容を詳しく確認したいときは、元記事や公式の案内を開いてください。</p><h2>今後追加するもの</h2><p>通知、閲覧行動を用いた自動興味学習、予測モデル、より豊富な予定情報は開発中です。現時点では提供していません。</p>'''),
      'methodology': ('検知方法と透明性', 'Trend Aheadの収集、検証、インデックス方針について説明します。', '''<h2>何を検知するか</h2><p>Google Trends日本版の急上昇RSSとGoogle News RSSなどから、話題の候補を収集します。情報源の稼働状況によって掲載数や更新頻度は変わります。</p><h2>観測と予測の違い</h2><p>検索急上昇の観測やニュース掲載は、未来に盛り上がると予測したことを意味しません。「検知」「公式確認済み予定」「予測」を区別します。現段階で独自の確率予測機能はありません。</p><h2>データの更新</h2><p>データ収集は30分ごとの定期実行を想定していますが、遅延や失敗があり得ます。カードの「初回検知日時」は当サービスの収集システムが最初に観測した時刻であり、SNS上の話題の発生日ではありません。</p><h2>確認済みの予定</h2><p>予定には開催日時・公式URL・確認日時が必要です。事実確認できていない予定は掲載しません。</p><h2>出典・著作権</h2><p>記事本文の転載は行わず、タイトル、メタ情報、元ソースへのリンクを表示します。各情報の正確性、権利、利用条件は原典で確認してください。</p><h2>検索インデックス</h2><p>自動収集候補の個別ページはnoindexで生成します。独自の価値を付与できない転載・量産ページを検索結果に出さない設計です。</p><h2>安全性</h2><p>事件・事故・災害を娯楽として推薦する設計は採用せず、通知を実装する際には別ポリシーに沿って扱います。</p>'''),
      'privacy': ('プライバシー', 'Trend Aheadのブラウザ保存機能と情報取得について説明します。', '''<h2>ブラウザ内の保存</h2><p>「保存」と「興味ジャンル」機能はブラウザのlocalStorageに識別子や選択したジャンルを保存します。現段階では会員登録機能やメールアドレス収集機能はありません。</p><h2>外部リンク</h2><p>各話題の元サイトへ移動した後は、移動先の利用規約とプライバシーポリシーが適用されます。</p><h2>アクセス計測</h2><p>現時点でこの配布コードには外部アクセス解析タグを組み込んでいません。ホスティング事業者による一般的なアクセスログはホスティング事業者の設定に依存します。</p><h2>お問い合わせ</h2><p>お問い合わせ先と運営者情報は正式公開前に明示します。公開予定のメールアドレスを設定するまでは正式サービスとして公開しないでください。</p>'''),
    }
    heading, desc, inner = pages[slug]
    if slug == 'privacy' and operator_contact:
        address = esc(operator_contact)
        inner = inner.replace('<h2>お問い合わせ</h2>',
                              f'<h2>お問い合わせ</h2><p>お問い合わせ: <a href="mailto:{address}">{address}</a></p>')
        inner = inner.replace('<p>お問い合わせ先と運営者情報は正式公開前に明示します。公開予定のメールアドレスを設定するまでは正式サービスとして公開しないでください。</p>',
                              '<p>正式な運営者情報・お問い合わせ対応方針は公開時に確認してください。</p>')
    body = f'{header()}<main class="content-page wrap"><a class="back" href="/">← ホームに戻る</a><span class="kicker">ABOUT TREND AHEAD</span><h1>{esc(heading)}</h1><p class="intro">{esc(desc)}</p><article class="prose">{inner}</article></main>{footer()}'
    path = f'/{slug}/'
    return format_page(f'{heading}｜Trend Ahead', desc, body, canonical=(site_url + path if site_url else ''), noindex=not indexing_enabled)


def write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding='utf-8')


def build(data_dir: Path, output: Path, now=None, site_url='', *, publish_ready=False, operator_contact=''):
    now = now or datetime.now(timezone.utc)
    site_url = clean_site_url(site_url)
    valid_contact = bool(re.fullmatch(r'[A-Za-z0-9_.+%-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}', operator_contact or ''))
    # An explicit publishing decision plus contact information is required
    # before preview sites are allowed into the search index.
    indexing_enabled = bool(site_url and publish_ready and valid_contact)
    rows = observed_freshness(read_jsonl(data_dir / 'events', now - timedelta(days=7)), data_dir, now)
    rows = [row for row in rows if safe_url(row.get('url')) and re.fullmatch(r'[a-f0-9]{24}', str(row.get('event_id', ''))) and row.get('title') and row.get('kind') in ('news', 'trends')]
    unique = {}
    for row in sorted(rows, key=lambda r: r['first_seen_at'], reverse=True):
        unique.setdefault(row['event_id'], row)
    # Signal coverage must not be restricted by the newest 100 news cards.
    # Otherwise a burst of articles can hide every repeated Google Trends topic.
    all_observed = sorted(unique.values(), key=lambda r: r['last_seen_at'], reverse=True)
    signal_candidates = [r for r in all_observed if r['kind'] == 'trends']
    signals = detect_signals(data_dir, signal_candidates, now)
    new_arrivals = recent_new_trends(signal_candidates, now) if not signals else []
    rows = all_observed[:MAX_CARDS]
    upcoming = load_upcoming(data_dir / 'upcoming.json', now)
    health = collection_status(data_dir, now)
    # Avoid deleting arbitrary paths when invoked with an unsafe output argument.
    output = output.resolve()
    if output == Path('/') or output == Path.cwd().resolve() or output == Path.home().resolve():
        raise ValueError('unsafe build output directory')
    if output.exists():
        shutil.rmtree(output)
    (output / 'assets').mkdir(parents=True)
    asset_dir = Path(__file__).resolve().parent / 'web' / 'assets'
    shutil.copy2(asset_dir / 'style.css', output / 'assets' / 'style.css')
    shutil.copy2(asset_dir / 'app.js', output / 'assets' / 'app.js')
    # GitHub Pages project sites live under /repo-name/, not the origin root.
    base_path = urlparse(site_url).path.rstrip('/') if site_url else ''
    def page_html(content):
        if base_path:
            return content.replace('href="/', f'href="{base_path}/').replace('src="/', f'src="{base_path}/')
        return content
    write(output / 'index.html', page_html(homepage(rows, upcoming, now, site_url, health, indexing_enabled, signals, new_arrivals)))
    write(output / 'signals.json', json.dumps({'generated_at': now.isoformat(), 'signals': signals, 'new_arrivals': new_arrivals, 'note': 'First sightings are separate from repeat-observation signals. Not a forecast probability.'}, ensure_ascii=False, indent=2) + '\n')
    create_subscription_feeds(output, rows, upcoming, now, site_url)
    write(output / 'week' / 'index.html', page_html(week_page(upcoming, now, site_url, indexing_enabled)))
    for name in ('how', 'methodology', 'privacy'):
        write(output / name / 'index.html', page_html(simple_page(name, site_url, indexing_enabled, operator_contact if valid_contact else '')))
    write(output / 'calendar' / 'upcoming.ics', make_calendar_feed(upcoming, now))
    for row in upcoming:
        write(output / 'calendar' / (calendar_id(row) + '.ics'), make_ics(row, calendar_id(row), now))
    priority_ids = {item['event_id'] for item in (signals + new_arrivals)}
    priority_rows = [row for row in signal_candidates if row['event_id'] in priority_ids]
    detail_rows = (priority_rows + [row for row in rows if row['event_id'] not in priority_ids])[:MAX_DETAIL_PAGES]
    for row in detail_rows:
        write(output / 'topics' / row['event_id'] / 'index.html', page_html(topic_page(row, site_url)))
    write(output / '404.html', page_html(format_page('ページが見つかりません｜Trend Ahead', 'お探しのページは見つかりませんでした。',
        f'{header()}<main class="content-page wrap"><h1>ページが見つかりません</h1><p>URLをご確認ください。</p><a class="primary-btn" href="/">ホームに戻る</a></main>{footer()}', noindex=True)))
    write(output / 'robots.txt', 'User-agent: *\nAllow: /\n' + (f'Sitemap: {site_url}/sitemap.xml\n' if indexing_enabled else ''))
    if indexing_enabled:
        from xml.sax.saxutils import escape as xml_escape
        paths = ['/', '/how/', '/methodology/', '/privacy/']
        if any(now - timedelta(hours=2) <= x['start_dt'] <= now + timedelta(days=7) for x in upcoming):
            paths.append('/week/')
        sitemap = '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + ''.join(
            f'<url><loc>{xml_escape(site_url + p)}</loc></url>\n' for p in paths) + '</urlset>\n'
        write(output / 'sitemap.xml', sitemap)
    write(output / 'build-meta.json', json.dumps({'built_at': now.isoformat(), 'observed_cards': len(rows), 'signals_count': len(signals), 'new_arrivals_count': len(new_arrivals), 'upcoming_cards': len(upcoming), 'detail_pages': min(MAX_DETAIL_PAGES, len(rows)), 'canonical_configured': bool(site_url), 'indexing_enabled': indexing_enabled, 'collection_status': health}, ensure_ascii=False, indent=2) + '\n')
    return {'observed_cards': len(rows), 'signals_count': len(signals), 'new_arrivals_count': len(new_arrivals), 'upcoming_cards': len(upcoming), 'detail_pages': min(MAX_DETAIL_PAGES, len(rows)), 'output': str(output), 'canonical_configured': bool(site_url), 'collection': health}


def main():
    parser = argparse.ArgumentParser(description='Build static Trend Ahead public site')
    parser.add_argument('--data-dir', default='data', type=Path)
    parser.add_argument('--output', default='dist', type=Path)
    parser.add_argument('--site-url', default=os.getenv('SITE_URL', ''))
    parser.add_argument('--publish-ready', action='store_true', default=os.getenv('PUBLISH_READY') == '1',
                        help='Enable search indexing (requires OPERATOR_CONTACT).')
    parser.add_argument('--operator-contact', default=os.getenv('OPERATOR_CONTACT', ''),
                        help='Public operator email address; never pass a private or secret address.')
    args = parser.parse_args()
    print(json.dumps(build(args.data_dir, args.output, site_url=args.site_url, publish_ready=args.publish_ready, operator_contact=args.operator_contact), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()

