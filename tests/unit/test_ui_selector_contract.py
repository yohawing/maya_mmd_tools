"""Stable objectName contracts for the model-authoring UI controls."""

import json
import os
import re
from collections import Counter
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from mmd_tools.ui.qt_compat import (
        QApplication,
        QCheckBox,
        QComboBox,
        QLineEdit,
        QListWidget,
        QPushButton,
        QSpinBox,
        QTableWidget,
        QTextEdit,
        QWidget,
    )
except ImportError:  # pragma: no cover - the release runner may omit Qt.
    QApplication = QCheckBox = QComboBox = QLineEdit = QListWidget = object
    QPushButton = QSpinBox = QTableWidget = QTextEdit = QWidget = object


_HAS_REAL_QT_WIDGETS = callable(getattr(QApplication, "instance", None)) and callable(
    getattr(QWidget, "setObjectName", None)
)
_DYNAMIC_PHYSICS_SELECTOR_NAMES = {
    "jointRigidBodyACombo",
    "jointRigidBodyBCombo",
    "physicsJointNameEdit",
    "physicsJointNameEnglishEdit",
    "physicsJointPositionEdit",
    "physicsJointRotationEdit",
    "physicsJointRotationMaxEdit",
    "physicsJointRotationMinEdit",
    "physicsJointSpringRotationEdit",
    "physicsJointSpringTranslationEdit",
    "physicsJointTranslationMaxEdit",
    "physicsJointTranslationMinEdit",
    "physicsJointTypeCombo",
    "physicsRigidAngularDampingEdit",
    "physicsRigidCollisionGroupSpin",
    "physicsRigidCollisionMaskEdit",
    "physicsRigidFrictionEdit",
    "physicsRigidLinearDampingEdit",
    "physicsRigidMassEdit",
    "physicsRigidNameEdit",
    "physicsRigidNameEnglishEdit",
    "physicsRigidPhysicsModeCombo",
    "physicsRigidPositionEdit",
    "physicsRigidRestitutionEdit",
    "physicsRigidRotationEdit",
    "physicsRigidShapeCombo",
    "physicsRigidShapeSizeEdit",
    "rigidRelatedBoneCombo",
}


