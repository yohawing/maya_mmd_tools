from dataclasses import dataclass, replace
from typing import Optional, Tuple

from ...core.physics_scene_query import MayaPhysicsSceneReader, JointSceneRef, PhysicsSceneRefs, RigidBodySceneRef
from ...core.physics_scene_writer import MayaPhysicsSceneWriter, PhysicsSceneWriteError
from ...core.physics_form_validation import (
    PhysicsFormValidationError,
    parse_joint_form,
    parse_rigid_body_form,
)
from ...adapters.maya_cmds_adapter import MayaCmdsAdapter
from ...core.logger import get_logger
from ...core.visibility_state import (
    connect_visibility_attr_to_node,
    get_visibility_category,
    set_visibility_category,
    sync_visibility_connections,
)
from ..qt_compat import QListWidgetItem, Qt
from .list_presenter_helpers import (
    apply_list_filter,
    select_existing_user_role_nodes,
)

logger = get_logger(__name__)

_SHAPE_LABELS = {
    0: "sphere",
    1: "box",
    2: "capsule",
}


@dataclass(frozen=True)
class _PhysicsUiState:
    list_tab_index: int = 0
    rigid_search: str = ""
    joint_search: str = ""
    splitter_sizes: Tuple[int, ...] = ()
    selected_rigid_transform: Optional[str] = None
    selected_joint_transform: Optional[str] = None


