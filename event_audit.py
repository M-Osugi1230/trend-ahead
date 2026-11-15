#!/usr/bin/env python3
"""Daily quality report for a curated, verified-event catalog.

This program deliberately NEVER updates verified_at or publishes invented events.
It identifies missing genres, expiring coverage, duplicate listings, and stale
source checks for human editorial verification.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
TARGET_30D = 30
MAX_VERIFICATION_DAYS = 30
REVIEW_AFTER_DAYS = 14
CATEGORIES = (
    "SNS・ネットミーム", "テレビ・芸能", "ゲーム", "音楽・ライブ",
    "スポーツ", "映画・アニメ", "配信・YouTuber", "グルメ・商品",
    "季節・イベント", "テクノロジー", "ニュース・社会", "その他の新現象"
)


def _date(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
        date_only = bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", value))
        if date_only:
            parsed = parsed.replace(tzinfo=JST)
        if parsed.tzinfo is None:
            return None
        return parsed, date_only
    except ValueError:
        return None


def audit(path: Path, now: datetime | None = None):
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise ValueError("catalog must be a list")
    except (OSError, ValueError) as e:
        return {"status": "invalid_catalog", "error": str(e), "total": 0}
    categories = Counter()
    ready_7 = ready_30 = ready_90 = 0
    due_review = []
    errors = []
    duplicates = []
    seen = set()
    future = []
    for index, row in enumerate(payload):
        if not isinstance(row, dict):
            errors.append({"row": index, "reason": "not an object"})
            continue
        title = row.get("title")
        url = row.get("official_url")
        start = _date(row.get("starts_at"))
        checked = _date(row.get("verified_at"))
        category = row.get("category")
        try:
            parsed_url = urlsplit(url or "")
            valid_url = parsed_url.scheme == "https" and bool(parsed_url.hostname)
        except (TypeError, ValueError):
            valid_url = False
        if not title or not isinstance(title, str) or not valid_url or category not in CATEGORIES or start is None or checked is None or checked[1]:
            errors.append({"row": index, "reason": "missing/invalid official source, title, category or timestamp"})
            continue
        if checked[0] > now + timedelta(hours=1):
            errors.append({"row": index, "reason": "verification in the future"})
            continue
        fingerprint = (re.sub(r"\s+", "", title.casefold()), str(row.get("starts_at")))
        if fingerprint in seen:
            duplicates.append({"row": index, "title": title})
            continue
        seen.add(fingerprint)
        ends = start[0] + (timedelta(days=1) if start[1] else timedelta(hours=2))
        if ends <= now:
            continue
        verified_age = (now - checked[0]).total_seconds() / 86400
        if verified_age > MAX_VERIFICATION_DAYS:
            due_review.append({"title": title, "reason": "verification_expired"})
            continue
        if verified_age >= REVIEW_AFTER_DAYS:
            due_review.append({"title": title, "reason": "verification_aging"})
        days_left = (start[0] - now).total_seconds() / 86400
        future.append((title, category, days_left))
        if days_left <= 90:
            ready_90 += 1
        if days_left <= 30:
            ready_30 += 1
            categories[category] += 1
        if days_left <= 7:
            ready_7 += 1
    total = len(payload)
    missing = [cat for cat in CATEGORIES if categories[cat] == 0]
    return {
        "status": "needs_attention" if errors or duplicates or ready_30 < TARGET_30D else "ok",
        "generated_at": now.isoformat(),
        "total_curated": total,
        "future_7_days": ready_7,
        "future_30_days": ready_30,
        "future_90_days": ready_90,
        "target_30_days": TARGET_30D,
        "category_counts_30d": dict(sorted(categories.items())),
        "missing_categories_30d": missing,
        "source_review_due": due_review,
        "errors": errors,
        "duplicates": duplicates,
        "notes": [
            "A successful HTTP response does not prove an event is still scheduled.",
            "Review source pages before refreshing any verified_at value.",
            "Counts are verified calendar items, NOT virality predictions.",
            "Do not invent publication hours for date-only entries.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description="Audit future event catalog without modifying it")
    parser.add_argument("--catalog", default="data/upcoming.json", type=Path)
    parser.add_argument("--output", default="reports/event-audit.json", type=Path)
    args = parser.parse_args()
    result = audit(args.catalog)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
