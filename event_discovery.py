#!/usr/bin/env python3
"""Find review-only future event candidates on allowlisted first-party calendars.

No event is automatically published. Candidates do not have verified_at; an
editor must confirm the organizer's exact date, status and any start time.
Only public HTTPS pages with a robots permission check are retrieved.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import unicodedata
import urllib.error
import urllib.request
import urllib.robotparser
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
MAX_RESPONSE_BYTES = 1_200_000
MAX_SOURCES = 16
USER_AGENT = "TrendAheadEditorialResearch/0.1 (+https://m-osugi1230.github.io/trend-ahead/)"
HEADING_TAGS = {"h2", "h3", "h4", "h5"}
SKIP_TAGS = {"script", "style", "noscript", "svg", "template"}
JP_DATE = re.compile(
    r"(?<!\d)(20\d{2})\s*[年./-]\s*(0?[1-9]|1[0-2])\s*[月./-]\s*"
    r"([0-2]?\d|3[01])\s*日?(?!\d)"
)
GENERIC_TITLE = re.compile(
    r"(イベント(情報|一覧|検索)?|展示一覧|LIST|企画展(&|＆)ストア|NEWS|お知らせ|"
    r"来場者|EVENTS|前の月|次の月|新着情報|一覧を見る)$",
    re.IGNORECASE,
)


def normalize(value: str) -> str:
    return re.sub(r"[\s　「」『』・!！?？:：‘’\"'。、,，._\-—]+", "",
                  unicodedata.normalize("NFKC", value).casefold())


def parse_date(value: str):
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            return date.fromisoformat(value)
        dt = datetime.fromisoformat(value)
        return dt.astimezone(JST).date() if dt.tzinfo else None
    except (ValueError, TypeError):
        return None


def allowed_https_url(value: str, expected_hostname: str) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = urlsplit(value)
        return (parsed.scheme == "https"
                and parsed.hostname == expected_hostname
                and parsed.username is None and parsed.password is None
                and parsed.port in (None, 443)
                and not any(ord(ch) < 32 for ch in value))
    except ValueError:
        return False


class SameHostRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, hostname):
        self.hostname = hostname

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not allowed_https_url(newurl, self.hostname):
            raise ValueError("cross-host or unsafe HTTP redirect blocked")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_official_page(url: str) -> str:
    """Be polite: robots.txt, one HTML GET, 1.2MB cap, fixed user agent."""
    parts = urlsplit(url)
    hostname = parts.hostname
    if not hostname or not allowed_https_url(url, hostname):
        raise ValueError("source URL is not an allowlisted HTTPS host")
    opener = urllib.request.build_opener(SameHostRedirect(hostname))
    robots_url = "https://" + hostname + "/robots.txt"
    try:
        with opener.open(urllib.request.Request(robots_url, headers={"User-Agent": USER_AGENT}),
                         timeout=12) as response:
            robots = response.read(150_000).decode("utf-8", errors="replace")
    except urllib.error.HTTPError as error:
        if error.code not in (404, 410):
            raise ValueError("robots policy unavailable: HTTP " + str(error.code)) from error
        robots = ""
    # Network failure on robots check is not permission to proceed.
    parser = urllib.robotparser.RobotFileParser()
    parser.parse(robots.splitlines())
    if not parser.can_fetch(USER_AGENT, url):
        raise ValueError("robots.txt disallows source retrieval")
    request = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/json",
    })
    with opener.open(request, timeout=18) as response:
        if not allowed_https_url(response.geturl(), hostname):
            raise ValueError("final source domain changed")
        content_type = response.headers.get("Content-Type", "").lower()
        if not ("text/html" in content_type or "application/xhtml+xml" in content_type
                or "application/json" in content_type or "text/plain" in content_type):
            raise ValueError("source was not an HTML page")
        content = response.read(MAX_RESPONSE_BYTES + 1)
    if len(content) > MAX_RESPONSE_BYTES:
        raise ValueError("page exceeded bounded fetch size")
    return content.decode("utf-8", errors="replace")


class HeadingTextExtractor(HTMLParser):
    """Retain human-visible heading/text order without executing scripts."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks = []
        self.skip_depth = 0
        self.current_heading = None
        self.head_parts = []
        self.raw_text = []

    def handle_starttag(self, tag, attrs):
        if tag in SKIP_TAGS:
            self.skip_depth += 1
        if self.skip_depth:
            return
        if tag in HEADING_TAGS and self.current_heading is None:
            self.current_heading = tag
            self.head_parts = []

    def handle_endtag(self, tag):
        if self.skip_depth:
            if tag in SKIP_TAGS:
                self.skip_depth -= 1
            return
        if self.current_heading == tag:
            title = " ".join(self.head_parts)
            title = re.sub(r"\s+", " ", title).strip()
            if title:
                self.blocks.append(("heading", title))
            self.current_heading = None
            self.head_parts = []

    def handle_data(self, data):
        if self.skip_depth:
            return
        stripped = re.sub(r"\s+", " ", data).strip()
        if not stripped:
            return
        if self.current_heading:
            self.head_parts.append(stripped)
        else:
            self.blocks.append(("text", stripped))


