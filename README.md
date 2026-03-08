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

Current workspace-mutating tool surface used by this package:

- `write_file`
- `document_write`
- `spreadsheet`
- `edit_file`
- `move_file`

Bundled `mgap_*` tools are read/compute only and must remain non-mutating.

Upgrade checklist for existing or new workspace-writing tools:

1. Set `is_mutating = True`.
2. Return accurate workspace-relative `files_changed` for every successful write, edit, move, or delete.
3. If write targets are not under `path`, expose `mutation_target_arg_keys` so policy can discover targets from args metadata (for example `output_path`, `destination`, `report_path`).
4. Normalize and constrain writes under `ctx.workspace` using `_resolve_path(...)`.
5. Keep `execution.sealed_artifact_post_call_guard` as defense-in-depth only (`off|warn|enforce`), with preflight evidence gating + reseal/provenance as primary controls.

Expected mutation result contract:

```python
@property
def is_mutating(self) -> bool:
    return True

@property
def mutation_target_arg_keys(self) -> tuple[str, ...]:
    return ("output_path", "destination")

async def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
    relpath = str(args.get("output_path", "")).strip()
    # ... mutate workspace file(s) ...
    return ToolResult.ok("updated", files_changed=[relpath])
```

Sealed-artifact protocol signals to keep stable:

- `sealed_policy_preflight_blocked`
- `sealed_reseal_applied`
- `sealed_unexpected_mutation_detected`

## Testing

Run package tests:

```bash
loom process test .
```

Run this repo's Python regression tests:

```bash
uv run pytest -q
```
