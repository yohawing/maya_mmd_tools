"""Reversible HumanIK scene transaction restore_state.

The restore_state captures only explicitly scoped plugs and nodes.  It restores exact
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

# Scene-persisted restore state lives on one non-DAG ``network`` node. The
# version/schema guard keeps a future plugin (or hand-edited scene data) from
# reconstructing state it cannot safely understand, while keeping storage off
# user-facing MMD model roots and out of the normal Outliner hierarchy.
HUMANIK_RESTORE_STATE_SCHEMA = "mmd_tools.humanik_restore_state"
HUMANIK_RESTORE_STATE_VERSION = 1
HUMANIK_RESTORE_STATE_NODE = "mmdHumanIkRestoreState"
HUMANIK_RESTORE_STATE_TAG_ATTR = "mmd_humanik_restore_state_schema"
HUMANIK_RESTORE_STATE_PAYLOAD_ATTR = "mmd_humanik_restore_state_payload"


def _node_uuid(cmds, node: str) -> Optional[str]:
    """Return one unambiguous Maya UUID for ``node`` when available."""
    try:
        values = cmds.ls(str(node), uuid=True) or []
    except Exception:
        return None
    return str(values[0]) if len(values) == 1 and values[0] else None


def _resolve_node_uuid(cmds, node: str, node_uuid: Optional[str]) -> Optional[str]:
    """Resolve a captured node by UUID, rejecting a reused/foreign name.

    Runtime transactions created by older host-neutral callers may not carry
    UUIDs and therefore retain their historical name-based behavior.  Scene
    payloads are validated strictly before they reach this helper, so a UUID
    present here is authoritative and a name collision is never silently
    adopted.
    """
    name = str(node)
    if not node_uuid:
        return name if cmds.objExists(name) else None
    try:
        matches = cmds.ls(str(node_uuid), long=True) or []
    except Exception as exc:
        raise RuntimeError(
            f"HumanIK restore_state UUID lookup failed for {name}: {exc}"
        ) from exc
    if len(matches) > 1:
        raise RuntimeError(
            f"HumanIK restore_state UUID is ambiguous for {name}: {node_uuid}"
        )
    if matches:
        return str(matches[0])
    if cmds.objExists(name):
        raise RuntimeError(
            f"HumanIK restore_state node UUID drift for {name}: expected {node_uuid}"
        )
    return None


@dataclass
class HumanIkPlugSnapshot:
    """Exact value and incoming sources for one destination plug."""

    plug: str
    sources: List[str]
    value: Any
    attr_type: str
    node_uuid: Optional[str] = None


@dataclass
class HumanIkNodeSnapshot:
    """Mutable enable-state attributes captured for one scene node."""

    node: str
    attributes: Dict[str, Any]
    node_uuid: Optional[str] = None


@dataclass
class HumanIkRestoreState:
    """JSON-safe reversible state for one character ownership operation."""

    ownership_id: str
    character: str
    lock_state: bool
    input_source: str
    input_type: int
    plugs: List[HumanIkPlugSnapshot]
    nodes: List[HumanIkNodeSnapshot]
    character_uuid: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Return a deterministic serialisable restore_state payload."""
        return asdict(self)

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, Any],
        *,
        require_authority: bool = False,
    ) -> "HumanIkRestoreState":
        """Reconstruct a restore_state after validating its persisted shape.

        Scene metadata is user-editable Maya data, so restoration must fail
        closed on malformed rows instead of relying on ``dict`` unpacking and
        accidentally accepting a stale/foreign payload.
        """
        if not isinstance(payload, Mapping):
            raise ValueError("HumanIK restore_state payload must be an object")
        required = ("ownership_id", "character", "lock_state", "input_source", "input_type", "plugs", "nodes")
        missing = [key for key in required if key not in payload]
        if missing:
            raise ValueError("HumanIK restore_state payload missing: " + ", ".join(missing))
        ownership_id = payload["ownership_id"]
        character = payload["character"]
        input_source = payload["input_source"]
        character_uuid = payload.get("character_uuid")
        if character_uuid is not None and (
            not isinstance(character_uuid, str) or not character_uuid
        ):
            raise ValueError("HumanIK restore_state character_uuid is invalid")
        if require_authority and character_uuid is None:
            raise ValueError("HumanIK restore_state character_uuid is required")
        if not all(isinstance(value, str) and value for value in (ownership_id, character)):
            raise ValueError("HumanIK restore_state ownership_id and character must be non-empty strings")
        if not isinstance(input_source, str):
            raise ValueError("HumanIK restore_state input_source must be a string")
        if not isinstance(payload["lock_state"], bool):
            raise ValueError("HumanIK restore_state lock_state must be boolean")
        if not isinstance(payload["input_type"], int) or isinstance(payload["input_type"], bool):
            raise ValueError("HumanIK restore_state input_type must be an integer")

        plugs = []
        raw_plugs = payload["plugs"]
        if not isinstance(raw_plugs, list):
            raise ValueError("HumanIK restore_state plugs must be an array")
        for row in raw_plugs:
            if not isinstance(row, Mapping):
                raise ValueError("HumanIK restore_state plug row must be an object")
            plug = row.get("plug")
            sources = row.get("sources")
            attr_type = row.get("attr_type")
            node_uuid = row.get("node_uuid")
            if not isinstance(plug, str) or not plug or not isinstance(attr_type, str):
                raise ValueError("HumanIK restore_state plug row has invalid plug or attr_type")
            if node_uuid is not None and (
                not isinstance(node_uuid, str) or not node_uuid
            ):
                raise ValueError("HumanIK restore_state plug node_uuid is invalid")
            if require_authority and node_uuid is None:
                raise ValueError(
                    f"HumanIK restore_state plug node_uuid is required: {plug}"
                )
            if not isinstance(sources, list) or not all(isinstance(source, str) for source in sources):
                raise ValueError("HumanIK restore_state plug sources must be an array of strings")
            value = row.get("value")
            try:
                json.dumps(value, ensure_ascii=False)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"HumanIK restore_state plug value is not JSON-safe: {plug}") from exc
            plugs.append(
                HumanIkPlugSnapshot(
                    plug,
                    list(sources),
                    value,
                    attr_type,
                    node_uuid,
                )
            )

        nodes = []
        raw_nodes = payload["nodes"]
        if not isinstance(raw_nodes, list):
            raise ValueError("HumanIK restore_state nodes must be an array")
        for row in raw_nodes:
            if not isinstance(row, Mapping) or not isinstance(row.get("node"), str) or not row.get("node"):
                raise ValueError("HumanIK restore_state node row has invalid node")
            attributes = row.get("attributes")
            node_uuid = row.get("node_uuid")
            if not isinstance(attributes, Mapping):
                raise ValueError("HumanIK restore_state node attributes must be an object")
            if node_uuid is not None and (
                not isinstance(node_uuid, str) or not node_uuid
            ):
                raise ValueError("HumanIK restore_state node node_uuid is invalid")
            if require_authority and node_uuid is None:
                raise ValueError(
                    f"HumanIK restore_state node node_uuid is required: {row['node']}"
                )
            try:
                json.dumps(attributes, ensure_ascii=False)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"HumanIK restore_state node attributes are not JSON-safe: {row['node']}") from exc
            nodes.append(
                HumanIkNodeSnapshot(
                    str(row["node"]),
                    dict(attributes),
                    node_uuid,
                )
            )
        return cls(
            ownership_id=str(ownership_id),
            character=str(character),
            lock_state=bool(payload["lock_state"]),
            input_source=input_source,
            input_type=int(payload["input_type"]),
            plugs=plugs,
            nodes=nodes,
            character_uuid=character_uuid,
        )


