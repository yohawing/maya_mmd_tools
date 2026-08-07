import os

from ..qt_compat import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGroupBox,
    QFormLayout,
    QLineEdit,
    QPushButton,
    QCheckBox,
    QComboBox,
    QSlider,
    QDoubleSpinBox,
    QLabel,
    QScrollArea,
    Qt,
    QSplitter,
    QListWidget,
    QListWidgetItem,
    QColor,
)
from ..base_tab import BaseTab
from ..import_export_view_state import ImportExportViewState
from ...core import settings_keys as setting_keys
from ...services.settings_service import SettingsService, normalize_reduce_bake_quality


_REDUCE_BAKE_QUALITY_DEFAULT = 1.0


class ImportExportTab(BaseTab):
    def __init__(self, parent=None, view_state=None, settings_service=None):
        super().__init__(parent)
        self.setObjectName("ImportExportTab")

        self.view_state = view_state or ImportExportViewState()
        self.settings_service = settings_service or SettingsService()

        # メインレイアウト
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(5, 5, 5, 5)

        # スプリッターを作成（横方向）
        splitter = QSplitter(Qt.Horizontal)

        # 左側：設定セクション（フラット）
        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.left_widget = QWidget()
        model_settings_layout = QVBoxLayout(self.left_widget)

        # Import scale (dev-only control; normal mode always uses 1.0)
        self.scale_row = QWidget()
        scale_layout = QHBoxLayout(self.scale_row)
        scale_layout.setContentsMargins(0, 0, 0, 0)
        self.scale_label = QLabel(self.tr("import_scale", "fields"))
        scale_layout.addWidget(self.scale_label)
        self.scale_spin = QDoubleSpinBox()
        self.scale_spin.setRange(0.001, 1000.0)
        self.scale_spin.setDecimals(3)
        # Normal mode always displays 1.0; persisted scale is only shown in dev mode.
        # Do not write 1.0 back over a previously stored development scale.
        initial_scale = (
            self.settings_service.resolve_import_scale()
            if hasattr(self.settings_service, "resolve_import_scale")
            else self.settings_service.get(setting_keys.IMPORT_GENERAL_SCALE_FACTOR, 1.0)
        )
        self.scale_spin.setValue(initial_scale)
        self.scale_spin.valueChanged.connect(lambda v: self.settings_service.set(setting_keys.IMPORT_GENERAL_SCALE_FACTOR, v))
        self.scale_spin.setToolTip(self.tr("import_scale", "tooltips"))
        scale_layout.addWidget(self.scale_spin)
        scale_layout.addStretch()
        model_settings_layout.addWidget(self.scale_row)

        # General Model Settings Group
        self.general_group = QGroupBox(self.tr("general", "groups"))
        general_layout = QVBoxLayout()

        self.use_namespace_check = self._bind_checkbox(
            "use_namespace", setting_keys.IMPORT_GENERAL_USE_NAMESPACE, False, general_layout, tooltip_key="use_namespace"
        )

        # Namespace名を手動で指定するオプション
        namespace_layout = QHBoxLayout()
        namespace_layout.setContentsMargins(20, 0, 0, 0)  # インデント

        self.custom_namespace_check = QCheckBox(self.tr("custom_namespace", "checkboxes"))
        # カスタムnamespaceチェックボックスの状態を読み込み
        saved_custom_namespace = self.view_state.get("custom_namespace_check", "false")
        self.custom_namespace_check.setChecked(str(saved_custom_namespace).lower() == "true")
        self.custom_namespace_check.setEnabled(self.use_namespace_check.isChecked())
        # 状態が変更されたら保存
        self.custom_namespace_check.toggled.connect(lambda checked: self.view_state.set("custom_namespace_check", str(checked)))
        namespace_layout.addWidget(self.custom_namespace_check)

        self.namespace_edit = QLineEdit()
        self.namespace_edit.setPlaceholderText(self.tr("namespace_placeholder", "labels"))
        # namespace名を読み込み
        saved_namespace = self.view_state.get("custom_namespace_name", "")
        self.namespace_edit.setText(saved_namespace)
        self.namespace_edit.setEnabled(self.custom_namespace_check.isChecked())
        # テキストが変更されたら保存
        self.namespace_edit.textChanged.connect(lambda text: self.view_state.set("custom_namespace_name", text))
        namespace_layout.addWidget(self.namespace_edit)

        # シグナル接続
        self.use_namespace_check.toggled.connect(self.custom_namespace_check.setEnabled)
        self.custom_namespace_check.toggled.connect(self.namespace_edit.setEnabled)

        general_layout.addLayout(namespace_layout)

        self.general_group.setLayout(general_layout)
        model_settings_layout.addWidget(self.general_group)

        # Model Settings Group
        self.model_group = QGroupBox(self.tr("model", "groups"))
        model_layout = QVBoxLayout()

        self.import_models_check = self._bind_checkbox(
            "import_models", setting_keys.IMPORT_MODEL_IMPORT_MODELS, True, model_layout
        )

        self.create_mmd_shaders_check = self._bind_checkbox(
            "create_mmd_shaders",
            setting_keys.IMPORT_MODEL_CREATE_MMD_SHADERS,
            True,
            model_layout,
            tooltip_key="create_mmd_shaders",
        )

        self.create_mmd_control_rig_check = self._bind_checkbox(
            "create_mmd_control_rig",
            setting_keys.IMPORT_MODEL_CREATE_MMD_CONTROL_RIG,
            False,
            model_layout,
            tooltip_key="create_mmd_control_rig",
        )

        self.separate_meshes_check = self._bind_checkbox(
            "separate_meshes",
            setting_keys.IMPORT_MODEL_SEPARATE_MESHES_BY_MATERIAL,
            False,
            model_layout,
            tooltip_key="separate_meshes",
        )

        self.auto_resolve_textures_check = self._bind_checkbox(
            "auto_resolve_textures",
            setting_keys.IMPORT_MODEL_AUTO_RESOLVE_TEXTURES,
            True,
            model_layout,
            tooltip_key="auto_resolve_textures",
        )

        self.disable_backface_culling_check = self._bind_checkbox(
            "disable_backface_culling",
            setting_keys.IMPORT_MODEL_DISABLE_BACKFACE_CULLING,
            True,
            model_layout,
            tooltip_key="disable_backface_culling",
        )

        # Texture search path
        self.texture_row = QWidget()
        texture_layout = QHBoxLayout(self.texture_row)
        texture_layout.setContentsMargins(0, 0, 0, 0)
        self.texture_search_label = QLabel(self.tr("texture_search_path", "fields"))
        texture_layout.addWidget(self.texture_search_label)
        self.texture_search_path_edit = QLineEdit(self.settings_service.get(setting_keys.IMPORT_MODEL_TEXTURE_SEARCH_PATH, ""))
        self.texture_search_path_edit.textChanged.connect(
            lambda v: self.settings_service.set(setting_keys.IMPORT_MODEL_TEXTURE_SEARCH_PATH, v)
        )
        texture_layout.addWidget(self.texture_search_path_edit)
        model_layout.addWidget(self.texture_row)

        # UV set name
        self.uv_row = QWidget()
        uv_layout = QHBoxLayout(self.uv_row)
        uv_layout.setContentsMargins(0, 0, 0, 0)
        self.uv_set_label = QLabel(self.tr("uv_set_name", "fields"))
        uv_layout.addWidget(self.uv_set_label)
        self.uv_set_name_edit = QLineEdit(self.settings_service.get(setting_keys.IMPORT_MODEL_UV_SET_NAME, "map#"))
        self.uv_set_name_edit.textChanged.connect(lambda v: self.settings_service.set(setting_keys.IMPORT_MODEL_UV_SET_NAME, v))
        uv_layout.addWidget(self.uv_set_name_edit)
        uv_layout.addStretch()
        model_layout.addWidget(self.uv_row)

        self.model_group.setLayout(model_layout)
        model_settings_layout.addWidget(self.model_group)

        # Morph Group
        self.morph_group = QGroupBox(self.tr("morph", "groups"))
        morph_layout = QVBoxLayout()

        self.import_morphs_check = self._bind_checkbox(
            "import_morphs", setting_keys.IMPORT_MORPH_IMPORT_MORPHS, True, morph_layout
        )

        self.morph_group.setLayout(morph_layout)
        model_settings_layout.addWidget(self.morph_group)

        # Physics Group
        self.physics_group = QGroupBox(self.tr("physics_settings", "groups"))
        physics_layout = QVBoxLayout()
        self.import_physics_check = self._bind_checkbox(
            "import_physics",
            setting_keys.IMPORT_PHYSICS_IMPORT_PHYSICS,
            True,
            physics_layout,
            tooltip_key="import_physics",
        )
        self.physics_group.setLayout(physics_layout)
        model_settings_layout.addWidget(self.physics_group)

        # Other Settings Group (dev-only advanced toggles)
        self.other_group = QGroupBox(self.tr("other", "groups"))
        other_layout = QVBoxLayout()

        self.use_cpp_fast_load_check = self._bind_checkbox(
            "use_cpp_fast_load",
            setting_keys.IMPORT_NATIVE_USE_CPP_FAST_LOAD,
            False,
            other_layout,
            tooltip_key="use_cpp_fast_load",
        )
        self.use_cpp_vp2_ownership_check = self._bind_checkbox(
            "use_cpp_vp2_ownership",
            setting_keys.IMPORT_NATIVE_USE_CPP_VP2_OWNERSHIP,
            False,
            other_layout,
            tooltip_key="use_cpp_vp2_ownership",
        )

        self.use_cpp_rig_nodes_check = self._bind_checkbox(
            "use_cpp_rig_nodes",
            setting_keys.IMPORT_NATIVE_USE_CPP_RIG_NODES,
            False,
            other_layout,
            tooltip_key="use_cpp_rig_nodes",
        )

        self.other_group.setLayout(other_layout)

        # Animation Import Settings
        self.animation_settings_group = QGroupBox(self.tr("animation", "tabs"))
        anim_settings_layout = QVBoxLayout()

        # VMD FPS (Maya scene time unit for VMD import; VMD has no FPS metadata)
        fps_layout = QHBoxLayout()
        self.vmd_fps_label = QLabel(self.tr("vmd_fps", "fields"))
        fps_layout.addWidget(self.vmd_fps_label)
        self.vmd_fps_combo = QComboBox()
        self.vmd_fps_combo.addItems(["24", "30", "60"])
        vmd_fps_val = self.settings_service.get(setting_keys.IMPORT_ANIMATION_VMD_FPS, 30)
        try:
            vmd_fps_int = int(vmd_fps_val)
        except (TypeError, ValueError):
            vmd_fps_int = 30
        if vmd_fps_int not in (24, 30, 60):
            vmd_fps_int = 30
            self.settings_service.set(setting_keys.IMPORT_ANIMATION_VMD_FPS, 30)
        self.vmd_fps_combo.setCurrentText(str(vmd_fps_int))
        self.vmd_fps_combo.currentTextChanged.connect(
            lambda v: self.settings_service.set(setting_keys.IMPORT_ANIMATION_VMD_FPS, int(v))
        )
        self.vmd_fps_combo.setToolTip(self.tr("vmd_fps", "tooltips"))
        fps_layout.addWidget(self.vmd_fps_combo)
        fps_layout.addStretch()
        anim_settings_layout.addLayout(fps_layout)

        # Motion scale
        self.motion_scale_row = QWidget()
        motion_scale_layout = QHBoxLayout(self.motion_scale_row)
        motion_scale_layout.setContentsMargins(0, 0, 0, 0)
        self.motion_scale_label = QLabel(self.tr("motion_scale", "fields"))
        motion_scale_layout.addWidget(self.motion_scale_label)
        self.motion_scale_spin = QDoubleSpinBox()
        self.motion_scale_spin.setRange(0.001, 1000.0)
        self.motion_scale_spin.setDecimals(3)
        self.motion_scale_spin.setSingleStep(0.1)
        self.motion_scale_spin.setValue(self.settings_service.get(setting_keys.IMPORT_ANIMATION_MOTION_SCALE, 1.0))
        self.motion_scale_spin.valueChanged.connect(lambda v: self.settings_service.set(setting_keys.IMPORT_ANIMATION_MOTION_SCALE, v))
        self.motion_scale_spin.setToolTip(self.tr("motion_scale", "tooltips"))
        motion_scale_layout.addWidget(self.motion_scale_spin)
        motion_scale_layout.addStretch()
        anim_settings_layout.addWidget(self.motion_scale_row)

        self.bake_mode_check = self._bind_checkbox(
            "bake_mode", setting_keys.IMPORT_RIG_BAKE_MODE, False, anim_settings_layout, tooltip_key="bake_mode"
        )
        self.vmd_rotation_time_curve_check = self._bind_checkbox(
            "vmd_rotation_time_curve",
            setting_keys.IMPORT_ANIMATION_VMD_ROTATION_TIME_CURVE,
            True,
            anim_settings_layout,
            tooltip_key="vmd_rotation_time_curve",
        )
        self.native_physics_bake_check = self._bind_checkbox(
            "native_physics_bake",
            setting_keys.IMPORT_ANIMATION_USE_NATIVE_PHYSICS_BAKE,
            False,
            anim_settings_layout,
            tooltip_key="native_physics_bake",
        )
        self.reduce_bake_keys_check = self._bind_checkbox(
            "reduce_bake_keys",
            setting_keys.IMPORT_ANIMATION_REDUCE_BAKE_KEYS,
            False,
            anim_settings_layout,
            tooltip_key="reduce_bake_keys",
        )
        (
            self.reduce_quality_row,
            self.reduce_quality_slider,
            self.reduce_quality_value_label,
        ) = self._create_reduction_quality_row()
        anim_settings_layout.addWidget(self.reduce_quality_row)
        self.bake_mode_check.toggled.connect(self._sync_native_physics_bake_enabled)
        self.bake_mode_check.toggled.connect(self._sync_reduce_bake_keys_enabled)
        self.bake_mode_check.toggled.connect(self._sync_reduce_bake_quality_enabled)
        self.bake_mode_check.toggled.connect(self._sync_vmd_rotation_time_curve_enabled)
        self.create_mmd_control_rig_check.toggled.connect(
            self._sync_vmd_rotation_time_curve_enabled
        )
        self.reduce_bake_keys_check.toggled.connect(self._sync_reduce_bake_quality_enabled)
        self.use_cpp_fast_load_check.toggled.connect(self._sync_cpp_vp2_ownership_enabled)
        self._sync_native_physics_bake_enabled(self.bake_mode_check.isChecked())
        self._sync_reduce_bake_keys_enabled(self.bake_mode_check.isChecked())
        self._sync_reduce_bake_quality_enabled()
        self._sync_vmd_rotation_time_curve_enabled()
        self._sync_cpp_vp2_ownership_enabled(self.use_cpp_fast_load_check.isChecked())

        self.animation_settings_group.setLayout(anim_settings_layout)
        model_settings_layout.addWidget(self.animation_settings_group)

        # Other group at the bottom (dev-only)
        model_settings_layout.addWidget(self.other_group)

        model_settings_layout.addStretch()

        # Export Settings
        self._export_settings_tab = QScrollArea()
        self._export_settings_tab.setWidgetResizable(True)
        self._export_settings_tab.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        export_settings_widget = QWidget()
        export_settings_layout = QVBoxLayout(export_settings_widget)

        # Export format
        format_layout = QHBoxLayout()
        self.format_label = QLabel(self.tr("format", "fields"))
        format_layout.addWidget(self.format_label)
        self.export_format_combo = QComboBox()
        # PMD エクスポートは実装済みだが、UI ではリリース対象形式だけを表示する。
        self.export_format_combo.addItems(["pmx", "vmd"])
        current_format = self.settings_service.get(setting_keys.EXPORT_GENERAL_EXPORT_FORMAT, "pmx")
        self.export_format_combo.setCurrentText(current_format)
        self.export_format_combo.currentTextChanged.connect(self._on_export_format_changed)
        format_layout.addWidget(self.export_format_combo)
        format_layout.addStretch()
        export_settings_layout.addLayout(format_layout)

        # Apply scale checkbox
        self.apply_scale_check = self._bind_checkbox(
            "apply_scale", setting_keys.EXPORT_GENERAL_APPLY_SCALE, True, export_settings_layout
        )

        export_settings_layout.addStretch()
        self._export_settings_tab.setWidget(export_settings_widget)
        model_settings_layout.addWidget(self._export_settings_tab)

        # 右側：インポート/エクスポートセクション
        right_scroll = QScrollArea()
        right_scroll.setWidgetResizable(True)
        right_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)

        # Model Import Group (PMX/PMD)
        self.model_import_group = QGroupBox(self.tr("model_import", "groups"))
        model_import_layout = QFormLayout()

        # File path row
        self.import_path_edit = QLineEdit()
        saved_import_path = self.view_state.get("import_path", "")
        self.import_path_edit.setText(saved_import_path)
        self.import_path_button = QPushButton(self.tr("browse", "buttons"))
        import_path_layout = QHBoxLayout()
        import_path_layout.addWidget(self.import_path_edit)
        import_path_layout.addWidget(self.import_path_button)
        self.import_path_label = QLabel(self.tr("file_path", "labels"))
        model_import_layout.addRow(self.import_path_label, import_path_layout)

        # Connect signal to save path when changed
        self.import_path_edit.textChanged.connect(lambda text: self.view_state.set("import_path", text))

        # Import button with new file checkbox
        import_button_layout = QHBoxLayout()
        self.import_button = QPushButton(self.tr("import_model", "actions"))
        self.new_file_check = QCheckBox(self.tr("new_file", "checkboxes"))
        # NewFileチェックボックスの状態を読み込み
        saved_new_file = self.view_state.get("new_file_check", "false")
        self.new_file_check.setChecked(str(saved_new_file).lower() == "true")
        # 状態が変更されたら保存
        self.new_file_check.toggled.connect(lambda checked: self.view_state.set("new_file_check", str(checked)))
        import_button_layout.addWidget(self.import_button)
        import_button_layout.addWidget(self.new_file_check)
        import_button_layout.addStretch()
        model_import_layout.addRow(import_button_layout)

        self.model_import_group.setLayout(model_import_layout)
        right_layout.addWidget(self.model_import_group)

        # Animation Import Group (VMD)
        self.animation_group = QGroupBox(self.tr("animation_import", "groups"))
        animation_layout = QFormLayout()

        # VMD file path
        self.vmd_path_edit = QLineEdit()
        saved_vmd_path = self.view_state.get("vmd_path", "")
        self.vmd_path_edit.setText(saved_vmd_path)
        self.vmd_path_button = QPushButton(self.tr("browse", "buttons"))
        vmd_path_layout = QHBoxLayout()
        vmd_path_layout.addWidget(self.vmd_path_edit)
        vmd_path_layout.addWidget(self.vmd_path_button)
        self.vmd_file_label = QLabel(self.tr("vmd_file", "fields"))
        animation_layout.addRow(self.vmd_file_label, vmd_path_layout)

        self.vmd_path_edit.textChanged.connect(lambda text: self.view_state.set("vmd_path", text))

        self.clear_existing_motion_check = QCheckBox(self.tr("clear_existing_motion", "checkboxes"))
        self.clear_existing_motion_check.setChecked(self.settings_service.get(setting_keys.IMPORT_ANIMATION_CLEAR_EXISTING_MOTION, False))
        self.clear_existing_motion_check.toggled.connect(
            lambda v: self.settings_service.set(setting_keys.IMPORT_ANIMATION_CLEAR_EXISTING_MOTION, v)
        )
        self.clear_existing_motion_check.setToolTip(self.tr("clear_existing_motion", "tooltips"))
        animation_layout.addRow(self.clear_existing_motion_check)

        self.import_vmd_button = QPushButton(self.tr("import_animation", "actions"))
        animation_layout.addRow(self.import_vmd_button)

        self.animation_group.setLayout(animation_layout)
        right_layout.addWidget(self.animation_group)

        # Export Group
        self.export_group = QGroupBox(self.tr("export", "buttons"))
        export_layout = QFormLayout()

        self.export_path_edit = QLineEdit()
        saved_export_path = self.view_state.get("export_path", "")
        self.export_path_edit.setText(saved_export_path)
        self.export_path_button = QPushButton(self.tr("browse", "buttons"))
        export_path_layout = QHBoxLayout()
        export_path_layout.addWidget(self.export_path_edit)
        export_path_layout.addWidget(self.export_path_button)
        self.export_path_label = QLabel(self.tr("file_path", "labels"))
        export_layout.addRow(self.export_path_label, export_path_layout)

        self.export_path_edit.textChanged.connect(lambda text: self.view_state.set("export_path", text))

        self.export_button = QPushButton(self.tr("export", "buttons"))
        export_layout.addRow(self.export_button)

        self.export_group.setLayout(export_layout)
        right_layout.addWidget(self.export_group)

        # 統合履歴表示エリア
        self._setup_unified_history_area(right_layout)

        right_layout.addStretch()

        # スクロールエリアにウィジェットを設定
        right_scroll.setWidget(right_widget)

        # 左側のスクロールエリアにタブウィジェットを設定
        left_scroll.setWidget(self.left_widget)

        # スプリッターに左右のウィジェットを追加
        splitter.addWidget(left_scroll)
        splitter.addWidget(right_scroll)

        # 初期のスプリッター比率を設定（左:右 = 1:2）
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)

        main_layout.addWidget(splitter)

        # import_models is always ON in behavior; checkbox removed from UI.
        self.import_models_check.setVisible(False)

        # Dev-only controls: shown only when development_mode=True.
        # Export UI/entry is also develop-mode only (export_group via _apply_export_visibility).
        self._dev_only_widgets = [
            self.scale_row,
            self.disable_backface_culling_check,
            self.texture_row,
            self.uv_row,
            self.morph_group,
            self.other_group,
            self.use_cpp_fast_load_check,
            self.use_cpp_vp2_ownership_check,
            self.use_cpp_rig_nodes_check,
            self.motion_scale_row,
            self.vmd_rotation_time_curve_check,
            self._export_settings_tab,
        ]
        self._apply_dev_mode_visibility()

    def _bind_checkbox(self, tr_key, settings_key, default, layout, tooltip_key=None):
        cb = QCheckBox(self.tr(tr_key, "checkboxes"))
        cb.setChecked(self.settings_service.get(settings_key, default))
        cb.toggled.connect(lambda v, k=settings_key: self.settings_service.set(k, v))
        if tooltip_key:
            cb.setToolTip(self.tr(tooltip_key, "tooltips"))
        layout.addWidget(cb)
        return cb

    def _sync_native_physics_bake_enabled(self, bake_mode_enabled):
        """Native physics bake is a VMD bake-mode option, not a model import option."""
        self.native_physics_bake_check.setEnabled(bool(bake_mode_enabled))

    def _sync_cpp_vp2_ownership_enabled(self, fast_load_enabled):
        """VP2 ownership is available only when C++ Fast Load is enabled."""
        enabled = bool(fast_load_enabled)
        self.use_cpp_vp2_ownership_check.setEnabled(enabled)
        if not enabled and self.use_cpp_vp2_ownership_check.isChecked():
            self.use_cpp_vp2_ownership_check.setChecked(False)

    def _sync_reduce_bake_keys_enabled(self, bake_mode_enabled):
        """Reduce Bake Keys is an opt-in control available only for Bake Motion."""
        self.reduce_bake_keys_check.setEnabled(bool(bake_mode_enabled))

    def _sync_reduce_bake_quality_enabled(self, *_args):
        """Enable Reduce Quality only for Bake Motion with key reduction enabled."""
        enabled = self.bake_mode_check.isChecked() and self.reduce_bake_keys_check.isChecked()
        row = getattr(self, "reduce_quality_row", None)
        slider = getattr(self, "reduce_quality_slider", None)
        if row is not None:
            row.setVisible(enabled)
        if slider is not None:
            slider.setEnabled(enabled)

    def _sync_vmd_rotation_time_curve_enabled(self, *_args):
        """Enable sparse rotation time curves only for direct Control Rig import."""
        enabled = (
            self.create_mmd_control_rig_check.isChecked()
            and not self.bake_mode_check.isChecked()
        )
        self.vmd_rotation_time_curve_check.setEnabled(enabled)

    def _create_reduction_quality_row(self):
        """Create the embedded 0..1 Reduce Quality slider row."""
        row = QWidget(self.animation_settings_group)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        label = QLabel(self.tr("reduce_quality", "fields"), row)
        layout.addWidget(label)
        slider = QSlider(Qt.Horizontal, row)
        slider.setRange(0, 100)
        quality = normalize_reduce_bake_quality(
            self.settings_service.get(setting_keys.IMPORT_ANIMATION_REDUCE_QUALITY, _REDUCE_BAKE_QUALITY_DEFAULT)
        )
        slider.setValue(int(round(quality * 100.0)))
        value_label = QLabel(row)
        value_label.setMinimumWidth(38)
        value_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        layout.addWidget(slider)
        layout.addWidget(value_label)
        layout.addStretch()
        slider.valueChanged.connect(self._on_reduce_quality_changed)
        self._set_reduce_quality_value_label(value_label, slider.value())
        slider.setToolTip(self.tr("reduce_quality", "tooltips"))
        setattr(self, "reduce_quality_label", label)
        return row, slider, value_label

    def _set_reduce_quality_value_label(self, label, slider_value):
        """Display the slider's quality value with stable two-decimal precision."""
        label.setText(f"{float(slider_value) / 100.0:.2f}")

    def _on_reduce_quality_changed(self, slider_value):
        """Persist slider quality and keep its compact value label in sync."""
        quality = normalize_reduce_bake_quality(float(slider_value) / 100.0)
        self.settings_service.set(setting_keys.IMPORT_ANIMATION_REDUCE_QUALITY, quality)
        self._set_reduce_quality_value_label(self.reduce_quality_value_label, slider_value)

    def _apply_dev_mode_visibility(self):
        """dev-only UI controls の表示/非表示を development_mode 設定に合わせる。"""
        is_dev = self.settings_service.get(setting_keys.UI_GENERAL_DEVELOPMENT_MODE, False)
        for widget in self._dev_only_widgets:
            widget.setVisible(is_dev)
        # Import scale: normal mode displays 1.0 without overwriting the persisted value.
        self._sync_import_scale_control(is_dev)
        self._sync_reduce_bake_quality_control()
        # Export entry also depends on develop mode.
        self._apply_export_visibility()

    def _sync_import_scale_control(self, is_dev):
        """Sync scale spin display for the current mode without clobbering settings.

        Normal mode shows DEFAULT 1.0 while the valueChanged handler is blocked so a
        previously persisted development scale remains stored. Development mode
        reloads the persisted value into the control (binding remains active).
        """
        if not hasattr(self, "scale_spin"):
            return
        if is_dev:
            if hasattr(self.settings_service, "resolve_import_scale"):
                value = self.settings_service.resolve_import_scale()
            else:
                value = self.settings_service.get(setting_keys.IMPORT_GENERAL_SCALE_FACTOR, 1.0)
        else:
            value = 1.0
        blocked = self.scale_spin.blockSignals(True)
        try:
            self.scale_spin.setValue(value)
        finally:
            self.scale_spin.blockSignals(blocked)

    def _sync_reduce_bake_quality_control(self):
        """Reload persisted Reduce Quality without writing it back."""
        if not hasattr(self, "reduce_quality_slider"):
            return
        quality = normalize_reduce_bake_quality(
            self.settings_service.get(setting_keys.IMPORT_ANIMATION_REDUCE_QUALITY, _REDUCE_BAKE_QUALITY_DEFAULT)
        )
        blocked = self.reduce_quality_slider.blockSignals(True)
        try:
            slider_value = int(round(quality * 100.0))
            self.reduce_quality_slider.setValue(slider_value)
            self._set_reduce_quality_value_label(self.reduce_quality_value_label, slider_value)
        finally:
            self.reduce_quality_slider.blockSignals(blocked)

    def _on_export_format_changed(self, export_format):
        """エクスポート形式を保存し、利用可能な export UI だけを表示する。"""
        self.settings_service.set(setting_keys.EXPORT_GENERAL_EXPORT_FORMAT, export_format)
        self._apply_export_visibility()

    def _apply_export_visibility(self):
        """Develop モードかつ対応済み export 形式のときだけ export UI を表示する。"""
        if not hasattr(self, "export_group"):
            return
        is_dev = self.settings_service.get(setting_keys.UI_GENERAL_DEVELOPMENT_MODE, False)
        export_format = self.settings_service.get(setting_keys.EXPORT_GENERAL_EXPORT_FORMAT, "pmx")
        self.export_group.setVisible(bool(is_dev) and export_format in {"pmx", "vmd"})

    def get_custom_namespace(self):
        """カスタムnamespace名を取得"""
        if (
            self.use_namespace_check.isChecked()
            and self.custom_namespace_check.isChecked()
            and self.namespace_edit.text().strip()
        ):
            return self.namespace_edit.text().strip()
        return None

    def retranslateUi(self):
        """言語切り替え時にUIを再翻訳"""
        # Labels
        if hasattr(self, "scale_label"):
            self.scale_label.setText(self.tr("import_scale", "fields"))
        if hasattr(self, "texture_search_label"):
            self.texture_search_label.setText(self.tr("texture_search_path", "fields"))
        if hasattr(self, "uv_set_label"):
            self.uv_set_label.setText(self.tr("uv_set_name", "fields"))
        if hasattr(self, "start_frame_label"):
            self.start_frame_label.setText(self.tr("start_frame", "fields"))
        if hasattr(self, "vmd_fps_label"):
            self.vmd_fps_label.setText(self.tr("vmd_fps", "fields"))
        if hasattr(self, "motion_scale_label"):
            self.motion_scale_label.setText(self.tr("motion_scale", "fields"))
        if hasattr(self, "format_label"):
            self.format_label.setText(self.tr("format", "fields"))
        if hasattr(self, "import_path_label"):
            self.import_path_label.setText(self.tr("file_path", "labels"))
        if hasattr(self, "vmd_file_label"):
            self.vmd_file_label.setText(self.tr("vmd_file", "fields"))
        if hasattr(self, "export_path_label"):
            self.export_path_label.setText(self.tr("file_path", "labels"))


        # GroupBoxes
        if hasattr(self, "general_group"):
            self.general_group.setTitle(self.tr("general", "groups"))
        if hasattr(self, "model_group"):
            self.model_group.setTitle(self.tr("model", "groups"))
        if hasattr(self, "morph_group"):
            self.morph_group.setTitle(self.tr("morph", "groups"))
        if hasattr(self, "physics_group"):
            self.physics_group.setTitle(self.tr("physics_settings", "groups"))
        if hasattr(self, "other_group"):
            self.other_group.setTitle(self.tr("other", "groups"))
        if hasattr(self, "model_import_group"):
            self.model_import_group.setTitle(self.tr("model_import", "groups"))
        if hasattr(self, "animation_group"):
            self.animation_group.setTitle(self.tr("animation_import", "groups"))
        if hasattr(self, "export_group"):
            self.export_group.setTitle(self.tr("export", "buttons"))
        if hasattr(self, "history_group"):
            self.history_group.setTitle(self.tr("file_history", "groups"))
        if hasattr(self, "clear_history_button"):
            self.clear_history_button.setText(self.tr("clear_history", "buttons"))

        # CheckBoxes
        self.use_namespace_check.setText(self.tr("use_namespace", "checkboxes"))
        self.custom_namespace_check.setText(self.tr("custom_namespace", "checkboxes"))
        self.namespace_edit.setPlaceholderText(self.tr("namespace_placeholder", "labels"))
        self.import_models_check.setText(self.tr("import_models", "checkboxes"))
        self.create_mmd_shaders_check.setText(self.tr("create_mmd_shaders", "checkboxes"))
        self.separate_meshes_check.setText(self.tr("separate_meshes", "checkboxes"))
        if hasattr(self, "auto_resolve_textures_check"):
            self.auto_resolve_textures_check.setText(self.tr("auto_resolve_textures", "checkboxes"))
        self.disable_backface_culling_check.setText(self.tr("disable_backface_culling", "checkboxes"))
        self.import_morphs_check.setText(self.tr("import_morphs", "checkboxes"))
        self.import_physics_check.setText(self.tr("import_physics", "checkboxes"))
        self.bake_mode_check.setText(self.tr("bake_mode", "checkboxes"))
        self.native_physics_bake_check.setText(self.tr("native_physics_bake", "checkboxes"))
        self.reduce_bake_keys_check.setText(self.tr("reduce_bake_keys", "checkboxes"))
        if hasattr(self, "reduce_quality_label"):
            self.reduce_quality_label.setText(self.tr("reduce_quality", "fields"))
        self.clear_existing_motion_check.setText(self.tr("clear_existing_motion", "checkboxes"))
        if hasattr(self, "create_mmd_control_rig_check"):
            self.create_mmd_control_rig_check.setText(self.tr("create_mmd_control_rig", "checkboxes"))
        if hasattr(self, "vmd_rotation_time_curve_check"):
            self.vmd_rotation_time_curve_check.setText(
                self.tr("vmd_rotation_time_curve", "checkboxes")
            )
        self.use_cpp_rig_nodes_check.setText(self.tr("use_cpp_rig_nodes", "checkboxes"))
        self.use_cpp_fast_load_check.setText(self.tr("use_cpp_fast_load", "checkboxes"))
        self.use_cpp_vp2_ownership_check.setText(
            self.tr("use_cpp_vp2_ownership", "checkboxes")
        )
        self.apply_scale_check.setText(self.tr("apply_scale", "checkboxes"))
        self.new_file_check.setText(self.tr("new_file", "checkboxes"))

        # Tooltips
        self.scale_spin.setToolTip(self.tr("import_scale", "tooltips"))
        self.use_namespace_check.setToolTip(self.tr("use_namespace", "tooltips"))
        self.create_mmd_shaders_check.setToolTip(self.tr("create_mmd_shaders", "tooltips"))
        self.separate_meshes_check.setToolTip(self.tr("separate_meshes", "tooltips"))
        self.auto_resolve_textures_check.setToolTip(self.tr("auto_resolve_textures", "tooltips"))
        self.disable_backface_culling_check.setToolTip(self.tr("disable_backface_culling", "tooltips"))
        self.import_physics_check.setToolTip(self.tr("import_physics", "tooltips"))
        self.bake_mode_check.setToolTip(self.tr("bake_mode", "tooltips"))
        self.native_physics_bake_check.setToolTip(self.tr("native_physics_bake", "tooltips"))
        self.reduce_bake_keys_check.setToolTip(self.tr("reduce_bake_keys", "tooltips"))
        if hasattr(self, "reduce_quality_slider"):
            self.reduce_quality_slider.setToolTip(self.tr("reduce_quality", "tooltips"))
        self.clear_existing_motion_check.setToolTip(self.tr("clear_existing_motion", "tooltips"))
        if hasattr(self, "create_mmd_control_rig_check"):
            self.create_mmd_control_rig_check.setToolTip(self.tr("create_mmd_control_rig", "tooltips"))
        if hasattr(self, "vmd_rotation_time_curve_check"):
            self.vmd_rotation_time_curve_check.setToolTip(
                self.tr("vmd_rotation_time_curve", "tooltips")
            )
        self.use_cpp_rig_nodes_check.setToolTip(self.tr("use_cpp_rig_nodes", "tooltips"))
        self.use_cpp_fast_load_check.setToolTip(self.tr("use_cpp_fast_load", "tooltips"))
        self.use_cpp_vp2_ownership_check.setToolTip(
            self.tr("use_cpp_vp2_ownership", "tooltips")
        )
        if hasattr(self, "animation_start_frame"):
            self.animation_start_frame.setToolTip(self.tr("start_frame", "tooltips"))
        self.vmd_fps_combo.setToolTip(self.tr("vmd_fps", "tooltips"))
        self.motion_scale_spin.setToolTip(self.tr("motion_scale", "tooltips"))

        # Buttons
        self.import_path_button.setText(self.tr("browse", "buttons"))
        self.vmd_path_button.setText(self.tr("browse", "buttons"))
        self.export_path_button.setText(self.tr("browse", "buttons"))
        self.import_button.setText(self.tr("import_model", "actions"))
        self.import_vmd_button.setText(self.tr("import_animation", "actions"))
        self.export_button.setText(self.tr("export", "buttons"))

        # Tab widget texts
        if hasattr(self, "animation_settings_group"):
            self.animation_settings_group.setTitle(self.tr("animation", "tabs"))

    def _setup_unified_history_area(self, layout):
        """統合履歴表示エリアを設定"""
        self.history_group = QGroupBox(self.tr("file_history", "groups"))
        history_layout = QVBoxLayout()

        # 統合履歴リスト
        self.unified_history_list = QListWidget()
        self.unified_history_list.setMaximumHeight(250)

        # ダブルクリックでファイルタイプに応じて適切なUIに設定
        self.unified_history_list.itemDoubleClicked.connect(self._on_history_item_double_clicked)

        history_layout.addWidget(self.unified_history_list)

        # 履歴クリアボタン
        self.clear_history_button = QPushButton(self.tr("clear_history", "buttons"))
        self.clear_history_button.clicked.connect(self._clear_all_history)
        history_layout.addWidget(self.clear_history_button)

        self.history_group.setLayout(history_layout)
        layout.addWidget(self.history_group)

        # 初期化時に履歴を読み込み
        self.refresh_unified_history()

    def _on_history_item_double_clicked(self, item):
        """履歴アイテムがダブルクリックされた時の処理"""
        file_path = item.data(Qt.UserRole)
        file_type = item.data(Qt.UserRole + 1)

        if file_type == "import":
            self.import_path_edit.setText(file_path)
        elif file_type == "vmd":
            self.vmd_path_edit.setText(file_path)
        elif file_type == "export":
            self.export_path_edit.setText(file_path)

    def _clear_all_history(self):
        """すべての履歴をクリア"""
        self.view_state.clear_file_history()
        self.refresh_unified_history()

    def refresh_unified_history(self):
        """統合履歴リストを更新"""
        self.unified_history_list.clear()

        history_limit = self.settings_service.resolve_file_history_limit()
        all_items = self.view_state.load_file_history(history_limit)
        display_prefixes = {
            "import": "Model",
            "vmd": "Animation",
            "export": "Export",
        }

        # リストに追加（最新のものから表示）
        for item_data in all_items:
            prefix = display_prefixes[item_data["type"]]
            item = QListWidgetItem(f"[{prefix}] {os.path.basename(item_data['path'])}")
            item.setData(Qt.UserRole, item_data["path"])
            item.setData(Qt.UserRole + 1, item_data["type"])
            item.setToolTip(item_data["path"])

            # タイプによって色分け
            if item_data["type"] == "import":
                item.setForeground(QColor(100, 200, 255))  # 水色
            elif item_data["type"] == "vmd":
                item.setForeground(QColor(255, 200, 100))  # オレンジ
            elif item_data["type"] == "export":
                item.setForeground(QColor(100, 255, 100))  # 緑

            self.unified_history_list.addItem(item)

    def add_import_path_to_history(self, path):
        """インポートパスを履歴に追加"""
        self.view_state.save_file_history("import", path)
        # 履歴リストを更新
        self.refresh_unified_history()

    def add_vmd_path_to_history(self, path):
        """アニメーションパスを履歴に追加"""
        self.view_state.save_file_history("vmd", path)
        # 履歴リストを更新
        self.refresh_unified_history()

    def add_export_path_to_history(self, path):
        """エクスポートパスを履歴に追加"""
        self.view_state.save_file_history("export", path)
        # 履歴リストを更新
        self.refresh_unified_history()
