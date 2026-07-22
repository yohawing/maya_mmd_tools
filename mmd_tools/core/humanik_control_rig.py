"""Transactional wrapper around HumanIK Control Rig creation.

``HUMANIK-CONTROL-RIG-CYCLE-1`` (see ``TODO.md``) found that calling
``hikCreateControlRig()`` directly on a characterized MMD model creates a DG
cycle: MMD ``mmdAppend``/``mmdCcdIk`` writers still feed the HIK-assigned
primary joints, so the new ``HIKState2SK -> pairBlend -> joint -> ... ->
joint`` chain Maya wires up closes a loop through those writers
(``HIKState2SK.LeftLegT`` / ``pairBlend.outTranslateX`` / joint ``translate``
and ``parentMatrix`` in the reported evidence,
``build/reports/humanik_control_rig_cycle_e2e.json``).

``humanik_preview.begin_humanik_target_preview`` already solves the
equivalent problem for TARGET preview by capturing restore state scene state, isolating
reviewed MMD writers, connecting the HIK source, and re-scanning/re-isolating
writers that reappeared. This module applies the identical machinery around
``hikCreateControlRig()`` -- Transaction candidate A from the TODO:

    restore_state -> isolate MMD writers -> pre-cycle gate -> hikCreateControlRig
    -> re-scan/re-isolate writers -> post-cycle gate

Any failure after the restore_state is captured rolls the scene back: MMD writer
edges are reconnected, plug values/node state/HIK source+lock are restored
from the restore_state, and any control-rig nodes ``hikCreateControlRig`` created
are removed via HIK's own ``hikDeleteControlRig()`` MEL command (never a bare
``cmds.delete`` first -- see ``_delete_control_rig`` for why a node-diff
delete is only a fallback).

The same transaction owns the public ``bake_humanik_control_rig`` route. It
invokes Maya's supported ``hikBakeToControlRig(0)`` over a caller-supplied
frame range while preserving playback/current-time state; baking leaves the
Control Rig active for interactive editing until the normal restore path.

Cycle-gate scoping: ``cmds.cycleCheck`` only supports scene-wide queries, so
this module captures a baseline cycle-plug set *before* mutating (after
writer isolation, right before ``hikCreateControlRig``) and only fails on
cycle plugs that are new relative to that baseline. This keeps a pre-existing,
unrelated cycle -- such as the known ``MMD-PHYSICS-SOLVER-CYCLE-1``
``mmdPhysicsSolver`` warning present in the Kokomi scene used for evidence
capture -- from ever blocking Control Rig creation or masquerading as an
HIK-caused regression.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Set

from mmd_tools.core.humanik_builder import create_humanik_control_rig, ensure_humanik_mel_loaded
from mmd_tools.core.humanik_constraints import (
    BLOCKING_CLASSIFICATIONS,
    collect_hik_ownership_report,
    is_supported_mmd_ccdik_feedback_row,
    split_ownership_rows,
)
from mmd_tools.core.humanik_preview import (
    HumanIkTargetPreview,
    disconnect_residual_muted_writers,
    disconnect_reviewed_writers,
    re_isolate_reviewed_edges,
    row_hik_writes,
)
from mmd_tools.core.humanik_transaction import (
    HumanIkRestoreState,
    capture_humanik_restore_state,
    deserialize_humanik_restore_state,
    apply_humanik_restore_state,
)
from mmd_tools.core.humanik_utils import maya_cmds, maya_mel, mel_string
from mmd_tools.core.logger import get_logger


logger = get_logger(__name__)


@dataclass
class HumanIkControlRigTransaction:
    """Active Control Rig created through the transactional path.

    ``pre_cycle_baseline``/``post_cycle_plugs`` are retained for diagnostics
    even though only their set difference (see ``new_cycle_plugs``) gates
    success; keeping both lets callers show the pre-existing/unrelated cycle
    plugs (for example a physics solver warning) alongside the HIK-caused set.
    """

    ownership_id: str
    character: str
    restore_state: HumanIkRestoreState
    disconnected: List[Dict[str, str]]
    retained_nodes: List[str]
    created_nodes: List[str]
    isolated_feedback_nodes: List[str] = field(default_factory=list)
    pre_cycle_baseline: List[str] = field(default_factory=list)
    post_cycle_plugs: List[str] = field(default_factory=list)
    preview: Optional[HumanIkTargetPreview] = None
    active: bool = True
    baked: bool = False

    def to_dict(self) -> Dict[str, Any]:
        """Return the JSON-safe control-rig transaction diagnostic payload."""
        return {
            "ownershipId": self.ownership_id,
            "character": self.character,
            "disconnected": list(self.disconnected),
            "retainedNodes": list(self.retained_nodes),
            "createdNodes": list(self.created_nodes),
            "isolatedFeedbackNodes": list(self.isolated_feedback_nodes),
            "preCycleBaseline": list(self.pre_cycle_baseline),
            "postCyclePlugs": list(self.post_cycle_plugs),
            "preview": self.preview.to_dict() if self.preview is not None else None,
            "active": self.active,
            "baked": bool(self.baked),
        }

    def to_scene_dict(self, model_root: str) -> Dict[str, Any]:
        """Return the minimal active transaction facts persisted in a scene."""
        return {
            "modelRoot": str(model_root),
            "ownershipId": self.ownership_id,
            "character": self.character,
            "restore_state": self.restore_state.to_dict(),
            "disconnected": list(self.disconnected),
            "retainedNodes": list(self.retained_nodes),
            "createdNodes": list(self.created_nodes),
            "isolatedFeedbackNodes": list(self.isolated_feedback_nodes),
            "preCycleBaseline": list(self.pre_cycle_baseline),
            "postCyclePlugs": list(self.post_cycle_plugs),
            "preview": (
                self.preview.to_scene_dict() if self.preview is not None else None
            ),
            "active": bool(self.active),
            "baked": bool(self.baked),
        }

    @classmethod
    def from_scene_dict(cls, payload: Dict[str, Any]) -> "HumanIkControlRigTransaction":
        """Reconstruct a transaction row loaded from scene metadata."""
        rows = deserialize_humanik_restore_state({
            "schema": "mmd_tools.humanik_restore_state",
            "version": 1,
            "transactions": [payload],
        })
        row = rows[0]
        restore_state = HumanIkRestoreState.from_dict(row["restore_state"])
        def _string_list(key: str) -> List[str]:
            value = row.get(key, [])
            if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                raise ValueError(f"HumanIK transaction {key} must be an array of strings")
            return list(value)
        disconnected = row.get("disconnected", [])
        if not isinstance(disconnected, list) or not all(isinstance(item, dict) for item in disconnected):
            raise ValueError("HumanIK transaction disconnected must be an array")
        preview_payload = row.get("preview")
        preview = (
            HumanIkTargetPreview.from_scene_dict(preview_payload)
            if preview_payload is not None
            else None
        )
        if preview is not None and preview.target_character != row["character"]:
            raise ValueError("HumanIK transaction preview character mismatch")
        baked = row.get("baked", False)
        if not isinstance(baked, bool):
            raise ValueError("HumanIK transaction baked must be a boolean")
        return cls(
            ownership_id=row["ownershipId"],
            character=row["character"],
            restore_state=restore_state,
            disconnected=[dict(item) for item in disconnected],
            retained_nodes=_string_list("retainedNodes"),
            created_nodes=_string_list("createdNodes"),
            isolated_feedback_nodes=_string_list("isolatedFeedbackNodes"),
            pre_cycle_baseline=_string_list("preCycleBaseline"),
            post_cycle_plugs=_string_list("postCyclePlugs"),
            preview=preview,
            active=bool(row.get("active", True)),
            baked=baked,
        )


@dataclass(frozen=True)
class HumanIkControlRigBakeResult:
    """Describe a native HumanIK bake onto an existing Control Rig.

    ``hikBakeToControlRig`` keys the currently active HIK character and then
    switches that character's input to the Control Rig.  The transaction is
    intentionally kept active after this operation so the caller can continue
    editing the Control Rig and later use the normal restore path.
    """

    character: str
    start: int
    end: int
    command: str = "hikBakeToControlRig(0);"

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-safe result for UI/menu diagnostics."""
        return {
            "character": self.character,
            "start": self.start,
            "end": self.end,
            "command": self.command,
        }