def candidate_from_heading(title, following, source, now):
    title = re.sub(r"\s+", " ", title).strip()
    min_title_length = 3 if source.get("kind") in ("movie_title_date", "movie_date_title") else 7
    if not min_title_length <= len(title) <= 150 or GENERIC_TITLE.fullmatch(title):
        return None
    # Capture the FIRST date after the heading; never infer time from venue hours.
    match = JP_DATE.search(following)
    if not match:
        return None
    try:
        starts = date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None
    today = now.astimezone(JST).date()
    if not today <= starts <= today + timedelta(days=90):
        return None
    source_url = source["url"]
    normalized_title = normalize(title)
    if len(normalized_title) < min_title_length:
        return None
    identity = f'{source["id"]}|{normalized_title}|{starts.isoformat()}'
    return {
        "candidate_id": hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20],
        "title": title,
        "starts_at": starts.isoformat(),
        "category": source["category"],
        "official_url": source_url,
        "source_id": source["id"],
        "evidence": "掲載日：" + match.group(0).strip(),
        "discovered_at": now.isoformat(),
        "status": "needs_review",
        "notes": "公式掲載の開始日候補。時間・中止・重複は人が確認",
    }


def extract_heading_dates(html: str, source: dict, now: datetime):
    parser = HeadingTextExtractor()
    parser.feed(html)
    blocks = parser.blocks
    found = []
    seen = set()
    for i, (kind, value) in enumerate(blocks):
        if kind != "heading":
            continue
        parts = []
        # Tight bounds avoid accidentally pairing a heading with a later event.
        for nxt_kind, nxt_value in blocks[i + 1:i + 55]:
            if nxt_kind == "heading":
                break
            parts.append(nxt_value)
            if sum(map(len, parts)) > 400:
                break
        candidate = candidate_from_heading(value, " ".join(parts), source, now)
        if candidate and candidate["candidate_id"] not in seen:
            seen.add(candidate["candidate_id"])
            found.append(candidate)
        if len(found) >= source.get("max_candidates", 25):
            break
    return found



# Venue calendars that show "YYYY.MM.DD ... YYYY.MM.DD Event title 対象..."
MAKUHARI_DATE_TITLE = re.compile(
    r"(?P<start>20\d{2}[./]\d{1,2}[./]\d{1,2})\s*"
    r"[（(][^)）]{1,4}[）)]\s*[〜~～\-ー]+\s*"
    r"(?P<end>20\d{2}[./]\d{1,2}[./]\d{1,2})\s*"
    r"[（(][^)）]{1,4}[）)]\s*(?P<title>.{6,150}?)\s*対象",
    re.UNICODE | re.DOTALL,
)


def extract_date_title_rows(html: str, source: dict, now: datetime):
    """Extract review-only candidates where the venue shows date before name."""
    parser = HeadingTextExtractor()
    parser.feed(html)
    readable = re.sub(r"\s+", " ", " ".join(text for _, text in parser.blocks))
    found = []
    seen = set()
    for match in MAKUHARI_DATE_TITLE.finditer(readable):
        title = re.sub(r"\s+", " ", match.group("title")).strip()
        if not title or len(title) > 150:
            continue
        candidate = candidate_from_heading(
            title, match.group("start"), source, now
        )
        if candidate and candidate["candidate_id"] not in seen:
            seen.add(candidate["candidate_id"])
            found.append(candidate)
        if len(found) >= source.get("max_candidates", 25):
            break
    return found


