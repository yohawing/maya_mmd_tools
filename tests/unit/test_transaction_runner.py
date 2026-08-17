"""Focused coverage for the Maya-independent authoring transaction runner."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import pytest

from mmd_tools.adapters.transaction_runner import TransactionFailure, TransactionRunner


def _runner(
    events: List[Any],
    *,
    fail_phase: str = "",
    phase_error: Optional[Exception] = None,
    rollback_error: Optional[Exception] = None,
    begin_error: Optional[Exception] = None,
    error_factory=None,
) -> TransactionRunner[str]:
    source_targets = ["|modelRoot", "|modelRoot|meshShape"]

    def begin(targets: Tuple[str, ...]) -> None:
        events.append(("begin", targets))
        if begin_error is not None:
            raise begin_error

    def mutate(targets: Tuple[str, ...]) -> str:
        events.append(("mutate", targets))
        if fail_phase == "mutate":
            raise phase_error or RuntimeError("mutate error")
        return "mutation-result"

    def validate(result: str, targets: Tuple[str, ...]) -> None:
        events.append(("validate", result, targets))
        if fail_phase == "validate":
            raise phase_error or RuntimeError("validate error")

    def verify_and_commit(result: str, targets: Tuple[str, ...]) -> None:
        events.append(("verify/commit", result, targets))
        if fail_phase == "verify":
            raise phase_error or RuntimeError("verify error")

    def rollback(targets: Tuple[str, ...]) -> None:
        events.append(("rollback", targets))
        if rollback_error is not None:
            raise rollback_error

    runner = TransactionRunner(
        "authoring operation",
        source_targets,
        begin=begin,
        mutate=mutate,
        validate_result=validate,
        verify_and_commit=verify_and_commit,
        rollback=rollback,
        error_factory=error_factory,
    )
    source_targets.append("caller mutation")
    return runner


def test_success_order_and_result() -> None:
    events: List[Any] = []
    runner = _runner(events)

    assert runner.run() == "mutation-result"
    assert [event[0] for event in events] == ["begin", "mutate", "validate", "verify/commit"]
    assert runner.started is True


def test_begin_failure_never_rolls_back_and_started_stays_false() -> None:
    events: List[Any] = []
    begin_error = RuntimeError("begin error")
    runner = _runner(events, begin_error=begin_error)

    with pytest.raises(TransactionFailure, match="begin error") as raised:
        runner.run()

    assert [event[0] for event in events] == ["begin"]
    assert raised.value.phase == "begin"
    assert raised.value.__cause__ is begin_error
    assert raised.value.original_error is begin_error
    assert runner.started is False


@pytest.mark.parametrize("failed_phase", ["mutate", "validate", "verify"])
def test_each_post_begin_failure_rolls_back_once(failed_phase: str) -> None:
    events: List[Any] = []
    phase_error = RuntimeError(f"{failed_phase} error")
    runner = _runner(events, fail_phase=failed_phase, phase_error=phase_error)

    with pytest.raises(TransactionFailure, match=f"{failed_phase} error") as raised:
        runner.run()

    assert [event[0] for event in events].count("rollback") == 1
    assert events[-1][0] == "rollback"
    assert raised.value.__cause__ is phase_error


def test_rollback_failure_preserves_original_and_rollback_errors() -> None:
    events: List[Any] = []
    rollback_error = RuntimeError("rollback error")
    runner = _runner(events, fail_phase="validate", rollback_error=rollback_error)

    with pytest.raises(TransactionFailure) as raised:
        runner.run()

    failure = raised.value
    assert "validate error" in str(failure)
    assert "rollback error" in str(failure)
    assert failure.original_error.args == ("validate error",)
    assert failure.rollback_error is rollback_error
    assert raised.value.__cause__ is rollback_error
    assert [event[0] for event in events].count("rollback") == 1


def test_target_identities_are_copied_and_visible_as_immutable_tuple() -> None:
    events: List[Any] = []
    runner = _runner(events)

    assert runner.targets == ("|modelRoot", "|modelRoot|meshShape")
    assert isinstance(runner.targets, tuple)
    runner.run()
    assert all(event[-1] is runner.targets for event in events)
    with pytest.raises(AttributeError):
        runner.targets.append("new target")  # type: ignore[attr-defined]
    with pytest.raises(AttributeError):
        runner.target_identities = ("replacement",)  # type: ignore[attr-defined]


def test_run_keeps_one_target_snapshot_when_attribute_changes_mid_run() -> None:
    seen: List[Tuple[str, ...]] = []
    holder: Dict[str, TransactionRunner[str]] = {}

    def begin(targets: Tuple[str, ...]) -> None:
        seen.append(targets)

    def mutate(targets: Tuple[str, ...]) -> str:
        seen.append(targets)
        holder["runner"]._target_identities = ("replacement",)
        return "result"

    def verify(result: str, targets: Tuple[str, ...]) -> None:
        seen.append(targets)

    runner = TransactionRunner(
        "snapshot operation",
        ("original",),
        begin=begin,
        mutate=mutate,
        verify_and_commit=verify,
        rollback=lambda targets: seen.append(targets),
    )
    holder["runner"] = runner

    assert runner.run() == "result"
    assert seen[0] is seen[1] is seen[2]
    assert seen[0] == ("original",)


def test_native_owned_callbacks_do_not_open_or_require_python_undo() -> None:
    events: List[str] = []

    runner = TransactionRunner(
        "native operation",
        ("native-node",),
        begin=lambda targets: events.append("native begin"),
        mutate=lambda targets: events.append("native mutate") or None,
        verify_and_commit=lambda result, targets: events.append("native commit"),
        rollback=lambda targets: events.append("native rollback"),
    )

    runner.run()

    assert events == ["native begin", "native mutate", "native commit"]
    assert not hasattr(runner, "open_undo")
    assert not hasattr(runner, "undo")


def test_runner_exposes_no_scene_or_read_spec_surface() -> None:
    runner = TransactionRunner(
        "surface check",
        (),
        begin=lambda targets: None,
        mutate=lambda targets: None,
        verify_and_commit=lambda result, targets: None,
        rollback=lambda targets: None,
    )

    assert not hasattr(runner, "scene")
    assert not hasattr(runner, "read_spec")


def test_caller_error_factory_can_preserve_specific_exception_type() -> None:
    class CallerError(RuntimeError):
        pass

    events: List[Any] = []
    runner = _runner(
        events,
        fail_phase="verify",
        error_factory=lambda failure: CallerError(str(failure)),
    )

    with pytest.raises(CallerError, match="verify error"):
        runner.run()


def test_error_factory_can_return_original_error_without_self_cause() -> None:
    original = RuntimeError("original")
    runner = _runner([], begin_error=original, error_factory=lambda failure: failure.original_error)

    with pytest.raises(RuntimeError, match="original") as raised:
        runner.run()

    assert raised.value is original
    assert raised.value.__cause__ is None


def test_failed_run_can_be_retried_without_duplicate_rollback() -> None:
    events: List[Any] = []
    mutate_calls = 0

    def mutate(targets: Tuple[str, ...]) -> str:
        nonlocal mutate_calls
        mutate_calls += 1
        events.append(("mutate", targets))
        if mutate_calls <= 2:
            raise RuntimeError(f"run {mutate_calls} failed")
        return "retry-result"

    runner = TransactionRunner(
        "retry operation",
        ("target",),
        begin=lambda targets: events.append(("begin", targets)),
        mutate=mutate,
        verify_and_commit=lambda result, targets: events.append(("verify/commit", result, targets)),
        rollback=lambda targets: events.append(("rollback", targets)),
    )

    with pytest.raises(TransactionFailure, match="run 1 failed"):
        runner.run()
    assert [event[0] for event in events].count("rollback") == 1

    with pytest.raises(TransactionFailure, match="run 2 failed"):
        runner.run()
    assert [event[0] for event in events].count("rollback") == 2

    assert runner.run() == "retry-result"
    assert [event[0] for event in events].count("rollback") == 2
    assert runner.started is True


def test_begin_failure_on_next_run_resets_started_without_rollback() -> None:
    events: List[Any] = []
    begin_calls = 0
    begin_error = RuntimeError("next begin failed")

    def begin(targets: Tuple[str, ...]) -> None:
        nonlocal begin_calls
        begin_calls += 1
        events.append(("begin", targets))
        if begin_calls == 2:
            raise begin_error

    runner = TransactionRunner(
        "rerun begin operation",
        ("target",),
        begin=begin,
        mutate=lambda targets: "result",
        verify_and_commit=lambda result, targets: events.append(("verify/commit", result, targets)),
        rollback=lambda targets: events.append(("rollback", targets)),
    )

    assert runner.run() == "result"
    assert runner.started is True

    with pytest.raises(TransactionFailure, match="next begin failed") as raised:
        runner.run()

    assert raised.value.__cause__ is begin_error
    assert runner.started is False
    assert [event[0] for event in events].count("rollback") == 0
