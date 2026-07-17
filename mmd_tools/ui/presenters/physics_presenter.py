"""Presenter for the Physics tab — loads rigid body / joint data from scene."""

from __future__ import annotations


from maya import cmds

from ...adapters.maya_cmds_adapter import MayaCmdsAdapter
from ...core.constants import CONSTRAINTS_GROUP, PHYSICS_GROUP, RIGID_BODIES_GROUP
from ...core.logger import get_logger
from ...core.visibility_state import get_visibility_category, set_visibility_category, sync_visibility_connections
from ..qt_compat import Qt
from ..translations import UITranslator
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

        collider_check = getattr(self.view, "collider_visible_check", None)
        if collider_check is not None:
            collider_check.stateChanged.connect(lambda state: self._on_collider_visibility_changed(state != 0))

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

    def on_current_model_changed(self, model_root):
        reload_for_current_model_change(logger, "PhysicsPresenter", model_root, lambda: self.refresh_physics(force=True))

    def refresh_physics(self, force=False):
        self._clear_view()
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

        self._populate_rigid_body_list(rb_group)
        self._populate_joint_list(jt_group)
        self.view.set_physics_details_enabled(True)
        self._sync_collider_visibility_checkbox(root)

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
        from ...core.physics_form_validation import (
            PhysicsFormValidationError,
            parse_joint_form,
            parse_rigid_body_form,
        )

        shape = self._current_shape
        if not shape or not cmds.objExists(shape):
            return

        try:
            if self._current_kind == "rigid":
                values = self._collect_rigid_body_form_values(shape)
                parsed = parse_rigid_body_form(values)
            elif self._current_kind == "joint":
                values = self._collect_joint_form_values(shape)
                parsed = parse_joint_form(values)
            else:
                return
        except PhysicsFormValidationError as e:
            self._report_validation_error(e)
            return

        try:
            cmds.undoInfo(openChunk=True, chunkName="MMD Physics Edit")
            if self._current_kind == "rigid":
                self._apply_validated_rigid_body(shape, parsed)
            elif self._current_kind == "joint":
                self._apply_validated_joint(shape, parsed)
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
            "related_bone": int(_get_attr(shape, "relatedBoneIndex", -1)),
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
            "joint_type": v.joint_type_spin.text(),
            "rigid_body_a": int(_get_attr(shape, "rigidBodyAIndex", -1)),
            "rigid_body_b": int(_get_attr(shape, "rigidBodyBIndex", -1)),
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

    def _apply_validated_rigid_body(self, shape, parsed):
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

    def _apply_validated_joint(self, shape, parsed):
        cmds.setAttr(f"{shape}.nameJp", parsed.name, type="string")
        cmds.setAttr(f"{shape}.nameEn", parsed.name_english, type="string")
        cmds.setAttr(f"{shape}.jointType", parsed.joint_type)
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
            cmds.setAttr(f"{shape}.{attr}X", values[0])
            cmds.setAttr(f"{shape}.{attr}Y", values[1])
            cmds.setAttr(f"{shape}.{attr}Z", values[2])

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
        for vec_attr in ("shapeSize", "position", "rotation"):
            for axis in ("X", "Y", "Z"):
                val = _get_attr(source_shape, f"{vec_attr}{axis}", 0.0)
                cmds.setAttr(f"{shape}.{vec_attr}{axis}", val)
        bone_conn = cmds.listConnections(f"{source_shape}.relatedBone", source=True, destination=False) or []
        if bone_conn:
            cmds.connectAttr(f"{bone_conn[0]}.message", f"{shape}.relatedBone", force=True)
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
