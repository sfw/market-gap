"""Sealed-artifact mutation protocol regressions for market-gap package tools."""

from __future__ import annotations

import asyncio
import enum
import hashlib
import sys
import datetime as _datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

if not hasattr(enum, "StrEnum"):  # Python 3.10 compatibility for Loom imports
    class StrEnum(str, enum.Enum):
        pass

    enum.StrEnum = StrEnum

if not hasattr(_datetime, "UTC"):  # Python 3.10 compatibility for Loom imports
    _datetime.UTC = _datetime.timezone.utc

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

from loom.engine.orchestrator import evidence as orchestrator_evidence  # noqa: E402
from loom.engine.orchestrator import telemetry as orchestrator_telemetry  # noqa: E402
from loom.engine.runner import SubtaskRunner, ToolCallRecord  # noqa: E402
from loom.engine.runner import policy as runner_policy  # noqa: E402
from loom.engine.runner import telemetry as runner_telemetry  # noqa: E402
from loom.events import types as event_types  # noqa: E402
from loom.processes.schema import ProcessLoader  # noqa: E402
from loom.state.task_state import Task  # noqa: E402
from loom.tools.document_write import DocumentWriteTool  # noqa: E402
from loom.tools.file_ops import EditFileTool, MoveFileTool, WriteFileTool  # noqa: E402
from loom.tools.registry import ToolContext, ToolResult  # noqa: E402
from loom.tools.spreadsheet import SpreadsheetTool  # noqa: E402

WRITER_TOOL_FACTORIES = {
    "write_file": lambda: WriteFileTool(),
    "document_write": lambda: DocumentWriteTool(),
    "spreadsheet": lambda: SpreadsheetTool(),
}

WRITER_TOOL_ARGS = {
    "write_file": {
        "path": "outputs/note.txt",
        "content": "hello\n",
    },
    "document_write": {
        "path": "outputs/summary.md",
        "title": "Summary",
        "content": "Body",
    },
    "spreadsheet": {
        "operation": "create",
        "path": "outputs/signals.csv",
        "headers": ["signal", "count"],
        "rows": [["trial", "1"]],
    },
}

WRITER_EXPECTED_CHANGED = {
    "write_file": ["outputs/note.txt"],
    "document_write": ["outputs/summary.md"],
    "spreadsheet": ["outputs/signals.csv"],
}
PACKAGE_MUTATING_TOOL_SURFACE = [
    "document_write",
    "edit_file",
    "move_file",
    "spreadsheet",
    "write_file",
]
PRE_FLIGHT_MUTATION_CASES = [
    ("write_file", lambda relpath: {"path": relpath, "content": "mutated\n"}),
    (
        "document_write",
        lambda relpath: {"path": relpath, "title": "Title", "content": "Body"},
    ),
    (
        "spreadsheet",
        lambda relpath: {
            "operation": "create",
            "path": relpath,
            "headers": ["a", "b"],
            "rows": [["1", "2"]],
        },
    ),
    (
        "edit_file",
        lambda relpath: {"path": relpath, "old_str": "alpha", "new_str": "beta"},
    ),
    (
        "move_file",
        lambda relpath: {
            "source": "reports/original-input.txt",
            "destination": relpath,
        },
    ),
]


class _EvidenceStub:
    def __init__(self, records: list[dict] | None = None) -> None:
        self._state = SimpleNamespace(load_evidence_records=lambda task_id: records or [])

    def _artifact_seal_registry(self, task: Task):
        return orchestrator_evidence._artifact_seal_registry(self, task)

    def _is_intermediate_artifact_path(self, *, task: Task, relpath: str) -> bool:
        del task, relpath
        return False

    def _task_run_id(self, task: Task) -> str:
        del task
        return "run-1"

    def _artifact_content_for_call(
        self,
        tool_name: str,
        args: dict[str, object],
        result_data: dict[str, object],
    ) -> str:
        return orchestrator_evidence._artifact_content_for_call(tool_name, args, result_data)

    def _backfill_artifact_seals_from_evidence(self, task: Task) -> int:
        return orchestrator_evidence._backfill_artifact_seals_from_evidence(self, task)


