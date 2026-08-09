from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
import math
from typing import Protocol

from mmd_tools.core import maya_attribute_utils
from mmd_tools.core import maya_material_utils
from mmd_tools.converters.material_shader_parameters import (
    ATTR_MMD_EDGE_ALPHA,
    ATTR_MMD_DIFFUSE_ALPHA,
    hardware_morph_route_for_uniform,
    iter_hardware_shader_values,
)
from mmd_tools.converters.mesh_converter import ensure_material_shader_backend
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
    ATTR_MMD_SHADER_OUTLINE_ENABLED,
    ATTR_MMD_SPHERE_MODE,
    ATTR_MMD_SPHERE_PATH,
    ATTR_MMD_SHARED_TOON_FLAG,
    ATTR_MMD_TOON_PATH,
    ATTR_MMD_TOON_TEXTURE_INDEX,
)
from mmd_tools.actions import (
    apply_sphere_map,
)
from ...adapters.maya_cmds_adapter import MayaCmdsAdapter
from ...core.logger import get_logger
from ...core.model_registry import (
    REGISTRY_CATEGORY_MATERIAL,
    list_model_registry_members_from_adapter,
)
from ...core.model_authoring_spec import MmdMaterialSpec, MmdModelAuthoringSpec
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


class MaterialAuthoringCoordinator(Protocol):
    """Transactional semantic/binding boundary used by Material Tab CRUD."""

    def create_material(self, model_root: str, targets: Sequence[str]) -> object: ...

    def duplicate_material(self, model_root: str, source_index: int, targets: Sequence[str]) -> object: ...

    def delete_material(self, model_root: str, material_index: int) -> object: ...

    def assign_material(self, model_root: str, material_index: int, targets: Sequence[str]) -> object: ...

    def read_spec(self, model_root: str) -> MmdModelAuthoringSpec: ...

    def replace_material(self, model_root: str, material: MmdMaterialSpec) -> MmdModelAuthoringSpec: ...


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
            ("assign_btn", self.assign_material),
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

            # Add materials in semantic index order when canonical indices are available.
            for display_index, mat, material_index in sorted(indexed_materials):
                # 日本語名と英語名を取得
                jp_name = maya_attribute_utils.get_attribute(mat, ATTR_MMD_MATERIAL_NAME)
                en_name = maya_attribute_utils.get_attribute(mat, ATTR_MMD_MATERIAL_NAME_EN)

                display_text = format_indexed_node_label(display_index + 1, jp_name, mat, en_name)

                # リストに追加
                from ..qt_compat import QListWidgetItem

                item = QListWidgetItem(display_text)
                item.setData(Qt.UserRole, mat)  # 実際のマテリアル名を保存
                item.setData(MATERIAL_INDEX_ROLE, material_index)
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

        # Mayaでマテリアルを選択
        try:
            if self.authoring_coordinator is not None:
                # Preserve the current mesh/face selection for Assign.
                self._update_authoring_actions()
                self.load_material_properties(material_name)
                return
            self.maya_adapter.select(material_name, replace=True)
            logger.debug(f"Selected material in Maya: {material_name}")

            # Hypershadeでマテリアルを表示（オプション）
            if self.maya_adapter.window("hyperShadePanel1Window", exists=True):
                self.maya_adapter.hyper_shade(material_name, assign=material_name)
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

    def _update_authoring_actions(self):
        root = self.app_state.current_model_root
        has_root = bool(root and self.maya_adapter.object_exists(root))
        available = self.authoring_coordinator is not None and has_root
        selected = available and type(self.current_material_index) is int
        for button_name, enabled in (
            ("create_btn", available),
            ("duplicate_btn", selected),
            ("delete_btn", selected),
            ("assign_btn", selected),
        ):
            button = getattr(self.view, button_name, None)
            if button is not None:
                button.setEnabled(bool(enabled))

    def _selection_targets(self):
        try:
            return tuple(self.maya_adapter.ls(selection=True, long=True, flatten=True) or ())
        except Exception:
            return ()

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

    def create_material(self):
        """Request one transactional semantic material creation."""
        return self._run_authoring("create_material", self._selection_targets())

    def duplicate_material(self):
        """Request duplication of the selected semantic material."""
        if type(self.current_material_index) is not int:
            return False
        return self._run_authoring(
            "duplicate_material", self.current_material_index, self._selection_targets()
        )

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

    def assign_material(self):
        """Request assignment; root ownership validation remains in the binding."""
        if type(self.current_material_index) is not int:
            return False
        targets = self._selection_targets()
        if not targets:
            self.app_state.emit_status(self.tr_message("material_authoring_selection_missing"))
            return False
        return self._run_authoring("assign_material", self.current_material_index, targets)

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

            if shader_type in ("dx11Shader", "GLSLShader"):
                diffuse_color, diffuse_owned = self._load_base_value(
                    material_name,
                    ATTR_MMD_DIFFUSE_COLOR,
                    ("DiffuseColorRGB", "g_Diffuse"),
                    (0.5, 0.5, 0.5),
                )
            else:
                actual_attr = "baseColor" if shader_type == "standardSurface" else "color"
                diffuse_color = self._get_attr_safe(material_name, actual_attr, (0.5, 0.5, 0.5))
                diffuse_owned = True
            self.material_data["_diffuse_base_owned"] = diffuse_owned
            self.material_data["diffuse"] = diffuse_color
            self._update_color_widget(self.view.diffuse_color_widget, diffuse_color)

            # Get specular color
            if shader_type in ("dx11Shader", "GLSLShader"):
                specular_color, specular_owned = self._load_base_value(
                    material_name, ATTR_MMD_SPECULAR_COLOR, ("SpecularColor",), (0.5, 0.5, 0.5)
                )
            else:
                specular_color = self._get_attr_safe(
                    material_name, "specularColor", (0.5, 0.5, 0.5)
                )
                specular_owned = True
            self.material_data["_specular_base_owned"] = specular_owned

            # タプルが正しい形式であることを確認
            if not isinstance(specular_color, (list, tuple)) or len(specular_color) < 3:
                specular_color = (0.5, 0.5, 0.5)

            self.material_data["specular"] = specular_color
            self._update_color_widget(self.view.specular_color_widget, specular_color)

            # Get ambient - Maya doesn't have ambient by default, check if attr exists
            if shader_type in ("dx11Shader", "GLSLShader"):
                ambient_color, ambient_owned = self._load_base_value(
                    material_name, ATTR_MMD_AMBIENT_COLOR, ("AmbientColor",), (0.5, 0.5, 0.5)
                )
            else:
                ambient_color = self._get_attr_safe(
                    material_name, "ambientColor", (0.5, 0.5, 0.5)
                )
                ambient_owned = True
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

        # An injected coordinator is the semantic authoring boundary.  Keep
        # this branch before every legacy Maya write so a failed spec build or
        # transaction cannot partially mutate individual shader attributes.
        if self.authoring_coordinator is not None:
            try:
                return self._apply_authoring_changes()
            except Exception as e:
                logger.error(f"Failed to apply authoring material changes: {e}", exc_info=True)
                self.app_state.emit_status(tr_message_format("material_changes_failed", error=str(e)))
                return None

        try:
            self.current_material = ensure_material_shader_backend(self.current_material)
            # Apply names
            jp_name = self.view.material_jp_name_edit.text()
            en_name = self.view.material_en_name_edit.text()

            # Ensure MMD attributes exist
            maya_attribute_utils.set_custom_attributes(
                self.current_material,
                {ATTR_MMD_MATERIAL_NAME: jp_name, ATTR_MMD_MATERIAL_NAME_EN: en_name},
            )

            shader_type = self.maya_adapter.node_type(self.current_material)
            snapshot = self.material_data.get("_loaded_base_snapshot", {})
            diffuse_changed = self._base_value_changed(
                self.material_data.get("diffuse"), snapshot.get("diffuse")
            )
            specular_changed = self._base_value_changed(
                self.material_data.get("specular"), snapshot.get("specular")
            )
            ambient_changed = self._base_value_changed(
                self.material_data.get("ambient"), snapshot.get("ambient")
            )
            transparency = self.view.transparency_spin.value()
            alpha_changed = self._base_value_changed(
                transparency, snapshot.get("transparency")
            )
            specular_coefficient = self.view.specular_coefficient_spin.value()
            specular_power_changed = self._base_value_changed(
                specular_coefficient, snapshot.get("specular_coefficient")
            )
            base_attrs = {}
            if "diffuse" in self.material_data and (
                self.material_data.get("_diffuse_base_owned", True) or diffuse_changed
            ):
                base_attrs["diffuse_color"] = self.material_data["diffuse"][:3]
            if "specular" in self.material_data and (
                self.material_data.get("_specular_base_owned", True) or specular_changed
            ):
                base_attrs["specular_color"] = self.material_data["specular"][:3]
            if "ambient" in self.material_data and (
                self.material_data.get("_ambient_base_owned", True) or ambient_changed
            ):
                base_attrs["ambient_color"] = self.material_data["ambient"][:3]
            if self.material_data.get("_diffuse_alpha_base_owned", True) or alpha_changed:
                base_attrs[ATTR_MMD_DIFFUSE_ALPHA] = 1.0 - transparency
            if base_attrs:
                maya_attribute_utils.set_custom_attributes(self.current_material, base_attrs)

            # Apply basic colors
            if "diffuse" in self.material_data:
                if shader_type == "standardSurface":
                    maya_attribute_utils.set_attribute(
                        self.current_material,
                        "baseColor",
                        self.material_data["diffuse"],
                        "double3",
                    )
                elif self.maya_adapter.attribute_exists("color", self.current_material):
                    maya_attribute_utils.set_attribute(
                        self.current_material,
                        "color",
                        self.material_data["diffuse"],
                        "double3",
                    )

            if "specular" in self.material_data and self.maya_adapter.attribute_exists(
                "specularColor", self.current_material
            ):
                maya_attribute_utils.set_attribute(
                    self.current_material,
                    "specularColor",
                    self.material_data["specular"],
                    "double3",
                )

            # Apply transparency
            # StandardSurfaceの場合はopacityに変換
            if self.maya_adapter.node_type(self.current_material) == "standardSurface":
                opacity = 1.0 - transparency
                maya_attribute_utils.set_attribute(
                    self.current_material,
                    "opacity",
                    [opacity, opacity, opacity],
                    "double3",
                )
            elif self.maya_adapter.attribute_exists("transparency", self.current_material):
                # その他のシェーダーの場合
                maya_attribute_utils.set_attribute(
                    self.current_material,
                    "transparency",
                    [transparency, transparency, transparency],
                    "double3",
                )

            # Apply specular coefficient
            # MMD係数として保存
            if self.material_data.get("_specular_power_base_owned", True) or specular_power_changed:
                authored_specular_coefficient = (
                    specular_coefficient
                    if specular_power_changed
                    else self.material_data.get(
                        "_authored_specular_coefficient", specular_coefficient
                    )
                )
                maya_attribute_utils.set_custom_attributes(
                    self.current_material,
                    {
                        "mmd_specular_coefficient": authored_specular_coefficient,
                        ATTR_MMD_SHININESS: authored_specular_coefficient,
                    },
                )

            if shader_type in ("dx11Shader", "GLSLShader"):
                opacity = 1.0 - transparency
                values = {
                    "diffuse_rgb": self.material_data.get("diffuse"),
                    "diffuse_alpha": opacity,
                    "opacity": opacity,
                    "ambient": self.material_data.get("ambient"),
                    "specular": self.material_data.get("specular"),
                    "specular_power": (
                        specular_coefficient
                        if specular_power_changed
                        else self.material_data.get(
                            "_authored_specular_coefficient", specular_coefficient
                        )
                    ),
                    "edge_size": self.view.edge_size_spin.value(),
                    "sphere_mode": self.view.sphere_mode_combo.currentIndex(),
                }
                edge = self.material_data.get("edge_color")
                if edge is not None:
                    values["edge_color"] = tuple(edge[:3]) + (
                        float(self.material_data.get("edge_alpha", 1.0)),
                    )
                self._apply_hardware_base_values(values, shader_type)

            # StandardSurfaceの場合はspecularに変換
            if shader_type == "standardSurface" and specular_power_changed:
                specular_weight = specular_coefficient
                maya_attribute_utils.set_attribute(self.current_material, "specular", specular_weight, "float")
                self.material_data["_standard_specular_weight"] = specular_weight

            # Apply textures
            texture_path = self.view.texture_path_edit.text()
            if texture_path and texture_path != self.material_data.get("texture", ""):
                self._apply_texture(self.current_material, texture_path)

            # Apply MMD-specific attributes
            self._apply_mmd_attributes()

            # Apply sphere map if specified
            sphere_path = self.view.sphere_map_path_edit.text()
            sphere_mode = self.view.sphere_mode_combo.currentIndex()
            if sphere_path and sphere_mode > 0:  # 0は「無効」
                try:
                    applied_sphere = apply_sphere_map(
                        self.current_material,
                        sphere_path,
                        sphere_mode,
                        maya_adapter=self.maya_adapter,
                    )
                    if not applied_sphere:
                        self.app_state.emit_status(
                            tr_message_format(
                                "sphere_map_apply_failed",
                                error=f"{sphere_path} (mode={sphere_mode})",
                            )
                        )
                except Exception as e:
                    logger.error(f"Failed to apply sphere map: {e}", exc_info=True)
                    self.app_state.emit_status(tr_message_format("sphere_map_apply_failed", error=str(e)))

            # リストビューの表示を更新
            for i in range(self.view.material_list.count()):
                item = self.view.material_list.item(i)
                if item.data(Qt.UserRole) == self.current_material:
                    # 現在のインデックスを取得
                    idx = i + 1  # 1ベースのインデックス

                    item.setText(
                        format_indexed_node_label(
                            idx,
                            jp_name,
                            self.current_material,
                            en_name,
                        )
                    )
                    item.setToolTip(self.current_material)
                    break

            if diffuse_changed:
                self.material_data["_diffuse_base_owned"] = True
            if specular_changed:
                self.material_data["_specular_base_owned"] = True
            if ambient_changed:
                self.material_data["_ambient_base_owned"] = True
            if alpha_changed:
                self.material_data["_diffuse_alpha_base_owned"] = True
            if specular_power_changed:
                self.material_data["_specular_power_base_owned"] = True
            self.material_data["transparency"] = transparency
            self.material_data["specular_coefficient"] = specular_coefficient
            self.material_data["_loaded_base_snapshot"] = {
                "diffuse": self.material_data.get("diffuse"),
                "specular": self.material_data.get("specular"),
                "ambient": self.material_data.get("ambient"),
                "transparency": transparency,
                "specular_coefficient": specular_coefficient,
            }
            self.has_unsaved_changes = False
            logger.info(f"Applied changes to material '{self.current_material}'")
            self.app_state.emit_status(tr_message_format("material_changes_applied", material=self.current_material))

        except Exception as e:
            logger.error(f"Failed to apply material changes: {e}", exc_info=True)
            self.app_state.emit_status(tr_message_format("material_changes_failed", error=str(e)))

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
        if not callable(read_spec) or not callable(replace_material):
            raise TypeError("authoring coordinator must expose read_spec and replace_material")

        current = read_spec(root)
        if type(current) is not MmdModelAuthoringSpec:
            raise TypeError("authoring coordinator read_spec returned an invalid spec")
        index = self.current_material_index
        if type(index) is not int or index < 0:
            raise ValueError("an indexed material must be selected before Apply")
        try:
            prior = next(material for material in current.materials if material.index == index)
        except StopIteration as exc:
            raise ValueError(f"material index {index} is not present in the current spec") from exc

        replacement = self._material_from_authoring_controls(prior)
        result = replace_material(root, replacement)
        if type(result) is not MmdModelAuthoringSpec:
            raise TypeError("authoring coordinator replace_material returned an invalid spec")
        reloaded = read_spec(root)
        if type(reloaded) is not MmdModelAuthoringSpec:
            raise TypeError("authoring coordinator strict reload returned an invalid spec")

        # Keep the fingerprint available for UI/application diagnostics while
        # avoiding a second, legacy attribute read path.
        self.material_data["_authoring_fingerprint"] = reloaded.fingerprint()
        self.has_unsaved_changes = False
        self.app_state.emit_status(
            tr_message_format("material_changes_applied", material=self.current_material)
        )
        return reloaded

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

    def _apply_hardware_base_values(self, values, shader_type):
        """Write base values only when no evaluator owns the final plug."""
        material = self.current_material
        for binding, value in iter_hardware_shader_values(values, shader_type):
            if value is None or not self.maya_adapter.attribute_exists(binding.attribute, material):
                continue
            plug = f"{material}.{binding.attribute}"
            incoming = self.maya_adapter.list_connections(
                plug, source=True, destination=False, plugs=True
            ) or []
            if incoming:
                evaluator = incoming[0].split(".", 1)[0]
                try:
                    ready = (
                        self.maya_adapter.node_type(evaluator) == "mmdMaterialMorphEval"
                        and
                        self.maya_adapter.attribute_exists(
                            "mmd_complete_route_ready", evaluator
                        )
                        and self.maya_adapter.get_attr(
                            f"{evaluator}.mmd_complete_route_ready"
                        )
                        and self.maya_adapter.get_attr(
                            f"{evaluator}.mmd_target_shader"
                        ) == material
                    )
                except Exception:
                    ready = False
                morph_route = hardware_morph_route_for_uniform(
                    binding.attribute, shader_type
                )
                if ready and morph_route and morph_route.evaluator_base and morph_route.size > 1:
                    base_prefix = morph_route.evaluator_base
                    axes = "RGBA"[:morph_route.size]
                    for axis, component in zip(axes, value):
                        maya_attribute_utils.set_attribute(
                            evaluator, f"{base_prefix}{axis}", component, "float"
                        )
                elif ready and morph_route and morph_route.evaluator_base:
                    maya_attribute_utils.set_attribute(
                        evaluator, morph_route.evaluator_base, value, "float"
                    )
                logger.debug("Skipping driven hardware material plug: %s", plug)
                continue
            if binding.attribute == "Opacity":
                # Complete material morph owns alpha through DiffuseColorA only.
                evaluators = self.maya_adapter.list_connections(
                    f"{material}.DiffuseColorA", source=True, destination=False, plugs=True
                ) or []
                if evaluators:
                    evaluator = evaluators[0].split(".", 1)[0]
                    if self.maya_adapter.attribute_exists(
                        "mmd_complete_route_ready", evaluator
                    ) and self.maya_adapter.node_type(evaluator) == "mmdMaterialMorphEval" and self.maya_adapter.get_attr(
                        f"{evaluator}.mmd_complete_route_ready"
                    ) and self.maya_adapter.get_attr(f"{evaluator}.mmd_target_shader") == material:
                        maya_attribute_utils.set_attribute(material, "Opacity", 1.0, "float")
                        continue
            maya_attribute_utils.set_attribute(material, binding.attribute, value, binding.attribute_type)

    def _apply_texture(self, material, texture_path):
        """Apply texture to material"""
        shader_type = self.maya_adapter.node_type(material)

        # Determine which attribute to connect to
        if shader_type == "standardSurface":
            color_attr = f"{material}.baseColor"
        else:
            color_attr = f"{material}.color"

        # Check if file node already connected
        file_nodes = self.maya_adapter.list_connections(color_attr, type="file")

        if file_nodes:
            file_node = file_nodes[0]
        else:
            # Create new file node
            file_node = self.maya_adapter.shading_node("file", asTexture=True, name=f"{material}_texture")
            self.maya_adapter.connect_attr(f"{file_node}.outColor", color_attr, force=True)

        maya_attribute_utils.set_attribute(file_node, "fileTextureName", texture_path, "str")

    def _apply_mmd_attributes(self):
        """Apply MMD-specific attributes"""
        # Create attributes if they don't exist
        self._ensure_mmd_attributes(self.current_material)

        # Sphere map path
        sphere_path = self.view.sphere_map_path_edit.text()
        maya_attribute_utils.set_attribute(self.current_material, "mmd_sphere_path", sphere_path, "str")

        # Sphere mode
        maya_attribute_utils.set_attribute(
            self.current_material,
            "mmd_sphere_mode",
            self.view.sphere_mode_combo.currentIndex(),
            "int",
        )

        # Toon semantics: shared toon uses the built-in 0..9 combo; custom toon
        # uses the regular PMX texture table and preserves its relative path.
        shared_toon_flag = self._toon_shared_flag()
        toon_index = self.view.toon_texture_combo.currentIndex()
        if not shared_toon_flag:
            toon_texture_index_spin = getattr(self.view, "toon_texture_index_spin", None)
            if toon_texture_index_spin is not None:
                toon_index = toon_texture_index_spin.value()
        try:
            toon_index = int(toon_index)
        except (TypeError, ValueError):
            toon_index = int(self.material_data.get("toon_index", 0) or 0)
        maya_attribute_utils.set_attribute(
            self.current_material,
            ATTR_MMD_SHARED_TOON_FLAG,
            shared_toon_flag,
            "int",
        )
        maya_attribute_utils.set_attribute(
            self.current_material,
            ATTR_MMD_TOON_TEXTURE_INDEX,
            toon_index,
            "int",
        )
        if not shared_toon_flag:
            toon_texture_path_edit = getattr(self.view, "toon_texture_path_edit", None)
            toon_path = toon_texture_path_edit.text() if toon_texture_path_edit is not None else ""
            maya_attribute_utils.set_custom_attributes(
                self.current_material,
                {ATTR_MMD_TOON_PATH: toon_path},
            )

        # Draw flags
        draw_flags = 0
        if self.view.both_face_check.isChecked():
            draw_flags |= 0x01
        if self.view.ground_shadow_check.isChecked():
            draw_flags |= 0x02
        if self.view.self_shadow_map_check.isChecked():
            draw_flags |= 0x04
        if self.view.self_shadow_check.isChecked():
            draw_flags |= 0x08
        if self.view.edge_draw_check.isChecked():
            draw_flags |= 0x10
        if self.view.vertex_color_check.isChecked():
            draw_flags |= 0x20
        if self.view.point_draw_check.isChecked():
            draw_flags |= 0x40
        if self.view.line_draw_check.isChecked():
            draw_flags |= 0x80

        maya_attribute_utils.set_attribute(self.current_material, "mmd_draw_flags", draw_flags, "int")

        # Edge properties
        if "edge_color" in self.material_data:
            edge_color = self.material_data["edge_color"]
            maya_attribute_utils.set_attribute(
                self.current_material,
                "mmd_edge_color",
                [edge_color[0], edge_color[1], edge_color[2]],
                "double3",
            )
            maya_attribute_utils.set_attribute(
                self.current_material,
                ATTR_MMD_EDGE_ALPHA,
                float(self.material_data.get("edge_alpha", 1.0)),
                "float",
            )

        edge_size_value = self.view.edge_size_spin.value()
        if abs(edge_size_value - float(self.material_data.get("edge_size_view", edge_size_value))) < 1e-6:
            edge_size_value = float(self.material_data.get("edge_size", edge_size_value))

        maya_attribute_utils.set_attribute(
            self.current_material,
            "mmd_edge_size",
            edge_size_value,
            "float",
        )
    def _ensure_mmd_attributes(self, material):
        """Ensure MMD attributes exist on material"""
        # デフォルト値を設定
        defaults = {
            "mmd_sphere_path": "",
            "mmd_sphere_mode": 0,
            ATTR_MMD_SHARED_TOON_FLAG: 1,
            ATTR_MMD_TOON_TEXTURE_INDEX: 0,
            "mmd_draw_flags": 0x1F,
            "mmd_edge_color": [0.0, 0.0, 0.0],
            ATTR_MMD_EDGE_ALPHA: 1.0,
            "mmd_edge_size": 1.0,
            ATTR_MMD_SHADER_OUTLINE_ENABLED: False,
        }

        # 存在しないアトリビュートのみデフォルト値で作成
        attrs_to_create = {}
        for attr_name, default_value in defaults.items():
            if not self.maya_adapter.attribute_exists(attr_name, material):
                attrs_to_create[attr_name] = default_value

        # 一括で作成・設定
        if attrs_to_create:
            maya_attribute_utils.set_custom_attributes(material, attrs_to_create)

    def _toon_shared_flag(self):
        """Return the authored shared-toon mode, with legacy-view fallback."""
        toon_sharing_check = getattr(self.view, "toon_sharing_check", None)
        if toon_sharing_check is not None:
            return int(bool(toon_sharing_check.isChecked()))
        return int(bool(self.material_data.get("shared_toon_flag", 1)))

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
        if self.authoring_coordinator is not None:
            # Keep explicit mesh/face targets selected for Assign.
            return
        select_existing_user_role_nodes(
            self.view.material_list,
            self.maya_adapter,
            Qt.UserRole,
            exists=self.maya_adapter.object_exists,
            logger=logger,
            label="materials",
        )
