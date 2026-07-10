"""Model-root visibility attributes shared by Animation and Physics UI."""

from __future__ import annotations

from .constants import (
    ATTR_MMD_SHOW_CONTROLLERS,
    ATTR_MMD_SHOW_IK,
    ATTR_MMD_SHOW_JOINTS,
    ATTR_MMD_SHOW_MESH,
    ATTR_MMD_SHOW_PHYSICS_COLLIDERS,
)

VISIBILITY_CATEGORY_ATTRS = {
    "mesh": ATTR_MMD_SHOW_MESH,
    "joints": ATTR_MMD_SHOW_JOINTS,
    "ik": ATTR_MMD_SHOW_IK,
    "controllers": ATTR_MMD_SHOW_CONTROLLERS,
    "colliders": ATTR_MMD_SHOW_PHYSICS_COLLIDERS,
}

DEFAULT_VISIBILITY_ATTR_VALUES = {
    ATTR_MMD_SHOW_MESH: True,
    ATTR_MMD_SHOW_JOINTS: True,
    ATTR_MMD_SHOW_IK: True,
    ATTR_MMD_SHOW_CONTROLLERS: True,
    ATTR_MMD_SHOW_PHYSICS_COLLIDERS: False,
}

_VIS_NODE_TYPES = {
    "mesh": "mesh",
    "joints": "joint",
    "ik": "ikHandle",
    "controllers": "locator",
    "colliders": "mmdRigidBodyLocator",
}


def ensure_visibility_attrs(adapter, model_root: str) -> None:
    """Ensure model-root viewport visibility attrs exist with release defaults."""
    if not model_root:
        return
    for attr, default in DEFAULT_VISIBILITY_ATTR_VALUES.items():
        try:
            if adapter.attribute_exists(attr, model_root):
                _show_bool_attr(adapter, model_root, attr)
                continue
            _add_bool_attr(adapter, model_root, attr)
            adapter.set_attr(f"{model_root}.{attr}", bool(default))
            _show_bool_attr(adapter, model_root, attr)
        except Exception:
            continue


def get_visibility_category(adapter, model_root: str, category: str) -> bool:
    """Read one visibility category from the model root, adding attrs if needed."""
    attr = VISIBILITY_CATEGORY_ATTRS.get(category)
    if not attr or not model_root:
        return True
    ensure_visibility_attrs(adapter, model_root)
    try:
        return bool(adapter.get_attr(f"{model_root}.{attr}"))
    except Exception:
        return bool(DEFAULT_VISIBILITY_ATTR_VALUES.get(attr, True))


def set_visibility_category(adapter, model_root: str, category: str, visible: bool) -> None:
    """Write one visibility category to the model root."""
    attr = VISIBILITY_CATEGORY_ATTRS.get(category)
    if not attr or not model_root:
        return
    ensure_visibility_attrs(adapter, model_root)
    adapter.set_attr(f"{model_root}.{attr}", bool(visible))


def sync_visibility_connections(adapter, model_root: str, category: str | None = None) -> None:
    """Connect model-root visibility attrs to existing display nodes."""
    if not model_root:
        return
    ensure_visibility_attrs(adapter, model_root)
    categories = [category] if category else list(VISIBILITY_CATEGORY_ATTRS)
    for item in categories:
        attr = VISIBILITY_CATEGORY_ATTRS.get(item)
        if not attr or item == "morphs":
            continue
        for node, target_attr in _iter_category_targets(adapter, model_root, item):
            connect_visibility_attr_to_node(
                adapter,
                model_root,
                item,
                node,
                target_attr=target_attr,
            )


