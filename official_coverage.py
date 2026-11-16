#!/usr/bin/env python3
"""Audit approved-calendar coverage within successfully extracted official sources.

This is NOT real-world event recall or trend prediction accuracy.
Missing/blocked official sources are unknown rather than negative outcomes.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from event_discovery import match_curated, parse_date

JST = ZoneInfo("Asia/Tokyo")
MAX_DAYS = 90
SUCCESS = {"ok", "ok_no_upcoming_in_horizon"}


def md(value):
    value = str(value).replace("\r", " ").replace("\n", " ")
    return re.sub(r"([*_{}\[\]()#+.!|>~-])", r"\\\1", value)


def evaluate(discovery: dict, curated: list, sources: list,
             now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("aware timestamp required")
    today = now.astimezone(JST).date()
    source_map = {
        item["id"]: item for item in sources
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    states = {
        item["source_id"]: item.get("status")
        for item in discovery.get("sources", [])
        if isinstance(item, dict) and isinstance(item.get("source_id"), str)
    }
    failures = [
        {"source_id": key, "status": state} for key, state in sorted(states.items())
        if state not in SUCCESS
    ]
    not_reported = sorted(set(source_map) - set(states))
    seen = set()
    rows = []
    counts = Counter()
    for item in discovery.get("observed", []):
        if not isinstance(item, dict):
            continue
        source_id = item.get("source_id")
        if source_id not in source_map or states.get(source_id) != "ok":
            continue
        date = parse_date(item.get("starts_at"))
        if date is None or not today <= date <= today + timedelta(days=MAX_DAYS):
            continue
        title = item.get("title")
        if not isinstance(title, str) or not title.strip():
            continue
        identity = (source_id, item.get("candidate_id"), date, title)
        if identity in seen:
            continue
        seen.add(identity)
        candidate = {"title": title, "starts_at": date.isoformat(),
                     "event_type": item.get("event_type", "unknown")}
        match = match_curated(candidate, curated)
        status = ("published_match" if match == "exact_curated" else
                  "possible_duplicate" if match == "possible_curated" else
                  "not_in_catalog")
        counts[status] += 1
        rows.append({
            "candidate_id": item.get("candidate_id"),
            "title": title[:150],
            "starts_at": date.isoformat(),
            "source_id": source_id,
            "category": source_map[source_id].get("category"),
            "official_url": source_map[source_id].get("url"),
            "catalog_status": status,
            "priority": ("within_7_days" if date <= today + timedelta(days=7)
                         else "within_14_days" if date <= today + timedelta(days=14)
                         else "routine"),
        })
    rows.sort(key=lambda x: (
        x["catalog_status"] == "published_match", x["starts_at"], x["source_id"], x["title"]
    ))
    total = len(rows)
    gaps = [item for item in rows if item["catalog_status"] != "published_match"]
    unknown = bool(failures or not_reported)
    return {
        "generated_at": now.isoformat(),
        "scope": "upcoming_parsed_official_source_events_only",
        "publication": "human_review_only",
        "status": ("no_source_evidence" if not states else
                   "needs_review" if gaps or unknown else
                   "no_observed_events" if not total else "ok_within_observed_sources"),
        "sources_configured": len(source_map),
        "sources_reported": len(states),
        "sources_parseable": sum(x in SUCCESS for x in states.values()),
        "sources_failed_or_unparseable": failures,
        "sources_not_reported": not_reported,
        "official_events_observed": total,
        "already_in_approved_calendar": counts["published_match"],
        "possible_duplicates": counts["possible_duplicate"],
        "unlisted_official_candidates": counts["not_in_catalog"],
        "observed_catalog_match_fraction": (
            round(counts["published_match"] / total, 4) if total else None
        ),
        "review_queue": gaps,
        "official_exclusions": [
            item for item in discovery.get("official_exclusions", [])
            if isinstance(item, dict)
        ],
        "caveats": [
            "Coverage is defined ONLY against events extracted from currently successful sources.",
            "Unable-to-fetch and unparsable sources make overall coverage unknown.",
            "Unlisted events are editorial leads, not proof of a missed forecast.",
            "Matching catalog entries does not imply they will become popular.",
            "Cancellation and postponement indications require a human review.",
        ],
    }


def markdown(report: dict) -> str:
    lines = [
        "# 公式イベント：掲載漏れ監査（審査用）", "",
        "この割合は抽出できた公式予定に限定した掲載照合率です。",
        "すべての重要イベントの捕捉率・予測的中率ではありません。",
        "**候補の自動公開、自動修正、確認日時の自動更新はしません。**", "",
        f"生成：{report['generated_at']}",
        f"取得元：{report['sources_parseable']}/{report['sources_configured']}",
        f"抽出済みの将来イベント：{report['official_events_observed']}",
        f"公開データと一致：{report['already_in_approved_calendar']}",
        f"未掲載：{report['unlisted_official_candidates']}",
        f"重複・変更疑い：{report['possible_duplicates']}",
        f"抽出済み予定との一致割合：{report['observed_catalog_match_fraction']}",
        "", "## 優先確認すべき公式予定", "",
    ]
    for item in report["review_queue"]:
        lines.append(
            f"- [ ] {md(item['starts_at'])}｜{md(item['title'])}"
            f"｜{md(item['catalog_status'])}｜{md(item['priority'])}"
        )
        lines.append(f"  - {md(item['official_url'])}")
    if not report["review_queue"]:
        lines.append("候補なし（情報源が正常に収集できた場合に限る）。")
    lines.extend(["", "## 取得できない、または抽出に失敗した情報源", ""])
    for item in report["sources_failed_or_unparseable"]:
        lines.append(f"- {md(item['source_id'])}：{md(item['status'])}")
    for source_id in report["sources_not_reported"]:
        lines.append(f"- {md(source_id)}：収集結果なし")
    if not report["sources_failed_or_unparseable"] and not report["sources_not_reported"]:
        lines.append("なし")
    lines.extend(["", "## 中止・延期の公式表示（掲載変更の要審査）", ""])
    for item in report["official_exclusions"]:
        lines.append(
            f"- [ ] {md(item.get('starts_at'))}｜"
            f"{md(item.get('team_name', '日本代表'))}｜{md(item.get('competition'))}"
        )
    if not report["official_exclusions"]:
        lines.append("抽出結果なし")
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description="Report official event discovery/catalog gaps")
    parser.add_argument("--discovery", type=Path, default=Path("reports/event-candidates.json"))
    parser.add_argument("--curated", type=Path, default=Path("data/upcoming.json"))
    parser.add_argument("--sources", type=Path, default=Path("data/event_sources.json"))
    parser.add_argument("--json-output", type=Path, default=Path("reports/official-coverage.json"))
    parser.add_argument("--review-output", type=Path, default=Path("reports/official-coverage.md"))
    args = parser.parse_args()
    result = evaluate(
        json.loads(args.discovery.read_text(encoding="utf-8")),
        json.loads(args.curated.read_text(encoding="utf-8")),
        json.loads(args.sources.read_text(encoding="utf-8")),
    )
    for target, content in (
        (args.json_output, json.dumps(result, ensure_ascii=False, indent=2) + "\n"),
        (args.review_output, markdown(result)),
    ):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    print(json.dumps({
        key: result[key] for key in (
            "status", "official_events_observed", "already_in_approved_calendar",
            "unlisted_official_candidates", "possible_duplicates", "sources_parseable"
        )
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