def serialize_humanik_restore_state(records: Iterable[Mapping[str, Any]]) -> Dict[str, Any]:
    """Build the versioned scene payload for active frontend transactions."""
    rows = []
    for record in records:
        if not isinstance(record, Mapping):
            raise ValueError("HumanIK transaction record must be an object")
        # Validate the nested restore_state before writing scene metadata.  This is
        # also useful for callers using lightweight test doubles: a fake
        # transaction is simply not eligible for persistence in the frontend.
        restore_state = HumanIkRestoreState.from_dict(
            record.get("restore_state", {}),
            require_authority=True,
        )
        row = dict(record)
        row["restore_state"] = restore_state.to_dict()
        row["active"] = bool(row.get("active", True))
        if not isinstance(row.get("modelRoot"), str) or not row["modelRoot"]:
            raise ValueError("HumanIK transaction modelRoot must be a non-empty string")
        if not isinstance(row.get("character"), str) or not row["character"]:
            raise ValueError("HumanIK transaction character must be a non-empty string")
        if row["character"] != restore_state.character:
            raise ValueError("HumanIK transaction restore_state character mismatch")
        for key in ("modelRootUuid", "characterUuid"):
            if not isinstance(row.get(key), str) or not row[key]:
                raise ValueError(
                    f"HumanIK transaction {key} must be a non-empty string"
                )
        if row["characterUuid"] != restore_state.character_uuid:
            raise ValueError("HumanIK transaction character UUID mismatch")
        rows.append(row)
    rows.sort(key=lambda item: item["modelRoot"])
    return {
        "schema": HUMANIK_RESTORE_STATE_SCHEMA,
        "version": HUMANIK_RESTORE_STATE_VERSION,
        "transactions": rows,
    }


