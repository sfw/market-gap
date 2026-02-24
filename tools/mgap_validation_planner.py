"""Validation planning tool for market-gap-foundry."""

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


def _to_float(value: Any, field: str, *, minimum: float | None = None, maximum: float | None = None) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be numeric") from exc
    if minimum is not None and number < minimum:
        raise ValueError(f"{field} must be >= {minimum}")
    if maximum is not None and number > maximum:
        raise ValueError(f"{field} must be <= {maximum}")
    return number


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "gap"


def _build_experiments(top_gaps: list[dict[str, Any]], max_gaps: int = 3) -> list[dict[str, Any]]:
    if max_gaps < 1:
        max_gaps = 1
    selected = top_gaps[:max_gaps]
    experiments: list[dict[str, Any]] = []

    for index, gap in enumerate(selected, start=1):
        if not isinstance(gap, dict):
            continue
        gap_name = str(gap.get("gap_name") or gap.get("name") or f"gap-{index}").strip()
        hypothesis = str(
            gap.get("hypothesis")
            or f"If we address {gap_name}, adoption should measurably increase."
        ).strip()
        risk_type = str(gap.get("risk_type", "demand")).strip().lower()
        method = RISK_METHOD_MAP.get(risk_type, "concierge pilot")

        gap_score = _to_float(gap.get("gap_score", 60), "gap_score", minimum=0, maximum=100)
        confidence = _to_float(
            gap.get("evidence_confidence", gap.get("confidence", 3)),
            "evidence_confidence",
            minimum=0,
            maximum=5,
        )

        quality_factor = (gap_score / 100.0) * (0.6 + 0.4 * (confidence / 5.0))
        threshold_30 = round(0.05 + 0.15 * quality_factor, 3)
        threshold_60 = round(threshold_30 * 1.4, 3)
        threshold_90 = round(threshold_60 * 1.3, 3)
        fail_30 = round(threshold_30 * 0.5, 3)
        fail_60 = round(threshold_60 * 0.5, 3)
        fail_90 = round(threshold_90 * 0.5, 3)

        metric = str(gap.get("leading_metric", "qualified-conversion-rate")).strip()
        base_slug = _slug(gap_name)

        experiments.extend(
            [
                {
                    "experiment_id": f"{base_slug}-30-signal-probe",
                    "gap_name": gap_name,
                    "window_days": "0-30",
                    "objective": "Validate demand and urgency with low-cost probes.",
                    "method": method,
                    "hypothesis": hypothesis,
                    "metric": metric,
                    "success_threshold": threshold_30,
                    "fail_threshold": fail_30,
                    "decision_rule": "Scale prep if >= success; kill if < fail; otherwise iterate.",
                },
                {
                    "experiment_id": f"{base_slug}-60-wedge-pilot",
                    "gap_name": gap_name,
                    "window_days": "31-60",
                    "objective": "Test wedge offer activation and conversion quality.",
                    "method": "limited pilot",
                    "hypothesis": hypothesis,
                    "metric": metric,
                    "success_threshold": threshold_60,
                    "fail_threshold": fail_60,
                    "decision_rule": "Expand pilot if >= success; stop if < fail; otherwise refine.",
                },
                {
                    "experiment_id": f"{base_slug}-90-repeatability",
                    "gap_name": gap_name,
                    "window_days": "61-90",
                    "objective": "Confirm repeatability, retention, and unit economics signal.",
                    "method": "repeatability cohort test",
                    "hypothesis": hypothesis,
                    "metric": metric,
                    "success_threshold": threshold_90,
                    "fail_threshold": fail_90,
                    "decision_rule": "Scale if >= success; deprioritize if < fail; else run one more cycle.",
                },
            ]
        )
    return experiments


def _build_thresholds(experiments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    thresholds: list[dict[str, Any]] = []
    for exp in experiments:
        if not isinstance(exp, dict):
            continue
        experiment_id = str(exp.get("experiment_id", "")).strip()
        metric = str(exp.get("metric", "metric")).strip()
        success = _to_float(exp.get("success_threshold", 0), f"{experiment_id}.success_threshold")
        fail = _to_float(exp.get("fail_threshold", 0), f"{experiment_id}.fail_threshold")
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
    return thresholds


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

                max_gaps_raw = op_args.get("max_gaps", 3)
                try:
                    max_gaps = int(max_gaps_raw)
                except (TypeError, ValueError):
                    max_gaps = 3
                max_gaps = max(1, min(max_gaps, 10))

                experiments = _build_experiments(top_gaps, max_gaps=max_gaps)
                output_lines = [
                    f"Generated {len(experiments)} experiments "
                    f"for {min(len(top_gaps), max_gaps)} top gaps."
                ]
                for exp in experiments[:6]:
                    output_lines.append(
                        f"- {exp['experiment_id']} ({exp['window_days']}): "
                        f"{exp['metric']} >= {exp['success_threshold']}"
                    )
                if len(experiments) > 6:
                    output_lines.append(f"... {len(experiments) - 6} more experiments.")
                return ToolResult.ok(
                    "\n".join(output_lines),
                    data={"experiments": experiments},
                )

            if operation == "build_thresholds":
                experiments = op_args.get("experiments", [])
                if not isinstance(experiments, list) or not experiments:
                    return ToolResult.fail("build_thresholds requires non-empty args.experiments list")

                thresholds = _build_thresholds(experiments)
                output_lines = [f"Built {len(thresholds)} threshold rules."]
                for rule in thresholds[:8]:
                    output_lines.append(
                        f"- {rule['experiment_id']}: scale if >= {rule['scale_if_gte']}, "
                        f"kill if < {rule['kill_if_lt']}"
                    )
                if len(thresholds) > 8:
                    output_lines.append(f"... {len(thresholds) - 8} more rules.")
                return ToolResult.ok("\n".join(output_lines), data={"thresholds": thresholds})

            return ToolResult.fail(
                "Unknown operation. Use: build_experiments or build_thresholds."
            )
        except ValueError as exc:
            return ToolResult.fail(str(exc))
