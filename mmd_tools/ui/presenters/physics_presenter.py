from dataclasses import replace
from typing import Optional

from ...core.physics_scene_query import MayaPhysicsSceneReader, JointSceneRef, PhysicsSceneRefs, RigidBodySceneRef
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
    reload_for_current_model_change,
    select_existing_user_role_nodes,
)

logger = get_logger(__name__)

_SHAPE_LABELS = {
    0: "sphere",
    1: "box",
    2: "capsule",
}


class PhysicsPresenter:
    def __init__(self, view, app_state, maya_adapter=None, physics_reader=None):
        self.view = view
        self.app_state = app_state
        self.maya_adapter = maya_adapter or MayaCmdsAdapter()
        self.physics_reader = physics_reader or MayaPhysicsSceneReader(self.maya_adapter)
        self._rigid_bodies_by_transform = {}
        self._joints_by_transform = {}
        self.connect_signals()

    def connect_signals(self):
        # ApplicationStateのシグナル
        self.app_state.current_model_changed.connect(self.on_current_model_changed)
        self.view.rigid_body_list.itemSelectionChanged.connect(self.on_rigid_body_selection_changed)
        self.view.joint_list.itemSelectionChanged.connect(self.on_joint_selection_changed)
        refresh_btn = getattr(self.view, "refresh_btn", None)
        if refresh_btn is not None:
            refresh_btn.clicked.connect(self.load_physics)
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

    def on_current_model_changed(self, model_root):
        """現在のモデルが変更されたときの処理"""
        reload_for_current_model_change(logger, "PhysicsPresenter", model_root, self.load_physics)

    def load_physics(self):
        self.view.rigid_body_list.clear()
        self.view.joint_list.clear()
        self._rigid_bodies_by_transform.clear()
        self._joints_by_transform.clear()
        self._reset_details()

        current_model_root = self.app_state.current_model_root
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
            self._set_physics_details_enabled(True)
            self._set_details(
                rigid_body.name,
                "Rigid body",
                f"{_SHAPE_LABELS.get(rigid_body.shape_type, 'unknown')}, mode={rigid_body.physics_mode}",
                f"bone={rigid_body.related_bone_index}",
                rigid_body.transform,
            )
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
            self._set_physics_details_enabled(True)
            self._set_details(
                joint.name,
                "Joint",
                f"type={joint.joint_type}",
                f"A={joint.rigid_body_a_index}, B={joint.rigid_body_b_index}",
                joint.transform,
            )
        else:
            self._reset_details()

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
        self._set_details("None", "", "", "")
        self._set_physics_details_enabled(False)

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
