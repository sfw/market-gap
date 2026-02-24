"""Deterministic, validated gap scoring for market-gap-foundry."""

from __future__ import annotations

from typing import Any

from loom.tools.registry import Tool, ToolContext, ToolResult

MAX_SCALE = 5.0
SCORE_MODEL_VERSION = "mgap-score-v1.1"

DEFAULT_WEIGHTS = {
    "demand": 0.22,
    "pain": 0.20,
    "underserved": 0.18,
    "access": 0.14,
    "adoption_feasibility": 0.10,
    "willingness_to_pay": 0.10,
    "confidence": 0.06,
}

ALLOWED_WEIGHT_KEYS = frozenset(DEFAULT_WEIGHTS.keys())

FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "demand_intensity": ("demand_intensity", "demand_signal", "demand"),
    "pain_severity": ("pain_severity", "pain_signal", "pain"),
    "incumbent_coverage": ("incumbent_coverage", "coverage", "supply_coverage"),
    "access_barrier": ("access_barrier", "access_friction", "barrier_intensity"),
    "switching_friction": ("switching_friction", "switching_cost", "switching"),
    "willingness_to_pay_signal": (
        "willingness_to_pay_signal",
        "willingness_to_pay",
        "wtp_signal",
    ),
    "evidence_confidence": ("evidence_confidence", "confidence"),
}


def _as_float(
    value: Any,
    field: str,
    *,
    minimum: float = 0.0,
    maximum: float = 5.0,
) -> float:
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


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _normalize_weights(raw: dict[str, Any] | None) -> dict[str, float]:
    weights = dict(DEFAULT_WEIGHTS)
    if raw is None:
        total = sum(weights.values())
        return {k: v / total for k, v in weights.items()}
    if not isinstance(raw, dict):
        raise ValueError("weights must be an object of numeric values")

    unknown = sorted(set(raw) - ALLOWED_WEIGHT_KEYS)
    if unknown:
        raise ValueError(
            f"weights contains unknown key(s): {', '.join(unknown)}"
        )

    for key, value in raw.items():
        weights[key] = _as_non_negative_float(value, f"weights.{key}")

    total = sum(weights.values())
    if total <= 0:
        raise ValueError("weights total must be greater than 0")
    return {k: v / total for k, v in weights.items()}


def _normalize(score_0_to_5: float) -> float:
    return score_0_to_5 / MAX_SCALE


def _get_metric(record: dict[str, Any], canonical_field: str) -> float:
    aliases = FIELD_ALIASES.get(canonical_field, (canonical_field,))
    for key in aliases:
        if key in record and record[key] is not None:
            return _as_float(record[key], canonical_field)
    raise ValueError(
        f"missing required metric {canonical_field}; "
        f"accepted aliases: {', '.join(aliases)}"
    )


def _coerce_gap_name(record: dict[str, Any]) -> str:
    raw = str(record.get("gap_name") or record.get("name") or "").strip()
    if not raw:
        raise ValueError("gap record must include a non-empty gap_name or name")
    if len(raw) > 160:
        return raw[:160].rstrip() + "…"
    return raw


def _score_gap_record(record: dict[str, Any], weights: dict[str, float]) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise ValueError("each gap record must be an object")

    gap_name = _coerce_gap_name(record)
    demand = _get_metric(record, "demand_intensity")
    pain = _get_metric(record, "pain_severity")
    incumbent_coverage = _get_metric(record, "incumbent_coverage")
    access_barrier = _get_metric(record, "access_barrier")
    switching_friction = _get_metric(record, "switching_friction")
    willingness_to_pay = _get_metric(record, "willingness_to_pay_signal")
    confidence = _get_metric(record, "evidence_confidence")

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
        "model_version": SCORE_MODEL_VERSION,
    }


def _rank_gaps(
    gaps: list[dict[str, Any]],
    weights: dict[str, float],
    *,
    skip_invalid: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    scored: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for index, raw in enumerate(gaps):
        try:
            scored.append(_score_gap_record(raw, weights))
        except ValueError as exc:
            issue = {"index": index, "error": str(exc)}
            if skip_invalid:
                skipped.append(issue)
                continue
            raise ValueError(f"gap[{index}] invalid: {exc}") from exc

    if not scored:
        raise ValueError("no valid gaps to rank")

    scored.sort(
        key=lambda x: (
            -float(x.get("gap_score", 0.0)),
            -float(x.get("components", {}).get("confidence", 0.0)),
            str(x.get("gap_name", "")).lower(),
        ),
    )

    total = len(scored)
    for i, item in enumerate(scored, start=1):
        item["rank"] = i
        item["rank_percentile"] = round((total - i + 1) / total, 4)
    return scored, skipped


class MarketGapScorerTool(Tool):
    """Score and rank market gap hypotheses."""

    @property
    def name(self) -> str:
        return "mgap_gap_scorer"

    @property
    def description(self) -> str:
        return (
            "Deterministic market-gap scoring and ranking with strict "
            "input validation. Metrics must be in 0-5 range."
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
                if not isinstance(payload, dict):
                    return ToolResult.fail("score_gap requires gap object in args.gap")
                weights = _normalize_weights(op_args.get("weights"))
                result = _score_gap_record(payload, weights)
                output = (
                    f"{result['gap_name']}: score {result['gap_score']} "
                    f"({result['tier']})\n"
                    f"Strongest: {result['strongest_component']}\n"
                    f"Weakest: {result['weakest_component']}"
                )
                return ToolResult.ok(
                    output,
                    data={
                        "model_version": SCORE_MODEL_VERSION,
                        "gap": result,
                        "weights": weights,
                    },
                )

            if operation == "rank_gaps":
                gaps = op_args.get("gaps")
                if not isinstance(gaps, list) or not gaps:
                    return ToolResult.fail("rank_gaps requires non-empty args.gaps list")

                weights = _normalize_weights(op_args.get("weights"))
                skip_invalid = _as_bool(op_args.get("skip_invalid", False), default=False)
                ranked, skipped = _rank_gaps(
                    gaps,
                    weights,
                    skip_invalid=skip_invalid,
                )

                top_n_raw = op_args.get("top_n", min(5, len(ranked)))
                try:
                    top_n = int(top_n_raw)
                except (TypeError, ValueError):
                    top_n = min(5, len(ranked))
                top_n = max(1, min(top_n, len(ranked)))
                top = ranked[:top_n]

                lines = [
                    f"Gap ranking ({len(ranked)} valid / {len(gaps)} provided):"
                ]
                for item in top:
                    lines.append(
                        f"{item['rank']}. {item['gap_name']} "
                        f"({item['gap_score']:.2f}, {item['tier']})"
                    )
                if skipped:
                    lines.append(f"Skipped invalid records: {len(skipped)}")
                if len(ranked) > top_n:
                    lines.append(f"... {len(ranked) - top_n} more gaps ranked.")

                return ToolResult.ok(
                    "\n".join(lines),
                    data={
                        "model_version": SCORE_MODEL_VERSION,
                        "weights": weights,
                        "top_gaps": top,
                        "ranked_gaps": ranked,
                        "skipped": skipped,
                    },
                )

            return ToolResult.fail(
                "Unknown operation. Use: score_gap or rank_gaps."
            )
        except ValueError as exc:
            return ToolResult.fail(str(exc))
