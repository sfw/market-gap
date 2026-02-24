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


if __name__ == "__main__":
    unittest.main()