# Module-level registry of active Control Rig transactions, keyed by HIK
# character name. ``begin_humanik_control_rig`` (plugin/frontend path)
# registers its transaction here so ``humanik_control_rig_watch`` can cheaply
# answer "does this character already have an active, writer-isolated
# Control Rig transaction?" before deciding whether to warn about a Control
# Rig it sees appear out of band (Maya's standard HumanIK UI, or a raw
# ``hikCreateControlRig()`` MEL call) -- the watch is warn-only and never
# registers a transaction of its own. ``stop_humanik_control_rig`` always
# unregisters.
_ACTIVE_TRANSACTIONS_BY_CHARACTER: Dict[str, "HumanIkControlRigTransaction"] = {}


def register_control_rig_transaction(
    character: str, transaction: "HumanIkControlRigTransaction"
) -> None:
    """Record ``transaction`` as the active Control Rig transaction for ``character``."""
    _ACTIVE_TRANSACTIONS_BY_CHARACTER[str(character)] = transaction


def unregister_control_rig_transaction(character: str) -> None:
    """Remove any registered Control Rig transaction for ``character``."""
    _ACTIVE_TRANSACTIONS_BY_CHARACTER.pop(str(character), None)


def get_active_control_rig_transaction(
    character: str,
) -> Optional["HumanIkControlRigTransaction"]:
    """Return the active registered transaction for ``character``, or ``None``.

    A registered-but-inactive transaction (already stopped) is pruned and
    treated as absent.
    """
    transaction = _ACTIVE_TRANSACTIONS_BY_CHARACTER.get(str(character))
    if transaction is not None and not transaction.active:
        _ACTIVE_TRANSACTIONS_BY_CHARACTER.pop(str(character), None)
        return None
    return transaction