def _required_writer_tools() -> list[str]:
    process = ProcessLoader(workspace=ROOT).load(ROOT / "process.yaml")
    required = set(process.tools.required)
    writers = set(WRITER_TOOL_FACTORIES)
    return sorted(required & writers)


def _package_tool_policy_surface() -> tuple[list[str], list[str]]:
    process = ProcessLoader(workspace=ROOT).load(ROOT / "process.yaml")
    required = sorted(set(process.tools.required))
    excluded = sorted(set(process.tools.excluded))
    return required, excluded


def _build_sealed_task(tmp_path: Path, relpath: str) -> Task:
    return Task(
        id="task-1",
        goal="sealed-policy",
        workspace=str(tmp_path),
        metadata={
            "artifact_seals": {
                relpath: {
                    "path": relpath,
                    "sha256": hashlib.sha256(b"seed").hexdigest(),
                    "subtask_id": "s1",
                    "sealed_at": "2026-03-05T10:00:00",
                },
            },
            "validity_scorecard": {
                "subtask_metrics": {
                    "s1": {"verification_outcome": "pass"},
                },
            },
        },
    )


def test_inventory_required_writer_tools() -> None:
    assert _required_writer_tools() == ["document_write", "spreadsheet", "write_file"]


def test_inventory_workspace_mutation_tool_surface() -> None:
    required, excluded = _package_tool_policy_surface()
    assert "delete_file" in excluded
    assert "write_file" in required
    assert "document_write" in required
    assert "spreadsheet" in required
    assert PACKAGE_MUTATING_TOOL_SURFACE == [
        "document_write",
        "edit_file",
        "move_file",
        "spreadsheet",
        "write_file",
    ]


@pytest.mark.parametrize("tool_name", _required_writer_tools())
def test_required_writers_are_mutating_and_emit_files_changed(
    tool_name: str,
    tmp_path: Path,
) -> None:
    tool = WRITER_TOOL_FACTORIES[tool_name]()
    args = WRITER_TOOL_ARGS[tool_name]

    assert tool.is_mutating is True

    result = asyncio.run(tool.execute(args, ToolContext(workspace=tmp_path)))
    assert result.success, result.error
    assert result.files_changed == WRITER_EXPECTED_CHANGED[tool_name]

    for relpath in result.files_changed:
        assert relpath == str(Path(relpath))
        assert (tmp_path / relpath).exists()


@pytest.mark.parametrize("tool_name", PACKAGE_MUTATING_TOOL_SURFACE)
def test_workspace_mutating_tools_emit_accurate_files_changed(
    tool_name: str,
    tmp_path: Path,
) -> None:
    if tool_name == "write_file":
        tool = WriteFileTool()
        args = {"path": "mut/write.txt", "content": "hello\n"}
        expected = ["mut/write.txt"]
    elif tool_name == "document_write":
        tool = DocumentWriteTool()
        args = {"path": "mut/doc.md", "title": "Doc", "content": "Body"}
        expected = ["mut/doc.md"]
    elif tool_name == "spreadsheet":
        tool = SpreadsheetTool()
        args = {
            "operation": "create",
            "path": "mut/sheet.csv",
            "headers": ["a", "b"],
            "rows": [["1", "2"]],
        }
        expected = ["mut/sheet.csv"]
    elif tool_name == "edit_file":
        (tmp_path / "mut").mkdir(parents=True, exist_ok=True)
        (tmp_path / "mut" / "edit.md").write_text("alpha\nbeta\n", encoding="utf-8")
        tool = EditFileTool()
        args = {"path": "mut/edit.md", "old_str": "beta", "new_str": "gamma"}
        expected = ["mut/edit.md"]
    else:
        assert tool_name == "move_file"
        (tmp_path / "mut").mkdir(parents=True, exist_ok=True)
        (tmp_path / "mut" / "src.txt").write_text("x", encoding="utf-8")
        tool = MoveFileTool()
        args = {"source": "mut/src.txt", "destination": "mut/dst.txt"}
        expected = ["mut/src.txt", "mut/dst.txt"]

    assert tool.is_mutating is True
    result = asyncio.run(tool.execute(args, ToolContext(workspace=tmp_path)))
    assert result.success, result.error
    assert result.files_changed == expected

    if tool_name == "move_file":
        assert not (tmp_path / "mut/src.txt").exists()
        assert (tmp_path / "mut/dst.txt").exists()
    else:
        for relpath in result.files_changed:
            assert (tmp_path / relpath).exists()


