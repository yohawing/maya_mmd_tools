"""Transactional Maya timeline pre-roll for the stateful MMD physics solver.

Physics enable uses the scene physics world's saved ``startFrame`` as the
authority.  The runner evaluates the target model one Maya frame at a time up
to the user's current time while preserving the surrounding Maya interaction
state.  It deliberately does not own UI policy; callers decide how failures
and cancellation are presented.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Callable, Optional, Sequence

from maya import cmds


DEFAULT_MAX_PREROLL_STEPS = 18_000
PROGRESS_WINDOW_THRESHOLD = 120
_TIME_EPSILON = 1.0e-6

PREROLL_CANCELLED = "physics_preroll_cancelled"
PREROLL_CURRENT_BEFORE_START = "physics_preroll_current_before_start"
PREROLL_EVALUATION_FAILED = "physics_preroll_evaluation_failed"
PREROLL_INVALID_RANGE = "physics_preroll_invalid_range"
PREROLL_RANGE_EXCEEDS_LIMIT = "physics_preroll_range_exceeds_limit"
PREROLL_RESTORE_FAILED = "physics_preroll_restore_failed"
PREROLL_SCOPE_MISMATCH = "physics_preroll_scope_mismatch"


class PhysicsPrerollError(RuntimeError):
    """Fail-closed pre-roll error with a stable machine-readable code."""

    def __init__(self, reason_code: str, detail: str = ""):
        self.reason_code = reason_code
        message = reason_code if not detail else f"{reason_code}: {detail}"
        super().__init__(message)


@dataclass(frozen=True)
class PhysicsPrerollResult:
    """Successful pre-roll summary for diagnostics and tests."""

    start_frame: float
    target_frame: float
    step_count: int
    solvers: tuple[str, ...]


@dataclass
class _SceneSnapshot:
    current_time: float
    playback: bool
    selection: tuple[str, ...]
    evaluation_modes: tuple[str, ...]
    world_enable: bool
    reset_generation: int
    solver_enable: dict[str, bool]


ProgressCallback = Callable[[int, int, float], bool]


def _long_nodes(nodes: Sequence[str], maya_cmds=cmds) -> list[str]:
    result = []
    for node in nodes:
        matches = maya_cmds.ls(node, long=True) or [node]
        result.append(str(matches[0]))
    return list(dict.fromkeys(result))


def _world_solvers(world: str, maya_cmds=cmds) -> list[str]:
    world_long = _long_nodes([world], maya_cmds)[0]
    connected = []
    for plug_name in ("message", "outSettingsVersion"):
        try:
            connected.extend(maya_cmds.listConnections(
                f"{world}.{plug_name}",
                source=False,
                destination=True,
            ) or [])
        except Exception:
            pass
    solvers = []
    candidates = list(dict.fromkeys(connected + (maya_cmds.ls(type="mmdPhysicsSolver") or [])))
    for node in candidates:
        try:
            if maya_cmds.nodeType(node) != "mmdPhysicsSolver":
                continue
        except Exception:
            # Lightweight command doubles do not need to implement nodeType;
            # the target/world scope check still protects the production path.
            pass
        sources = maya_cmds.listConnections(
            f"{node}.inWorldSettings",
            source=True,
            destination=False,
        ) or []
        if not sources:
            continue
        source_long = _long_nodes([sources[0]], maya_cmds)[0]
        if source_long == world_long:
            solvers.append(node)
    return _long_nodes(solvers, maya_cmds)


def _solver_uses_world(solver: str, world: str, maya_cmds=cmds) -> bool:
    """Validate a solver's direct world-settings source without global scans."""
    sources = []
    try:
        source_plug = maya_cmds.connectionInfo(
            f"{solver}.inWorldSettings",
            sourceFromDestination=True,
        )
        if source_plug:
            sources.append(str(source_plug).rsplit(".", 1)[0])
    except Exception:
        pass
    if not sources:
        sources = maya_cmds.listConnections(
            f"{solver}.inWorldSettings",
            source=True,
            destination=False,
        ) or []
    if not sources:
        return False
    world_long = _long_nodes([world], maya_cmds)[0]
    source_longs = _long_nodes(sources, maya_cmds)
    if world_long in source_longs:
        return True
    world_leaf = world_long.rsplit("|", 1)[-1]
    return any(source.rsplit("|", 1)[-1] == world_leaf for source in source_longs)


def _sample_times(start_frame: float, target_frame: float, max_steps: int) -> list[float]:
    if not math.isfinite(start_frame) or not math.isfinite(target_frame):
        raise PhysicsPrerollError(PREROLL_INVALID_RANGE, "start/target must be finite")
    if target_frame < start_frame - _TIME_EPSILON:
        raise PhysicsPrerollError(
            PREROLL_CURRENT_BEFORE_START,
            f"start={start_frame} target={target_frame}",
        )
    if max_steps < 0:
        raise PhysicsPrerollError(PREROLL_INVALID_RANGE, f"max_steps={max_steps}")

    distance = max(0.0, target_frame - start_frame)
    whole_steps = int(math.floor(distance + _TIME_EPSILON))
    samples = [start_frame + float(index) for index in range(whole_steps + 1)]
    if not samples:
        samples = [start_frame]
    if samples[-1] < target_frame - _TIME_EPSILON:
        samples.append(target_frame)
    else:
        samples[-1] = target_frame

    step_count = max(0, len(samples) - 1)
    if step_count > max_steps:
        raise PhysicsPrerollError(
            PREROLL_RANGE_EXCEEDS_LIMIT,
            f"steps={step_count} limit={max_steps}",
        )
    return samples


