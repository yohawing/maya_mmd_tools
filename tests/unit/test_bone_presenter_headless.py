"""BonePresenterのMaya非依存ロジックを検証するheadless unitテスト。"""

import unittest
from unittest.mock import Mock, patch

from tests.common.maya_stub import install_headless_ui_stubs

install_headless_ui_stubs()

from mmd_tools.core.constants import (  # noqa: E402
    ATTR_MMD_BONE_FLAGS,
    ATTR_MMD_BONE_INDEX,
    ATTR_MMD_BONE_NAME,
    ATTR_MMD_BONE_NAME_EN,
    ATTR_MMD_BONE_OFFSET,
    ATTR_MMD_CONNECTION_BONE,
    ATTR_MMD_DEFORM_LAYER,
    ATTR_MMD_EXTERNAL_PARENT_KEY,
    ATTR_MMD_IK_LINKS,
    ATTR_MMD_IK_LIMIT_ANGLE,
    ATTR_MMD_IK_LOOP,
    ATTR_MMD_IK_TARGET_INDEX,
)
from mmd_tools.core.pmx_data.bone import PmxBoneFlag  # noqa: E402
from mmd_tools.core.model_authoring_spec import (  # noqa: E402
    MmdBoneSpec,
    MmdModelAuthoringSpec,
    MmdModelSpec,
)
from mmd_tools.core.bone_authoring import BoneResetPlan  # noqa: E402
from mmd_tools.ui.presenters import bone_presenter as bone_presenter_module  # noqa: E402
from mmd_tools.ui.presenters.bone_presenter import BonePresenter  # noqa: E402
from mmd_tools.ui.qt_compat import QMessageBox  # noqa: E402
from mmd_tools.ui.translations import UITranslator  # noqa: E402

UITranslator.instance().set_language("en")

TEST_MODEL = "test_mmd_model"
TEST_BONE = "center_jnt"


class _FakeSignal:
    def __init__(self):
        self.callbacks = []

    def connect(self, callback):
        self.callbacks.append(callback)


class _FakeButton:
    def __init__(self):
        self.clicked = _FakeSignal()
        self.enabled = True

    def setEnabled(self, enabled):
        self.enabled = enabled


class _FakeLineEdit:
    def __init__(self, text=""):
        self._text = text
        self.enabled = True
        self.textChanged = _FakeSignal()

    def text(self):
        return self._text

    def setText(self, text):
        self._text = text

    def clear(self):
        self._text = ""

    def setEnabled(self, enabled):
        self.enabled = enabled


class _FakeCheckBox:
    def __init__(self, checked=False):
        self._checked = checked
        self.toggled = _FakeSignal()

    def isChecked(self):
        return self._checked

    def setChecked(self, checked):
        self._checked = checked


class _FakeSpinBox:
    def __init__(self, value=0):
        self._value = value
        self.enabled = True
        self.visible = True

    def value(self):
        return self._value

    def setValue(self, value):
        self._value = value

    def setEnabled(self, enabled):
        self.enabled = enabled

    def setVisible(self, visible):
        self.visible = visible


class _FakeComboBox:
    def __init__(self, index=0):
        self._index = index
        self.currentIndexChanged = _FakeSignal()

    def currentIndex(self):
        return self._index

    def setCurrentIndex(self, index):
        self._index = index


class _FakeGroup:
    def __init__(self):
        self.visible = None

    def setVisible(self, visible):
        self.visible = visible


class _FakeLabel(_FakeGroup):
    def __init__(self):
        super().__init__()
        self.text = ""

    def setText(self, text):
        self.text = text


class _FakeList:
    def __init__(self):
        self.clear_calls = 0
        self.items = []
        self.current_row = -1
        self.currentItemChanged = _FakeSignal()
        self.itemSelectionChanged = _FakeSignal()

    def clear(self):
        self.clear_calls += 1
        self.items.clear()

    def addItem(self, item):
        self.items.append(item)

    def selectedItems(self):
        return []

    def currentRow(self):
        return self.current_row

    def takeItem(self, row):
        return self.items.pop(row)

    def insertItem(self, row, item):
        self.items.insert(row, item)

    def setCurrentItem(self, item):
        self.current_row = self.items.index(item)