class ListItemExtractor(HTMLParser):
    """Collect visible list-item text, preserving per-item boundaries."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.items = []
        self.depth = 0
        self.skip_depth = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in SKIP_TAGS:
            self.skip_depth += 1
        if self.skip_depth:
            return
        if tag == "li":
            if self.depth == 0:
                self.parts = []
            self.depth += 1

    def handle_endtag(self, tag):
        if self.skip_depth:
            if tag in SKIP_TAGS:
                self.skip_depth -= 1
            return
        if tag == "li" and self.depth:
            self.depth -= 1
            if self.depth == 0:
                item = re.sub(r"\s+", " ", " ".join(self.parts)).strip()
                if item:
                    self.items.append(item)
                self.parts = []

    def handle_data(self, data):
        if self.depth and not self.skip_depth and data.strip():
            self.parts.append(data.strip())


def extract_movie_title_date(html: str, source: dict, now: datetime):
    """Official movie catalogs with movie title before its release date."""
    parser = ListItemExtractor()
    parser.feed(html)
    found = []
    seen = set()
    for item in parser.items:
        match = JP_DATE.search(item)
        if not match:
            continue
        title = item[:match.start()].strip()
        title = re.sub(r"^(公開予定作品|Coming Soon|映画作品ラインナップ)\s*", "", title)
        if not 3 <= len(title) <= 120 or "公開中" in title:
            continue
        candidate = candidate_from_heading(title, match.group(0), source, now)
        if candidate and candidate["candidate_id"] not in seen:
            candidate["event_type"] = "movie_release"
            seen.add(candidate["candidate_id"])
            found.append(candidate)
        if len(found) >= source.get("max_candidates", 25):
            break
    return found


def extract_movie_date_title(html: str, source: dict, now: datetime):
    """Official movie lists with date and 公開映画『title』 in one list item."""
    parser = ListItemExtractor()
    parser.feed(html)
    found = []
    seen = set()
    for item in parser.items:
        match = JP_DATE.search(item)
        if not match:
            continue
        tail = item[match.end():]
        title_match = re.search(
            r"公開\s*映画[『「](.{3,105}?)[』」]\s*(?:近日公開|公開中|$)", tail
        )
        if not title_match:
            continue
        title = title_match.group(1).strip()
        candidate = candidate_from_heading(title, match.group(0), source, now)
        if candidate and candidate["candidate_id"] not in seen:
            candidate["event_type"] = "movie_release"
            seen.add(candidate["candidate_id"])
            found.append(candidate)
        if len(found) >= source.get("max_candidates", 25):
            break
    return found


ARENA_LISTING = re.compile(
    r"^(?P<month>0?[1-9]|1[0-2])\.(?P<day>0?[1-9]|[12][0-9]|3[01])"
    r"\s+(?:MON|TUE|WED|THU|FRI|SAT|SUN)\s+(?P<title>.+)$",
    re.IGNORECASE,
)


def extract_arena_month_day(html: str, source: dict, now: datetime):
    """Official concert venue list, month/day with weekday and event title."""
    parser = ListItemExtractor()
    parser.feed(html)
    today = now.astimezone(JST).date()
    found = []
    seen = set()
    for item in parser.items:
        match = ARENA_LISTING.fullmatch(item.strip())
        if not match:
            continue
        title = match.group("title").strip()
        if title.upper() == "PRIVATE" or not 6 <= len(title) <= 150:
            continue
        year = today.year + int(int(match.group("month")) < today.month)
        try:
            starts = date(year, int(match.group("month")), int(match.group("day")))
        except ValueError:
            continue
        if not today <= starts <= today + timedelta(days=90):
            continue
        candidate = candidate_from_heading(
            title, starts.strftime("%Y-%m-%d"), source, now
        )
        if candidate and candidate["candidate_id"] not in seen:
            candidate["event_type"] = "concert"
            seen.add(candidate["candidate_id"])
            found.append(candidate)
        if len(found) >= source.get("max_candidates", 35):
            break
    return found



def extract_game_title_date(html: str, source: dict, now: datetime):
    """Nintendo-style visible product row with title, full date and publisher.

    Only full dates are accepted. JavaScript-only catalogs are reported as
    no parseable candidates, never as verified releases.
    """
    parser = ListItemExtractor()
    parser.feed(html)
    rows, seen = [], set()
    for item in parser.items:
        match = JP_DATE.search(item)
        if not match:
            continue
        title = re.sub(r"\s+", " ", item[:match.start()]).strip(" \t・-：")
        title = re.sub(
            r"^(?:今後発売予定のソフト|Nintendo Switch 2 ソフト)\s*", "", title
        )
        if not 6 <= len(title) <= 120 or "発売中のソフト" in title or "公開中" in title:
            continue
        candidate = candidate_from_heading(title + " 発売", match.group(0), source, now)
        if candidate and candidate["candidate_id"] not in seen:
            candidate["event_type"] = "game_release"
            seen.add(candidate["candidate_id"])
            rows.append(candidate)
        if len(rows) >= source.get("max_candidates", 25):
            break
    return rows


class TableRowTextExtractor(HTMLParser):
    """Read visible cells within each table row for official schedule metadata."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows = []
        self.row_depth = 0
        self.cell_depth = 0
        self.skip_depth = 0
        self.cells = []
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in SKIP_TAGS:
            self.skip_depth += 1
        if self.skip_depth:
            return
        if tag == "tr":
            if self.row_depth == 0:
                self.cells = []
            self.row_depth += 1
        if tag in ("th", "td") and self.row_depth:
            if self.cell_depth == 0:
                self.parts = []
            self.cell_depth += 1

    def handle_endtag(self, tag):
        if self.skip_depth:
            if tag in SKIP_TAGS:
                self.skip_depth -= 1
            return
        if tag in ("th", "td") and self.cell_depth:
            self.cell_depth -= 1
            if self.cell_depth == 0:
                self.cells.append(re.sub(r"\s+", " ", " ".join(self.parts)).strip())
        if tag == "tr" and self.row_depth:
            self.row_depth -= 1
            if self.row_depth == 0 and self.cells:
                self.rows.append(list(self.cells))

    def handle_data(self, text):
        if self.row_depth and self.cell_depth and not self.skip_depth and text.strip():
            self.parts.append(text.strip())


JRA_DATE = re.compile(r"(?<!\d)(?P<month>0?[1-9]|1[0-2])\s*月\s*"
                      r"(?P<day>0?[1-9]|[12][0-9]|3[01])\s*日")