def _capture_scene(world: str, solvers: Sequence[str], maya_cmds=cmds) -> _SceneSnapshot:
    evaluation_modes = maya_cmds.evaluationManager(query=True, mode=True) or []
    return _SceneSnapshot(
        current_time=float(maya_cmds.currentTime(query=True)),
        playback=bool(maya_cmds.play(query=True, state=True)),
        selection=tuple(maya_cmds.ls(selection=True, long=True) or []),
        evaluation_modes=tuple(str(mode) for mode in evaluation_modes),
        world_enable=bool(maya_cmds.getAttr(f"{world}.enable")),
        reset_generation=int(maya_cmds.getAttr(f"{world}.resetGeneration")),
        solver_enable={
            solver: bool(maya_cmds.getAttr(f"{solver}.enable"))
            for solver in solvers
        },
    )


def _restore_interaction(snapshot: _SceneSnapshot, maya_cmds=cmds) -> list[str]:
    """Restore non-physics Maya interaction state after success or rollback."""
    errors = []
    try:
        maya_cmds.currentTime(snapshot.current_time, edit=True)
    except Exception as exc:
        errors.append(f"currentTime: {exc}")
    try:
        current_modes = tuple(maya_cmds.evaluationManager(query=True, mode=True) or [])
        if snapshot.evaluation_modes and current_modes != snapshot.evaluation_modes:
            maya_cmds.evaluationManager(mode=snapshot.evaluation_modes[0])
    except Exception as exc:
        errors.append(f"evaluationMode: {exc}")
    try:
        if snapshot.selection:
            maya_cmds.select(list(snapshot.selection), replace=True)
        else:
            maya_cmds.select(clear=True)
    except Exception as exc:
        errors.append(f"selection: {exc}")
    try:
        maya_cmds.play(state=snapshot.playback)
    except Exception as exc:
        errors.append(f"playback: {exc}")
    return errors


def _invalidate_solver_runtime(solvers: Sequence[str], maya_cmds=cmds) -> None:
    """Discard mutated native state after a failed or cancelled pre-roll."""
    try:
        import maya.api.OpenMaya as om
    except Exception:
        om = None

    for solver in solvers:
        if om is not None:
            try:
                selection = om.MSelectionList()
                selection.add(solver)
                user_node = om.MFnDependencyNode(selection.getDependNode(0)).userNode()
                free_handles = getattr(user_node, "_free_handles", None)
                if callable(free_handles):
                    free_handles()
            except Exception:
                pass
        try:
            maya_cmds.dgdirty(solver, allPlugs=True)
        except Exception:
            pass


def _rollback_physics(
    world: str,
    snapshot: _SceneSnapshot,
    target_solvers: Sequence[str],
    maya_cmds=cmds,
) -> list[str]:
    """Restore scene attributes and discard native state mutated by pre-roll."""
    errors = []
    try:
        maya_cmds.setAttr(f"{world}.enable", snapshot.world_enable)
    except Exception as exc:
        errors.append(f"world.enable: {exc}")
    try:
        maya_cmds.setAttr(f"{world}.resetGeneration", snapshot.reset_generation)
    except Exception as exc:
        errors.append(f"world.resetGeneration: {exc}")
    for solver, enabled in snapshot.solver_enable.items():
        try:
            maya_cmds.setAttr(f"{solver}.enable", enabled)
        except Exception as exc:
            errors.append(f"{solver}.enable: {exc}")
    _invalidate_solver_runtime(target_solvers, maya_cmds)
    return errors


class _ProgressWindow:
    """Best-effort Maya progress UI with cancellation polling."""

    def __init__(self, total: int, maya_cmds=cmds):
        self.total = total
        self.cmds = maya_cmds
        self.active = False

    def __enter__(self):
        if self.total < PROGRESS_WINDOW_THRESHOLD:
            return self
        try:
            self.cmds.progressWindow(
                title="MMD Physics Pre-roll",
                progress=0,
                maxValue=max(1, self.total),
                status="Preparing physics...",
                isInterruptable=True,
            )
            self.active = True
        except Exception:
            self.active = False
        return self

    def update(self, completed: int, frame: float) -> bool:
        if not self.active:
            return True
        try:
            if self.cmds.progressWindow(query=True, isCancelled=True):
                return False
            self.cmds.progressWindow(
                edit=True,
                progress=min(completed, self.total),
                status=f"Physics pre-roll frame {frame:g}",
            )
        except Exception:
            return True
        return True

    def __exit__(self, _exc_type, _exc, _traceback):
        if self.active:
            try:
                self.cmds.progressWindow(endProgress=True)
            except Exception:
                pass