def deserialize_humanik_restore_state(payload: Any) -> List[Dict[str, Any]]:
    """Validate a scene payload and return reconstructable transaction rows.

    ``ValueError`` is intentional for malformed/stale metadata; frontend
    startup catches it, reports the data as unusable, and leaves the scene
    untouched rather than guessing ownership.
    """
    if not isinstance(payload, Mapping):
        raise ValueError("HumanIK transaction scene payload must be an object")
    if payload.get("schema") != HUMANIK_RESTORE_STATE_SCHEMA:
        raise ValueError("HumanIK transaction scene schema mismatch")
    if payload.get("version") != HUMANIK_RESTORE_STATE_VERSION:
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
        for key in ("modelRootUuid", "characterUuid"):
            if not isinstance(row.get(key), str) or not row[key]:
                raise ValueError(
                    f"HumanIK transaction scene row has invalid {key}"
                )
        restore_state = HumanIkRestoreState.from_dict(
            row.get("restore_state", {}),
            require_authority=True,
        )
        if restore_state.character != character:
            raise ValueError("HumanIK transaction restore_state character mismatch")
        ownership_id = row.get("ownershipId")
        if not isinstance(ownership_id, str) or not ownership_id:
            raise ValueError("HumanIK transaction scene row has invalid ownershipId")
        if restore_state.ownership_id != ownership_id:
            raise ValueError("HumanIK transaction restore_state ownership mismatch")
        if restore_state.character_uuid != row["characterUuid"]:
            raise ValueError("HumanIK transaction restore_state character UUID mismatch")
        active = row.get("active", True)
        if not isinstance(active, bool):
            raise ValueError("HumanIK transaction scene row has invalid active flag")
        result.append({**dict(row), "restore_state": restore_state.to_dict(), "active": active})
    return result


def persist_humanik_restore_state(
    records: Iterable[Mapping[str, Any]],
    cmds_module=None,
) -> bool:
    """Persist active restore-state rows on an internal Maya network node.

    The operation is fail-soft and returns ``False`` when Maya is unavailable
    or an attribute cannot be written.  A failed metadata write must never
    make a successful HumanIK operation fail, since the in-memory transaction
    remains authoritative for the current session.
    """
    try:
        cmds = cmds_module or maya_cmds()
        payload = serialize_humanik_restore_state(records)
        node = _find_or_create_restore_state_node(cmds)
        if node is None:
            return False
        _ensure_string_attr(cmds, node, HUMANIK_RESTORE_STATE_TAG_ATTR)
        _ensure_string_attr(cmds, node, HUMANIK_RESTORE_STATE_PAYLOAD_ATTR)
        cmds.setAttr(
            f"{node}.{HUMANIK_RESTORE_STATE_TAG_ATTR}",
            HUMANIK_RESTORE_STATE_SCHEMA,
            type="string",
        )
        cmds.setAttr(
            f"{node}.{HUMANIK_RESTORE_STATE_PAYLOAD_ATTR}",
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            type="string",
        )
        return True
    except Exception as exc:  # noqa: BLE001 - metadata is best-effort
        logger.warning("HumanIK transaction scene persistence skipped: %s", exc)
        return False


def load_humanik_restore_state(cmds_module=None) -> List[Dict[str, Any]]:
    """Load valid restore-state rows from the internal network node."""
    try:
        cmds = cmds_module or maya_cmds()
        for node in _restore_state_nodes(cmds):
            raw = cmds.getAttr(f"{node}.{HUMANIK_RESTORE_STATE_PAYLOAD_ATTR}") or ""
            if raw:
                return deserialize_humanik_restore_state(json.loads(str(raw)))
    except Exception as exc:  # noqa: BLE001 - stale/foreign metadata is rejected
        logger.warning("HumanIK restore state rejected: %s", exc)
    return []


