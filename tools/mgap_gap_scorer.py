"""Deterministic gap scoring for market-gap-foundry."""

from __future__ import annotations

from typing import Any

from loom.tools.registry import Tool, ToolContext, ToolResult

MAX_SCALE = 5.0

DEFAULT_WEIGHTS = {
    "demand": 0.22,
    "pain": 0.20,
    "underserved": 0.18,
    "access": 0.14,
    "adoption_feasibility": 0.10,
    "willingness_to_pay": 0.10,
    "confidence": 0.06,
}


def _as_float(value: Any, field: str, *, minimum: float = 0.0, maximum: float = 5.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a number in [{minimum}, {maximum}]") from exc
    if number < minimum or number > maximum:
        raise ValueError(f"{field} must be in [{minimum}, {maximum}]")
    return number


def _as_non_negative_float(value: Any, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be numeric") from exc
    if number < 0:
        raise ValueError(f"{field} must be >= 0")
    return number


def _normalize_weights(raw: dict[str, Any] | None) -> dict[str, float]:
    weights = dict(DEFAULT_WEIGHTS)
    if raw is None:
        total = sum(weights.values())
        return {k: v / total for k, v in weights.items()}
    if not isinstance(raw, dict):
        raise ValueError("weights must be an object of numeric values")

    for key, value in raw.items():
        if key in weights:
            weights[key] = _as_non_negative_float(value, f"weights.{key}")

    total = sum(weights.values())
    if total <= 0:
        raise ValueError("weights total must be greater than 0")
    return {k: v / total for k, v in weights.items()}


def _normalize(score_0_to_5: float) -> float:
    return score_0_to_5 / MAX_SCALE


def _score_gap_record(record: dict[str, Any], weights: dict[str, float]) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise ValueError("each gap record must be an object")

    gap_name = str(record.get("gap_name") or record.get("name") or "unnamed-gap").strip()
    demand = _as_float(record.get("demand_intensity", 0), "demand_intensity")
    pain = _as_float(record.get("pain_severity", 0), "pain_severity")
    incumbent_coverage = _as_float(record.get("incumbent_coverage", 0), "incumbent_coverage")
    access_barrier = _as_float(record.get("access_barrier", 0), "access_barrier")
    switching_friction = _as_float(record.get("switching_friction", 0), "switching_friction")
    willingness_to_pay = _as_float(
        record.get("willingness_to_pay_signal", 0),
        "willingness_to_pay_signal",
    )
    confidence = _as_float(record.get("evidence_confidence", 0), "evidence_confidence")

    components = {
        "demand": demand,
        "pain": pain,
        "underserved": MAX_SCALE - incumbent_coverage,
        "access": access_barrier,
        "adoption_feasibility": MAX_SCALE - switching_friction,
        "willingness_to_pay": willingness_to_pay,
        "confidence": confidence,
    }

    weighted_score = 0.0
    for key, value in components.items():
        weighted_score += weights[key] * _normalize(value)
    gap_score = round(weighted_score * 100.0, 2)

    if gap_score >= 70:
        tier = "high"
    elif gap_score >= 45:
        tier = "medium"
    else:
        tier = "low"

    weakest_component = min(components, key=components.get)
    strongest_component = max(components, key=components.get)

    return {
        "gap_name": gap_name,
        "gap_score": gap_score,
        "tier": tier,
        "components": {k: round(v, 3) for k, v in components.items()},
        "strongest_component": strongest_component,
        "weakest_component": weakest_component,
    }


def _rank_gaps(gaps: list[dict[str, Any]], weights: dict[str, float]) -> list[dict[str, Any]]:
    scored: list[dict[str, Any]] = []
    for raw in gaps:
        scored.append(_score_gap_record(raw, weights))

    scored.sort(
        key=lambda x: (
            float(x.get("gap_score", 0.0)),
            float(x.get("components", {}).get("confidence", 0.0)),
        ),
        reverse=True,
    )

    for i, item in enumerate(scored, start=1):
        item["rank"] = i
    return scored


class MarketGapScorerTool(Tool):
    """Score and rank market gap hypotheses."""

    @property
    def name(self) -> str:
        return "mgap_gap_scorer"

    @property
    def description(self) -> str:
        return (
            "Deterministic market-gap scoring and ranking. "
            "Inputs are 0-5 signals: demand_intensity, pain_severity, "
            "incumbent_coverage, access_barrier, switching_friction, "
            "willingness_to_pay_signal, and evidence_confidence."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "enum": ["score_gap", "rank_gaps"],
                    "description": "Single score or ranked list mode.",
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
            if operation == "score_gap":
                payload = op_args.get("gap", op_args)
                weights = _normalize_weights(op_args.get("weights"))
                result = _score_gap_record(payload, weights)
                output = (
                    f"{result['gap_name']}: score {result['gap_score']} "
                    f"({result['tier']})\n"
                    f"Strongest: {result['strongest_component']}\n"
                    f"Weakest: {result['weakest_component']}"
                )
                return ToolResult.ok(output, data={"gap": result, "weights": weights})

            if operation == "rank_gaps":
                gaps = op_args.get("gaps")
                if not isinstance(gaps, list) or not gaps:
                    return ToolResult.fail("rank_gaps requires non-empty args.gaps list")

                weights = _normalize_weights(op_args.get("weights"))
                ranked = _rank_gaps(gaps, weights)

                top_n_raw = op_args.get("top_n", min(5, len(ranked)))
                try:
                    top_n = int(top_n_raw)
                except (TypeError, ValueError):
                    top_n = min(5, len(ranked))
                top_n = max(1, min(top_n, len(ranked)))
                top = ranked[:top_n]

                lines = ["Gap ranking (top results):"]
                for item in top:
                    lines.append(
                        f"{item['rank']}. {item['gap_name']} "
                        f"({item['gap_score']}, {item['tier']})"
                    )
                if len(ranked) > top_n:
                    lines.append(f"... {len(ranked) - top_n} more gaps ranked.")

                return ToolResult.ok(
                    "\n".join(lines),
                    data={
                        "weights": weights,
                        "top_gaps": top,
                        "ranked_gaps": ranked,
                    },
                )

            return ToolResult.fail(
                "Unknown operation. Use: score_gap or rank_gaps."
            )
        except ValueError as exc:
            return ToolResult.fail(str(exc))