def run_physics_preroll(
    world: str,
    target_solvers: Sequence[str],
    *,
    max_steps: int = DEFAULT_MAX_PREROLL_STEPS,
    progress_callback: Optional[ProgressCallback] = None,
    maya_cmds=cmds,
) -> PhysicsPrerollResult:
    """Enable and pre-roll one model's solvers from saved start to current time.

    The operation is transactional.  Failure and cancellation restore the
    world's enable/reset values, every connected solver's enable value, and
    Maya interaction state, then discard mutated target runtime handles.
    """
    try:
        world_long = _long_nodes([world], maya_cmds)[0]
        all_solvers = _world_solvers(world_long, maya_cmds)
        targets = _long_nodes(target_solvers, maya_cmds)
        invalid_targets = [
            solver
            for solver in targets
            if solver not in all_solvers
            and not _solver_uses_world(solver, world_long, maya_cmds)
        ]
        if not targets or invalid_targets:
            raise PhysicsPrerollError(
                PREROLL_SCOPE_MISMATCH,
                f"targets={targets} invalidTargets={invalid_targets} worldSolvers={all_solvers}",
            )
        all_solvers = list(dict.fromkeys(all_solvers + targets))

        snapshot = _capture_scene(world_long, all_solvers, maya_cmds)
        start_frame = float(maya_cmds.getAttr(f"{world_long}.startFrame"))
        samples = _sample_times(start_frame, snapshot.current_time, max_steps)
        active_targets = [
            solver
            for solver in targets
            if snapshot.solver_enable.get(solver, False)
        ]
        if not active_targets:
            raise PhysicsPrerollError(PREROLL_SCOPE_MISMATCH, "target solvers are disabled")
    except PhysicsPrerollError:
        raise
    except Exception as exc:
        raise PhysicsPrerollError(
            PREROLL_EVALUATION_FAILED,
            f"preflight: {exc}",
        ) from exc

    if snapshot.playback:
        maya_cmds.play(state=False)

    succeeded = False
    try:
        # Prevent foreign models sharing the scene world from advancing while
        # this model owns the pre-roll transaction.
        for solver in all_solvers:
            if solver not in active_targets and snapshot.solver_enable[solver]:
                maya_cmds.setAttr(f"{solver}.enable", False)

        # Move to the authoritative start while physics is still OFF.  Enabling
        # at the target frame can cause Maya to pull the solver once there
        # before the backward seek, contaminating the supposedly fresh reset.
        maya_cmds.currentTime(samples[0], edit=True)
        # Re-enable must start from the same native world construction state as
        # first enable.  Bullet reset alone can retain history in an existing
        # handle, so release only the target model's runtime before rebuilding.
        _invalidate_solver_runtime(active_targets, maya_cmds)
        maya_cmds.setAttr(f"{world_long}.resetGeneration", snapshot.reset_generation + 1)
        maya_cmds.setAttr(f"{world_long}.enable", True)

        step_total = max(0, len(samples) - 1)
        with _ProgressWindow(step_total, maya_cmds) as progress:
            for sample_index, frame in enumerate(samples):
                maya_cmds.currentTime(frame, edit=True)
                for solver in active_targets:
                    solved = bool(maya_cmds.getAttr(f"{solver}.outSolved"))
                    if not solved:
                        status = maya_cmds.getAttr(f"{solver}.outStatus")
                        raise PhysicsPrerollError(
                            PREROLL_EVALUATION_FAILED,
                            f"solver={solver} frame={frame} status={status}",
                        )
                completed = min(sample_index, step_total)
                if progress_callback is not None:
                    keep_going = bool(progress_callback(completed, step_total, frame))
                else:
                    keep_going = progress.update(completed, frame)
                if not keep_going:
                    raise PhysicsPrerollError(PREROLL_CANCELLED, f"frame={frame}")

        for solver, enabled in snapshot.solver_enable.items():
            if solver not in active_targets:
                maya_cmds.setAttr(f"{solver}.enable", enabled)
        succeeded = True
        return PhysicsPrerollResult(
            start_frame=start_frame,
            target_frame=snapshot.current_time,
            step_count=max(0, len(samples) - 1),
            solvers=tuple(active_targets),
        )
    except PhysicsPrerollError:
        raise
    except Exception as exc:
        raise PhysicsPrerollError(PREROLL_EVALUATION_FAILED, str(exc)) from exc
    finally:
        restore_errors = []
        if not succeeded:
            restore_errors.extend(
                _rollback_physics(world_long, snapshot, active_targets, maya_cmds)
            )
        interaction_errors = _restore_interaction(snapshot, maya_cmds)
        if interaction_errors and succeeded:
            # A successful simulation is not a successful transaction unless
            # Maya interaction state also restores.  Roll physics back before
            # reporting the fail-closed restoration error.
            restore_errors.extend(
                _rollback_physics(world_long, snapshot, active_targets, maya_cmds)
            )
        restore_errors.extend(interaction_errors)
        if restore_errors:
            raise PhysicsPrerollError(
                PREROLL_RESTORE_FAILED,
                "; ".join(restore_errors),
            )
