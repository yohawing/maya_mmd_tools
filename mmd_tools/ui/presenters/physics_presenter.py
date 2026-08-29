"""Presenter for the Physics tab — loads rigid body / joint data from scene."""

from __future__ import annotations

import math

from maya import cmds
import maya.api.OpenMaya as om
import maya.api.OpenMayaRender as omr

from mmd_tools.core.collider_authoring import (
    connect_collider_authoring_follow,
    connect_collider_authoring_transform,
    migrate_legacy_collider_authoring_pose,
    set_collider_authoring_pose,
)

from ...adapters.maya_cmds_adapter import MayaCmdsAdapter
from ...core.constants import (
    ATTR_MMD_IMPORT_SCALE,
    CONSTRAINTS_GROUP,
    PHYSICS_GROUP,
    RIGID_BODIES_GROUP,
)
from ...core.coordinate_transform import mmd_point_to_maya
from ...core.logger import get_logger
from ...core.model_registry import (
    REGISTRY_CATEGORY_PHYSICS,
    list_model_registry_members,
)
from ...core.visibility_state import get_visibility_category, set_visibility_category, sync_visibility_connections
from ..qt_compat import Qt
from ..translations import UITranslator
from .list_presenter_helpers import (
    apply_list_filter,
    format_indexed_name_label,
    reload_for_current_model_change,
    select_existing_user_role_nodes,
)

logger = get_logger(__name__)


def _mark_geometry_draw_dirty(shape):
    """Invalidate VP2 geometry after collider authoring attributes change."""
    try:
        selection = om.MSelectionList()
        selection.add(shape)
        omr.MRenderer.setGeometryDrawDirty(selection.getDependNode(0), False)
    except Exception:
        logger.debug("Could not dirty collider viewport geometry: %s", shape, exc_info=True)


def _get_attr(node, attr, default=None):
    try:
        return cmds.getAttr(f"{node}.{attr}")
    except Exception:
        return default


def _get_vector_str(node, attr):
    x = _get_attr(node, f"{attr}X", 0.0)
    y = _get_attr(node, f"{attr}Y", 0.0)
    z = _get_attr(node, f"{attr}Z", 0.0)
    return f"{x:.4f}, {y:.4f}, {z:.4f}"


def _get_angle_vector_deg_str(node, attr):
    """Read kAngle attrs and format them in degrees regardless of Maya UI units."""
    angle_unit = cmds.currentUnit(query=True, angle=True)
    x = _get_attr(node, f"{attr}X", 0.0)
    y = _get_attr(node, f"{attr}Y", 0.0)
    z = _get_attr(node, f"{attr}Z", 0.0)
    if angle_unit == "rad":
        x, y, z = (math.degrees(value) for value in (x, y, z))
    return f"{x:.2f}, {y:.2f}, {z:.2f}"


def _set_angle_vector_degrees(node, attr, values):
    """Write degree UI values through undoable cmds.setAttr calls."""
    if cmds.currentUnit(query=True, angle=True) == "rad":
        values = tuple(math.radians(value) for value in values)
    for axis, value in zip("XYZ", values):
        cmds.setAttr(f"{node}.{attr}{axis}", value)


def _resolve_message_name(shape, attr):
    """Resolve an optional message input without rejecting legacy scene nodes."""
    plug = f"{shape}.{attr}"
    try:
        if not cmds.objExists(plug):
            return ""
        connections = cmds.listConnections(
            plug,
            source=True,
            destination=False,
        ) or []
    except Exception:
        # Startup list population is capability discovery.  Missing attributes
        # on old or partially generated physics nodes mean "unbound".
        return ""
    return connections[0] if connections else ""


def _parse_vector_str(text):
    parts = [p.strip() for p in text.split(",")]
    if len(parts) != 3:
        return None
    try:
        return tuple(float(p) for p in parts)
    except ValueError:
        return None


def _format_validation_error(error):
    translator = UITranslator.instance()
    field = translator.translate(error.field_key, "fields").rstrip(" :：")
    reason = translator.translate(error.message_key, "messages").format(**error.params)
    return translator.translate("physics_validation_error", "messages").format(field=field, reason=reason)


def _next_pmx_index(shape_pairs):
    """Return a unique, monotonically increasing PMX source-order index."""
    return max(
        (int(_get_attr(shape, "pmxIndex", -1)) for _transform, shape in shape_pairs),
        default=-1,
    ) + 1


def _long_name(node):
    matches = cmds.ls(node, long=True) or []
    return matches[0] if matches else ""


def _candidate_display(index, name_jp, name_en, node):
    name = name_jp or node.rsplit("|", 1)[-1]
    return f"{index}: {name}" + (f" [{name_en}]" if name_en else "")


