"""Reversible HumanIK scene transaction journal.

The journal captures only explicitly scoped plugs and nodes.  It restores exact
incoming connections, values, node enable state, definition lock state, and HIK
source input after failures without changing unrelated scene data.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass
import json
from typing import Any, Dict, Iterable, Iterator, List, Mapping, Optional

from mmd_tools.core.humanik_builder import (
    ensure_humanik_mel_loaded,
    get_humanik_definition_lock_state,
)
from mmd_tools.core.humanik_utils import incoming_sources, maya_cmds, maya_mel, mel_string
from mmd_tools.core.logger import get_logger


logger = get_logger(__name__)

STATE_ATTRIBUTES = ("nodeState", "mute", "envelope", "enabled")

# Scene-persisted transaction metadata is deliberately a small, versioned
# JSON payload on an owned ``network`` node.  The version/schema guard keeps a
# future plugin (or hand-edited scene data) from reconstructing a transaction
# it cannot safely understand.
HUMANIK_TRANSACTION_SCHEMA = "mmd_tools.humanik_transaction"
HUMANIK_TRANSACTION_VERSION = 1
HUMANIK_TRANSACTION_NODE = "mmdHumanIkTransactionJournal"
HUMANIK_TRANSACTION_TAG_ATTR = "mmd_humanik_transaction_schema"
HUMANIK_TRANSACTION_PAYLOAD_ATTR = "mmd_humanik_transaction_payload"


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

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "HumanIkTransactionJournal":
        """Reconstruct a journal after validating its persisted shape.

        Scene metadata is user-editable Maya data, so restoration must fail
        closed on malformed rows instead of relying on ``dict`` unpacking and
        accidentally accepting a stale/foreign payload.
        """
        if not isinstance(payload, Mapping):
            raise ValueError("HumanIK journal payload must be an object")
        required = ("ownership_id", "character", "lock_state", "input_source", "input_type", "plugs", "nodes")
        missing = [key for key in required if key not in payload]
        if missing:
            raise ValueError("HumanIK journal payload missing: " + ", ".join(missing))
        ownership_id = payload["ownership_id"]
        character = payload["character"]
        input_source = payload["input_source"]
        if not all(isinstance(value, str) and value for value in (ownership_id, character)):
            raise ValueError("HumanIK journal ownership_id and character must be non-empty strings")
        if not isinstance(input_source, str):
            raise ValueError("HumanIK journal input_source must be a string")
        if not isinstance(payload["lock_state"], bool):
            raise ValueError("HumanIK journal lock_state must be boolean")
        if not isinstance(payload["input_type"], int) or isinstance(payload["input_type"], bool):
            raise ValueError("HumanIK journal input_type must be an integer")

        plugs = []
        raw_plugs = payload["plugs"]
        if not isinstance(raw_plugs, list):
            raise ValueError("HumanIK journal plugs must be an array")
        for row in raw_plugs:
            if not isinstance(row, Mapping):
                raise ValueError("HumanIK journal plug row must be an object")
            plug = row.get("plug")
            sources = row.get("sources")
            attr_type = row.get("attr_type")
            if not isinstance(plug, str) or not plug or not isinstance(attr_type, str):
                raise ValueError("HumanIK journal plug row has invalid plug or attr_type")
            if not isinstance(sources, list) or not all(isinstance(source, str) for source in sources):
                raise ValueError("HumanIK journal plug sources must be an array of strings")
            value = row.get("value")
            try:
                json.dumps(value, ensure_ascii=False)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"HumanIK journal plug value is not JSON-safe: {plug}") from exc
            plugs.append(HumanIkPlugSnapshot(plug, list(sources), value, attr_type))

        nodes = []
        raw_nodes = payload["nodes"]
        if not isinstance(raw_nodes, list):
            raise ValueError("HumanIK journal nodes must be an array")
        for row in raw_nodes:
            if not isinstance(row, Mapping) or not isinstance(row.get("node"), str) or not row.get("node"):
                raise ValueError("HumanIK journal node row has invalid node")
            attributes = row.get("attributes")
            if not isinstance(attributes, Mapping):
                raise ValueError("HumanIK journal node attributes must be an object")
            try:
                json.dumps(attributes, ensure_ascii=False)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"HumanIK journal node attributes are not JSON-safe: {row['node']}") from exc
            nodes.append(HumanIkNodeSnapshot(str(row["node"]), dict(attributes)))
        return cls(
            ownership_id=str(ownership_id),
            character=str(character),
            lock_state=bool(payload["lock_state"]),
            input_source=input_source,
            input_type=int(payload["input_type"]),
            plugs=plugs,
            nodes=nodes,
        )


def serialize_humanik_transaction_state(records: Iterable[Mapping[str, Any]]) -> Dict[str, Any]:
    """Build the versioned scene payload for active frontend transactions."""
    rows = []
    for record in records:
        if not isinstance(record, Mapping):
            raise ValueError("HumanIK transaction record must be an object")
        # Validate the nested journal before writing scene metadata.  This is
        # also useful for callers using lightweight test doubles: a fake
        # transaction is simply not eligible for persistence in the frontend.
        journal = HumanIkTransactionJournal.from_dict(record.get("journal", {}))
        row = dict(record)
        row["journal"] = journal.to_dict()
        row["active"] = bool(row.get("active", True))
        if not isinstance(row.get("modelRoot"), str) or not row["modelRoot"]:
            raise ValueError("HumanIK transaction modelRoot must be a non-empty string")
        if not isinstance(row.get("character"), str) or not row["character"]:
            raise ValueError("HumanIK transaction character must be a non-empty string")
        rows.append(row)
    rows.sort(key=lambda item: item["modelRoot"])
    return {
        "schema": HUMANIK_TRANSACTION_SCHEMA,
        "version": HUMANIK_TRANSACTION_VERSION,
        "transactions": rows,
    }


def deserialize_humanik_transaction_state(payload: Any) -> List[Dict[str, Any]]:
    """Validate a scene payload and return reconstructable transaction rows.

    ``ValueError`` is intentional for malformed/stale metadata; frontend
    startup catches it, reports the data as unusable, and leaves the scene
    untouched rather than guessing ownership.
    """
    if not isinstance(payload, Mapping):
        raise ValueError("HumanIK transaction scene payload must be an object")
    if payload.get("schema") != HUMANIK_TRANSACTION_SCHEMA:
        raise ValueError("HumanIK transaction scene schema mismatch")
    if payload.get("version") != HUMANIK_TRANSACTION_VERSION:
        raise ValueError("Unsupported HumanIK transaction scene version")
    rows = payload.get("transactions")
    if not isinstance(rows, list):
        raise ValueError("HumanIK transaction scene transactions must be an array")
    result = []
    for row in rows:
        if not isinstance(row, Mapping):
            raise ValueError("HumanIK transaction scene row must be an object")
        model_root = row.get("modelRoot")
        character = row.get("character")
        if not isinstance(model_root, str) or not model_root:
            raise ValueError("HumanIK transaction scene row has invalid modelRoot")
        if not isinstance(character, str) or not character:
            raise ValueError("HumanIK transaction scene row has invalid character")
        journal = HumanIkTransactionJournal.from_dict(row.get("journal", {}))
        if journal.character != character:
            raise ValueError("HumanIK transaction journal character mismatch")
        ownership_id = row.get("ownershipId")
        if not isinstance(ownership_id, str) or not ownership_id:
            raise ValueError("HumanIK transaction scene row has invalid ownershipId")
        if journal.ownership_id != ownership_id:
            raise ValueError("HumanIK transaction journal ownership mismatch")
        active = row.get("active", True)
        if not isinstance(active, bool):
            raise ValueError("HumanIK transaction scene row has invalid active flag")
        result.append({**dict(row), "journal": journal.to_dict(), "active": active})
    return result


def persist_humanik_transaction_state(
    records: Iterable[Mapping[str, Any]],
    cmds_module=None,
) -> bool:
    """Persist active transaction rows on an owned Maya network node.

    The operation is fail-soft and returns ``False`` when Maya is unavailable
    or an attribute cannot be written.  A failed metadata write must never
    make a successful HumanIK operation fail, since the in-memory transaction
    remains authoritative for the current session.
    """
    try:
        cmds = cmds_module or maya_cmds()
        payload = serialize_humanik_transaction_state(records)
        node = _find_or_create_transaction_node(cmds)
        if node is None:
            return False
        _ensure_string_attr(cmds, node, HUMANIK_TRANSACTION_TAG_ATTR)
        _ensure_string_attr(cmds, node, HUMANIK_TRANSACTION_PAYLOAD_ATTR)
        cmds.setAttr(
            f"{node}.{HUMANIK_TRANSACTION_TAG_ATTR}",
            HUMANIK_TRANSACTION_SCHEMA,
            type="string",
        )
        cmds.setAttr(
            f"{node}.{HUMANIK_TRANSACTION_PAYLOAD_ATTR}",
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            type="string",
        )
        return True
    except Exception as exc:  # noqa: BLE001 - metadata is best-effort
        logger.warning("HumanIK transaction scene persistence skipped: %s", exc)
        return False


def load_humanik_transaction_state(cmds_module=None) -> List[Dict[str, Any]]:
    """Load and validate persisted transaction rows, or return an empty list."""
    try:
        cmds = cmds_module or maya_cmds()
        for node in _transaction_nodes(cmds):
            raw = cmds.getAttr(f"{node}.{HUMANIK_TRANSACTION_PAYLOAD_ATTR}") or ""
            if not raw:
                continue
            return deserialize_humanik_transaction_state(json.loads(str(raw)))
    except Exception as exc:  # noqa: BLE001 - stale/foreign metadata is rejected
        logger.warning("HumanIK transaction scene metadata rejected: %s", exc)
    return []


def _transaction_nodes(cmds) -> List[str]:
    candidates = []
    try:
        candidates.extend(str(node) for node in (cmds.ls(type="network") or []))
    except Exception:
        pass
    try:
        if cmds.objExists(HUMANIK_TRANSACTION_NODE):
            candidates.insert(0, HUMANIK_TRANSACTION_NODE)
    except Exception:
        pass
    result = []
    for node in dict.fromkeys(candidates):
        try:
            if cmds.attributeQuery(HUMANIK_TRANSACTION_TAG_ATTR, node=node, exists=True):
                tag = cmds.getAttr(f"{node}.{HUMANIK_TRANSACTION_TAG_ATTR}")
                if tag == HUMANIK_TRANSACTION_SCHEMA:
                    result.append(node)
        except Exception:
            continue
    return result


def _find_or_create_transaction_node(cmds) -> Optional[str]:
    existing = _transaction_nodes(cmds)
    if existing:
        return existing[0]
    try:
        if cmds.objExists(HUMANIK_TRANSACTION_NODE):
            return None  # a foreign node owns our preferred name
    except Exception:
        pass
    try:
        return str(cmds.createNode("network", name=HUMANIK_TRANSACTION_NODE))
    except Exception:
        return None


def _ensure_string_attr(cmds, node: str, attr: str) -> None:
    if cmds.attributeQuery(attr, node=node, exists=True):
        return
    cmds.addAttr(node, longName=attr, dataType="string")


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