def test_target_paths_support_metadata_keys_for_mutating_tools(tmp_path: Path) -> None:
    paths = runner_policy.target_paths_for_policy(
        tool_name="custom_output_writer",
        tool_args={
            "payload": {
                "output_path": "reports/summary.csv",
                "destination": "reports/summary-v2.csv",
            },
        },
        workspace=tmp_path,
        is_mutating_tool=True,
        mutation_target_arg_keys=("output_path", "destination"),
        is_mutating_file_tool_fn=lambda tool_name, tool_args, **kwargs: True,
    )
    assert paths == ["reports/summary.csv", "reports/summary-v2.csv"]


def test_output_path_writer_preflight_blocked_without_post_seal_evidence(
    tmp_path: Path,
) -> None:
    relpath = "reports/summary.csv"
    task = _build_sealed_task(tmp_path, relpath)

    error = SubtaskRunner._validate_sealed_artifact_mutation_policy(
        task=task,
        tool_name="custom_output_writer",
        tool_args={"output_path": relpath},
        workspace=tmp_path,
        is_mutating_tool=True,
        mutation_target_arg_keys=("output_path",),
        prior_successful_tool_calls=[],
        current_tool_calls=[],
    )

    assert error is not None
    assert "Sealed artifact mutation blocked" in error


def test_output_path_writer_preflight_allowed_with_post_seal_evidence(
    tmp_path: Path,
) -> None:
    relpath = "reports/summary.csv"
    task = _build_sealed_task(tmp_path, relpath)

    prior_calls = [
        ToolCallRecord(
            tool="read_file",
            args={"path": relpath},
            result=ToolResult.ok("fresh evidence"),
            timestamp="2026-03-05T10:15:00",
        ),
    ]

    error = SubtaskRunner._validate_sealed_artifact_mutation_policy(
        task=task,
        tool_name="custom_output_writer",
        tool_args={"output_path": relpath},
        workspace=tmp_path,
        is_mutating_tool=True,
        mutation_target_arg_keys=("output_path",),
        prior_successful_tool_calls=prior_calls,
        current_tool_calls=[],
    )

    assert error is None


@pytest.mark.parametrize("tool_name,args_factory", PRE_FLIGHT_MUTATION_CASES)
def test_preflight_blocks_all_mutating_tools_without_post_seal_evidence(
    tool_name: str,
    args_factory,
    tmp_path: Path,
) -> None:
    relpath = f"reports/{tool_name}-sealed-target.txt"
    task = _build_sealed_task(tmp_path, relpath)

    error = SubtaskRunner._validate_sealed_artifact_mutation_policy(
        task=task,
        tool_name=tool_name,
        tool_args=args_factory(relpath),
        workspace=tmp_path,
        is_mutating_tool=True,
        mutation_target_arg_keys=(),
        prior_successful_tool_calls=[],
        current_tool_calls=[],
    )

    assert error is not None
    assert "Sealed artifact mutation blocked" in error


