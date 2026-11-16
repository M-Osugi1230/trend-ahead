#!/usr/bin/env python3
"""Review-only upcoming YouTube livestream discovery via the official Data API.

Requires a user-configured API key AND individually approved official channels.
No account data, OAuth, comments, publishing or notifications are involved.
No live stream enters the public catalog until an editor approves it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
API_BASE = "https://www.googleapis.com/youtube/v3/"
CHANNEL_ID_RE = re.compile(r"UC[A-Za-z0-9_-]{22}$")
VIDEO_ID_RE = re.compile(r"[A-Za-z0-9_-]{11}$")
MAX_CHANNELS = 10
MAX_PER_CHANNEL = 15
MAX_RESPONSE_BYTES = 500_000
CATEGORIES = {"配信・YouTuber", "ゲーム", "音楽・ライブ", "映画・アニメ"}



class RejectCrossHostRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # The URL contains a secret API key. Do not forward it to any host.
        raise RuntimeError("youtube_api_redirect_blocked")



def _api_get(url):
    """Bound API response size. Never include an API key in errors or logs."""
    request = urllib.request.Request(
        url, headers={"Accept": "application/json", "User-Agent": "TrendAheadScheduleResearch/1.0"}
    )
    try:
        opener = urllib.request.build_opener(RejectCrossHostRedirect)
        with opener.open(request, timeout=12) as response:
            if not response.geturl().startswith(API_BASE):
                raise ValueError("unexpected API redirect")
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"youtube_api_http_{exc.code}") from None
    except (OSError, ValueError, urllib.error.URLError):
        raise RuntimeError("youtube_api_unavailable") from None
    if len(raw) > MAX_RESPONSE_BYTES:
        raise RuntimeError("youtube_api_response_too_large")
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError):
        raise RuntimeError("youtube_api_invalid_json") from None


def _api_url(endpoint, params):
    # The key is only in the HTTPS API call. Do not print the resulting URL.
    return API_BASE + endpoint + "?" + urllib.parse.urlencode(params)


def validate_channels(entries):
    if not isinstance(entries, list):
        raise ValueError("channel configuration must be a list")
    validated = []
    ids = set()
    for item in entries[:MAX_CHANNELS]:
        if not isinstance(item, dict):
            continue
        ident = item.get("channel_id")
        if (not isinstance(ident, str) or not CHANNEL_ID_RE.fullmatch(ident)
                or ident in ids or item.get("official_confirmed") is not True
                or item.get("category") not in CATEGORIES):
            continue
        name = item.get("name")
        if not isinstance(name, str) or not 2 <= len(name.strip()) <= 100:
            continue
        validated.append({
            "channel_id": ident, "name": name.strip(), "category": item["category"],
        })
        ids.add(ident)
    return validated


def _upcoming_rows(search_response, video_response, channel, now, days=30):
    if not isinstance(search_response, dict) or not isinstance(video_response, dict):
        return []
    candidate_ids = set()
    for item in search_response.get("items", []):
        if not isinstance(item, dict):
            continue
        ident = item.get("id") or {}
        snippet = item.get("snippet") or {}
        vid = ident.get("videoId") if isinstance(ident, dict) else None
        if (isinstance(vid, str) and VIDEO_ID_RE.fullmatch(vid)
                and isinstance(snippet, dict)
                and snippet.get("channelId") == channel["channel_id"]
                and snippet.get("liveBroadcastContent") == "upcoming"):
            candidate_ids.add(vid)
    upcoming = []
    for item in video_response.get("items", []):
        if not isinstance(item, dict):
            continue
        vid = item.get("id")
        snippet = item.get("snippet") or {}
        details = item.get("liveStreamingDetails") or {}
        if (vid not in candidate_ids or not isinstance(snippet, dict)
                or not isinstance(details, dict)
                or snippet.get("channelId") != channel["channel_id"]
                or snippet.get("liveBroadcastContent") != "upcoming"
                or details.get("actualStartTime")):
            continue
        when = details.get("scheduledStartTime")
        try:
            dt = datetime.fromisoformat(when)
        except (ValueError, TypeError):
            continue
        if dt.tzinfo is None or not now < dt <= now + timedelta(days=days):
            continue
        title = snippet.get("title")
        if not isinstance(title, str):
            continue
        title = " ".join(title.split())
        if not 7 <= len(title) <= 150:
            continue
        event_id = hashlib.sha256(
            ("youtube|" + vid + "|" + dt.isoformat()).encode("utf-8")
        ).hexdigest()[:20]
        upcoming.append({
            "candidate_id": event_id, "title": title,
            "starts_at": dt.astimezone(JST).isoformat(),
            "category": channel["category"],
            "event_type": "official_scheduled_livestream",
            "official_url": "https://www.youtube.com/watch?v=" + vid,
            "source_id": "youtube_" + channel["channel_id"],
            "channel_name": channel["name"],
            "video_id": vid,
            "evidence": "YouTube API liveStreamingDetails.scheduledStartTime",
            "discovered_at": now.isoformat(),
            "status": "needs_review",
            "notes": "事前に公式として登録したチャンネルの配信予定。変更・中止・公開状態を編集者が確認",
        })
    return sorted(upcoming, key=lambda x: x["starts_at"])[:MAX_PER_CHANNEL]


def discover(channels, api_key, now, fetcher=_api_get):
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    validated = validate_channels(channels)
    if not api_key:
        return {
            "status": "not_configured", "generated_at": now.isoformat(),
            "channels_configured": len(validated), "channels_ok": 0,
            "candidates": [], "queued": 0, "errors": [],
            "publication": "manual_editorial_review_only",
        }
    if not validated:
        return {
            "status": "no_approved_channels", "generated_at": now.isoformat(),
            "channels_configured": 0, "channels_ok": 0,
            "candidates": [], "queued": 0, "errors": [],
            "publication": "manual_editorial_review_only",
        }
    all_rows, errors, ok_count, seen = [], [], 0, set()
    for channel in validated:
        try:
            search = fetcher(_api_url("search", {
                "part": "snippet", "type": "video", "eventType": "upcoming",
                "channelId": channel["channel_id"], "maxResults": 25,
                "order": "date", "key": api_key
            }))
            if not isinstance(search, dict) or not isinstance(search.get("items"), list):
                raise ValueError("invalid_search_response")
            ids = []
            for item in search["items"]:
                if isinstance(item, dict) and isinstance(item.get("id"), dict):
                    vid = item["id"].get("videoId")
                    if isinstance(vid, str) and VIDEO_ID_RE.fullmatch(vid):
                        ids.append(vid)
            ids = list(dict.fromkeys(ids))
            if ids:
                videos = fetcher(_api_url("videos", {
                    "part": "snippet,liveStreamingDetails", "id": ",".join(ids),
                    "key": api_key
                }))
            else:
                videos = {"items": []}
            if not isinstance(videos, dict) or not isinstance(videos.get("items"), list):
                raise ValueError("invalid_video_response")
            for row in _upcoming_rows(search, videos, channel, now):
                if row["candidate_id"] not in seen:
                    seen.add(row["candidate_id"])
                    all_rows.append(row)
            ok_count += 1
        except (ValueError, TypeError, RuntimeError):
            # Do not log exception bodies: some transports include API keys.
            errors.append({"channel_id": channel["channel_id"],
                           "error": "youtube_api_or_response_error"})
    all_rows.sort(key=lambda x: x["starts_at"])
    return {
        "status": "ok" if ok_count == len(validated) else ("partial" if ok_count else "unavailable"),
        "generated_at": now.isoformat(), "channels_configured": len(validated),
        "channels_ok": ok_count, "queued": len(all_rows),
        "candidates": all_rows, "errors": errors,
        "publication": "manual_editorial_review_only",
    }


def review_markdown(report):
    lines = [
        "# 公式YouTube配信・編集審査",
        "",
        "**自動公開・自動投稿は行いません。** 公式チャンネル、予定変更、配信URL、"
        "開始時刻を手動で確認した場合のみ公開予定の登録を検討してください。",
        f"実行結果：{report['status']}／候補{report['queued']}件",
        "",
    ]
    if report["status"] == "not_configured":
        lines.append("YouTube Data APIキーが未設定です。既存の収集・公開は正常継続します。")
    elif report["status"] == "no_approved_channels":
        lines.append("明示承認済みの公式チャンネルがありません。")
    for item in report["candidates"]:
        lines.extend([
            f"- [ ] {item['starts_at']}｜{item['title']}（{item['channel_name']}）",
            "  - 公式動画：" + item["official_url"],
            "  - API：liveStreamingDetails.scheduledStartTime／変更・中止の再確認が必要",
        ])
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description="Stage official scheduled streams for review only")
    parser.add_argument("--channels", type=Path, default=Path("data/youtube_channels.json"))
    parser.add_argument("--output", type=Path, default=Path("reports/youtube-candidates.json"))
    parser.add_argument("--review-output", type=Path, default=Path("reports/youtube-review.md"))
    args = parser.parse_args()
    config = json.loads(args.channels.read_text(encoding="utf-8"))
    report = discover(config, os.environ.get("YOUTUBE_API_KEY", ""), datetime.now(timezone.utc))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.review_output.write_text(review_markdown(report), encoding="utf-8")
    print(json.dumps({key: report[key] for key in
                      ("status", "channels_configured", "channels_ok", "queued")},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
