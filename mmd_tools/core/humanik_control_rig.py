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
equivalent problem for TARGET preview by journaling scene state, isolating
reviewed MMD writers, connecting the HIK source, and re-scanning/re-isolating
writers that reappeared. This module applies the identical machinery around
``hikCreateControlRig()`` -- Transaction candidate A from the TODO:

    journal -> isolate MMD writers -> pre-cycle gate -> hikCreateControlRig
    -> re-scan/re-isolate writers -> post-cycle gate

Any failure after the journal is captured rolls the scene back: MMD writer
edges are reconnected, plug values/node state/HIK source+lock are restored
from the journal, and any control-rig nodes ``hikCreateControlRig`` created
are removed via HIK's own ``hikDeleteControlRig()`` MEL command (never a bare
``cmds.delete`` first -- see ``_delete_control_rig`` for why a node-diff
delete is only a fallback).

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
    split_ownership_rows,
)
from mmd_tools.core.humanik_preview import (
    disconnect_residual_muted_writers,
    disconnect_reviewed_writers,
    re_isolate_reviewed_edges,
    row_hik_writes,
)
from mmd_tools.core.humanik_transaction import (
    HumanIkTransactionJournal,
    capture_humanik_journal,
    restore_humanik_journal,
)
from mmd_tools.core.humanik_utils import maya_cmds, maya_mel
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
    journal: HumanIkTransactionJournal
    disconnected: List[Dict[str, str]]
    retained_nodes: List[str]
    created_nodes: List[str]
    pre_cycle_baseline: List[str] = field(default_factory=list)
    post_cycle_plugs: List[str] = field(default_factory=list)
    active: bool = True

    def to_dict(self) -> Dict[str, Any]:
        """Return the JSON-safe control-rig transaction diagnostic payload."""
        return {
            "ownershipId": self.ownership_id,
            "character": self.character,
            "disconnected": list(self.disconnected),
            "retainedNodes": list(self.retained_nodes),
            "createdNodes": list(self.created_nodes),
            "preCycleBaseline": list(self.pre_cycle_baseline),
            "postCyclePlugs": list(self.post_cycle_plugs),
            "active": self.active,
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
        ownership_id: Journal owner id, distinct from any active preview's id
            so the two transactions can never cross-restore each other.
        character: Already characterized and locked HIK character name.
        hik_joints: The character's HIK-assigned primary joints (long paths).
        cmds_module: Optional Maya ``cmds`` compatible module for tests.
        mel_module: Optional Maya ``mel`` compatible module for tests.

    Returns:
        The active :class:`HumanIkControlRigTransaction`.

    Raises:
        RuntimeError: On a pre-existing blocker, a post-scan residual muted
            writer, a post-scan blocker, or a new DG cycle relative to the
            pre-mutation baseline. The scene is rolled back to its
            pre-mutation state (writers reconnected, journal restored, any
            newly created control-rig nodes removed) before re-raising.
    """
    cmds = cmds_module or maya_cmds()
    mel = mel_module or maya_mel()
    ensure_humanik_mel_loaded(mel)
    hik_joint_set = {str(joint) for joint in hik_joints}

    ownership_report = collect_hik_ownership_report(hik_joint_set, cmds_module=cmds)
    blockers, mute_rows, retained_nodes = split_ownership_rows(ownership_report)
    if blockers:
        labels = ", ".join(f"{row['node']}:{row['classification']}" for row in blockers)
        raise RuntimeError(f"HumanIK Control Rig creation blocked: {labels}")

    destinations = sorted({plug for row in mute_rows for plug in row.get("writes", [])})
    muted_nodes = sorted({row["node"] for row in mute_rows})

    journal = capture_humanik_journal(
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
        disconnect_reviewed_writers(cmds, mute_rows, disconnected)

        pre_cycle_baseline = detect_dg_cycles(cmds)

        before_nodes = _snapshot_scene_nodes(cmds)
        create_humanik_control_rig(character, mel_module=mel)
        after_nodes = _snapshot_scene_nodes(cmds)
        created_nodes = sorted(after_nodes - before_nodes)

        re_isolate_reviewed_edges(cmds, disconnected)

        post_report = collect_hik_ownership_report(hik_joint_set, cmds_module=cmds)
        residual_muted_writers = [
            row
            for row in post_report["rows"]
            if row["node"] in muted_nodes and row_hik_writes(row, hik_joint_set)
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
            journal=journal,
            ownership_id=ownership_id,
            cmds=cmds,
            mel=mel,
        )
        raise
    transaction = HumanIkControlRigTransaction(
        ownership_id=ownership_id,
        character=character,
        journal=journal,
        disconnected=sorted(disconnected, key=lambda row: (row["destination"], row["source"])),
        retained_nodes=retained_nodes,
        created_nodes=created_nodes,
        pre_cycle_baseline=pre_cycle_baseline,
        post_cycle_plugs=post_cycle_plugs,
    )
    register_control_rig_transaction(character, transaction)
    return transaction


def stop_humanik_control_rig(
    transaction: HumanIkControlRigTransaction,
    cmds_module=None,
    mel_module=None,
) -> None:
    """Delete the control rig and restore NEUTRAL; repeated stops are safe.

    This is the ``restore_mmd_rig``/post-bake teardown path: it deletes the
    control rig through HIK's own ``hikDeleteControlRig()`` MEL command (which
    also resets the character's source input to stance/none) and then
    restores every journaled MMD writer connection, plug value, node state,
    and HIK source/lock -- returning the model to the same unblocked NEUTRAL
    state ``describe_humanik_import_lock`` reports for an uncharacterized or
    SOURCE-only model.

    HUMANIK-RESTORE-GAPS-1: teardown is exception-safe. ``transaction`` is
    always deactivated and unregistered before this function returns or
    raises -- previously, an exception partway through teardown (for
    example the user manually deleting the ``HIKCharacterNode`` before
    calling ``restore_mmd_rig``) left the transaction registered and active,
    so every subsequent ``restore_mmd_rig`` call re-attempted the exact same
    doomed teardown and hit the exact same exception forever. Both teardown
    steps are attempted even if the first one fails (a delete failure must
    not prevent the journal restore, and vice versa); any failures are
    aggregated and raised together as a single ``RuntimeError`` *after* the
    transaction has already been released, so the failure surfaces exactly
    once instead of on every retry.
    """
    if not transaction.active:
        return
    cmds = cmds_module or maya_cmds()
    mel = mel_module or maya_mel()
    failures: List[str] = []
    try:
        _delete_control_rig(transaction.character, transaction.created_nodes, cmds, mel)
    except Exception as exc:  # noqa: BLE001 - aggregated below, transaction still released
        failures.append(f"control rig delete failed: {exc}")
    try:
        restore_humanik_journal(
            transaction.journal,
            ownership_id=transaction.ownership_id,
            cmds_module=cmds,
            mel_module=mel,
        )
    except Exception as exc:  # noqa: BLE001 - aggregated below, transaction still released
        failures.append(f"journal restore failed: {exc}")
    transaction.active = False
    unregister_control_rig_transaction(transaction.character)
    if failures:
        raise RuntimeError(
            "HumanIK Control Rig teardown had failures (transaction released "
            "so a retry will not repeat them): " + "; ".join(failures)
        )


def delete_orphaned_control_rig(character: str, cmds_module=None, mel_module=None) -> None:
    """Delete a Control Rig for ``character`` with no tracked transaction.

    HUMANIK-RESTORE-GAPS-1 slice 1c: ``HumanIkFrontendSession.restore_mmd_rig``'s
    scene-facts fallback recovery pass uses this for a ``HIKControlSetNode``
    that has nothing in ``_control_rig_transactions`` to tear it down with --
    a scene reopen (the in-memory transaction is lost even though the node
    survives save/reopen) or a Control Rig created through Maya's standard
    HumanIK UI / a raw ``hikCreateControlRig()`` call. There is no journal to
    restore in either case, so this performs only the
    ``hikSetCurrentCharacter`` -> ``hikHasControlRig`` -> ``hikDeleteControlRig()``
    sequence :func:`_delete_control_rig` already uses for a tracked
    transaction's teardown, passing an empty ``created_nodes`` (nothing was
    recorded to node-diff-delete as a fallback).

    Unlike :func:`stop_humanik_control_rig`, this never touches a
    :class:`HumanIkTransactionJournal` -- callers must report separately (see
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


def _rollback(error, *, character, created_nodes, journal, ownership_id, cmds, mel) -> None:
    """Undo a failed control-rig creation: delete new nodes, then the journal."""
    try:
        _delete_control_rig(character, created_nodes, cmds, mel)
    except Exception as delete_error:
        try:
            restore_humanik_journal(
                journal, ownership_id=ownership_id, cmds_module=cmds, mel_module=mel,
            )
        except Exception as rollback_error:
            raise RuntimeError(
                "HumanIK Control Rig creation failed; node deletion failed; "
                f"journal rollback failed: failure={error}; "
                f"delete={delete_error}; rollback={rollback_error}"
            ) from error
        raise RuntimeError(
            "HumanIK Control Rig creation failed and node deletion failed "
            f"(journal restored): failure={error}; delete={delete_error}"
        ) from error
    try:
        restore_humanik_journal(
            journal, ownership_id=ownership_id, cmds_module=cmds, mel_module=mel,
        )
    except Exception as rollback_error:
        raise RuntimeError(
            "HumanIK Control Rig creation failed and journal rollback failed: "
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
    character *does* exist; ``stop_humanik_control_rig`` aggregates and
    surfaces it instead.
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
