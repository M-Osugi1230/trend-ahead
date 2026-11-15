#!/usr/bin/env python3
"""Trend Ahead Phase 1: collect RSS trend candidates into auditable JSONL."""
import argparse
import hashlib
import json
import re
import sys
import unicodedata
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
MAX_BYTES = 2_000_000
MAX_ITEMS = 30


def timestamp(dt):
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def normalize(value):
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value).strip()).casefold()


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


def find_text(node, name):
    for child in node:
        if child.tag.rsplit("}", 1)[-1] == name:
            return (child.text or "").strip()
    return ""


def parse_rss(xml_bytes, source):
    root = ET.fromstring(xml_bytes)
    if root.tag.rsplit("}", 1)[-1] != "rss":
        raise ValueError("not an RSS document")
    channel = root.find("channel")
    if channel is None:
        raise ValueError("RSS channel missing")
    parsed = []
    for item in channel.findall("item")[:MAX_ITEMS]:
        title, link = find_text(item, "title"), find_text(item, "link")
        if not title or not link.startswith(("https://", "http://")):
            continue
        group = "google_trends" if source["kind"] == "trends" else "google_news"
        identity = normalize(title) if group == "google_trends" else (find_text(item, "guid") or link)
        parsed.append({
            "key": digest(group + "|" + identity),
            "source": source["id"],
            "category": source["category"],
            "kind": source["kind"],
            "title": title,
            "url": link,
            "published_at": find_text(item, "pubDate") or None,
            "approx_traffic": find_text(item, "approx_traffic") or None,
        })
    return parsed


def fetch_feed(url):
    if not url.startswith("https://"):
        raise ValueError("only HTTPS source URLs are permitted")
    req = urllib.request.Request(url, headers={"User-Agent": "TrendAhead/0.1 (personal RSS collector)"})
    with urllib.request.urlopen(req, timeout=20) as response:
        payload = response.read(MAX_BYTES + 1)
    if len(payload) > MAX_BYTES:
        raise ValueError("RSS response exceeds limit")
    return payload


def load_json(path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def append_jsonl(path, records):
    if not records:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        for record in records:
            stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def collect(sources, data_dir, now=None, fetcher=fetch_feed):
    now = now or datetime.now(timezone.utc)
    at = timestamp(now)
    date_jst = now.astimezone(JST).strftime("%Y-%m-%d")
    month_jst = now.astimezone(JST).strftime("%Y-%m")
    state_path = data_dir / "state.json"
    state = load_json(state_path, {})
    state = {k: v for k, v in state.items()
             if datetime.fromisoformat(v["last_seen_at"]) >= now - timedelta(days=7)}
    events, observations, errors = [], [], []
    checked, feeds_ok = 0, 0
    for source in sources:
        try:
            items = parse_rss(fetcher(source["url"]), source)
            feeds_ok += 1
            for item in items:
                checked += 1
                key = item.pop("key")
                previous = state.get(key)
                if previous is None:
                    event_id = digest(key + "|" + at)
                    events.append({"event_id": event_id, "first_seen_at": at,
                                   "status": "detected", **item})
                    state[key] = {"event_id": event_id, "last_seen_at": at}
                else:
                    event_id = previous["event_id"]
                    previous["last_seen_at"] = at
                if item["kind"] == "trends":
                    observations.append({"event_id": event_id, "observed_at": at,
                                         "source": item["source"],
                                         "approx_traffic": item["approx_traffic"]})
        except Exception as exc:
            errors.append({"source": source.get("id", "unknown"),
                           "error": f"{type(exc).__name__}: {exc}"[:300]})
    if feeds_ok:
        append_jsonl(data_dir / "events" / f"{date_jst}.jsonl", events)
        append_jsonl(data_dir / "observations" / f"{date_jst}.jsonl", observations)
        save_json(state_path, state)
    run = {"ran_at": at, "feeds_total": len(sources), "feeds_ok": feeds_ok,
           "feeds_failed": len(errors), "items_seen": checked,
           "events_added": len(events), "observations_added": len(observations),
           "errors": errors}
    append_jsonl(data_dir / "runs" / f"{month_jst}.jsonl", [run])
    return run


def main():
    parser = argparse.ArgumentParser(description="Collect trend candidates from RSS")
    parser.add_argument("--sources", type=Path, default=Path("sources.json"))
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    args = parser.parse_args()
    sources = load_json(args.sources, [])
    if not sources:
        parser.error("no feeds configured")
    if len({s["id"] for s in sources}) != len(sources):
        parser.error("source ids must be unique")
    report = collect(sources, args.data_dir)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["feeds_ok"] else 1


if __name__ == "__main__":
    sys.exit(main())

