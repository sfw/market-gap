"""ERRC helper with stronger validation for market-gap-foundry."""

from __future__ import annotations

import re
from typing import Any

from loom.tools.registry import Tool, ToolContext, ToolResult

ACTION_KEYS = ("eliminate", "reduce", "raise", "create")
MAX_ITEMS = 120


def _clean_text(value: Any, field: str, *, required: bool = False, max_len: int = 180) -> str:
    text = " ".join(str(value or "").strip().split())
    if required and not text:
        raise ValueError(f"{field} must be non-empty")
    if len(text) > max_len:
        text = text[: max_len - 1].rstrip() + "…"
    return text


def _as_score(value: Any, field: str, *, minimum: int = 1, maximum: int = 5) -> int:
    try:
        score = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be an integer in [{minimum}, {maximum}]") from exc
    if score < minimum or score > maximum:
        raise ValueError(f"{field} must be in [{minimum}, {maximum}]")
    return score


def _tokenize(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        normalized = _clean_text(item, "item", required=False, max_len=180)
        key = normalized.lower()
        if not normalized or key in seen:
            continue
        seen.add(key)
        out.append(normalized)
    return out


def _normalize_features(raw_features: list[Any]) -> list[dict[str, Any]]:
    if not isinstance(raw_features, list) or not raw_features:
        raise ValueError("incumbent_features must be a non-empty list")

    normalized: list[dict[str, Any]] = []
    for idx, raw in enumerate(raw_features[:MAX_ITEMS]):
        if isinstance(raw, str):
            name = _clean_text(raw, f"incumbent_features[{idx}]", required=True)
            normalized.append(
                {
                    "name": name,
                    "customer_value": 3,
                    "delivery_cost": 3,
                    "accessibility": 3,
                }
            )
            continue
        if not isinstance(raw, dict):
            continue

        name = _clean_text(raw.get("name"), f"incumbent_features[{idx}].name", required=True)
        normalized.append(
            {
                "name": name,
                "customer_value": _as_score(
                    raw.get("customer_value", 3),
                    f"{name}.customer_value",
                ),
                "delivery_cost": _as_score(
                    raw.get("delivery_cost", 3),
                    f"{name}.delivery_cost",
                ),
                "accessibility": _as_score(
                    raw.get("accessibility", 3),
                    f"{name}.accessibility",
                ),
            }
        )

    if not normalized:
        raise ValueError("incumbent_features did not contain any usable entries")
    normalized.sort(key=lambda row: row["name"].lower())
    return normalized


def _normalize_needs(raw_needs: list[Any]) -> list[str]:
    if not isinstance(raw_needs, list) or not raw_needs:
        raise ValueError("underserved_needs must be a non-empty list")
    cleaned: list[str] = []
    for idx, raw in enumerate(raw_needs[:MAX_ITEMS]):
        text = _clean_text(raw, f"underserved_needs[{idx}]", required=False)
        if text:
            cleaned.append(text)
    cleaned = _dedupe(cleaned)
    if not cleaned:
        raise ValueError("underserved_needs did not contain any usable entries")
    return cleaned


def _remove_cross_action_duplicates(
    eliminate: list[str],
    reduce: list[str],
    raise_: list[str],
) -> tuple[list[str], list[str], list[str]]:
    eliminated = {item.lower() for item in eliminate}
    reduce_filtered = [item for item in reduce if item.lower() not in eliminated]
    reduced = {item.lower() for item in reduce_filtered} | eliminated
    raise_filtered = [item for item in raise_ if item.lower() not in reduced]
    return eliminate, reduce_filtered, raise_filtered


def _propose_errc(
    gap_statement: str,
    incumbent_features: list[Any],
    underserved_needs: list[Any],
) -> dict[str, Any]:
    gap = _clean_text(gap_statement, "gap_statement", required=True, max_len=320)
    features = _normalize_features(incumbent_features)
    needs = _normalize_needs(underserved_needs)

    eliminate: list[str] = []
    reduce: list[str] = []
    raise_: list[str] = []
    create: list[str] = []

    incumbent_tokens: set[str] = set()
    for feature in features:
        name = feature["name"]
        incumbent_tokens.update(_tokenize(name))
        customer_value = int(feature["customer_value"])
        delivery_cost = int(feature["delivery_cost"])
        accessibility = int(feature["accessibility"])

        if customer_value <= 2 and delivery_cost >= 4:
            eliminate.append(name)
        elif customer_value <= 3 and delivery_cost >= 4:
            reduce.append(name)

        if customer_value >= 3 and accessibility <= 2:
            raise_.append(name)

    for need in needs:
        overlap = bool(_tokenize(need) & incumbent_tokens)
        if not overlap:
            create.append(need)

    if not create:
        create.append(f"Offer purpose-built to solve: {_clean_text(needs[0], 'need')}")

    eliminate = _dedupe(eliminate)
    reduce = _dedupe(reduce)
    raise_ = _dedupe(raise_)
    create = _dedupe(create)
    eliminate, reduce, raise_ = _remove_cross_action_duplicates(eliminate, reduce, raise_)

    rows = (
        [{"action": "eliminate", "item": item} for item in eliminate]
        + [{"action": "reduce", "item": item} for item in reduce]
        + [{"action": "raise", "item": item} for item in raise_]
        + [{"action": "create", "item": item} for item in create]
    )

    return {
        "gap_statement": gap,
        "incumbent_features_considered": [feature["name"] for feature in features],
        "eliminate": eliminate,
        "reduce": reduce,
        "raise": raise_,
        "create": create,
        "grid_rows": rows,
        "coverage": {key: len([row for row in rows if row["action"] == key]) for key in ACTION_KEYS},
        "rationale": [
            "Eliminate low-value/high-cost incumbency baggage.",
            "Reduce cost and complexity that are not core to customer outcomes.",
            "Raise accessibility factors that currently block adoption.",
            "Create new value factors tied directly to underserved jobs.",
        ],
    }


def _curve_to_map(curve: list[Any], label: str) -> dict[str, int]:
    if not isinstance(curve, list) or not curve:
        raise ValueError(f"{label} must be a non-empty list")

    out: dict[str, int] = {}
    for idx, raw in enumerate(curve[:MAX_ITEMS]):
        if isinstance(raw, str):
            name = _clean_text(raw, f"{label}[{idx}]", required=True)
            score = 3
        elif isinstance(raw, dict):
            name = _clean_text(raw.get("name"), f"{label}[{idx}].name", required=True)
            score = _as_score(raw.get("score", 3), f"{label}:{name}.score", minimum=0, maximum=5)
        else:
            continue
        out[name] = score

    if not out:
        raise ValueError(f"{label} did not contain usable entries")
    return out


def _value_curve_shift(
    current_curve: list[Any],
    proposed_curve: list[Any],
) -> dict[str, Any]:
    current = _curve_to_map(current_curve, "current_curve")
    proposed = _curve_to_map(proposed_curve, "proposed_curve")

    factor_names = sorted(set(current.keys()) | set(proposed.keys()))
    rows: list[dict[str, Any]] = []
    for name in factor_names:
        current_score = current.get(name, 0)
        proposed_score = proposed.get(name, 0)
        delta = proposed_score - current_score
        if delta > 0:
            direction = "up"
        elif delta < 0:
            direction = "down"
        else:
            direction = "flat"
        rows.append(
            {
                "factor": name,
                "current_score": current_score,
                "proposed_score": proposed_score,
                "delta": delta,
                "direction": direction,
            }
        )

    rows.sort(
        key=lambda row: (abs(int(row["delta"])), str(row["factor"]).lower()),
        reverse=True,
    )
    high_impact = [row for row in rows if abs(int(row["delta"])) >= 2]
    return {
        "rows": rows,
        "high_impact_factors": high_impact,
        "summary": {
            "factor_count": len(rows),
            "increased": len([row for row in rows if row["delta"] > 0]),
            "decreased": len([row for row in rows if row["delta"] < 0]),
            "unchanged": len([row for row in rows if row["delta"] == 0]),
        },
    }


def _validate_errc_grid(grid_rows: list[Any]) -> dict[str, Any]:
    if not isinstance(grid_rows, list) or not grid_rows:
        raise ValueError("grid_rows must be a non-empty list")

    seen: dict[str, str] = {}
    duplicates: list[dict[str, str]] = []
    counts = {action: 0 for action in ACTION_KEYS}

    for idx, raw in enumerate(grid_rows):
        if not isinstance(raw, dict):
            raise ValueError(f"grid_rows[{idx}] must be an object")
        action = str(raw.get("action", "")).strip().lower()
        item = _clean_text(raw.get("item"), f"grid_rows[{idx}].item", required=True)
        if action not in ACTION_KEYS:
            raise ValueError(f"grid_rows[{idx}].action must be one of: {', '.join(ACTION_KEYS)}")

        counts[action] += 1
        key = item.lower()
        previous_action = seen.get(key)
        if previous_action and previous_action != action:
            duplicates.append(
                {"item": item, "action_a": previous_action, "action_b": action}
            )
        seen[key] = action

    return {
        "valid": len(duplicates) == 0,
        "action_counts": counts,
        "cross_action_duplicates": duplicates,
    }


class MarketGapERRCTool(Tool):
    """Generate and validate ERRC grids and value-curve shifts."""

    @property
    def name(self) -> str:
        return "mgap_errc_builder"

    @property
    def description(self) -> str:
        return (
            "Build ERRC action grids, validate ERRC rows, and analyze "
            "value-curve shift deltas for market-creation design."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "enum": ["propose_errc", "validate_errc", "value_curve_shift"],
                    "description": "ERRC generation, validation, or value-curve delta analysis.",
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
            if operation == "propose_errc":
                grid = _propose_errc(
                    gap_statement=op_args.get("gap_statement", ""),
                    incumbent_features=op_args.get("incumbent_features", []),
                    underserved_needs=op_args.get("underserved_needs", []),
                )

                output_lines = [
                    f"ERRC for gap: {grid['gap_statement']}",
                    f"Eliminate: {', '.join(grid['eliminate']) or '(none)'}",
                    f"Reduce: {', '.join(grid['reduce']) or '(none)'}",
                    f"Raise: {', '.join(grid['raise']) or '(none)'}",
                    f"Create: {', '.join(grid['create']) or '(none)'}",
                ]
                return ToolResult.ok("\n".join(output_lines), data=grid)

            if operation == "validate_errc":
                validation = _validate_errc_grid(op_args.get("grid_rows", []))
                if validation["valid"]:
                    output = "ERRC grid is valid. No cross-action duplicates found."
                else:
                    output = (
                        "ERRC grid has cross-action duplicates: "
                        f"{len(validation['cross_action_duplicates'])}"
                    )
                return ToolResult.ok(output, data=validation)

            if operation == "value_curve_shift":
                shift = _value_curve_shift(
                    current_curve=op_args.get("current_curve", []),
                    proposed_curve=op_args.get("proposed_curve", []),
                )
                rows = shift["rows"]
                lines = ["Value-curve shifts (largest deltas first):"]
                for row in rows[:10]:
                    lines.append(
                        f"- {row['factor']}: {row['current_score']} -> "
                        f"{row['proposed_score']} ({row['delta']:+d})"
                    )
                if len(rows) > 10:
                    lines.append(f"... {len(rows) - 10} more factors.")

                return ToolResult.ok("\n".join(lines), data=shift)

            return ToolResult.fail(
                "Unknown operation. Use: propose_errc, validate_errc, or value_curve_shift."
            )
        except ValueError as exc:
            return ToolResult.fail(str(exc))
