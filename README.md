# market-gap-foundry

Loom process package for this question:

> "Find a gap in the market, and make a market in the gap."

The package takes a market definition as input and produces:

- Ranked market gaps with deterministic scoring
- ERRC-based market-creation plays for top gaps
- A falsifiable 30/60/90-day validation plan

## Files

- `process.yaml` — Loom process contract (schema v2)
- `tools/mgap_signal_harvester.py` — no-key public signal collection
- `tools/mgap_gap_scorer.py` — deterministic gap scoring and ranking
- `tools/mgap_errc_builder.py` — ERRC/action design helpers
- `tools/mgap_validation_planner.py` — experiment and threshold planning

## Install

```bash
loom install /absolute/path/to/market-gap
```

## Run

```bash
loom -w /path/to/workspace --process market-gap-foundry
```

Example goal input:

```text
Find gaps in the US SMB payroll software market and propose market-creation plays.
```

## Deliverables

Primary outputs include:

- `gap-scorecard.csv`
- `top-gap-shortlist.md`
- `market-playbook.md`
- `errc-grid.csv`
- `validation-plan-30-60-90.md`

## Notes

- No custom tool in this package requires API keys or credentials.
- External data collection uses public endpoints only.
