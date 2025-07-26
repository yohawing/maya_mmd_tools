from maya import cmds
from mmd_tools.core import maya_utils
from mmd_tools.core.constants import (
    ATTR_MMD_DRAW_FLAGS,
    ATTR_MMD_EDGE_COLOR,
    ATTR_MMD_EDGE_SIZE,
    ATTR_MMD_MATERIAL_NAME,
    ATTR_MMD_SPHERE_MODE,
    ATTR_MMD_SPHERE_PATH,
    ATTR_MMD_TOON_TEXTURE_INDEX,
)
from mmd_tools.core.pmx_data.material import PmxSphereMode
from ...core.logger import get_logger
from ..qt_compat import QColorDialog, QFileDialog, QColor, Qt

logger = get_logger(__name__)


class MaterialPresenter:
    def __init__(self, view, app_state):
        self.view = view
        self.app_state = app_state
        self.current_material = None
        self.material_data = {}  # Store original material data for reset
        self.has_unsaved_changes = False
        self._loading_properties = (
            False  # Flag to prevent change tracking during loading
        )
        self.connect_signals()

        # 既に選択されているモデルがある場合はロード
        if self.app_state.current_model_root:
            self.load_materials()

    def connect_signals(self):
        # ApplicationStateのシグナル
        self.app_state.current_model_changed.connect(self.on_current_model_changed)

        # UIのシグナル
        self.view.material_list.currentItemChanged.connect(self.on_material_selected)
        self.view.refresh_btn.clicked.connect(self.load_materials)

        # Color buttons
        self.view.diffuse_color_btn.clicked.connect(lambda: self.pick_color("diffuse"))
        self.view.specular_color_btn.clicked.connect(
            lambda: self.pick_color("specular")
        )
        self.view.ambient_color_btn.clicked.connect(lambda: self.pick_color("ambient"))
        self.view.edge_color_btn.clicked.connect(lambda: self.pick_color("edge"))

        # File browsers
        self.view.texture_browse_btn.clicked.connect(
            lambda: self.browse_file("texture")
        )
        self.view.sphere_map_browse_btn.clicked.connect(
            lambda: self.browse_file("sphere")
        )

        # Track changes in input fields
        self.view.specular_coefficient_spin.valueChanged.connect(self._on_value_changed)
        self.view.transparency_spin.valueChanged.connect(self._on_value_changed)
        self.view.edge_size_spin.valueChanged.connect(self._on_value_changed)
        self.view.sphere_mode_combo.currentIndexChanged.connect(self._on_value_changed)
        self.view.toon_texture_combo.currentIndexChanged.connect(self._on_value_changed)

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
        logger.info(f"MaterialPresenter: Current model changed to {model_root}")
        self.load_materials()

    def load_materials(self):
        self.view.material_list.clear()

        current_model_root = self.app_state.current_model_root
        if not current_model_root or not cmds.objExists(current_model_root):
            self.view._set_details_enabled(False)
            self.view.material_count_label.setText("マテリアル数: 0")
            self.view._show_placeholder()
            return

        try:
            # MMDマテリアルノードを探す
            # モデルルートにアトリビュートとして保存されているマテリアルリストを確認
            mmd_materials = []

            # 方法3: mmd_material_name属性を持つマテリアルを検索
            if not mmd_materials:
                shapes = cmds.listRelatives(
                    current_model_root, allDescendents=True, type="mesh"
                )
                if shapes:
                    shading_groups = cmds.listConnections(shapes, type="shadingEngine")
                    if shading_groups:
                        shading_groups = list(set(shading_groups))
                        for sg in shading_groups:
                            materials = cmds.ls(
                                cmds.listConnections(sg), materials=True
                            )
                            if materials:
                                for mat in materials:
                                    # MMD関連の属性があるかチェック
                                    if cmds.attributeQuery(
                                        ATTR_MMD_MATERIAL_NAME, node=mat, exists=True
                                    ):
                                        mmd_materials.append(mat)

            # 重複を削除
            unique_materials = list(set(mmd_materials))

            # Add materials to list with Japanese names
            for mat in sorted(unique_materials):
                # 日本語名を取得
                jp_name = maya_utils.get_attribute(mat, ATTR_MMD_MATERIAL_NAME)

                # 表示テキストを決定
                if jp_name:
                    display_text = f"{jp_name} ({mat})"
                else:
                    display_text = mat

                # リストに追加
                from ..qt_compat import QListWidgetItem

                item = QListWidgetItem(display_text)
                item.setData(Qt.UserRole, mat)  # 実際のマテリアル名を保存
                self.view.material_list.addItem(item)

            # Update material count
            material_count = self.view.material_list.count()
            self.view.material_count_label.setText(f"マテリアル数: {material_count}")

            # Show placeholder if no materials
            if material_count == 0:
                self.view._show_placeholder()

            logger.info(
                f"Loaded {material_count} MMD materials for model: {current_model_root}"
            )

        except Exception as e:
            logger.error(f"Failed to load materials: {e}", exc_info=True)
            self.view._set_details_enabled(False)
            self.view.material_count_label.setText("マテリアル数: 0")
            self.view._show_placeholder()
            self.app_state.emit_status(f"マテリアルの読み込みに失敗しました: {str(e)}")

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
                "未保存の変更",
                "変更が保存されていません。別のマテリアルを選択しますか？",
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

        logger.info(f"Selected material: {material_name}")

        self.current_material = material_name
        self.view._set_details_enabled(True)
        self.load_material_properties(material_name)
        self.has_unsaved_changes = (
            False  # Reset after loading to prevent false positives
        )

    def load_material_properties(self, material_name):
        """Load material properties from Maya material"""
        self._loading_properties = True
        try:
            # Store original data for reset
            self.material_data = {}

            # Japanese name
            jp_name = maya_utils.get_attribute(material_name, ATTR_MMD_MATERIAL_NAME)
            self.view.material_jp_name_edit.setText(jp_name if jp_name else "")

            # Material name
            self.view.material_name_edit.setText(material_name)

            # Get basic colors
            # Check shader type
            shader_type = cmds.nodeType(material_name)

            # Get diffuse color based on shader type
            if shader_type == "standardSurface":
                diffuse_color = maya_utils.get_attribute(material_name, "baseColor")
            elif cmds.attributeQuery("color", node=material_name, exists=True):
                diffuse_color = maya_utils.get_attribute(material_name, "color")
            else:
                diffuse_color = (0.5, 0.5, 0.5)
            self.material_data["diffuse"] = diffuse_color
            self._update_color_widget(self.view.diffuse_color_widget, diffuse_color)

            # Get specular color
            if cmds.attributeQuery("specularColor", node=material_name, exists=True):
                specular_color = maya_utils.get_attribute(
                    material_name, "specularColor"
                )
            else:
                specular_color = (0.5, 0.5, 0.5)
            self.material_data["specular"] = specular_color
            self._update_color_widget(self.view.specular_color_widget, specular_color)

            # Get ambient - Maya doesn't have ambient by default, check if attr exists
            if cmds.attributeQuery("ambientColor", node=material_name, exists=True):
                ambient_color = maya_utils.get_attribute(material_name, "ambientColor")
            else:
                ambient_color = (0.5, 0.5, 0.5)
            self.material_data["ambient"] = ambient_color
            self._update_color_widget(self.view.ambient_color_widget, ambient_color)

            # Get specular coefficient (MMD style)
            if cmds.attributeQuery(
                "mmd_specular_coefficient", node=material_name, exists=True
            ):
                specular_coefficient = maya_utils.get_attribute(
                    material_name, "mmd_specular_coefficient"
                )
            elif cmds.attributeQuery("specular", node=material_name, exists=True):
                # StandardSurfaceの場合、specular値を係数に変換
                specular_weight = maya_utils.get_attribute(material_name, "specular")
                specular_coefficient = specular_weight * 100.0
            else:
                specular_coefficient = 5.0
            self.material_data["specular_coefficient"] = specular_coefficient
            self.view.specular_coefficient_spin.setValue(specular_coefficient)

            # Get transparency (PMX style)
            if cmds.attributeQuery("opacity", node=material_name, exists=True):
                # StandardSurfaceの場合
                opacity = maya_utils.get_attribute(material_name, "opacity")
                transparency = 1.0 - opacity[0]  # Convert opacity to transparency
            elif cmds.attributeQuery("transparency", node=material_name, exists=True):
                transparency_val = maya_utils.get_attribute(
                    material_name, "transparency"
                )
                transparency = transparency_val[0]
            else:
                transparency = 0.0
            self.material_data["transparency"] = transparency
            self.view.transparency_spin.setValue(transparency)

            # Get texture paths
            # Check which attribute to look for connections
            texture_attrs = []
            if shader_type == "standardSurface":
                texture_attrs.append(f"{material_name}.baseColor")
            if cmds.attributeQuery("color", node=material_name, exists=True):
                texture_attrs.append(f"{material_name}.color")

            file_node = None
            for attr in texture_attrs:
                connections = cmds.listConnections(attr, type="file")
                if connections:
                    file_node = connections
                    break

            if file_node:
                texture_path = maya_utils.get_attribute(file_node[0], "fileTextureName")
                self.material_data["texture"] = texture_path
                self.view.texture_path_edit.setText(texture_path)
            else:
                self.material_data["texture"] = ""
                self.view.texture_path_edit.clear()

            # Get MMD-specific attributes if they exist
            self._load_mmd_attributes(material_name)

        except Exception as e:
            logger.error(
                f"Failed to load material details for {material_name}: {e}",
                exc_info=True,
            )
        finally:
            self._loading_properties = False

    def _load_mmd_attributes(self, material_name):
        """Load MMD-specific attributes from material"""
        # Sphere map
        sphere_path = self._get_attr_safe(material_name, ATTR_MMD_SPHERE_PATH, "")
        self.material_data["sphere_map"] = sphere_path
        self.view.sphere_map_path_edit.setText(sphere_path)

        # Sphere mode
        sphere_mode = self._get_attr_safe(material_name, ATTR_MMD_SPHERE_MODE, 0)
        self.material_data["sphere_mode"] = sphere_mode
        self.view.sphere_mode_combo.setCurrentIndex(sphere_mode)

        # Toon texture
        toon_index = self._get_attr_safe(material_name, ATTR_MMD_TOON_TEXTURE_INDEX, 0)
        self.material_data["toon_index"] = toon_index
        self.view.toon_texture_combo.setCurrentIndex(toon_index)

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
        edge_color = self._get_attr_safe(
            material_name, ATTR_MMD_EDGE_COLOR, (0.0, 0.0, 0.0, 1.0)
        )
        if len(edge_color) == 4:
            edge_color = edge_color[:3]  # Remove alpha
        self.material_data["edge_color"] = edge_color
        self._update_color_widget(self.view.edge_color_widget, edge_color)

        edge_size = self._get_attr_safe(material_name, ATTR_MMD_EDGE_SIZE, 1.0)
        self.material_data["edge_size"] = edge_size
        self.view.edge_size_spin.setValue(edge_size)

    def _get_attr_safe(self, node, attr, default):
        """Get attribute value safely, return default if not exists"""
        if cmds.attributeQuery(attr, node=node, exists=True):
            return maya_utils.get_attribute(node, attr)
        return default

    def _update_color_widget(self, widget, color):
        """Update color display widget"""
        r, g, b = int(color[0] * 255), int(color[1] * 255), int(color[2] * 255)
        widget.setStyleSheet(
            f"background-color: rgb({r}, {g}, {b}); border: 1px solid black;"
        )

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
        initial_color = QColor(
            int(current[0] * 255), int(current[1] * 255), int(current[2] * 255)
        )
        color = QColorDialog.getColor(
            initial_color, self.view, f"{color_type.capitalize()}色を選択"
        )

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
            caption = "テクスチャファイルを選択"
            filter_str = (
                "Image Files (*.png *.jpg *.jpeg *.bmp *.tga *.dds);;All Files (*.*)"
            )
            line_edit = self.view.texture_path_edit
        elif file_type == "sphere":
            caption = "スフィアマップを選択"
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
            start_dir = cmds.workspace(query=True, rootDirectory=True)

        # Open file dialog
        file_path, _ = QFileDialog.getOpenFileName(
            self.view, caption, start_dir, filter_str
        )

        if file_path:
            line_edit.setText(file_path)
            self.material_data[f"{file_type}_path"] = file_path
            self.has_unsaved_changes = True

    def apply_changes(self):
        """Apply material changes to Maya material"""
        if not self.current_material:
            return

        try:
            # Apply basic colors
            if "diffuse" in self.material_data:
                shader_type = cmds.nodeType(self.current_material)
                if shader_type == "standardSurface":
                    maya_utils.set_attribute(
                        self.current_material,
                        "baseColor",
                        self.material_data["diffuse"],
                        "double3",
                    )
                elif cmds.attributeQuery(
                    "color", node=self.current_material, exists=True
                ):
                    maya_utils.set_attribute(
                        self.current_material,
                        "color",
                        self.material_data["diffuse"],
                        "double3",
                    )

            if "specular" in self.material_data:
                maya_utils.set_attribute(
                    self.current_material,
                    "specularColor",
                    self.material_data["specular"],
                    "double3",
                )

            # Apply transparency
            transparency = self.view.transparency_spin.value()
            # StandardSurfaceの場合はopacityに変換
            if cmds.nodeType(self.current_material) == "standardSurface":
                opacity = 1.0 - transparency
                maya_utils.set_attribute(
                    self.current_material,
                    "opacity",
                    [opacity, opacity, opacity],
                    "double3",
                )
            else:
                # その他のシェーダーの場合
                maya_utils.set_attribute(
                    self.current_material,
                    "transparency",
                    [transparency, transparency, transparency],
                    "double3",
                )

            # Apply specular coefficient
            specular_coefficient = self.view.specular_coefficient_spin.value()
            # MMD係数として保存
            maya_utils.set_custom_attributes(
                self.current_material,
                {"mmd_specular_coefficient": specular_coefficient},
            )

            # StandardSurfaceの場合はspecularに変換
            if cmds.nodeType(self.current_material) == "standardSurface":
                specular_weight = min(1.0, specular_coefficient / 100.0)
                maya_utils.set_attribute(
                    self.current_material, "specular", specular_weight, "float"
                )

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
                self._apply_sphere_map(self.current_material, sphere_path, sphere_mode)

            self.has_unsaved_changes = False
            logger.info(f"材質 '{self.current_material}' の変更を適用しました")
            self.app_state.emit_status(
                f"材質の変更を適用しました: {self.current_material}"
            )

        except Exception as e:
            logger.error(f"Failed to apply material changes: {e}", exc_info=True)
            self.app_state.emit_status(f"材質の変更に失敗しました: {str(e)}")

    def _apply_texture(self, material, texture_path):
        """Apply texture to material"""
        shader_type = cmds.nodeType(material)

        # Determine which attribute to connect to
        if shader_type == "standardSurface":
            color_attr = f"{material}.baseColor"
        else:
            color_attr = f"{material}.color"

        # Check if file node already connected
        file_nodes = cmds.listConnections(color_attr, type="file")

        if file_nodes:
            file_node = file_nodes[0]
        else:
            # Create new file node
            file_node = cmds.shadingNode(
                "file", asTexture=True, name=f"{material}_texture"
            )
            cmds.connectAttr(f"{file_node}.outColor", color_attr, force=True)

        maya_utils.set_attribute(file_node, "fileTextureName", texture_path, "str")

    def _apply_mmd_attributes(self):
        """Apply MMD-specific attributes"""
        # Create attributes if they don't exist
        self._ensure_mmd_attributes(self.current_material)

        # Sphere map path
        sphere_path = self.view.sphere_map_path_edit.text()
        maya_utils.set_attribute(
            self.current_material, "mmd_sphere_path", sphere_path, "str"
        )

        # Sphere mode
        maya_utils.set_attribute(
            self.current_material,
            "mmd_sphere_mode",
            self.view.sphere_mode_combo.currentIndex(),
            "int",
        )

        # Toon index
        maya_utils.set_attribute(
            self.current_material,
            "mmd_toon_index",
            self.view.toon_texture_combo.currentIndex(),
            "int",
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

        maya_utils.set_attribute(
            self.current_material, "mmd_draw_flags", draw_flags, "int"
        )

        # Edge properties
        if "edge_color" in self.material_data:
            edge_color = self.material_data["edge_color"]
            maya_utils.set_attribute(
                self.current_material,
                "mmd_edge_color",
                [edge_color[0], edge_color[1], edge_color[2], 1.0],
                "double4",
            )

        maya_utils.set_attribute(
            self.current_material,
            "mmd_edge_size",
            self.view.edge_size_spin.value(),
            "float",
        )

    def _ensure_mmd_attributes(self, material):
        """Ensure MMD attributes exist on material"""
        # デフォルト値を設定
        defaults = {
            "mmd_sphere_path": "",
            "mmd_sphere_mode": 0,
            "mmd_toon_index": 0,
            "mmd_draw_flags": 0x1F,
            "mmd_edge_color": [0.0, 0.0, 0.0, 1.0],
            "mmd_edge_size": 1.0,
            "mmd_specular_coefficient": 5.0,
            "ambientColor": [0.5, 0.5, 0.5],
        }

        # 存在しないアトリビュートのみデフォルト値で作成
        attrs_to_create = {}
        for attr_name, default_value in defaults.items():
            if not cmds.attributeQuery(attr_name, node=material, exists=True):
                attrs_to_create[attr_name] = default_value

        # 一括で作成・設定
        if attrs_to_create:
            maya_utils.set_custom_attributes(material, attrs_to_create)

    def reset_changes(self):
        """Reset material properties to original values"""
        if not self.current_material:
            return

        # Reload original properties
        self.load_material_properties(self.current_material)
        self.has_unsaved_changes = False
        logger.info(f"材質 '{self.current_material}' の変更をリセットしました")
        self.app_state.emit_status(
            f"材質の変更をリセットしました: {self.current_material}"
        )

    def _on_value_changed(self, value=None):
        """値が変更されたときの処理"""
        if self.current_material and not self._loading_properties:
            self.has_unsaved_changes = True

    def _apply_sphere_map(self, material, sphere_path, sphere_mode):
        """スフィアマップをマテリアルに適用"""
        try:
            import os

            if not os.path.exists(sphere_path):
                logger.warning(f"Sphere map file not found: {sphere_path}")
                return

            # スフィアマップ用のファイルノードを作成または取得
            sphere_file_node = None
            file_nodes = cmds.ls(type="file")
            for node in file_nodes:
                if maya_utils.get_attribute(node, "fileTextureName") == sphere_path:
                    sphere_file_node = node
                    break

            if not sphere_file_node:
                sphere_file_node = cmds.shadingNode(
                    "file", asTexture=True, name=f"{material}_sphere"
                )
                maya_utils.set_attribute(
                    sphere_file_node, "fileTextureName", sphere_path, "str"
                )

            # Mayaでスフィアマップを近似的に再現
            # モード: 1=乗算, 2=加算, 3=サブテクスチャ
            if sphere_mode == PmxSphereMode.MULTIPLY:  # 乗算
                # layeredTextureを使用して乗算合成
                layered_texture = cmds.shadingNode(
                    "layeredTexture", asTexture=True, name=f"{material}_layered"
                )

                # ベーステクスチャを接続
                base_file = cmds.listConnections(f"{material}.baseColor", type="file")
                if base_file:
                    cmds.connectAttr(
                        f"{base_file[0]}.outColor", f"{layered_texture}.inputs[0].color"
                    )
                    maya_utils.set_attribute(
                        layered_texture, "inputs[0].blendMode", 0, "int"
                    )  # None

                # スフィアマップを接続
                cmds.connectAttr(
                    f"{sphere_file_node}.outColor", f"{layered_texture}.inputs[1].color"
                )
                maya_utils.set_attribute(
                    layered_texture, "inputs[1].blendMode", 6, "int"
                )  # Multiply

                # マテリアルに接続
                cmds.connectAttr(
                    f"{layered_texture}.outColor", f"{material}.baseColor", force=True
                )

            elif sphere_mode == PmxSphereMode.ADDITIVE:  # 加算
                # エミッションにスフィアマップを接続して加算効果を近似
                cmds.connectAttr(
                    f"{sphere_file_node}.outColor",
                    f"{material}.emissionColor",
                    force=True,
                )
                maya_utils.set_attribute(
                    material, "emission", 0.5, "float"
                )  # エミッション強度

            elif sphere_mode == PmxSphereMode.SUB_TEXTURE:  # サブテクスチャ
                # スペキュラーマップとして使用
                cmds.connectAttr(
                    f"{sphere_file_node}.outColor",
                    f"{material}.specularColor",
                    force=True,
                )

            logger.info(
                f"Applied sphere map to material '{material}' with mode {sphere_mode}"
            )

        except Exception as e:
            logger.error(f"Failed to apply sphere map: {e}", exc_info=True)
            self.app_state.emit_status(f"スフィアマップの適用に失敗しました: {str(e)}")
