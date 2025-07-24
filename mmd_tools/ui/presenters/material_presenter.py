from maya import cmds
from ...core.logger import get_logger
from ..qt_compat import QColorDialog, QFileDialog, QColor

logger = get_logger(__name__)

class MaterialPresenter:
    def __init__(self, view, app_state):
        self.view = view
        self.app_state = app_state
        self.current_material = None
        self.material_data = {}  # Store original material data for reset
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
        self.view.specular_color_btn.clicked.connect(lambda: self.pick_color("specular"))
        self.view.ambient_color_btn.clicked.connect(lambda: self.pick_color("ambient"))
        self.view.edge_color_btn.clicked.connect(lambda: self.pick_color("edge"))
        
        # File browsers
        self.view.texture_browse_btn.clicked.connect(lambda: self.browse_file("texture"))
        self.view.sphere_map_browse_btn.clicked.connect(lambda: self.browse_file("sphere"))
        
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
            return

        shapes = cmds.listRelatives(current_model_root, allDescendents=True, type="mesh")
        if not shapes:
            self.view._set_details_enabled(False)
            return

        shading_groups = cmds.listConnections(shapes, type='shadingEngine')
        if not shading_groups:
            self.view._set_details_enabled(False)
            return

        # Get unique shading groups
        shading_groups = list(set(shading_groups))

        for sg in shading_groups:
            materials = cmds.ls(cmds.listConnections(sg), materials=True)
            for mat in materials:
                self.view.material_list.addItem(mat)
        
        logger.info(f"Loaded {self.view.material_list.count()} materials for model: {current_model_root}")

    def on_material_selected(self, current, previous):
        if not current:
            self.view._set_details_enabled(False)
            return
            
        material_name = current.text()
        logger.info(f"Selected material: {material_name}")
        
        self.current_material = material_name
        self.view._set_details_enabled(True)
        self.load_material_properties(material_name)
    
    def load_material_properties(self, material_name):
        """Load material properties from Maya material"""
        try:
            # Store original data for reset
            self.material_data = {}
            
            # Material name
            self.view.material_name_edit.setText(material_name)
            
            # Get basic colors
            diffuse_color = cmds.getAttr(f"{material_name}.color")[0]
            self.material_data["diffuse"] = diffuse_color
            self._update_color_widget(self.view.diffuse_color_widget, diffuse_color)
            
            specular_color = cmds.getAttr(f"{material_name}.specularColor")[0]
            self.material_data["specular"] = specular_color
            self._update_color_widget(self.view.specular_color_widget, specular_color)
            
            # Get ambient - Maya doesn't have ambient by default, check if attr exists
            if cmds.attributeQuery("ambientColor", node=material_name, exists=True):
                ambient_color = cmds.getAttr(f"{material_name}.ambientColor")[0]
            else:
                ambient_color = (0.5, 0.5, 0.5)
            self.material_data["ambient"] = ambient_color
            self._update_color_widget(self.view.ambient_color_widget, ambient_color)
            
            # Get specular power
            if cmds.attributeQuery("cosinePower", node=material_name, exists=True):
                specular_power = cmds.getAttr(f"{material_name}.cosinePower")
            else:
                specular_power = 5.0
            self.material_data["specular_power"] = specular_power
            self.view.specular_power_spin.setValue(specular_power)
            
            # Get transparency
            transparency = cmds.getAttr(f"{material_name}.transparency")[0]
            alpha = 1.0 - transparency[0]  # Maya uses transparency, MMD uses opacity
            self.material_data["alpha"] = alpha
            self.view.alpha_spin.setValue(alpha)
            
            # Get texture paths
            file_node = cmds.listConnections(f"{material_name}.color", type="file")
            if file_node:
                texture_path = cmds.getAttr(f"{file_node[0]}.fileTextureName")
                self.material_data["texture"] = texture_path
                self.view.texture_path_edit.setText(texture_path)
            else:
                self.material_data["texture"] = ""
                self.view.texture_path_edit.clear()
            
            # Get MMD-specific attributes if they exist
            self._load_mmd_attributes(material_name)
            
        except Exception as e:
            logger.error(f"Failed to load material details for {material_name}: {e}", exc_info=True)
    
    def _load_mmd_attributes(self, material_name):
        """Load MMD-specific attributes from material"""
        # Sphere map
        sphere_path = self._get_attr_safe(material_name, "mmdSpherePath", "")
        self.material_data["sphere_map"] = sphere_path
        self.view.sphere_map_path_edit.setText(sphere_path)
        
        # Sphere mode
        sphere_mode = self._get_attr_safe(material_name, "mmdSphereMode", 0)
        self.material_data["sphere_mode"] = sphere_mode
        self.view.sphere_mode_combo.setCurrentIndex(sphere_mode)
        
        # Toon texture
        toon_index = self._get_attr_safe(material_name, "mmdToonIndex", 0)
        self.material_data["toon_index"] = toon_index
        self.view.toon_texture_combo.setCurrentIndex(toon_index)
        
        # Draw flags
        draw_flags = self._get_attr_safe(material_name, "mmdDrawFlags", 0x1F)
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
        edge_color = self._get_attr_safe(material_name, "mmdEdgeColor", (0.0, 0.0, 0.0, 1.0))
        if len(edge_color) == 4:
            edge_color = edge_color[:3]  # Remove alpha
        self.material_data["edge_color"] = edge_color
        self._update_color_widget(self.view.edge_color_widget, edge_color)
        
        edge_size = self._get_attr_safe(material_name, "mmdEdgeSize", 1.0)
        self.material_data["edge_size"] = edge_size
        self.view.edge_size_spin.setValue(edge_size)
    
    def _get_attr_safe(self, node, attr, default):
        """Get attribute value safely, return default if not exists"""
        if cmds.attributeQuery(attr, node=node, exists=True):
            return cmds.getAttr(f"{node}.{attr}")
        return default
    
    def _update_color_widget(self, widget, color):
        """Update color display widget"""
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
        color = QColorDialog.getColor(initial_color, self.view, f"{color_type.capitalize()}色を選択")
        
        if color.isValid():
            # Update display
            new_color = (color.red() / 255.0, color.green() / 255.0, color.blue() / 255.0)
            self._update_color_widget(widget, new_color)
            # Store in temp data (not applied yet)
            self.material_data[color_type] = new_color
    
    def browse_file(self, file_type):
        """Open file browser dialog"""
        if not self.current_material:
            return
            
        if file_type == "texture":
            caption = "テクスチャファイルを選択"
            filter_str = "Image Files (*.png *.jpg *.jpeg *.bmp *.tga *.dds);;All Files (*.*)"
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
        file_path, _ = QFileDialog.getOpenFileName(self.view, caption, start_dir, filter_str)
        
        if file_path:
            line_edit.setText(file_path)
            self.material_data[f"{file_type}_path"] = file_path
    
    def apply_changes(self):
        """Apply material changes to Maya material"""
        if not self.current_material:
            return
            
        try:
            # Apply basic colors
            if "diffuse" in self.material_data:
                cmds.setAttr(f"{self.current_material}.color", *self.material_data["diffuse"], type="double3")
            
            if "specular" in self.material_data:
                cmds.setAttr(f"{self.current_material}.specularColor", *self.material_data["specular"], type="double3")
            
            # Apply transparency
            alpha = self.view.alpha_spin.value()
            transparency = 1.0 - alpha
            cmds.setAttr(f"{self.current_material}.transparency", transparency, transparency, transparency, type="double3")
            
            # Apply specular power
            if cmds.attributeQuery("cosinePower", node=self.current_material, exists=True):
                cmds.setAttr(f"{self.current_material}.cosinePower", self.view.specular_power_spin.value())
            
            # Apply textures
            texture_path = self.view.texture_path_edit.text()
            if texture_path and texture_path != self.material_data.get("texture", ""):
                self._apply_texture(self.current_material, texture_path)
            
            # Apply MMD-specific attributes
            self._apply_mmd_attributes()
            
            logger.info(f"材質 '{self.current_material}' の変更を適用しました")
            self.app_state.emit_status(f"材質の変更を適用しました: {self.current_material}")
            
        except Exception as e:
            logger.error(f"Failed to apply material changes: {e}", exc_info=True)
            self.app_state.emit_status(f"材質の変更に失敗しました: {str(e)}")
    
    def _apply_texture(self, material, texture_path):
        """Apply texture to material"""
        # Check if file node already connected
        file_nodes = cmds.listConnections(f"{material}.color", type="file")
        
        if file_nodes:
            file_node = file_nodes[0]
        else:
            # Create new file node
            file_node = cmds.shadingNode("file", asTexture=True, name=f"{material}_texture")
            cmds.connectAttr(f"{file_node}.outColor", f"{material}.color", force=True)
        
        cmds.setAttr(f"{file_node}.fileTextureName", texture_path, type="string")
    
    def _apply_mmd_attributes(self):
        """Apply MMD-specific attributes"""
        # Create attributes if they don't exist
        self._ensure_mmd_attributes(self.current_material)
        
        # Sphere map path
        sphere_path = self.view.sphere_map_path_edit.text()
        cmds.setAttr(f"{self.current_material}.mmdSpherePath", sphere_path, type="string")
        
        # Sphere mode
        cmds.setAttr(f"{self.current_material}.mmdSphereMode", self.view.sphere_mode_combo.currentIndex())
        
        # Toon index
        cmds.setAttr(f"{self.current_material}.mmdToonIndex", self.view.toon_texture_combo.currentIndex())
        
        # Draw flags
        draw_flags = 0
        if self.view.both_face_check.isChecked(): draw_flags |= 0x01
        if self.view.ground_shadow_check.isChecked(): draw_flags |= 0x02
        if self.view.self_shadow_map_check.isChecked(): draw_flags |= 0x04
        if self.view.self_shadow_check.isChecked(): draw_flags |= 0x08
        if self.view.edge_draw_check.isChecked(): draw_flags |= 0x10
        if self.view.vertex_color_check.isChecked(): draw_flags |= 0x20
        if self.view.point_draw_check.isChecked(): draw_flags |= 0x40
        if self.view.line_draw_check.isChecked(): draw_flags |= 0x80
        
        cmds.setAttr(f"{self.current_material}.mmdDrawFlags", draw_flags)
        
        # Edge properties
        if "edge_color" in self.material_data:
            edge_color = self.material_data["edge_color"]
            cmds.setAttr(f"{self.current_material}.mmdEdgeColor", 
                        edge_color[0], edge_color[1], edge_color[2], 1.0, type="double4")
        
        cmds.setAttr(f"{self.current_material}.mmdEdgeSize", self.view.edge_size_spin.value())
    
    def _ensure_mmd_attributes(self, material):
        """Ensure MMD attributes exist on material"""
        attrs = [
            ("mmdSpherePath", "string", ""),
            ("mmdSphereMode", "long", 0),
            ("mmdToonIndex", "long", 0),
            ("mmdDrawFlags", "long", 0x1F),
            ("mmdEdgeColor", "double4", None),
            ("mmdEdgeSize", "double", 1.0),
            ("ambientColor", "double3", None)
        ]
        
        for attr_name, attr_type, default in attrs:
            if not cmds.attributeQuery(attr_name, node=material, exists=True):
                if attr_type == "double3":
                    cmds.addAttr(material, longName=attr_name, attributeType="double3")
                    cmds.addAttr(material, longName=f"{attr_name}R", attributeType="double", parent=attr_name)
                    cmds.addAttr(material, longName=f"{attr_name}G", attributeType="double", parent=attr_name)
                    cmds.addAttr(material, longName=f"{attr_name}B", attributeType="double", parent=attr_name)
                    if default is None:
                        cmds.setAttr(f"{material}.{attr_name}", 0.5, 0.5, 0.5, type="double3")
                elif attr_type == "double4":
                    cmds.addAttr(material, longName=attr_name, attributeType="double4")
                    cmds.addAttr(material, longName=f"{attr_name}X", attributeType="double", parent=attr_name)
                    cmds.addAttr(material, longName=f"{attr_name}Y", attributeType="double", parent=attr_name)
                    cmds.addAttr(material, longName=f"{attr_name}Z", attributeType="double", parent=attr_name)
                    cmds.addAttr(material, longName=f"{attr_name}W", attributeType="double", parent=attr_name)
                    if default is None:
                        cmds.setAttr(f"{material}.{attr_name}", 0.0, 0.0, 0.0, 1.0, type="double4")
                else:
                    cmds.addAttr(material, longName=attr_name, attributeType=attr_type, defaultValue=default)
    
    def reset_changes(self):
        """Reset material properties to original values"""
        if not self.current_material:
            return
            
        # Reload original properties
        self.load_material_properties(self.current_material)
        logger.info(f"材質 '{self.current_material}' の変更をリセットしました")