def detect_dg_cycles(cmds_module=None) -> List[str]:
    """Return the sorted plugs Maya's ``cycleCheck`` currently reports.

    ``cmds.cycleCheck`` has no node/plug scoping option, so this is always a
    scene-wide snapshot. Callers scope it themselves by diffing a
    before/after pair with :func:`new_cycle_plugs` rather than asking this
    function to filter by reachability -- see the module docstring for why.
    """
    cmds = cmds_module or maya_cmds()
    return sorted(str(plug) for plug in (cmds.cycleCheck(all=True, list=True) or []))


def new_cycle_plugs(baseline: Iterable[str], current: Iterable[str]) -> List[str]:
    """Return cycle plugs present in ``current`` but absent from ``baseline``."""
    return sorted(set(str(plug) for plug in current) - set(str(plug) for plug in baseline))


def begin_humanik_control_rig(
    ownership_id: str,
    character: str,
    hik_joints: Iterable[str],
    cmds_module=None,
    mel_module=None,
    assignments: Optional[Iterable[Any]] = None,
) -> HumanIkControlRigTransaction:
    """Create a Control Rig with MMD writer isolation and DG-cycle gating.

    Fail-closed session-level preconditions (SOURCE role, another active
    TARGET preview, an already-active control rig transaction) are the
    caller's responsibility -- see
    ``HumanIkFrontendSession.create_control_rig`` -- so a rejected request
    never reaches this function and never mutates the scene. This function
    still independently re-derives ownership from scene facts and refuses to
    mutate on any blocker classification, so direct/non-frontend callers
    (Maya's standard HumanIK UI going through the same helper, or a future
    command adapter) get the same fail-closed guarantee.

    Args:
        ownership_id: RestoreState owner id, distinct from any active preview's id
            so the two transactions can never cross-restore each other.
        character: Already characterized and locked HIK character name.
        hik_joints: The character's HIK-assigned primary joints (long paths).
        cmds_module: Optional Maya ``cmds`` compatible module for tests.
        mel_module: Optional Maya ``mel`` compatible module for tests.
        assignments: Optional HIK slot assignments used to recognize the
            narrowly supported importer-created leg/toe ``mmdCcdIk`` graph.
            Without assignments, feedback blockers remain fail-closed.

    Returns:
        The active :class:`HumanIkControlRigTransaction`.

    Raises:
        RuntimeError: On a pre-existing blocker, a post-scan residual muted
            writer, a post-scan blocker, or a new DG cycle relative to the
            pre-mutation baseline. The scene is rolled back to its
            pre-mutation state (writers reconnected, restore_state restored, any
            newly created control-rig nodes removed) before re-raising.
    """
    cmds = cmds_module or maya_cmds()
    mel = mel_module or maya_mel()
    ensure_humanik_mel_loaded(mel)
    hik_joint_set = {str(joint) for joint in hik_joints}

    ownership_report = collect_hik_ownership_report(hik_joint_set, cmds_module=cmds)
    blockers, mute_rows, retained_nodes = split_ownership_rows(ownership_report)
    supported_feedback = [
        row
        for row in ownership_report.get("rows", [])
        if is_supported_mmd_ccdik_feedback_row(row, assignments or ())
    ]
    blockers = [row for row in blockers if row not in supported_feedback]
    if blockers:
        labels = ", ".join(f"{row['node']}:{row['classification']}" for row in blockers)
        raise RuntimeError(f"HumanIK Control Rig creation blocked: {labels}")

    isolated_rows = [*mute_rows, *supported_feedback]
    destinations = sorted({plug for row in isolated_rows for plug in row.get("writes", [])})
    muted_nodes = sorted({row["node"] for row in mute_rows})
    feedback_nodes = sorted({row["node"] for row in supported_feedback})

    restore_state = capture_humanik_restore_state(
        ownership_id,
        character,
        destinations,
        muted_nodes,
        cmds_module=cmds,
        mel_module=mel,
    )
    disconnected: List[Dict[str, str]] = []
    created_nodes: List[str] = []
    pre_cycle_baseline: List[str] = []
    try:
        disconnect_reviewed_writers(cmds, isolated_rows, disconnected)

        pre_cycle_baseline = detect_dg_cycles(cmds)

        before_nodes = _snapshot_scene_nodes(cmds)
        create_humanik_control_rig(character, mel_module=mel)
        after_nodes = _snapshot_scene_nodes(cmds)
        created_nodes = sorted(after_nodes - before_nodes)

        re_isolate_reviewed_edges(cmds, disconnected)

        post_report = collect_hik_ownership_report(hik_joint_set, cmds_module=cmds)
        isolated_nodes = set(muted_nodes) | set(feedback_nodes)
        residual_muted_writers = [
            row
            for row in post_report["rows"]
            if row["node"] in isolated_nodes and row_hik_writes(row, hik_joint_set)
        ]
        if residual_muted_writers:
            labels = ", ".join(
                f"{row['node']}->{','.join(sorted(row_hik_writes(row, hik_joint_set)))}"
                for row in sorted(residual_muted_writers, key=lambda item: item["node"])
            )
            disconnect_residual_muted_writers(cmds, residual_muted_writers, hik_joint_set)
            raise RuntimeError(
                "HumanIK Control Rig post-scan found residual muted HIK writers: "
                f"{labels}"
            )
        post_blockers = [
            row for row in post_report["rows"]
            if row["node"] not in muted_nodes
            and row["node"] not in feedback_nodes
            and row["classification"] in BLOCKING_CLASSIFICATIONS
        ]
        if post_blockers:
            raise RuntimeError("HumanIK Control Rig post-scan found blocker")

        post_cycle_plugs = detect_dg_cycles(cmds)
        regressed = new_cycle_plugs(pre_cycle_baseline, post_cycle_plugs)
        if regressed:
            raise RuntimeError(
                "HumanIK Control Rig creation introduced new DG cycles: "
                f"{regressed}"
            )
    except Exception as error:
        _rollback(
            error,
            character=character,
            created_nodes=created_nodes,
            restore_state=restore_state,
            ownership_id=ownership_id,
            cmds=cmds,
            mel=mel,
        )
        raise
    transaction = HumanIkControlRigTransaction(
        ownership_id=ownership_id,
        character=character,
        restore_state=restore_state,
        disconnected=sorted(disconnected, key=lambda row: (row["destination"], row["source"])),
        retained_nodes=retained_nodes,
        created_nodes=created_nodes,
        isolated_feedback_nodes=feedback_nodes,
        pre_cycle_baseline=pre_cycle_baseline,
        post_cycle_plugs=post_cycle_plugs,
    )
    register_control_rig_transaction(character, transaction)
    return transaction


