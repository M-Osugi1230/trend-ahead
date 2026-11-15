#!/usr/bin/env python3
"""Explainable, observation-only trend signals from repeated Google Trends RSS readings.

This is NOT a probability forecast. RSS approximate traffic is bucketed/rounded;
changes can reflect sampling and sliding windows rather than organic momentum.
Only compare repeated observations for the same collector event_id.
"""
from __future__ import annotations

import math
import re
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
import json

EVENT_ID = re.compile(r"^[a-f0-9]{24}$")
TRAFFIC = re.compile(r"^([0-9][0-9,]*(?:\.[0-9]+)?)\s*(万|千|[KkMm])?\s*\+?$", re.UNICODE)


def parse_traffic(value):
    """Read an approximate traffic *lower bound*; None means unknown, not zero."""
    if not isinstance(value, str):
        return None
    match = TRAFFIC.fullmatch(value.strip())
    if not match:
        return None
    try:
        number = float(match.group(1).replace(",", ""))
        multiplier = {"万": 10000, "千": 1000, "k": 1000, "m": 1000000}.get(
            (match.group(2) or "").lower(), 1
        )
        result = round(number * multiplier)
        return result if 0 < result <= 1_000_000_000 else None
    except (ValueError, OverflowError):
        return None


def _load_observations(data_dir: Path, now: datetime):
    """Read recent, bounded collection history without relying on event page order."""
    folder = data_dir / "observations"
    if not folder.exists():
        return {}
    grouped = defaultdict(dict)
    oldest = now - timedelta(hours=24)
    for path in sorted(folder.glob("*.jsonl"), reverse=True)[:3]:
        with path.open(encoding="utf-8") as stream:
            for line in stream:
                try:
                    row = json.loads(line)
                    event_id = row.get("event_id")
                    if not isinstance(event_id, str) or not EVENT_ID.fullmatch(event_id):
                        continue
                    when = datetime.fromisoformat(row["observed_at"])
                    if when.tzinfo is None or not (oldest <= when <= now + timedelta(minutes=5)):
                        continue
                    estimate = parse_traffic(row.get("approx_traffic"))
                    if estimate is None:
                        continue
                    grouped[event_id][when.isoformat()] = (when, estimate, str(row["approx_traffic"]))
                except (ValueError, TypeError, KeyError, json.JSONDecodeError):
                    continue
    return {key: sorted(samples.values(), key=lambda x: x[0]) for key, samples in grouped.items()}


def detect_signals(data_dir: Path, rows: list[dict], now: datetime, limit: int = 3):
    """Return ranked evidence-backed signals, never projected peak times or odds.

    A signal needs two distinct valid readings at least three minutes apart,
    a fresh last reading (<=120 minutes), and traffic that has not decreased.
    Stronger 'increase observed' status requires >=100 estimated searches
    and >=25% change in the approximate lower bound, NOT verified acceleration.
    """
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    samples_by_id = _load_observations(data_dir, now)
    result = []
    for row in rows:
        if row.get("kind") != "trends":
            continue
        event_id = row.get("event_id")
        samples = samples_by_id.get(event_id, [])
        if len(samples) < 2:
            continue
        first, last = samples[0], samples[-1]
        elapsed = (last[0] - first[0]).total_seconds() / 60
        age = (now - last[0]).total_seconds() / 60
        if elapsed < 3 or age > 120 or age < -5 or last[1] < first[1]:
            continue
        increase = last[1] - first[1]
        ratio = last[1] / max(1, first[1])
        growing = increase >= 100 and ratio >= 1.25
        freshness = max(0, 20 - int(max(0, age) // 6))
        history = min(12, len(samples) * 3)
        growth_bonus = min(40, int(12 * math.log2(max(1, ratio)))) if growing else 0
        score = min(100, 25 + freshness + history + growth_bonus)
        label = "検索量の推定値が増加" if growing else "複数回観測"
        evidence = (f"RSS推定検索量 {first[2]} → {last[2]}"
                    f"／{len(samples)}回観測・約{round(elapsed)}分間")
        result.append({
            "event_id": event_id,
            "title": str(row.get("title", "")),
            "category": str(row.get("category", "全般")),
            "label": label,
            "score": score,
            "evidence": evidence,
            "growing": growing,
            "first_observed_at": first[0].isoformat(),
            "last_observed_at": last[0].isoformat(),
            "samples": len(samples),
            "estimate_first": first[1],
            "estimate_last": last[1],
        })
    result.sort(key=lambda item: (not item["growing"], -item["score"], item["last_observed_at"], item["event_id"]))
    return result[:max(0, limit)]