def extract_jra_g1_dates(html: str, source: dict, now: datetime):
    """JRA official GI schedule: structured table first, visible text fallback.

    Both forms produce review-only date candidates; no start times are inferred.
    """
    parser = TableRowTextExtractor()
    parser.feed(html)
    rows = []
    for cells in parser.rows:
        if len(cells) < 2:
            continue
        date_match = JRA_DATE.search(cells[0])
        if not date_match:
            continue
        label = cells[1].strip()
        grade_match = re.match(r"^(?:J・)?G[ⅠI１](?![ⅠI１])\s*(.+)", label)
        if not grade_match:
            continue
        rows.append((date_match.group("month"), date_match.group("day"),
                     grade_match.group(1).strip()))

    # JRA has multiple HTML presentations. A page with div/list content can
    # contain the same public schedule even without table rows.
    if not rows:
        visible = HeadingTextExtractor()
        visible.feed(html)
        text = re.sub(r"\s+", " ", " ".join(x[1] for x in visible.blocks))
        fallback = re.compile(
            r"(?P<month>0?[1-9]|1[0-2])月(?P<day>0?[1-9]|[12]\d|3[01])日"
            r"(?:月曜|火曜|水曜|木曜|金曜|土曜|日曜)?\s*"
            r"(?:J・)?G[ⅠI１]\s*(?P<title>.{5,70}?)\s+"
            r"(?:東京|中山|京都|阪神|中京|新潟|小倉|福島|札幌|函館)(?:\s|$)"
        )
        rows = [(m.group("month"), m.group("day"), m.group("title").strip())
                for m in fallback.finditer(text)]

    result, seen = [], set()
    year = source.get("season_year")
    if not isinstance(year, int) or not 2020 <= year <= 2100:
        return result
    today = now.astimezone(JST).date()
    for month, day, name in rows:
        try:
            starts = date(year, int(month), int(day))
        except ValueError:
            continue
        if not today <= starts <= today + timedelta(days=90):
            continue
        name = re.sub(r"\s+", " ", name).strip()
        if not 5 <= len(name) <= 100:
            continue
        candidate = candidate_from_heading(name + "（GⅠ）", starts.isoformat(), source, now)
        if candidate and candidate["candidate_id"] not in seen:
            candidate["event_type"] = "horse_race"
            candidate["notes"] = "公式GⅠ日程の開催日候補。発走時刻・中止を再確認"
            seen.add(candidate["candidate_id"])
            result.append(candidate)
        if len(result) >= source.get("max_candidates", 25):
            break
    return result


K_ARENA_DATE = re.compile(
    r"^(?P<year>20\d{2})\.(?P<month>0?[1-9]|1[0-2])\."
    r"(?P<day>0?[1-9]|[12]\d|3[01])\.\s*"
    r"(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\.?\s*(?P<title>.+)$",
    re.IGNORECASE,
)
ZEPP_DATE = re.compile(
    r"(?<!\d)(?P<year>20\d{2})\s+(?P<month>0?[1-9]|1[0-2])\."
    r"(?P<day>0?[1-9]|[12]\d|3[01])\s+"
    r"(?:MON|TUE|WED|THU|FRI|SAT|SUN)\s+(?P<title>.+?)"
    r"(?=\s+\[OPEN\]|\s+\[START\])",
    re.IGNORECASE | re.DOTALL,
)


def _full_date_candidate(source, now, year, month, day, title):
    """No inferred time; a review candidate is all-day until independently checked."""
    title = re.sub(r"\s+", " ", title).strip()
    title = re.sub(r"^取扱公演\s*", "", title)
    title = re.sub(r"\s*(?:OPEN|START)\s+\d{1,2}:\d{2}.*$", "", title)
    if not 7 <= len(title) <= 150 or title.upper() == "PRIVATE":
        return None
    try:
        start = date(int(year), int(month), int(day))
    except ValueError:
        return None
    return candidate_from_heading(title, start.isoformat(), source, now)


def extract_k_arena_dates(html: str, source: dict, now: datetime):
    """K-Arena Yokohama listings: YYYY.MM.DD.Day. title OPEN..."""
    parser = ListItemExtractor()
    parser.feed(html)
    found, seen = [], set()
    for item in parser.items:
        match = K_ARENA_DATE.match(item)
        if not match:
            continue
        candidate = _full_date_candidate(source, now, match["year"],
                                         match["month"], match["day"], match["title"])
        if candidate and candidate["candidate_id"] not in seen:
            candidate["event_type"] = "concert"
            found.append(candidate)
            seen.add(candidate["candidate_id"])
        if len(found) >= source.get("max_candidates", 30):
            break
    return found


def extract_zepp_schedule(html: str, source: dict, now: datetime):
    """Zepp official daily schedule. Do not treat [OPEN] as [START]."""
    parser = HeadingTextExtractor()
    parser.feed(html)
    text = re.sub(r"\s+", " ", " ".join(value for _, value in parser.blocks))
    found, seen = [], set()
    for match in ZEPP_DATE.finditer(text):
        title = match["title"].strip()
        if len(title) > 150:
            continue  # long performer list needs human review of the source instead
        candidate = _full_date_candidate(source, now, match["year"],
                                         match["month"], match["day"], title)
        if candidate and candidate["candidate_id"] not in seen:
            candidate["event_type"] = "concert"
            found.append(candidate)
            seen.add(candidate["candidate_id"])
        if len(found) >= source.get("max_candidates", 35):
            break
    return found


def _movie_title_key(title: str) -> str:
    """Strip presentation words only for same-date film-release comparisons."""
    value = normalize(title)
    value = re.sub(r"^映画", "", value)
    value = re.sub(r"(劇場公開|全国公開|公開)$", "", value)
    return value