def bake_humanik_control_rig(
    transaction: HumanIkControlRigTransaction,
    start: int,
    end: int,
    cmds_module=None,
    mel_module=None,
) -> HumanIkControlRigBakeResult:
    """Bake the active HIK retarget onto an existing Control Rig.

    Maya's supported ``hikBakeToControlRig(0)`` command delegates its range
    selection to the current playback range.  This wrapper temporarily sets
    both playback and animation ranges to the requested integer interval,
    runs that native command, and restores the user's original range/current
    time/current HIK character even when Maya raises.  The Control Rig
    transaction deliberately remains active: the command's post hook switches
    the character input to the Control Rig, which is the editable/live state
    this action promises.

    Raises:
        RuntimeError: If the transaction, character, or Control Rig is not
            active/present, or if Maya's native bake command fails.
        ValueError: If the requested frame interval is empty.
    """
    if transaction is None or not bool(getattr(transaction, "active", False)):
        raise RuntimeError("HumanIK Control Rig transaction is not active")
    try:
        bake_start = int(start)
        bake_end = int(end)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Bake frame range must be integer-valued: {start}..{end}") from exc
    if bake_end < bake_start:
        raise ValueError(f"Bake frame range is empty after integer conversion: {bake_start}..{bake_end}")

    cmds = cmds_module or maya_cmds()
    mel = mel_module or maya_mel()
    character = str(getattr(transaction, "character", "") or "").strip()
    if not character:
        raise RuntimeError("HumanIK Control Rig transaction has no character")
    if not cmds.objExists(character):
        raise RuntimeError(f"HumanIK Control Rig character no longer exists: {character}")

    ensure_humanik_mel_loaded(mel)
    character_literal = mel_string(character)
    if not bool(mel.eval(f"hikHasControlRig({character_literal})")):
        raise RuntimeError(f"HumanIK Control Rig is not active for character: {character}")

    # ``hikSetCurrentCharacter`` changes Maya's process-global HIK selection,
    # not just the transaction's character.  Capture that selection before
    # switching so a bake cannot leave another character (or the empty
    # selection) stranded as the current character after the operation.
    previous_character = str(mel.eval("hikGetCurrentCharacter()") or "").strip()

    playback = {
        "minTime": cmds.playbackOptions(query=True, minTime=True),
        "maxTime": cmds.playbackOptions(query=True, maxTime=True),
        "animationStartTime": cmds.playbackOptions(query=True, animationStartTime=True),
        "animationEndTime": cmds.playbackOptions(query=True, animationEndTime=True),
    }
    current_time = cmds.currentTime(query=True)
    command = "hikBakeToControlRig(0);"
    operation_error = None
    bake_succeeded = False
    try:
        mel.eval(f"hikSetCurrentCharacter({character_literal});")
        cmds.playbackOptions(
            edit=True,
            minTime=bake_start,
            maxTime=bake_end,
            animationStartTime=bake_start,
            animationEndTime=bake_end,
        )
        mel.eval(command)
        if not bool(mel.eval(f"hikHasControlRig({character_literal})")):
            raise RuntimeError(
                f"HumanIK native bake removed the Control Rig for character: {character}"
            )
        bake_succeeded = True
    except Exception as exc:
        operation_error = exc

    cleanup_errors = []
    try:
        cmds.playbackOptions(edit=True, **playback)
    except Exception as exc:  # noqa: BLE001 - cleanup must continue
        cleanup_errors.append(f"playback restore failed: {exc}")
    try:
        cmds.currentTime(current_time, edit=True)
    except Exception as exc:  # noqa: BLE001 - cleanup must continue
        cleanup_errors.append(f"current time restore failed: {exc}")
    try:
        # An empty value is intentional: Maya uses it to represent no
        # current HIK character.  Do not synthesize a character when the
        # pre-bake query returned no selection.
        mel.eval(f"hikSetCurrentCharacter({mel_string(previous_character)});")
    except Exception as exc:  # noqa: BLE001 - cleanup must not strand a successful bake
        cleanup_errors.append(f"current character restore failed: {exc}")

    if cleanup_errors:
        logger.error(
            "HumanIK native bake cleanup was incomplete%s: %s",
            " after an operation failure" if operation_error is not None else "",
            "; ".join(cleanup_errors),
        )
    if operation_error is not None:
        raise operation_error

    if not bake_succeeded:
        raise RuntimeError("HumanIK native bake did not complete")

    return HumanIkControlRigBakeResult(character, bake_start, bake_end, command)


