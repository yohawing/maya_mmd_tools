"""Presenter for the Physics tab — loads rigid body / joint data from scene."""

from __future__ import annotations


from maya import cmds

from ...adapters.maya_cmds_adapter import MayaCmdsAdapter
from ...core.constants import CONSTRAINTS_GROUP, PHYSICS_GROUP, RIGID_BODIES_GROUP
from ...core.logger import get_logger
from ..qt_compat import Qt
from .list_presenter_helpers import (
    apply_list_filter,
    reload_for_current_model_change,
    select_existing_user_role_nodes,
)

logger = get_logger(__name__)


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
    """Read kAngle attrs via cmds.getAttr (returns Maya's current angle UI unit, degrees by default)."""
    x = _get_attr(node, f"{attr}X", 0.0)
    y = _get_attr(node, f"{attr}Y", 0.0)
    z = _get_attr(node, f"{attr}Z", 0.0)
    return f"{x:.2f}, {y:.2f}, {z:.2f}"


def _resolve_message_name(shape, attr):
    connections = cmds.listConnections(f"{shape}.{attr}", source=True, destination=False) or []
    return connections[0] if connections else ""


def _parse_vector_str(text):
    parts = [p.strip() for p in text.split(",")]
    if len(parts) != 3:
        return None
    try:
        return tuple(float(p) for p in parts)
    except ValueError:
        return None


