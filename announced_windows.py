"""Month-precision official announcements, kept separate from timed events.

Never synthesize day 1 or a midnight timestamp. Month windows have no ICS,
reminder, or inclusion in the confirmed-exact-date event count.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
PERIOD_RE = re.compile(r"20\d{2}-(?:0[1-9]|1[0-2])$")
ALLOWED_KINDS = {"anime_broadcast", "game_release", "movie_release", "other_announcement"}
CATEGORIES = {"映画・アニメ", "ゲーム", "テレビ・芸能", "配信・YouTuber", "グルメ・商品", "音楽・ライブ"}


def _valid_url(value):
    if not isinstance(value, str):
        return False
    try:
        u = urlsplit(value)
        return (u.scheme == "https" and bool(u.hostname)
                and u.username is None and u.password is None
                and u.port in (None, 443)
                and not any(ord(ch) < 32 for ch in value))
    except ValueError:
        return False


def load_announced_windows(path: Path, now: datetime, max_count=12, *, include_stale=False):
    if now.tzinfo is None:
        raise ValueError("now requires a timezone")
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return []
    if not isinstance(payload, list):
        return []
    today = now.astimezone(JST)
    current_month = today.year * 12 + today.month
    valid = []
    seen = set()
    for row in payload:
        if not isinstance(row, dict):
            continue
        period = row.get("period")
        title = row.get("title")
        category = row.get("category")
        kind = row.get("kind")
        official_url = row.get("official_url")
        if (not isinstance(period, str) or not PERIOD_RE.fullmatch(period)
                or not isinstance(title, str) or not 3 <= len(title.strip()) <= 120
                or category not in CATEGORIES or kind not in ALLOWED_KINDS
                or not _valid_url(official_url)
                or row.get("starts_at") is not None):
            continue
        try:
            verified = datetime.fromisoformat(row.get("verified_at"))
            if verified.tzinfo is None or verified > now:
                continue
            if not include_stale and (now - verified).total_seconds() > 30 * 86400:
                continue
            year, month = map(int, period.split("-"))
        except (ValueError, TypeError):
            continue
        months = year * 12 + month - current_month
        if not 0 <= months <= 8:
            continue
        key = (title.casefold().strip(), period)
        if key in seen:
            continue
        seen.add(key)
        valid.append({
            "title": title.strip(),
            "period": period,
            "period_label": f"{year}年{month}月",
            "category": category,
            "kind": kind,
            "official_url": official_url,
            "verified_at": verified.isoformat(),
            "note": "具体的な日付・開始時刻は未発表",
        })
    valid.sort(key=lambda item: (item["period"], item["category"], item["title"]))
    return valid[:max(0, max_count)]