def stop_humanik_control_rig(
    transaction: HumanIkControlRigTransaction,
    cmds_module=None,
    mel_module=None,
) -> None:
    """Delete the control rig and restore NEUTRAL; repeated stops are safe.

    This is the ``restore_mmd_rig``/post-bake teardown path: it deletes the
    control rig through HIK's own ``hikDeleteControlRig()`` MEL command (which
    also resets the character's source input to stance/none) and then
    restores every captured MMD writer connection, plug value, node state,
    and HIK source/lock -- returning the model to the same unblocked NEUTRAL
    state ``describe_humanik_import_lock`` reports for an uncharacterized or
    SOURCE-only model.

    Teardown is fail-closed and retryable: the Control Rig must be deleted
    successfully before the captured MMD writer state is restored. If either
    deletion or restore fails, the transaction remains active and registered
    so a later retry can finish safely. A retry after a partial deletion
    treats the already-removed rig as a no-op, then retries ``restore_state``.
    """
    if not transaction.active:
        return
    cmds = cmds_module or maya_cmds()
    mel = mel_module or maya_mel()
    try:
        _delete_control_rig(transaction.character, transaction.created_nodes, cmds, mel)
    except Exception as exc:  # noqa: BLE001 - retain transaction for a safe retry
        raise RuntimeError(
            "HumanIK Control Rig deletion failed; restore_state was left pending: "
            f"{exc}"
        ) from exc
    try:
        apply_humanik_restore_state(
            transaction.restore_state,
            ownership_id=transaction.ownership_id,
            cmds_module=cmds,
            mel_module=mel,
        )
    except Exception as exc:  # noqa: BLE001 - retain transaction for a safe retry
        raise RuntimeError(
            "HumanIK Control Rig was deleted but restore_state remains pending: "
            f"{exc}"
        ) from exc
    transaction.active = False
    unregister_control_rig_transaction(transaction.character)


