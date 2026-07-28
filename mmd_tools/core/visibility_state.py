"""Model-root visibility attributes shared by Animation and Physics UI."""

from __future__ import annotations

from enum import Enum

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


class VisibilityState(str, Enum):
    """Scene-authoritative display state for an Animator category.

    The values deliberately remain stable strings so presenters and persisted
    diagnostics can pass state values without depending on enum ordinals.
    """

    VISIBLE = "visible"
    REFERENCE = "reference"
    HIDDEN = "hidden"


# Public aliases make the contract convenient for callers that prefer module
# constants while retaining a single enum as the source of truth.
VISIBLE = VisibilityState.VISIBLE
REFERENCE = VisibilityState.REFERENCE
HIDDEN = VisibilityState.HIDDEN

_CATEGORY_ALIASES = {
    "geometry": "mesh",
    "skeleton": "joints",
    "physics": "colliders",
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


def resolve_visibility_group(adapter, model_root: str, category: str) -> str | None:
    """Resolve a category's direct display group below ``model_root``.

    Only the established Geometry/Skeleton/Physics boundaries are resolved.
    Matching is namespace-safe and intentionally fails closed when more than
    one direct child has the same short group name; silently choosing one
    would make the scene state ambiguous.
    """

    if not model_root:
        return None
    category_key = _canonical_category(category)
    if category_key is None:
        return None
    return _direct_child_group(adapter, model_root, _CATEGORY_GROUPS[category_key])


# Backward/forward-friendly spelling for callers that treat this as a getter.
get_visibility_group = resolve_visibility_group


def get_visibility_state(
    adapter, model_root: str, category: str
) -> VisibilityState:
    """Read the evaluated three-state value from a category group.

    Hidden wins over all drawing overrides.  A visible group is Reference only
    when both ``overrideEnabled`` and ``overrideDisplayType == 2`` are active;
    every other readable combination is normal Visible.  Missing or ambiguous
    groups use Visible as the non-destructive fallback used by the legacy bool
    API.
    """

    group = resolve_visibility_group(adapter, model_root, category)
    if not group:
        return VisibilityState.VISIBLE

    try:
        visibility = adapter.get_attr(f"{group}.visibility")
        if visibility is None:
            return VisibilityState.VISIBLE
        if not bool(visibility):
            return VisibilityState.HIDDEN
    except Exception:
        # A group that cannot be read must not cause a category to disappear.
        return VisibilityState.VISIBLE

    try:
        override_enabled = bool(adapter.get_attr(f"{group}.overrideEnabled"))
        display_type = int(adapter.get_attr(f"{group}.overrideDisplayType"))
    except Exception:
        return VisibilityState.VISIBLE
    if override_enabled and display_type == 2:
        return VisibilityState.REFERENCE
    return VisibilityState.VISIBLE


def set_visibility_state(
    adapter, model_root: str, category: str, state: VisibilityState | str
) -> bool:
    """Write one category's three-state display contract.

    The model-root bool remains the visibility authority.  Normal and
    Reference additionally own the category group's drawing override; Hidden
    changes only the authority bool and intentionally leaves existing override
    fields untouched.  No connection is forced over a foreign driver.

    Returns ``True`` only when the requested state is observable from the
    actual group plugs. Invalid state, missing/ambiguous groups, and blocked or
    foreign-owned plugs are rejected before mutation.
    """

    category_key = _canonical_category(category)
    normalized = _coerce_visibility_state(state)
    group = resolve_visibility_group(adapter, model_root, category_key or "")
    if not category_key or normalized is None or not group:
        return False

    attr = VISIBILITY_CATEGORY_ATTRS[category_key]
    root_plug = f"{model_root}.{attr}"
    group_visibility = f"{group}.visibility"
    root_source = root_plug
    group_sources = _source_connections(adapter, group_visibility)
    if any(not _plug_matches(source, root_source) for source in group_sources):
        return False

    # Check every plug that this state would mutate before touching the root.
    # This keeps a locked or externally driven override from leaving a partial
    # state behind when the caller requested an atomic UI transition.
    root_exists = _attribute_exists(adapter, model_root, attr)
    if root_exists and not _plug_writable(adapter, root_plug):
        return False
    if not _attribute_exists(adapter, group, "visibility"):
        return False
    if normalized is not VisibilityState.HIDDEN:
        for override_attr in ("overrideEnabled", "overrideDisplayType"):
            override_plug = f"{group}.{override_attr}"
            if not _attribute_exists(adapter, group, override_attr):
                return False
            if not _plug_writable(adapter, override_plug):
                return False
    elif not group_sources:
        if not _plug_writable(adapter, group_visibility):
            return False

    try:
        ensure_visibility_attrs(adapter, model_root)
        adapter.set_attr(root_plug, normalized != VisibilityState.HIDDEN)
    except Exception:
        return False

    if not group_sources:
        # Existing imports normally have the root authority connected already.
        # For hand-authored/legacy scenes, establish that link without ever
        # forcing over a foreign source.  When a thin test/headless adapter
        # cannot connect plugs, mirror the evaluated value as the established
        # adapter convention does.
        connected = False
        if hasattr(adapter, "connect_attr"):
            try:
                adapter.connect_attr(root_source, group_visibility, force=False)
                connected = True
            except Exception:
                connected = False
        if not connected:
            try:
                adapter.set_attr(
                    group_visibility,
                    normalized is not VisibilityState.HIDDEN,
                )
            except Exception:
                return False

    if normalized is not VisibilityState.HIDDEN:
        expected_type = 2 if normalized is VisibilityState.REFERENCE else 0
        try:
            adapter.set_attr(f"{group}.overrideEnabled", True)
            adapter.set_attr(f"{group}.overrideDisplayType", expected_type)
        except Exception:
            return False

    # A successful write must be observable through the same plugs used for
    # presenter readback.  This also rejects a stale/foreign visibility link.
    return get_visibility_state(adapter, model_root, category_key) is normalized


def sync_visibility_connections(adapter, model_root: str, category: str | None = None) -> None:
    """Connect model-root visibility attrs to existing display nodes."""
    if not model_root:
        return
    ensure_visibility_attrs(adapter, model_root)
    categories = [_canonical_category(category)] if category else list(VISIBILITY_CATEGORY_ATTRS)
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
    category = _canonical_category(category)
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


def _direct_child_group(adapter, model_root: str, group_name: str | None) -> str | None:
    """Return one named direct child transform below the model root.

    A duplicate short name is treated as ambiguous even when the duplicates
    differ only by namespace.  This keeps a malformed or merged scene from
    receiving a write intended for another model.
    """
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
    matches = []
    for child in children:
        short_name = child.rsplit("|", 1)[-1].rsplit(":", 1)[-1]
        if short_name == group_name:
            matches.append(child)
    return matches[0] if len(matches) == 1 else None


def _canonical_category(category: str | None) -> str | None:
    if not isinstance(category, str):
        return None
    key = category.strip().lower()
    key = _CATEGORY_ALIASES.get(key, key)
    return key if key in _CATEGORY_GROUPS else None


def _coerce_visibility_state(state) -> VisibilityState | None:
    if isinstance(state, VisibilityState):
        return state
    if isinstance(state, str):
        normalized = state.strip().lower()
        try:
            return VisibilityState(normalized)
        except ValueError:
            return None
    return None


def _attribute_exists(adapter, node: str, attr: str) -> bool:
    """Read an attribute's existence without making adapters mandatory."""

    if not hasattr(adapter, "attribute_exists"):
        return True
    try:
        return bool(adapter.attribute_exists(attr, node))
    except Exception:
        # Existing adapters treat unavailable introspection as best-effort;
        # let the actual write/readback decide rather than rejecting a scene.
        return True


def _plug_writable(adapter, plug: str) -> bool:
    """Check lock/input state when the adapter exposes Maya's settable query."""

    if _source_connections(adapter, plug):
        return False
    if not hasattr(adapter, "is_attr_settable"):
        return True
    try:
        return bool(adapter.is_attr_settable(plug))
    except Exception:
        return False


def _plug_matches(actual: str, expected: str) -> bool:
    """Compare Maya plug paths while tolerating a leading DAG ``|``."""

    if actual == expected:
        return True
    try:
        actual_node, actual_attr = actual.rsplit(".", 1)
        expected_node, expected_attr = expected.rsplit(".", 1)
    except (AttributeError, ValueError):
        return False
    return (
        actual_node.lstrip("|") == expected_node.lstrip("|")
        and actual_attr == expected_attr
    )


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