def connect_visibility_attr_to_node(
    adapter,
    model_root: str,
    category: str,
    node: str,
    *,
    target_attr: str | None = None,
) -> None:
    """Connect or mirror one root visibility attr to a display node attr."""
    attr = VISIBILITY_CATEGORY_ATTRS.get(category)
    if not attr or not model_root or not node:
        return
    ensure_visibility_attrs(adapter, model_root)
    target_attr = target_attr or ("drawEnabled" if category == "colliders" else "visibility")
    source = f"{model_root}.{attr}"
    destination = f"{node}.{target_attr}"
    try:
        existing_sources = _source_connections(adapter, destination)
        if source in existing_sources:
            return
        if existing_sources:
            return
        if hasattr(adapter, "connect_attr"):
            adapter.connect_attr(source, destination, force=False)
            return
    except Exception:
        pass
    try:
        adapter.set_attr(destination, bool(adapter.get_attr(source)))
    except Exception:
        pass


def _iter_category_targets(adapter, model_root: str, category: str):
    if category == "colliders":
        yield from _iter_collider_targets(adapter, model_root)
        return

    node_type = _VIS_NODE_TYPES.get(category)
    if not node_type:
        return
    try:
        descendants = adapter.list_relatives(
            model_root,
            allDescendents=True,
            type=node_type,
            fullPath=True,
        ) or []
    except Exception:
        return
    if category == "mesh":
        seen = set()
        for mesh in descendants:
            try:
                parents = adapter.list_relatives(mesh, parent=True, fullPath=True) or []
            except Exception:
                parents = []
            for parent in parents:
                if parent in seen:
                    continue
                seen.add(parent)
                yield parent, "visibility"
        return
    target_attr = "drawEnabled" if category == "colliders" else "visibility"
    for node in descendants:
        if category == "controllers" and _node_type(adapter, node) == "mmdRigidBodyLocator":
            continue
        yield node, target_attr


def _iter_collider_targets(adapter, model_root: str):
    # Primary display target: the model root's direct Physics group.
    try:
        children = adapter.list_relatives(
            model_root,
            children=True,
            type="transform",
            fullPath=True,
        ) or []
    except Exception:
        children = []
    for child in children:
        if child.rsplit("|", 1)[-1].rsplit(":", 1)[-1] == "Physics":
            yield child, "visibility"
            break

    # Legacy/fallback targets: per-rigid-body locators and curve groups.
    try:
        locators = adapter.list_relatives(
            model_root,
            allDescendents=True,
            type="mmdRigidBodyLocator",
            fullPath=True,
        ) or []
    except Exception:
        locators = []
    for locator in locators:
        yield locator, "drawEnabled"

    try:
        transforms = adapter.list_relatives(
            model_root,
            allDescendents=True,
            type="transform",
            fullPath=True,
        ) or []
    except Exception:
        transforms = []
    for node in transforms:
        if node.rsplit("|", 1)[-1].endswith("_colliderCurve"):
            yield node, "visibility"


def _add_bool_attr(adapter, node: str, attr: str) -> None:
    if hasattr(adapter, "add_attr"):
        adapter.add_attr(node, longName=attr, attributeType="bool", keyable=True)
        return
    cmds_module = getattr(adapter, "_cmds", None)
    if cmds_module is not None:
        cmds_module.addAttr(node, longName=attr, attributeType="bool", keyable=True)


def _show_bool_attr(adapter, node: str, attr: str) -> None:
    attr_path = f"{node}.{attr}"
    try:
        value = bool(adapter.get_attr(attr_path))
        adapter.set_attr(attr_path, value, keyable=True)
        return
    except Exception:
        pass
    cmds_module = getattr(adapter, "_cmds", None)
    if cmds_module is not None:
        cmds_module.setAttr(attr_path, edit=True, keyable=True)


def _source_connections(adapter, destination: str) -> list[str]:
    if not hasattr(adapter, "list_connections"):
        return []
    try:
        return adapter.list_connections(
            destination,
            source=True,
            destination=False,
            plugs=True,
        ) or []
    except Exception:
        return []


def _node_type(adapter, node: str) -> str | None:
    if not hasattr(adapter, "node_type"):
        return None
    try:
        return adapter.node_type(node)
    except Exception:
        return None