def delete_orphaned_control_rig(character: str, cmds_module=None, mel_module=None) -> None:
    """Delete a Control Rig for ``character`` with no tracked transaction.

    HUMANIK-RESTORE-GAPS-1 slice 1c: ``HumanIkFrontendSession.restore_mmd_rig``'s
    scene-facts fallback recovery pass uses this for a ``HIKControlSetNode``
    that has nothing in ``_control_rig_transactions`` to tear it down with --
    a scene reopen (the in-memory transaction is lost even though the node
    survives save/reopen) or a Control Rig created through Maya's standard
    HumanIK UI / a raw ``hikCreateControlRig()`` call. There is no restore_state to
    restore in either case, so this performs only the
    ``hikSetCurrentCharacter`` -> ``hikHasControlRig`` -> ``hikDeleteControlRig()``
    sequence :func:`_delete_control_rig` already uses for a tracked
    transaction's teardown, passing an empty ``created_nodes`` (nothing was
    recorded to node-diff-delete as a fallback).

    Unlike :func:`stop_humanik_control_rig`, this never touches a
    :class:`HumanIkRestoreState` -- callers must report separately (see
    ``describe_frontend_state``/the recovery report) that any muted MMD
    writer edge or pre-characterize stance for this character cannot be
    restored automatically.

    Raises:
        Exception: Any MEL failure (for example the HumanIK Character
            Controls UI not being available in a batch/mayapy process)
            surfaces uncaught. Fail-soft handling belongs to the caller.
    """
    cmds = cmds_module or maya_cmds()
    mel = mel_module or maya_mel()
    _delete_control_rig(character, (), cmds, mel)