def test_manifest_object_names_are_unique_source_contracts():
    """Keep selector spelling covered when the pure test runner uses Qt stubs."""
    repository_root = Path(__file__).resolve().parents[2]
    manifest = json.loads(
        (repository_root / "tools" / "ui_coverage_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    required_names = {
        entry["selector"].removeprefix("objectName=")
        for entry in (*manifest["tabs"], *manifest["surfaces"])
        if entry.get("selector")
    }
    declared_names = []
    for source_path in (repository_root / "mmd_tools" / "ui").rglob("*.py"):
        declared_names.extend(
            re.findall(
                r'\.setObjectName\(\s*["\']([^"\']+)',
                source_path.read_text(encoding="utf-8"),
            )
        )

    counts = Counter(declared_names)
    # Physics editor names are assigned from a data table and are exercised by
    # the real-Qt contract below. All literal selectors remain statically gated.
    literal_required_names = required_names & counts.keys()
    assert literal_required_names
    assert required_names - literal_required_names == _DYNAMIC_PHYSICS_SELECTOR_NAMES
    assert not {
        name: counts[name] for name in literal_required_names if counts[name] != 1
    }


@pytest.fixture(scope="module")
def qapp():
    instance = getattr(QApplication, "instance", lambda: None)()
    if instance is not None:
        return instance
    try:
        return QApplication([])
    except TypeError:
        # A prior headless unit module may have installed the shared Qt stub.
        return QApplication()


def _assert_selectors(root, selectors):
    """Check selector spelling, uniqueness, and concrete Qt widget type."""
    names = []
    for attribute, expected_name, expected_type in selectors:
        widget = getattr(root, attribute)
        assert isinstance(widget, expected_type), attribute
        assert widget.objectName() == expected_name, attribute
        assert expected_name, attribute
        names.append(expected_name)
    assert len(names) == len(set(names)), root.objectName()
    return names


@pytest.mark.skipif(not _HAS_REAL_QT_WIDGETS, reason="real Qt widgets unavailable")
def test_authoring_tabs_have_stable_action_list_table_and_swatch_selectors(qapp):
    from mmd_tools.ui.tabs.bone_tab import BoneTab
    from mmd_tools.ui.tabs.display_pane_tab import DisplayPaneTab
    from mmd_tools.ui.tabs.material_tab import MaterialTab
    from mmd_tools.ui.tabs.morph_tab import MorphTab

    names = _assert_selectors(
        MaterialTab(),
        (
            ("authoring_toolbar", "materialAuthoringToolbar", QWidget),
            ("refresh_btn", "materialRefreshButton", QPushButton),
            ("create_btn", "materialCreateButton", QPushButton),
            ("duplicate_btn", "materialDuplicateButton", QPushButton),
            ("delete_btn", "materialDeleteButton", QPushButton),
            ("reindex_up_btn", "materialMoveUpButton", QPushButton),
            ("reindex_down_btn", "materialMoveDownButton", QPushButton),
            ("material_list", "materialList", QListWidget),
            ("search_edit", "materialSearchEdit", QLineEdit),
            ("diffuse_color_widget", "diffuseColorSwatch", QWidget),
            ("specular_color_widget", "specularColorSwatch", QWidget),
            ("ambient_color_widget", "ambientColorSwatch", QWidget),
            ("edge_color_widget", "edgeColorSwatch", QWidget),
            ("texture_browse_btn", "materialTextureBrowseButton", QPushButton),
            ("sphere_map_browse_btn", "materialSphereMapBrowseButton", QPushButton),
            ("apply_btn", "materialApplyButton", QPushButton),
            ("reset_btn", "materialResetButton", QPushButton),
        ),
    )
    names += _assert_selectors(
        BoneTab(),
        (
            ("bone_authoring_toolbar", "boneAuthoringToolbar", QWidget),
            ("refresh_btn", "boneRefreshButton", QPushButton),
            ("reindex_up_btn", "boneMoveUpButton", QPushButton),
            ("reindex_down_btn", "boneMoveDownButton", QPushButton),
            ("reset_authoring_btn", "boneResetAuthoringButton", QPushButton),
            ("bone_list", "boneList", QListWidget),
            ("search_edit", "boneSearchEdit", QLineEdit),
            ("ik_authoring_toolbar", "boneIkAuthoringToolbar", QWidget),
            ("add_ik_link_btn", "boneAddIkLinkButton", QPushButton),
            ("remove_ik_link_btn", "boneRemoveIkLinkButton", QPushButton),
            ("move_up_btn", "boneMoveIkLinkUpButton", QPushButton),
            ("move_down_btn", "boneMoveIkLinkDownButton", QPushButton),
            ("ik_links_table", "boneIkLinksTable", QTableWidget),
            ("select_ik_target_btn", "boneSelectIkTargetButton", QPushButton),
            ("select_grant_parent_btn", "boneSelectGrantParentButton", QPushButton),
            ("apply_btn", "boneApplyButton", QPushButton),
            ("reset_btn", "boneResetButton", QPushButton),
        ),
    )
    names += _assert_selectors(
        MorphTab(),
        (
            ("morph_refresh_toolbar", "morphRefreshToolbar", QWidget),
            ("refresh_morphs_btn", "morphRefreshButton", QPushButton),
            ("morph_authoring_toolbar", "morphAuthoringToolbar", QWidget),
            ("create_morph_btn", "morphCreateButton", QPushButton),
            ("delete_morph_btn", "morphDeleteButton", QPushButton),
            ("move_morph_up_btn", "morphMoveUpButton", QPushButton),
            ("move_morph_down_btn", "morphMoveDownButton", QPushButton),
            ("morph_list", "morphList", QListWidget),
            ("search_edit", "morphSearchEdit", QLineEdit),
            ("reset_slider_btn", "morphResetSliderButton", QPushButton),
            ("reset_all_btn", "morphResetAllButton", QPushButton),
            ("apply_btn", "morphApplyButton", QPushButton),
            ("reset_btn", "morphResetButton", QPushButton),
        ),
    )
    names += _assert_selectors(
        DisplayPaneTab(),
        (
            ("frame_authoring_toolbar", "displayFrameAuthoringToolbar", QWidget),
            ("add_frame_btn", "displayAddFrameButton", QPushButton),
            ("delete_frame_btn", "displayDeleteFrameButton", QPushButton),
            ("move_frame_up_btn", "displayMoveFrameUpButton", QPushButton),
            ("move_frame_down_btn", "displayMoveFrameDownButton", QPushButton),
            ("refresh_toolbar", "displayRefreshToolbar", QWidget),
            ("refresh_btn", "displayRefreshButton", QPushButton),
            ("frame_list", "displayFrameList", QListWidget),
            ("item_element_toolbar", "displayItemElementToolbar", QWidget),
            ("add_element_btn", "displayAddElementButton", QPushButton),
            ("item_authoring_toolbar", "displayItemAuthoringToolbar", QWidget),
            ("delete_item_btn", "displayDeleteItemButton", QPushButton),
            ("move_item_up_btn", "displayMoveItemUpButton", QPushButton),
            ("move_item_down_btn", "displayMoveItemDownButton", QPushButton),
            ("item_table", "displayItemTable", QTableWidget),
            ("apply_btn", "displayApplyButton", QPushButton),
            ("reset_btn", "displayResetButton", QPushButton),
        ),
    )
    assert len(names) == len(set(names))


@pytest.mark.skipif(not _HAS_REAL_QT_WIDGETS, reason="real Qt widgets unavailable")
def test_physics_settings_and_validation_selectors(qapp):
    from mmd_tools.ui.tabs.physics_tab import PhysicsTab
    from mmd_tools.ui.tabs.settings_tab import SettingsTab
    from mmd_tools.ui.validation_console import ValidationConsole

    names = _assert_selectors(
        PhysicsTab(),
        (
            ("refresh_btn", "physicsRefreshButton", QPushButton),
            ("create_btn", "physicsCreateButton", QPushButton),
            ("duplicate_btn", "physicsDuplicateButton", QPushButton),
            ("delete_btn", "physicsDeleteButton", QPushButton),
            ("rigid_body_list", "rigidBodyList", QListWidget),
            ("rigid_body_search_edit", "rigidBodySearchEdit", QLineEdit),
            ("joint_list", "jointList", QListWidget),
            ("joint_search_edit", "jointSearchEdit", QLineEdit),
            ("rigid_related_bone_combo", "rigidRelatedBoneCombo", QComboBox),
            ("joint_body_a_combo", "jointRigidBodyACombo", QComboBox),
            ("joint_body_b_combo", "jointRigidBodyBCombo", QComboBox),
            ("apply_btn", "physicsApplyButton", QPushButton),
            ("reset_btn", "physicsResetButton", QPushButton),
        ),
    )
    names += _assert_selectors(
        SettingsTab(),
        (
            ("save_settings_btn", "settingsSaveButton", QPushButton),
            ("reset_settings_btn", "settingsResetButton", QPushButton),
            ("export_settings_btn", "settingsExportButton", QPushButton),
            ("import_settings_btn", "settingsImportButton", QPushButton),
            ("development_mode_check", "settingsDevelopmentModeCheck", QCheckBox),
            ("language_combo", "settingsLanguageCombo", QComboBox),
            ("file_history_limit_spin", "settingsFileHistoryLimitSpin", QSpinBox),
            ("command_port_spin", "settingsCommandPortSpin", QSpinBox),
            ("open_command_port_btn", "settingsOpenCommandPortButton", QPushButton),
            ("logging_enabled_check", "settingsLoggingEnabledCheck", QCheckBox),
            ("log_level_combo", "settingsLogLevelCombo", QComboBox),
            ("log_file_path_edit", "settingsLogFilePathEdit", QLineEdit),
            ("log_file_browse_btn", "settingsLogFileBrowseButton", QPushButton),
        ),
    )
    validation = ValidationConsole()
    assert validation.objectName() == "validationConsole"
    names += _assert_selectors(
        validation,
        (
            ("filter_combo", "validationFilterCombo", QComboBox),
            ("issue_list", "validationIssueList", QListWidget),
            ("detail_text", "validationDetailEdit", QTextEdit),
            ("acknowledge_check", "validationAcknowledgeCheck", QCheckBox),
            ("revalidate_button", "validationRevalidateButton", QPushButton),
            ("copy_button", "validationCopyButton", QPushButton),
            ("save_button", "validationSaveButton", QPushButton),
        ),
    )
    assert len(names) == len(set(names))