class _FakeListItem:
    def __init__(self, text, data=None):
        self._text = text
        self._data = data
        self._tooltip = ""
        self.hidden = None

    def text(self):
        return self._text

    def setText(self, text):
        self._text = text

    def data(self, _role):
        return self._data

    def setToolTip(self, tooltip):
        self._tooltip = tooltip

    def toolTip(self):
        return self._tooltip

    def setHidden(self, hidden):
        self.hidden = hidden


class _FakeTable:
    def rowCount(self):
        return 0

    def currentRow(self):
        return -1


class _FakeView:
    def __init__(self):
        self.bone_list = _FakeList()
        self.refresh_btn = _FakeButton()
        self.bind_pose_btn = _FakeButton()
        self.register_joint_btn = _FakeButton()
        self.capture_rest_btn = _FakeButton()
        self.reindex_up_btn = _FakeButton()
        self.reindex_down_btn = _FakeButton()
        self.apply_reindex_btn = _FakeButton()
        self.unregister_btn = _FakeButton()
        self.reset_authoring_btn = _FakeButton()
        self.animation_warning_label = _FakeLabel()
        self.search_edit = _FakeLineEdit()
        self.select_ik_target_btn = _FakeButton()
        self.select_grant_parent_btn = _FakeButton()
        self.add_ik_link_btn = _FakeButton()
        self.remove_ik_link_btn = _FakeButton()
        self.move_up_btn = _FakeButton()
        self.move_down_btn = _FakeButton()
        self.apply_btn = _FakeButton()
        self.reset_btn = _FakeButton()

        self.bone_name_jp_edit = _FakeLineEdit("センター")
        self.bone_name_en_edit = _FakeLineEdit("Center")
        self.parent_bone_edit = _FakeLineEdit()
        self.ik_target_edit = _FakeLineEdit()
        self.grant_parent_edit = _FakeLineEdit("parent_jnt:親")

        self.rotatable_check = _FakeCheckBox(True)
        self.movable_check = _FakeCheckBox(False)
        self.visible_check = _FakeCheckBox(True)
        self.enabled_check = _FakeCheckBox(True)
        self.after_physics_check = _FakeCheckBox(False)
        self.external_parent_check = _FakeCheckBox(False)
        self.ik_enabled_check = _FakeCheckBox(False)
        self.rotation_grant_check = _FakeCheckBox(False)
        self.move_grant_check = _FakeCheckBox(False)
        self.local_grant_check = _FakeCheckBox(False)
        self.fixed_axis_check = _FakeCheckBox(False)
        self.local_axis_check = _FakeCheckBox(False)

        self.deform_layer_spin = _FakeSpinBox(4)
        self.ik_loop_spin = _FakeSpinBox(10)
        self.ik_limit_angle_spin = _FakeSpinBox(57.0)
        self.grant_rate_spin = _FakeSpinBox(0.5)
        self.fixed_axis_x_spin = _FakeSpinBox(0.0)
        self.fixed_axis_y_spin = _FakeSpinBox(1.0)
        self.fixed_axis_z_spin = _FakeSpinBox(0.0)
        self.local_x_axis_x_spin = _FakeSpinBox(1.0)
        self.local_x_axis_y_spin = _FakeSpinBox(0.0)
        self.local_x_axis_z_spin = _FakeSpinBox(0.0)
        self.local_z_axis_x_spin = _FakeSpinBox(0.0)
        self.local_z_axis_y_spin = _FakeSpinBox(0.0)
        self.local_z_axis_z_spin = _FakeSpinBox(1.0)
        self.external_parent_key_spin = _FakeSpinBox(-1)

        self.ik_settings_group = _FakeGroup()
        self.ik_links_group = _FakeGroup()
        self.grant_settings_group = _FakeGroup()
        self.fixed_axis_group = _FakeGroup()
        self.local_axis_group = _FakeGroup()
        self.external_parent_key_label = _FakeLabel()

        self.ik_links_table = _FakeTable()
        self.details_enabled = None

    def set_bone_details_enabled(self, enabled):
        self.details_enabled = enabled