def extract_pokemon_goods_json(raw: str, source: dict, now: datetime):
    """Metadata-only candidate discovery from Pokémon's public goods calendar.

    The calendar's event_date_start may be an announcement/campaign date rather
    than a product availability date. It is NEVER auto-approved or published.
    """
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return []
    if not isinstance(parsed, dict) or not isinstance(parsed.get("results"), list):
        return []
    today = now.astimezone(JST).date()
    items, seen = [], set()
    for entry in parsed["results"]:
        if not isinstance(entry, dict):
            continue
        if entry.get("model") != "goods" and entry.get("term") != "goods":
            continue
        raw_date = entry.get("event_date_start")
        if not isinstance(raw_date, str):
            continue
        try:
            event_date = date.fromisoformat(raw_date)
        except ValueError:
            continue
        if not today <= event_date <= today + timedelta(days=90):
            continue
        title = entry.get("event_title") or entry.get("title")
        if not isinstance(title, str):
            continue
        title = re.sub(r"\s+", " ", title).strip()
        if not 7 <= len(title) <= 135:
            continue
        candidate = candidate_from_heading(title, raw_date, source, now)
        if not candidate or candidate["candidate_id"] in seen:
            continue
        candidate["event_type"] = "product_or_campaign_candidate"
        candidate["notes"] = ("ポケモン公式の告知カレンダー上の日付。"
                              "実際の発売日・応募締切・開始時刻とは限らないため個別公式情報で要確認")
        reference = entry.get("full_uniq")
        if isinstance(reference, str) and reference.startswith("https://"):
            try:
                parts = urlsplit(reference)
                if (parts.hostname and not parts.username and not parts.password
                        and parts.port in (None, 443) and not any(ord(ch) < 32 for ch in reference)):
                    candidate["review_reference_url"] = reference
            except ValueError:
                pass
        candidate["date_claim"] = "calendar_metadata_unverified"
        seen.add(candidate["candidate_id"])
        items.append(candidate)
        if len(items) >= source.get("max_candidates", 25):
            break
    return items



TCG_PRODUCT_TYPES = (
    "ブースターパック", "プレミアムブースター",
    "トライアルデッキ", "エクストラブースター",
)
TCG_PRODUCT_ENTRY = re.compile(
    r"(?P<title>(?:ブースターパック|プレミアムブースター|トライアルデッキ|"
    r"エクストラブースター)\s+.{3,105}?)"
    r"\s*発売日\s*[:：]?\s*(?P<released>20\d{2}年\s*\d{1,2}月\s*\d{1,2}日)",
)


def extract_tcg_product_list(html: str, source: dict, now: datetime):
    """Staged product launches from a first-party trading card catalog.

    Requires an explicit 発売日 next to the title, not a news posting timestamp.
    Avoids copying product descriptions or prices.
    """
    parser = ListItemExtractor()
    parser.feed(html)
    candidates = list(parser.items)
    # If the site uses non-list cards, allow a bounded textual fallback.
    if not candidates:
        visible = HeadingTextExtractor()
        visible.feed(html)
        candidates = [" ".join(value for _, value in visible.blocks)]
    found, seen = [], set()
    for row in candidates:
        for match in TCG_PRODUCT_ENTRY.finditer(row):
            title = re.sub(r"\s+", " ", match["title"]).strip()
            if not any(title.startswith(t) for t in TCG_PRODUCT_TYPES):
                continue
            candidate = candidate_from_heading(title+" 発売", match["released"], source, now)
            if candidate and candidate["candidate_id"] not in seen:
                candidate["event_type"] = "tcg_release"
                candidate["notes"] = "公式商品一覧の発売日候補。商品個別URL、販売変更・在庫は手動審査"
                found.append(candidate)
                seen.add(candidate["candidate_id"])
            if len(found) >= source.get("max_candidates", 25):
                break
        if len(found) >= source.get("max_candidates", 25):
            break
    return found


TCG_TYPES = ("プレミアムブースター", "トライアルデッキ",
             "エクストラブースター", "ブースターパック")


def _tcg_product_key(title):
    value = normalize(title)
    value = re.sub(r"^(ヴァイスシュヴァルツ|weissschwarz)", "", value)
    value = re.sub(r"(発売予定|発売)$", "", value)
    product_type = next((kind for kind in TCG_TYPES if normalize(kind) in value), None)
    for kind in TCG_TYPES:
        value = value.replace(normalize(kind), "", 1)
    return value, product_type



YUGIOH_PRODUCT_ITEM = re.compile(
    r"(?:基本パック|構築済みデッキ|コンセプトパック|スペシャルパック|"
    r"スペシャルセット|デュエリストアイテム)\s+"
    r"(?P<title>.{5,140}?)\s+"
    r"(?P<release>20\d{2}年\s*\d{1,2}月\s*\d{1,2}日)"
)


def extract_yugioh_products(html: str, source: dict, now: datetime):
    """Stage Konami's exact product dates; skip products with month-only dates.

    A date within the official product list is review evidence only. Especially
    limited editions might not be available to general retail customers.
    """
    parser = ListItemExtractor()
    parser.feed(html)
    found, seen = [], set()
    for entry in parser.items:
        match = YUGIOH_PRODUCT_ITEM.search(entry)
        if not match:
            continue
        title = re.sub(r"\s+", " ", match["title"]).strip()
        if not 5 <= len(title) <= 130:
            continue
        candidate = candidate_from_heading(title + " 発売", match["release"], source, now)
        if not candidate or candidate["candidate_id"] in seen:
            continue
        candidate["event_type"] = "tcg_release"
        candidate["notes"] = (
            "遊戯王OCG公式商品情報の発売日。限定販売・取扱店舗・"
            "延期・重複を個別確認してから掲載"
        )
        if "限定" in entry[:match.start()]:
            candidate["availability_hint"] = "limited_distribution_review_required"
        seen.add(candidate["candidate_id"])
        found.append(candidate)
        if len(found) >= source.get("max_candidates", 25):
            break
    return found



