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

## Process contract v2

This package uses Loom schema v2 process definitions with:

- explicit `risk_level: medium`
- process-level `validity_contract` for claim extraction and contradiction control
- synthesis hardening for `validation-plan` (stricter support thresholds + recency checks)
- an iteration loop on `validation-plan` with deterministic artifact gates

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

## Tool Mutation Protocol (Maintainers)

When adding or upgrading bundled tools that write/modify/move/delete workspace files:

- Set `is_mutating = True` on each workspace-mutating tool.
- Return accurate workspace-relative `files_changed` on every successful mutation.
- If targets are not under `path`, expose `mutation_target_arg_keys` (for example `output_path`, `destination`) so sealed-artifact preflight policy can resolve targets.
- Resolve writes inside `ctx.workspace` using `_resolve_path(...)`; do not bypass workspace normalization.
- Keep post-call guard (`execution.sealed_artifact_post_call_guard`) as defense-in-depth only (`off|warn|enforce`), and rely on preflight evidence gating + reseal metadata as primary controls.

## Testing

Run package tests:

```bash
loom process test .
```

Run this repo's Python regression tests:

```bash
uv run pytest -q
```
