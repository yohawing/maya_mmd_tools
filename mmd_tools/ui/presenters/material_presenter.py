from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
import math
import re
from typing import Protocol

from mmd_tools.core import maya_attribute_utils
from mmd_tools.core import maya_material_utils
from mmd_tools.converters.material_shader_parameters import (
    ATTR_MMD_EDGE_ALPHA,
    ATTR_MMD_DIFFUSE_ALPHA,
)
from mmd_tools.core.constants import (
    ATTR_MMD_DRAW_FLAGS,
    ATTR_MMD_DIFFUSE_COLOR,
    ATTR_MMD_SPECULAR_COLOR,
    ATTR_MMD_AMBIENT_COLOR,
    ATTR_MMD_SHININESS,
    ATTR_MMD_EDGE_COLOR,
    ATTR_MMD_EDGE_SIZE,
    ATTR_MMD_MATERIAL_NAME,
    ATTR_MMD_MATERIAL_NAME_EN,
    ATTR_MMD_MATERIAL_INDEX,
    ATTR_MMD_ORIGINAL_TEXTURE_PATH,
    ATTR_MMD_SPHERE_MODE,
    ATTR_MMD_SPHERE_PATH,
    ATTR_MMD_SHARED_TOON_FLAG,
    ATTR_MMD_TOON_PATH,
    ATTR_MMD_TOON_TEXTURE_INDEX,
)
from ...adapters.maya_cmds_adapter import MayaCmdsAdapter
from ...core.logger import get_logger
from ...core.model_registry import (
    REGISTRY_CATEGORY_MATERIAL,
    list_model_registry_members_from_adapter,
)
from ...core.model_authoring_spec import MmdMaterialSpec, MmdModelAuthoringSpec
from ...core.material_authoring import classify_material_change
from ..qt_compat import QColorDialog, QFileDialog, QColor, Qt
from ..translations import UITranslator
from .list_presenter_helpers import (
    apply_list_filter,
    format_indexed_node_label,
    reload_for_current_model_change,
    select_existing_user_role_nodes,
    tr_message_format,
)

logger = get_logger(__name__)

MATERIAL_INDEX_ROLE = Qt.UserRole + 1
MATERIAL_ASSIGNMENT_ROLE = Qt.UserRole + 2


class MaterialAuthoringCoordinator(Protocol):
    """Transactional semantic/binding boundary used by Material Tab CRUD."""

    def create_material(self, model_root: str) -> MmdMaterialSpec: ...

    def duplicate_material(self, model_root: str, source_index: int) -> MmdMaterialSpec: ...

    def delete_material(self, model_root: str, material_index: int) -> object: ...

    def reindex_materials(self, model_root: str, ordered_indices: Sequence[int]) -> object: ...

    def move_material(self, model_root: str, index: int, new_position: int) -> object: ...

    def move_material_fast(self, model_root: str, index: int, new_position: int) -> object: ...

    def read_spec(self, model_root: str) -> MmdModelAuthoringSpec: ...

    def replace_material(self, model_root: str, material: MmdMaterialSpec) -> MmdModelAuthoringSpec: ...

    def apply_material_value_patch(self, model_root: str, material: MmdMaterialSpec) -> object: ...

    def apply_material_binding_patch(self, model_root: str, material: MmdMaterialSpec) -> object: ...