# The JFA official men's national-team schedule has a table with columns:
# 開催日 | 試合名 | スコア | 対戦チーム | 会場 | 公式記録.
# Its rows may use either "11/17(火)" or "2027/1/11(月)" dates.
JFA_MATCH_DATE = re.compile(
    r"^(?:(?P<year>20\d{2})/)?(?P<month>0?[1-9]|1[0-2])/"
    r"(?P<day>0?[1-9]|[12]\d|3[01])\s*[（(][^）)]{1,5}[）)]$"
)
JFA_NOT_PLAYING = re.compile(r"中止|延期|中断|開催取りやめ|不開催")
JFA_UNDECIDED = {"未定", "調整中", "TBD", "-", "－", "―"}
JFA_NO_RESULT = {"", "-", "－", "―", "未定"}


def _jfa_date(row, source):
    year = source.get("season_year")
    if type(year) is not int or not 2020 <= year <= 2100:
        return None
    if not isinstance(row, list) or len(row) < 5:
        return None
    match = JFA_MATCH_DATE.fullmatch(row[0].strip())
    if not match:
        return None  # Multi-day tournament windows are NOT single kickoffs.
    try:
        return date(int(match["year"] or year), int(match["month"]), int(match["day"]))
    except ValueError:
        return None


def _jfa_schedule_records(html, source, now):
    """Parse exact official match rows; keep excluded fixtures for audit."""
    if source.get("category") != "スポーツ":
        return [], []
    table = TableRowTextExtractor()
    table.feed(html)
    today = now.astimezone(JST).date()
    accepted, exclusions, seen = [], [], set()
    for cells in table.rows:
        played_on = _jfa_date(cells, source)
        if played_on is None or not today <= played_on <= today + timedelta(days=90):
            continue
        competition = re.sub(r"\s+", " ", cells[1]).strip()
        score = re.sub(r"\s+", " ", cells[2]).strip()
        opponent = re.sub(r"\s+", " ", cells[3]).strip()
        venue = re.sub(r"\s+", " ", cells[4]).strip()
        if not 5 <= len(competition) <= 130 or not 1 <= len(opponent) <= 70:
            continue
        identity = (played_on.isoformat(), normalize(competition), normalize(opponent))
        if identity in seen:
            continue
        seen.add(identity)
        if JFA_NOT_PLAYING.search(" ".join((competition, score, opponent))):
            exclusions.append({
                "source_id": source["id"], "official_url": source["url"],
                "starts_at": played_on.isoformat(), "competition": competition,
                "opponent": opponent, "status": "official_cancel_or_postpone_review",
                "reason": "JFAの中止・延期表記。自動公開も既存掲載の自動削除もしない",
            })
            continue
        if score not in JFA_NO_RESULT:
            continue  # A final/result row is not an upcoming fixture.
        if opponent in JFA_UNDECIDED:
            title = f"サッカー日本代表 対戦相手未定｜{competition}"
        else:
            clean_opponent = re.sub(r"代表$", "", opponent).strip()
            title = f"サッカー日本代表 vs {clean_opponent}｜{competition}"
        candidate = candidate_from_heading(title[:150], played_on.isoformat(), source, now)
        if candidate is None:
            continue
        candidate["event_type"] = "national_team_match"
        candidate["opponent"] = opponent
        candidate["venue_hint"] = venue[:140]
        candidate["date_claim"] = "jfa_schedule_date_only_jst"
        candidate["notes"] = (
            "JFA公式試合日程。日付は日本時間。開始時刻・放送・変更は"
            "JFAの個別試合ページを人が確認。対戦相手未定は推測しない"
        )
        candidate["evidence"] = f"JFA日程表：{cells[0]}／対戦：{opponent}／結果未掲載"
        accepted.append(candidate)
        if len(accepted) >= source.get("max_candidates", 25):
            break
    return accepted, exclusions




def _jfa_header_cells(html: str):
    parser = TableRowTextExtractor()
    parser.feed(html)
    return parser.rows

def extract_jfa_schedule_table(html: str, source: dict, now: datetime):
    return _jfa_schedule_records(html, source, now)[0]


def extract_jfa_schedule_exclusions(html: str, source: dict, now: datetime):
    return _jfa_schedule_records(html, source, now)[1]


def _jfa_opponent_from_title(title: str) -> str | None:
    if not isinstance(title, str):
        return None
    match = re.search(
        r"(?:サッカー)?日本代表\s*(?:vs\.?|対)\s*"
        r"(?P<opponent>[^\s｜|（(]+)", title, re.IGNORECASE
    )
    if not match:
        return None
    value = normalize(match["opponent"])
    return re.sub(r"代表$", "", value) or None

