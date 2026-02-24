"""ERRC helper tool for market-gap-foundry."""

from __future__ import annotations

import re
from typing import Any

from loom.tools.registry import Tool, ToolContext, ToolResult


def _as_score(value: Any, field: str) -> int:
    try:
        score = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be an integer in [1, 5]") from exc
    if score < 1 or score > 5:
        raise ValueError(f"{field} must be in [1, 5]")
    return score


def _tokenize(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        key = item.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(item.strip())
    return out


def _propose_errc(
    gap_statement: str,
    incumbent_features: list[dict[str, Any]],
    underserved_needs: list[str],
) -> dict[str, Any]:
    eliminate: list[str] = []
    reduce: list[str] = []
    raise_: list[str] = []
    create: list[str] = []

    incumbent_names: list[str] = []
    incumbent_tokens: set[str] = set()

    for raw in incumbent_features:
        if isinstance(raw, str):
            name = raw.strip()
            customer_value = 3
            delivery_cost = 3
            accessibility = 3
        elif isinstance(raw, dict):
            name = str(raw.get("name", "")).strip()
            customer_value = _as_score(raw.get("customer_value", 3), f"{name}.customer_value")
            delivery_cost = _as_score(raw.get("delivery_cost", 3), f"{name}.delivery_cost")
            accessibility = _as_score(raw.get("accessibility", 3), f"{name}.accessibility")
        else:
            continue

        if not name:
            continue

        incumbent_names.append(name)
        incumbent_tokens.update(_tokenize(name))

        if customer_value <= 2 and delivery_cost >= 4:
            eliminate.append(name)
        elif customer_value <= 3 and delivery_cost >= 3:
            reduce.append(name)

        if customer_value >= 3 and accessibility <= 2:
            raise_.append(name)

    for need in underserved_needs:
        if not isinstance(need, str):
            continue
        cleaned = need.strip()
        if not cleaned:
            continue
        overlap = bool(_tokenize(cleaned) & incumbent_tokens)
        if not overlap:
            create.append(cleaned)

    if not create and underserved_needs:
        fallback = str(underserved_needs[0]).strip()
        if fallback:
            create.append(f"New offer centered on: {fallback}")

    eliminate = _dedupe(eliminate)
    reduce = _dedupe(reduce)
    raise_ = _dedupe(raise_)
    create = _dedupe(create)

    rows = (
        [{"action": "eliminate", "item": item} for item in eliminate]
        + [{"action": "reduce", "item": item} for item in reduce]
        + [{"action": "raise", "item": item} for item in raise_]
        + [{"action": "create", "item": item} for item in create]
    )

    return {
        "gap_statement": gap_statement,
        "incumbent_features_considered": incumbent_names,
        "eliminate": eliminate,
        "reduce": reduce,
        "raise": raise_,
        "create": create,
        "grid_rows": rows,
        "rationale": [
            "Eliminate low-value/high-cost incumbency baggage.",
            "Reduce complexity where users tolerate simplification.",
            "Raise accessibility on factors that block adoption.",
            "Create new factors tied directly to underserved jobs.",
        ],
    }


def _curve_to_map(curve: list[dict[str, Any]] | list[str], label: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for raw in curve:
        if isinstance(raw, str):
            name = raw.strip()
            score = 3
        elif isinstance(raw, dict):
            name = str(raw.get("name", "")).strip()
            score = _as_score(raw.get("score", 3), f"{label}:{name}.score")
        else:
            continue
        if name:
            out[name] = score
    return out


def _value_curve_shift(
    current_curve: list[dict[str, Any]] | list[str],
    proposed_curve: list[dict[str, Any]] | list[str],
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

    rows.sort(key=lambda row: abs(int(row["delta"])), reverse=True)
    high_impact = [row for row in rows if abs(int(row["delta"])) >= 2]
    return {"rows": rows, "high_impact_factors": high_impact}


class MarketGapERRCTool(Tool):
    """Generate ERRC grids and value-curve shifts."""

    @property
    def name(self) -> str:
        return "mgap_errc_builder"

    @property
    def description(self) -> str:
        return (
            "Build ERRC (eliminate/reduce/raise/create) action grids and "
            "value-curve shift tables for market-creation design."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "enum": ["propose_errc", "value_curve_shift"],
                    "description": "ERRC generation or value-curve delta analysis.",
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
                gap_statement = str(op_args.get("gap_statement", "")).strip()
                incumbent_features = op_args.get("incumbent_features", [])
                underserved_needs = op_args.get("underserved_needs", [])

                if not isinstance(incumbent_features, list):
                    return ToolResult.fail("incumbent_features must be a list")
                if not isinstance(underserved_needs, list):
                    return ToolResult.fail("underserved_needs must be a list")

                grid = _propose_errc(
                    gap_statement=gap_statement,
                    incumbent_features=incumbent_features,
                    underserved_needs=[str(item) for item in underserved_needs],
                )

                output_lines = [
                    f"ERRC for gap: {grid['gap_statement'] or '(unspecified)'}",
                    f"Eliminate: {', '.join(grid['eliminate']) or '(none)'}",
                    f"Reduce: {', '.join(grid['reduce']) or '(none)'}",
                    f"Raise: {', '.join(grid['raise']) or '(none)'}",
                    f"Create: {', '.join(grid['create']) or '(none)'}",
                ]
                return ToolResult.ok("\n".join(output_lines), data=grid)

            if operation == "value_curve_shift":
                current_curve = op_args.get("current_curve", [])
                proposed_curve = op_args.get("proposed_curve", [])
                if not isinstance(current_curve, list) or not isinstance(proposed_curve, list):
                    return ToolResult.fail("current_curve and proposed_curve must be lists")

                shift = _value_curve_shift(current_curve, proposed_curve)
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
                "Unknown operation. Use: propose_errc or value_curve_shift."
            )
        except ValueError as exc:
            return ToolResult.fail(str(exc))