class PhysicsPresenter:
    def __init__(self, view, app_state, maya_adapter=None, physics_reader=None, physics_writer=None):
        self.view = view
        self.app_state = app_state
        self.maya_adapter = maya_adapter or MayaCmdsAdapter()
        self.physics_reader = physics_reader or MayaPhysicsSceneReader(self.maya_adapter)
        self.physics_writer = physics_writer or MayaPhysicsSceneWriter(self.maya_adapter)
        self._rigid_bodies_by_transform = {}
        self._joints_by_transform = {}
        self._current_physics_ref = None
        self._validated_form_values = None
        self._validated_form_ref = None
        self._ui_state_by_root = {}
        self._active_model_root = None
        self._default_ui_state = self._capture_ui_state()
        self.connect_signals()

    @property
    def validated_form_values(self):
        """Typed dirty values reserved for the later explicit writer slice."""
        return self._validated_form_values

    def connect_signals(self):
        # ApplicationStateのシグナル
        self.app_state.current_model_changed.connect(self.on_current_model_changed)
        self.view.rigid_body_list.itemSelectionChanged.connect(self.on_rigid_body_selection_changed)
        self.view.joint_list.itemSelectionChanged.connect(self.on_joint_selection_changed)
        refresh_btn = getattr(self.view, "refresh_btn", None)
        if refresh_btn is not None:
            refresh_btn.clicked.connect(self.refresh_physics)
        collider_visible_check = getattr(self.view, "collider_visible_check", None)
        if collider_visible_check is not None:
            collider_visible_check.toggled.connect(self.on_collider_visibility_toggled)

        rigid_body_search_edit = getattr(self.view, "rigid_body_search_edit", None)
        if rigid_body_search_edit is not None and hasattr(rigid_body_search_edit, "textChanged"):
            rigid_body_search_edit.textChanged.connect(self.filter_rigid_bodies)

        joint_search_edit = getattr(self.view, "joint_search_edit", None)
        if joint_search_edit is not None and hasattr(joint_search_edit, "textChanged"):
            joint_search_edit.textChanged.connect(self.filter_joints)

        list_tabs = getattr(self.view, "list_tabs", None)
        if list_tabs is not None and hasattr(list_tabs, "currentChanged"):
            list_tabs.currentChanged.connect(self.on_list_tab_changed)

        form_changed = getattr(self.view, "physics_form_changed", None)
        if form_changed is not None and hasattr(form_changed, "connect"):
            form_changed.connect(self.on_physics_form_changed)
        reset_btn = getattr(self.view, "reset_btn", None)
        if reset_btn is not None and hasattr(reset_btn, "clicked"):
            reset_btn.clicked.connect(self.reset_physics_form)
        apply_btn = getattr(self.view, "apply_btn", None)
        if apply_btn is not None and hasattr(apply_btn, "clicked"):
            apply_btn.clicked.connect(self.apply_physics_form)

    def on_current_model_changed(self, model_root):
        """現在のモデルが変更されたときの処理"""
        old_root = self._active_model_root
        if old_root:
            self._ui_state_by_root[old_root] = self._capture_ui_state()
        logger.debug("PhysicsPresenter: Current model changed to %s", model_root)
        self.load_physics()
        self._restore_ui_state(model_root or None)

    def refresh_physics(self):
        """Reload the active root while preserving its in-memory UI state."""
        root = self.app_state.current_model_root
        if root:
            self._ui_state_by_root[root] = self._capture_ui_state()
        self.load_physics()
        self._restore_ui_state(root)

    def load_physics(self):
        self.view.rigid_body_list.clear()
        self.view.joint_list.clear()
        self._rigid_bodies_by_transform.clear()
        self._joints_by_transform.clear()
        self._reset_details()

        current_model_root = self.app_state.current_model_root
        self._active_model_root = current_model_root or None
        if not current_model_root or not self.maya_adapter.object_exists(current_model_root):
            return

        refs = self._ensure_collider_locators(
            self.physics_reader.collect(current_model_root),
            current_model_root,
        )
        for rigid_body in refs.rigid_bodies:
            self._rigid_bodies_by_transform[rigid_body.transform] = rigid_body
            self.view.rigid_body_list.addItem(_rigid_body_item(rigid_body))

        for joint in refs.joints:
            self._joints_by_transform[joint.transform] = joint
            self.view.joint_list.addItem(_joint_item(joint))
        self.filter_rigid_bodies(self._search_text("rigid_body_search_edit"))
        self.filter_joints(self._search_text("joint_search_edit"))
        self._sync_collider_visibility_control(current_model_root)
        self._apply_collider_visibility(self._colliders_visible(current_model_root))
        # Lists are populated; details stay disabled until an explicit selection.

    def filter_rigid_bodies(self, text):
        """Filter rigid body list items by search query (no Maya queries)."""
        apply_list_filter(
            self._iter_list_items(self.view.rigid_body_list),
            text,
            self._rigid_body_filter_terms,
        )

    def filter_joints(self, text):
        """Filter joint list items by search query (no Maya queries)."""
        apply_list_filter(
            self._iter_list_items(self.view.joint_list),
            text,
            self._joint_filter_terms,
        )

    def on_list_tab_changed(self, index):
        """Clear inactive list selection and reset details when switching subtabs."""
        if index == 0:
            self._clear_list_selection(self.view.joint_list)
        else:
            self._clear_list_selection(self.view.rigid_body_list)
        self._reset_details()

    def on_rigid_body_selection_changed(self):
        """Select the selected rigid body transforms in Maya."""
        self._clear_list_selection(self.view.joint_list)
        selected_nodes = select_existing_user_role_nodes(
            self.view.rigid_body_list,
            self.maya_adapter,
            Qt.UserRole,
            self.maya_adapter.object_exists,
            logger,
            "physics rigid bodies",
        )
        rigid_body = self._selected_ref(self.view.rigid_body_list, self._rigid_bodies_by_transform)
        has_valid_selection = rigid_body is not None and (
            rigid_body.transform in selected_nodes or self.maya_adapter.object_exists(rigid_body.transform)
        )
        if has_valid_selection:
            self._current_physics_ref = rigid_body
            self._set_physics_details_enabled(True)
            self._set_details(
                rigid_body.name,
                "Rigid body",
                f"{_SHAPE_LABELS.get(rigid_body.shape_type, 'unknown')}, mode={rigid_body.physics_mode}",
                f"bone={rigid_body.related_bone_index}",
                rigid_body.transform,
            )
            self._populate_rigid_body_form(rigid_body)
        else:
            self._reset_details()

    def on_joint_selection_changed(self):
        """Select the selected joint transforms in Maya."""
        self._clear_list_selection(self.view.rigid_body_list)
        selected_nodes = select_existing_user_role_nodes(
            self.view.joint_list,
            self.maya_adapter,
            Qt.UserRole,
            self.maya_adapter.object_exists,
            logger,
            "physics joints",
        )
        joint = self._selected_ref(self.view.joint_list, self._joints_by_transform)
        has_valid_selection = joint is not None and (
            joint.transform in selected_nodes or self.maya_adapter.object_exists(joint.transform)
        )
        if has_valid_selection:
            self._current_physics_ref = joint
            self._set_physics_details_enabled(True)
            self._set_details(
                joint.name,
                "Joint",
                f"type={joint.joint_type}",
                f"A={joint.rigid_body_a_index}, B={joint.rigid_body_b_index}",
                joint.transform,
            )
            self._populate_joint_form(joint)
        else:
            self._reset_details()

    def on_physics_form_changed(self, *_args):
        """Validate dirty widgets and cache typed values without scene writes."""
        current = self._current_physics_ref
        if current is None:
            return
        getter = getattr(self.view, "get_physics_form_values", None)
        if not callable(getter):
            return
        kind = "rigid" if isinstance(current, RigidBodySceneRef) else "joint"
        try:
            raw_values = getter(kind)
            if kind == "rigid":
                parsed = parse_rigid_body_form(raw_values)
            else:
                parsed = parse_joint_form(raw_values)
        except PhysicsFormValidationError as exc:
            self._validated_form_values = None
            self._validated_form_ref = None
            self._set_validation_error(exc)
            valid = False
        else:
            self._validated_form_values = parsed
            self._validated_form_ref = current
            self._clear_validation_error()
            valid = True
        setter = getattr(self.view, "set_physics_dirty", None)
        if callable(setter):
            setter(True, valid=valid)

    def reset_physics_form(self):
        """Restore widgets from the selected cached scene reference."""
        current = self._current_physics_ref
        if isinstance(current, RigidBodySceneRef):
            self._populate_rigid_body_form(current)
        elif isinstance(current, JointSceneRef):
            self._populate_joint_form(current)

    def apply_physics_form(self):
        """Atomically write the validated dirty form, then re-read the scene."""
        current = self._current_physics_ref
        values = self._validated_form_values
        if current is None or values is None or self._validated_form_ref is not current:
            self._set_write_error(
                PhysicsSceneWriteError("node", "physics_write_stale_form")
            )
            return
        try:
            if isinstance(current, RigidBodySceneRef):
                self.physics_writer.apply_rigid_body(current, values)
            elif isinstance(current, JointSceneRef):
                self.physics_writer.apply_joint(current, values)
            else:
                raise PhysicsSceneWriteError("node", "physics_write_stale_form")
        except PhysicsSceneWriteError as exc:
            self._set_write_error(exc)
            return

        self._validated_form_values = None
        self._validated_form_ref = None
        self._clear_validation_error()
        self.refresh_physics()

    def on_collider_visibility_toggled(self, visible):
        """Toggle display-only collider locator shapes."""
        model_root = self.app_state.current_model_root
        if model_root:
            try:
                set_visibility_category(self.maya_adapter, model_root, "colliders", bool(visible))
            except Exception as exc:
                logger.debug("Could not update collider root visibility attr: %s", exc)
        self._apply_collider_visibility(bool(visible))

    def _apply_collider_visibility(self, visible: bool):
        model_root = self.app_state.current_model_root
        if model_root:
            try:
                sync_visibility_connections(self.maya_adapter, model_root, "colliders")
                return
            except Exception as exc:
                logger.debug("Could not sync collider drawEnabled connections: %s", exc)
        for rigid_body in self._rigid_bodies_by_transform.values():
            locator = rigid_body.locator_shape
            if not locator or not self.maya_adapter.object_exists(locator):
                continue
            try:
                self.maya_adapter.set_attr(f"{locator}.drawEnabled", bool(visible))
            except Exception as exc:
                logger.warning("Could not set collider locator visibility for %s: %s", locator, exc)

    def _ensure_collider_locators(self, refs: PhysicsSceneRefs, model_root: str) -> PhysicsSceneRefs:
        repaired = []
        changed = False
        for rigid_body in refs.rigid_bodies:
            if rigid_body.locator_shape:
                repaired.append(rigid_body)
                continue
            # Normal Bullet path is structural bulletRigidBodyShape existence.
            # Only repair locators when the Bullet shape is structurally absent.
            bullet_shape = rigid_body.bullet_shape
            if bullet_shape and self.maya_adapter.object_exists(bullet_shape):
                repaired.append(rigid_body)
                continue
            locator = self._create_collider_locator(rigid_body, model_root)
            if locator:
                repaired.append(replace(rigid_body, locator_shape=locator))
                changed = True
            else:
                repaired.append(rigid_body)
        if not changed:
            return refs
        return PhysicsSceneRefs(rigid_bodies=tuple(repaired), joints=refs.joints)

    def _create_collider_locator(self, rigid_body: RigidBodySceneRef, model_root: str) -> Optional[str]:
        if not hasattr(self.maya_adapter, "create_node"):
            return None
        try:
            if hasattr(self.maya_adapter, "all_node_types"):
                node_types = self.maya_adapter.all_node_types() or []
                if "mmdRigidBodyLocator" not in node_types:
                    return None
            created = self.maya_adapter.create_node(
                "mmdRigidBodyLocator",
                name=f"{rigid_body.transform.rsplit('|', 1)[-1]}_colliderLocatorShape",
                parent=rigid_body.transform,
            )
            locator = self._resolve_collider_locator(rigid_body.transform, created)
            if not locator:
                return None
            self._seed_collider_locator_attrs(locator, rigid_body.bullet_shape)
            connect_visibility_attr_to_node(
                self.maya_adapter,
                model_root,
                "colliders",
                locator,
                target_attr="drawEnabled",
            )
            return locator
        except Exception as exc:
            logger.debug("Could not create collider locator for %s: %s", rigid_body.transform, exc)
            return None

    def _resolve_collider_locator(self, transform: str, created: Optional[str]) -> Optional[str]:
        try:
            locators = self.maya_adapter.list_relatives(
                transform,
                shapes=True,
                type="mmdRigidBodyLocator",
                fullPath=True,
            ) or []
        except Exception:
            locators = []
        if not locators:
            return created
        if created:
            created_short = created.rsplit("|", 1)[-1]
            for locator in locators:
                if locator == created or locator.rsplit("|", 1)[-1] == created_short:
                    return locator
        return locators[0]

    def _seed_collider_locator_attrs(self, locator: str, bullet_shape: str) -> None:
        for source_attr, target_attr in (
            ("colliderShapeType", "colliderShapeType"),
            ("radius", "radius"),
            ("length", "length"),
            ("colliderShapeSizeX", "boxSizeX"),
            ("colliderShapeSizeY", "boxSizeY"),
            ("colliderShapeSizeZ", "boxSizeZ"),
        ):
            try:
                value = self.maya_adapter.get_attr(f"{bullet_shape}.{source_attr}")
                self.maya_adapter.set_attr(f"{locator}.{target_attr}", value)
            except Exception:
                continue

    def _colliders_visible(self, model_root: Optional[str] = None) -> bool:
        if model_root:
            try:
                return get_visibility_category(self.maya_adapter, model_root, "colliders")
            except Exception:
                pass
        collider_visible_check = getattr(self.view, "collider_visible_check", None)
        if collider_visible_check is None or not hasattr(collider_visible_check, "isChecked"):
            return True
        return bool(collider_visible_check.isChecked())

    def _sync_collider_visibility_control(self, model_root: str):
        visible = self._colliders_visible(model_root)
        collider_visible_check = getattr(self.view, "collider_visible_check", None)
        if collider_visible_check is None:
            return
        try:
            if hasattr(collider_visible_check, "setChecked"):
                collider_visible_check.setChecked(visible)
            elif hasattr(collider_visible_check, "set_checked"):
                collider_visible_check.set_checked(visible)
        except Exception:
            pass

    def _iter_list_items(self, list_widget):
        """Yield items from a QListWidget via count/item when available."""
        count = getattr(list_widget, "count", None)
        item_at = getattr(list_widget, "item", None)
        if callable(count) and callable(item_at):
            return (item_at(i) for i in range(count()))
        return list(getattr(list_widget, "items", []) or [])

    def _search_text(self, attr_name):
        search_edit = getattr(self.view, attr_name, None)
        text = getattr(search_edit, "text", None)
        return text() if callable(text) else ""

    def _rigid_body_filter_terms(self, item):
        transform = item.data(Qt.UserRole) if hasattr(item, "data") else None
        ref = self._rigid_bodies_by_transform.get(transform) if transform else None
        return (
            item.text() if hasattr(item, "text") else "",
            transform or "",
            ref.name if ref is not None else "",
            ref.name_english if ref is not None else "",
        )

    def _joint_filter_terms(self, item):
        transform = item.data(Qt.UserRole) if hasattr(item, "data") else None
        ref = self._joints_by_transform.get(transform) if transform else None
        return (
            item.text() if hasattr(item, "text") else "",
            transform or "",
            ref.name if ref is not None else "",
            ref.name_english if ref is not None else "",
        )

    def _clear_list_selection(self, list_widget):
        """Clear selection with signal blocking when available (avoids recursion)."""
        if list_widget is None:
            return
        block_signals = getattr(list_widget, "blockSignals", None)
        previous = False
        if callable(block_signals):
            previous = block_signals(True)
        try:
            clear_selection = getattr(list_widget, "clearSelection", None)
            if callable(clear_selection):
                clear_selection()
            elif hasattr(list_widget, "_selected_items"):
                list_widget._selected_items = []
        finally:
            if callable(block_signals):
                block_signals(previous)

    def _selected_ref(self, list_widget, ref_by_transform):
        selected_items = list_widget.selectedItems()
        if not selected_items:
            return None
        transform = selected_items[0].data(Qt.UserRole)
        return ref_by_transform.get(transform)

    def _set_physics_details_enabled(self, enabled):
        setter = getattr(self.view, "set_physics_details_enabled", None)
        if callable(setter):
            setter(enabled)

    def _reset_details(self):
        """Clear detail labels and disable the details panel."""
        self._current_physics_ref = None
        self._validated_form_values = None
        self._validated_form_ref = None
        self._set_details("None", "", "", "")
        set_form = getattr(self.view, "set_physics_form", None)
        if callable(set_form):
            set_form(None, {})
        self._set_physics_details_enabled(False)

    def _populate_rigid_body_form(self, rigid_body):
        self._set_cached_form(
            "rigid",
            {
                "name": rigid_body.name,
                "name_english": rigid_body.name_english,
                "shape": rigid_body.shape_type,
                "physics_mode": rigid_body.physics_mode,
                "related_bone": rigid_body.related_bone_index,
                "collision_group": rigid_body.collision_group,
                "collision_mask": rigid_body.collision_mask,
                "mass": rigid_body.mass,
                "linear_damping": rigid_body.linear_damping,
                "angular_damping": rigid_body.angular_damping,
                "restitution": rigid_body.restitution,
                "friction": rigid_body.friction,
            },
        )

    def _populate_joint_form(self, joint):
        self._set_cached_form(
            "joint",
            {
                "name": joint.name,
                "name_english": joint.name_english,
                "joint_type": joint.joint_type,
                "rigid_body_a": joint.rigid_body_a_index,
                "rigid_body_b": joint.rigid_body_b_index,
                "linear_constraint_states": _format_vector(joint.linear_constraint_states),
                "angular_constraint_states": _format_vector(joint.angular_constraint_states),
                "translation_limit_min": _format_vector(joint.translation_limit_min),
                "translation_limit_max": _format_vector(joint.translation_limit_max),
                "rotation_limit_min_degrees": _format_vector(joint.rotation_limit_min_degrees),
                "rotation_limit_max_degrees": _format_vector(joint.rotation_limit_max_degrees),
                "spring_translation": _format_vector(joint.spring_translation),
                "spring_rotation": _format_vector(joint.spring_rotation),
                "spring_translation_enabled": _format_vector(joint.spring_translation_enabled),
                "spring_rotation_enabled": _format_vector(joint.spring_rotation_enabled),
            },
        )

    def _set_cached_form(self, kind, values):
        self._validated_form_values = None
        self._validated_form_ref = None
        set_form = getattr(self.view, "set_physics_form", None)
        if callable(set_form):
            set_form(kind, values)

    def _set_validation_error(self, error):
        setter = getattr(self.view, "set_physics_validation_error", None)
        if callable(setter):
            setter(error.field_key, error.message_key, error.params)

    def _clear_validation_error(self):
        setter = getattr(self.view, "set_physics_validation_error", None)
        if callable(setter):
            setter()

    def _set_write_error(self, error):
        self._set_validation_error(error)
        setter = getattr(self.view, "set_physics_dirty", None)
        if callable(setter):
            setter(True, valid=False)

    def _capture_ui_state(self):
        list_tabs = getattr(self.view, "list_tabs", None)
        current_index = getattr(list_tabs, "currentIndex", None)
        splitter = getattr(self.view, "splitter", None)
        sizes = getattr(splitter, "sizes", None)
        return _PhysicsUiState(
            list_tab_index=int(current_index()) if callable(current_index) else 0,
            rigid_search=self._search_text("rigid_body_search_edit"),
            joint_search=self._search_text("joint_search_edit"),
            splitter_sizes=tuple(int(value) for value in sizes()) if callable(sizes) else (),
            selected_rigid_transform=self._selected_transform(self.view.rigid_body_list),
            selected_joint_transform=self._selected_transform(self.view.joint_list),
        )

    def _restore_ui_state(self, root):
        state = self._ui_state_by_root.get(root, self._default_ui_state)
        self._set_search_text("rigid_body_search_edit", state.rigid_search)
        self._set_search_text("joint_search_edit", state.joint_search)
        self.filter_rigid_bodies(state.rigid_search)
        self.filter_joints(state.joint_search)

        splitter = getattr(self.view, "splitter", None)
        set_sizes = getattr(splitter, "setSizes", None)
        if state.splitter_sizes and callable(set_sizes):
            set_sizes(list(state.splitter_sizes))

        list_tabs = getattr(self.view, "list_tabs", None)
        set_current_index = getattr(list_tabs, "setCurrentIndex", None)
        if callable(set_current_index):
            set_current_index(state.list_tab_index)

        if state.list_tab_index == 0:
            self._restore_list_selection(
                self.view.rigid_body_list,
                state.selected_rigid_transform,
            )
        else:
            self._restore_list_selection(
                self.view.joint_list,
                state.selected_joint_transform,
            )

    def _set_search_text(self, attr_name, value):
        search_edit = getattr(self.view, attr_name, None)
        setter = getattr(search_edit, "setText", None)
        if callable(setter):
            setter(value)

    def _selected_transform(self, list_widget):
        selected_items = list_widget.selectedItems() if list_widget is not None else []
        if not selected_items:
            return None
        return selected_items[0].data(Qt.UserRole)

    def _restore_list_selection(self, list_widget, transform):
        if not transform or not self.maya_adapter.object_exists(transform):
            return
        for item in self._iter_list_items(list_widget):
            if item.data(Qt.UserRole) != transform:
                continue
            is_hidden = getattr(item, "isHidden", None)
            if callable(is_hidden) and is_hidden():
                return
            set_current_item = getattr(list_widget, "setCurrentItem", None)
            if callable(set_current_item):
                set_current_item(item)
                return
            select_items = getattr(list_widget, "select_items", None)
            if callable(select_items):
                select_items(item)
                return

    def _set_details(self, name: str, kind: str, shape_or_type: str, bodies: str, node: str = ""):
        label_values = {
            "detail_name_value": name,
            "detail_type_value": kind,
            "detail_shape_value": shape_or_type,
            "detail_bodies_value": bodies,
            "detail_node_value": node,
        }
        for attr_name, value in label_values.items():
            label = getattr(self.view, attr_name, None)
            if label is not None and hasattr(label, "setText"):
                label.setText(value)


def _rigid_body_item(rigid_body: RigidBodySceneRef):
    label = (
        f"{rigid_body.index}: {rigid_body.name} "
        f"({_SHAPE_LABELS.get(rigid_body.shape_type, 'unknown')}, "
        f"mode={rigid_body.physics_mode}, bone={rigid_body.related_bone_index})"
    )
    item = QListWidgetItem(label)
    item.setData(Qt.UserRole, rigid_body.transform)
    return item


def _joint_item(joint: JointSceneRef):
    label = f"{joint.name} (type={joint.joint_type}, A={joint.rigid_body_a_index}, B={joint.rigid_body_b_index})"
    item = QListWidgetItem(label)
    item.setData(Qt.UserRole, joint.transform)
    return item


def _format_vector(values):
    def _format(value):
        if isinstance(value, bool):
            return "1" if value else "0"
        if isinstance(value, int):
            return str(value)
        return repr(float(value))

    return ", ".join(_format(value) for value in values)