class MaterialPresenter:
    def __init__(self, view, app_state, maya_adapter=None, authoring_coordinator=None):
        self.view = view
        self.app_state = app_state
        self.maya_adapter = maya_adapter or MayaCmdsAdapter()
        self.authoring_coordinator = authoring_coordinator
        self.current_material = None
        self.current_material_index = None
        self.material_data = {}  # Store original material data for reset
        self.has_unsaved_changes = False
        self._loading_properties = False  # Flag to prevent change tracking during loading
        self.connect_signals()
        self._update_authoring_actions()

        # 既に選択されているモデルがある場合はロード
        if self.app_state.current_model_root:
            self.load_materials()

    def connect_signals(self):
        # ApplicationStateのシグナル
        self.app_state.current_model_changed.connect(self.on_current_model_changed)

        # UIのシグナル
        self.view.material_list.currentItemChanged.connect(self.on_material_selected)
        self.view.material_list.itemSelectionChanged.connect(self.on_selection_changed_maya)
        self.view.refresh_btn.clicked.connect(self.load_materials)
        self.view.search_edit.textChanged.connect(self.on_search_text_changed)
        for button_name, handler in (
            ("create_btn", self.create_material),
            ("duplicate_btn", self.duplicate_material),
            ("delete_btn", self.delete_material),
            ("reindex_up_btn", lambda: self.move_material(-1)),
            ("reindex_down_btn", lambda: self.move_material(1)),
        ):
            button = getattr(self.view, button_name, None)
            if button is not None:
                button.clicked.connect(handler)

        # Color widgets (clickable)
        self.view.diffuse_color_widget.mousePressEvent = lambda e: self.pick_color("diffuse")
        self.view.specular_color_widget.mousePressEvent = lambda e: self.pick_color("specular")
        self.view.ambient_color_widget.mousePressEvent = lambda e: self.pick_color("ambient")
        self.view.edge_color_widget.mousePressEvent = lambda e: self.pick_color("edge")

        # File browsers
        self.view.texture_browse_btn.clicked.connect(lambda: self.browse_file("texture"))
        self.view.sphere_map_browse_btn.clicked.connect(lambda: self.browse_file("sphere"))

        # Track changes in input fields
        self.view.material_jp_name_edit.textChanged.connect(self._on_value_changed)
        self.view.material_en_name_edit.textChanged.connect(self._on_value_changed)
        self.view.texture_path_edit.textChanged.connect(self._on_value_changed)
        self.view.sphere_map_path_edit.textChanged.connect(self._on_value_changed)
        self.view.specular_coefficient_spin.valueChanged.connect(self._on_value_changed)
        self.view.transparency_spin.valueChanged.connect(self._on_value_changed)
        self.view.edge_size_spin.valueChanged.connect(self._on_value_changed)
        self.view.sphere_mode_combo.currentIndexChanged.connect(self._on_value_changed)
        self.view.toon_texture_combo.currentIndexChanged.connect(self._on_value_changed)
        for control_name in ("toon_texture_path_edit", "toon_texture_index_spin"):
            control = getattr(self.view, control_name, None)
            if control is not None:
                signal = getattr(control, "textChanged", None) or getattr(control, "valueChanged", None)
                if signal is not None:
                    signal.connect(self._on_value_changed)
        toon_sharing_check = getattr(self.view, "toon_sharing_check", None)
        if toon_sharing_check is not None:
            toon_sharing_check.stateChanged.connect(self._on_toon_sharing_changed)

        # Slider connections for transparency and specular coefficient
        self.view.transparency_slider.valueChanged.connect(lambda v: self.view.transparency_spin.setValue(v / 100.0))
        self.view.transparency_spin.valueChanged.connect(lambda v: self.view.transparency_slider.setValue(int(v * 100)))

        self.view.specular_coefficient_slider.valueChanged.connect(
            lambda v: self.view.specular_coefficient_spin.setValue(v / 100.0)
        )
        self.view.specular_coefficient_spin.valueChanged.connect(
            lambda v: self.view.specular_coefficient_slider.setValue(int(v * 100))
        )

        # Check boxes
        for checkbox in [
            self.view.both_face_check,
            self.view.ground_shadow_check,
            self.view.self_shadow_map_check,
            self.view.self_shadow_check,
            self.view.edge_draw_check,
            self.view.vertex_color_check,
            self.view.point_draw_check,
            self.view.line_draw_check,
        ]:
            checkbox.stateChanged.connect(self._on_value_changed)

        # Apply/Reset buttons
        self.view.apply_btn.clicked.connect(self.apply_changes)
        self.view.reset_btn.clicked.connect(self.reset_changes)


    def on_current_model_changed(self, model_root):
        """現在のモデルが変更されたときの処理"""
        reload_for_current_model_change(logger, "MaterialPresenter", model_root, self.load_materials)
        self._update_authoring_actions()

    def tr_message(self, key: str) -> str:
        """Translate a material presenter message key."""
        return UITranslator.instance().translate(key, "messages")

    def load_materials(self):
        self.view.material_list.clear()
        self.current_material = None
        self.current_material_index = None

        current_model_root = self.app_state.current_model_root
        if not current_model_root or not self.maya_adapter.object_exists(current_model_root):
            self.view._set_details_enabled(False)
            self.view._show_placeholder()
            self._update_authoring_actions()
            return

        try:
            # MMDマテリアルノードを探す
            # Registry ownership keeps newly-created/unassigned materials
            # discoverable. Legacy roots retain mesh/SG discovery below.
            registry_materials = list_model_registry_members_from_adapter(
                self.maya_adapter,
                current_model_root,
                REGISTRY_CATEGORY_MATERIAL,
            )
            mmd_materials = list(registry_materials or [])

            if registry_materials is None:
                shapes = self.maya_adapter.list_relatives(current_model_root, allDescendents=True, type="mesh")
                if shapes:
                    shading_groups = self.maya_adapter.list_connections(shapes, type="shadingEngine")
                    if shading_groups:
                        shading_groups = list(set(shading_groups))
                        for sg in shading_groups:
                            materials = self.maya_adapter.ls(self.maya_adapter.list_connections(sg), materials=True)
                            if materials:
                                for mat in materials:
                                    # MMD関連の属性があるかチェック
                                    if self.maya_adapter.attribute_exists(ATTR_MMD_MATERIAL_NAME, mat):
                                        mmd_materials.append(mat)

            # 重複を削除
            unique_materials = list(set(mmd_materials))

            indexed_materials = []
            for ordinal, mat in enumerate(sorted(unique_materials)):
                material_index = (
                    self._read_material_index(mat)
                    if self.authoring_coordinator is not None
                    else None
                )
                indexed_materials.append(
                    (material_index if material_index is not None else ordinal, mat, material_index)
                )

            assignment_summaries = {
                mat: self._read_material_assignment_summary(current_model_root, mat)
                for _display_index, mat, _material_index in indexed_materials
            }

            # Add materials in semantic index order when canonical indices are available.
            for display_index, mat, material_index in sorted(indexed_materials):
                # 日本語名と英語名を取得
                jp_name = maya_attribute_utils.get_attribute(mat, ATTR_MMD_MATERIAL_NAME)
                en_name = maya_attribute_utils.get_attribute(mat, ATTR_MMD_MATERIAL_NAME_EN)

                display_text = format_indexed_node_label(display_index + 1, jp_name, mat, en_name)
                assignment_summary = assignment_summaries.get(mat, "meshes=0, faces=0")

                # リストに追加
                from ..qt_compat import QListWidgetItem

                item = QListWidgetItem(display_text)
                item.setData(Qt.UserRole, mat)  # 実際のマテリアル名を保存
                item.setData(MATERIAL_INDEX_ROLE, material_index)
                item.setData(MATERIAL_ASSIGNMENT_ROLE, assignment_summary)
                item.setToolTip(mat)
                self.view.material_list.addItem(item)

            # Show placeholder if no materials
            if self.view.material_list.count() == 0:
                self.view._show_placeholder()

            logger.debug(f"Loaded {self.view.material_list.count()} MMD materials for model: {current_model_root}")
            self._update_authoring_actions()

        except Exception as e:
            logger.error(f"Failed to load materials: {e}", exc_info=True)
            self.view._set_details_enabled(False)
            self.view._show_placeholder()
            self.app_state.emit_status(tr_message_format("materials_load_failed", error=str(e)))

    def on_material_selected(self, current, previous):
        if not current:
            self.view._set_details_enabled(False)
            return

        # プレースホルダーアイテムの場合は何もしない
        if current.text().startswith("--"):
            return

        # 未保存の変更がある場合は警告
        if self.has_unsaved_changes and previous:
            from ..qt_compat import QMessageBox

            reply = QMessageBox.question(
                self.view,
                self.tr_message("unsaved_changes_title"),
                self.tr_message("unsaved_changes_select_material"),
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )

            if reply == QMessageBox.No:
                # 前の選択に戻す
                self.view.material_list.blockSignals(True)
                self.view.material_list.setCurrentItem(previous)
                self.view.material_list.blockSignals(False)
                return

        # 実際のマテリアル名を取得（UserRoleに保存されている）
        material_name = current.data(Qt.UserRole)
        if not material_name:
            # 互換性のため、データがない場合はテキストを使用
            material_name = current.text()

        logger.debug(f"Selected material: {material_name}")

        self.current_material = material_name
        material_index = current.data(MATERIAL_INDEX_ROLE)
        self.current_material_index = material_index if type(material_index) is int else self._read_material_index(
            material_name
        )
        # 変更フラグを事前にリセットして、ロード中の変更検知を無効化
        self.has_unsaved_changes = False
        self.view._set_details_enabled(True)
        self._update_authoring_actions()

        # Mayaでマテリアルを選択
        try:
            # Material-list selection is deliberately shader-only.  Maya's
            # standard set membership remains the assignment authority; do
            # not open HyperShade or implicitly assign the selected shader.
            self.maya_adapter.select(material_name, replace=True)
            logger.debug(f"Selected material in Maya: {material_name}")
        except Exception as e:
            logger.warning(f"Could not select material in Maya: {e}")

        self.load_material_properties(material_name)

    def _read_material_index(self, material):
        """Read a binding index for UI routing, never as semantic authority."""
        try:
            if not self.maya_adapter.attribute_exists(ATTR_MMD_MATERIAL_INDEX, material):
                return None
            value = self.maya_adapter.get_attr(f"{material}.{ATTR_MMD_MATERIAL_INDEX}")
            return value if type(value) is int and value >= 0 else None
        except Exception:
            return None

    def _read_material_assignment_summary(self, model_root: str, shader: str) -> str:
        """Read current Maya shadingEngine membership without mutating scene state.

        The registry remains the material ownership/list authority.  This
        projection only reports the live standard-set membership below the
        current model root; export continues to collect face ownership from
        the same Maya shading graph.
        """
        try:
            meshes = {
                node
                for node in (
                    self.maya_adapter.list_relatives(
                        model_root,
                        allDescendents=True,
                        fullPath=True,
                        type="mesh",
                    )
                    or ()
                )
                if isinstance(node, str) and node.startswith(f"{model_root}|")
            }
            mesh_parents = {node.rsplit("|", 1)[0] for node in meshes if "|" in node}
            shading_groups = self.maya_adapter.list_connections(shader, type="shadingEngine") or ()
            if isinstance(shading_groups, (str, bytes, bytearray)):
                shading_groups = (shading_groups,)
            members_by_mesh: dict[str, int] = {}
            explicit_face_count = 0
            for shading_group in shading_groups:
                if not isinstance(shading_group, str):
                    continue
                members = self.maya_adapter.sets(shading_group, query=True) or ()
                if isinstance(members, (str, bytes, bytearray)):
                    members = (members,)
                for member in members:
                    if not isinstance(member, str) or not member.startswith(f"{model_root}|"):
                        continue
                    base = member.split(".f[", 1)[0]
                    if meshes and base not in meshes and base not in mesh_parents:
                        # Maya may return the transform instead of its shape;
                        # retain only members that are still below this root.
                        continue
                    face_match = re.search(r"\.f\[(\d+)(?::(\d+))?\]", member)
                    if face_match is None:
                        members_by_mesh.setdefault(base, 0)
                        continue
                    start = int(face_match.group(1))
                    end = int(face_match.group(2) or start)
                    if end < start:
                        continue
                    members_by_mesh.setdefault(base, 0)
                    members_by_mesh[base] += end - start + 1
                    explicit_face_count += end - start + 1
            mesh_count = len(members_by_mesh)
            if mesh_count == 0:
                return "meshes=0, faces=0"
            face_summary = str(explicit_face_count) if explicit_face_count else "all"
            return f"meshes={mesh_count}, faces={face_summary}"
        except Exception:
            # Refresh must remain usable when an older adapter lacks optional
            # set-query support; no write or inferred ownership is performed.
            return "meshes=0, faces=?"

    def _update_authoring_actions(self):
        root = self.app_state.current_model_root
        has_root = bool(root and self.maya_adapter.object_exists(root))
        available = self.authoring_coordinator is not None and has_root
        selected = available and type(self.current_material_index) is int
        raw_row = self.view.material_list.currentRow() if selected else -1
        raw_count = self.view.material_list.count() if selected else 0
        row = raw_row if type(raw_row) is int else -1
        count = raw_count if type(raw_count) is int else 0
        can_move_up = selected and row > 0
        can_move_down = selected and 0 <= row < count - 1
        translate = getattr(self.view, "tr", None)
        if callable(translate):
            reason_unavailable = translate("authoring_unavailable", "tooltips")
            reason_selection = translate("authoring_selection_required", "tooltips")
            reason_boundary = translate("authoring_move_boundary", "tooltips")
        else:
            reason_unavailable = "Authoring coordinator is not available"
            reason_selection = "Select an item first"
            reason_boundary = "The selected item is already at this edge"
        if selected:
            move_reason = reason_boundary
            move_reason_key = "authoring_move_boundary"
        elif available:
            move_reason = reason_selection
            move_reason_key = "authoring_selection_required"
        else:
            move_reason = reason_unavailable
            move_reason_key = "authoring_unavailable"
        for button_name, enabled, reason, reason_key in (
            ("create_btn", available, "" if available else reason_unavailable, "" if available else "authoring_unavailable"),
            ("duplicate_btn", selected, "" if selected else (reason_selection if available else reason_unavailable), "" if selected else ("authoring_selection_required" if available else "authoring_unavailable")),
            ("delete_btn", selected, "" if selected else (reason_selection if available else reason_unavailable), "" if selected else ("authoring_selection_required" if available else "authoring_unavailable")),
            (
                "reindex_up_btn",
                can_move_up,
                "" if can_move_up else move_reason,
                "" if can_move_up else move_reason_key,
            ),
            (
                "reindex_down_btn",
                can_move_down,
                "" if can_move_down else move_reason,
                "" if can_move_down else move_reason_key,
            ),
        ):
            button = getattr(self.view, button_name, None)
            if button is not None:
                set_reason = getattr(button, "set_disabled_reason", None)
                if callable(set_reason):
                    set_reason(reason, reason_key)
                button.setEnabled(bool(enabled))

    def _authoring_root(self):
        root = self.app_state.current_model_root
        if not root or not self.maya_adapter.object_exists(root):
            self.app_state.emit_status(self.tr_message("material_authoring_root_missing"))
            return None
        if self.authoring_coordinator is None:
            self.app_state.emit_status(self.tr_message("material_authoring_unavailable"))
            return None
        return root

    def _run_authoring(self, operation, *args):
        root = self._authoring_root()
        if root is None:
            return False
        try:
            getattr(self.authoring_coordinator, operation)(root, *args)
        except Exception as exc:
            logger.error("Material authoring %s failed", operation, exc_info=True)
            self.app_state.emit_status(
                tr_message_format("material_authoring_failed", operation=operation, error=str(exc))
            )
            return False
        self.load_materials()
        self.app_state.emit_status(self.tr_message(f"material_{operation}_succeeded"))
        return True

    def _run_material_create(self, operation, *args):
        """Run create/duplicate and append exactly one selected list row."""
        root = self._authoring_root()
        if root is None:
            return False
        try:
            result = getattr(self.authoring_coordinator, operation)(root, *args)
            if not isinstance(result, MmdMaterialSpec):
                raise TypeError("material creation returned an invalid material")
            self._append_material_row(result)
        except Exception as exc:
            logger.error("Material authoring %s failed", operation, exc_info=True)
            self.app_state.emit_status(
                tr_message_format("material_authoring_failed", operation=operation, error=str(exc))
            )
            return False
        self.app_state.emit_status(self.tr_message(f"material_{operation}_succeeded"))
        return True

    def _append_material_row(self, material: MmdMaterialSpec) -> None:
        """Append and select one newly-created row without reloading the list."""
        from ..qt_compat import QListWidgetItem

        binding = material.binding_identity
        if not isinstance(binding, str) or not binding:
            raise TypeError("created material has no Maya binding identity")
        item = QListWidgetItem(
            format_indexed_node_label(
                material.index + 1,
                material.name,
                binding,
                material.name_english,
            )
        )
        item.setData(Qt.UserRole, binding)
        item.setData(MATERIAL_INDEX_ROLE, material.index)
        item.setData(MATERIAL_ASSIGNMENT_ROLE, "meshes=0, faces=0")
        item.setToolTip(binding)
        self.view.material_list.addItem(item)
        self.view.material_list.setCurrentItem(item)
        self.current_material = binding
        self.current_material_index = material.index
        self.has_unsaved_changes = False
        self._update_authoring_actions()

    def create_material(self):
        """Request one transactional semantic material creation."""
        return self._run_material_create("create_material")

    def duplicate_material(self):
        """Request duplication of the selected semantic material."""
        if type(self.current_material_index) is not int:
            return False
        return self._run_material_create("duplicate_material", self.current_material_index)

    def delete_material(self):
        """Confirm and request transactional deletion of the selected material."""
        if type(self.current_material_index) is not int:
            return False
        from ..qt_compat import QMessageBox

        reply = QMessageBox.question(
            self.view,
            self.tr_message("material_delete_title"),
            self.tr_message("material_delete_confirm"),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return False
        return self._run_authoring("delete_material", self.current_material_index)

    def move_material(self, direction):
        """Swap the selected material with one adjacent row transactionally."""
        if type(direction) is not int or direction not in (-1, 1) or type(self.current_material_index) is not int:
            return False
        root = self._authoring_root()
        if root is None:
            return False
        try:
            material_list = self.view.material_list
            count = material_list.count()
            if type(count) is not int:
                raise TypeError("material list count is invalid")
            position = None
            for row in range(count):
                item = material_list.item(row)
                if item is None:
                    continue
                if item.data(MATERIAL_INDEX_ROLE) == self.current_material_index:
                    position = row
                    break
                if position is None and item.data(Qt.UserRole) == self.current_material:
                    position = row
            if position is None and 0 <= self.current_material_index < count:
                # Older/headless views may not provide either item role.  The
                # legacy list is still ordered by the semantic material index.
                position = self.current_material_index
            if position is None:
                raise RuntimeError("selected material row is missing")
            target = position + direction
            if target < 0 or target >= count:
                return False
            target_item = material_list.item(target)
            target_index = target_item.data(MATERIAL_INDEX_ROLE) if target_item is not None else None
            if type(target_index) is not int:
                target_index = target
            selected_binding = self.current_material
            move = getattr(self.authoring_coordinator, "move_material_fast", None)
            if not callable(move):
                raise TypeError("material authoring coordinator lacks move_material_fast")
            move(root, self.current_material_index, target_index)
        except Exception as exc:
            logger.error("Material authoring reindex_materials failed", exc_info=True)
            self.app_state.emit_status(
                tr_message_format(
                    "material_authoring_failed",
                    operation="move_material",
                    error=str(exc),
                )
            )
            return False
        self._swap_material_rows(position, target)
        self.current_material_index = target_index
        for row in range(self.view.material_list.count()):
            item = self.view.material_list.item(row)
            if item.data(Qt.UserRole) == selected_binding:
                self.view.material_list.setCurrentItem(item)
                break
        self._update_authoring_actions()
        self.app_state.emit_status(self.tr_message("material_reindex_materials_succeeded"))
        return True

    def _swap_material_rows(self, first_row: int, second_row: int) -> None:
        """Swap two existing list items and refresh only their index labels."""
        material_list = self.view.material_list
        if first_row == second_row:
            return
        count = material_list.count()
        if type(count) is int and (first_row < 0 or second_row < 0 or max(first_row, second_row) >= count):
            raise RuntimeError("material list rows disappeared during reindex")
        if type(count) is not int:
            # Headless/legacy views may not expose real list rows.  The
            # semantic transaction has already succeeded; retain selection
            # state without triggering a full list reload.
            return
        low, high = sorted((first_row, second_row))
        take_item = getattr(material_list, "takeItem", None)
        insert_item = getattr(material_list, "insertItem", None)
        if callable(take_item) and callable(insert_item):
            high_item = take_item(high)
            low_item = take_item(low)
            if high_item is None or low_item is None:
                raise RuntimeError("material list rows disappeared during reindex")
            insert_item(low, high_item)
            insert_item(high, low_item)
            first_item = material_list.item(first_row)
            second_item = material_list.item(second_row)
        else:
            first_item = material_list.item(first_row)
            second_item = material_list.item(second_row)
            swap = getattr(material_list, "swapItemsAt", None)
            if not callable(swap):
                raise RuntimeError("material list does not support row swapping")
            swap(first_row, second_row)
            first_item = material_list.item(first_row)
            second_item = material_list.item(second_row)

        for row, item in ((first_row, first_item), (second_row, second_item)):
            if item is None:
                raise RuntimeError("material list row is missing after reindex")
            binding = item.data(Qt.UserRole)
            if not isinstance(binding, str) or not binding:
                raise RuntimeError("material list row has no material binding")
            semantic_index = row
            item.setData(MATERIAL_INDEX_ROLE, semantic_index)
            try:
                jp_name = maya_attribute_utils.get_attribute(binding, ATTR_MMD_MATERIAL_NAME)
                en_name = maya_attribute_utils.get_attribute(binding, ATTR_MMD_MATERIAL_NAME_EN)
                item.setText(format_indexed_node_label(semantic_index + 1, jp_name, binding, en_name))
            except Exception:
                # Keep the existing label text when a headless adapter cannot
                # read optional display metadata; only its numeric prefix is
                # stale after the local row swap.
                prior_text = item.text()
                suffix = prior_text.split(":", 1)[1] if ":" in prior_text else prior_text
                item.setText(f"{semantic_index + 1}:{suffix}")

    def load_material_properties(self, material_name):
        """Load material properties from Maya material"""
        self._loading_properties = True
        try:
            # Store original data for reset
            self.material_data = {}

            # Japanese name
            jp_name = maya_attribute_utils.get_attribute(material_name, ATTR_MMD_MATERIAL_NAME)
            self.view.material_jp_name_edit.setText(jp_name if jp_name else "")
            self.material_data["jp_name"] = jp_name if jp_name else ""

            # English name
            en_name = maya_attribute_utils.get_attribute(material_name, ATTR_MMD_MATERIAL_NAME_EN)
            self.view.material_en_name_edit.setText(en_name if en_name else "")
            self.material_data["en_name"] = en_name if en_name else ""

            # Get basic colors
            # Check shader type
            shader_type = self.maya_adapter.node_type(material_name)

            diffuse_fallbacks = (
                ("DiffuseColorRGB", "g_Diffuse")
                if shader_type in ("dx11Shader", "GLSLShader")
                else (("baseColor",) if shader_type == "standardSurface" else ("color",))
            )
            diffuse_color, diffuse_owned = self._load_base_value(
                material_name,
                ATTR_MMD_DIFFUSE_COLOR,
                diffuse_fallbacks,
                (0.5, 0.5, 0.5),
            )
            self.material_data["_diffuse_base_owned"] = diffuse_owned
            self.material_data["diffuse"] = diffuse_color
            self._update_color_widget(self.view.diffuse_color_widget, diffuse_color)

            # Get specular color
            specular_fallbacks = (
                ("SpecularColor",)
                if shader_type in ("dx11Shader", "GLSLShader")
                else ("specularColor",)
            )
            specular_color, specular_owned = self._load_base_value(
                material_name,
                ATTR_MMD_SPECULAR_COLOR,
                specular_fallbacks,
                (0.5, 0.5, 0.5),
            )
            self.material_data["_specular_base_owned"] = specular_owned

            # タプルが正しい形式であることを確認
            if not isinstance(specular_color, (list, tuple)) or len(specular_color) < 3:
                specular_color = (0.5, 0.5, 0.5)

            self.material_data["specular"] = specular_color
            self._update_color_widget(self.view.specular_color_widget, specular_color)

            # Get ambient - Maya doesn't have ambient by default, check if attr exists
            ambient_fallbacks = (
                ("AmbientColor",)
                if shader_type in ("dx11Shader", "GLSLShader")
                else ("ambientColor",)
            )
            ambient_color, ambient_owned = self._load_base_value(
                material_name,
                ATTR_MMD_AMBIENT_COLOR,
                ambient_fallbacks,
                (0.5, 0.5, 0.5),
            )
            self.material_data["_ambient_base_owned"] = ambient_owned

            # タプルが正しい形式であることを確認
            if not isinstance(ambient_color, (list, tuple)) or len(ambient_color) < 3:
                ambient_color = (0.5, 0.5, 0.5)

            self.material_data["ambient"] = ambient_color
            self._update_color_widget(self.view.ambient_color_widget, ambient_color)

            # Get specular coefficient (MMD style)
            if shader_type == "standardSurface":
                standard_specular_weight = float(self._get_attr_safe(material_name, "specular", 0.5))
                self.material_data["_standard_specular_weight"] = standard_specular_weight
                if self.maya_adapter.attribute_exists("mmd_specular_coefficient", material_name):
                    specular_coefficient = maya_attribute_utils.get_attribute(
                        material_name, "mmd_specular_coefficient"
                    )
                elif self.maya_adapter.attribute_exists(ATTR_MMD_SHININESS, material_name):
                    specular_coefficient = maya_attribute_utils.get_attribute(
                        material_name, ATTR_MMD_SHININESS
                    )
                else:
                    specular_coefficient = standard_specular_weight
                self.material_data["_specular_power_base_owned"] = True
            elif shader_type in ("dx11Shader", "GLSLShader") and self.maya_adapter.attribute_exists(
                "mmd_specular_coefficient", material_name
            ):
                specular_coefficient = maya_attribute_utils.get_attribute(material_name, "mmd_specular_coefficient")
                self.material_data["_specular_power_base_owned"] = True
            elif shader_type in ("dx11Shader", "GLSLShader") and self.maya_adapter.attribute_exists(
                ATTR_MMD_SHININESS, material_name
            ):
                specular_coefficient = maya_attribute_utils.get_attribute(material_name, ATTR_MMD_SHININESS)
                self.material_data["_specular_power_base_owned"] = True
            elif shader_type in ("dx11Shader", "GLSLShader") and self._plug_is_unconnected(
                material_name, "Shininess"
            ):
                specular_coefficient = maya_attribute_utils.get_attribute(material_name, "Shininess")
                self.material_data["_specular_power_base_owned"] = True
            elif self.maya_adapter.attribute_exists("specular", material_name):
                specular_coefficient = maya_attribute_utils.get_attribute(material_name, "specular")
                self.material_data["_specular_power_base_owned"] = True
            else:
                specular_coefficient = 0.5
                self.material_data["_specular_power_base_owned"] = False
            self.material_data["_authored_specular_coefficient"] = specular_coefficient
            # MaterialTab's form contract is 0..1 for every backend. Preserve
            # out-of-range imported PMX values separately until the user edits.
            specular_coefficient = max(0.0, min(1.0, float(specular_coefficient)))
            self.material_data["specular_coefficient"] = specular_coefficient
            self.view.specular_coefficient_spin.setValue(specular_coefficient)

            # Get transparency (PMX style)
            if shader_type in ("dx11Shader", "GLSLShader") and self.maya_adapter.attribute_exists(
                ATTR_MMD_DIFFUSE_ALPHA, material_name
            ):
                diffuse_alpha = float(maya_attribute_utils.get_attribute(material_name, ATTR_MMD_DIFFUSE_ALPHA))
                self.material_data["_diffuse_alpha_base_owned"] = True
                transparency = 1.0 - diffuse_alpha
            elif shader_type in ("dx11Shader", "GLSLShader") and self._plug_is_unconnected(
                material_name, "DiffuseColorA"
            ):
                transparency = 1.0 - float(maya_attribute_utils.get_attribute(material_name, "DiffuseColorA"))
                self.material_data["_diffuse_alpha_base_owned"] = True
            elif self.maya_adapter.attribute_exists("opacity", material_name):
                # StandardSurfaceの場合
                opacity = maya_attribute_utils.get_attribute(material_name, "opacity")
                transparency = 1.0 - opacity[0]  # Convert opacity to transparency
            elif self.maya_adapter.attribute_exists("transparency", material_name):
                transparency_val = maya_attribute_utils.get_attribute(material_name, "transparency")
                transparency = transparency_val[0]
            else:
                transparency = 0.0
                self.material_data["_diffuse_alpha_base_owned"] = False
            self.material_data["transparency"] = transparency
            self.view.transparency_spin.setValue(transparency)

            # Get texture paths
            # Check which attribute to look for connections
            texture_attrs = []
            if shader_type == "standardSurface":
                texture_attrs.append(f"{material_name}.baseColor")
            elif shader_type in ("dx11Shader", "GLSLShader"):
                # Hardware shader texture slots use the same names.
                if self.maya_adapter.attribute_exists("MainTexture", material_name):
                    texture_attrs.append(f"{material_name}.MainTexture")
                if self.maya_adapter.attribute_exists("DiffuseTexture", material_name):
                    texture_attrs.append(f"{material_name}.DiffuseTexture")
            if self.maya_adapter.attribute_exists("color", material_name):
                texture_attrs.append(f"{material_name}.color")
            # Also check for direct outColor connections
            if self.maya_adapter.attribute_exists("outColor", material_name):
                texture_attrs.append(f"{material_name}.outColor")

            # Debug: Log available attributes
            logger.debug(f"Material type: {shader_type}")
            logger.debug(f"Checking texture attributes: {texture_attrs}")

            # Also check all connections to the material
            all_connections = self.maya_adapter.list_connections(material_name, source=True, destination=False, plugs=True) or []
            logger.debug(f"All connections to {material_name}: {all_connections}")

            file_node = None
            # First try direct attribute connections
            for attr in texture_attrs:
                connections = self.maya_adapter.list_connections(attr, type="file", source=True, destination=False)
                if connections:
                    file_node = connections
                    logger.debug(f"Found file node connected to {attr}: {connections[0]}")
                    break

            # If not found, check for file nodes in the material's shading group
            if not file_node:
                shading_groups = self.maya_adapter.list_connections(material_name, type="shadingEngine")
                if shading_groups:
                    logger.debug(f"Found shading groups: {shading_groups}")
                    for sg in shading_groups:
                        file_nodes = self.maya_adapter.ls(self.maya_adapter.list_connections(sg), type="file") or []
                        if file_nodes:
                            file_node = file_nodes
                            logger.debug(f"Found file nodes in shading group {sg}: {file_nodes}")
                            break

            if file_node:
                texture_path = maya_attribute_utils.get_attribute(file_node[0], "fileTextureName")
                self.material_data["texture"] = texture_path
                self.view.texture_path_edit.setText(texture_path)
                self._load_texture_provenance(file_node[0])
                logger.debug(f"Loaded texture: {texture_path}")
            else:
                # Check if there's a stored texture path in MMD attributes
                mmd_texture_path = self._get_attr_safe(material_name, "mmd_texture_path", "")
                if mmd_texture_path:
                    self.material_data["texture"] = mmd_texture_path
                    self.view.texture_path_edit.setText(mmd_texture_path)
                    self._set_texture_provenance_fields("")
                    logger.debug(f"Loaded texture from MMD attribute: {mmd_texture_path}")
                else:
                    self.material_data["texture"] = ""
                    self.view.texture_path_edit.clear()
                    self._set_texture_provenance_fields("")
                    logger.debug(f"No texture found for material: {material_name}")

            # Get MMD-specific attributes if they exist
            self._load_mmd_attributes(material_name)
            self.material_data["_loaded_base_snapshot"] = {
                "diffuse": self.material_data.get("diffuse"),
                "specular": self.material_data.get("specular"),
                "ambient": self.material_data.get("ambient"),
                "transparency": self.material_data.get("transparency"),
                "specular_coefficient": self.material_data.get("specular_coefficient"),
            }

        except Exception as e:
            logger.error(
                f"Failed to load material details for {material_name}: {e}",
                exc_info=True,
            )
        finally:
            self._loading_properties = False
            # プロパティの読み込み完了後、変更フラグを確実にリセット
            self.has_unsaved_changes = False

    def _set_texture_provenance_fields(self, original_path):
        """Update read-only texture provenance fields when the view provides them."""

        if hasattr(self.view, "original_pmx_path_edit"):
            self.view.original_pmx_path_edit.setText(original_path or "")

    def _load_texture_provenance(self, file_node):
        original_path = ""
        try:
            if self.maya_adapter.attribute_exists(ATTR_MMD_ORIGINAL_TEXTURE_PATH, file_node):
                original_path = maya_material_utils.get_mmd_original_texture_path(file_node)
        except Exception:
            logger.debug("Failed to read original PMX texture path from %s", file_node, exc_info=True)
        self.material_data["original_pmx_texture_path"] = original_path
        self._set_texture_provenance_fields(original_path)

    def _load_mmd_attributes(self, material_name):
        """Load MMD-specific attributes from material"""
        # Debug: List all attributes on the material
        try:
            all_attrs = self.maya_adapter.list_attr(material_name, userDefined=True) or []
            if all_attrs:
                logger.debug(f"User-defined attributes on {material_name}: {all_attrs}")

            # dx11Shaderの場合、uniformParametersをチェック
            if self.maya_adapter.node_type(material_name) == "dx11Shader":
                uniform_params = self.maya_adapter.list_attr(material_name + ".uniformParameters") or []
                if uniform_params:
                    logger.debug(f"Uniform parameters on {material_name}: {uniform_params}")
        except Exception:
            pass

        # Sphere map
        sphere_path = self._get_attr_safe(material_name, ATTR_MMD_SPHERE_PATH, "")
        if not sphere_path:
            # mmd_sphere_pathカスタムアトリビュートからも確認
            sphere_path = self._get_attr_safe(material_name, "mmd_sphere_path", "")

        self.material_data["sphere_map"] = sphere_path
        self.view.sphere_map_path_edit.setText(sphere_path)

        # Sphere mode
        sphere_mode = self._get_attr_safe(material_name, ATTR_MMD_SPHERE_MODE, 0)
        self.material_data["sphere_mode"] = sphere_mode
        self.view.sphere_mode_combo.setCurrentIndex(sphere_mode)

        # Toon texture
        shared_toon_flag = self._get_attr_safe(material_name, ATTR_MMD_SHARED_TOON_FLAG, 1)
        try:
            shared_toon_flag = int(shared_toon_flag)
        except (TypeError, ValueError):
            shared_toon_flag = 1
        shared_toon_flag = 1 if shared_toon_flag else 0
        toon_index = self._get_attr_safe(material_name, ATTR_MMD_TOON_TEXTURE_INDEX, 0)
        try:
            toon_index = int(toon_index)
        except (TypeError, ValueError):
            toon_index = 0
        self.material_data["shared_toon_flag"] = shared_toon_flag
        self.material_data["toon_index"] = toon_index
        self.view.toon_texture_combo.setCurrentIndex(max(0, min(9, toon_index)))
        toon_sharing_check = getattr(self.view, "toon_sharing_check", None)
        toon_texture_path_edit = getattr(self.view, "toon_texture_path_edit", None)
        toon_texture_index_spin = getattr(self.view, "toon_texture_index_spin", None)
        if toon_sharing_check is not None:
            toon_sharing_check.setChecked(bool(shared_toon_flag))
        if toon_texture_path_edit is not None:
            toon_texture_path_edit.setText(
                "" if shared_toon_flag else self._get_attr_safe(material_name, ATTR_MMD_TOON_PATH, "")
            )
        if toon_texture_index_spin is not None:
            toon_texture_index_spin.setValue(toon_index if not shared_toon_flag else -1)
        self._set_toon_controls_enabled(bool(shared_toon_flag))

        # Draw flags
        draw_flags = self._get_attr_safe(material_name, ATTR_MMD_DRAW_FLAGS, 0x1F)
        self.material_data["draw_flags"] = draw_flags

        self.view.both_face_check.setChecked(bool(draw_flags & 0x01))
        self.view.ground_shadow_check.setChecked(bool(draw_flags & 0x02))
        self.view.self_shadow_map_check.setChecked(bool(draw_flags & 0x04))
        self.view.self_shadow_check.setChecked(bool(draw_flags & 0x08))
        self.view.edge_draw_check.setChecked(bool(draw_flags & 0x10))
        self.view.vertex_color_check.setChecked(bool(draw_flags & 0x20))
        self.view.point_draw_check.setChecked(bool(draw_flags & 0x40))
        self.view.line_draw_check.setChecked(bool(draw_flags & 0x80))

        # Edge properties
        edge_color = self._get_attr_safe(material_name, ATTR_MMD_EDGE_COLOR, (0.0, 0.0, 0.0, 1.0))
        edge_alpha = float(self._get_attr_safe(material_name, ATTR_MMD_EDGE_ALPHA, 1.0))
        # エッジカラーの形式を確認
        if isinstance(edge_color, (list, tuple)):
            if len(edge_color) == 4:
                # Legacy double4 scenes predate the separate alpha attribute.
                if not self.maya_adapter.attribute_exists(ATTR_MMD_EDGE_ALPHA, material_name):
                    edge_alpha = float(edge_color[3])
                edge_color = edge_color[:3]  # Remove alpha
            elif len(edge_color) < 3:
                edge_color = (0.0, 0.0, 0.0)
        else:
            edge_color = (0.0, 0.0, 0.0)

        self.material_data["edge_color"] = edge_color
        self._update_color_widget(self.view.edge_color_widget, edge_color)

        self.material_data["edge_alpha"] = edge_alpha
        raw_edge_size = float(self._get_attr_safe(material_name, ATTR_MMD_EDGE_SIZE, 1.0))
        visible_edge_size = max(0.0, min(2.0, raw_edge_size))
        self.material_data["edge_size"] = raw_edge_size
        self.material_data["edge_size_view"] = visible_edge_size
        self.view.edge_size_spin.setValue(visible_edge_size)

    def _get_attr_safe(self, node, attr, default):
        """Get attribute value safely, return default if not exists"""
        if self.maya_adapter.attribute_exists(attr, node):
            return maya_attribute_utils.get_attribute(node, attr)
        return default

    def _plug_is_unconnected(self, node, attr):
        if not self.maya_adapter.attribute_exists(attr, node):
            return False
        return not bool(
            self.maya_adapter.list_connections(
                f"{node}.{attr}", source=True, destination=False, plugs=True
            ) or []
        )

    def _load_base_value(self, node, authored_attr, fallback_attrs, default):
        """Read authored data first; never treat a driven final plug as base."""
        if self.maya_adapter.attribute_exists(authored_attr, node):
            return maya_attribute_utils.get_attribute(node, authored_attr), True
        for attr in fallback_attrs:
            if self._plug_is_unconnected(node, attr):
                return maya_attribute_utils.get_attribute(node, attr), True
        return default, False

    @staticmethod
    def _base_value_changed(current, loaded):
        if loaded is None:
            return current is not None
        if isinstance(current, (list, tuple)) and isinstance(loaded, (list, tuple)):
            if len(current) != len(loaded):
                return True
            return any(abs(float(a) - float(b)) > 1e-6 for a, b in zip(current, loaded))
        try:
            return abs(float(current) - float(loaded)) > 1e-6
        except (TypeError, ValueError):
            return current != loaded

    def _update_color_widget(self, widget, color):
        """Update color display widget"""
        # colorが正しい形式であることを確認
        if not color or len(color) < 3:
            # デフォルトの色（グレー）を使用
            r, g, b = 128, 128, 128
        else:
            r, g, b = int(color[0] * 255), int(color[1] * 255), int(color[2] * 255)
        widget.setStyleSheet(f"background-color: rgb({r}, {g}, {b}); border: 1px solid black;")

    def pick_color(self, color_type):
        """Open color picker dialog"""
        if not self.current_material:
            return

        # Get current color
        if color_type == "diffuse":
            current = self.material_data.get("diffuse", (0.5, 0.5, 0.5))
            widget = self.view.diffuse_color_widget
        elif color_type == "specular":
            current = self.material_data.get("specular", (0.5, 0.5, 0.5))
            widget = self.view.specular_color_widget
        elif color_type == "ambient":
            current = self.material_data.get("ambient", (0.5, 0.5, 0.5))
            widget = self.view.ambient_color_widget
        elif color_type == "edge":
            current = self.material_data.get("edge_color", (0.0, 0.0, 0.0))
            widget = self.view.edge_color_widget
        else:
            return

        # Open color dialog
        initial_color = QColor(int(current[0] * 255), int(current[1] * 255), int(current[2] * 255))
        color = QColorDialog.getColor(initial_color, self.view, f"Select {color_type.capitalize()} Color")

        if color.isValid():
            # Update display
            new_color = (
                color.red() / 255.0,
                color.green() / 255.0,
                color.blue() / 255.0,
            )
            self._update_color_widget(widget, new_color)
            # Store in temp data (not applied yet)
            self.material_data[color_type] = new_color
            self.has_unsaved_changes = True

    def browse_file(self, file_type):
        """Open file browser dialog"""
        if not self.current_material:
            return

        if file_type == "texture":
            caption = "Select Texture File"
            filter_str = "Image Files (*.png *.jpg *.jpeg *.bmp *.tga *.dds);;All Files (*.*)"
            line_edit = self.view.texture_path_edit
        elif file_type == "sphere":
            caption = "Select Sphere Map"
            filter_str = "Sphere Maps (*.spa *.sph *.png *.jpg *.bmp);;All Files (*.*)"
            line_edit = self.view.sphere_map_path_edit
        else:
            return

        # Get current directory
        current_path = line_edit.text()
        if current_path:
            import os

            start_dir = os.path.dirname(current_path)
        else:
            start_dir = self.maya_adapter.workspace(query=True, rootDirectory=True)

        # Open file dialog
        file_path, _ = QFileDialog.getOpenFileName(self.view, caption, start_dir, filter_str)

        if file_path:
            line_edit.setText(file_path)
            self.material_data[f"{file_type}_path"] = file_path
            self.has_unsaved_changes = True

    def apply_changes(self):
        """Apply material changes to Maya material"""
        if not self.current_material:
            return

        if self.authoring_coordinator is None:
            self.app_state.emit_status(self.tr_message("material_authoring_unavailable"))
            return None

        try:
            return self._apply_authoring_changes()
        except Exception as exc:
            logger.error("Failed to apply authoring material changes: %s", exc, exc_info=True)
            self.app_state.emit_status(tr_message_format("material_changes_failed", error=str(exc)))
            return None

    def _apply_authoring_changes(self):
        """Build and replace one complete semantic material specification.

        The coordinator owns the scene transaction and binding writes.  This
        presenter only translates the current controls into an immutable
        :class:`MmdMaterialSpec`, then performs a strict read-back.
        """
        root = self.app_state.current_model_root
        if not isinstance(root, str) or not root.strip():
            raise ValueError("authoring material apply requires an explicit model root")
        coordinator = self.authoring_coordinator
        read_spec = getattr(coordinator, "read_spec", None)
        replace_material = getattr(coordinator, "replace_material", None)
        index = self.current_material_index
        if type(index) is not int or index < 0:
            raise ValueError("an indexed material must be selected before Apply")
        read_material_value = getattr(coordinator, "read_material_value", None)
        narrow_patch = getattr(coordinator, "apply_material_value_patch", None)
        binding_patch = getattr(coordinator, "apply_material_binding_patch", None)
        if callable(read_material_value):
            if not callable(narrow_patch):
                raise TypeError("authoring coordinator lacks apply_material_value_patch")
            prior = read_material_value(root, index, self.current_material)
            if not isinstance(prior, MmdMaterialSpec):
                raise TypeError("selected-material reader returned an invalid material")
            replacement = self._material_from_authoring_controls(prior)
            route = classify_material_change(prior, replacement)
            if route in {"value", "noop"}:
                # A failed narrow transaction is surfaced to the user; it must
                # not silently fall back to the full binding transaction.
                result = prior if route == "noop" else narrow_patch(root, replacement)
                if not isinstance(result, MmdMaterialSpec):
                    raise TypeError("material value patch returned an invalid material")
                reloaded = result
                self.material_data["_authoring_material"] = reloaded.to_mapping()
            else:
                if not callable(binding_patch):
                    raise TypeError("authoring coordinator lacks apply_material_binding_patch")
                result = binding_patch(root, replacement)
                if not isinstance(result, MmdMaterialSpec):
                    raise TypeError("material binding patch returned an invalid material")
                reloaded = result
                self.material_data["_authoring_material"] = reloaded.to_mapping()
        else:
            if not callable(read_spec) or not callable(replace_material):
                raise TypeError("authoring coordinator must expose read_spec and replace_material")
            current = read_spec(root)
            if not isinstance(current, MmdModelAuthoringSpec):
                raise TypeError("authoring coordinator read_spec returned an invalid spec")
            prior = next(
                (material for material in current.materials if material.index == index),
                None,
            )
            if prior is None:
                raise ValueError(f"material index {index} is not present in the current spec")
            replacement = self._material_from_authoring_controls(prior)
            route = classify_material_change(prior, replacement)
            if route == "noop":
                reloaded = current
            else:
                result = replace_material(root, replacement)
                if not isinstance(result, MmdModelAuthoringSpec):
                    raise TypeError("material binding transaction returned an invalid spec")
                reloaded = read_spec(root)
                if not isinstance(reloaded, MmdModelAuthoringSpec):
                    raise TypeError("authoring coordinator strict reload returned an invalid spec")
            self.material_data["_authoring_fingerprint"] = reloaded.fingerprint()
        self.has_unsaved_changes = False
        self._update_selected_material_row(reloaded, replacement.binding_identity)
        self.app_state.emit_status(
            tr_message_format("material_changes_applied", material=self.current_material)
        )
        return reloaded

    def _update_selected_material_row(
        self,
        spec: MmdModelAuthoringSpec | MmdMaterialSpec,
        binding_identity: str | None,
    ) -> None:
        """Update only the selected material row after a successful Apply."""
        if not isinstance(binding_identity, str) or not binding_identity:
            return
        if isinstance(spec, MmdMaterialSpec):
            material = spec if spec.binding_identity == binding_identity else None
        else:
            material = next(
                (item for item in spec.materials if item.binding_identity == binding_identity),
                None,
            )
        if material is None:
            return
        material_list = self.view.material_list
        count = material_list.count()
        if type(count) is not int:
            return
        for row in range(count):
            item = material_list.item(row)
            if item is None or item.data(Qt.UserRole) != binding_identity:
                continue
            item.setData(MATERIAL_INDEX_ROLE, material.index)
            item.setText(
                format_indexed_node_label(
                    material.index + 1,
                    material.name,
                    binding_identity,
                    material.name_english,
                )
            )
            break

    def _material_from_authoring_controls(self, prior: MmdMaterialSpec) -> MmdMaterialSpec:
        """Return a complete replacement spec from the current Material tab."""
        diffuse_rgb = self._authoring_vector(
            self.material_data.get("diffuse", prior.diffuse[:3]), 3, "diffuse"
        )
        specular = self._authoring_vector(
            self.material_data.get("specular", prior.specular), 3, "specular"
        )
        ambient = self._authoring_vector(
            self.material_data.get("ambient", prior.ambient), 3, "ambient"
        )
        edge_rgb = self._authoring_vector(
            self.material_data.get("edge_color", prior.edge_color[:3]), 3, "edge_color"
        )
        transparency = self._authoring_number(
            self.view.transparency_spin.value(), "transparency"
        )
        specular_coefficient = self._authoring_number(
            self.view.specular_coefficient_spin.value(), "specular_coefficient"
        )
        edge_size = self._authoring_number(self.view.edge_size_spin.value(), "edge_size")
        diffuse_alpha = 1.0 - transparency

        texture_source, resolved_texture = self._authoring_main_texture_paths(prior)
        sphere_source, resolved_sphere = self._authoring_aux_texture_paths(
            prior.sphere_texture_path,
            prior.resolved_sphere_texture_path,
            self._authoring_text("sphere_map_path_edit"),
        )
        shared_toon = bool(self._authoring_toon_shared(prior))
        toon_index = self._authoring_toon_index(prior, shared_toon)
        if shared_toon:
            toon_source = None
            resolved_toon = None
        else:
            toon_source, resolved_toon = self._authoring_aux_texture_paths(
                prior.toon_texture_path,
                prior.resolved_toon_texture_path,
                self._authoring_text("toon_texture_path_edit"),
            )

        return replace(
            prior,
            name=self._authoring_text("material_jp_name_edit"),
            name_english=self._authoring_text("material_en_name_edit"),
            diffuse=(*diffuse_rgb, diffuse_alpha),
            specular=specular,
            specular_coefficient=specular_coefficient,
            ambient=ambient,
            draw_flags=self._authoring_draw_flags(prior.draw_flags),
            edge_color=(
                *edge_rgb,
                self._authoring_number(
                    self.material_data.get("edge_alpha", prior.edge_color[3]),
                    "edge_alpha",
                ),
            ),
            edge_size=edge_size,
            texture_path=texture_source,
            resolved_texture_path=resolved_texture,
            sphere_texture_path=sphere_source,
            resolved_sphere_texture_path=resolved_sphere,
            sphere_mode=self.view.sphere_mode_combo.currentIndex(),
            shared_toon=shared_toon,
            toon_texture_index=toon_index,
            toon_texture_path=toon_source,
            resolved_toon_texture_path=resolved_toon,
        )

    def _authoring_main_texture_paths(self, prior: MmdMaterialSpec) -> tuple[str | None, str | None]:
        """Translate the resolved main-texture editor without clobbering source provenance."""
        value = self._authoring_text("texture_path_edit", optional=True)
        source = prior.texture_path
        if value is None:
            return source, None
        original = self.material_data.get("original_pmx_texture_path")
        if value == source or value == original:
            # The field can display source provenance when no file graph is
            # connected.  Never reinterpret that source string as a resolved
            # Maya path, even when a stale resolved value is persisted.
            return source, prior.resolved_texture_path
        if prior.resolved_texture_path and value == prior.resolved_texture_path:
            return source, prior.resolved_texture_path
        # The editable field is the resolved Maya path when a file graph is
        # present.  Preserve source-relative PMX provenance separately.
        return source, value

    @staticmethod
    def _authoring_aux_texture_paths(
        source: str | None,
        resolved: str | None,
        value: str | None,
    ) -> tuple[str | None, str | None]:
        """Translate source-path controls and clear stale resolved paths on edits."""
        if value is None:
            return None, None
        if value == "" and source is None:
            return None, None
        if value == source:
            return source, resolved
        if resolved and value == resolved:
            return source, resolved
        return value, None

    def _authoring_toon_shared(self, prior: MmdMaterialSpec) -> bool:
        control = getattr(self.view, "toon_sharing_check", None)
        if control is None:
            return prior.shared_toon
        value = control.isChecked()
        if not isinstance(value, bool):
            raise TypeError("toon sharing control must return bool")
        return value

    def _authoring_toon_index(self, prior: MmdMaterialSpec, shared: bool) -> int | None:
        control = (
            getattr(self.view, "toon_texture_combo", None)
            if shared
            else getattr(self.view, "toon_texture_index_spin", None)
        )
        if control is None:
            return prior.toon_texture_index
        value = control.currentIndex() if shared else control.value()
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError("toon texture index control must return an integer")
        return value if value >= 0 else None

    def _authoring_draw_flags(self, prior: int) -> int:
        """Build PMX draw flags from the explicit checkbox controls."""
        flags = 0
        for bit, name in (
            (0x01, "both_face_check"),
            (0x02, "ground_shadow_check"),
            (0x04, "self_shadow_map_check"),
            (0x08, "self_shadow_check"),
            (0x10, "edge_draw_check"),
            (0x20, "vertex_color_check"),
            (0x40, "point_draw_check"),
            (0x80, "line_draw_check"),
        ):
            control = getattr(self.view, name, None)
            if control is None:
                enabled = bool(prior & bit)
            else:
                value = control.isChecked()
                if not isinstance(value, bool):
                    raise TypeError(f"{name} must return bool")
                enabled = value
            if enabled:
                flags |= bit
        return flags

    def _authoring_text(self, control_name: str, *, optional: bool = False) -> str | None:
        """Read one text control while preserving Unicode and empty-path semantics."""
        control = getattr(self.view, control_name, None)
        if control is None:
            if optional:
                return None
            raise AttributeError(f"missing authoring control: {control_name}")
        value = control.text()
        if not isinstance(value, str):
            raise TypeError(f"{control_name} must return text")
        if optional and value == "":
            return None
        return value

    @staticmethod
    def _authoring_vector(value, size: int, field: str) -> tuple:
        """Validate one UI vector shape without coercing mutable or boolean values."""
        if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
            raise TypeError(f"{field} must be a sequence")
        if len(value) != size:
            raise ValueError(f"{field} must contain exactly {size} values")
        return tuple(value)

    @staticmethod
    def _authoring_number(value, field: str) -> float:
        """Validate a finite numeric control without bool coercion."""
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError(f"{field} must be a number")
        numeric = float(value)
        if not math.isfinite(numeric):
            raise ValueError(f"{field} must be finite")
        return numeric

    def _set_toon_controls_enabled(self, shared_toon):
        """Enable the UI controls belonging to the selected PMX toon mode."""
        toon_texture_combo = getattr(self.view, "toon_texture_combo", None)
        toon_texture_path_edit = getattr(self.view, "toon_texture_path_edit", None)
        toon_texture_index_spin = getattr(self.view, "toon_texture_index_spin", None)
        if toon_texture_combo is not None:
            toon_texture_combo.setEnabled(bool(shared_toon))
        if toon_texture_path_edit is not None:
            toon_texture_path_edit.setEnabled(not shared_toon)
        if toon_texture_index_spin is not None:
            toon_texture_index_spin.setEnabled(not shared_toon)

    def _on_toon_sharing_changed(self, state):
        """Switch between built-in shared toon and custom texture-table controls."""
        self._set_toon_controls_enabled(bool(state))
        self._on_value_changed(state)

    def reset_changes(self):
        """Reset material properties to original values"""
        if not self.current_material:
            return

        # Reload original properties
        self.load_material_properties(self.current_material)
        self.has_unsaved_changes = False
        logger.info(f"Reset changes to material '{self.current_material}'")
        self.app_state.emit_status(tr_message_format("material_changes_reset", material=self.current_material))

    def _on_value_changed(self, value=None):
        """値が変更されたときの処理"""
        if self.current_material and not self._loading_properties:
            self.has_unsaved_changes = True

    def on_search_text_changed(self, text):
        """検索テキストが変更されたときの処理"""
        apply_list_filter(
            (self.view.material_list.item(i) for i in range(self.view.material_list.count())),
            text,
            self._material_filter_terms,
            always_hidden=lambda item: item.text().startswith("--"),
        )

    def _material_filter_terms(self, item):
        """Return searchable terms for a material list item."""
        material = item.data(Qt.UserRole)
        return (
            item.text(),
            material,
            maya_attribute_utils.get_attribute(material, ATTR_MMD_MATERIAL_NAME) if material else "",
            maya_attribute_utils.get_attribute(material, ATTR_MMD_MATERIAL_NAME_EN) if material else "",
        )

    def on_selection_changed_maya(self):
        """リスト選択が変更されたときにMayaでも選択する"""
        select_existing_user_role_nodes(
            self.view.material_list,
            self.maya_adapter,
            Qt.UserRole,
            exists=self.maya_adapter.object_exists,
            logger=logger,
            label="materials",
        )
