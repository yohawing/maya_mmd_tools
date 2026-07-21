"""Reversible HumanIK scene transaction journal.

The journal captures only explicitly scoped plugs and nodes.  It restores exact
incoming connections, values, node enable state, definition lock state, and HIK
source input after failures without changing unrelated scene data.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass
from typing import Any, Dict, Iterable, Iterator, List, Optional

from mmd_tools.core.humanik_builder import (
    ensure_humanik_mel_loaded,
    get_humanik_definition_lock_state,
)
from mmd_tools.core.humanik_utils import incoming_sources, maya_cmds, maya_mel, mel_string
from mmd_tools.core.logger import get_logger


logger = get_logger(__name__)

STATE_ATTRIBUTES = ("nodeState", "mute", "envelope", "enabled")


@dataclass
class HumanIkPlugSnapshot:
    """Exact value and incoming sources for one destination plug."""

    plug: str
    sources: List[str]
    value: Any
    attr_type: str


@dataclass
class HumanIkNodeSnapshot:
    """Mutable enable-state attributes captured for one scene node."""

    node: str
    attributes: Dict[str, Any]


@dataclass
class HumanIkTransactionJournal:
    """JSON-safe reversible state for one character ownership operation."""

    ownership_id: str
    character: str
    lock_state: bool
    input_source: str
    input_type: int
    plugs: List[HumanIkPlugSnapshot]
    nodes: List[HumanIkNodeSnapshot]

    def to_dict(self) -> Dict[str, Any]:
        """Return a deterministic serialisable journal payload."""
        return asdict(self)


def capture_humanik_journal(
    ownership_id: str,
    character: str,
    destination_plugs: Iterable[str],
    nodes: Iterable[str],
    cmds_module=None,
    mel_module=None,
) -> HumanIkTransactionJournal:
    """Capture a scoped HumanIK transaction journal without editing the scene."""
    if not ownership_id or not character:
        raise ValueError("ownership_id and character are required")
    cmds = cmds_module or maya_cmds()
    mel = mel_module or maya_mel()
    ensure_humanik_mel_loaded(mel)
    plugs = [_capture_plug(cmds, plug) for plug in sorted(set(destination_plugs))]
    node_snapshots = [_capture_node(cmds, node) for node in sorted(set(nodes))]
    source = str(mel.eval(f"hikGetRetargetCharacterInput({mel_string(character)})") or "")
    raw_input_type = mel.eval(f"hikGetInputType({mel_string(character)})")
    input_type = int(raw_input_type) if raw_input_type is not None else -1
    return HumanIkTransactionJournal(
        ownership_id=str(ownership_id),
        character=str(character),
        lock_state=get_humanik_definition_lock_state(character, mel),
        input_source=source,
        input_type=input_type,
        plugs=plugs,
        nodes=node_snapshots,
    )


def restore_humanik_journal(
    journal: HumanIkTransactionJournal,
    ownership_id: Optional[str] = None,
    cmds_module=None,
    mel_module=None,
) -> List[str]:
    """Restore a journal exactly; repeated restores are idempotent.

    A journal entry (the character itself, a plug's node, a reconnect
    source, or a state node) that no longer exists in the scene -- because
    the user deleted it, or because a Control Rig teardown step already
    removed it -- is skipped with a logged warning instead of raising: there
    is nothing to restore back onto a node that is gone, and treating that
    as fatal is what previously left ``HumanIkControlRigTransaction``s stuck
    permanently active (every retry hit the same "node not found" MEL error
    from ``restore_humanik_journal`` before it ever reached the
    deactivate/unregister step -- see ``stop_humanik_control_rig``).

    Restore failures against nodes that DO still exist are not skipped: every
    remaining entry is still attempted, then all such failures are raised
    together as a single aggregated ``RuntimeError`` so one bad plug cannot
    hide failures on the others. This keeps the original "an incomplete
    rollback surfaces as an error" guarantee for anything actually
    restorable, while making the "node was deleted out from under us" case
    (which is not restorable, by definition) non-fatal.

    Returns:
        Warning messages for skipped missing-node entries (empty when every
        journaled node was present in the scene).

    Raises:
        ValueError: ``ownership_id`` does not match the journal's owner.
        RuntimeError: Aggregated restore failures against nodes that still
            exist in the scene.
    """
    if ownership_id is not None and ownership_id != journal.ownership_id:
        raise ValueError("HumanIK journal ownership mismatch")
    cmds = cmds_module or maya_cmds()
    mel = mel_module or maya_mel()
    ensure_humanik_mel_loaded(mel)

    warnings: List[str] = []
    failures: List[str] = []

    if cmds.objExists(journal.character):
        try:
            current_source = str(
                mel.eval(f"hikGetRetargetCharacterInput({mel_string(journal.character)})") or ""
            )
            if current_source != journal.input_source:
                mel.eval(
                    f"hikSetCharacterInput({mel_string(journal.character)}, "
                    f"{mel_string(journal.input_source)});"
                )
            current_lock = get_humanik_definition_lock_state(journal.character, mel)
            if current_lock != journal.lock_state:
                mel.eval(
                    f"hikCharacterLock({mel_string(journal.character)}, "
                    f"{1 if journal.lock_state else 0}, 1);"
                )
        except Exception as exc:  # noqa: BLE001 - aggregated below, character node exists
            failures.append(
                f"character input/lock restore failed for {journal.character}: {exc}"
            )
    else:
        warnings.append(
            f"HumanIK journal skip: character node no longer exists: {journal.character}"
        )

    live_plugs = []
    for snapshot in journal.plugs:
        node = snapshot.plug.rsplit(".", 1)[0]
        if cmds.objExists(node):
            live_plugs.append(snapshot)
        else:
            warnings.append(
                f"HumanIK journal skip: plug node no longer exists: {snapshot.plug}"
            )

    for snapshot in live_plugs:
        try:
            for source in incoming_sources(cmds, snapshot.plug):
                cmds.disconnectAttr(source, snapshot.plug)
        except Exception as exc:  # noqa: BLE001 - aggregated below
            failures.append(f"disconnect failed for {snapshot.plug}: {exc}")
    for snapshot in live_plugs:
        if snapshot.sources:
            continue
        try:
            _set_plug_value(cmds, snapshot.plug, snapshot.value, snapshot.attr_type)
        except Exception as exc:  # noqa: BLE001 - aggregated below
            failures.append(f"value restore failed for {snapshot.plug}: {exc}")
    for snapshot in live_plugs:
        for source in snapshot.sources:
            source_node = source.rsplit(".", 1)[0]
            if not cmds.objExists(source_node):
                warnings.append(
                    f"HumanIK journal skip: reconnect source no longer exists: "
                    f"{source} -> {snapshot.plug}"
                )
                continue
            try:
                if not _is_connected(cmds, source, snapshot.plug):
                    cmds.connectAttr(source, snapshot.plug, force=True)
            except Exception as exc:  # noqa: BLE001 - aggregated below
                failures.append(
                    f"reconnect failed for {source} -> {snapshot.plug}: {exc}"
                )

    for snapshot in journal.nodes:
        if not cmds.objExists(snapshot.node):
            warnings.append(
                f"HumanIK journal skip: node no longer exists: {snapshot.node}"
            )
            continue
        for attr, value in snapshot.attributes.items():
            try:
                cmds.setAttr(f"{snapshot.node}.{attr}", value)
            except Exception as exc:  # noqa: BLE001 - aggregated below
                failures.append(
                    f"attribute restore failed for {snapshot.node}.{attr}: {exc}"
                )

    for message in warnings:
        logger.warning(message)
    if failures:
        raise RuntimeError(
            "HumanIK journal restore failed for existing nodes: " + "; ".join(failures)
        )
    return warnings


@contextmanager
def humanik_transaction(
    ownership_id: str,
    character: str,
    destination_plugs: Iterable[str],
    nodes: Iterable[str],
    cmds_module=None,
    mel_module=None,
) -> Iterator[HumanIkTransactionJournal]:
    """Capture state and automatically roll it back when the body raises."""
    journal = capture_humanik_journal(
        ownership_id,
        character,
        destination_plugs,
        nodes,
        cmds_module=cmds_module,
        mel_module=mel_module,
    )
    try:
        yield journal
    except Exception:
        restore_humanik_journal(
            journal,
            ownership_id=ownership_id,
            cmds_module=cmds_module,
            mel_module=mel_module,
        )
        raise


def _capture_plug(cmds, plug: str) -> HumanIkPlugSnapshot:
    return HumanIkPlugSnapshot(
        plug=str(plug),
        sources=incoming_sources(cmds, plug),
        value=cmds.getAttr(plug),
        attr_type=str(cmds.getAttr(plug, type=True) or ""),
    )


def _capture_node(cmds, node: str) -> HumanIkNodeSnapshot:
    attributes = {}
    for attr in STATE_ATTRIBUTES:
        if cmds.attributeQuery(attr, node=node, exists=True):
            attributes[attr] = cmds.getAttr(f"{node}.{attr}")
    return HumanIkNodeSnapshot(node=str(node), attributes=attributes)


def _is_connected(cmds, source: str, destination: str) -> bool:
    try:
        return bool(cmds.isConnected(source, destination))
    except Exception:
        return source in incoming_sources(cmds, destination)


def _set_plug_value(cmds, plug: str, value: Any, attr_type: str) -> None:
    while isinstance(value, (list, tuple)) and len(value) == 1 and isinstance(value[0], (list, tuple)):
        value = value[0]
    if attr_type == "string":
        cmds.setAttr(plug, value or "", type="string")
    elif isinstance(value, (list, tuple)):
        cmds.setAttr(plug, *value, type=attr_type or None)
    else:
        cmds.setAttr(plug, value)
