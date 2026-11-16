#!/usr/bin/env python3
"""Retrospective trend learning dossier from real archived RSS observations.

The 2025 Year in Search reference confirms that topics later became popular,
but contains NO historical pre-trend signals. Archive comparisons are evidence
of observations, not proof that Trend Ahead predicted anything.
No network requests, model training, ranking changes, or auto-publication.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backtest import evaluate, load_samples
from signal_engine import EVENT_ID

JST = timezone(timedelta(hours=9))
MIN_SPAN = timedelta(minutes=30)
GROWTH_RATIO = 1.25
DECLINE_RATIO = 0.8
MIN_DELTA = 100
MAX_DAYS = 30

LABELS = {
    "growth_observed": "増加を後から観測",
    "decline_observed": "減少を後から観測",
    "same_or_mixed_bucket": "変化が小さい・判定困難",
    "insufficient_observations": "観測不足",
}


def parse_time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value)
        return dt.astimezone(timezone.utc) if dt.tzinfo else None
    except ValueError:
        return None


def read_event_metadata(data_dir: Path, now: datetime) -> dict[str, dict]:
    """Attach titles only from recorded historical Google Trends events."""
    folder = data_dir / "events"
    if not folder.is_dir():
        return {}
    result = {}
    oldest = now - timedelta(days=MAX_DAYS + 1)
    for path in sorted(folder.glob("*.jsonl"), reverse=True)[:MAX_DAYS + 2]:
        try:
            with path.open(encoding="utf-8") as handle:
                for line in handle:
                    try:
                        item = json.loads(line)
                        if not isinstance(item, dict) or item.get("kind") != "trends":
                            continue
                        event_id = item.get("event_id")
                        when = parse_time(item.get("first_seen_at"))
                        title = item.get("title")
                        if (not isinstance(event_id, str) or not EVENT_ID.fullmatch(event_id)
                                or when is None or not oldest <= when <= now
                                or not isinstance(title, str) or not title.strip()):
                            continue
                        result[event_id] = {
                            "title": re.sub(r"\s+", " ", title.strip())[:150],
                            "first_seen_at": when.isoformat(),
                            "source": str(item.get("source", ""))[:60],
                        }
                    except (ValueError, TypeError, json.JSONDecodeError):
                        continue
        except OSError:
            continue
    return result


def read_reference(path: Path) -> dict:
    """Read manually verified YEAR-END outcomes without inventing onset times."""
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"reference file unavailable or invalid: {path}") from exc
    if (not isinstance(doc, dict) or doc.get("schema_version") != 1
            or not isinstance(doc.get("cases"), list)
            or not str(doc.get("source_url", "")).startswith("https://")):
        raise ValueError("invalid reference casebook")
    examples = []
    for item in doc["cases"]:
        if not isinstance(item, dict):
            raise ValueError("invalid reference case")
        rank, topic = item.get("rank"), item.get("topic")
        if (type(rank) is not int or rank <= 0 or not isinstance(topic, str)
                or not topic.strip() or item.get("ex_ante_evidence") is not None):
            raise ValueError("reference rankings cannot be used as ex-ante observations")
        examples.append({
            "topic": topic,
            "ranking_group": str(item.get("ranking_group", "")),
            "rank": rank,
            "pattern_hypothesis": str(item.get("pattern_hypothesis", "")),
            "ex_ante_evidence": None,
        })
    return {
        "year": doc.get("year"),
        "published_at": doc.get("published_at"),
        "source_url": doc["source_url"],
        "ranking_url": doc.get("ranking_url"),
        "cases": examples,
    }


def classify(series: list[tuple[datetime, int]]) -> str:
    """Classify a retrospective change only when TWO readings span >=30 min."""
    if len(series) < 2 or series[-1][0] - series[0][0] < MIN_SPAN:
        return "insufficient_observations"
    first, last = series[0][1], series[-1][1]
    if last - first >= MIN_DELTA and last >= first * GROWTH_RATIO:
        return "growth_observed"
    if first - last >= MIN_DELTA and last <= first * DECLINE_RATIO:
        return "decline_observed"
    return "same_or_mixed_bucket"


def build_report(data_dir: Path, reference: Path, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("now must be timezone aware")
    now = now.astimezone(timezone.utc)
    metadata = read_event_metadata(data_dir, now)
    samples = load_samples(data_dir, now)
    episodes = []
    seen_times = []
    for event_id, series in sorted(samples.items()):
        if not series:
            continue
        seen_times.extend(t for t, _ in series)
        meta = metadata.get(event_id, {})
        outcome = classify(series)
        first_at, first_value = series[0]
        last_at, last_value = series[-1]
        episodes.append({
            "event_id": event_id,
            "title": meta.get("title", "タイトル未取得"),
            "source": meta.get("source", "google_trends_jp"),
            "first_seen_at": meta.get("first_seen_at"),
            "first_observed_at": first_at.isoformat(),
            "last_observed_at": last_at.isoformat(),
            "observation_count": len(series),
            "span_minutes": round((last_at - first_at).total_seconds() / 60),
            "first_approx_lower_bound": first_value,
            "last_approx_lower_bound": last_value,
            "approx_lower_bound_delta": last_value - first_value,
            "result": outcome,
            "result_label": LABELS[outcome],
            "forecast_was_issued": False,
        })
    counts = Counter(c["result"] for c in episodes)
    positives = sorted((e for e in episodes if e["result"] == "growth_observed"),
                       key=lambda x: (-x["approx_lower_bound_delta"], x["event_id"]))
    declines = sorted((e for e in episodes if e["result"] == "decline_observed"),
                      key=lambda x: (x["approx_lower_bound_delta"], x["event_id"]))
    uncertain = sorted((e for e in episodes if e["result"] == "same_or_mixed_bucket"),
                       key=lambda x: (-x["observation_count"], x["event_id"]))
    reference_doc = read_reference(reference)
    backtest = evaluate(data_dir, now)
    newest = max(seen_times) if seen_times else None
    lag_hours = round((now - newest).total_seconds() / 3600, 2) if newest else None
    return {
        "generated_at": now.isoformat(),
        "purpose": "research_only_outcome_learning",
        "prediction_accuracy_claim": False,
        "historical_verified_examples": reference_doc,
        "rss_observations": {
            "source": "archived_google_trends_rss",
            "topic_count": len(episodes),
            "growth_observed": counts["growth_observed"],
            "decline_observed": counts["decline_observed"],
            "same_or_mixed_bucket": counts["same_or_mixed_bucket"],
            "insufficient_observations": counts["insufficient_observations"],
            "latest_observed_at": newest.isoformat() if newest else None,
            "collection_lag_hours": lag_hours,
            "collection_freshness": ("no_observations" if lag_hours is None
                                     else "stale" if lag_hours > 3 else "recent"),
            "examples": {
                "growth": positives[:5],
                "decline": declines[:5],
                "uncertain": uncertain[:5],
            },
        },
        "prospective_three_hour_evaluation": backtest,
        "interpretation": [
            "Year-in-Search rankings verify year-end search growth, not the time a trend began.",
            "Google Trends RSS appearance means a query was already trending on that source.",
            "RSS estimated traffic is a rounded lower bound; changes are not exact search counts.",
            "An event disappearing from the limited RSS feed is censored, NOT a failed forecast.",
            "Retrospective growth is NOT an earlier forecast, and the same RSS is not independent validation.",
            "Do not optimize live alert ranking until prospective time-separated evaluation beats a simple baseline.",
        ],
        "hypotheses_to_test": [
            "Scheduled releases and events can be listed early, but future demand must be evaluated separately.",
            "Popular products and lifestyle topics may benefit from repeated cross-source confirmation.",
            "Result-driven and unexpected events require fast detection rather than claims of advance prediction.",
        ],
    }


def md_escape(value: object) -> str:
    return re.sub(r"([\\`*_{}\[\]()#+.!|>~-])", r"\\\1",
                  str(value).replace("\n", " ").replace("\r", " "))


def render_markdown(report: dict) -> str:
    stats = report["rss_observations"]
    reference = report["historical_verified_examples"]
    bt = report["prospective_three_hour_evaluation"]
    lines = [
        "# Trend Ahead：実績からの学習監査（研究用）",
        "",
        "このレポートは**検証・編集用**です。予測的中率やSNS拡散実績を示しません。",
        "",
        f"生成時刻（UTC）：{report['generated_at']}",
        "",
        "## 過去に実際に急上昇した公式確認事例",
        "",
        f"確認元：{reference['source_url']}",
        f"公表日：{reference['published_at']}。年間ランキングは発生前の時系列データを含みません。",
        "",
        "|話題|Googleのランキング|順位|仮説（検証前）|",
        "|---|---|---:|---|",
    ]
    for case in reference["cases"]:
        lines.append("|" + "|".join([
            md_escape(case["topic"]), md_escape(case["ranking_group"]),
            str(case["rank"]), md_escape(case["pattern_hypothesis"])
        ]) + "|")
    lines.extend([
        "",
        "## サービス自身の観測履歴を事後照合",
        "",
        f"- 観測済み検索トピック：{stats['topic_count']}件",
        f"- 30分以上離れた観測で増加：{stats['growth_observed']}件",
        f"- 30分以上離れた観測で減少：{stats['decline_observed']}件",
        f"- 増減を特定できない：{stats['same_or_mixed_bucket']}件",
        f"- 観測期間不足：{stats['insufficient_observations']}件",
        f"- 最終収集：{stats['latest_observed_at'] or 'なし'}",
        f"- 最終収集からの経過：{stats['collection_lag_hours'] if stats['collection_lag_hours'] is not None else '不明'}時間（{stats['collection_freshness']}）",
        "",
    ])
    for key, heading in (("growth", "観測後に増加した例"),
                         ("decline", "観測後に減少した例"),
                         ("uncertain", "変化が小さい・不明な例")):
        lines.extend([f"### {heading}", ""])
        cases = stats["examples"][key]
        if not cases:
            lines.append("該当する十分な履歴がありません。")
        else:
            for case in cases:
                lines.append(
                    f"- {md_escape(case['title'])}："
                    f"{case['first_approx_lower_bound']}+ → {case['last_approx_lower_bound']}+"
                    f"（{case['span_minutes']}分・{case['observation_count']}回観測）"
                    "。**これは事後の観測であり、事前予測の的中ではありません。**"
                )
        lines.append("")
    lines.extend([
        "## 予測精度の検証ゲート",
        "",
        f"- 状態：{bt['status']}",
        f"- 評価可能な候補：{bt['labeled_cases']}件／観測日数：{bt['days_with_labels']}日",
        f"- 未来値欠測：{bt['missing_future_outcomes']}件（不正解として扱わない）",
        "- **データ不足の間は予測精度を公表せず、モデルの自動反映もしません。**",
        "",
        "## 次の検証仮説",
        "",
    ])
    lines.extend(f"- {md_escape(item)}" for item in report["hypotheses_to_test"])
    lines.extend(["", "## 解釈上の注意", ""])
    lines.extend(f"- {md_escape(item)}" for item in report["interpretation"])
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit realized trends without claiming forecasts")
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--reference", type=Path,
                        default=Path("data/verified_trend_cases_2025.json"))
    parser.add_argument("--json-output", type=Path,
                        default=Path("reports/trend-learning.json"))
    parser.add_argument("--markdown-output", type=Path,
                        default=Path("reports/trend-learning.md"))
    args = parser.parse_args()
    report = build_report(args.data_dir, args.reference)
    args.json_output.parent.mkdir(parents=True, exist_ok=True)
    args.markdown_output.parent.mkdir(parents=True, exist_ok=True)
    args.json_output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    args.markdown_output.write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({
        "status": report["prospective_three_hour_evaluation"]["status"],
        "topics_observed": report["rss_observations"]["topic_count"],
        "growth_observed": report["rss_observations"]["growth_observed"],
        "collection_freshness": report["rss_observations"]["collection_freshness"],
        "prediction_accuracy_claim": False,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