class PhysicsPresenter:
    """Load rigid bodies / joints from the Physics DAG and drive the tab view."""

    def __init__(self, view, app_state, maya_adapter=None, **_kwargs):
        self.view = view
        self.app_state = app_state
        self.maya_adapter = maya_adapter or MayaCmdsAdapter()
        self._current_kind = None
        self._current_shape = None
        self._bone_candidates = []
        self._rigid_body_candidates = []
        self._pending_refresh_generation = None
        self._last_refresh_generation = None
        self._form_dirty = False
        self._loading_form = False
        self._connect_signals()

        if self.app_state.current_model_root:
            self.refresh_physics(force=True)

    def _connect_signals(self):
        current_model_changed = getattr(self.app_state, "current_model_changed", None)
        if current_model_changed is not None and hasattr(current_model_changed, "connect"):
            current_model_changed.connect(self.on_current_model_changed)
        refresh_signal = getattr(self.app_state, "model_refresh_completed", None)
        if refresh_signal is not None and hasattr(refresh_signal, "connect"):
            refresh_signal.connect(self.on_model_refresh)

        refresh_btn = getattr(self.view, "refresh_btn", None)
        if refresh_btn is not None:
            refresh_btn.clicked.connect(self._on_refresh_requested)

        rb_list = getattr(self.view, "rigid_body_list", None)
        if rb_list is not None:
            rb_list.currentItemChanged.connect(self._on_rigid_body_selected)
            rb_list.itemSelectionChanged.connect(self._on_rb_selection_changed_maya)

        jt_list = getattr(self.view, "joint_list", None)
        if jt_list is not None:
            jt_list.currentItemChanged.connect(self._on_joint_selected)
            jt_list.itemSelectionChanged.connect(self._on_jt_selection_changed_maya)

        rb_search = getattr(self.view, "rigid_body_search_edit", None)
        if rb_search is not None:
            rb_search.textChanged.connect(self.filter_rigid_bodies)

        jt_search = getattr(self.view, "joint_search_edit", None)
        if jt_search is not None:
            jt_search.textChanged.connect(self.filter_joints)

        collider_check = getattr(self.view, "collider_visible_check", None)
        if collider_check is not None:
            collider_check.stateChanged.connect(lambda state: self._on_collider_visibility_changed(state != 0))

        physics_enable_check = getattr(self.view, "physics_enable_check", None)
        if physics_enable_check is not None:
            physics_enable_check.stateChanged.connect(
                lambda state: self._on_physics_enable_changed(state != 0)
            )

        create_btn = getattr(self.view, "create_btn", None)
        if create_btn is not None:
            create_btn.clicked.connect(self.create_item)

        duplicate_btn = getattr(self.view, "duplicate_btn", None)
        if duplicate_btn is not None:
            duplicate_btn.clicked.connect(self.duplicate_item)

        delete_btn = getattr(self.view, "delete_btn", None)
        if delete_btn is not None:
            delete_btn.clicked.connect(self.delete_item)

        list_tabs = getattr(self.view, "list_tabs", None)
        if list_tabs is not None:
            list_tabs.currentChanged.connect(self._on_list_tab_changed)

        apply_btn = getattr(self.view, "apply_btn", None)
        if apply_btn is not None:
            apply_btn.clicked.connect(self.apply_changes)

        reset_btn = getattr(self.view, "reset_btn", None)
        if reset_btn is not None:
            reset_btn.clicked.connect(self.reset_changes)

        # PhysicsTab blocks these signals while populating a form.  Tracking
        # them here gives Refresh a Maya-free pending-work predicate.
        for editor in getattr(self.view, "_physics_editors", {}).values():
            editor = editor[1] if isinstance(editor, tuple) else editor
            # Connect only the editor's highest-level semantic signal.  A
            # QSpinBox emits both textChanged and valueChanged for one
            # committed edit, which would otherwise dispatch this Action
            # twice.
            for signal_name in ("currentIndexChanged", "valueChanged", "textChanged"):
                signal = getattr(editor, signal_name, None)
                if signal is not None and hasattr(signal, "connect"):
                    signal.connect(self._mark_form_dirty)
                    break

    def _mark_form_dirty(self, *_args):
        if not self._loading_form:
            self._form_dirty = True


    def on_current_model_changed(self, model_root):
        if getattr(self.app_state, "refreshing", False) is True:
            self.on_model_refresh(getattr(self.app_state, "refresh_generation", 0))
            return
        self._pending_refresh_generation = None
        self._form_dirty = False
        reload_for_current_model_change(logger, "PhysicsPresenter", model_root, lambda: self.refresh_physics(force=True))

    def on_model_refresh(self, generation):
        """Mark physics data stale without touching hidden Maya graphs."""
        self._pending_refresh_generation = generation

    def refresh_for_generation(self, generation):
        """Reload a visible tab once per generation when its form is clean."""
        if self._pending_refresh_generation != generation:
            if self._last_refresh_generation == generation:
                return True
            self.refresh_physics(force=True)
            self._last_refresh_generation = generation
            return True
        if self._form_dirty:
            self._last_refresh_generation = generation
            return True
        self.refresh_physics(force=True)
        self._pending_refresh_generation = None
        self._last_refresh_generation = generation
        return True

    def refresh_physics(self, force=False):
        if self._pending_refresh_generation is not None and self._form_dirty:
            return
        self._last_refresh_generation = getattr(self.app_state, "refresh_generation", 0)
        self._pending_refresh_generation = None
        self._form_dirty = False
        self._clear_view()
        self._sync_physics_enable_checkbox()
        root = self.app_state.current_model_root
        if not root or not self.maya_adapter.object_exists(root):
            return

        create_btn = getattr(self.view, "create_btn", None)
        if create_btn is not None:
            create_btn.setEnabled(True)

        physics_group = self._find_child(root, PHYSICS_GROUP)
        if not physics_group:
            return

        rb_group = self._find_child(physics_group, RIGID_BODIES_GROUP)
        jt_group = self._find_child(physics_group, CONSTRAINTS_GROUP)

        self._refresh_binding_candidates(root, rb_group)

        self._migrate_legacy_collider_poses(rb_group)
        self._populate_rigid_body_list(rb_group)
        self._populate_joint_list(jt_group)
        self.view.set_physics_details_enabled(True)
        self._sync_collider_visibility_checkbox(root)

    def _find_physics_world_shape(self):
        root = getattr(self.app_state, "current_model_root", None)
        if not root or not cmds.objExists(root):
            return None
        try:
            solvers = self._model_physics_solvers(root)
            for solver in dict.fromkeys(solvers):
                world_nodes = cmds.listConnections(
                    f"{solver}.inWorldSettings",
                    source=True,
                    destination=False,
                ) or []
                for world_node in world_nodes:
                    if cmds.nodeType(world_node) == "mmdPhysicsWorldShape":
                        shapes = [world_node]
                    else:
                        shapes = cmds.listRelatives(
                            world_node,
                            shapes=True,
                            fullPath=True,
                            type="mmdPhysicsWorldShape",
                        ) or []
                    if shapes:
                        return (cmds.ls(shapes[0], long=True) or [shapes[0]])[0]
            return None
        except Exception:
            return None

    def _model_physics_solvers(self, model_root=None):
        """Return only physics solvers owned by the selected model.

        New scenes keep non-DAG ownership on the model registry.  A missing
        registry means this is an older scene, so retain the explicit
        ``root.message`` fallback.  A malformed registry is fail-closed and
        must not broaden the search to unrelated scene solvers.
        """
        root = model_root or getattr(self.app_state, "current_model_root", None)
        if not root or not cmds.objExists(root):
            return []
        try:
            registry_members = list_model_registry_members(root, REGISTRY_CATEGORY_PHYSICS)
        except Exception:
            logger.debug("Could not validate model physics registry: %s", root, exc_info=True)
            return []
        if registry_members is None:
            return cmds.listConnections(
                f"{root}.message",
                source=False,
                destination=True,
                type="mmdPhysicsSolver",
            ) or []
        return [
            solver
            for solver in dict.fromkeys(registry_members)
            if cmds.objExists(solver) and cmds.nodeType(solver) == "mmdPhysicsSolver"
        ]

    def _world_solvers(self, world):
        if not world:
            return []
        try:
            connected = list(dict.fromkeys(cmds.listConnections(
                f"{world}.message",
                source=False,
                destination=True,
                type="mmdPhysicsSolver",
            ) or []))
            root = getattr(self.app_state, "current_model_root", None)
            if not root or not cmds.objExists(root):
                return []
            selected = set(self._model_physics_solvers(root))
            return [solver for solver in connected if solver in selected]
        except Exception:
            return []

    def _solvers_requiring_world_settings_version_repair(self, world):
        repairs = []
        world_long = (cmds.ls(world, long=True) or [world])[0]
        for solver in self._world_solvers(world):
            try:
                source = cmds.connectionInfo(
                    f"{solver}.inWorldSettingsVersion", isDestination=True
                ) and cmds.connectionInfo(
                    f"{solver}.inWorldSettingsVersion", sourceFromDestination=True
                )
                if not source:
                    repairs.append(solver)
                    continue
                source_node, source_attr = source.rsplit(".", 1)
                source_long = (cmds.ls(source_node, long=True) or [source_node])[0]
                if source_attr != "outSettingsVersion" or source_long != world_long:
                    repairs.append(solver)
            except Exception:
                continue
        return repairs

    @staticmethod
    def _repair_world_settings_version_connections(world, solvers):
        for solver in solvers:
            cmds.connectAttr(
                f"{world}.outSettingsVersion",
                f"{solver}.inWorldSettingsVersion",
                force=True,
            )

    def _on_refresh_requested(self, *_args):
        world = self._find_physics_world_shape()
        repairs = self._solvers_requiring_world_settings_version_repair(world)
        if repairs:
            cmds.undoInfo(openChunk=True, chunkName="Repair MMD Physics Settings")
            try:
                self._repair_world_settings_version_connections(world, repairs)
            finally:
                cmds.undoInfo(closeChunk=True)
        self.refresh_physics(force=True)

    def _sync_physics_enable_checkbox(self):
        checkbox = getattr(self.view, "physics_enable_check", None)
        if checkbox is None:
            return
        world = self._find_physics_world_shape()
        has_physics_data = False
        if world:
            try:
                has_physics_data = bool(self._world_solvers(world))
                enabled = bool(cmds.getAttr(f"{world}.enable")) if has_physics_data else False
            except Exception:
                has_physics_data = False
                enabled = False
        else:
            enabled = False
        checkbox.blockSignals(True)
        checkbox.setChecked(enabled)
        checkbox.setEnabled(bool(world and has_physics_data))
        checkbox.blockSignals(False)

    def _on_physics_enable_changed(self, enabled):
        world = self._find_physics_world_shape()
        if not world:
            self._sync_physics_enable_checkbox()
            return
        solvers = self._world_solvers(world)
        has_physics_data = bool(solvers)
        if not has_physics_data:
            self._sync_physics_enable_checkbox()
            return
        cmds.undoInfo(openChunk=True, chunkName="MMD Physics Enable")
        try:
            repairs = self._solvers_requiring_world_settings_version_repair(world)
            self._repair_world_settings_version_connections(world, repairs)
            cmds.setAttr(f"{world}.enable", bool(enabled))
            if enabled:
                for solver in solvers:
                    if not cmds.getAttr(f"{solver}.enable"):
                        continue
                    if not cmds.getAttr(f"{solver}.outSolved"):
                        status = cmds.getAttr(f"{solver}.outStatus")
                        raise RuntimeError(f"solver={solver} status={status}")
        except Exception as exc:
            if enabled:
                try:
                    cmds.setAttr(f"{world}.enable", False)
                except Exception:
                    logger.error(
                        "Failed to disable MMD Physics after enable failure",
                        exc_info=True,
                    )
            logger.warning(
                "event=mmd_physics_toggle_failed enabled=%s detail=%s",
                bool(enabled),
                exc,
            )
            try:
                action = "enable" if enabled else "disable"
                cmds.warning(f"MMD Physics {action} failed")
            except Exception:
                pass
        finally:
            cmds.undoInfo(closeChunk=True)
        self._sync_physics_enable_checkbox()

    def load_physics(self):
        self.refresh_physics(force=True)

    def invalidate_physics_cache(self, *_args):
        root = getattr(self.app_state, "current_model_root", None)
        if not root or not cmds.objExists(root):
            return
        solvers = self._model_physics_solvers(root)
        for solver in dict.fromkeys(solvers):
            if cmds.attributeQuery("inDescriptorVersion", node=solver, exists=True):
                version = int(cmds.getAttr(f"{solver}.inDescriptorVersion"))
                cmds.setAttr(f"{solver}.inDescriptorVersion", version + 1)
                continue

            # Nodes registered before this input was added need one explicit
            # invalidation so Apply also works in an already-open Maya session.
            selection = om.MSelectionList()
            selection.add(solver)
            user_node = om.MFnDependencyNode(selection.getDependNode(0)).userNode()
            user_node._free_handles()
            cmds.dgdirty(solver)

    def filter_rigid_bodies(self, text):
        rb_list = self.view.rigid_body_list
        apply_list_filter(
            [rb_list.item(i) for i in range(rb_list.count())],
            text,
            lambda item: [item.text(), item.data(Qt.UserRole) or ""],
        )

    def filter_joints(self, text):
        jt_list = self.view.joint_list
        apply_list_filter(
            [jt_list.item(i) for i in range(jt_list.count())],
            text,
            lambda item: [item.text(), item.data(Qt.UserRole) or ""],
        )

    # -- internal --

    def _find_child(self, parent, name):
        children = cmds.listRelatives(parent, children=True, fullPath=True, type="transform") or []
        for child in children:
            leaf_name = child.rsplit("|", 1)[-1].rsplit(":", 1)[-1]
            if leaf_name == name:
                return child
        return None

    def _find_shapes(self, group, node_type):
        if not group:
            return []
        result = []
        children = cmds.listRelatives(group, children=True, fullPath=True, type="transform") or []
        for transform in children:
            shapes = cmds.listRelatives(transform, shapes=True, fullPath=True, type=node_type) or []
            if shapes:
                result.append((transform, shapes[0]))
        result.sort(key=lambda p: int(_get_attr(p[1], "pmxIndex", 9999)))
        return result

    def _refresh_binding_candidates(self, root, rigid_body_group, *, publish=True):
        root = _long_name(root)
        bones = []
        for node in cmds.listRelatives(root, allDescendents=True, fullPath=True, type="joint") or []:
            if not cmds.attributeQuery("mmd_bone_index", node=node, exists=True):
                continue
            node = _long_name(node)
            index = int(_get_attr(node, "mmd_bone_index", -1))
            bones.append((
                _candidate_display(
                    index,
                    _get_attr(node, "mmd_bone_name", "") or "",
                    _get_attr(node, "mmd_bone_name_en", "") or "",
                    node,
                ),
                node,
                index,
            ))
        bones.sort(key=lambda candidate: candidate[2])
        rigids = []
        for transform, shape in self._find_shapes(rigid_body_group, "mmdRigidBodyShape"):
            transform = _long_name(transform)
            index = int(_get_attr(shape, "pmxIndex", -1))
            rigids.append((
                _candidate_display(
                    index,
                    _get_attr(shape, "nameJp", "") or "",
                    _get_attr(shape, "nameEn", "") or "",
                    transform,
                ),
                transform,
                index,
            ))
        self._bone_candidates = bones
        self._rigid_body_candidates = rigids
        setter = getattr(self.view, "set_binding_options", None) if publish else None
        if setter:
            setter("rigid_related_bone", bones)
            setter("joint_body_a", rigids)
            setter("joint_body_b", rigids)

    def _migrate_legacy_collider_poses(self, rigid_body_group):
        if not rigid_body_group:
            return
        cmds.undoInfo(openChunk=True, chunkName="Migrate Collider Authoring Pose")
        try:
            for transform, shape in self._find_shapes(
                rigid_body_group, "mmdRigidBodyShape"
            ):
                try:
                    display_scale = float(_get_attr(transform, "scaleX", 1.0) or 1.0)
                    if migrate_legacy_collider_authoring_pose(
                        transform, shape, display_scale
                    ):
                        logger.info("Migrated legacy collider authoring pose '%s'", transform)
                except Exception:
                    logger.warning(
                        "Failed to migrate legacy collider authoring pose '%s'",
                        transform,
                        exc_info=True,
                    )
        finally:
            cmds.undoInfo(closeChunk=True)

    def _populate_rigid_body_list(self, rb_group):
        from ..qt_compat import QListWidgetItem
        rb_list = self.view.rigid_body_list
        rb_list.clear()
        for transform, shape in self._find_shapes(rb_group, "mmdRigidBodyShape"):
            index = int(_get_attr(shape, "pmxIndex", -1))
            name_jp = _get_attr(shape, "nameJp", "") or ""
            name_en = _get_attr(shape, "nameEn", "") or ""
            group = max(0, min(15, int(_get_attr(shape, "collisionGroup", 0)))) + 1
            bone = _resolve_message_name(shape, "relatedBone")
            bone_name = (
                (_get_attr(bone, "mmd_bone_name", "") or "")
                if bone else ""
            )
            if not bone_name and bone:
                bone_name = bone.rsplit("|", 1)[-1].rsplit(":", 1)[-1]
            display = format_indexed_name_label(
                index,
                name_jp or ("" if name_en else transform.rsplit("|", 1)[-1]),
                name_en,
                prefix=f"G{group} ",
            )
            display += f" - [{bone_name or '-'}]"
            item = QListWidgetItem(display)
            item.setData(Qt.UserRole, shape)
            rb_list.addItem(item)

    def _populate_joint_list(self, jt_group):
        from ..qt_compat import QListWidgetItem
        jt_list = self.view.joint_list
        jt_list.clear()
        for transform, shape in self._find_shapes(jt_group, "mmdPhysicsJointShape"):
            index = int(_get_attr(shape, "pmxIndex", -1))
            name_jp = _get_attr(shape, "nameJp", "") or ""
            name_en = _get_attr(shape, "nameEn", "") or ""
            display = format_indexed_name_label(
                index,
                name_jp or ("" if name_en else transform.rsplit("|", 1)[-1]),
                name_en,
            )
            item = QListWidgetItem(display)
            item.setData(Qt.UserRole, shape)
            jt_list.addItem(item)

    def _on_rigid_body_selected(self, current, _previous):
        if current is None:
            self._current_kind = None
            self._current_shape = None
            self._set_apply_reset_enabled(False)
            return
        shape = current.data(Qt.UserRole)
        if not shape or not cmds.objExists(shape):
            return
        self._current_kind = "rigid"
        self._current_shape = shape
        values = self._read_rigid_body_values(shape)
        self._loading_form = True
        try:
            self.view.set_physics_form("rigid", values)
        finally:
            self._loading_form = False
        self._form_dirty = False
        self._set_apply_reset_enabled(True)

    def _on_joint_selected(self, current, _previous):
        if current is None:
            self._current_kind = None
            self._current_shape = None
            self._set_apply_reset_enabled(False)
            return
        shape = current.data(Qt.UserRole)
        if not shape or not cmds.objExists(shape):
            return
        self._current_kind = "joint"
        self._current_shape = shape
        values = self._read_joint_values(shape)
        self._loading_form = True
        try:
            self.view.set_physics_form("joint", values)
        finally:
            self._loading_form = False
        self._form_dirty = False
        self._set_apply_reset_enabled(True)

    def _set_apply_reset_enabled(self, enabled):
        for btn_name in ("apply_btn", "reset_btn", "delete_btn", "duplicate_btn"):
            btn = getattr(self.view, btn_name, None)
            if btn is not None:
                btn.setEnabled(enabled)

    def _on_rb_selection_changed_maya(self):
        select_existing_user_role_nodes(
            self.view.rigid_body_list,
            self.maya_adapter,
            Qt.UserRole,
            cmds.objExists,
            logger=logger,
            label="rigid bodies",
        )

    def _on_jt_selection_changed_maya(self):
        select_existing_user_role_nodes(
            self.view.joint_list,
            self.maya_adapter,
            Qt.UserRole,
            cmds.objExists,
            logger=logger,
            label="joints",
        )

    def _read_rigid_body_values(self, shape):
        bone_name = _long_name(_resolve_message_name(shape, "relatedBone"))
        bone_index = int(_get_attr(shape, "relatedBoneIndex", -1))
        mask = int(_get_attr(shape, "collisionMask", 0))
        return {
            "name": _get_attr(shape, "nameJp", "") or "",
            "name_english": _get_attr(shape, "nameEn", "") or "",
            "shape": int(_get_attr(shape, "shapeType", 0)),
            "physics_mode": int(_get_attr(shape, "physicsMode", 0)),
            "related_bone": (bone_name, bone_index),
            "shape_size": _get_vector_str(shape, "shapeSize"),
            "pmx_position": _get_vector_str(shape, "position"),
            "pmx_rotation_degrees": _get_angle_vector_deg_str(shape, "rotation"),
            "collision_group": int(_get_attr(shape, "collisionGroup", 0)),
            "collision_mask": f"0x{mask:04X}",
            "mass": f"{_get_attr(shape, 'mass', 0.0):.4f}",
            "linear_damping": f"{_get_attr(shape, 'linearDamping', 0.0):.4f}",
            "angular_damping": f"{_get_attr(shape, 'angularDamping', 0.0):.4f}",
            "restitution": f"{_get_attr(shape, 'restitution', 0.0):.4f}",
            "friction": f"{_get_attr(shape, 'friction', 0.0):.4f}",
            "node": shape.rsplit("|", 1)[-1],
        }

    def _read_joint_values(self, shape):
        rb_a = _long_name(_resolve_message_name(shape, "rigidBodyA"))
        rb_b = _long_name(_resolve_message_name(shape, "rigidBodyB"))
        rb_a_idx = int(_get_attr(shape, "rigidBodyAIndex", -1))
        rb_b_idx = int(_get_attr(shape, "rigidBodyBIndex", -1))
        return {
            "name": _get_attr(shape, "nameJp", "") or "",
            "name_english": _get_attr(shape, "nameEn", "") or "",
            "joint_type": int(_get_attr(shape, "jointType", 0)),
            "rigid_body_a": (rb_a, rb_a_idx),
            "rigid_body_b": (rb_b, rb_b_idx),
            "pmx_position": _get_vector_str(shape, "position"),
            "pmx_rotation_degrees": _get_angle_vector_deg_str(shape, "rotation"),
            "linear_constraint_states": "",
            "angular_constraint_states": "",
            "translation_limit_min": _get_vector_str(shape, "translationLimitMin"),
            "translation_limit_max": _get_vector_str(shape, "translationLimitMax"),
            "rotation_limit_min_degrees": _get_angle_vector_deg_str(shape, "rotationLimitMin"),
            "rotation_limit_max_degrees": _get_angle_vector_deg_str(shape, "rotationLimitMax"),
            "spring_translation": _get_vector_str(shape, "springTranslation"),
            "spring_rotation": _get_vector_str(shape, "springRotation"),
            "spring_translation_enabled": "",
            "spring_rotation_enabled": "",
            "node": shape.rsplit("|", 1)[-1],
        }

    def apply_changes(self):
        from ...core.physics_form_validation import (
            PhysicsFormValidationError,
            parse_joint_form,
            parse_rigid_body_form,
        )

        shape = self._current_shape
        if not shape or not cmds.objExists(shape):
            return

        try:
            bindings = self._validated_binding_selections(shape)
            if self._current_kind == "rigid":
                values = self._collect_rigid_body_form_values(shape)
                parsed = parse_rigid_body_form(values)
            elif self._current_kind == "joint":
                values = self._collect_joint_form_values(shape)
                parsed = parse_joint_form(values)
                if parsed.joint_type != 0:
                    raise PhysicsFormValidationError(
                        "joint_type", "physics_validation_joint_type_live_unsupported"
                    )
            else:
                return
        except PhysicsFormValidationError as e:
            self._report_validation_error(e)
            return

        try:
            cmds.undoInfo(openChunk=True, chunkName="MMD Physics Edit")
            if self._current_kind == "rigid":
                self._apply_validated_rigid_body(shape, parsed, bindings)
            elif self._current_kind == "joint":
                self._apply_validated_joint(shape, parsed, bindings)
            self.invalidate_physics_cache()
            self._form_dirty = False
            self.app_state.emit_status(f"Applied physics changes: {shape}")
            logger.info("Applied physics changes to '%s'", shape)
        except Exception:
            logger.error("Failed to apply physics changes to '%s'", shape, exc_info=True)
        finally:
            cmds.undoInfo(closeChunk=True)

    def _report_validation_error(self, error):
        message = _format_validation_error(error)
        self.app_state.emit_status(message)
        cmds.warning(message)
        return message

    def reset_changes(self):
        shape = self._current_shape
        if not shape or not cmds.objExists(shape):
            return
        if self._current_kind == "rigid":
            values = self._read_rigid_body_values(shape)
            self.view.set_physics_form("rigid", values)
        elif self._current_kind == "joint":
            values = self._read_joint_values(shape)
            self.view.set_physics_form("joint", values)
        self._form_dirty = False

    def _collect_rigid_body_form_values(self, shape):
        """Collect form values for rigid body validation."""
        v = self.view
        mask_text = v.rigid_collision_mask_spin.text().strip()
        try:
            mask_int = int(mask_text, 0)
        except ValueError:
            mask_int = mask_text
        return {
            "name": v.rigid_name_edit.text(),
            "name_english": v.rigid_name_english_edit.text(),
            "shape": v.rigid_shape_combo.currentIndex(),
            "physics_mode": v.rigid_physics_mode_combo.currentIndex(),
            "related_bone": self._selected_binding(
                "rigid_related_bone", shape, "relatedBone", "relatedBoneIndex"
            )[1],
            "shape_size": v.rigid_shape_size_edit.text(),
            "pmx_position": v.rigid_position_edit.text(),
            "pmx_rotation_degrees": v.rigid_rotation_edit.text(),
            "collision_group": v.rigid_collision_group_spin.value(),
            "collision_mask": mask_int,
            "mass": v.rigid_mass_edit.text(),
            "linear_damping": v.rigid_linear_damping_edit.text(),
            "angular_damping": v.rigid_angular_damping_edit.text(),
            "restitution": v.rigid_restitution_edit.text(),
            "friction": v.rigid_friction_edit.text(),
        }

    def _collect_joint_form_values(self, shape):
        """Collect form values for joint validation."""
        v = self.view
        return {
            "name": v.joint_name_edit.text(),
            "name_english": v.joint_name_english_edit.text(),
            "joint_type": v.joint_type_combo.currentIndex(),
            "rigid_body_a": self._selected_binding(
                "joint_body_a", shape, "rigidBodyA", "rigidBodyAIndex"
            )[1],
            "rigid_body_b": self._selected_binding(
                "joint_body_b", shape, "rigidBodyB", "rigidBodyBIndex"
            )[1],
            "pmx_position": v.joint_position_edit.text(),
            "pmx_rotation_degrees": v.joint_rotation_edit.text(),
            "linear_constraint_states": "0, 0, 0",
            "angular_constraint_states": "0, 0, 0",
            "translation_limit_min": getattr(v, "joint_translation_min_edit").text(),
            "translation_limit_max": getattr(v, "joint_translation_max_edit").text(),
            "rotation_limit_min_degrees": getattr(v, "joint_rotation_min_edit").text(),
            "rotation_limit_max_degrees": getattr(v, "joint_rotation_max_edit").text(),
            "spring_translation": getattr(v, "joint_spring_translation_edit").text(),
            "spring_rotation": getattr(v, "joint_spring_rotation_edit").text(),
            "spring_translation_enabled": "0, 0, 0",
            "spring_rotation_enabled": "0, 0, 0",
        }

    def _selected_binding(self, editor_key, shape, message_attr, index_attr):
        getter = getattr(self.view, "binding_selection", None)
        if getter:
            return getter(editor_key)
        node = _long_name(_resolve_message_name(shape, message_attr))
        return node, int(_get_attr(shape, index_attr, -1))

    def _validated_binding_selections(self, shape):
        from ...core.physics_form_validation import PhysicsFormValidationError

        specs = (
            ("rigid_related_bone", "relatedBone", "relatedBoneIndex", "related_bone"),
        ) if self._current_kind == "rigid" else (
            ("joint_body_a", "rigidBodyA", "rigidBodyAIndex", "rigid_body_a"),
            ("joint_body_b", "rigidBodyB", "rigidBodyBIndex", "rigid_body_b"),
        )
        selections = {
            message_attr: self._selected_binding(editor_key, shape, message_attr, index_attr)
            for editor_key, message_attr, index_attr, _field_key in specs
        }
        root = getattr(self.app_state, "current_model_root", None)
        root_long = _long_name(root) if root and cmds.objExists(root) else ""
        shape_long = _long_name(shape)
        if not root_long or not shape_long.startswith(root_long + "|"):
            raise PhysicsFormValidationError(specs[0][3], "physics_validation_binding")
        physics_group = self._find_child(root, PHYSICS_GROUP) if root else None
        rb_group = self._find_child(physics_group, RIGID_BODIES_GROUP) if physics_group else None
        self._refresh_binding_candidates(root_long, rb_group, publish=False)
        candidate_specs = (
            (specs[0], getattr(self, "_bone_candidates", [])),
        ) if self._current_kind == "rigid" else (
            (specs[0], getattr(self, "_rigid_body_candidates", [])),
            (specs[1], getattr(self, "_rigid_body_candidates", [])),
        )
        result = {}
        for (_editor_key, message_attr, _index_attr, field_key), candidates in candidate_specs:
            selection = selections[message_attr]
            selection = (_long_name(selection[0]) if selection[0] else "", int(selection[1]))
            valid = selection == ("", -1) or any(
                selection == (candidate[1], candidate[2]) for candidate in candidates
            )
            if not valid:
                raise PhysicsFormValidationError(field_key, "physics_validation_binding")
            result[message_attr] = selection
        return result

    @staticmethod
    def _apply_message_binding(shape, message_attr, index_attr, selection):
        destination = f"{shape}.{message_attr}"
        for source in cmds.listConnections(
            destination, source=True, destination=False, plugs=True
        ) or []:
            cmds.disconnectAttr(source, destination)
        node, index = selection
        if node:
            cmds.connectAttr(f"{node}.message", destination)
        cmds.setAttr(f"{shape}.{index_attr}", index)

    def _apply_validated_rigid_body(self, shape, parsed, bindings=None):
        bindings = bindings or {
            "relatedBone": self._selected_binding(
                "rigid_related_bone", shape, "relatedBone", "relatedBoneIndex"
            )
        }
        cmds.setAttr(f"{shape}.nameJp", parsed.name, type="string")
        cmds.setAttr(f"{shape}.nameEn", parsed.name_english, type="string")
        cmds.setAttr(f"{shape}.shapeType", parsed.shape_type)
        cmds.setAttr(f"{shape}.physicsMode", parsed.physics_mode)
        cmds.setAttr(f"{shape}.collisionGroup", parsed.collision_group)
        cmds.setAttr(f"{shape}.collisionMask", parsed.collision_mask)
        cmds.setAttr(f"{shape}.mass", parsed.mass)
        cmds.setAttr(f"{shape}.linearDamping", parsed.linear_damping)
        cmds.setAttr(f"{shape}.angularDamping", parsed.angular_damping)
        cmds.setAttr(f"{shape}.restitution", parsed.restitution)
        cmds.setAttr(f"{shape}.friction", parsed.friction)
        cmds.setAttr(f"{shape}.shapeSize", *parsed.shape_size, type="double3")
        self._apply_message_binding(
            shape, "relatedBone", "relatedBoneIndex", bindings["relatedBone"]
        )
        transform = (cmds.listRelatives(shape, parent=True, fullPath=True) or [None])[0]
        if not transform:
            raise RuntimeError(f"Rigid body transform not found for {shape}")
        display_scale = float(_get_attr(transform, "scaleX", 1.0))
        set_collider_authoring_pose(
            transform,
            shape,
            parsed.pmx_position,
            tuple(math.radians(value) for value in parsed.pmx_rotation_degrees),
            display_scale,
        )
        _mark_geometry_draw_dirty(shape)
        try:
            cmds.refresh(force=True)
        except RuntimeError:
            logger.debug("Collider viewport refresh is unavailable", exc_info=True)

    def _apply_validated_joint(self, shape, parsed, bindings=None):
        bindings = bindings or {
            attr: self._selected_binding(editor_key, shape, attr, index_attr)
            for attr, editor_key, index_attr in (
                ("rigidBodyA", "joint_body_a", "rigidBodyAIndex"),
                ("rigidBodyB", "joint_body_b", "rigidBodyBIndex"),
            )
        }
        cmds.setAttr(f"{shape}.nameJp", parsed.name, type="string")
        cmds.setAttr(f"{shape}.nameEn", parsed.name_english, type="string")
        cmds.setAttr(f"{shape}.jointType", parsed.joint_type)
        self._apply_message_binding(
            shape, "rigidBodyA", "rigidBodyAIndex", bindings["rigidBodyA"]
        )
        self._apply_message_binding(
            shape, "rigidBodyB", "rigidBodyBIndex", bindings["rigidBodyB"]
        )
        cmds.setAttr(f"{shape}.position", *parsed.pmx_position, type="double3")
        _set_angle_vector_degrees(shape, "rotation", parsed.pmx_rotation_degrees)
        transform = (cmds.listRelatives(shape, parent=True, fullPath=True) or [None])[0]
        if not transform:
            raise RuntimeError(f"Physics joint transform not found for {shape}")
        root = getattr(self.app_state, "current_model_root", None)
        display_scale = float(_get_attr(root, ATTR_MMD_IMPORT_SCALE, 1.0))
        cmds.setAttr(
            f"{transform}.translate",
            *mmd_point_to_maya(parsed.pmx_position, display_scale),
            type="double3",
        )
        for attr, values in (
            ("translationLimitMin", parsed.translation_limit_min),
            ("translationLimitMax", parsed.translation_limit_max),
            ("springTranslation", parsed.spring_translation),
            ("springRotation", parsed.spring_rotation),
        ):
            cmds.setAttr(f"{shape}.{attr}X", values[0])
            cmds.setAttr(f"{shape}.{attr}Y", values[1])
            cmds.setAttr(f"{shape}.{attr}Z", values[2])
        for attr, values in (
            ("rotationLimitMin", parsed.rotation_limit_min_degrees),
            ("rotationLimitMax", parsed.rotation_limit_max_degrees),
        ):
            _set_angle_vector_degrees(shape, attr, values)

    def _on_collider_visibility_changed(self, visible):
        root = self.app_state.current_model_root
        if not root or not self.maya_adapter.object_exists(root):
            return
        try:
            set_visibility_category(self.maya_adapter, root, "colliders", visible)
            sync_visibility_connections(self.maya_adapter, root, "colliders")
        except Exception:
            logger.debug("Collider visibility toggle failed", exc_info=True)

    def _sync_collider_visibility_checkbox(self, root):
        collider_check = getattr(self.view, "collider_visible_check", None)
        if collider_check is None:
            return
        try:
            sync_visibility_connections(self.maya_adapter, root, "colliders")
            visible = get_visibility_category(self.maya_adapter, root, "colliders")
            collider_check.blockSignals(True)
            collider_check.setChecked(visible)
            collider_check.blockSignals(False)
        except Exception:
            logger.debug("Failed to sync collider visibility checkbox", exc_info=True)

    def _on_list_tab_changed(self, index):
        """Enable create/delete when a physics list tab is active."""
        has_model = bool(self.app_state.current_model_root)
        create_btn = getattr(self.view, "create_btn", None)
        if create_btn is not None:
            create_btn.setEnabled(has_model)

    def create_item(self):
        root = self.app_state.current_model_root
        if not root or not cmds.objExists(root):
            return
        list_tabs = getattr(self.view, "list_tabs", None)
        tab_index = list_tabs.currentIndex() if list_tabs else 0
        try:
            cmds.undoInfo(openChunk=True, chunkName="MMD Physics Create")
            if tab_index == 0:
                self._create_rigid_body(root)
            else:
                self._create_joint(root)
        except Exception:
            logger.error("Failed to create physics item", exc_info=True)
        finally:
            cmds.undoInfo(closeChunk=True)
        self.refresh_physics(force=True)

    def duplicate_item(self):
        shape = self._current_shape
        if not shape or not cmds.objExists(shape):
            return
        root = self.app_state.current_model_root
        if not root or not cmds.objExists(root):
            return
        try:
            cmds.undoInfo(openChunk=True, chunkName="MMD Physics Duplicate")
            if self._current_kind == "rigid":
                self._duplicate_rigid_body(root, shape)
            elif self._current_kind == "joint":
                self._duplicate_joint(root, shape)
        except Exception:
            logger.error("Failed to duplicate physics item", exc_info=True)
        finally:
            cmds.undoInfo(closeChunk=True)
        self.refresh_physics(force=True)

    def delete_item(self):
        shape = self._current_shape
        if not shape or not cmds.objExists(shape):
            return
        parent = cmds.listRelatives(shape, parent=True, fullPath=True)
        if not parent:
            return
        try:
            cmds.undoInfo(openChunk=True, chunkName="MMD Physics Delete")
            if self._current_kind == "rigid":
                self._clear_deleted_rigid_body_references(
                    root=self.app_state.current_model_root,
                    rigid_transform=parent[0],
                )
            cmds.delete(parent[0])
        except Exception:
            logger.error("Failed to delete physics item", exc_info=True)
        finally:
            cmds.undoInfo(closeChunk=True)
        self._current_shape = None
        self._current_kind = None
        self.refresh_physics(force=True)

    def _clear_deleted_rigid_body_references(self, root, rigid_transform):
        """Prevent disconnected joints from falling back to a stale PMX index."""
        physics_group = self._find_child(root, PHYSICS_GROUP)
        jt_group = self._find_child(physics_group, CONSTRAINTS_GROUP) if physics_group else None
        for _transform, joint_shape in self._find_shapes(jt_group, "mmdPhysicsJointShape"):
            for message_attr, fallback_attr in (
                ("rigidBodyA", "rigidBodyAIndex"),
                ("rigidBodyB", "rigidBodyBIndex"),
            ):
                source = f"{rigid_transform}.message"
                destination = f"{joint_shape}.{message_attr}"
                if cmds.isConnected(source, destination):
                    cmds.setAttr(f"{joint_shape}.{fallback_attr}", -1)
                    cmds.disconnectAttr(source, destination)

    def _create_rigid_body(self, root):
        physics_group = self._find_child(root, PHYSICS_GROUP)
        if not physics_group:
            physics_group = cmds.group(empty=True, name=PHYSICS_GROUP, parent=root)
        rb_group = self._find_child(physics_group, RIGID_BODIES_GROUP)
        if not rb_group:
            rb_group = cmds.group(empty=True, name=RIGID_BODIES_GROUP, parent=physics_group)

        existing = self._find_shapes(rb_group, "mmdRigidBodyShape")
        new_index = _next_pmx_index(existing)

        transform = cmds.createNode("transform", name=f"rb_{new_index}", parent=rb_group)
        shape = cmds.createNode("mmdRigidBodyShape", name=f"rb_{new_index}Shape", parent=transform)
        cmds.setAttr(f"{shape}.pmxIndex", new_index)
        cmds.setAttr(f"{shape}.nameJp", f"剛体{new_index}", type="string")
        cmds.setAttr(f"{shape}.nameEn", f"rigid_body_{new_index}", type="string")
        cmds.setAttr(f"{shape}.shapeType", 0)
        cmds.setAttr(f"{shape}.shapeSizeX", 0.5)
        cmds.setAttr(f"{shape}.shapeSizeY", 0.5)
        cmds.setAttr(f"{shape}.shapeSizeZ", 0.5)
        cmds.setAttr(f"{shape}.mass", 1.0)
        cmds.setAttr(f"{shape}.collisionGroup", 0)
        cmds.setAttr(f"{shape}.collisionMask", 0xFFFF)
        connect_collider_authoring_transform(transform, shape)
        logger.info("Created rigid body '%s'", transform)

    def _create_joint(self, root):
        physics_group = self._find_child(root, PHYSICS_GROUP)
        if not physics_group:
            physics_group = cmds.group(empty=True, name=PHYSICS_GROUP, parent=root)
        jt_group = self._find_child(physics_group, CONSTRAINTS_GROUP)
        if not jt_group:
            jt_group = cmds.group(empty=True, name=CONSTRAINTS_GROUP, parent=physics_group)

        existing = self._find_shapes(jt_group, "mmdPhysicsJointShape")
        new_index = _next_pmx_index(existing)

        transform = cmds.createNode("transform", name=f"joint_{new_index}", parent=jt_group)
        shape = cmds.createNode("mmdPhysicsJointShape", name=f"joint_{new_index}Shape", parent=transform)
        cmds.setAttr(f"{shape}.pmxIndex", new_index)
        cmds.setAttr(f"{shape}.nameJp", f"ジョイント{new_index}", type="string")
        cmds.setAttr(f"{shape}.nameEn", f"joint_{new_index}", type="string")
        logger.info("Created joint '%s'", transform)

    def _duplicate_rigid_body(self, root, source_shape):
        physics_group = self._find_child(root, PHYSICS_GROUP)
        rb_group = self._find_child(physics_group, RIGID_BODIES_GROUP) if physics_group else None
        if not rb_group:
            return

        existing = self._find_shapes(rb_group, "mmdRigidBodyShape")
        new_index = _next_pmx_index(existing)

        transform = cmds.createNode("transform", name=f"rb_{new_index}", parent=rb_group)
        shape = cmds.createNode("mmdRigidBodyShape", name=f"rb_{new_index}Shape", parent=transform)
        cmds.setAttr(f"{shape}.pmxIndex", new_index)
        for attr in (
            "nameJp", "nameEn", "shapeType", "physicsMode", "mass",
            "linearDamping", "angularDamping", "restitution", "friction",
            "collisionGroup", "collisionMask", "relatedBoneIndex",
        ):
            val = _get_attr(source_shape, attr)
            if val is not None:
                if isinstance(val, str):
                    cmds.setAttr(f"{shape}.{attr}", val, type="string")
                else:
                    cmds.setAttr(f"{shape}.{attr}", val)
        for vec_attr in ("shapeSize",):
            for axis in ("X", "Y", "Z"):
                val = _get_attr(source_shape, f"{vec_attr}{axis}", 0.0)
                cmds.setAttr(f"{shape}.{vec_attr}{axis}", val)
        position = tuple(_get_attr(source_shape, f"position{axis}", 0.0) for axis in "XYZ")
        rotation_degrees = tuple(_get_attr(source_shape, f"rotation{axis}", 0.0) for axis in "XYZ")
        source_transform = (cmds.listRelatives(source_shape, parent=True, fullPath=True) or [None])[0]
        display_scale = _get_attr(source_transform, "scaleX", 1.0) if source_transform else 1.0
        set_collider_authoring_pose(
            transform,
            shape,
            position,
            tuple(math.radians(value) for value in rotation_degrees),
            display_scale,
        )
        bone_conn = cmds.listConnections(f"{source_shape}.relatedBone", source=True, destination=False) or []
        if bone_conn:
            cmds.connectAttr(f"{bone_conn[0]}.message", f"{shape}.relatedBone", force=True)
            connect_collider_authoring_follow(transform, shape)
        logger.info("Duplicated rigid body '%s' from '%s'", transform, source_shape)

    def _duplicate_joint(self, root, source_shape):
        physics_group = self._find_child(root, PHYSICS_GROUP)
        jt_group = self._find_child(physics_group, CONSTRAINTS_GROUP) if physics_group else None
        if not jt_group:
            return

        existing = self._find_shapes(jt_group, "mmdPhysicsJointShape")
        new_index = _next_pmx_index(existing)

        transform = cmds.createNode("transform", name=f"joint_{new_index}", parent=jt_group)
        shape = cmds.createNode("mmdPhysicsJointShape", name=f"joint_{new_index}Shape", parent=transform)
        cmds.setAttr(f"{shape}.pmxIndex", new_index)
        for attr in ("nameJp", "nameEn", "jointType", "rigidBodyAIndex", "rigidBodyBIndex"):
            val = _get_attr(source_shape, attr)
            if val is not None:
                if isinstance(val, str):
                    cmds.setAttr(f"{shape}.{attr}", val, type="string")
                else:
                    cmds.setAttr(f"{shape}.{attr}", val)
        for vec_attr in (
            "position", "rotation",
            "translationLimitMin", "translationLimitMax",
            "rotationLimitMin", "rotationLimitMax",
            "springTranslation", "springRotation",
        ):
            for axis in ("X", "Y", "Z"):
                val = _get_attr(source_shape, f"{vec_attr}{axis}", 0.0)
                cmds.setAttr(f"{shape}.{vec_attr}{axis}", val)
        for rb_attr in ("rigidBodyA", "rigidBodyB"):
            conn = cmds.listConnections(f"{source_shape}.{rb_attr}", source=True, destination=False) or []
            if conn:
                cmds.connectAttr(f"{conn[0]}.message", f"{shape}.{rb_attr}", force=True)
        logger.info("Duplicated joint '%s' from '%s'", transform, source_shape)

    def _clear_view(self):
        self._current_kind = None
        self._current_shape = None
        self._set_apply_reset_enabled(False)
        for name in ("rigid_body_list", "joint_list"):
            widget = getattr(self.view, name, None)
            if widget is not None and hasattr(widget, "clear"):
                widget.clear()
        set_enabled = getattr(self.view, "set_physics_details_enabled", None)
        if callable(set_enabled):
            set_enabled(False)
