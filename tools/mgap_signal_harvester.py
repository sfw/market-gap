"""Public no-key signal harvesting with safety and retry controls."""

from __future__ import annotations

import asyncio
import json
import re
import socket
import time
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from loom.tools.registry import Tool, ToolContext, ToolResult

USER_AGENT = "market-gap-foundry/0.2 (+no-auth-public-data)"

HN_BASE = "https://hacker-news.firebaseio.com/v0"
WORLD_BANK_BASE = "https://api.worldbank.org/v2"

ALLOWED_HOSTS = frozenset({"hacker-news.firebaseio.com", "api.worldbank.org"})

MAX_RESPONSE_BYTES = 1_000_000
REQUEST_TIMEOUT_SECONDS = 20
MAX_RETRIES = 3
RETRY_BASE_DELAY_SECONDS = 0.4
RETRYABLE_HTTP_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})

MAX_ITEMS_LIMIT = 300
MAX_MATCHES_LIMIT = 100
HN_CONCURRENCY = 8


def _trim_text(value: str, *, max_len: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= max_len:
        return text
    return text[: max_len - 1].rstrip() + "…"


def _to_int(value: Any, field: str, *, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be an integer") from exc
    if parsed < minimum:
        return minimum
    if parsed > maximum:
        return maximum
    return parsed


def _iso_utc(epoch_seconds: int) -> str:
    if epoch_seconds <= 0:
        return ""
    try:
        return datetime.fromtimestamp(epoch_seconds, tz=timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return ""


def _normalize_story_pool(raw: Any) -> str:
    value = str(raw or "top").strip().lower()
    mapping = {
        "top": "topstories",
        "new": "newstories",
        "best": "beststories",
    }
    if value not in mapping:
        allowed = ", ".join(sorted(mapping))
        raise ValueError(f"story_pool must be one of: {allowed}")
    return mapping[value]


def _validate_country_code(raw: Any) -> str:
    code = str(raw or "").strip().upper()
    if not re.fullmatch(r"[A-Z0-9]{2,3}", code):
        raise ValueError("country_code must be a 2-3 character ISO/World Bank code")
    return code


def _validate_indicator_code(raw: Any) -> str:
    code = str(raw or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{3,80}", code):
        raise ValueError(
            "indicator_code must contain 3-80 characters [A-Za-z0-9_.-]"
        )
    return code


def _validate_url(url: str) -> str:
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").lower()
    if scheme != "https":
        raise ValueError(f"Only https URLs are allowed: {url}")
    if host not in ALLOWED_HOSTS:
        raise ValueError(f"Host is not allowed for this tool: {host or '(none)'}")
    return url


def _fetch_json_sync(url: str) -> Any:
    safe_url = _validate_url(url)

    for attempt in range(MAX_RETRIES):
        req = Request(safe_url, headers={"User-Agent": USER_AGENT})
        try:
            with urlopen(req, timeout=REQUEST_TIMEOUT_SECONDS) as resp:
                payload = resp.read(MAX_RESPONSE_BYTES + 1)
                if len(payload) > MAX_RESPONSE_BYTES:
                    raise ValueError(
                        f"Response too large (> {MAX_RESPONSE_BYTES} bytes): {safe_url}"
                    )
        except HTTPError as exc:
            if (
                exc.code in RETRYABLE_HTTP_STATUS
                and attempt < MAX_RETRIES - 1
            ):
                time.sleep(RETRY_BASE_DELAY_SECONDS * (2 ** attempt))
                continue
            raise ValueError(f"HTTP {exc.code} for {safe_url}") from exc
        except (URLError, socket.timeout, TimeoutError) as exc:
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_BASE_DELAY_SECONDS * (2 ** attempt))
                continue
            reason = getattr(exc, "reason", str(exc))
            raise ValueError(f"Network error for {safe_url}: {reason}") from exc

        try:
            return json.loads(payload.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON response from {safe_url}") from exc

    raise ValueError(f"Request failed after {MAX_RETRIES} attempts: {safe_url}")


async def _fetch_json(url: str) -> Any:
    return await asyncio.to_thread(_fetch_json_sync, url)


async def _fetch_hn_item(story_id: int, keyword: str, sem: asyncio.Semaphore) -> dict[str, Any] | None:
    async with sem:
        item_url = f"{HN_BASE}/item/{story_id}.json"
        try:
            item = await _fetch_json(item_url)
        except ValueError:
            return None

    if not isinstance(item, dict):
        return None
    if bool(item.get("deleted")) or bool(item.get("dead")):
        return None

    title = str(item.get("title", "") or "")
    text = str(item.get("text", "") or "")
    url = str(item.get("url", "") or f"https://news.ycombinator.com/item?id={story_id}")
    haystack = f"{title}\n{text}\n{url}".lower()
    if keyword not in haystack:
        return None

    raw_time = item.get("time", 0)
    try:
        unix_time = int(raw_time or 0)
    except (TypeError, ValueError):
        unix_time = 0

    return {
        "id": int(story_id),
        "title": _trim_text(title.strip(), max_len=240),
        "url": url,
        "score": _to_int(item.get("score", 0), "score", minimum=0, maximum=1_000_000),
        "time_utc": _iso_utc(unix_time),
        "unix_time": unix_time,
    }


async def _hn_keyword_scan(
    keyword: str,
    story_pool: str = "top",
    max_items: int = 60,
    max_matches: int = 20,
) -> dict[str, Any]:
    normalized_keyword = str(keyword or "").strip().lower()
    if len(normalized_keyword) < 2:
        raise ValueError("keyword must be at least 2 characters")

    selected_pool = _normalize_story_pool(story_pool)
    max_items = _to_int(max_items, "max_items", minimum=1, maximum=MAX_ITEMS_LIMIT)
    max_matches = _to_int(max_matches, "max_matches", minimum=1, maximum=MAX_MATCHES_LIMIT)

    ids_url = f"{HN_BASE}/{selected_pool}.json"
    ids_payload = await _fetch_json(ids_url)
    if not isinstance(ids_payload, list):
        raise ValueError("Unexpected Hacker News story list response")

    story_ids: list[int] = []
    for raw_id in ids_payload:
        if len(story_ids) >= max_items:
            break
        try:
            story_ids.append(int(raw_id))
        except (TypeError, ValueError):
            continue

    sem = asyncio.Semaphore(HN_CONCURRENCY)
    tasks = [
        asyncio.create_task(_fetch_hn_item(story_id, normalized_keyword, sem))
        for story_id in story_ids
    ]
    items = await asyncio.gather(*tasks, return_exceptions=True)

    matches: list[dict[str, Any]] = []
    for item in items:
        if isinstance(item, Exception):
            continue
        if isinstance(item, dict):
            matches.append(item)

    matches.sort(
        key=lambda row: (
            int(row.get("score", 0)),
            int(row.get("unix_time", 0)),
            -int(row.get("id", 0)),
        ),
        reverse=True,
    )
    matches = matches[:max_matches]

    if not matches:
        return {
            "keyword": normalized_keyword,
            "story_pool": selected_pool,
            "matched_count": 0,
            "mention_intensity_per_day": 0.0,
            "observed_window_days": 0.0,
            "matches": [],
        }

    times = [int(row["unix_time"]) for row in matches if int(row["unix_time"]) > 0]
    if len(times) >= 2:
        observed_window_days = max((max(times) - min(times)) / 86400.0, 1.0)
    else:
        observed_window_days = 1.0

    mention_intensity = round(len(matches) / observed_window_days, 3)

    return {
        "keyword": normalized_keyword,
        "story_pool": selected_pool,
        "matched_count": len(matches),
        "mention_intensity_per_day": mention_intensity,
        "observed_window_days": round(observed_window_days, 3),
        "matches": matches,
    }


async def _world_bank_indicator(
    country_code: str,
    indicator_code: str,
    years: int = 10,
) -> dict[str, Any]:
    country = _validate_country_code(country_code)
    indicator = _validate_indicator_code(indicator_code)
    years = _to_int(years, "years", minimum=1, maximum=60)

    query = urlencode({"format": "json", "per_page": max(120, years * 3)})
    url = f"{WORLD_BANK_BASE}/country/{country}/indicator/{indicator}?{query}"
    payload = await _fetch_json(url)
    if not isinstance(payload, list) or len(payload) < 2:
        raise ValueError("Unexpected World Bank response structure")

    meta = payload[0]
    records = payload[1]
    if not isinstance(records, list):
        raise ValueError("Unexpected World Bank data payload")

    indicator_name = indicator
    series_by_year: dict[int, float] = {}
    for row in records:
        if not isinstance(row, dict):
            continue
        if indicator_name == indicator and isinstance(row.get("indicator"), dict):
            maybe_name = row["indicator"].get("value")
            if maybe_name:
                indicator_name = str(maybe_name).strip()

        value = row.get("value")
        year_raw = row.get("date")
        if value is None:
            continue
        try:
            year = int(year_raw)
            numeric_value = float(value)
        except (TypeError, ValueError):
            continue
        if year not in series_by_year:
            series_by_year[year] = numeric_value

    if not series_by_year:
        raise ValueError("No non-null values returned for indicator")

    series = [
        {"year": year, "value": value}
        for year, value in sorted(series_by_year.items(), reverse=True)
    ][:years]

    latest = series[0]
    oldest = series[-1]
    delta = latest["value"] - oldest["value"]

    pct_change: float | None = None
    if abs(oldest["value"]) > 1e-12:
        pct_change = round((delta / abs(oldest["value"])) * 100.0, 3)

    year_span = max(latest["year"] - oldest["year"], 0)
    cagr_pct: float | None = None
    if (
        year_span > 0
        and latest["value"] > 0
        and oldest["value"] > 0
    ):
        cagr_pct = round((((latest["value"] / oldest["value"]) ** (1 / year_span)) - 1) * 100.0, 3)

    if pct_change is None:
        trend = "up" if delta > 0 else "down" if delta < 0 else "flat"
    elif abs(pct_change) < 1.0:
        trend = "flat"
    else:
        trend = "up" if pct_change > 0 else "down"

    pages = None
    total_records = None
    if isinstance(meta, dict):
        pages = meta.get("pages")
        total_records = meta.get("total")

    return {
        "country_code": country,
        "indicator_code": indicator,
        "indicator_name": indicator_name,
        "latest_year": latest["year"],
        "latest_value": latest["value"],
        "oldest_year": oldest["year"],
        "oldest_value": oldest["value"],
        "delta": round(delta, 6),
        "pct_change": pct_change,
        "cagr_pct": cagr_pct,
        "trend": trend,
        "series": series,
        "source": {
            "provider": "world_bank",
            "url": url,
            "pages": pages,
            "total_records": total_records,
        },
    }


class MarketGapSignalHarvesterTool(Tool):
    """Collect no-auth public signal snapshots."""

    @property
    def name(self) -> str:
        return "mgap_signal_harvester"

    @property
    def description(self) -> str:
        return (
            "Collect public market signals with no credentials. "
            "Operations: hn_keyword_scan, world_bank_indicator."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "enum": ["hn_keyword_scan", "world_bank_indicator"],
                    "description": "Signal collection operation.",
                },
                "args": {
                    "type": "object",
                    "description": "Arguments for the selected operation.",
                },
            },
            "required": ["operation", "args"],
        }

    @property
    def timeout_seconds(self) -> int:
        return 90

    async def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        operation = str(args.get("operation", "")).strip()
        op_args = args.get("args", {})
        if not isinstance(op_args, dict):
            return ToolResult.fail("args must be an object")

        try:
            if operation == "hn_keyword_scan":
                keyword = str(op_args.get("keyword", "")).strip()
                story_pool = op_args.get("story_pool", "top")
                max_items = op_args.get("max_items", 60)
                max_matches = op_args.get("max_matches", 20)

                result = await _hn_keyword_scan(
                    keyword=keyword,
                    story_pool=str(story_pool),
                    max_items=int(max_items),
                    max_matches=int(max_matches),
                )
                lines = [
                    f"HN keyword scan for '{result['keyword']}' ({result['story_pool']}):",
                    f"- Matches: {result['matched_count']}",
                    f"- Mention intensity/day: {result['mention_intensity_per_day']}",
                    f"- Observed window days: {result['observed_window_days']}",
                ]
                for item in result["matches"][:5]:
                    lines.append(f"- {item['title']} ({item['score']} votes)")
                if len(result["matches"]) > 5:
                    lines.append(f"... {len(result['matches']) - 5} more matches.")
                return ToolResult.ok("\n".join(lines), data=result)

            if operation == "world_bank_indicator":
                country_code = op_args.get("country_code", "WLD")
                indicator_code = op_args.get("indicator_code", "")
                years = op_args.get("years", 10)

                result = await _world_bank_indicator(
                    country_code=str(country_code),
                    indicator_code=str(indicator_code),
                    years=int(years),
                )
                pct_text = "n/a" if result["pct_change"] is None else f"{result['pct_change']}%"
                cagr_text = "n/a" if result["cagr_pct"] is None else f"{result['cagr_pct']}%"
                output = (
                    f"World Bank {result['indicator_name']} ({result['country_code']}): "
                    f"{result['latest_value']} in {result['latest_year']} "
                    f"(trend: {result['trend']}, change: {pct_text}, CAGR: {cagr_text})"
                )
                return ToolResult.ok(output, data=result)

            return ToolResult.fail(
                "Unknown operation. Use: hn_keyword_scan or world_bank_indicator."
            )
        except (TypeError, ValueError) as exc:
            return ToolResult.fail(str(exc))
