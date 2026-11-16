#!/usr/bin/env python3
"""Compare first-party event sightings across runs without auto-editing listings.

Important: An absent listing is NOT proof of cancellation, and an apparent
date change is NOT a confirmed postponement. These are editorial review leads.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from event_discovery import normalize, parse_date

JST = ZoneInfo("Asia/Tokyo")
SCHEMA_VERSION = 1
MAX_BASELINE_AGE_DAYS = 8


def _identity(row):
    if not isinstance(row, dict):
        return None
    source = row.get("source_id")
    title = row.get("title")
    if not isinstance(source, str) or not isinstance(title, str):
        return None
    normalized_title = normalize(title)
    if len(normalized_title) < 3:
        return None
    return (source, normalized_title, row.get("event_type", "unknown"))


def _source_states(report):
    return {row["source_id"]: row.get("status")
            for row in report.get("sources", [])
            if isinstance(row, dict) and isinstance(row.get("source_id"), str)}


def snapshot(report):
    """Persist what the source actually showed, including already-curated events."""
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": report.get("generated_at"),
        "observed": report.get("observed", []),
        "sources": report.get("sources", []),
    }


def compare(previous, current, now):
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    curr = snapshot(current)
    current_states = _source_states(curr)
    bad_sources = [{"source_id": sid, "status": state}
                   for sid, state in current_states.items() if state != "ok"]
    result = {
        "generated_at": now.isoformat(),
        "status": "baseline_only",
        "publication": "manual_editorial_review_only",
        "source_issues": bad_sources,
        "date_changes_to_check": [],
        "no_longer_seen_to_check": [],
        "new_sightings": [],
        "ambiguous_groups": [],
        "comparison_sources": [],
        "note": ("Missing from a calendar never proves cancellation. "
                 "The site and curated data are NOT automatically changed."),
    }
    if (not isinstance(previous, dict)
            or previous.get("schema_version") != SCHEMA_VERSION
            or not isinstance(previous.get("observed"), list)):
        return result
    try:
        when = datetime.fromisoformat(previous["generated_at"])
        if when.tzinfo is None or not timedelta(0) <= now - when <= timedelta(days=MAX_BASELINE_AGE_DAYS):
            result["status"] = "stale_baseline"
            return result
    except (ValueError, TypeError, KeyError):
        result["status"] = "invalid_baseline"
        return result

    old_states = _source_states(previous)
    stable_sources = {sid for sid, status in old_states.items()
                      if status == "ok" and current_states.get(sid) == "ok"}
    result["comparison_sources"] = sorted(stable_sources)
    if not stable_sources:
        result["status"] = "no_comparable_sources"
        return result

    def group(rows):
        groups = defaultdict(list)
        for row in rows:
            identity = _identity(row)
            if identity and identity[0] in stable_sources:
                # A date-only listing and a timestamped listing are both valid,
                # but dates must be parseable to compare safely.
                day = parse_date(row.get("starts_at"))
                if day is not None:
                    groups[identity].append(row)
        return groups

    before = group(previous["observed"])
    after = group(curr["observed"])
    today = now.astimezone(JST).date()
    for key in sorted(before.keys() | after.keys()):
        old_items = before.get(key, [])
        new_items = after.get(key, [])
        old_future = [row for row in old_items if parse_date(row["starts_at"]) >= today]
        new_future = [row for row in new_items if parse_date(row["starts_at"]) >= today]
        if not old_future and not new_future:
            continue
        if old_future and new_future and len(old_future) == 1 and len(new_future) == 1:
            old_time = old_future[0]["starts_at"]
            new_time = new_future[0]["starts_at"]
            if old_time != new_time:
                result["date_changes_to_check"].append({
                    "source_id": key[0], "title": new_future[0]["title"],
                    "previous": old_time, "current": new_time,
                    "reason": "same_source_same_title_different_time_check_official_page",
                })
            continue
        if old_future and new_future:
            old_dates = sorted(set(row["starts_at"] for row in old_future))
            new_dates = sorted(set(row["starts_at"] for row in new_future))
            if old_dates != new_dates:
                result["ambiguous_groups"].append({
                    "source_id": key[0], "title": new_future[0]["title"],
                    "previous_dates": old_dates, "current_dates": new_dates,
                    "reason": "multiple_sessions_do_not_assume_postponement",
                })
            continue
        if old_future and not new_future:
            for row in old_future:
                result["no_longer_seen_to_check"].append({
                    "source_id": key[0], "title": row["title"],
                    "previous": row["starts_at"],
                    "reason": "not_seen_in_latest_listing_not_proof_of_cancellation",
                })
        elif new_future and not old_future:
            for row in new_future:
                result["new_sightings"].append({
                    "source_id": key[0], "title": row["title"],
                    "current": row["starts_at"],
                    "reason": "new_or_newly_visible_source_listing",
                })
    result["status"] = (
        "attention" if result["source_issues"]
        or result["date_changes_to_check"]
        or result["no_longer_seen_to_check"] or result["ambiguous_groups"]
        else "ok"
    )
    return result


def markdown(report):
    lines = [
        "# 公式予定の変化・編集確認リスト", "",
        "これは**自動確定された延期・中止情報ではありません**。"
        "会場カレンダーの表示件数、掲載順、終了、取得制限でも差分が発生します。",
        "",
        f"結果：{report['status']}／比較可能な公式ソース "
        f"{len(report['comparison_sources'])}件", "",
    ]
    sections = (
        ("日時が変わった可能性（要確認）", "date_changes_to_check"),
        ("前回あった予定が見えない（中止とは断定しない）", "no_longer_seen_to_check"),
        ("複数公演で差分あり（要確認）", "ambiguous_groups"),
        ("新しく見つかった掲載（審査候補）", "new_sightings"),
    )
    for label, key in sections:
        rows = report[key]
        lines.extend([f"## {label}：{len(rows)}件", ""])
        if not rows:
            lines.extend(["該当なし", ""])
            continue
        for row in rows:
            dates = " / ".join(str(row.get(v, "")) for v in (
                "previous", "current", "previous_dates", "current_dates"
            ) if row.get(v))
            lines.append(f"- [ ] {row['source_id']}｜{row['title']}｜{dates}")
        lines.append("")
    if report["source_issues"]:
        lines.extend(["## 取得・抽出で問題のあった公式ソース", ""])
        for item in report["source_issues"]:
            lines.append(f"- {item['source_id']}：{item['status']}")
        lines.append("")
    lines.append("**自動公開・自動削除・verified_atの自動更新は一切行いません。**")
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description="Editorial-only first-party listing change detection")
    parser.add_argument("--current", type=Path, default=Path("reports/event-candidates.json"))
    parser.add_argument("--previous", type=Path, default=Path("reports/event-snapshot.json"))
    parser.add_argument("--snapshot-out", type=Path, default=Path("reports/event-snapshot.json"))
    parser.add_argument("--report-out", type=Path, default=Path("reports/event-changes.json"))
    parser.add_argument("--review-out", type=Path, default=Path("reports/event-changes.md"))
    args = parser.parse_args()
    current = json.loads(args.current.read_text(encoding="utf-8"))
    previous = None
    if args.previous.exists():
        try:
            previous = json.loads(args.previous.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    now = datetime.now(timezone.utc)
    result = compare(previous, current, now)
    for path, value in (
        (args.snapshot_out, json.dumps(snapshot(current), ensure_ascii=False, indent=2) + "\n"),
        (args.report_out, json.dumps(result, ensure_ascii=False, indent=2) + "\n"),
        (args.review_out, markdown(result)),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")
    print(json.dumps({
        "status": result["status"],
        "sources_compared": len(result["comparison_sources"]),
        "date_changes_to_check": len(result["date_changes_to_check"]),
        "no_longer_seen_to_check": len(result["no_longer_seen_to_check"]),
        "source_issues": len(result["source_issues"]),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
