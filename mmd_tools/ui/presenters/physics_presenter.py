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
    """Read angle attrs (stored in radians internally for kAngle) and display as degrees."""
    x = _get_attr(node, f"{attr}X", 0.0)
    y = _get_attr(node, f"{attr}Y", 0.0)
    z = _get_attr(node, f"{attr}Z", 0.0)
    return f"{x:.2f}, {y:.2f}, {z:.2f}"


def _resolve_message_name(shape, attr):
    connections = cmds.listConnections(f"{shape}.{attr}", source=True, destination=False) or []
    return connections[0] if connections else ""


class PhysicsPresenter:
    """Load rigid bodies / joints from the Physics DAG and drive the tab view."""

    def __init__(self, view, app_state, maya_adapter=None, **_kwargs):
        self.view = view
        self.app_state = app_state
        self.maya_adapter = maya_adapter or MayaCmdsAdapter()
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
            return
        shape = current.data(Qt.UserRole)
        if not shape or not cmds.objExists(shape):
            return
        values = self._read_rigid_body_values(shape)
        self.view.set_physics_form("rigid", values)

    def _on_joint_selected(self, current, _previous):
        if current is None:
            return
        shape = current.data(Qt.UserRole)
        if not shape or not cmds.objExists(shape):
            return
        values = self._read_joint_values(shape)
        self.view.set_physics_form("joint", values)

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

    def _clear_view(self):
        for name in ("rigid_body_list", "joint_list"):
            widget = getattr(self.view, name, None)
            if widget is not None and hasattr(widget, "clear"):
                widget.clear()
        set_enabled = getattr(self.view, "set_physics_details_enabled", None)
        if callable(set_enabled):
            set_enabled(False)
