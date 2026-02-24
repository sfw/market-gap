"""Public no-key signal harvesting for market-gap-foundry."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from loom.tools.registry import Tool, ToolContext, ToolResult

USER_AGENT = "market-gap-foundry/0.1 (+no-auth-public-data)"

HN_BASE = "https://hacker-news.firebaseio.com/v0"
WORLD_BANK_BASE = "https://api.worldbank.org/v2"


def _fetch_json(url: str) -> Any:
    req = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(req, timeout=20) as resp:
            payload = resp.read()
    except HTTPError as exc:
        raise ValueError(f"HTTP {exc.code} for {url}") from exc
    except URLError as exc:
        raise ValueError(f"Network error for {url}: {exc.reason}") from exc
    try:
        return json.loads(payload.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON response from {url}") from exc


def _to_int(value: Any, field: str, *, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be an integer") from exc
    return max(minimum, min(parsed, maximum))


def _iso_utc(epoch_seconds: int) -> str:
    return datetime.fromtimestamp(epoch_seconds, tz=timezone.utc).isoformat()


def _hn_keyword_scan(
    keyword: str,
    story_pool: str = "top",
    max_items: int = 60,
    max_matches: int = 20,
) -> dict[str, Any]:
    keyword = keyword.strip().lower()
    if not keyword:
        raise ValueError("keyword must be non-empty")

    pool_map = {
        "top": "topstories",
        "new": "newstories",
        "best": "beststories",
    }
    selected_pool = pool_map.get(story_pool.strip().lower(), "topstories")

    ids_url = f"{HN_BASE}/{selected_pool}.json"
    ids_payload = _fetch_json(ids_url)
    if not isinstance(ids_payload, list):
        raise ValueError("Unexpected Hacker News story list response")

    matches: list[dict[str, Any]] = []
    for story_id in ids_payload[:max_items]:
        item_url = f"{HN_BASE}/item/{story_id}.json"
        item = _fetch_json(item_url)
        if not isinstance(item, dict):
            continue
        title = str(item.get("title", "") or "")
        text = str(item.get("text", "") or "")
        url = str(item.get("url", "") or f"https://news.ycombinator.com/item?id={story_id}")
        haystack = f"{title}\n{text}\n{url}".lower()
        if keyword not in haystack:
            continue

        ts = int(item.get("time", 0) or 0)
        matches.append(
            {
                "id": int(story_id),
                "title": title.strip(),
                "url": url,
                "score": int(item.get("score", 0) or 0),
                "time_utc": _iso_utc(ts) if ts > 0 else "",
                "unix_time": ts,
            }
        )
        if len(matches) >= max_matches:
            break

    matches.sort(key=lambda row: (row["score"], row["unix_time"]), reverse=True)

    if not matches:
        return {
            "keyword": keyword,
            "story_pool": selected_pool,
            "matched_count": 0,
            "mention_intensity_per_day": 0.0,
            "matches": [],
        }

    times = [row["unix_time"] for row in matches if row["unix_time"] > 0]
    if not times:
        intensity = float(len(matches))
    else:
        span_days = max((max(times) - min(times)) / 86400.0, 1.0)
        intensity = round(len(matches) / span_days, 3)

    return {
        "keyword": keyword,
        "story_pool": selected_pool,
        "matched_count": len(matches),
        "mention_intensity_per_day": intensity,
        "matches": matches,
    }


def _world_bank_indicator(
    country_code: str,
    indicator_code: str,
    years: int = 10,
) -> dict[str, Any]:
    country = country_code.strip().upper()
    indicator = indicator_code.strip()
    if not country:
        raise ValueError("country_code must be non-empty")
    if not indicator:
        raise ValueError("indicator_code must be non-empty")

    query = urlencode({"format": "json", "per_page": 120})
    url = f"{WORLD_BANK_BASE}/country/{country}/indicator/{indicator}?{query}"
    payload = _fetch_json(url)
    if not isinstance(payload, list) or len(payload) < 2:
        raise ValueError("Unexpected World Bank response structure")

    records = payload[1]
    if not isinstance(records, list):
        raise ValueError("Unexpected World Bank data payload")

    series: list[dict[str, Any]] = []
    indicator_name = ""
    for row in records:
        if not isinstance(row, dict):
            continue
        if not indicator_name and isinstance(row.get("indicator"), dict):
            indicator_name = str(row["indicator"].get("value", "")).strip()
        value = row.get("value")
        year_raw = row.get("date")
        if value is None:
            continue
        try:
            year = int(year_raw)
            numeric_value = float(value)
        except (TypeError, ValueError):
            continue
        series.append({"year": year, "value": numeric_value})

    if not series:
        raise ValueError("No non-null values returned for indicator")

    series.sort(key=lambda point: point["year"], reverse=True)
    series = series[:years]
    latest = series[0]
    oldest = series[-1]

    delta = latest["value"] - oldest["value"]
    pct_change = None
    if abs(oldest["value"]) > 1e-12:
        pct_change = round((delta / abs(oldest["value"])) * 100.0, 3)

    if pct_change is None:
        trend = "up" if delta > 0 else "down" if delta < 0 else "flat"
    elif abs(pct_change) < 1.0:
        trend = "flat"
    else:
        trend = "up" if pct_change > 0 else "down"

    return {
        "country_code": country,
        "indicator_code": indicator,
        "indicator_name": indicator_name or indicator,
        "latest_year": latest["year"],
        "latest_value": latest["value"],
        "oldest_year": oldest["year"],
        "oldest_value": oldest["value"],
        "delta": round(delta, 6),
        "pct_change": pct_change,
        "trend": trend,
        "series": series,
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

    async def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        operation = str(args.get("operation", "")).strip()
        op_args = args.get("args", {})
        if not isinstance(op_args, dict):
            return ToolResult.fail("args must be an object")

        try:
            if operation == "hn_keyword_scan":
                keyword = str(op_args.get("keyword", "")).strip()
                story_pool = str(op_args.get("story_pool", "top")).strip().lower()
                max_items = _to_int(op_args.get("max_items", 60), "max_items", minimum=1, maximum=500)
                max_matches = _to_int(
                    op_args.get("max_matches", 20),
                    "max_matches",
                    minimum=1,
                    maximum=100,
                )

                result = _hn_keyword_scan(
                    keyword=keyword,
                    story_pool=story_pool,
                    max_items=max_items,
                    max_matches=max_matches,
                )
                lines = [
                    f"HN keyword scan for '{result['keyword']}' ({result['story_pool']}):",
                    f"- Matches: {result['matched_count']}",
                    f"- Mention intensity/day: {result['mention_intensity_per_day']}",
                ]
                for item in result["matches"][:5]:
                    lines.append(f"- {item['title']} ({item['score']} votes)")
                if len(result["matches"]) > 5:
                    lines.append(f"... {len(result['matches']) - 5} more matches.")
                return ToolResult.ok("\n".join(lines), data=result)

            if operation == "world_bank_indicator":
                country_code = str(op_args.get("country_code", "WLD")).strip()
                indicator_code = str(op_args.get("indicator_code", "")).strip()
                years = _to_int(op_args.get("years", 10), "years", minimum=1, maximum=60)

                result = _world_bank_indicator(
                    country_code=country_code,
                    indicator_code=indicator_code,
                    years=years,
                )
                pct_text = "n/a" if result["pct_change"] is None else f"{result['pct_change']}%"
                output = (
                    f"World Bank {result['indicator_name']} ({result['country_code']}): "
                    f"{result['latest_value']} in {result['latest_year']} "
                    f"(trend: {result['trend']}, change: {pct_text})"
                )
                return ToolResult.ok(output, data=result)

            return ToolResult.fail(
                "Unknown operation. Use: hn_keyword_scan or world_bank_indicator."
            )
        except ValueError as exc:
            return ToolResult.fail(str(exc))
