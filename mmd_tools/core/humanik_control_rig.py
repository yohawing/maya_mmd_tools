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
    classify_humanik_constraints,
    collect_humanik_constraint_facts,
)
from mmd_tools.core.humanik_preview import (
    BLOCKING_CLASSIFICATIONS,
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
# character name. ``begin_humanik_control_rig`` (plugin/frontend path) and
# ``adopt_humanik_control_rig`` (out-of-band standard-UI path, see
# ``humanik_control_rig_watch``) both register their transaction here so
# either path can cheaply answer "does this character already have an
# active, writer-isolated Control Rig transaction?" without needing to know
# which path created it. ``stop_humanik_control_rig`` always unregisters.
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


def iter_active_control_rig_transactions() -> List["HumanIkControlRigTransaction"]:
    """Return every currently active registered Control Rig transaction.

    Used by ``HumanIkFrontendSession.restore_mmd_rig`` to sweep up
    transactions adopted from Maya's standard HumanIK UI that no
    ``HumanIkFrontendSession.create_control_rig()`` call ever created.
    """
    stale = [
        character
        for character, transaction in _ACTIVE_TRANSACTIONS_BY_CHARACTER.items()
        if not transaction.active
    ]
    for character in stale:
        _ACTIVE_TRANSACTIONS_BY_CHARACTER.pop(character, None)
    return list(_ACTIVE_TRANSACTIONS_BY_CHARACTER.values())


def detect_dg_cycles(cmds_module=None) -> List[str]:
    """Return the sorted plugs Maya's ``cycleCheck`` currently reports.

    ``cmds.cycleCheck`` has no node/plug scoping option, so this is always a
    scene-wide snapshot. Callers scope it themselves by diffing a
    before/after pair with :func:`new_cycle_plugs` rather than asking this
    function to filter by reachability -- see the module docstring for why.
    """
    cmds = cmds_module or _maya_cmds()
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
    cmds = cmds_module or _maya_cmds()
    mel = mel_module or _maya_mel()
    ensure_humanik_mel_loaded(mel)
    hik_joint_set = {str(joint) for joint in hik_joints}

    ownership_report = classify_humanik_constraints(
        collect_humanik_constraint_facts(cmds_module=cmds),
        hik_joint_set,
    )
    blockers = [
        row for row in ownership_report.get("rows", [])
        if row.get("classification") in BLOCKING_CLASSIFICATIONS
    ]
    if blockers:
        labels = ", ".join(f"{row['node']}:{row['classification']}" for row in blockers)
        raise RuntimeError(f"HumanIK Control Rig creation blocked: {labels}")

    mute_rows = [
        row for row in ownership_report.get("rows", [])
        if row.get("classification") == "mute_for_hik"
    ]
    retained_nodes = sorted(
        row["node"] for row in ownership_report.get("rows", [])
        if row.get("classification") == "keep_post"
    )
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

        post_report = classify_humanik_constraints(
            collect_humanik_constraint_facts(cmds_module=cmds),
            hik_joint_set,
        )
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


def adopt_humanik_control_rig(
    ownership_id: str,
    character: str,
    hik_joints: Iterable[str],
    cmds_module=None,
    mel_module=None,
) -> HumanIkControlRigTransaction:
    """Retroactively isolate MMD writers around a Control Rig that already exists.

    ``begin_humanik_control_rig`` isolates writers *before* calling
    ``hikCreateControlRig()``, so the DG cycle never has a chance to form.
    That ordering is unavailable when Maya's own standard HumanIK UI
    (Character Controls -> Create Control Rig, or a raw ``hikCreateControlRig()``
    MEL call) creates the rig: mmd_tools only learns about it after the fact,
    via ``humanik_control_rig_watch``'s ``HIKState2SK`` node-added callback,
    by which point Maya has already wired the cyclic connections.

    This function is the ``begin_humanik_control_rig`` machinery minus the
    creation step: journal the current writer state, isolate
    ``mute_for_hik`` writers, re-scan for residual writers/blockers, and
    cycle-gate against a baseline captured at adoption start (which, unlike
    ``begin_humanik_control_rig``'s pre-creation baseline, may already
    reflect the HIK-caused cycle -- isolating the writers is expected to
    remove it, not merely avoid making it worse).

    Failure policy deliberately differs from ``begin_humanik_control_rig``:
    the Control Rig was created intentionally by the user through Maya's own
    UI, so a failure here must NEVER delete it. Only the isolation attempt
    (disconnected writer edges, journal) is rolled back; the rig itself, and
    any DG cycle it still has, is left in place, and the caller is expected
    to log/warn loudly and direct the user to the plugin menu's supported
    Control Rig creation path.

    Args:
        ownership_id: Journal owner id, distinct from any other active
            transaction's id (see ``begin_humanik_control_rig``).
        character: The already characterized, locked HIK character that owns
            the out-of-band Control Rig.
        hik_joints: The character's HIK-assigned primary joints (long paths).
        cmds_module: Optional Maya ``cmds`` compatible module for tests.
        mel_module: Optional Maya ``mel`` compatible module for tests.

    Returns:
        The active :class:`HumanIkControlRigTransaction`, already registered
        in the module-level registry (see ``register_control_rig_transaction``).

    Raises:
        RuntimeError: On a pre-existing blocker, a post-scan residual muted
            writer, a post-scan blocker, or a new DG cycle relative to the
            adoption-start baseline. The isolation attempt is rolled back
            (writer edges reconnected, journal restored) before re-raising;
            the Control Rig itself is never touched.
    """
    cmds = cmds_module or _maya_cmds()
    mel = mel_module or _maya_mel()
    ensure_humanik_mel_loaded(mel)
    hik_joint_set = {str(joint) for joint in hik_joints}

    ownership_report = classify_humanik_constraints(
        collect_humanik_constraint_facts(cmds_module=cmds),
        hik_joint_set,
    )
    blockers = [
        row for row in ownership_report.get("rows", [])
        if row.get("classification") in BLOCKING_CLASSIFICATIONS
    ]
    if blockers:
        labels = ", ".join(f"{row['node']}:{row['classification']}" for row in blockers)
        raise RuntimeError(
            f"HumanIK Control Rig adoption blocked (rig left in place): {labels}"
        )

    mute_rows = [
        row for row in ownership_report.get("rows", [])
        if row.get("classification") == "mute_for_hik"
    ]
    retained_nodes = sorted(
        row["node"] for row in ownership_report.get("rows", [])
        if row.get("classification") == "keep_post"
    )
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
    pre_cycle_baseline = detect_dg_cycles(cmds)
    try:
        disconnect_reviewed_writers(cmds, mute_rows, disconnected)
        re_isolate_reviewed_edges(cmds, disconnected)

        post_report = classify_humanik_constraints(
            collect_humanik_constraint_facts(cmds_module=cmds),
            hik_joint_set,
        )
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
                "HumanIK Control Rig adoption post-scan found residual muted HIK writers: "
                f"{labels}"
            )
        post_blockers = [
            row for row in post_report["rows"]
            if row["node"] not in muted_nodes
            and row["classification"] in BLOCKING_CLASSIFICATIONS
        ]
        if post_blockers:
            raise RuntimeError("HumanIK Control Rig adoption post-scan found blocker")

        post_cycle_plugs = detect_dg_cycles(cmds)
        regressed = new_cycle_plugs(pre_cycle_baseline, post_cycle_plugs)
        if regressed:
            raise RuntimeError(
                "HumanIK Control Rig adoption still shows new DG cycles after isolation: "
                f"{regressed}"
            )
    except Exception as error:
        _rollback_isolation_only(
            error,
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
        # Adoption starts after Maya already created the rig, so there is no
        # pre-creation node snapshot to diff -- deletion relies solely on
        # ``hikDeleteControlRig()`` (see ``_delete_control_rig``), not the
        # node-diff fallback.
        created_nodes=[],
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
    """
    if not transaction.active:
        return
    cmds = cmds_module or _maya_cmds()
    mel = mel_module or _maya_mel()
    _delete_control_rig(transaction.character, transaction.created_nodes, cmds, mel)
    restore_humanik_journal(
        transaction.journal,
        ownership_id=transaction.ownership_id,
        cmds_module=cmds,
        mel_module=mel,
    )
    transaction.active = False
    unregister_control_rig_transaction(transaction.character)


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


def _rollback_isolation_only(error, *, journal, ownership_id, cmds, mel) -> None:
    """Undo a failed adoption's writer isolation without touching the Control Rig.

    Unlike ``_rollback`` (the plugin-path rollback, which also deletes any
    control-rig nodes ``hikCreateControlRig`` created), adoption never owns
    the rig's creation -- it was created by the user through Maya's own UI --
    so a failed adoption must leave it exactly as found.
    """
    try:
        restore_humanik_journal(
            journal, ownership_id=ownership_id, cmds_module=cmds, mel_module=mel,
        )
    except Exception as rollback_error:
        raise RuntimeError(
            "HumanIK Control Rig adoption failed and journal rollback failed "
            "(Control Rig left in place): "
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
    """
    try:
        mel.eval(f'hikSetCurrentCharacter("{character}");')
        if bool(mel.eval(f'hikHasControlRig("{character}")')):
            mel.eval("hikDeleteControlRig();")
    except Exception:
        pass
    remaining = sorted(node for node in created_nodes if cmds.objExists(node))
    if remaining:
        cmds.delete(remaining)


def _snapshot_scene_nodes(cmds) -> Set[str]:
    return {str(node) for node in (cmds.ls(long=True) or [])}


def _maya_cmds():
    from maya import cmds

    return cmds


def _maya_mel():
    from maya import mel

    return mel