@pytest.mark.parametrize("tool_name,args_factory", PRE_FLIGHT_MUTATION_CASES)
def test_preflight_allows_all_mutating_tools_with_post_seal_evidence(
    tool_name: str,
    args_factory,
    tmp_path: Path,
) -> None:
    relpath = f"reports/{tool_name}-sealed-target.txt"
    task = _build_sealed_task(tmp_path, relpath)
    prior_calls = [
        ToolCallRecord(
            tool="read_file",
            args={"path": relpath},
            result=ToolResult.ok("fresh evidence"),
            timestamp="2026-03-05T10:15:00",
        ),
    ]

    error = SubtaskRunner._validate_sealed_artifact_mutation_policy(
        task=task,
        tool_name=tool_name,
        tool_args=args_factory(relpath),
        workspace=tmp_path,
        is_mutating_tool=True,
        mutation_target_arg_keys=(),
        prior_successful_tool_calls=prior_calls,
        current_tool_calls=[],
    )

    assert error is None


def test_spreadsheet_reseal_clears_stale_seal_mismatch(tmp_path: Path) -> None:
    relpath = "competitor-pricing.csv"
    artifact = tmp_path / relpath
    artifact.write_text("name,price\nA,10\n", encoding="utf-8")
    task = Task(id="task-1", goal="seal", workspace=str(tmp_path), metadata={})
    stub = _EvidenceStub(records=[])

    seed_calls = [
        ToolCallRecord(
            tool="write_file",
            args={"path": relpath, "content": artifact.read_text(encoding="utf-8")},
            result=ToolResult.ok("ok", files_changed=[relpath]),
            call_id="call-seed",
        ),
    ]
    assert orchestrator_evidence._record_artifact_seals(
        stub,
        task=task,
        subtask_id="seed",
        tool_calls=seed_calls,
    ) == 1

    artifact.write_text("name,price\nA,20\n", encoding="utf-8")
    passed_before, mismatches_before, validated_before = orchestrator_evidence._validate_artifact_seals(
        stub,
        task=task,
    )
    assert passed_before is False
    assert validated_before == 1
    assert mismatches_before[0]["reason"] == "artifact_seal_mismatch"

    spreadsheet_calls = [
        ToolCallRecord(
            tool="spreadsheet",
            args={"operation": "create", "path": relpath},
            result=ToolResult.ok("ok", files_changed=[relpath]),
            call_id="call-spreadsheet",
        ),
    ]
    assert orchestrator_evidence._record_artifact_seals(
        stub,
        task=task,
        subtask_id="spreadsheet",
        tool_calls=spreadsheet_calls,
    ) == 1

    passed_after, mismatches_after, validated_after = orchestrator_evidence._validate_artifact_seals(
        stub,
        task=task,
    )
    assert passed_after is True
    assert mismatches_after == []
    assert validated_after == 1


def test_reseal_is_tool_agnostic_for_output_path_mutations(tmp_path: Path) -> None:
    relpath = "reports/output.csv"
    artifact = tmp_path / relpath
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text("a,b\n1,2\n", encoding="utf-8")
    old_sha = hashlib.sha256(artifact.read_bytes()).hexdigest()

    task = Task(
        id="task-1",
        goal="reseal",
        workspace=str(tmp_path),
        metadata={
            "artifact_seals": {
                relpath: {
                    "path": relpath,
                    "sha256": old_sha,
                    "subtask_id": "s1",
                    "sealed_at": "2026-03-05T10:00:00",
                },
            },
            "validity_scorecard": {
                "subtask_metrics": {
                    "s1": {"verification_outcome": "pass"},
                },
            },
        },
    )

    artifact.write_text("a,b\n1,9\n", encoding="utf-8")

    updated = SubtaskRunner._reseal_tracked_artifacts_after_mutation(
        task=task,
        workspace=tmp_path,
        tool_name="custom_output_writer",
        tool_args={"output_path": relpath},
        tool_result=ToolResult.ok("ok", files_changed=[relpath]),
        is_mutating_tool=True,
        mutation_target_arg_keys=("output_path",),
        subtask_id="s2",
        tool_call_id="call-2",
    )

    assert updated == 1
    seal = task.metadata["artifact_seals"][relpath]
    assert seal["tool"] == "custom_output_writer"
    assert seal["sha256"] == hashlib.sha256(artifact.read_bytes()).hexdigest()
    assert seal["previous_sha256"] == old_sha
    assert seal["verified_origin"] is True


