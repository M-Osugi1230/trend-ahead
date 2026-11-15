#!/usr/bin/env python3
"""Make a human-review outreach pack using only verified future event metadata.

This script NEVER publishes, mentions, messages, follows, or contacts anyone.
Review every official event detail before using generated outbound copy.
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode, urlsplit

JST = timezone(timedelta(hours=9))
SAFE_CATEGORY = {"スポーツ", "ゲーム", "映画・アニメ", "音楽・ライブ",
                 "季節・イベント", "テレビ・芸能", "テクノロジー", "配信・YouTuber"}


def iso_datetime(value):
    try:
        result = datetime.fromisoformat(value)
        return result if result.tzinfo is not None else None
    except (ValueError, TypeError):
        return None


def safe_https(value):
    try:
        url = urlsplit(value)
        return url.scheme == "https" and bool(url.netloc) and not any(ord(x) < 32 for x in value)
    except (TypeError, ValueError):
        return False


def get_candidates(path, now, days=14):
    if not path.exists():
        return []
    try:
        items = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(items, list):
        return []
    result = []
    for row in items:
        if not isinstance(row, dict):
            continue
        starts = iso_datetime(row.get("starts_at"))
        verified = iso_datetime(row.get("verified_at"))
        if starts is None or verified is None:
            continue
        if not (now <= starts <= now + timedelta(days=days)):
            continue
        if not (now - timedelta(days=30) <= verified <= now + timedelta(hours=1)):
            continue
        title = str(row.get("title", "")).strip()
        if not title or len(title) > 150:
            continue
        if row.get("category") not in SAFE_CATEGORY:
            continue
        if not safe_https(row.get("official_url")):
            continue
        result.append({"title": title, "category": str(row["category"]),
                       "starts_at": starts, "verified_at": verified,
                       "official_url": row["official_url"]})
    return sorted(result, key=lambda x: x["starts_at"])


def markdown_pack(events, now, site_url):
    """Include official sources, truthful timestamps, and opt-in posting guidance."""
    if not site_url.startswith("https://") or not safe_https(site_url):
        raise ValueError("site_url must be HTTPS")
    day = now.astimezone(JST).strftime("%Y-%m-%d")
    campaign = "preview_" + day.replace("-", "")
    site_link = site_url.rstrip("/") + "/week/?" + urlencode({
        "utm_source": "x", "utm_medium": "organic_social", "utm_campaign": campaign
    })
    community_link = site_url.rstrip("/") + "/?" + urlencode({
        "utm_source": "community", "utm_medium": "organic_social",
        "utm_campaign": campaign
    })
    header = [
        "# Trend Ahead — 発信準備パック（人間の確認が必要）",
        "",
        f"生成日時（JST）：{now.astimezone(JST).isoformat()}",
        "",
        "**自動投稿は禁止。利用前に日時・公式URL・内容を再確認すること。**",
        "サイトの収集・兆候検知は話題化の確率予測ではありません。",
        "各コミュニティの宣伝ルールに従い、同一文面の大量投稿・無差別DM・自動メンションは行わない。",
        "",
        "## 公式情報の確認リスト",
        "",
    ]
    if events:
        for event in events[:6]:
            when = event["starts_at"].astimezone(JST).strftime("%m/%d %H:%M JST")
            verify_at = event["verified_at"].astimezone(JST).strftime("%m/%d %H:%M JST")
            header.append(f"- [ ] {when}｜{event['title']}（{event['category']}）")
            header.append(f"  - 公式URL：{event['official_url']}")
            header.append(f"  - 最終確認：{verify_at}。**投稿直前に更新・中止情報を再確認**")
    else:
        header.append("- 直近14日以内の公式確認済み・安全な予定はありません。**無理に投稿しないこと。**")
    header.extend(["", "## X用：事実ベースの紹介文（手動投稿）", ""])
    if events:
        upcoming = events[:2]
        lines = ["「終わってから知った」を減らしたい。"]
        lines += [f"・{event['starts_at'].astimezone(JST).strftime('%m/%d %H:%M')} {event['title']}"
                  for event in upcoming]
        lines.extend(["公式発表済みの予定を一覧化しました（話題化の予測ではありません）。", site_link])
        post = "\n".join(lines)
        if len(post) > 280:
            lines = ["「あとから知った」を減らす、今後の注目予定リスト。", site_link]
            post = "\n".join(lines)
        header.extend(["> " + line for line in post.split("\n")])
        header.append("")
        header.append(f"文字数目安：{len(post)}字。Xに投稿する場合は日時・出典を再確認。")
    else:
        header.append("**投稿を保留**（共有可能な公式予定がありません）。")
    header.extend([
        "", "## コミュニティ向け：ベータ版の率直な紹介（投稿先ルール確認必須）",
        "",
        "> SNSで「話題になっていたのを後から知った」という経験から、",
        "公式に確認できた今後のイベントと、検索の急上昇をまとめる小さなサイトを作っています。",
        "今は予測AIではなく、先に気付くためのベータ版です。",
        "ご意見をいただける場であれば、見逃した話題・必要なジャンルを教えていただけると助かります。",
        f"サイト：{community_link}",
        "",
        "## 少人数のテスト協力依頼（知人や関係者向け、送信は手動）",
        "",
        "> 話題を後から知って悔しかった経験を減らせるか検証しています。",
        "今週サイトを1〜2回試して、面白い話題を見つけられたか、逆に何を見逃したかを",
        "率直に教えてもらえませんか？ 登録不要のベータ版です。",
        "",
        "## 評価ログ（実施後に記入）",
        "",
        "| 項目 | 記録 |",
        "|---|---|",
        "| 投稿先とルール確認 | 未実施 |",
        "| 実際の投稿URL | 未投稿 |",
        "| 公開日時 | 未投稿 |",
        "| ユーザーからの有効な反応 | 未計測 |",
        "| 誘導先のアクセス | 計測基盤未接続 |",
        "| 投稿後のサイト内回遊・原典遷移 | 計測基盤未接続 |",
        "",
    ])
    return "\n".join(header)


def main():
    parser = argparse.ArgumentParser(description="Create an offline, review-only launch pack")
    parser.add_argument("--events", type=Path, default=Path("data/upcoming.json"))
    parser.add_argument("--output", type=Path, default=Path("outreach/draft.md"))
    parser.add_argument("--site-url", default="https://m-osugi1230.github.io/trend-ahead")
    args = parser.parse_args()
    now = datetime.now(timezone.utc)
    events = get_candidates(args.events, now)
    content = markdown_pack(events, now, args.site_url)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(content + "\n", encoding="utf-8")
    print(json.dumps({"generated_at": now.isoformat(), "qualified_events": len(events),
                      "outbound_posts_sent": 0, "output": str(args.output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
