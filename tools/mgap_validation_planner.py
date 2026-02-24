"""Validation planning tool with deterministic IDs and bounded thresholds."""

from __future__ import annotations

import re
from typing import Any

from loom.tools.registry import Tool, ToolContext, ToolResult

RISK_METHOD_MAP = {
    "demand": "problem-interview + intent capture",
    "behavior": "concierge pilot",
    "channel": "distribution channel test",
    "economics": "price/packaging test",
    "feasibility": "manual prototype trial",
}

MAX_GAPS_LIMIT = 10
MIN_THRESHOLD = 0.01
MAX_THRESHOLD = 0.95


def _to_float(
    value: Any,
    field: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be numeric") from exc
    if minimum is not None and number < minimum:
        raise ValueError(f"{field} must be >= {minimum}")
    if maximum is not None and number > maximum:
        raise ValueError(f"{field} must be <= {maximum}")
    return number


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


def _clamp(value: float, minimum: float, maximum: float) -> float:
    if value < minimum:
        return minimum
    if value > maximum:
        return maximum
    return value


def _clean_text(value: Any, field: str, *, required: bool = False, max_len: int = 180) -> str:
    text = " ".join(str(value or "").strip().split())
    if required and not text:
        raise ValueError(f"{field} must be non-empty")
    if len(text) > max_len:
        text = text[: max_len - 1].rstrip() + "…"
    return text


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "gap"


def _unique_id(base: str, used: set[str]) -> str:
    if base not in used:
        used.add(base)
        return base
    counter = 2
    while True:
        candidate = f"{base}-{counter}"
        if candidate not in used:
            used.add(candidate)
            return candidate
        counter += 1


def _build_experiments(
    top_gaps: list[dict[str, Any]],
    max_gaps: int = 3,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if max_gaps < 1:
        max_gaps = 1

    selected = top_gaps[:max_gaps]
    experiments: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    used_experiment_ids: set[str] = set()

    for index, gap in enumerate(selected, start=1):
        if not isinstance(gap, dict):
            skipped.append({"index": index - 1, "error": "gap entry must be an object"})
            continue

        try:
            gap_name = _clean_text(
                gap.get("gap_name") or gap.get("name") or f"gap-{index}",
                f"top_gaps[{index - 1}].gap_name",
                required=True,
            )
            hypothesis = _clean_text(
                gap.get("hypothesis") or f"If we address {gap_name}, adoption should increase.",
                f"{gap_name}.hypothesis",
                required=True,
                max_len=260,
            )
            risk_type = _clean_text(gap.get("risk_type", "demand"), f"{gap_name}.risk_type").lower()
            method = RISK_METHOD_MAP.get(risk_type, "concierge pilot")

            gap_score = _to_float(gap.get("gap_score", 60), f"{gap_name}.gap_score", minimum=0, maximum=100)
            confidence = _to_float(
                gap.get("evidence_confidence", gap.get("confidence", 3)),
                f"{gap_name}.evidence_confidence",
                minimum=0,
                maximum=5,
            )

            quality_factor = (gap_score / 100.0) * (0.6 + 0.4 * (confidence / 5.0))
            threshold_30 = _clamp(0.05 + 0.15 * quality_factor, MIN_THRESHOLD, MAX_THRESHOLD)
            threshold_60 = _clamp(threshold_30 * 1.4, MIN_THRESHOLD, MAX_THRESHOLD)
            threshold_90 = _clamp(threshold_60 * 1.3, MIN_THRESHOLD, MAX_THRESHOLD)
            fail_30 = _clamp(threshold_30 * 0.5, MIN_THRESHOLD, MAX_THRESHOLD)
            fail_60 = _clamp(threshold_60 * 0.5, MIN_THRESHOLD, MAX_THRESHOLD)
            fail_90 = _clamp(threshold_90 * 0.5, MIN_THRESHOLD, MAX_THRESHOLD)

            metric = _clean_text(
                gap.get("leading_metric", "qualified-conversion-rate"),
                f"{gap_name}.leading_metric",
                required=True,
            )
            base_slug = _slug(gap_name)
            assumption = _clean_text(
                gap.get("assumption_id", f"{base_slug}-core-assumption"),
                f"{gap_name}.assumption_id",
                required=True,
            )

            def build_exp(
                suffix: str,
                window_days: str,
                objective: str,
                method_name: str,
                success_threshold: float,
                fail_threshold: float,
                decision_rule: str,
            ) -> dict[str, Any]:
                exp_id = _unique_id(f"{base_slug}-{suffix}", used_experiment_ids)
                return {
                    "experiment_id": exp_id,
                    "gap_name": gap_name,
                    "assumption_id": assumption,
                    "window_days": window_days,
                    "objective": objective,
                    "method": method_name,
                    "hypothesis": hypothesis,
                    "metric": metric,
                    "success_threshold": round(success_threshold, 3),
                    "fail_threshold": round(fail_threshold, 3),
                    "decision_rule": decision_rule,
                }

            experiments.extend(
                [
                    build_exp(
                        "30-signal-probe",
                        "0-30",
                        "Validate demand and urgency with low-cost probes.",
                        method,
                        threshold_30,
                        fail_30,
                        "Scale prep if >= success; kill if < fail; otherwise iterate.",
                    ),
                    build_exp(
                        "60-wedge-pilot",
                        "31-60",
                        "Test wedge offer activation and conversion quality.",
                        "limited pilot",
                        threshold_60,
                        fail_60,
                        "Expand pilot if >= success; stop if < fail; otherwise refine.",
                    ),
                    build_exp(
                        "90-repeatability",
                        "61-90",
                        "Confirm repeatability, retention, and economics signal.",
                        "repeatability cohort test",
                        threshold_90,
                        fail_90,
                        "Scale if >= success; deprioritize if < fail; else run one more cycle.",
                    ),
                ]
            )
        except ValueError as exc:
            skipped.append({"index": index - 1, "error": str(exc)})

    return experiments, skipped


def _build_thresholds(
    experiments: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    thresholds: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for index, exp in enumerate(experiments):
        if not isinstance(exp, dict):
            skipped.append({"index": index, "error": "experiment entry must be an object"})
            continue

        try:
            experiment_id = _clean_text(
                exp.get("experiment_id"),
                f"experiments[{index}].experiment_id",
                required=True,
            )
            metric = _clean_text(
                exp.get("metric", "metric"),
                f"{experiment_id}.metric",
                required=True,
            )
            success = _to_float(
                exp.get("success_threshold", 0),
                f"{experiment_id}.success_threshold",
                minimum=0,
            )
            fail = _to_float(
                exp.get("fail_threshold", 0),
                f"{experiment_id}.fail_threshold",
                minimum=0,
            )
            if fail > success:
                fail, success = success, fail

            thresholds.append(
                {
                    "experiment_id": experiment_id,
                    "metric": metric,
                    "scale_if_gte": round(success, 3),
                    "iterate_if_between": [round(fail, 3), round(success, 3)],
                    "kill_if_lt": round(fail, 3),
                }
            )
        except ValueError as exc:
            skipped.append({"index": index, "error": str(exc)})

    return thresholds, skipped


class MarketGapValidationPlannerTool(Tool):
    """Build staged experiments and decision thresholds."""

    @property
    def name(self) -> str:
        return "mgap_validation_planner"

    @property
    def description(self) -> str:
        return (
            "Build 30/60/90 validation experiments and explicit "
            "scale/iterate/kill decision thresholds for top market gaps."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "enum": ["build_experiments", "build_thresholds"],
                    "description": "Generate experiment backlog or threshold rules.",
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
            if operation == "build_experiments":
                top_gaps = op_args.get("top_gaps", [])
                if not isinstance(top_gaps, list) or not top_gaps:
                    return ToolResult.fail("build_experiments requires non-empty args.top_gaps list")

                max_gaps = _to_int(
                    op_args.get("max_gaps", 3),
                    "max_gaps",
                    minimum=1,
                    maximum=MAX_GAPS_LIMIT,
                )

                experiments, skipped = _build_experiments(top_gaps, max_gaps=max_gaps)
                if not experiments:
                    return ToolResult.fail("no valid experiments could be generated from top_gaps")

                output_lines = [
                    f"Generated {len(experiments)} experiments "
                    f"for up to {max_gaps} top gaps."
                ]
                for exp in experiments[:6]:
                    output_lines.append(
                        f"- {exp['experiment_id']} ({exp['window_days']}): "
                        f"{exp['metric']} >= {exp['success_threshold']}"
                    )
                if skipped:
                    output_lines.append(f"Skipped invalid gap entries: {len(skipped)}")
                if len(experiments) > 6:
                    output_lines.append(f"... {len(experiments) - 6} more experiments.")
                return ToolResult.ok(
                    "\n".join(output_lines),
                    data={"experiments": experiments, "skipped": skipped},
                )

            if operation == "build_thresholds":
                experiments = op_args.get("experiments", [])
                if not isinstance(experiments, list) or not experiments:
                    return ToolResult.fail("build_thresholds requires non-empty args.experiments list")

                thresholds, skipped = _build_thresholds(experiments)
                if not thresholds:
                    return ToolResult.fail("no valid thresholds could be built from experiments")

                output_lines = [f"Built {len(thresholds)} threshold rules."]
                for rule in thresholds[:8]:
                    output_lines.append(
                        f"- {rule['experiment_id']}: scale if >= {rule['scale_if_gte']}, "
                        f"kill if < {rule['kill_if_lt']}"
                    )
                if skipped:
                    output_lines.append(f"Skipped invalid experiment entries: {len(skipped)}")
                if len(thresholds) > 8:
                    output_lines.append(f"... {len(thresholds) - 8} more rules.")
                return ToolResult.ok(
                    "\n".join(output_lines),
                    data={"thresholds": thresholds, "skipped": skipped},
                )

            return ToolResult.fail(
                "Unknown operation. Use: build_experiments or build_thresholds."
            )
        except ValueError as exc:
            return ToolResult.fail(str(exc))
