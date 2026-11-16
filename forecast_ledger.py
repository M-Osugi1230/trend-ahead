#!/usr/bin/env python3
"""Append-only, prospective Google Trends RSS forecast journal and evaluator.

A forecast is issued at the ACTUAL collection time using only earlier readings.
No historical observation is backfilled as a forecast. The outcome is a later
RSS lower-bound reading, NOT proof of a societal trend or SNS virality.
Missing future observations are censored, not counted as forecast failures.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backtest import load_samples
from signal_engine import EVENT_ID

UTC = timezone.utc
JST = timezone(timedelta(hours=9))
HORIZON = timedelta(hours=3)
TOLERANCE = timedelta(minutes=45)
MAX_RUN_LAG = timedelta(minutes=20)
MIN_HISTORY = timedelta(minutes=30)
MAX_HISTORY = timedelta(hours=6)
DEDUP_WINDOW = timedelta(hours=3)
MIN_CASES = 50
MIN_DAYS = 7
MIN_POSITIVES = 5
MODEL_VERSION = "rss_momentum_25pct_v1"


def parse_time(value):
    if not isinstance(value, str):
        return None
    try:
        result = datetime.fromisoformat(value)
        return result.astimezone(UTC) if result.tzinfo is not None else None
    except ValueError:
        return None


def read_forecasts(data_dir: Path, now: datetime) -> list[dict]:
    folder = data_dir / "predictions"
    if not folder.exists():
        return []
    result = {}
    for path in sorted(folder.glob("*.jsonl"), reverse=True)[:33]:
        try:
            with path.open(encoding="utf-8") as handle:
                for line in handle:
                    try:
                        item = json.loads(line)
                    except (ValueError, TypeError):
                        continue
                    if not isinstance(item, dict):
                        continue
                    event_id = item.get("event_id")
                    issued = parse_time(item.get("issued_at"))
                    current_at = parse_time(item.get("current_observed_at"))
                    forecast_id = item.get("forecast_id")
                    if (not isinstance(event_id, str) or not EVENT_ID.fullmatch(event_id)
                            or not isinstance(forecast_id, str) or len(forecast_id) != 24
                            or issued is None or current_at is None or issued > now
                            or issued < now - timedelta(days=31)
                            or current_at > issued
                            or item.get("model_version") != MODEL_VERSION
                            or type(item.get("forecast_positive")) is not bool
                            or type(item.get("current_lower_bound")) is not int):
                        continue
                    result[forecast_id] = item
        except OSError:
            continue
    return sorted(result.values(), key=lambda row: (row["issued_at"], row["forecast_id"]))


def build_forecasts(data_dir: Path, now: datetime, *, last_run_at: datetime) -> list[dict]:
    """Only freeze new eligible predictions when called near a real collection."""
    if now.tzinfo is None or last_run_at.tzinfo is None:
        raise ValueError("timestamps must be timezone aware")
    now = now.astimezone(UTC)
    last_run_at = last_run_at.astimezone(UTC)
    if not timedelta(0) <= now - last_run_at <= MAX_RUN_LAG:
        return []

    prior_forecasts = read_forecasts(data_dir, now)
    recently_issued = {
        row["event_id"] for row in prior_forecasts
        if now - parse_time(row["issued_at"]) < DEDUP_WINDOW
    }
    rows = []
    for event_id, series in sorted(load_samples(data_dir, now).items()):
        if event_id in recently_issued or len(series) < 2:
            continue
        latest_at, current = series[-1]
        if latest_at != last_run_at:
            continue  # Never infer a forecast from stale/other collection data.
        prior = None
        for at, volume in reversed(series[:-1]):
            delta = latest_at - at
            if delta >= MIN_HISTORY:
                if delta <= MAX_HISTORY:
                    prior = (at, volume)
                break
        if prior is None:
            continue
        older_at, older = prior
        forecast_positive = (current - older >= 100 and current >= older * 1.25)
        issued_at = now.isoformat()
        forecast_id = hashlib.sha256(
            f"{event_id}|{issued_at}|{MODEL_VERSION}".encode("utf-8")
        ).hexdigest()[:24]
        rows.append({
            "forecast_id": forecast_id,
            "event_id": event_id,
            "issued_at": issued_at,
            "current_observed_at": latest_at.isoformat(),
            "previous_observed_at": older_at.isoformat(),
            "current_lower_bound": current,
            "previous_lower_bound": older,
            "momentum_ratio": round(current / older, 4),
            "model_version": MODEL_VERSION,
            "forecast_positive": forecast_positive,
            "target_at": (now + HORIZON).isoformat(),
            "horizon_hours": 3,
            "outcome_definition": "RSS estimate +25% and +100 vs frozen current lower bound",
            "status": "issued_pending_future_observation",
            "source": "google_trends_jp",
        })
    return rows


def issue_forecasts(data_dir: Path, now: datetime | None = None) -> dict:
    now = (now or datetime.now(UTC)).astimezone(UTC)
    runs_folder = data_dir / "runs"
    last_run_at = None
    last_run = None
    for path in sorted(runs_folder.glob("*.jsonl"), reverse=True)[:2] if runs_folder.exists() else []:
        try:
            with path.open(encoding="utf-8") as handle:
                for line in handle:
                    try:
                        run = json.loads(line)
                        when = parse_time(run.get("ran_at"))
                        if when is not None and when <= now and (last_run_at is None or when > last_run_at):
                            last_run_at, last_run = when, run
                    except (TypeError, ValueError):
                        continue
        except OSError:
            continue
    if last_run_at is None or not last_run or int(last_run.get("observations_added", 0)) == 0:
        return {"issued": 0, "reason": "no_successful_trend_observations"}
    rows = build_forecasts(data_dir, now, last_run_at=last_run_at)
    if rows:
        target = data_dir / "predictions" / f"{now.astimezone(JST):%Y-%m-%d}.jsonl"
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    return {"issued": len(rows),
            "reason": "new_snapshots" if rows else "insufficient_history_or_no_recent_collection",
            "last_run_at": last_run_at.isoformat()}


def evaluate_forecasts(data_dir: Path, now: datetime | None = None) -> dict:
    now = (now or datetime.now(UTC)).astimezone(UTC)
    predictions = read_forecasts(data_dir, now)
    samples = load_samples(data_dir, now)
    counts = Counter()
    examples = []
    mature = []
    positives = 0
    for prediction in predictions:
        cutoff = parse_time(prediction["issued_at"])
        target = cutoff + HORIZON
        # The horizon must have finished before we call an outcome 'missing'.
        if now < target + TOLERANCE:
            status = "pending"
        else:
            options = []
            for at, volume in samples.get(prediction["event_id"], []):
                if at > cutoff and abs(at - target) <= TOLERANCE:
                    options.append((abs((at - target).total_seconds()), at, volume))
            if not options:
                status = "missing_future"
            else:
                future = min(options)[2]
                baseline = prediction["current_lower_bound"]
                realized = future - baseline >= 100 and future >= baseline * 1.25
                forecast_positive = prediction["forecast_positive"]
                status = ("true_positive" if forecast_positive and realized else
                          "false_positive" if forecast_positive and not realized else
                          "false_negative" if realized else "true_negative")
                positives += int(realized)
                mature.append(prediction | {"actual_outcome": realized, "future_lower_bound": future,
                                            "status": status})
        counts[status] += 1
        if len(examples) < 8:
            examples.append({"forecast_id": prediction["forecast_id"],
                             "event_id": prediction["event_id"],
                             "issued_at": prediction["issued_at"],
                             "forecast_positive": prediction["forecast_positive"],
                             "evaluation_status": status})
    days = len({parse_time(row["issued_at"]).astimezone(JST).date() for row in mature})
    enough = len(mature) >= MIN_CASES and days >= MIN_DAYS and positives >= MIN_POSITIVES
    predicted_positive = counts["true_positive"] + counts["false_positive"]
    return {
        "generated_at": now.isoformat(),
        "status": "evaluation_available" if enough else "insufficient_data",
        "model_version": MODEL_VERSION,
        "issued_forecasts": len(predictions),
        "mature_labeled": len(mature),
        "pending": counts["pending"],
        "missing_future": counts["missing_future"],
        "positive_outcomes": positives,
        "days_with_labels": days,
        "minimum_cases": MIN_CASES,
        "minimum_days": MIN_DAYS,
        "minimum_positive_outcomes": MIN_POSITIVES,
        "confusion_counts": {key: counts[key] for key in
                             ("true_positive", "false_positive", "false_negative", "true_negative")},
        "precision_on_observed_rss": (round(counts["true_positive"] / predicted_positive, 4)
                                      if enough and predicted_positive else None),
        "sample_outcomes": examples,
        "caveats": [
            "Outcomes are subsequent rounded Google Trends RSS estimates, not independent popularity data.",
            "Only topics remaining observable in the limited RSS feed can receive labels.",
            "No historical prediction is reconstructed; snapshots are issued only on real collection runs.",
            "Missing future RSS readings are censored, never counted as negative outcomes.",
            "This is a heuristic baseline, not calibrated probability or virality prediction.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description="Record prospective RSS predictions or evaluate them")
    parser.add_argument("action", choices=("issue", "evaluate"))
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--output", type=Path, default=Path("reports/forecast-evaluation.json"))
    args = parser.parse_args()
    if args.action == "issue":
        report = issue_forecasts(args.data_dir)
    else:
        report = evaluate_forecasts(args.data_dir)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