class PhysicsPresenter:
    """Load rigid bodies / joints from the Physics DAG and drive the tab view."""

    def __init__(self, view, app_state, maya_adapter=None, **_kwargs):
        self.view = view
        self.app_state = app_state
        self.maya_adapter = maya_adapter or MayaCmdsAdapter()
        self._current_kind = None
        self._current_shape = None
        self._connect_signals()

        if self.app_state.current_model_root:
            self.refresh_physics(force=True)

    def _connect_signals(self):
        current_model_changed = getattr(self.app_state, "current_model_changed", None)
        if current_model_changed is not None and hasattr(current_model_changed, "connect"):
            current_model_changed.connect(self.on_current_model_changed)

        refresh_btn = getattr(self.view, "refresh_btn", None)
        if refresh_btn is not None:
            refresh_btn.clicked.connect(lambda *_: self.refresh_physics(force=True))

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

        apply_btn = getattr(self.view, "apply_btn", None)
        if apply_btn is not None:
            apply_btn.clicked.connect(self.apply_changes)

        reset_btn = getattr(self.view, "reset_btn", None)
        if reset_btn is not None:
            reset_btn.clicked.connect(self.reset_changes)

    def on_current_model_changed(self, model_root):
        reload_for_current_model_change(logger, "PhysicsPresenter", model_root, lambda: self.refresh_physics(force=True))

    def refresh_physics(self, force=False):
        self._clear_view()
        root = self.app_state.current_model_root
        if not root or not self.maya_adapter.object_exists(root):
            return

        physics_group = self._find_child(root, PHYSICS_GROUP)
        if not physics_group:
            return

        rb_group = self._find_child(physics_group, RIGID_BODIES_GROUP)
        jt_group = self._find_child(physics_group, CONSTRAINTS_GROUP)

        self._populate_rigid_body_list(rb_group)
        self._populate_joint_list(jt_group)
        self.view.set_physics_details_enabled(True)

    def load_physics(self):
        self.refresh_physics(force=True)

    def invalidate_physics_cache(self, *_args):
        return None

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
            if child.rsplit("|", 1)[-1] == name:
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

    def _populate_rigid_body_list(self, rb_group):
        from ..qt_compat import QListWidgetItem
        rb_list = self.view.rigid_body_list
        rb_list.clear()
        for transform, shape in self._find_shapes(rb_group, "mmdRigidBodyShape"):
            index = int(_get_attr(shape, "pmxIndex", -1))
            name_jp = _get_attr(shape, "nameJp", "") or ""
            name_en = _get_attr(shape, "nameEn", "") or ""
            display = f"{index}: {name_jp}"
            if name_en:
                display += f" [{name_en}]"
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
            display = f"{index}: {name_jp}"
            if name_en:
                display += f" [{name_en}]"
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
        self.view.set_physics_form("rigid", values)
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
        self.view.set_physics_form("joint", values)
        self._set_apply_reset_enabled(True)

    def _set_apply_reset_enabled(self, enabled):
        apply_btn = getattr(self.view, "apply_btn", None)
        if apply_btn is not None:
            apply_btn.setEnabled(enabled)
        reset_btn = getattr(self.view, "reset_btn", None)
        if reset_btn is not None:
            reset_btn.setEnabled(enabled)

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
        bone_name = _resolve_message_name(shape, "relatedBone")
        bone_index = int(_get_attr(shape, "relatedBoneIndex", -1))
        related_str = bone_name if bone_name else str(bone_index)
        mask = int(_get_attr(shape, "collisionMask", 0))
        return {
            "name": _get_attr(shape, "nameJp", "") or "",
            "name_english": _get_attr(shape, "nameEn", "") or "",
            "shape": int(_get_attr(shape, "shapeType", 0)),
            "physics_mode": int(_get_attr(shape, "physicsMode", 0)),
            "related_bone": related_str,
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
        rb_a = _resolve_message_name(shape, "rigidBodyA")
        rb_b = _resolve_message_name(shape, "rigidBodyB")
        rb_a_idx = int(_get_attr(shape, "rigidBodyAIndex", -1))
        rb_b_idx = int(_get_attr(shape, "rigidBodyBIndex", -1))
        return {
            "name": _get_attr(shape, "nameJp", "") or "",
            "name_english": _get_attr(shape, "nameEn", "") or "",
            "joint_type": str(_get_attr(shape, "jointType", 0)),
            "rigid_body_a": rb_a if rb_a else str(rb_a_idx),
            "rigid_body_b": rb_b if rb_b else str(rb_b_idx),
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
        shape = self._current_shape
        if not shape or not cmds.objExists(shape):
            return
        try:
            cmds.undoInfo(openChunk=True, chunkName="MMD Physics Edit")
            if self._current_kind == "rigid":
                self._apply_rigid_body_changes(shape)
            elif self._current_kind == "joint":
                self._apply_joint_changes(shape)
            logger.info("Applied physics changes to '%s'", shape)
        except Exception:
            logger.error("Failed to apply physics changes to '%s'", shape, exc_info=True)
        finally:
            cmds.undoInfo(closeChunk=True)

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

    def _apply_rigid_body_changes(self, shape):
        v = self.view
        cmds.setAttr(f"{shape}.nameJp", v.rigid_name_edit.text(), type="string")
        cmds.setAttr(f"{shape}.nameEn", v.rigid_name_english_edit.text(), type="string")
        cmds.setAttr(f"{shape}.shapeType", v.rigid_shape_combo.currentIndex())
        cmds.setAttr(f"{shape}.physicsMode", v.rigid_physics_mode_combo.currentIndex())
        cmds.setAttr(f"{shape}.collisionGroup", v.rigid_collision_group_spin.value())
        mask_text = v.rigid_collision_mask_spin.text().strip()
        try:
            cmds.setAttr(f"{shape}.collisionMask", int(mask_text, 0))
        except ValueError:
            pass
        for attr, editor in (
            ("mass", v.rigid_mass_edit),
            ("linearDamping", v.rigid_linear_damping_edit),
            ("angularDamping", v.rigid_angular_damping_edit),
            ("restitution", v.rigid_restitution_edit),
            ("friction", v.rigid_friction_edit),
        ):
            try:
                cmds.setAttr(f"{shape}.{attr}", float(editor.text()))
            except ValueError:
                pass

    def _apply_joint_changes(self, shape):
        v = self.view
        cmds.setAttr(f"{shape}.nameJp", v.joint_name_edit.text(), type="string")
        cmds.setAttr(f"{shape}.nameEn", v.joint_name_english_edit.text(), type="string")
        try:
            cmds.setAttr(f"{shape}.jointType", int(v.joint_type_spin.text()))
        except ValueError:
            pass
        for attr, editor_name in (
            ("translationLimitMin", "joint_translation_min_edit"),
            ("translationLimitMax", "joint_translation_max_edit"),
            ("springTranslation", "joint_spring_translation_edit"),
            ("springRotation", "joint_spring_rotation_edit"),
        ):
            vec = _parse_vector_str(getattr(v, editor_name).text())
            if vec is not None:
                cmds.setAttr(f"{shape}.{attr}X", vec[0])
                cmds.setAttr(f"{shape}.{attr}Y", vec[1])
                cmds.setAttr(f"{shape}.{attr}Z", vec[2])
        for attr, editor_name in (
            ("rotationLimitMin", "joint_rotation_min_edit"),
            ("rotationLimitMax", "joint_rotation_max_edit"),
        ):
            vec = _parse_vector_str(getattr(v, editor_name).text())
            if vec is not None:
                cmds.setAttr(f"{shape}.{attr}X", vec[0])
                cmds.setAttr(f"{shape}.{attr}Y", vec[1])
                cmds.setAttr(f"{shape}.{attr}Z", vec[2])

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
