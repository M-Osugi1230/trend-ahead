#!/usr/bin/env python3
"""Read-only freshness / schedule health audit for GitHub Actions RSS collection."""
from __future__ import annotations

import argparse
import json
import statistics
from datetime import datetime, timedelta, timezone
from pathlib import Path

UTC = timezone.utc
WINDOW = timedelta(hours=24)
TARGET_MINUTES = 30
STALE_AFTER = timedelta(hours=3)


def dt(value):
    if not isinstance(value, str):
        return None
    try:
        value = datetime.fromisoformat(value)
        return value.astimezone(UTC) if value.tzinfo else None
    except ValueError:
        return None


def audit(data_dir: Path, now: datetime | None = None) -> dict:
    now = (now or datetime.now(UTC)).astimezone(UTC)
    folder = data_dir / "runs"
    records = {}
    for path in sorted(folder.glob("*.jsonl"), reverse=True)[:2] if folder.exists() else []:
        try:
            with path.open(encoding="utf-8") as lines:
                for line in lines:
                    try:
                        item = json.loads(line)
                        when = dt(item.get("ran_at"))
                        if when and now - WINDOW <= when <= now:
                            records[when] = item
                    except (TypeError, ValueError):
                        continue
        except OSError:
            continue
    runs = sorted(records.items())
    latest = runs[-1] if runs else None
    intervals = [(b[0] - a[0]).total_seconds() / 60 for a, b in zip(runs, runs[1:])]
    failed = sum(1 for _, row in runs if int(row.get("feeds_ok", 0)) == 0)
    throttled = sum(1 for _, row in runs
                    if any("429" in str(error.get("error", "")) for error in row.get("errors", [])
                           if isinstance(error, dict)))
    skipped = sum(int(row.get("feeds_skipped_rate_limit", 0)) for _, row in runs)
    lag = (now - latest[0]) if latest else None
    result = {
        "generated_at": now.isoformat(),
        "status": ("no_recent_runs" if lag is None else
                   "stale" if lag > STALE_AFTER else
                   "degraded" if latest[1].get("feeds_ok") != latest[1].get("feeds_total")
                                 or throttled else "recent"),
        "scheduled_interval_minutes": TARGET_MINUTES,
        "expected_run_slots_24h": 48,
        "observed_run_records_24h": len(runs),
        "record_count_vs_target_slots": round(len(runs) / 48, 4),
        "last_run_at": latest[0].isoformat() if latest else None,
        "minutes_since_last_run": round(lag.total_seconds() / 60, 1) if lag is not None else None,
        "median_gap_minutes": round(statistics.median(intervals), 1) if intervals else None,
        "max_gap_minutes": round(max(intervals), 1) if intervals else None,
        "completely_failed_runs_24h": failed,
        "runs_with_429_24h": throttled,
        "cooldown_skipped_feeds_24h": skipped,
        "last_feeds_ok": latest[1].get("feeds_ok") if latest else None,
        "last_feeds_total": latest[1].get("feeds_total") if latest else None,
        "caveats": [
            "Run logs exist only when the collection job reaches the commit stage.",
            "A missing record may mean skipped scheduling, failed job, or failed git push.",
            "48 slots per 24h is a configured goal, not a proven GitHub Actions SLA.",
            "GitHub schedule may be delayed or skipped; inspect Actions history for root causes.",
        ],
    }
    return result


def main():
    parser = argparse.ArgumentParser(description="Audit RSS collection regularity without claiming uptime")
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--output", type=Path, default=Path("reports/collection-health.json"))
    args = parser.parse_args()
    report = audit(args.data_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": report["status"],
        "observed_run_records_24h": report["observed_run_records_24h"],
        "minutes_since_last_run": report["minutes_since_last_run"],
        "runs_with_429_24h": report["runs_with_429_24h"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
