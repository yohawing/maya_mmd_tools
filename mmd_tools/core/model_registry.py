"""Scene-owned model registry for non-DAG MMD nodes.

The model root remains the user-facing DAG anchor.  This module stores one
root-to-registry message connection and keeps non-DAG ownership in category
member arrays so new imports do not fan ``root.message`` out to every leaf.
Existing ``mmd_model_root`` links are intentionally read only as a fallback;
an invalid registry never falls back to a broader scene scan.
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional

from maya import cmds

from .constants import (
    ATTR_MMD_MODEL_NAME,
    ATTR_MMD_MODEL_NAME_EN,
    ATTR_MMD_MODEL_REGISTRY,
    ATTR_MMD_REGISTRY_MORPH_MEMBERS,
    ATTR_MMD_REGISTRY_MATERIAL_MEMBERS,
    ATTR_MMD_REGISTRY_MATERIAL_MORPH_WORK_MEMBERS,
    ATTR_MMD_REGISTRY_PHYSICS_MEMBERS,
    ATTR_MMD_REGISTRY_ROOT,
    ATTR_MMD_REGISTRY_SCHEMA,
    ATTR_MMD_REGISTRY_TEXTURE_MEMBERS,
    SCENE_ROOT_SUFFIX,
)


REGISTRY_SCHEMA_VERSION = "1"
REGISTRY_CATEGORY_MORPH = "morph"
REGISTRY_CATEGORY_TEXTURE = "texture"
REGISTRY_CATEGORY_PHYSICS = "physics"
REGISTRY_CATEGORY_MATERIAL = "material"
REGISTRY_CATEGORY_MATERIAL_MORPH_WORK = "material_morph_work"

_CATEGORY_ATTRIBUTES: Dict[str, str] = {
    REGISTRY_CATEGORY_MORPH: ATTR_MMD_REGISTRY_MORPH_MEMBERS,
    REGISTRY_CATEGORY_TEXTURE: ATTR_MMD_REGISTRY_TEXTURE_MEMBERS,
    REGISTRY_CATEGORY_PHYSICS: ATTR_MMD_REGISTRY_PHYSICS_MEMBERS,
    REGISTRY_CATEGORY_MATERIAL: ATTR_MMD_REGISTRY_MATERIAL_MEMBERS,
    REGISTRY_CATEGORY_MATERIAL_MORPH_WORK: ATTR_MMD_REGISTRY_MATERIAL_MORPH_WORK_MEMBERS,
}


class ModelRegistryError(RuntimeError):
    """Raised when a registry connection is present but not unambiguous."""


def ensure_model_registry(model_root: str) -> str:
    """Create or validate the one registry owned by ``model_root``.

    The registry is a Maya ``network`` node.  ``model_root.message`` connects
    only to ``registry.modelRoot``; each category stores incoming member
    messages on the registry itself.
    """
    root = _canonical_root(model_root)
    if root is None or not _is_model_root(root):
        raise ModelRegistryError(f"invalid MMD model root: {model_root}")

    existing = get_model_registry(root)
    if existing:
        return existing

    root_leaf = root.rsplit("|", 1)[-1]
    namespace, base_name = _split_namespace(root_leaf)
    registry_name = f":{namespace}:{base_name}_modelRegistry" if namespace else f"{base_name}_modelRegistry"
    registry = cmds.createNode("network", name=registry_name)
    try:
        _ensure_message_attr(registry, ATTR_MMD_REGISTRY_ROOT)
        _ensure_string_attr(registry, ATTR_MMD_REGISTRY_SCHEMA)
        cmds.setAttr(
            f"{registry}.{ATTR_MMD_REGISTRY_SCHEMA}",
            REGISTRY_SCHEMA_VERSION,
            type="string",
        )
        _ensure_message_attr(root, ATTR_MMD_MODEL_REGISTRY)
        cmds.connectAttr(f"{root}.message", f"{registry}.{ATTR_MMD_REGISTRY_ROOT}")
        cmds.connectAttr(f"{registry}.message", f"{root}.{ATTR_MMD_MODEL_REGISTRY}", force=True)
    except Exception:
        if cmds.objExists(registry):
            cmds.delete(registry)
        raise
    return str(registry)


def get_model_registry(model_root: str) -> Optional[str]:
    """Return a validated registry, or ``None`` when the root has no registry."""
    root = _canonical_root(model_root)
    if root is None:
        raise ModelRegistryError(f"invalid MMD model root: {model_root}")
    if not _has_attr(root, ATTR_MMD_MODEL_REGISTRY):
        return None

    registries = cmds.listConnections(
        f"{root}.{ATTR_MMD_MODEL_REGISTRY}",
        source=True,
        destination=False,
    ) or []
    if len(registries) != 1:
        raise ModelRegistryError(
            f"model root has {len(registries)} registry connections: {root}"
        )
    registry = str(registries[0])
    registry_root = _validate_registry_node(registry)
    if not _same_node(registry_root, root):
        raise ModelRegistryError(f"registry belongs to another model root: {registry}")
    return registry


def register_model_members(
    registry: str,
    category: str,
    members: Iterable[str],
) -> List[str]:
    """Register unique member messages under one category and return members."""
    try:
        member_attr = _CATEGORY_ATTRIBUTES[category]
    except KeyError as exc:
        raise ValueError(f"unknown model registry category: {category}") from exc

    _validate_registry_node(registry)
    _ensure_message_attr(registry, member_attr, multi=True)
    existing = list(
        cmds.listConnections(
            f"{registry}.{member_attr}",
            source=True,
            destination=False,
        )
        or []
    )
    registered = list(existing)
    existing_names = {
        canonical
        for canonical in (_canonical_node(member) for member in existing)
        if canonical is not None
    }
    for member in members or []:
        if not member or not cmds.objExists(member):
            continue
        canonical = _canonical_node(member)
        if canonical is None or canonical in existing_names:
            continue
        index = _next_member_index(registry, member_attr, registered)
        cmds.connectAttr(f"{member}.message", f"{registry}.{member_attr}[{index}]")
        registered.append(str(member))
        existing_names.add(canonical)
    return registered


def unregister_model_members(
    registry: str,
    category: str,
    members: Iterable[str],
) -> List[str]:
    """Disconnect exactly the requested owned members from one category.

    The destination plug is resolved from the actual message connection rather
    than inferred from iteration order.  Unknown requested members are
    rejected before any disconnect so callers cannot accidentally report a
    partially applied ownership update.
    """
    member_attr = registry_category_attribute(category)
    _validate_registry_node(registry)
    connections, current = _registry_member_connections(registry, member_attr)

    if isinstance(members, (str, bytes, bytearray)):
        raise ModelRegistryError("members must be an iterable of node names")
    requested_values = list(members or [])
    requested: set[str] = set()
    for member in requested_values:
        if not isinstance(member, str) or not member.strip():
            raise ModelRegistryError(f"requested registry member is invalid: {member!r}")
        try:
            canonical = _canonical_node(member)
        except Exception as exc:
            raise ModelRegistryError(
                f"requested registry member cannot be resolved: {member!r}"
            ) from exc
        if canonical is None:
            raise ModelRegistryError(f"requested registry member is not a valid node: {member!r}")
        requested.add(canonical)

    owned = {canonical for canonical in connections}
    unknown = requested - owned
    if unknown:
        raise ModelRegistryError(
            f"requested registry members are not owned by {registry}: {sorted(unknown)!r}"
        )

    remaining: List[str] = []
    for member in current:
        canonical = _canonical_node(member)
        if canonical in requested:
            source_plug, destination_plug = connections[canonical]
            cmds.disconnectAttr(source_plug, destination_plug)
        else:
            remaining.append(str(member))
    return remaining


def _registry_member_connections(
    registry: str,
    member_attr: str,
) -> tuple[Dict[str, tuple[str, str]], List[str]]:
    """Return canonical member to source/destination plug mappings.

    Maya's ``connections=True, plugs=True`` result is an alternating source /
    destination list.  A compact fallback retains compatibility with command
    adapters that expose only node names; in that case actual sparse indices
    are read from ``multiIndices`` when available.
    """
    endpoint = f"{registry}.{member_attr}"
    records: Dict[str, tuple[str, str]] = {}
    try:
        raw_pairs = cmds.listConnections(
            endpoint,
            source=True,
            destination=False,
            connections=True,
            plugs=True,
        ) or []
    except (TypeError, RuntimeError):
        raw_pairs = []

    values = [str(value) for value in raw_pairs]
    for left, right in zip(values[::2], values[1::2]):
        if left.endswith(".message") and right.startswith(endpoint + "["):
            source_plug, destination_plug = left, right
        elif right.endswith(".message") and left.startswith(endpoint + "["):
            source_plug, destination_plug = right, left
        else:
            continue
        member = source_plug.rsplit(".", 1)[0]
        canonical = _canonical_node(member)
        if canonical is None:
            continue
        if canonical in records and records[canonical][1] != destination_plug:
            raise ModelRegistryError(f"registry member connection is ambiguous: {member}")
        records[canonical] = (source_plug, destination_plug)

    current_raw = list(cmds.listConnections(endpoint, source=True, destination=False) or [])
    if records:
        def destination_index(item: tuple[str, tuple[str, str]]) -> int:
            destination = item[1][1]
            try:
                return int(destination.rsplit("[", 1)[1].rstrip("]"))
            except (IndexError, ValueError) as exc:
                raise ModelRegistryError(
                    f"registry member destination plug is malformed: {destination!r}"
                ) from exc

        ordered = sorted(
            records.items(),
            key=destination_index,
        )
        return dict(records), [source.rsplit(".", 1)[0] for _, (source, _) in ordered]

    current = [str(member) for member in current_raw]
    indices: list[int] = []
    try:
        raw_indices = cmds.getAttr(endpoint, multiIndices=True) or []
        if isinstance(raw_indices, (list, tuple)) and all(
            type(index) is int and index >= 0 for index in raw_indices
        ):
            indices = list(raw_indices)
    except (TypeError, RuntimeError, ValueError):
        indices = []
    if len(indices) != len(current):
        indices = list(range(len(current)))
    for member, index in zip(current, indices):
        canonical = _canonical_node(member)
        if canonical is None:
            continue
        destination = f"{endpoint}[{index}]"
        source = f"{member}.message"
        if canonical in records and records[canonical][1] != destination:
            raise ModelRegistryError(f"registry member connection is ambiguous: {member}")
        records[canonical] = (source, destination)
    return records, current


def list_model_registry_members(model_root: str, category: str) -> Optional[List[str]]:
    """List registry members, or ``None`` when the scene uses legacy ownership."""
    try:
        member_attr = _CATEGORY_ATTRIBUTES[category]
    except KeyError as exc:
        raise ValueError(f"unknown model registry category: {category}") from exc
    registry = get_model_registry(model_root)
    if registry is None:
        return None
    if not _has_attr(registry, member_attr):
        return []
    return list(
        cmds.listConnections(
            f"{registry}.{member_attr}",
            source=True,
            destination=False,
        )
        or []
    )


def list_model_registry_members_from_adapter(
    adapter,
    model_root: str,
    category: str,
) -> Optional[List[str]]:
    """Adapter-based equivalent used by UI presenters without global cmds."""
    member_attr = registry_category_attribute(category)
    try:
        if not adapter.attribute_exists(ATTR_MMD_MODEL_REGISTRY, model_root):
            return None
        registries = adapter.list_connections(
            f"{model_root}.{ATTR_MMD_MODEL_REGISTRY}",
            source=True,
            destination=False,
        ) or []
        if len(registries) != 1:
            return []
        registry = registries[0]
        if not adapter.attribute_exists(ATTR_MMD_REGISTRY_SCHEMA, registry):
            return []
        get_attr = getattr(adapter, "get_attr", None)
        if not callable(get_attr):
            return []
        if str(get_attr(f"{registry}.{ATTR_MMD_REGISTRY_SCHEMA}") or "") != REGISTRY_SCHEMA_VERSION:
            return []
        if not adapter.attribute_exists(ATTR_MMD_REGISTRY_ROOT, registry):
            return []
        roots = adapter.list_connections(
            f"{registry}.{ATTR_MMD_REGISTRY_ROOT}",
            source=True,
            destination=False,
        ) or []
        if len(roots) != 1 or not _same_adapter_node(adapter, roots[0], model_root):
            return []
        if not adapter.attribute_exists(member_attr, registry):
            return []
        return adapter.list_connections(
            f"{registry}.{member_attr}",
            source=True,
            destination=False,
        ) or []
    except Exception:
        return []


def registry_category_attribute(category: str) -> str:
    """Return the persisted member attribute for a registry category."""
    try:
        return _CATEGORY_ATTRIBUTES[category]
    except KeyError as exc:
        raise ValueError(f"unknown model registry category: {category}") from exc


def _canonical_root(model_root: str) -> Optional[str]:
    if not model_root or not cmds.objExists(model_root):
        return None
    matches = cmds.ls(model_root, long=True) or []
    if len(matches) != 1:
        return None
    root = str(matches[0])
    if not root.startswith("|"):
        return None
    return root


def _validate_registry_node(registry: str) -> str:
    """Validate schema and both sides of one registry/root ownership link."""
    if not cmds.objExists(registry):
        raise ModelRegistryError(f"registry does not exist: {registry}")
    if not _has_attr(registry, ATTR_MMD_REGISTRY_SCHEMA) or not _has_attr(
        registry, ATTR_MMD_REGISTRY_ROOT
    ):
        raise ModelRegistryError(f"registry schema is incomplete: {registry}")
    try:
        schema = str(cmds.getAttr(f"{registry}.{ATTR_MMD_REGISTRY_SCHEMA}") or "")
    except Exception as exc:
        raise ModelRegistryError(f"registry schema cannot be read: {registry}") from exc
    if schema != REGISTRY_SCHEMA_VERSION:
        raise ModelRegistryError(
            f"unsupported registry schema {schema!r}: {registry}"
        )

    connected_roots = cmds.listConnections(
        f"{registry}.{ATTR_MMD_REGISTRY_ROOT}",
        source=True,
        destination=False,
    ) or []
    if len(connected_roots) != 1:
        raise ModelRegistryError(f"registry root connection is ambiguous: {registry}")
    root = _canonical_root(connected_roots[0])
    if root is None or not _is_model_root(root):
        raise ModelRegistryError(f"registry root is not an MMD model root: {registry}")
    if not _has_attr(root, ATTR_MMD_MODEL_REGISTRY):
        raise ModelRegistryError(f"model root does not reference registry: {registry}")
    owner_registries = cmds.listConnections(
        f"{root}.{ATTR_MMD_MODEL_REGISTRY}",
        source=True,
        destination=False,
    ) or []
    if len(owner_registries) != 1 or not _same_node(owner_registries[0], registry):
        raise ModelRegistryError(f"registry owner connection is ambiguous: {registry}")
    return root


def _is_model_root(root: str) -> bool:
    return root.rsplit("|", 1)[-1].endswith(SCENE_ROOT_SUFFIX) or _has_attr(
        root,
        ATTR_MMD_MODEL_NAME,
    ) or _has_attr(root, ATTR_MMD_MODEL_NAME_EN)


def _split_namespace(leaf: str) -> tuple[str, str]:
    if ":" not in leaf:
        return "", leaf
    namespace, base_name = leaf.rsplit(":", 1)
    return namespace, base_name


def _ensure_message_attr(node: str, attribute: str, *, multi: bool = False) -> None:
    if _has_attr(node, attribute):
        return
    kwargs = {"longName": attribute, "attributeType": "message"}
    if multi:
        kwargs["multi"] = True
    cmds.addAttr(node, **kwargs)


def _ensure_string_attr(node: str, attribute: str) -> None:
    if not _has_attr(node, attribute):
        cmds.addAttr(node, longName=attribute, dataType="string")


def _next_member_index(registry: str, member_attr: str, registered: List[str]) -> int:
    try:
        indices = cmds.getAttr(f"{registry}.{member_attr}", multiIndices=True) or []
    except Exception:
        indices = []
    if indices:
        return max(int(index) for index in indices) + 1
    return len(registered)


def _same_node(left: str, right: str) -> bool:
    left_name = _canonical_node(left)
    right_name = _canonical_node(right)
    return left_name is not None and left_name == right_name


def _canonical_node(node: str) -> Optional[str]:
    if not node or not cmds.objExists(node):
        return None
    matches = cmds.ls(node, long=True) or []
    return str(matches[0]) if len(matches) == 1 else None


def _same_adapter_node(adapter, left: str, right: str) -> bool:
    if left == right:
        return True
    left_names = adapter.ls(left, long=True) or []
    right_names = adapter.ls(right, long=True) or []
    return len(left_names) == 1 and len(right_names) == 1 and left_names[0] == right_names[0]


def _has_attr(node: str, attribute: str) -> bool:
    try:
        return bool(cmds.attributeQuery(attribute, node=node, exists=True))
    except Exception:
        return False


__all__ = [
    "ModelRegistryError",
    "REGISTRY_CATEGORY_MORPH",
    "REGISTRY_CATEGORY_MATERIAL",
    "REGISTRY_CATEGORY_PHYSICS",
    "REGISTRY_CATEGORY_TEXTURE",
    "REGISTRY_SCHEMA_VERSION",
    "ensure_model_registry",
    "get_model_registry",
    "list_model_registry_members",
    "list_model_registry_members_from_adapter",
    "register_model_members",
    "unregister_model_members",
    "registry_category_attribute",
]
