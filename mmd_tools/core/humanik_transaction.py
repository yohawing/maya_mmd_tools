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
    cmds = cmds_module or _maya_cmds()
    mel = mel_module or _maya_mel()
    ensure_humanik_mel_loaded(mel)
    plugs = [_capture_plug(cmds, plug) for plug in sorted(set(destination_plugs))]
    node_snapshots = [_capture_node(cmds, node) for node in sorted(set(nodes))]
    source = str(mel.eval(f"hikGetRetargetCharacterInput({_mel_string(character)})") or "")
    raw_input_type = mel.eval(f"hikGetInputType({_mel_string(character)})")
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
) -> None:
    """Restore a journal exactly; repeated restores are idempotent."""
    if ownership_id is not None and ownership_id != journal.ownership_id:
        raise ValueError("HumanIK journal ownership mismatch")
    cmds = cmds_module or _maya_cmds()
    mel = mel_module or _maya_mel()
    ensure_humanik_mel_loaded(mel)

    current_source = str(
        mel.eval(f"hikGetRetargetCharacterInput({_mel_string(journal.character)})") or ""
    )
    if current_source != journal.input_source:
        mel.eval(
            f"hikSetCharacterInput({_mel_string(journal.character)}, "
            f"{_mel_string(journal.input_source)});"
        )
    current_lock = get_humanik_definition_lock_state(journal.character, mel)
    if current_lock != journal.lock_state:
        mel.eval(
            f"hikCharacterLock({_mel_string(journal.character)}, "
            f"{1 if journal.lock_state else 0}, 1);"
        )

    for snapshot in journal.plugs:
        for source in _incoming_sources(cmds, snapshot.plug):
            cmds.disconnectAttr(source, snapshot.plug)
    for snapshot in journal.plugs:
        if not snapshot.sources:
            _set_plug_value(cmds, snapshot.plug, snapshot.value, snapshot.attr_type)
    for snapshot in journal.plugs:
        for source in snapshot.sources:
            if not _is_connected(cmds, source, snapshot.plug):
                cmds.connectAttr(source, snapshot.plug, force=True)
    for snapshot in journal.nodes:
        for attr, value in snapshot.attributes.items():
            cmds.setAttr(f"{snapshot.node}.{attr}", value)


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
        sources=_incoming_sources(cmds, plug),
        value=cmds.getAttr(plug),
        attr_type=str(cmds.getAttr(plug, type=True) or ""),
    )


def _capture_node(cmds, node: str) -> HumanIkNodeSnapshot:
    attributes = {}
    for attr in STATE_ATTRIBUTES:
        if cmds.attributeQuery(attr, node=node, exists=True):
            attributes[attr] = cmds.getAttr(f"{node}.{attr}")
    return HumanIkNodeSnapshot(node=str(node), attributes=attributes)


def _incoming_sources(cmds, plug: str) -> List[str]:
    return sorted(
        str(source)
        for source in (cmds.listConnections(plug, source=True, destination=False, plugs=True) or [])
    )


def _is_connected(cmds, source: str, destination: str) -> bool:
    try:
        return bool(cmds.isConnected(source, destination))
    except Exception:
        return source in _incoming_sources(cmds, destination)


def _set_plug_value(cmds, plug: str, value: Any, attr_type: str) -> None:
    while isinstance(value, (list, tuple)) and len(value) == 1 and isinstance(value[0], (list, tuple)):
        value = value[0]
    if attr_type == "string":
        cmds.setAttr(plug, value or "", type="string")
    elif isinstance(value, (list, tuple)):
        cmds.setAttr(plug, *value, type=attr_type or None)
    else:
        cmds.setAttr(plug, value)


def _mel_string(value: str) -> str:
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _maya_cmds():
    from maya import cmds

    return cmds


def _maya_mel():
    from maya import mel

    return mel