def _restore_state_nodes(cmds) -> List[str]:
    candidates = []
    try:
        candidates.extend(str(node) for node in (cmds.ls(type="network") or []))
    except Exception:
        pass
    try:
        if cmds.objExists(HUMANIK_RESTORE_STATE_NODE):
            candidates.insert(0, HUMANIK_RESTORE_STATE_NODE)
    except Exception:
        pass
    result = []
    for node in dict.fromkeys(candidates):
        try:
            if cmds.attributeQuery(HUMANIK_RESTORE_STATE_TAG_ATTR, node=node, exists=True):
                if cmds.getAttr(f"{node}.{HUMANIK_RESTORE_STATE_TAG_ATTR}") == HUMANIK_RESTORE_STATE_SCHEMA:
                    result.append(node)
        except Exception:
            continue
    return result


def _find_or_create_restore_state_node(cmds) -> Optional[str]:
    existing = _restore_state_nodes(cmds)
    if existing:
        return existing[0]
    try:
        if cmds.objExists(HUMANIK_RESTORE_STATE_NODE):
            return None  # a foreign node owns the reserved storage name
    except Exception:
        pass
    try:
        return str(cmds.createNode("network", name=HUMANIK_RESTORE_STATE_NODE))
    except Exception:
        return None


def _ensure_string_attr(cmds, node: str, attr: str) -> None:
    if cmds.attributeQuery(attr, node=node, exists=True):
        return
    cmds.addAttr(node, longName=attr, dataType="string")


def capture_humanik_restore_state(
    ownership_id: str,
    character: str,
    destination_plugs: Iterable[str],
    nodes: Iterable[str],
    cmds_module=None,
    mel_module=None,
) -> HumanIkRestoreState:
    """Capture a scoped HumanIK transaction restore_state without editing the scene."""
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
    return HumanIkRestoreState(
        ownership_id=str(ownership_id),
        character=str(character),
        lock_state=get_humanik_definition_lock_state(character, mel),
        input_source=source,
        input_type=input_type,
        plugs=plugs,
        nodes=node_snapshots,
        character_uuid=_node_uuid(cmds, character),
    )


