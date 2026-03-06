"""Process contract validation tests for market-gap-foundry."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

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

from loom.processes.schema import ProcessLoader  # noqa: E402


class ProcessContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.loader = ProcessLoader(workspace=ROOT)
        self.process = self.loader.load(ROOT / "process.yaml")

    def test_process_loads(self) -> None:
        self.assertEqual("market-gap-foundry", self.process.name)
        self.assertEqual(2, self.process.schema_version)
        self.assertEqual("strict", self.process.phase_mode)
        self.assertEqual("medium", self.process.risk_level)

    def test_verification_rules_include_hardening_checks(self) -> None:
        names = {rule.name for rule in self.process.verification_rules}
        self.assertIn("no-placeholders", names)
        self.assertIn("scorecard-fields-complete", names)
        self.assertIn("source-index-quality", names)

    def test_declares_custom_tools(self) -> None:
        required_tools = set(self.process.tools.required)
        self.assertIn("mgap_signal_harvester", required_tools)
        self.assertIn("mgap_gap_scorer", required_tools)
        self.assertIn("mgap_errc_builder", required_tools)
        self.assertIn("mgap_validation_planner", required_tools)

    def test_process_validity_contract_is_declared(self) -> None:
        contract = self.process.validity_contract
        self.assertTrue(contract.get("enabled"))
        self.assertEqual(0, contract.get("max_contradicted_count"))
        self.assertEqual("rewrite_uncertainty", contract.get("prune_mode"))
        final_gate = contract.get("final_gate", {})
        self.assertEqual(2, final_gate.get("synthesis_min_verification_tier"))
        self.assertFalse(final_gate.get("enforce_verified_context_only"))

    def test_validation_phase_has_synthesis_hardening_and_iteration(self) -> None:
        phase = next(p for p in self.process.phases if p.id == "validation-plan")
        contract = phase.validity_contract
        self.assertEqual(0.8, contract.get("min_supported_ratio"))
        self.assertEqual(0.2, contract.get("max_unverified_ratio"))
        temporal = contract.get("final_gate", {}).get("temporal_consistency", {})
        self.assertTrue(temporal.get("enabled"))
        self.assertEqual(730, temporal.get("max_source_age_days"))

        self.assertIsNotNone(phase.iteration)
        assert phase.iteration is not None
        self.assertTrue(phase.iteration.enabled)
        self.assertEqual(3, phase.iteration.max_attempts)
        self.assertGreaterEqual(len(phase.iteration.gates), 1)


if __name__ == "__main__":
    unittest.main()