class _FakeAppState:
    def __init__(self, current_model_root=None):
        self.current_model_root = current_model_root
        self.current_model_changed = _FakeSignal()
        self.status_messages = []

    def emit_status(self, message):
        self.status_messages.append(message)


class _FakeMayaAdapter:
    def __init__(self, exists=True, relatives=None, node_types=None, selection=None):
        self.exists = exists
        self.relatives = relatives or {}
        self.node_types = node_types or {}
        self.selection = selection or []
        self.calls = []

    def object_exists(self, node):
        self.calls.append(("object_exists", node))
        return self.exists

    def list_relatives(self, node, **kwargs):
        self.calls.append(("list_relatives", node, kwargs))
        key = (node, tuple(sorted(kwargs.items())))
        return self.relatives.get(key, [])

    def node_type(self, node):
        self.calls.append(("node_type", node))
        return self.node_types.get(node)

    def ls(self, *args, **kwargs):
        self.calls.append(("ls", args, kwargs))
        if kwargs in (
            {"selection": True, "type": "joint"},
            {"selection": True, "type": "joint", "long": True},
        ):
            return self.selection
        return []

    def select(self, nodes, **kwargs):
        self.calls.append(("select", nodes, kwargs))

    def xform(self, node, **kwargs):
        self.calls.append(("xform", node, kwargs))
        return [0.0, 0.0, 0.0]

    def attribute_exists(self, attr, node):
        self.calls.append(("attribute_exists", attr, node))
        return True


def _make_presenter(
    adapter=None,
    coordinator=None,
):
    view = _FakeView()
    app_state = _FakeAppState()
    adapter = adapter or _FakeMayaAdapter()
    presenter = BonePresenter(
        view,
        app_state,
        maya_adapter=adapter,
        authoring_coordinator=coordinator,
    )
    return presenter, view, app_state, adapter


def _attr_getter(values):
    def _get_attribute(node, attr):
        return values.get((node, attr))

    return _get_attribute