def match_curated(candidate: dict, curated: list):
    """Same-day duplicate checks: preserve uncertainty for renamed releases."""
    match_title = normalize(candidate["title"])
    candidate_is_movie = candidate.get("event_type") == "movie_release"
    candidate_is_tcg = candidate.get("event_type") == "tcg_release"
    film_key = _movie_title_key(candidate["title"]) if candidate_is_movie else ""
    product_key, product_type = (
        _tcg_product_key(candidate["title"]) if candidate_is_tcg else ("", None)
    )
    for row in curated:
        official_title = normalize(str(row.get("title", "")))
        official_date = parse_date(str(row.get("starts_at", "")))
        if not official_date or official_date.isoformat() != candidate["starts_at"]:
            continue
        if match_title == official_title:
            return "exact_curated"
        if (candidate.get("event_type") == "national_team_match"
                and row.get("category") == "スポーツ"):
            challenger = _jfa_opponent_from_title(candidate["title"])
            existing = _jfa_opponent_from_title(str(row.get("title", "")))
            if challenger and existing and challenger == existing:
                return "exact_curated"
            # The same official fixture may have changed from TBD to named
            # opposition; never silently re-register the replacement.
            if ("日本代表" in str(row.get("title", ""))
                    and (challenger is None or existing is None)):
                return "possible_curated"
        if (candidate_is_movie and row.get("category") == "映画・アニメ"
                and len(film_key) >= 3 and film_key == _movie_title_key(row.get("title", ""))):
            return "exact_curated"
        if candidate_is_tcg and row.get("category") == "グルメ・商品":
            curated_key, curated_type = _tcg_product_key(str(row.get("title", "")))
            if len(product_key) >= 5 and product_key == curated_key:
                if curated_type and product_type == curated_type:
                    return "exact_curated"
                # A release with unspecified package type may be a distinct SKU.
                return "possible_curated"
        if len(match_title) >= 3 and len(official_title) >= 5:
            if match_title in official_title or official_title in match_title:
                return "possible_curated"
    return None

def discover(sources: list, curated: list, now: datetime, fetcher=fetch_official_page):
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    candidates = []
    observations = []
    results = []
    official_exclusions = []
    seen = set()
    for source in sources[:MAX_SOURCES]:
        source_id = str(source.get("id", "invalid"))
        url = str(source.get("url", ""))
        hostname = urlsplit(url).hostname
        if (not hostname or not allowed_https_url(url, hostname)
                or source.get("kind") not in ("heading_dates", "date_then_title", "movie_title_date", "movie_date_title", "arena_month_day", "game_title_date", "jra_g1_table", "k_arena_dates", "zepp_schedule", "pokemon_goods_json", "tcg_product_list", "yugioh_products", "jfa_schedule_table")
                or source.get("category") is None
                or not re.fullmatch(r"[a-z0-9_-]{3,48}", source_id)):
            results.append({"source_id": source_id, "status": "invalid_config"})
            continue
        try:
            html = fetcher(url)
            extractor = {
                "heading_dates": extract_heading_dates,
                "date_then_title": extract_date_title_rows,
                "movie_title_date": extract_movie_title_date,
                "movie_date_title": extract_movie_date_title,
                "arena_month_day": extract_arena_month_day,
                "game_title_date": extract_game_title_date,
                "jra_g1_table": extract_jra_g1_dates,
                "k_arena_dates": extract_k_arena_dates,
                "zepp_schedule": extract_zepp_schedule,
                "pokemon_goods_json": extract_pokemon_goods_json,
                "tcg_product_list": extract_tcg_product_list,
                "yugioh_products": extract_yugioh_products,
                "jfa_schedule_table": extract_jfa_schedule_table,
            }[source["kind"]]
            rows = extractor(html, source, now)
            if source["kind"] == "jfa_schedule_table":
                official_exclusions.extend(extract_jfa_schedule_exclusions(html, source, now))
            added = 0
            for item in rows:
                if item["candidate_id"] in seen:
                    continue
                seen.add(item["candidate_id"])
                # Persist source sightings even when the item is already public.
                # Review candidates alone omit exact matches and cannot be used
                # to detect an official listing that disappeared or moved.
                observations.append({
                    "candidate_id": item["candidate_id"],
                    "source_id": item["source_id"],
                    "title": item["title"],
                    "starts_at": item["starts_at"],
                    "event_type": item.get("event_type", "unknown"),
                })
                item["match_curated"] = match_curated(item, curated)
                if item["match_curated"] == "exact_curated":
                    continue
                candidates.append(item)
                added += 1
            # A successful HTTP request with zero parsed dates is NOT a
            # successful content source. Surface the mismatch for review.
            if rows:
                status = "ok"
            elif (source["kind"] == "jfa_schedule_table"
                  and any("開催日" in cells and "対戦チーム" in cells
                          for cells in _jfa_header_cells(html))):
                status = "ok_no_upcoming_in_horizon"
            else:
                status = "no_parseable_items"
            results.append({"source_id": source_id, "status": status,
                            "extracted": len(rows), "queued": added})
        except (ValueError, OSError, urllib.error.URLError) as error:
            results.append({"source_id": source_id, "status": "fetch_or_parse_error",
                            "reason": str(error)[:180]})
    candidates.sort(key=lambda c: (c["starts_at"], c["source_id"], c["title"]))
    observations.sort(key=lambda c: (c["source_id"], c["starts_at"], c["title"]))
    ok = sum(item["status"] in ("ok", "ok_no_upcoming_in_horizon") for item in results)
    return {
        "generated_at": now.isoformat(),
        "status": "ok" if ok == len(results) else ("partial" if ok else "unavailable"),
        "sources_total": len(results), "sources_ok": ok,
        "queued": len(candidates),
        "candidates": candidates,
        "observed": observations,
        "official_exclusions": official_exclusions,
        "sources": results,
        "publication": "manual_editorial_review_only",
    }