def _rollback(error, *, character, created_nodes, restore_state, ownership_id, cmds, mel) -> None:
    """Undo a failed control-rig creation: delete new nodes, then the restore_state."""
    try:
        _delete_control_rig(character, created_nodes, cmds, mel)
    except Exception as delete_error:
        try:
            apply_humanik_restore_state(
                restore_state, ownership_id=ownership_id, cmds_module=cmds, mel_module=mel,
            )
        except Exception as rollback_error:
            raise RuntimeError(
                "HumanIK Control Rig creation failed; node deletion failed; "
                f"restore_state rollback failed: failure={error}; "
                f"delete={delete_error}; rollback={rollback_error}"
            ) from error
        raise RuntimeError(
            "HumanIK Control Rig creation failed and node deletion failed "
            f"(restore_state restored): failure={error}; delete={delete_error}"
        ) from error
    try:
        apply_humanik_restore_state(
            restore_state, ownership_id=ownership_id, cmds_module=cmds, mel_module=mel,
        )
    except Exception as rollback_error:
        raise RuntimeError(
            "HumanIK Control Rig creation failed and restore_state rollback failed: "
            f"failure={error}; rollback={rollback_error}"
        ) from error


def _delete_control_rig(character: str, created_nodes: Iterable[str], cmds, mel) -> None:
    """Remove a Control Rig via HIK's own deletion MEL, falling back to a node diff.

    ``hikDeleteControlRig()`` (``hikControlRigOperations.mel``) has no
    character argument -- it always targets ``hikGetCurrentCharacter()`` -- so
    the current character must be set first. Its internal ``doDeleteControlRig``
    also resets the source input to stance and disables the character, which
    keeps Maya's own HIK bookkeeping (current source, contextual UI) correct
    in a way a raw ``cmds.delete`` on the control-rig nodes would not. The
    node diff delete afterwards is a fallback safety net for any stray nodes
    ``hikDeleteControlRig`` did not know about (or if it silently no-ops),
    not the primary mechanism.

    HUMANIK-RESTORE-GAPS-1: if ``character`` was deleted out from under the
    transaction (for example manually, between ``create_control_rig`` and
    ``restore_mmd_rig``), there is no HIK character left for
    ``hikSetCurrentCharacter``/``hikDeleteControlRig`` to act on -- calling
    them anyway is exactly what previously raised Maya's "node not found"
    MEL error (``hikInputSourceUtils.mel``, ``OutputCharacterDefinition``)
    on every single retry. That step is now skipped with a logged warning
    instead. This function no longer swallows a genuine MEL failure when the
    character *does* exist; ``stop_humanik_control_rig`` surfaces the failure
    and retains the transaction for a safe retry instead.
    """
    if cmds.objExists(character):
        mel.eval(f'hikSetCurrentCharacter("{character}");')
        if bool(mel.eval(f'hikHasControlRig("{character}")')):
            mel.eval("hikDeleteControlRig();")
    else:
        logger.warning(
            "HumanIK control rig delete skipped: character node no longer exists: %s",
            character,
        )
    remaining = sorted(node for node in created_nodes if cmds.objExists(node))
    if remaining:
        cmds.delete(remaining)


def _snapshot_scene_nodes(cmds) -> Set[str]:
    return {str(node) for node in (cmds.ls(long=True) or [])}
