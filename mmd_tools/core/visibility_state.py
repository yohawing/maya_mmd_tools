"""Model-root visibility attributes shared by Animation and Physics UI."""

from __future__ import annotations

from .constants import (
    ATTR_MMD_SHOW_JOINTS,
    ATTR_MMD_SHOW_MESH,
    ATTR_MMD_SHOW_PHYSICS_COLLIDERS,
    GEOMETRY_GROUP,
    PHYSICS_GROUP,
    SKELETON_GROUP,
)

VISIBILITY_CATEGORY_ATTRS = {
    "mesh": ATTR_MMD_SHOW_MESH,
    "joints": ATTR_MMD_SHOW_JOINTS,
    "colliders": ATTR_MMD_SHOW_PHYSICS_COLLIDERS,
}

DEFAULT_VISIBILITY_ATTR_VALUES = {
    ATTR_MMD_SHOW_MESH: True,
    ATTR_MMD_SHOW_JOINTS: True,
    ATTR_MMD_SHOW_PHYSICS_COLLIDERS: False,
}

_LEGACY_VISIBILITY_ATTRS = ("mmd_show_ik", "mmd_show_controllers")

_CATEGORY_GROUPS = {
    "mesh": GEOMETRY_GROUP,
    "joints": SKELETON_GROUP,
    "colliders": PHYSICS_GROUP,
}


def ensure_visibility_attrs(adapter, model_root: str) -> None:
    """Ensure model-root viewport visibility attrs exist with release defaults."""
    if not model_root:
        return
    _remove_legacy_visibility_attrs(adapter, model_root)
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
    group = _direct_child_group(adapter, model_root, _CATEGORY_GROUPS.get(category))
    if group:
        yield group, "visibility"


def _iter_collider_targets(adapter, model_root: str):
    # Primary display target: the model root's direct Physics group.
    physics_group = _direct_child_group(adapter, model_root, PHYSICS_GROUP)
    if physics_group:
        yield physics_group, "visibility"

    # Legacy/fallback targets: per-rigid-body locators and curve groups.
    # These remain for scenes created before the common Physics group route.
    if physics_group:
        return
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


def _direct_child_group(adapter, model_root: str, group_name: str | None) -> str | None:
    """Return one named direct child transform below the model root."""
    if not group_name:
        return None
    try:
        children = adapter.list_relatives(
            model_root,
            children=True,
            type="transform",
            fullPath=True,
        ) or []
    except Exception:
        return None
    for child in children:
        short_name = child.rsplit("|", 1)[-1].rsplit(":", 1)[-1]
        if short_name == group_name:
            return child
    return None


def _remove_legacy_visibility_attrs(adapter, model_root: str) -> None:
    """Remove discontinued IK/controller visibility attrs from model roots."""
    for attr in _LEGACY_VISIBILITY_ATTRS:
        try:
            if adapter.attribute_exists(attr, model_root):
                adapter.delete_attr(f"{model_root}.{attr}")
        except Exception:
            continue


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