def review_markdown(report):
    """Put near-term genuinely new leads first, possible duplicates last."""
    try:
        now = datetime.fromisoformat(report["generated_at"]).astimezone(JST).date()
    except (ValueError, TypeError, KeyError):
        now = datetime.now(JST).date()
    candidates = report.get("candidates", [])
    urgent, later, duplicates = [], [], []
    for item in candidates:
        if item.get("match_curated") == "possible_curated":
            duplicates.append(item)
            continue
        try:
            due = date.fromisoformat(item["starts_at"])
        except (TypeError, ValueError, KeyError):
            later.append(item)
            continue
        if due <= now + timedelta(days=14):
            urgent.append(item)
        else:
            later.append(item)
    urgent.sort(key=lambda x: (x["starts_at"], x["source_id"]))
    later.sort(key=lambda x: (x["starts_at"], x["source_id"]))
    duplicates.sort(key=lambda x: (x["starts_at"], x["source_id"]))
    lines = [
        "# 公式イベント候補・編集審査キュー", "",
        "これらの候補は**未確認・未公開**。公式ページを開いて名称・日付・時刻・"
        "中止・主催者・ジャンル・重複をチェックするまで公開データに追加しない。",
        "",
        f"生成：{report['generated_at']} ／ 候補 {report['queued']}件"
        f" ／ 情報源成功 {report['sources_ok']}/{report['sources_total']}",
        f"優先審査：{len(urgent)}件（14日以内）"
        f" ／ 後日審査：{len(later)}件"
        f" ／ 重複疑い：{len(duplicates)}件", "",
    ]
    def section(title, items):
        lines.extend(["## " + title, ""])
        if not items:
            lines.extend(["候補なし", ""])
            return
        for item in items:
            lines.extend([
                f"- [ ] {item['starts_at']}｜{item['title']}（{item['category']}）",
                f"  - 出典：{item['official_url']}",
                f"  - 抽出根拠：{item['evidence']} ／ ID: {item['candidate_id']}",
                f"  - 重複判定：{item.get('match_curated') or '未検出'}",
            ])
            if item.get("review_reference_url"):
                lines.append("  - 追加の参照候補（公式性と日付は要確認）："
                             + item["review_reference_url"])
            if item.get("date_claim") == "calendar_metadata_unverified":
                lines.append("  - **注意：公式カレンダー上の日付で、実際の発売日とは限りません**")
            lines.append("  - 編集者は主催者・メーカーの個別ページを開き確認")
        lines.append("")
    section("今後14日間・優先確認", urgent)
    section("14日より先・確認待ち", later)
    section("既存掲載との重複疑い・再登録しない", duplicates)
    if report.get("official_exclusions"):
        lines.extend(["## JFA公式に中止・延期表示がある日程（要確認）", ""])
        for row in report["official_exclusions"]:
            lines.extend([
                f"- [ ] {row['starts_at']}｜{row['competition']}｜対戦：{row['opponent']}",
                f"  - 出典：{row['official_url']}",
                "  - 新規予定候補から除外。既存公開の有無と状況を編集者が確認",
            ])
        lines.append("")
    lines.extend(["## 情報源の状態", ""])
    for row in report["sources"]:
        lines.append(f"- {row['source_id']}: {row['status']}"
                     f"／抽出 {row.get('extracted', '?')}件"
                     f"／追加 {row.get('queued', '?')}件"
                     + (f"／{row['reason']}" if row.get("reason") else ""))
    lines.append("")
    return "\n".join(lines)

def main():
    parser = argparse.ArgumentParser(description="Generate editorial-only event discovery queue")
    parser.add_argument("--sources", type=Path, default=Path("data/event_sources.json"))
    parser.add_argument("--curated", type=Path, default=Path("data/upcoming.json"))
    parser.add_argument("--json-output", type=Path, default=Path("reports/event-candidates.json"))
    parser.add_argument("--review-output", type=Path, default=Path("reports/event-review.md"))
    args = parser.parse_args()
    sources = json.loads(args.sources.read_text(encoding="utf-8"))
    curated = json.loads(args.curated.read_text(encoding="utf-8"))
    if not isinstance(sources, list) or not isinstance(curated, list):
        raise ValueError("sources and curated must be JSON arrays")
    report = discover(sources, curated, datetime.now(timezone.utc))
    args.json_output.parent.mkdir(parents=True, exist_ok=True)
    args.json_output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                                encoding="utf-8")
    args.review_output.parent.mkdir(parents=True, exist_ok=True)
    args.review_output.write_text(review_markdown(report), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("status", "sources_ok", "sources_total", "queued")},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