@pytest.mark.parametrize("tool_name", ["write_file", "document_write", "spreadsheet", "edit_file"])
def test_reseal_applies_for_each_workspace_mutation_tool(
    tool_name: str,
    tmp_path: Path,
) -> None:
    extension = "csv" if tool_name == "spreadsheet" else "md" if tool_name in {"document_write", "edit_file"} else "txt"
    relpath = f"reports/{tool_name}-tracked.{extension}"
    artifact = tmp_path / relpath
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text("alpha\n", encoding="utf-8")
    old_sha = hashlib.sha256(artifact.read_bytes()).hexdigest()

    task = Task(
        id="task-1",
        goal="reseal-matrix",
        workspace=str(tmp_path),
        metadata={
            "artifact_seals": {
                relpath: {
                    "path": relpath,
                    "sha256": old_sha,
                    "subtask_id": "s1",
                    "sealed_at": "2026-03-05T10:00:00",
                },
            },
            "validity_scorecard": {
                "subtask_metrics": {
                    "s1": {"verification_outcome": "pass"},
                },
            },
        },
    )

    if tool_name == "write_file":
        tool = WriteFileTool()
        args = {"path": relpath, "content": "beta\n"}
    elif tool_name == "document_write":
        tool = DocumentWriteTool()
        args = {"path": relpath, "title": "Updated", "content": "beta"}
    elif tool_name == "spreadsheet":
        tool = SpreadsheetTool()
        args = {
            "operation": "create",
            "path": relpath,
            "headers": ["col"],
            "rows": [["beta"]],
        }
    else:
        tool = EditFileTool()
        args = {"path": relpath, "old_str": "alpha", "new_str": "beta"}

    result = asyncio.run(tool.execute(args, ToolContext(workspace=tmp_path)))
    assert result.success, result.error
    assert relpath in result.files_changed

    updated = SubtaskRunner._reseal_tracked_artifacts_after_mutation(
        task=task,
        workspace=tmp_path,
        tool_name=tool_name,
        tool_args=args,
        tool_result=result,
        is_mutating_tool=True,
        mutation_target_arg_keys=(),
        subtask_id="s2",
        tool_call_id="call-2",
    )

    assert updated == 1
    seal = task.metadata["artifact_seals"][relpath]
    assert seal["tool"] == tool_name
    assert seal["sha256"] == hashlib.sha256((tmp_path / relpath).read_bytes()).hexdigest()
    assert seal["sha256"] != old_sha
    assert seal["previous_sha256"] == old_sha
    assert seal["verified_origin"] is True


def test_move_file_reseal_transfers_tracking_to_destination(tmp_path: Path) -> None:
    source = "reports/source.txt"
    destination = "reports/destination.txt"
    source_path = tmp_path / source
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_text("alpha\n", encoding="utf-8")
    source_sha = hashlib.sha256(source_path.read_bytes()).hexdigest()

    task = Task(
        id="task-1",
        goal="move-reseal",
        workspace=str(tmp_path),
        metadata={
            "artifact_seals": {
                source: {
                    "path": source,
                    "sha256": source_sha,
                    "subtask_id": "s1",
                    "sealed_at": "2026-03-05T10:00:00",
                },
            },
            "validity_scorecard": {
                "subtask_metrics": {
                    "s1": {"verification_outcome": "pass"},
                },
            },
        },
    )

    tool = MoveFileTool()
    args = {"source": source, "destination": destination}
    result = asyncio.run(tool.execute(args, ToolContext(workspace=tmp_path)))
    assert result.success, result.error

    updated = SubtaskRunner._reseal_tracked_artifacts_after_mutation(
        task=task,
        workspace=tmp_path,
        tool_name="move_file",
        tool_args=args,
        tool_result=result,
        is_mutating_tool=True,
        mutation_target_arg_keys=(),
        subtask_id="s2",
        tool_call_id="call-move",
    )

    assert updated == 2
    seals = task.metadata["artifact_seals"]
    assert source not in seals
    assert destination in seals
    destination_seal = seals[destination]
    assert destination_seal["tool"] == "move_file"
    assert destination_seal["verified_origin"] is True


