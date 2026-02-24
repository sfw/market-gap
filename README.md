# Market Gap Foundry for Loom

A Loom process package that turns a market input into a production-grade
gap-discovery and market-creation workflow.

It is designed around this principle:

> "Find a gap in the market, and make a market in the gap."

## What it does

This package runs a strict 7-phase process:

1. frame-market
2. map-jobs-and-nonconsumption
3. collect-signals
4. map-supply
5. score-gaps
6. design-market-plays
7. validation-plan

Core outputs:

- deterministic ranked market gaps
- ERRC-based market-creation plays
- falsifiable 30/60/90 experiment plans with scale/iterate/kill thresholds

## Credentials and APIs

This package does not require API keys, account credentials, or private
integrations.

Bundled tools use public/no-auth sources and local deterministic logic:

- `mgap_signal_harvester`
- `mgap_gap_scorer`
- `mgap_errc_builder`
- `mgap_validation_planner`

## Installation

`loom install` supports local paths and GitHub sources.

Install from GitHub (full URL):

```bash
loom install https://github.com/sfw/market-gap
```

Install from GitHub (shorthand):

```bash
loom install sfw/market-gap
```

Install from a local path:

```bash
loom install /absolute/path/to/market-gap
```

Install into a specific workspace:

```bash
loom install /absolute/path/to/market-gap -w /path/to/project
```

## Usage

Interactive:

```bash
loom -w /path/to/workspace --process market-gap-foundry
```

Example run goal:

```text
Find gaps in the US SMB payroll software market and propose market-creation plays.
```

Non-interactive:

```bash
loom run "Find gaps in the US SMB payroll software market and propose market-creation plays." \
  --workspace /path/to/workspace \
  --process market-gap-foundry
```

## Deliverables

Selected key deliverables:

- `gap-register.csv`
- `gap-scorecard.csv`
- `top-gap-shortlist.md`
- `market-playbook.md`
- `errc-grid.csv`
- `wedge-offers.csv`
- `experiment-backlog.csv`
- `validation-plan-30-60-90.md`
- `decision-thresholds.md`

## Testing

Run package tests:

```bash
loom process test .
```

Run this repo's Python regression tests:

```bash
PYTHONPATH=/Users/sfw/Development/loom/src python3 -m unittest discover -s tests -p 'test_*.py'
```