class TestBonePresenterHeadless(unittest.TestCase):
    def test_authoring_actions_fail_closed_without_injected_coordinator(self):
        presenter, view, app_state, _ = _make_presenter()
        app_state.current_model_root = TEST_MODEL

        presenter.load_bones()

        self.assertFalse(view.reindex_up_btn.enabled)
        self.assertFalse(view.reindex_down_btn.enabled)
        self.assertFalse(view.reset_authoring_btn.enabled)

    def test_authoring_actions_fail_closed_for_incomplete_coordinator(self):
        presenter, view, app_state, _ = _make_presenter(
            adapter=_FakeMayaAdapter(selection=[TEST_BONE]),
            coordinator=object(),
        )
        app_state.current_model_root = TEST_MODEL

        presenter.load_bones()

        self.assertFalse(view.reset_authoring_btn.enabled)
        self.assertFalse(presenter.reset_authoring())

    def test_register_selected_joint_routes_exactly_one_joint(self):
        coordinator = Mock()
        adapter = _FakeMayaAdapter(selection=["|model|new_joint"])
        presenter, view, app_state, _ = _make_presenter(adapter, coordinator)
        app_state.current_model_root = TEST_MODEL
        presenter.load_bones()

        self.assertFalse(view.register_joint_btn.enabled)
        self.assertFalse(hasattr(presenter, "reset_pose"))

    def test_register_selected_joint_rejects_ambiguous_selection(self):
        coordinator = Mock()
        adapter = _FakeMayaAdapter(selection=["joint_a", "joint_b"])
        presenter, _, app_state, _ = _make_presenter(adapter, coordinator)
        app_state.current_model_root = TEST_MODEL
        presenter.load_bones()

        self.assertFalse(presenter.register_selected_joint())

        coordinator.register_bone.assert_not_called()
        self.assertTrue(app_state.status_messages)

    def test_capture_rest_routes_current_registered_index_and_joint(self):
        coordinator = Mock()
        presenter, view, app_state, _ = _make_presenter(coordinator=coordinator)
        app_state.current_model_root = TEST_MODEL
        presenter._model_root_valid = True
        presenter._registered_indices = {TEST_BONE: 4}
        item = _FakeListItem("4:center", TEST_BONE)

        with patch.object(bone_presenter_module, "object_exists", return_value=True):
            with patch.object(presenter, "load_bone_properties"):
                presenter.on_bone_selected(item, None)
        with patch.object(presenter, "load_bones"):
            self.assertTrue(presenter.capture_rest())

        self.assertFalse(view.capture_rest_btn.enabled)

    def test_reindex_requires_explicit_move_then_apply(self):
        coordinator = Mock()
        presenter, view, app_state, _ = _make_presenter(coordinator=coordinator)
        app_state.current_model_root = TEST_MODEL
        presenter._model_root_valid = True
        presenter.all_bones = ["joint_a", "joint_b"]
        presenter._registered_indices = {"joint_a": 0, "joint_b": 1}
        presenter.current_bone = "joint_b"
        presenter.current_bone_index = 1
        view.bone_list.items = [
            _FakeListItem("0:a", "joint_a"),
            _FakeListItem("1:b", "joint_b"),
        ]
        view.bone_list.current_row = 1

        self.assertTrue(presenter.move_reindex(-1))
        self.assertEqual(presenter.all_bones, ["joint_b", "joint_a"])
        self.assertTrue(view.reset_authoring_btn.enabled)

    def test_unregister_requires_confirmation(self):
        coordinator = Mock()
        presenter, _, app_state, _ = _make_presenter(coordinator=coordinator)
        app_state.current_model_root = TEST_MODEL
        presenter._model_root_valid = True
        presenter.current_bone = TEST_BONE
        presenter.current_bone_index = 2

        with patch("mmd_tools.ui.qt_compat.QMessageBox.question", return_value=0):
            self.assertFalse(presenter.unregister_bone())
        coordinator.unregister_bone.assert_not_called()

        yes = QMessageBox.Yes
        with patch("mmd_tools.ui.qt_compat.QMessageBox.question", return_value=yes):
            with patch.object(presenter, "load_bones"):
                self.assertTrue(presenter.unregister_bone())
        coordinator.unregister_bone.assert_called_once_with(TEST_MODEL, 2)

    def test_bone_tab_reset_pose_surface_is_removed(self):
        presenter, view, _, _ = _make_presenter()
        self.assertFalse(hasattr(presenter, "reset_pose"))
        self.assertEqual(view.bind_pose_btn.clicked.callbacks, [])

    def test_animation_warning_is_visible_but_reset_remains_enabled(self):
        coordinator = Mock()
        spec = MmdModelAuthoringSpec(model=MmdModelSpec("Model"))
        coordinator.plan_bone_reset.return_value = BoneResetPlan(
            current_spec=spec,
            target_spec=spec,
            expected_fingerprint=spec.fingerprint(),
            warnings=("animation inputs detected; current frame 12 will be captured as PMX Rest",),
        )
        coordinator.reset_bones.return_value = spec
        presenter, view, app_state, _ = _make_presenter(
            adapter=_FakeMayaAdapter(), coordinator=coordinator
        )
        app_state.current_model_root = TEST_MODEL
        presenter._model_root_valid = True
        presenter._pending_order = []
        presenter._update_authoring_actions()
        with patch.object(presenter, "load_bones"):
            self.assertTrue(presenter.reset_authoring())
        self.assertTrue(view.animation_warning_label.visible)
        self.assertTrue(view.reset_authoring_btn.enabled)

    def test_load_bones_clears_and_returns_when_no_model(self):
        presenter, view, _, adapter = _make_presenter()

        presenter.load_bones()

        self.assertEqual(view.bone_list.clear_calls, 1)
        self.assertFalse(view.details_enabled)
        self.assertEqual(view.bone_list.items, [])
        self.assertEqual(adapter.calls, [])
        self.assertEqual(presenter.all_bones, [])

    def test_load_bones_returns_when_model_does_not_exist(self):
        adapter = _FakeMayaAdapter(exists=False)
        presenter, view, app_state, adapter = _make_presenter(adapter=adapter)
        app_state.current_model_root = TEST_MODEL

        presenter.load_bones()

        self.assertEqual(adapter.calls, [("object_exists", TEST_MODEL)])
        self.assertEqual(view.bone_list.items, [])
        self.assertEqual(presenter.bone_list_items, {})

    def test_load_bones_routes_to_adapter_and_adds_sorted_items(self):
        relatives = {
            (TEST_MODEL, (("allDescendents", True), ("type", "joint"))): [
                "arm_jnt",
                "center_jnt",
            ],
        }
        adapter = _FakeMayaAdapter(relatives=relatives)
        presenter, view, app_state, adapter = _make_presenter(adapter=adapter)
        app_state.current_model_root = TEST_MODEL
        attrs = {
            ("arm_jnt", ATTR_MMD_BONE_INDEX): 7,
            ("center_jnt", ATTR_MMD_BONE_INDEX): 0,
            ("arm_jnt", ATTR_MMD_BONE_NAME): "腕",
            ("center_jnt", ATTR_MMD_BONE_NAME): "センター",
            ("arm_jnt", ATTR_MMD_BONE_NAME_EN): "Arm",
            ("center_jnt", ATTR_MMD_BONE_NAME_EN): "Center",
        }

        with patch.object(bone_presenter_module, "get_attribute", side_effect=_attr_getter(attrs)):
            presenter.load_bones()

        self.assertEqual(
            adapter.calls,
            [
                ("object_exists", TEST_MODEL),
                (
                    "list_relatives",
                    TEST_MODEL,
                    {"allDescendents": True, "type": "joint"},
                ),
            ],
        )
        self.assertEqual(presenter.all_bones, ["center_jnt", "arm_jnt"])
        self.assertEqual(
            [item.text() for item in view.bone_list.items],
            ["0:センター（center_jnt） [Center]", "7:腕（arm_jnt） [Arm]"],
        )
        self.assertEqual(
            [item.data(bone_presenter_module.Qt.UserRole) for item in view.bone_list.items],
            ["center_jnt", "arm_jnt"],
        )

    def test_load_bones_falls_back_to_descendant_node_type_filter(self):
        relatives = {
            (TEST_MODEL, (("allDescendents", True), ("type", "joint"))): [],
            (TEST_MODEL, (("children", True),)): ["skeleton_grp"],
            (TEST_MODEL, (("allDescendents", True),)): ["mesh", "leg_jnt"],
        }
        adapter = _FakeMayaAdapter(relatives=relatives, node_types={"mesh": "mesh", "leg_jnt": "joint"})
        presenter, view, app_state, adapter = _make_presenter(adapter=adapter)
        app_state.current_model_root = TEST_MODEL
        attrs = {
            ("leg_jnt", ATTR_MMD_BONE_INDEX): 2,
            ("leg_jnt", ATTR_MMD_BONE_NAME): "足",
            ("leg_jnt", ATTR_MMD_BONE_NAME_EN): "",
        }

        with patch.object(bone_presenter_module, "get_attribute", side_effect=_attr_getter(attrs)):
            presenter.load_bones()

        self.assertEqual(
            adapter.calls,
            [
                ("object_exists", TEST_MODEL),
                ("list_relatives", TEST_MODEL, {"allDescendents": True, "type": "joint"}),
                ("list_relatives", TEST_MODEL, {"children": True}),
                ("list_relatives", TEST_MODEL, {"allDescendents": True}),
                ("node_type", "mesh"),
                ("node_type", "leg_jnt"),
            ],
        )
        self.assertEqual([item.text() for item in view.bone_list.items], ["2:足（leg_jnt）"])

    def test_load_bones_hides_namespace_and_path_but_preserves_full_node(self):
        joint = "|root|outer:model:manipulation_center"
        relatives = {
            (TEST_MODEL, (("allDescendents", True), ("type", "joint"))): [joint],
        }
        adapter = _FakeMayaAdapter(relatives=relatives)
        presenter, view, app_state, _ = _make_presenter(adapter=adapter)
        app_state.current_model_root = TEST_MODEL
        attrs = {
            (joint, ATTR_MMD_BONE_INDEX): 0,
            (joint, ATTR_MMD_BONE_NAME): "操作中心",
            (joint, ATTR_MMD_BONE_NAME_EN): "Manipulation Center",
        }

        with patch.object(bone_presenter_module, "get_attribute", side_effect=_attr_getter(attrs)):
            presenter.load_bones()

        item = view.bone_list.items[0]
        self.assertEqual(
            item.text(),
            "0:操作中心（manipulation_center） [Manipulation Center]",
        )
        self.assertEqual(item.data(bone_presenter_module.Qt.UserRole), joint)
        self.assertEqual(item.toolTip(), joint)

    def test_filter_bones_uses_display_japanese_english_and_joint_names(self):
        presenter, _, _, _ = _make_presenter()
        center_item = _FakeListItem("0:センター（center_jnt）", "center_jnt")
        arm_item = _FakeListItem("7:腕（arm_jnt） [Arm]", "arm_jnt")
        presenter.bone_list_items = {"center_jnt": center_item, "arm_jnt": arm_item}
        attrs = {
            ("center_jnt", ATTR_MMD_BONE_NAME): "センター",
            ("center_jnt", ATTR_MMD_BONE_NAME_EN): "Center",
            ("arm_jnt", ATTR_MMD_BONE_NAME): "腕",
            ("arm_jnt", ATTR_MMD_BONE_NAME_EN): "Arm",
        }

        with patch.object(bone_presenter_module, "get_attribute", side_effect=_attr_getter(attrs)):
            presenter.filter_bones("arm")

        self.assertTrue(center_item.hidden)
        self.assertFalse(arm_item.hidden)

    def test_ui_toggles_reflect_state_on_view_groups(self):
        presenter, view, _, _ = _make_presenter()

        presenter.on_ik_enabled_toggled(True)
        self.assertTrue(view.ik_settings_group.visible)
        self.assertTrue(view.ik_links_group.visible)

        view.rotation_grant_check.setChecked(False)
        view.move_grant_check.setChecked(True)
        presenter.on_grant_toggled()
        self.assertTrue(view.grant_settings_group.visible)

        view.fixed_axis_check.setChecked(True)
        view.local_axis_check.setChecked(False)
        presenter.on_axis_toggled()
        self.assertTrue(view.fixed_axis_group.visible)
        self.assertFalse(view.local_axis_group.visible)

        presenter.on_external_parent_toggled(True)
        self.assertTrue(view.external_parent_key_label.visible)
        self.assertTrue(view.external_parent_key_spin.visible)

    def test_load_bone_properties_treats_missing_flags_as_unflagged(self):
        presenter, view, _, adapter = _make_presenter()
        presenter.current_bone = TEST_BONE
        attrs = {
            (TEST_BONE, ATTR_MMD_BONE_NAME): "センター",
            (TEST_BONE, ATTR_MMD_BONE_NAME_EN): "Center",
            (TEST_BONE, ATTR_MMD_DEFORM_LAYER): 0,
            (TEST_BONE, ATTR_MMD_BONE_FLAGS): None,
            (TEST_BONE, ATTR_MMD_BONE_OFFSET): [0.0, -1.0, 0.0],
            (TEST_BONE, ATTR_MMD_CONNECTION_BONE): "",
            (TEST_BONE, ATTR_MMD_IK_TARGET_INDEX): -1,
            (TEST_BONE, ATTR_MMD_IK_LOOP): 10,
            (TEST_BONE, ATTR_MMD_IK_LIMIT_ANGLE): 0.0,
            (TEST_BONE, ATTR_MMD_IK_LINKS): [],
            (TEST_BONE, ATTR_MMD_EXTERNAL_PARENT_KEY): -1,
        }

        with patch.object(bone_presenter_module, "get_attribute", side_effect=_attr_getter(attrs)):
            with patch.object(bone_presenter_module, "object_exists", return_value=True):
                presenter.load_bone_properties()

        self.assertFalse(view.rotatable_check.isChecked())
        self.assertFalse(view.movable_check.isChecked())
        self.assertFalse(view.ik_enabled_check.isChecked())
        self.assertEqual(adapter.calls[0], ("list_relatives", TEST_BONE, {"parent": True, "type": "joint"}))

    def test_calculate_bone_flags_combines_enabled_ui_state(self):
        presenter, view, _, _ = _make_presenter()
        view.movable_check.setChecked(True)
        view.ik_enabled_check.setChecked(True)
        view.local_grant_check.setChecked(True)
        view.rotation_grant_check.setChecked(True)
        view.fixed_axis_check.setChecked(True)
        view.local_axis_check.setChecked(True)
        view.after_physics_check.setChecked(True)
        view.external_parent_check.setChecked(True)

        flags = presenter._calculate_bone_flags(PmxBoneFlag.CONNECT_BONE)

        expected = (
            PmxBoneFlag.CONNECT_BONE
            | PmxBoneFlag.ROTATABLE
            | PmxBoneFlag.MOVABLE
            | PmxBoneFlag.DISPLAY
            | PmxBoneFlag.OPERATABLE
            | PmxBoneFlag.IK
            | PmxBoneFlag.LOCAL
            | PmxBoneFlag.GRANT_PARENT_ROTATE
            | PmxBoneFlag.AXIS_FIXED
            | PmxBoneFlag.LOCAL_AXIS
            | PmxBoneFlag.DEFORM_AFTER_PHYSICS
            | PmxBoneFlag.EXTERNAL_PARENT_DEFORM
        )
        self.assertEqual(flags, expected)

    def test_select_bone_dialog_routes_selection_query_and_updates_target_field(self):
        adapter = _FakeMayaAdapter(selection=["ik_target_jnt"])
        presenter, view, _, adapter = _make_presenter(adapter=adapter)
        attrs = {("ik_target_jnt", ATTR_MMD_BONE_NAME): "IK先"}

        with patch.object(bone_presenter_module, "object_exists", return_value=True):
            with patch.object(bone_presenter_module, "get_attribute", side_effect=_attr_getter(attrs)):
                presenter.select_bone_dialog("ik_target")

        self.assertEqual(adapter.calls, [("ls", (), {"selection": True, "type": "joint"})])
        self.assertEqual(view.ik_target_edit.text(), "ik_target_jnt:IK先")

    def test_apply_changes_routes_complete_spec_through_semantic_coordinator(self):
        adapter = _FakeMayaAdapter()
        coordinator = Mock()
        coordinator.read_spec.return_value = MmdModelAuthoringSpec(
            model=MmdModelSpec("Model"),
            bones=(MmdBoneSpec(
                "center",
                index=3,
                flags=int(PmxBoneFlag.CONNECT_BONE),
                connect_bone_index=4,
                tail_offset=(0.0, 2.0, 0.0),
                rest_position=(9.0, 8.0, 7.0),
                binding_identity=TEST_BONE,
            ), MmdBoneSpec("tail", index=4, binding_identity="tail_jnt")),
        )
        presenter, view, app_state, adapter = _make_presenter(adapter=adapter, coordinator=coordinator)
        app_state.current_model_root = TEST_MODEL
        presenter._model_root_valid = True
        presenter.current_bone = TEST_BONE
        presenter.current_bone_index = 3

        with patch.object(presenter, "load_bones"):
            presenter.apply_changes()

        coordinator.read_spec.assert_called_once_with(TEST_MODEL)
        root, replacement = coordinator.replace_bone_semantic.call_args.args
        self.assertEqual(root, TEST_MODEL)
        self.assertEqual(replacement.name, "センター")
        self.assertEqual(replacement.name_english, "Center")
        self.assertEqual(replacement.transform_layer, 4)
        self.assertEqual(replacement.tail_offset, (0.0, 2.0, 0.0))
        self.assertEqual(replacement.connect_bone_index, 4)
        self.assertTrue(replacement.flags & PmxBoneFlag.CONNECT_BONE)
        self.assertEqual(replacement.rest_position, (9.0, 8.0, 7.0))
        self.assertIsNone(replacement.grant_parent_index)
        self.assertIsNone(replacement.ik_target_index)
        self.assertEqual(
            adapter.calls,
            [("object_exists", TEST_BONE), ("object_exists", TEST_MODEL)],
        )
        self.assertEqual(app_state.status_messages, ["Applied bone changes: center_jnt"])


if __name__ == "__main__":
    unittest.main()
