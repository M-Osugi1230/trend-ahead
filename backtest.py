#!/usr/bin/env python3
"""Research-only, leakage-aware retrospective evaluation of RSS traffic signals.

This is an exploratory backtest. It does NOT estimate a probability of virality,
social-media reach, or user enjoyment. Missing future RSS readings are censored
instead of mislabeled as negative outcomes.
"""
from __future__ import annotations

import argparse
import json
import math
from bisect import bisect_right
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from signal_engine import EVENT_ID, parse_traffic

JST = timezone(timedelta(hours=9))
MAX_DAYS = 30
EVAL_JST_HOURS = (10, 20)
HORIZON_HOURS = 3
MAX_AGE_MINUTES = 90
FUTURE_TOLERANCE_MINUTES = 45
MIN_HISTORY_GAP_MINUTES = 3


def _parse_time(value):
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value)
        return dt.astimezone(timezone.utc) if dt.tzinfo else None
    except (TypeError, ValueError):
        return None


def load_samples(data_dir: Path, now: datetime):
    """Read only timestamped, valid traffic observations inside a bounded window."""
    rows = defaultdict(dict)
    folder = data_dir / "observations"
    if not folder.exists():
        return {}
    window_start = now - timedelta(days=MAX_DAYS + 1)
    for path in sorted(folder.glob("*.jsonl"), reverse=True)[:MAX_DAYS + 2]:
        try:
            with path.open(encoding="utf-8") as handle:
                for line in handle:
                    try:
                        record = json.loads(line)
                        event_id = record.get("event_id")
                        at = _parse_time(record.get("observed_at"))
                        volume = parse_traffic(record.get("approx_traffic"))
                        if not isinstance(event_id, str) or not EVENT_ID.fullmatch(event_id):
                            continue
                        if at is None or not window_start <= at <= now or volume is None:
                            continue
                        # Identical timestamp is a single observation.
                        rows[event_id][at] = volume
                    except (ValueError, TypeError, json.JSONDecodeError):
                        continue
        except OSError:
            continue
    return {key: sorted(items.items()) for key, items in rows.items()}


def eligible_case(series, cutoff, horizon=timedelta(hours=HORIZON_HOURS)):
    """Return features at cutoff and a future label ONLY when future evidence exists.

    Selection is fixed at each JST clock time, not optimized to the outcome.
    The previous and current observations must precede the cutoff.
    """
    timestamps = [row[0] for row in series]
    i = bisect_right(timestamps, cutoff) - 1
    if i < 1:
        return None
    latest_at, current = series[i]
    if not timedelta(0) <= cutoff - latest_at <= timedelta(minutes=MAX_AGE_MINUTES):
        return None
    prior = None
    for j in range(i - 1, -1, -1):
        at, value = series[j]
        gap = latest_at - at
        if gap >= timedelta(minutes=MIN_HISTORY_GAP_MINUTES):
            if gap <= timedelta(hours=6):
                prior = (at, value)
            break
    if prior is None:
        return None
    past = prior[1]
    momentum = (current - past) / max(1, past)
    target = cutoff + horizon
    lower = target - timedelta(minutes=FUTURE_TOLERANCE_MINUTES)
    upper = target + timedelta(minutes=FUTURE_TOLERANCE_MINUTES)
    future_options = [(abs((at - target).total_seconds()), at, volume)
                      for at, volume in series[i + 1:]
                      if lower <= at <= upper and at > cutoff]
    future = min(future_options)[2] if future_options else None
    return {
        "volume_score": math.log1p(current),
        "momentum_score": momentum,
        "current_volume": current,
        "outcome": None if future is None else int(future >= current * 1.25 and future - current >= 100),
        "future_volume": future,
    }


def _precision_at_k(cohort, method, k=3):
    if not cohort:
        return None
    ranked = sorted(cohort, key=lambda c: (-c[method], c["event_id"]))
    chosen = ranked[: min(k, len(ranked))]
    return sum(c["outcome"] for c in chosen) / len(chosen)


def evaluate(data_dir: Path, now: datetime | None = None, *, minimum_cases=50, minimum_days=7):
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    now = now.astimezone(timezone.utc)
    samples = load_samples(data_dir, now)
    horizon = timedelta(hours=HORIZON_HOURS)
    # Outcomes must be observed; avoid reporting the newest partial time windows.
    last_cutoff = now - horizon - timedelta(minutes=FUTURE_TOLERANCE_MINUTES)
    first_date = (now - timedelta(days=MAX_DAYS)).astimezone(JST).date()
    last_date = now.astimezone(JST).date()
    cohorts = []
    missing_outcomes = 0
    possible_cases = 0
    current = first_date
    while current <= last_date:
        for local_hour in EVAL_JST_HOURS:
            cutoff = datetime(current.year, current.month, current.day,
                              local_hour, tzinfo=JST).astimezone(timezone.utc)
            if cutoff > last_cutoff:
                continue
            group = []
            for event_id, series in samples.items():
                candidate = eligible_case(series, cutoff, horizon)
                if candidate is None:
                    continue
                possible_cases += 1
                if candidate["outcome"] is None:
                    missing_outcomes += 1
                    continue
                group.append({"event_id": event_id, **candidate})
            if group:
                cohorts.append({"cutoff_at": cutoff.isoformat(), "cases": group})
        current += timedelta(days=1)

    labeled = [item for group in cohorts for item in group["cases"]]
    dates = {group["cutoff_at"][:10] for group in cohorts}
    positive = sum(item["outcome"] for item in labeled)
    unique_events = len({item["event_id"] for item in labeled})
    result = {
        "generated_at": now.isoformat(),
        "status": "insufficient_data",
        "method": "exploratory_nonprobabilistic_daily_cohorts",
        "horizon_hours": HORIZON_HOURS,
        "cutoff_hours_jst": list(EVAL_JST_HOURS),
        "labeled_cases": len(labeled),
        "positive_cases": positive,
        "unique_events": unique_events,
        "days_with_labels": len(dates),
        "eligible_cases_before_censoring": possible_cases,
        "missing_future_outcomes": missing_outcomes,
        "minimum_cases": minimum_cases,
        "minimum_days": minimum_days,
        "precision_at_3_momentum": None,
        "precision_at_3_volume_baseline": None,
        "caveats": [
            "RSS traffic numbers are bucketed and time-window dependent.",
            "Missing future readings are censored, not counted as zero growth.",
            "Observations exist only for RSS-listed topics (selection bias).",
            "Multiple cutoffs can include the same topic; results are not independent.",
            "Search-volume growth is not equivalent to social virality or fun.",
        ],
    }
    if len(labeled) < minimum_cases or len(dates) < minimum_days or positive < 5:
        return result
    precision_momentum = [_precision_at_k(group["cases"], "momentum_score") for group in cohorts]
    precision_volume = [_precision_at_k(group["cases"], "volume_score") for group in cohorts]
    result.update({
        "status": "exploratory_evaluation_available",
        "precision_at_3_momentum": round(sum(precision_momentum) / len(precision_momentum), 4),
        "precision_at_3_volume_baseline": round(sum(precision_volume) / len(precision_volume), 4),
        "cohorts": len(cohorts),
    })
    return result


def main():
    parser = argparse.ArgumentParser(description="Evaluate archived trends without look-ahead leakage")
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--output", type=Path, default=Path("reports/backtest.json"))
    args = parser.parse_args()
    report = evaluate(args.data_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
