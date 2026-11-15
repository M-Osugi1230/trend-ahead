#!/usr/bin/env python3
"""Apply explicitly human-approved candidates to the curated upcoming catalog.

This is intentionally an OFFLINE CLI, never invoked from scheduled Actions.
The default mode is dry-run. A reviewed candidate, a fresh timestamp, and an
explicit --apply are required before public source data can change.
"""
from __future__ import annotations

import argparse
import json
import re
import unicodedata
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
CATEGORIES = {
    "SNS・ネットミーム", "テレビ・芸能", "ゲーム", "音楽・ライブ",
    "スポーツ", "映画・アニメ", "配信・YouTuber", "グルメ・商品",
    "季節・イベント", "テクノロジー", "ニュース・社会", "その他の新現象",
}
REQUIRED_APPROVAL_FIELDS = (
    "candidate_id", "title", "starts_at", "category", "description",
    "official_url", "verified_at", "reviewer", "review_note",
)


def normalized(value: str):
    return re.sub(r"[\s　「」『』・\-—._!！?？]+", "",
                  unicodedata.normalize("NFKC", value).casefold())


def event_day(value):
    if not isinstance(value, str):
        raise ValueError("starts_at must be a string")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return date.fromisoformat(value)
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        raise ValueError("an exact event time needs a timezone")
    return dt.astimezone(JST).date()


def official_https(url):
    try:
        parsed = urlsplit(url)
        return (parsed.scheme == "https" and bool(parsed.hostname)
                and parsed.username is None and parsed.password is None
                and parsed.port in (None, 443)
                and not any(ord(c) < 32 for c in url))
    except (TypeError, ValueError):
        return False


def prepare(queue, approvals, curated, now):
    """Validate ALL approvals atomically; return a new list without file writes."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    candidates = {x.get("candidate_id"): x for x in queue.get("candidates", [])
                  if isinstance(x, dict) and x.get("status") == "needs_review"}
    if queue.get("publication") != "manual_editorial_review_only":
        raise ValueError("queue must be explicitly marked review-only")
    if not isinstance(approvals, list) or not isinstance(curated, list):
        raise ValueError("approvals and curated must be arrays")
    result = list(curated)
    stats = {"requested": len(approvals), "new": 0, "duplicates_rejected": 0, "errors": []}
    fingerprints = {(normalized(str(x.get("title", ""))),
                     event_day(str(x["starts_at"]))) for x in curated
                    if isinstance(x, dict) and x.get("title") and x.get("starts_at")}
    for ix, approval in enumerate(approvals):
        try:
            if not isinstance(approval, dict):
                raise ValueError("approval must be a JSON object")
            if approval.get("status") != "approved" or approval.get("source_confirmed") is not True:
                raise ValueError("requires human approval and source_confirmed=true")
            missing = [k for k in REQUIRED_APPROVAL_FIELDS
                       if not isinstance(approval.get(k), str) or not approval[k].strip()]
            if missing:
                raise ValueError("missing human review fields: " + ", ".join(missing))
            candidate = candidates.get(approval["candidate_id"])
            if candidate is None:
                raise ValueError("candidate_id not in the review-only queue")
            title = approval["title"].strip()
            if normalized(title) != normalized(candidate["title"]):
                raise ValueError("candidate title differs; investigate instead of silently renaming")
            day = event_day(approval["starts_at"])
            if day.isoformat() != candidate["starts_at"]:
                raise ValueError("approved date differs from discovered date; investigate first")
            if day < now.astimezone(JST).date() or day > now.astimezone(JST).date() + timedelta(days=90):
                raise ValueError("event is outside future 90-day window")
            checked = datetime.fromisoformat(approval["verified_at"])
            if (checked.tzinfo is None or
                    not now - timedelta(days=2) <= checked <= now + timedelta(minutes=5)):
                raise ValueError("verified_at must be a recent human check with timezone")
            if approval["category"] not in CATEGORIES:
                raise ValueError("unknown category")
            if not official_https(approval["official_url"]):
                raise ValueError("approved organizer URL must be HTTPS")
            if not 5 <= len(title) <= 150:
                raise ValueError("invalid event title")
            if not 3 <= len(approval["reviewer"].strip()) <= 100 or not approval["review_note"].strip():
                raise ValueError("reviewer and evidence note required")
            identity = (normalized(title), day)
            if identity in fingerprints:
                stats["duplicates_rejected"] += 1
                raise ValueError("event is already curated or duplicated in approvals")
            fingerprints.add(identity)
            # Editorial metadata must not be confused with public event claims.
            row = {key: approval[key] for key in (
                "title", "starts_at", "category", "description", "official_url", "verified_at"
            )}
            result.append(row)
            stats["new"] += 1
        except (ValueError, KeyError, TypeError) as exc:
            stats["errors"].append({"index": ix, "candidate_id": approval.get("candidate_id")
                                    if isinstance(approval, dict) else None,
                                    "reason": str(exc)})
    result.sort(key=lambda x: str(x.get("starts_at", "")))
    return result, stats


def main():
    parser = argparse.ArgumentParser(description="Validate curated-event approvals (dry-run by default)")
    parser.add_argument("--queue", type=Path, required=True,
                        help="event-candidates.json from a successful discovery artifact")
    parser.add_argument("--approvals", type=Path, required=True,
                        help="human-filled list of records with source_confirmed=true")
    parser.add_argument("--catalog", type=Path, default=Path("data/upcoming.json"))
    parser.add_argument("--apply", action="store_true",
                        help="explicitly write validated results to the curated catalog")
    args = parser.parse_args()
    queue = json.loads(args.queue.read_text(encoding="utf-8"))
    approvals = json.loads(args.approvals.read_text(encoding="utf-8"))
    curated = json.loads(args.catalog.read_text(encoding="utf-8"))
    updated, stats = prepare(queue, approvals, curated, datetime.now(timezone.utc))
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    if stats["errors"]:
        raise SystemExit("No files changed: approval validation failed")
    if args.apply:
        tmp = args.catalog.with_suffix(args.catalog.suffix + ".tmp")
        tmp.write_text(json.dumps(updated, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8")
        tmp.replace(args.catalog)
        print("Applied reviewed events to " + str(args.catalog))
    else:
        print("Dry run only. Explicit --apply required before writing.")


if __name__ == "__main__":
    main()
