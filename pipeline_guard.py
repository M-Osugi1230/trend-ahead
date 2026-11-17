#!/usr/bin/env python3
"""Independent collection watchdog: distinguish healthy, degraded and stale.

The persisted GitHub data files are evidence of saved collection, not of all
Actions scheduler attempts. This command cannot prove why a run is missing.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from collection_health import audit as collection_audit, dt

UTC = timezone.utc
CRITICAL_AFTER_MINUTES = 120
RECENT_AFTER_MINUTES = 75
MIN_RUNS_PER_24H = 46  # 95% of 48 expected half-hourly slots, rounded up


def latest_forecast_journal(data_dir: Path, now: datetime) -> dict | None:
    folder = data_dir / "forecast_runs"
    if not folder.is_dir():
        return None
    newest = None
    for path in sorted(folder.glob("*.jsonl"), reverse=True)[:2]:
        try:
            with path.open(encoding="utf-8") as lines:
                for line in lines:
                    try:
                        item = json.loads(line)
                        issued = dt(item.get("checked_at"))
                        if (issued is not None and issued <= now
                                and (newest is None or issued > dt(newest["checked_at"]))):
                            newest = item
                    except (TypeError, ValueError):
                        continue
        except OSError:
            continue
    return newest


def inspect(data_dir: Path, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    now = now.astimezone(UTC)
    c = collection_audit(data_dir, now)
    lag = c["minutes_since_last_run"]
    last_feeds_ok = c["last_feeds_ok"]
    last_feeds_total = c["last_feeds_total"]
    if lag is None or lag > CRITICAL_AFTER_MINUTES:
        status = "critical_collection_stale"
    elif (lag > RECENT_AFTER_MINUTES or
          c["observed_run_records_24h"] < MIN_RUNS_PER_24H or
          last_feeds_ok != last_feeds_total or
          c["runs_with_429_24h"] > 0 or
          c["cooldown_skipped_feeds_24h"] > 0):
        status = "degraded_collection"
    else:
        status = "recent_collection"
    journal = latest_forecast_journal(data_dir, now)
    last_run_at = dt(c["last_run_at"]) if c["last_run_at"] else None
    journal_at = dt(journal["checked_at"]) if journal else None
    journal_complete = (last_run_at is not None and journal_at is not None
                        and journal_at >= last_run_at)
    forecast_state = (
        "not_reported" if journal is None else
        "lagging_collection" if not journal_complete else
        journal.get("reason", "unknown")
    )
    if status == "recent_collection" and not journal_complete:
        status = "degraded_forecast_audit"
    return {
        "generated_at": now.isoformat(),
        "status": status,
        "collection": c,
        "forecast_audit": {
            "state": forecast_state,
            "checked_at": journal_at.isoformat() if journal_at else None,
            "last_collection_run_at": last_run_at.isoformat() if last_run_at else None,
            "is_current_for_last_saved_run": journal_complete,
            "issued_at_last_check": journal.get("issued") if journal else None,
        },
        "action_required": (
            "Check Actions schedule, jobs, and commit/push history" if
            status == "critical_collection_stale" else
            "Investigate irregular collection and rate limits" if
            status == "degraded_collection" else
            "Inspect forecast issue job and journal" if
            status == "degraded_forecast_audit" else None
        ),
        "limitations": [
            "Only successful data commits appear in data/runs; no root-cause diagnosis is possible here.",
            "A GitHub scheduled watchdog can itself be delayed or skipped.",
            "Freshness does not establish the truth of external news or a forecast hit.",
            "Forecasts with insufficient observation history correctly issue zero candidates.",
        ],
    }


def markdown(report: dict) -> str:
    coll = report["collection"]
    forecast = report["forecast_audit"]
    return "\n".join([
        "# Trend Ahead データパイプライン監視", "",
        f"検査時刻（UTC）：{report['generated_at']}",
        f"状態：**{report['status']}**",
        f"最新保存済み取得：{coll['last_run_at'] or '記録なし'}",
        f"最終記録から：{coll['minutes_since_last_run']}分",
        f"24時間内の保存済み実行：{coll['observed_run_records_24h']}回（設定目標48回）",
        f"最新のRSS取得成功：{coll['last_feeds_ok']}/{coll['last_feeds_total']}",
        f"過去24時間の429発生回：{coll['runs_with_429_24h']}",
        f"最大保存記録間隔：{coll['max_gap_minutes']}分",
        f"予測発行ジョブの記録：{forecast['state']}",
        f"予測発行監査が最新取得をカバー：{forecast['is_current_for_last_saved_run']}",
        "", "## 推奨確認", "",
        report["action_required"] or "監視閾値を満たしています。ただし本番の精度は別途検証。",
        "", "## 注意", "",
        *["- " + note for note in report["limitations"]],
        ""
    ])


def main() -> None:
    parser = argparse.ArgumentParser(description="Alert on stale persisted RSS collection")
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--json-output", type=Path, default=Path("reports/pipeline-health.json"))
    parser.add_argument("--markdown-output", type=Path, default=Path("reports/pipeline-health.md"))
    parser.add_argument("--fail-on-critical", action="store_true")
    args = parser.parse_args()
    report = inspect(args.data_dir)
    for path, content in (
        (args.json_output, json.dumps(report, ensure_ascii=False, indent=2) + "\n"),
        (args.markdown_output, markdown(report)),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    print(json.dumps({
        "status": report["status"],
        "lag_minutes": report["collection"]["minutes_since_last_run"],
        "runs_24h": report["collection"]["observed_run_records_24h"],
        "forecast_audit": report["forecast_audit"]["state"],
    }, ensure_ascii=False))
    if args.fail_on_critical and report["status"] == "critical_collection_stale":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