def test_backfill_artifact_seals_is_tool_agnostic(tmp_path: Path) -> None:
    task = Task(id="task-1", goal="backfill", workspace=str(tmp_path), metadata={})
    stub = _EvidenceStub(
        records=[
            {
                "tool": "custom_output_writer",
                "artifact_workspace_relpath": "reports/summary.csv",
                "artifact_sha256": "abc123",
                "artifact_size_bytes": 12,
                "tool_call_id": "call-1",
                "subtask_id": "subtask-1",
                "created_at": "2026-03-07T00:00:00",
            },
        ],
    )

    updated = orchestrator_evidence._backfill_artifact_seals_from_evidence(stub, task)

    assert updated == 1
    assert task.metadata["artifact_seals"]["reports/summary.csv"]["tool"] == "custom_output_writer"


@pytest.mark.parametrize("mode", ["off", "warn", "enforce"])
def test_post_call_guard_mode_respects_supported_values(mode: str) -> None:
    runner_stub = SimpleNamespace(
        _sealed_artifact_post_call_guard=mode,
        SEALED_ARTIFACT_POST_CALL_GUARD="off",
    )
    assert SubtaskRunner._sealed_artifact_post_call_guard_mode(runner_stub) == mode


def test_post_call_guard_mode_invalid_falls_back_to_default_off() -> None:
    runner_stub = SimpleNamespace(
        _sealed_artifact_post_call_guard="mystery",
        SEALED_ARTIFACT_POST_CALL_GUARD="off",
    )
    assert SubtaskRunner._sealed_artifact_post_call_guard_mode(runner_stub) == "off"


def test_sealed_event_constants_are_stable() -> None:
    assert event_types.SEALED_POLICY_PREFLIGHT_BLOCKED == "sealed_policy_preflight_blocked"
    assert event_types.SEALED_RESEAL_APPLIED == "sealed_reseal_applied"
    assert (
        event_types.SEALED_UNEXPECTED_MUTATION_DETECTED
        == "sealed_unexpected_mutation_detected"
    )


def test_runner_and_orchestrator_telemetry_include_sealed_mutation_counters() -> None:
    runner_counters = runner_telemetry.new_subtask_telemetry_counters()
    orchestrator_rollup = orchestrator_telemetry.new_telemetry_rollup()

    for key in (
        "sealed_policy_preflight_blocked",
        "sealed_reseal_applied",
        "sealed_unexpected_mutation_detected",
    ):
        assert key in runner_counters
        assert key in orchestrator_rollup
        assert runner_counters[key] == 0
        assert orchestrator_rollup[key] == 0


def test_edit_file_regression_behavior_unchanged(tmp_path: Path) -> None:
    path = tmp_path / "analysis.md"
    path.write_text("alpha\nbeta\n", encoding="utf-8")

    tool = EditFileTool()
    result = asyncio.run(
        tool.execute(
            {
                "path": "analysis.md",
                "old_str": "beta",
                "new_str": "gamma",
            },
            ToolContext(workspace=tmp_path),
        ),
    )

    assert result.success, result.error
    assert result.files_changed == ["analysis.md"]
    assert path.read_text(encoding="utf-8") == "alpha\ngamma\n"
    assert "Edited analysis.md" in result.output
