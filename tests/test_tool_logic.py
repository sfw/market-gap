"""Regression tests for bundled mgap tools."""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

try:  # Python 3.11+
    import tomllib as _tomllib  # noqa: F401
except ModuleNotFoundError:  # Python 3.10 fallback
    import tomli as _tomllib  # type: ignore
    sys.modules.setdefault("tomllib", _tomllib)

ROOT = Path(__file__).resolve().parents[1]
LOOM_SRC = Path("/Users/sfw/Development/loom/src")

for p in (ROOT, LOOM_SRC):
    p_str = str(p)
    if p_str not in sys.path:
        sys.path.insert(0, p_str)

from tools import mgap_signal_harvester as signal  # noqa: E402
from tools.mgap_errc_builder import _propose_errc, _validate_errc_grid, _value_curve_shift  # noqa: E402
from tools.mgap_gap_scorer import _normalize_weights, _rank_gaps, _score_gap_record  # noqa: E402
from tools.mgap_validation_planner import _build_experiments, _build_thresholds  # noqa: E402


class GapScorerTests(unittest.TestCase):
    def test_score_gap_requires_all_metrics(self) -> None:
        weights = _normalize_weights(None)
        with self.assertRaises(ValueError):
            _score_gap_record({"gap_name": "missing-fields"}, weights)

    def test_rank_gaps_is_deterministic(self) -> None:
        weights = _normalize_weights(None)
        gaps = [
            {
                "gap_name": "Gap B",
                "demand_intensity": 4.0,
                "pain_severity": 4.0,
                "incumbent_coverage": 2.0,
                "access_barrier": 3.0,
                "switching_friction": 2.0,
                "willingness_to_pay_signal": 3.5,
                "evidence_confidence": 4.0,
            },
            {
                "gap_name": "Gap A",
                "demand_intensity": 4.0,
                "pain_severity": 4.0,
                "incumbent_coverage": 2.0,
                "access_barrier": 3.0,
                "switching_friction": 2.0,
                "willingness_to_pay_signal": 3.5,
                "evidence_confidence": 4.0,
            },
        ]
        ranked, skipped = _rank_gaps(gaps, weights)
        self.assertEqual([], skipped)
        self.assertEqual("Gap A", ranked[0]["gap_name"])
        self.assertEqual(1, ranked[0]["rank"])


class ErrcBuilderTests(unittest.TestCase):
    def test_propose_and_validate_errc_grid(self) -> None:
        grid = _propose_errc(
            gap_statement="Busy owners need bookkeeping done in under 15 minutes weekly",
            incumbent_features=[
                {"name": "Complex multi-step setup", "customer_value": 2, "delivery_cost": 5, "accessibility": 2},
                {"name": "Manual reconciliation", "customer_value": 3, "delivery_cost": 4, "accessibility": 2},
            ],
            underserved_needs=[
                "One-click weekly books close",
                "Low-skill setup with guided defaults",
            ],
        )
        validation = _validate_errc_grid(grid["grid_rows"])
        self.assertTrue(validation["valid"])
        self.assertGreaterEqual(len(grid["create"]), 1)

    def test_value_curve_shift_summary(self) -> None:
        shift = _value_curve_shift(
            current_curve=[{"name": "Onboarding effort", "score": 5}],
            proposed_curve=[{"name": "Onboarding effort", "score": 2}],
        )
        self.assertEqual(1, shift["summary"]["factor_count"])
        self.assertEqual(-3, shift["rows"][0]["delta"])


class ValidationPlannerTests(unittest.TestCase):
    def test_build_experiments_ensures_unique_ids(self) -> None:
        top_gaps = [
            {"gap_name": "Instant onboarding", "gap_score": 80, "evidence_confidence": 4},
            {"gap_name": "Instant onboarding", "gap_score": 76, "evidence_confidence": 3},
        ]
        experiments, skipped = _build_experiments(top_gaps, max_gaps=2)
        self.assertEqual([], skipped)
        ids = [exp["experiment_id"] for exp in experiments]
        self.assertEqual(len(ids), len(set(ids)))

        thresholds, skipped_thresholds = _build_thresholds(experiments)
        self.assertEqual([], skipped_thresholds)
        for rule in thresholds:
            self.assertLessEqual(rule["kill_if_lt"], rule["scale_if_gte"])


class SignalHarvesterTests(unittest.TestCase):
    def test_hn_keyword_scan_parses_matches(self) -> None:
        async def fake_fetch(url: str):
            if url.endswith("/topstories.json"):
                return [11, 22]
            if url.endswith("/item/11.json"):
                return {
                    "id": 11,
                    "title": "Payroll automation for SMB",
                    "score": 42,
                    "time": 1_700_000_000,
                    "url": "https://example.com/a",
                }
            if url.endswith("/item/22.json"):
                return {
                    "id": 22,
                    "title": "Unrelated topic",
                    "score": 9,
                    "time": 1_700_000_100,
                    "url": "https://example.com/b",
                }
            raise AssertionError(f"Unexpected URL: {url}")

        with patch.object(signal, "_fetch_json", new=AsyncMock(side_effect=fake_fetch)):
            result = asyncio.run(
                signal._hn_keyword_scan(
                    keyword="payroll",
                    story_pool="top",
                    max_items=10,
                    max_matches=10,
                )
            )
        self.assertEqual(1, result["matched_count"])
        self.assertEqual("payroll automation for smb", result["matches"][0]["title"].lower())

    def test_world_bank_indicator_parses_series(self) -> None:
        async def fake_fetch(url: str):
            return [
                {"page": 1, "pages": 1, "per_page": "120", "total": 2},
                [
                    {
                        "indicator": {"value": "GDP growth (annual %)"},
                        "date": "2024",
                        "value": 2.5,
                    },
                    {
                        "indicator": {"value": "GDP growth (annual %)"},
                        "date": "2023",
                        "value": 1.0,
                    },
                ],
            ]

        with patch.object(signal, "_fetch_json", new=AsyncMock(side_effect=fake_fetch)):
            result = asyncio.run(
                signal._world_bank_indicator(
                    country_code="USA",
                    indicator_code="NY.GDP.MKTP.KD.ZG",
                    years=5,
                )
            )
        self.assertEqual("USA", result["country_code"])
        self.assertEqual(2024, result["latest_year"])
        self.assertIn("source", result)


if __name__ == "__main__":
    unittest.main()