def apply_humanik_restore_state(
    restore_state: HumanIkRestoreState,
    ownership_id: Optional[str] = None,
    cmds_module=None,
    mel_module=None,
) -> List[str]:
    """Restore a restore_state exactly; repeated restores are idempotent.

    A restore_state entry (the character itself, a plug's node, a reconnect
    source, or a state node) that no longer exists in the scene -- because
    the user deleted it, or because a Control Rig teardown step already
    removed it -- is skipped with a logged warning instead of raising: there
    is nothing to restore back onto a node that is gone, and treating that
    as fatal is what previously left ``HumanIkControlRigTransaction``s stuck
    permanently active (every retry hit the same "node not found" MEL error
    from ``apply_humanik_restore_state`` before it ever reached the
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
        captured node was present in the scene).

    Raises:
        ValueError: ``ownership_id`` does not match the restore_state's owner.
        RuntimeError: Aggregated restore failures against nodes that still
            exist in the scene.
    """
    if ownership_id is not None and ownership_id != restore_state.ownership_id:
        raise ValueError("HumanIK restore_state ownership mismatch")
    cmds = cmds_module or maya_cmds()
    mel = mel_module or maya_mel()
    ensure_humanik_mel_loaded(mel)

    warnings: List[str] = []
    failures: List[str] = []

    # Resolve every captured destination by its persisted node identity before
    # touching HIK input/lock state or disconnecting any writer.  A name that
    # now refers to another node is foreign topology, not a recoverable rename.
    character = _resolve_node_uuid(
        cmds,
        restore_state.character,
        restore_state.character_uuid,
    )
    live_plugs = []
    for snapshot in restore_state.plugs:
        node = snapshot.plug.rsplit(".", 1)[0]
        resolved_node = _resolve_node_uuid(cmds, node, snapshot.node_uuid)
        if resolved_node is None:
            warnings.append(
                f"HumanIK restore_state skip: plug node no longer exists: {snapshot.plug}"
            )
            continue
        attribute = snapshot.plug.rsplit(".", 1)[-1]
        live_plugs.append((snapshot, f"{resolved_node}.{attribute}"))

    live_nodes = []
    for snapshot in restore_state.nodes:
        resolved_node = _resolve_node_uuid(cmds, snapshot.node, snapshot.node_uuid)
        if resolved_node is None:
            warnings.append(
                f"HumanIK restore_state skip: node no longer exists: {snapshot.node}"
            )
            continue
        live_nodes.append((snapshot, resolved_node))

    # HIK deletion is expected to leave captured MMD writers disconnected (or
    # already restored).  Any other incoming source is a foreign writer that
    # appeared while the transaction was active; fail closed before the first
    # disconnect/value/MEL mutation so the user can inspect and repair it.
    topology_drift = []
    for snapshot, destination in live_plugs:
        actual = incoming_sources(cmds, destination)
        unexpected = sorted(set(actual) - set(snapshot.sources))
        if unexpected:
            topology_drift.append(
                {
                    "destination": destination,
                    "expected": sorted(snapshot.sources),
                    "actual": actual,
                    "unexpected": unexpected,
                }
            )
    if topology_drift:
        raise RuntimeError(
            "HumanIK restore_state foreign topology drift: "
            + "; ".join(
                f"{row['destination']} unexpected={row['unexpected']}"
                for row in topology_drift
            )
        )

    if character is not None:
        try:
            current_source = str(
                mel.eval(f"hikGetRetargetCharacterInput({mel_string(character)})") or ""
            )
            if current_source != restore_state.input_source:
                mel.eval(
                    f"hikSetCharacterInput({mel_string(character)}, "
                    f"{mel_string(restore_state.input_source)});"
                )
            current_lock = get_humanik_definition_lock_state(character, mel)
            if current_lock != restore_state.lock_state:
                mel.eval(
                    f"hikCharacterLock({mel_string(character)}, "
                    f"{1 if restore_state.lock_state else 0}, 1);"
                )
        except Exception as exc:  # noqa: BLE001 - aggregated below, character node exists
            failures.append(
                f"character input/lock restore failed for {character}: {exc}"
            )
    else:
        warnings.append(
            f"HumanIK restore_state skip: character node no longer exists: {restore_state.character}"
        )

    for snapshot, destination in live_plugs:
        try:
            for source in incoming_sources(cmds, destination):
                cmds.disconnectAttr(source, destination)
        except Exception as exc:  # noqa: BLE001 - aggregated below
            failures.append(f"disconnect failed for {destination}: {exc}")
    for snapshot, destination in live_plugs:
        if snapshot.sources:
            continue
        try:
            _set_plug_value(cmds, destination, snapshot.value, snapshot.attr_type)
        except Exception as exc:  # noqa: BLE001 - aggregated below
            failures.append(f"value restore failed for {destination}: {exc}")
    for snapshot, destination in live_plugs:
        for source in snapshot.sources:
            source_node = source.rsplit(".", 1)[0]
            if not cmds.objExists(source_node):
                warnings.append(
                    f"HumanIK restore_state skip: reconnect source no longer exists: "
                    f"{source} -> {destination}"
                )
                continue
            try:
                if not _is_connected(cmds, source, destination):
                    cmds.connectAttr(source, destination, force=True)
            except Exception as exc:  # noqa: BLE001 - aggregated below
                failures.append(
                    f"reconnect failed for {source} -> {destination}: {exc}"
                )

    for snapshot, node in live_nodes:
        for attr, value in snapshot.attributes.items():
            try:
                cmds.setAttr(f"{node}.{attr}", value)
            except Exception as exc:  # noqa: BLE001 - aggregated below
                failures.append(
                    f"attribute restore failed for {node}.{attr}: {exc}"
                )

    for message in warnings:
        logger.warning(message)
    if failures:
        raise RuntimeError(
            "HumanIK restore_state restore failed for existing nodes: " + "; ".join(failures)
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
) -> Iterator[HumanIkRestoreState]:
    """Capture state and automatically roll it back when the body raises."""
    restore_state = capture_humanik_restore_state(
        ownership_id,
        character,
        destination_plugs,
        nodes,
        cmds_module=cmds_module,
        mel_module=mel_module,
    )
    try:
        yield restore_state
    except Exception:
        apply_humanik_restore_state(
            restore_state,
            ownership_id=ownership_id,
            cmds_module=cmds_module,
            mel_module=mel_module,
        )
        raise


def _capture_plug(cmds, plug: str) -> HumanIkPlugSnapshot:
    destination = str(plug)
    return HumanIkPlugSnapshot(
        plug=destination,
        sources=incoming_sources(cmds, destination),
        value=cmds.getAttr(destination),
        attr_type=str(cmds.getAttr(destination, type=True) or ""),
        node_uuid=_node_uuid(cmds, destination.rsplit(".", 1)[0]),
    )


def _capture_node(cmds, node: str) -> HumanIkNodeSnapshot:
    attributes = {}
    for attr in STATE_ATTRIBUTES:
        if cmds.attributeQuery(attr, node=node, exists=True):
            attributes[attr] = cmds.getAttr(f"{node}.{attr}")
    return HumanIkNodeSnapshot(
        node=str(node),
        attributes=attributes,
        node_uuid=_node_uuid(cmds, node),
    )


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
