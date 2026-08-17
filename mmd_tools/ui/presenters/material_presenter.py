from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
import math
import os
from pathlib import Path, PureWindowsPath
from typing import Protocol

from ...adapters.maya_cmds_adapter import MayaCmdsAdapter
from ...core.logger import get_logger
from ...core.model_authoring_spec import MmdMaterialSpec, MmdModelAuthoringSpec
from ...core.material_read_projection import (
    MaterialDetailProjection,
    MaterialListProjection,
    MaterialTextureSlot,
)
from ...core.material_authoring import classify_material_change
from ..qt_compat import QColorDialog, QFileDialog, QColor, Qt
from ..translations import UITranslator
from .list_presenter_helpers import (
    apply_list_filter,
    format_indexed_node_label,
    reload_for_current_model_change,
    tr_message_format,
)

logger = get_logger(__name__)

MATERIAL_INDEX_ROLE = Qt.UserRole + 1
MATERIAL_ASSIGNMENT_ROLE = Qt.UserRole + 2


def _is_absolute_texture_path(value: str) -> bool:
    """Recognize native and Windows absolute paths on every host OS."""
    return Path(value).is_absolute() or PureWindowsPath(value).is_absolute()


class MaterialAuthoringCoordinator(Protocol):
    """Transactional semantic/binding boundary used by Material Tab CRUD."""

    def create_material(self, model_root: str) -> MmdMaterialSpec: ...

    def duplicate_material(self, model_root: str, source_index: int) -> MmdMaterialSpec: ...

    def delete_material(self, model_root: str, material_index: int) -> object: ...

    def reindex_materials(self, model_root: str, ordered_indices: Sequence[int]) -> object: ...

    def move_material(self, model_root: str, index: int, new_position: int) -> object: ...

    def move_material_fast(self, model_root: str, index: int, new_position: int) -> object: ...

    def read_spec(self, model_root: str) -> MmdModelAuthoringSpec: ...

    def read_material_list_projection(self, model_root: str) -> MaterialListProjection: ...

    def read_material_detail_projection(
        self, model_root: str, index: int, binding: str, assignment: object
    ) -> MaterialDetailProjection: ...

    def replace_material(self, model_root: str, material: MmdMaterialSpec) -> MmdModelAuthoringSpec: ...

    def apply_material_value_patch(
        self,
        model_root: str,
        material: MmdMaterialSpec,
        outline_enabled: bool | None = None,
    ) -> object: ...

    def apply_material_binding_patch(
        self,
        model_root: str,
        material: MmdMaterialSpec,
        outline_enabled: bool | None = None,
    ) -> object: ...


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
        self._pending_refresh_generation = None
        self._last_refresh_generation = None
        self._material_list_projection = None
        self.connect_signals()
        self._update_authoring_actions()

        # 既に選択されているモデルがある場合はロード
        if self.app_state.current_model_root:
            self.load_materials()

    def connect_signals(self):
        # ApplicationStateのシグナル
        self.app_state.current_model_changed.connect(self.on_current_model_changed)
        refresh_signal = getattr(self.app_state, "model_refresh_completed", None)
        if refresh_signal is not None and hasattr(refresh_signal, "connect"):
            refresh_signal.connect(self.on_model_refresh)

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
            self.view.shader_outline_check,
        ]:
            checkbox.stateChanged.connect(self._on_value_changed)

        # Apply/Reset buttons
        self.view.apply_btn.clicked.connect(self.apply_changes)
        self.view.reset_btn.clicked.connect(self.reset_changes)


    def on_current_model_changed(self, model_root):
        """現在のモデルが変更されたときの処理"""
        if getattr(self.app_state, "refreshing", False) is True:
            self.on_model_refresh(getattr(self.app_state, "refresh_generation", 0))
            return
        self._pending_refresh_generation = None
        reload_for_current_model_change(logger, "MaterialPresenter", model_root, self.load_materials)
        self._update_authoring_actions()

    def on_model_refresh(self, generation):
        """Invalidate list data without discarding an unsaved material copy."""
        self._pending_refresh_generation = generation

    def refresh_for_generation(self, generation):
        """Reload a visible tab once per generation when it is clean."""
        if self._pending_refresh_generation != generation:
            if self._last_refresh_generation == generation:
                return True
            self.load_materials()
            self._last_refresh_generation = generation
            return True
        if self.has_unsaved_changes:
            self._last_refresh_generation = generation
            return True
        self.load_materials()
        self._pending_refresh_generation = None
        self._last_refresh_generation = generation
        return True

    def tr_message(self, key: str) -> str:
        """Translate a material presenter message key."""
        return UITranslator.instance().translate(key, "messages")

    def load_materials(self):
        if self._pending_refresh_generation is not None and self.has_unsaved_changes:
            return
        self._last_refresh_generation = getattr(self.app_state, "refresh_generation", 0)
        self._pending_refresh_generation = None
        current_model_root = self.app_state.current_model_root
        if not current_model_root or not self.maya_adapter.object_exists(current_model_root):
            self._material_list_projection = None
            self.view.material_list.clear()
            self.current_material = None
            self.current_material_index = None
            self.view._set_details_enabled(False)
            self.view._show_placeholder()
            self._update_authoring_actions()
            return

        try:
            if self.authoring_coordinator is None:
                raise RuntimeError("material list projection reader is unavailable")
            projection = self.authoring_coordinator.read_material_list_projection(
                current_model_root
            )
            if not isinstance(projection, MaterialListProjection):
                raise TypeError("material list projection reader returned an invalid result")
            if projection.root_identity != current_model_root:
                raise ValueError(
                    "material list projection root does not match current model root"
                )

            # Construct every row before replacing the visible generation.
            from ..qt_compat import QListWidgetItem

            projected_items = []
            for projected in projection.items:
                semantic = projected.semantic
                display_text = format_indexed_node_label(
                    semantic.index + 1,
                    semantic.name,
                    semantic.binding_identity,
                    semantic.name_english,
                )

                item = QListWidgetItem(display_text)
                item.setData(Qt.UserRole, semantic.binding_identity)
                item.setData(MATERIAL_INDEX_ROLE, semantic.index)
                item.setData(MATERIAL_ASSIGNMENT_ROLE, projected.assignment.label)
                projected_items.append(item)

            # Swap only after the complete immutable generation and all Qt
            # rows were built successfully.
            self.current_material = None
            self.current_material_index = None
            self._material_list_projection = projection
            was_blocked = self.view.material_list.blockSignals(True)
            try:
                self.view.material_list.clear()
                for item in projected_items:
                    self.view.material_list.addItem(item)
            finally:
                self.view.material_list.blockSignals(was_blocked)

            # Show placeholder if no materials
            if self.view.material_list.count() == 0:
                self.view._show_placeholder()

            logger.debug(f"Loaded {self.view.material_list.count()} MMD materials for model: {current_model_root}")
            self._update_authoring_actions()

        except Exception as e:
            logger.error(f"Failed to load materials: {e}", exc_info=True)
            self._material_list_projection = None
            self.view.material_list.clear()
            self.current_material = None
            self.current_material_index = None
            self.view._set_details_enabled(False)
            self.view._show_placeholder()
            self._update_authoring_actions()
            self.app_state.emit_status(tr_message_format("materials_load_failed", error=str(e)))

    def on_material_selected(self, current, previous):
        if not current:
            self._clear_material_selection()
            return

        # プレースホルダーアイテムの場合は何もしない
        if current.text().startswith("--"):
            self._clear_material_selection()
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

        material_name = current.data(Qt.UserRole)
        material_index = current.data(MATERIAL_INDEX_ROLE)
        if (
            not isinstance(material_name, str)
            or not material_name
            or type(material_index) is not int
            or material_index < 0
        ):
            logger.error("Material list row has invalid hidden routing roles")
            self._clear_material_selection()
            return
        projection = self._material_list_projection
        try:
            projected = (
                projection.item_for_index(material_index)
                if isinstance(projection, MaterialListProjection)
                else None
            )
        except KeyError:
            projected = None
        if projected is None or projected.binding_identity != material_name:
            logger.error("Material list row does not match the current projection")
            self._clear_material_selection()
            return

        logger.debug(f"Selected material: {material_name}")

        self.current_material = material_name
        self.current_material_index = material_index
        # 変更フラグを事前にリセットして、ロード中の変更検知を無効化
        self.has_unsaved_changes = False
        self.view._set_details_enabled(True)
        self._update_authoring_actions()

        # Mayaでマテリアルを選択
        try:
            # Material-list selection is deliberately shader-only.  Maya's
            # standard set membership remains the assignment authority; do
            # not open HyperShade or implicitly assign the selected shader.
            self._select_material_nodes(material_name, replace=True)
            logger.debug(f"Selected material in Maya: {material_name}")
        except Exception as e:
            logger.warning(f"Could not select material in Maya: {e}")

        self.load_material_properties(material_name)

    def _clear_material_selection(self):
        """Clear routing authority and disable selection-dependent actions."""

        self.current_material = None
        self.current_material_index = None
        self.view._set_details_enabled(False)
        self._update_authoring_actions()

    def _select_material_nodes(self, nodes, *, replace=True):
        """Select material nodes without creating an undo entry when possible."""
        select_fast = getattr(self.maya_adapter, "select_fast", None)
        if callable(select_fast):
            return select_fast(nodes, replace=replace)
        return self.maya_adapter.select(nodes, replace=replace)

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
        """Run create/duplicate then refresh and select its projected row."""
        root = self._authoring_root()
        if root is None:
            return False
        try:
            result = getattr(self.authoring_coordinator, operation)(root, *args)
            if not isinstance(result, MmdMaterialSpec):
                raise TypeError("material creation returned an invalid material")
            binding = result.binding_identity
            if not isinstance(binding, str) or not binding:
                raise TypeError("created material has no Maya binding identity")
            self.load_materials()
            self._select_projected_binding(binding)
        except Exception as exc:
            logger.error("Material authoring %s failed", operation, exc_info=True)
            self.app_state.emit_status(
                tr_message_format("material_authoring_failed", operation=operation, error=str(exc))
            )
            return False
        self.app_state.emit_status(self.tr_message(f"material_{operation}_succeeded"))
        return True

    def _select_projected_binding(self, binding: str) -> bool:
        """Select a canonical binding only when the refreshed projection owns it."""

        projection = self._material_list_projection
        if not isinstance(projection, MaterialListProjection):
            return False
        try:
            projected = projection.item_for_binding(binding)
        except KeyError:
            return False
        for row in range(self.view.material_list.count()):
            item = self.view.material_list.item(row)
            if (
                item is not None
                and item.data(Qt.UserRole) == binding
                and item.data(MATERIAL_INDEX_ROLE) == projected.index
            ):
                self.current_material = binding
                self.current_material_index = projected.index
                self.view.material_list.setCurrentItem(item)
                self._update_authoring_actions()
                return True
        return False

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
        self.current_material_index = target_index
        self.load_materials()
        self._select_projected_binding(selected_binding)
        self._update_authoring_actions()
        self.app_state.emit_status(self.tr_message("material_reindex_materials_succeeded"))
        return True

    def load_material_properties(self, material_name):
        """Read and render one immutable selected-material detail generation."""
        self._loading_properties = True
        try:
            root = self.app_state.current_model_root
            index = self.current_material_index
            projection = self._material_list_projection
            if (
                not isinstance(root, str)
                or not root
                or type(index) is not int
                or not isinstance(projection, MaterialListProjection)
                or projection.root_identity != root
            ):
                raise RuntimeError("selected material detail routing is unavailable")
            projected = projection.item_for_index(index)
            if projected.binding_identity != material_name:
                raise RuntimeError("selected material detail binding is stale")
            coordinator = self.authoring_coordinator
            if coordinator is None:
                raise RuntimeError("material detail projection reader is unavailable")
            detail = coordinator.read_material_detail_projection(
                root,
                index,
                material_name,
                projected.assignment,
            )
            if not isinstance(detail, MaterialDetailProjection):
                raise TypeError("material detail projection reader returned an invalid result")
            self._render_material_detail(detail)
        except Exception as exc:
            logger.error(
                f"Failed to load material details for {material_name}: {exc}",
                exc_info=True,
            )
            self.material_data = {}
            self._clear_material_selection()
        finally:
            self._loading_properties = False
            self.has_unsaved_changes = False

    def _render_material_detail(self, detail):
        """Render authored semantics separately from effective preview state."""
        material = detail.material
        self.material_data = {
            "jp_name": material.name,
            "en_name": material.name_english,
            "diffuse": material.diffuse[:3],
            "specular": material.specular,
            "ambient": material.ambient,
            "transparency": 1.0 - material.diffuse[3],
            "specular_coefficient": material.specular_coefficient,
            "draw_flags": material.draw_flags,
            "edge_color": material.edge_color[:3],
            "edge_alpha": material.edge_color[3],
            "edge_size": material.edge_size,
            "edge_size_view": max(0.0, min(2.0, material.edge_size)),
            "shader_outline_enabled": detail.preview.outline_enabled,
            "shader_type": detail.preview.shader_type,
            "_authoring_material": material.to_mapping(),
        }
        self.view.material_jp_name_edit.setText(material.name)
        self.view.material_en_name_edit.setText(material.name_english)
        self._update_color_widget(self.view.diffuse_color_widget, material.diffuse)
        self._update_color_widget(self.view.specular_color_widget, material.specular)
        self._update_color_widget(self.view.ambient_color_widget, material.ambient)
        self.view.transparency_spin.setValue(self.material_data["transparency"])
        self.view.specular_coefficient_spin.setValue(
            max(0.0, min(1.0, material.specular_coefficient))
        )

        texture_by_slot = {texture.slot: texture for texture in detail.textures}
        main = texture_by_slot.get(MaterialTextureSlot.MAIN)
        main_source = main.source_path if main is not None else material.texture_path
        main_resolved = (
            main.resolved_path if main is not None else material.resolved_texture_path
        )
        main_effective = main_resolved or main_source or ""
        self.material_data["texture"] = main_effective
        self.material_data["original_pmx_texture_path"] = main_source or ""
        self.view.texture_path_edit.setText(main_effective)
        self._set_texture_provenance_fields(main_source or "")

        sphere = texture_by_slot.get(MaterialTextureSlot.SPHERE)
        sphere_source = (
            sphere.source_path if sphere is not None else material.sphere_texture_path
        )
        sphere_resolved = (
            sphere.resolved_path
            if sphere is not None
            else material.resolved_sphere_texture_path
        )
        sphere_effective = sphere_resolved or sphere_source or ""
        self.material_data["sphere_map"] = sphere_effective
        self.view.sphere_map_path_edit.setText(sphere_effective)
        self.material_data["sphere_mode"] = material.sphere_mode
        self.view.sphere_mode_combo.setCurrentIndex(material.sphere_mode)

        shared_toon_flag = int(material.shared_toon)
        toon_index = (
            material.toon_texture_index
            if material.toon_texture_index is not None
            else -1
        )
        toon = texture_by_slot.get(MaterialTextureSlot.TOON)
        toon_source = toon.source_path if toon is not None else material.toon_texture_path
        toon_resolved = (
            toon.resolved_path if toon is not None else material.resolved_toon_texture_path
        )
        toon_effective = toon_resolved or toon_source or ""
        self.material_data["shared_toon_flag"] = shared_toon_flag
        self.material_data["toon_index"] = toon_index
        self.view.toon_texture_combo.setCurrentIndex(
            max(0, min(9, toon_index if toon_index >= 0 else 0))
        )
        toon_sharing_check = getattr(self.view, "toon_sharing_check", None)
        if toon_sharing_check is not None:
            toon_sharing_check.setChecked(material.shared_toon)
        toon_texture_path_edit = getattr(self.view, "toon_texture_path_edit", None)
        if toon_texture_path_edit is not None:
            toon_texture_path_edit.setText("" if material.shared_toon else toon_effective)
        toon_texture_index_spin = getattr(self.view, "toon_texture_index_spin", None)
        if toon_texture_index_spin is not None:
            toon_texture_index_spin.setValue(
                -1 if material.shared_toon else toon_index
            )
        self._set_toon_controls_enabled(material.shared_toon)

        for control, mask in (
            (self.view.both_face_check, 0x01),
            (self.view.ground_shadow_check, 0x02),
            (self.view.self_shadow_map_check, 0x04),
            (self.view.self_shadow_check, 0x08),
            (self.view.edge_draw_check, 0x10),
            (self.view.vertex_color_check, 0x20),
            (self.view.point_draw_check, 0x40),
            (self.view.line_draw_check, 0x80),
        ):
            control.setChecked(bool(material.draw_flags & mask))
        self.view.shader_outline_check.setChecked(detail.preview.outline_enabled)
        self._update_color_widget(self.view.edge_color_widget, material.edge_color)
        self.view.edge_size_spin.setValue(self.material_data["edge_size_view"])

    def _set_texture_provenance_fields(self, original_path):
        """Update read-only texture provenance fields when the view provides them."""
        if hasattr(self.view, "original_pmx_path_edit"):
            self.view.original_pmx_path_edit.setText(original_path or "")

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
            storage_key = "edge_color" if color_type == "edge" else color_type
            self.material_data[storage_key] = new_color
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
            self._validate_changed_authoring_texture_files(prior, replacement)
            route = classify_material_change(prior, replacement)
            outline_intent = self._viewport_outline_intent(prior, replacement)
            if route in {"value", "noop"}:
                # A failed narrow transaction is surfaced to the user; it must
                # not silently fall back to the full binding transaction.
                result = (
                    prior
                    if route == "noop" and outline_intent is None
                    else (
                        narrow_patch(root, replacement)
                        if outline_intent is None
                        else narrow_patch(root, replacement, outline_enabled=outline_intent)
                    )
                )
                if not isinstance(result, MmdMaterialSpec):
                    raise TypeError("material value patch returned an invalid material")
                reloaded = result
                self.material_data["_authoring_material"] = reloaded.to_mapping()
            else:
                if not callable(binding_patch):
                    raise TypeError("authoring coordinator lacks apply_material_binding_patch")
                result = (
                    binding_patch(root, replacement)
                    if outline_intent is None
                    else binding_patch(root, replacement, outline_enabled=outline_intent)
                )
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
            self._validate_changed_authoring_texture_files(prior, replacement)
            route = classify_material_change(prior, replacement)
            outline_intent = self._viewport_outline_intent(prior, replacement)
            if outline_intent is not None:
                raise TypeError("viewport outline edits require the narrow material coordinator")
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
        if outline_intent is not None:
            self.material_data["shader_outline_enabled"] = outline_intent
        self.has_unsaved_changes = False
        self._update_selected_material_row(reloaded, replacement.binding_identity)
        self.app_state.emit_status(
            tr_message_format("material_changes_applied", material=self.current_material)
        )
        return reloaded

    def _viewport_outline_intent(
        self,
        prior: MmdMaterialSpec,
        material: MmdMaterialSpec,
    ) -> bool | None:
        """Return an explicit DX11 preview edit, or ``None`` for no Maya write."""
        enabled = bool(self.view.shader_outline_check.isChecked())
        previous_enabled = bool(self.material_data.get("shader_outline_enabled", False))
        edge_size_changed = abs(float(prior.edge_size) - float(material.edge_size)) > 1e-6
        if enabled == previous_enabled and (not enabled or not edge_size_changed):
            return None
        if self.material_data.get("shader_type") != "dx11Shader":
            return None
        return enabled

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
        if _is_absolute_texture_path(value):
            return source or value, value
        # The editable field is the resolved Maya path when a file graph is
        # present.  Preserve source-relative PMX provenance separately.
        return source, value

    def _validate_changed_authoring_texture_files(
        self,
        prior: MmdMaterialSpec,
        replacement: MmdMaterialSpec,
    ) -> None:
        """Reject newly entered texture paths that do not resolve to files."""
        for label, previous, current in (
            ("main", prior.resolved_texture_path, replacement.resolved_texture_path),
            ("sphere", prior.resolved_sphere_texture_path, replacement.resolved_sphere_texture_path),
            ("toon", prior.resolved_toon_texture_path, replacement.resolved_toon_texture_path),
        ):
            if not current or current == previous:
                continue
            expanded = current
            workspace = getattr(self.maya_adapter, "workspace", None)
            if callable(workspace):
                candidate = workspace(expandName=current)
                if not isinstance(candidate, str) or not candidate:
                    continue
                expanded = candidate
            if not os.path.isfile(expanded):
                raise ValueError(f"{label} texture file does not exist: {current}")

    @staticmethod
    def _authoring_aux_texture_paths(
        source: str | None,
        resolved: str | None,
        value: str | None,
    ) -> tuple[str | None, str | None]:
        """Translate source-path controls and clear stale resolved paths on edits."""
        if value is None:
            return None, None
        if value == "":
            return None, None
        if value == source:
            return source, resolved
        if resolved and value == resolved:
            return source, resolved
        if _is_absolute_texture_path(value):
            return value, value
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
        """Search only the already-projected human-facing row text."""
        return (item.text(),)

    def on_selection_changed_maya(self):
        """リスト選択が変更されたときにMayaでも選択する"""
        selected_items = self.view.material_list.selectedItems()
        if not selected_items:
            return

        nodes = []
        for item in selected_items:
            node = item.data(Qt.UserRole)
            if node and self.maya_adapter.object_exists(node):
                nodes.append(node)
        if not nodes:
            return

        try:
            self._select_material_nodes(nodes, replace=True)
            logger.debug("Selected materials in Maya: %s", nodes)
        except Exception as exc:
            logger.warning("Could not select materials in Maya: %s", exc)
