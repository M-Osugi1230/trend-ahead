#!/usr/bin/env python3
"""Review-only health check of month-precision announcements at official URLs.

Does NOT approve, edit, re-date, remove, or publish anything. A source-side
absence is not proof of delay or cancellation. Fetches via the existing
robots-aware, HTTPS same-host policy.
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

from event_discovery import fetch_official_page
from announced_windows import load_announced_windows

MONTH_TEXT = re.compile(r"(?<!\d)(20\d{2})\s*年\s*(0?[1-9]|1[0-2])\s*月")
MAX_CHECKS = 8


class VisibleMonthParser(HTMLParser):
    """Semantic visible text and image alt text, not scripts or style content."""
    IGNORED = {"script", "style", "template", "noscript", "svg"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.skip = []

    def handle_starttag(self, tag, attrs):
        if tag in self.IGNORED:
            self.skip.append(tag)
            return
        if self.skip:
            return
        if tag == "img":
            alt = dict(attrs).get("alt", "")
            if alt:
                self.parts.append(alt)

    def handle_endtag(self, tag):
        if self.skip and self.skip[-1] == tag:
            self.skip.pop()

    def handle_data(self, text):
        if not self.skip and text.strip():
            self.parts.append(text.strip())


def extract_months(html: str) -> list[str]:
    parser = VisibleMonthParser()
    parser.feed(html)
    text = " ".join(parser.parts)
    periods = {f"{year}-{int(month):02d}" for year, month in MONTH_TEXT.findall(text)}
    return sorted(periods)


def check(announcements, now, fetcher=fetch_official_page):
    checked = []
    for item in announcements[:MAX_CHECKS]:
        status = "unavailable"
        observed_months = []
        try:
            page = fetcher(item["official_url"])
            observed_months = extract_months(page)
            if item["period"] in observed_months:
                status = "month_still_visible"
            elif observed_months:
                status = "different_month_visible_review"
            else:
                status = "month_not_detected_review"
        except (ValueError, OSError, RuntimeError):
            # A failed fetch is not permission to bypass robots or conclude change.
            status = "official_page_unavailable"
        last_verified = datetime.fromisoformat(item["verified_at"])
        stale = (now - last_verified).total_seconds() > 30 * 86400
        if stale:
            status = "editor_verification_older_than_30_days"
        checked.append({
            "title": item["title"],
            "period": item["period"],
            "official_url": item["official_url"],
            "status": status,
            "observed_months": observed_months[:8],
            "verified_at": item["verified_at"],
            "verification_stale": stale,
        })
    concerns = [x for x in checked if x["status"] != "month_still_visible"]
    return {
        "generated_at": now.isoformat(),
        "status": "ok" if not concerns else "review_required",
        "checked": len(checked),
        "month_still_visible": len(checked) - len(concerns),
        "review_required": len(concerns),
        "items": checked,
        "publication": "manual_editorial_review_only",
        "note": "Absence, redirect, or read failure is not evidence of cancellation.",
    }


def markdown(report):
    lines = [
        "# 公式発表・放送月の確認",
        "",
        "元の公式ページに放送月が見えたかを確認する編集資料です。"
        "**中止・変更確定の判定ではなく、公開予定の自動変更もしません。**",
        "",
        f"確認数 {report['checked']}件／継続確認 {report['month_still_visible']}件"
        f"／確認が必要 {report['review_required']}件", "",
    ]
    for item in report["items"]:
        mark = "[x]" if item["status"] == "month_still_visible" else "[ ]"
        lines.extend([
            f"- {mark} {item['period']}｜{item['title']}",
            f"  - 公式：{item['official_url']}",
            f"  - 結果：{item['status']} ／検出月：{', '.join(item['observed_months']) or '未検出'}",
        ])
    lines.extend(["", "日付・時刻が正式発表された場合は、"
                  "一次情報を人が確認し、月単位の予告から正式な予定への切替を検討してください。", ""])
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Review-only checks for official month announcements")
    parser.add_argument("--catalog", type=Path, default=Path("data/announced_windows.json"))
    parser.add_argument("--output", type=Path, default=Path("reports/announcement-check.json"))
    parser.add_argument("--review-output", type=Path, default=Path("reports/announcement-check.md"))
    args = parser.parse_args()
    now = datetime.now(timezone.utc)
    catalog = load_announced_windows(args.catalog, now, include_stale=True)
    result = check(catalog, now)
    for path, value in (
        (args.output, json.dumps(result, ensure_ascii=False, indent=2) + "\n"),
        (args.review_output, markdown(result)),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")
    print(json.dumps({
        "status": result["status"],
        "checked": result["checked"],
        "month_still_visible": result["month_still_visible"],
        "review_required": result["review_required"],
    }, ensure_ascii=False))
    if result["review_required"]:
        print("::warning title=Announcement recheck needed::"
              "Official month announcement needs editorial review. "
              "See announcement-check.md artifact.")


if __name__ == "__main__":
    main()
