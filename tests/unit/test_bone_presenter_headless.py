"""BonePresenterのMaya非依存ロジックを検証するheadless unitテスト。"""

import unittest
from unittest.mock import patch

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
    ATTR_MMD_GRANT_PARENT,
    ATTR_MMD_GRANT_RATE,
    ATTR_MMD_IK_LINKS,
    ATTR_MMD_IK_LIMIT_ANGLE,
    ATTR_MMD_IK_LOOP,
    ATTR_MMD_IK_TARGET_INDEX,
)
from mmd_tools.core.pmx_data.bone import PmxBoneFlag  # noqa: E402
from mmd_tools.ui.presenters import bone_presenter as bone_presenter_module  # noqa: E402
from mmd_tools.ui.presenters.bone_presenter import BonePresenter  # noqa: E402
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
    pass


class _FakeList:
    def __init__(self):
        self.clear_calls = 0
        self.items = []
        self.currentItemChanged = _FakeSignal()
        self.itemSelectionChanged = _FakeSignal()

    def clear(self):
        self.clear_calls += 1
        self.items.clear()

    def addItem(self, item):
        self.items.append(item)

    def selectedItems(self):
        return []


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
        self.rest_pose_btn = _FakeButton()
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
        self.connection_bone_edit = _FakeLineEdit()
        self.ik_target_edit = _FakeLineEdit()
        self.grant_parent_edit = _FakeLineEdit("parent_jnt:親")

        self.connection_type_combo = _FakeComboBox(0)
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

        self.pos_x_spin = _FakeSpinBox(1.0)
        self.pos_y_spin = _FakeSpinBox(2.0)
        self.pos_z_spin = _FakeSpinBox(3.0)
        self.deform_layer_spin = _FakeSpinBox(4)
        self.offset_x_spin = _FakeSpinBox(0.25)
        self.offset_y_spin = _FakeSpinBox(-1.5)
        self.offset_z_spin = _FakeSpinBox(0.75)
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
        self.rest_pose_active = None

    def set_bone_details_enabled(self, enabled):
        self.details_enabled = enabled

    def set_rest_pose_state(self, active):
        self.rest_pose_active = active


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
        if kwargs == {"selection": True, "type": "joint"}:
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


class _FakeRestPoseResult:
    def __init__(
        self, *, succeeded=True, active=False, error="", model_root="", joint_count=0
    ):
        self.succeeded = succeeded
        self.active = active
        self.error = error
        self.model_root = model_root
        self.joint_count = joint_count


class _FakeRestPoseManager:
    def __init__(self):
        self.active = False
        self.listeners = []
        self.toggle_models = []

    def add_listener(self, callback):
        self.listeners.append(callback)

    def remove_listener(self, callback):
        if callback in self.listeners:
            self.listeners.remove(callback)

    def state(self):
        return _FakeRestPoseResult(active=self.active)

    def toggle(self, model_root):
        self.toggle_models.append(model_root)
        self.active = not self.active
        result = _FakeRestPoseResult(active=self.active, model_root=model_root)
        for listener in self.listeners:
            listener(result)
        return result

    def ensure_model(self, _model_root):
        return self.state()


def _make_presenter(adapter=None):
    view = _FakeView()
    app_state = _FakeAppState()
    adapter = adapter or _FakeMayaAdapter()
    presenter = BonePresenter(view, app_state, maya_adapter=adapter)
    return presenter, view, app_state, adapter


def _attr_getter(values):
    def _get_attribute(node, attr):
        return values.get((node, attr))

    return _get_attribute


class TestBonePresenterHeadless(unittest.TestCase):
    def test_rest_pose_button_toggles_shared_model_session_and_locks_editor(self):
        view = _FakeView()
        app_state = _FakeAppState(TEST_MODEL)
        manager = _FakeRestPoseManager()
        presenter = BonePresenter(
            view,
            app_state,
            maya_adapter=_FakeMayaAdapter(),
            rest_pose_manager=manager,
        )

        presenter.toggle_rest_pose()

        self.assertEqual(manager.toggle_models, [TEST_MODEL])
        self.assertTrue(view.rest_pose_active)
        self.assertFalse(view.details_enabled)

        presenter.disconnect_signals()
        self.assertEqual(manager.listeners, [])

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

        presenter.on_connection_type_changed(1)
        self.assertFalse(view.offset_x_spin.enabled)
        self.assertFalse(view.offset_y_spin.enabled)
        self.assertFalse(view.offset_z_spin.enabled)
        self.assertTrue(view.connection_bone_edit.enabled)

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
        view.connection_type_combo.setCurrentIndex(1)
        view.movable_check.setChecked(True)
        view.ik_enabled_check.setChecked(True)
        view.local_grant_check.setChecked(True)
        view.rotation_grant_check.setChecked(True)
        view.fixed_axis_check.setChecked(True)
        view.local_axis_check.setChecked(True)
        view.after_physics_check.setChecked(True)
        view.external_parent_check.setChecked(True)

        flags = presenter._calculate_bone_flags()

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

    def test_apply_changes_routes_object_exists_and_xform_and_updates_view_item(self):
        adapter = _FakeMayaAdapter()
        presenter, view, app_state, adapter = _make_presenter(adapter=adapter)
        presenter.current_bone = TEST_BONE
        list_item = _FakeListItem("old")
        presenter.bone_list_items[TEST_BONE] = list_item
        attrs = {(TEST_BONE, ATTR_MMD_BONE_INDEX): 3}

        with patch.object(bone_presenter_module, "get_attribute", side_effect=_attr_getter(attrs)):
            with patch.object(bone_presenter_module, "set_custom_attributes") as set_attrs:
                with patch.object(presenter, "_ensure_mmd_attributes") as ensure_attrs:
                    presenter.apply_changes()

                    self.assertEqual(
                        adapter.calls,
                        [
                            ("object_exists", TEST_BONE),
                            (
                                "xform",
                                TEST_BONE,
                                {"translation": [1.0, 2.0, 3.0], "worldSpace": True},
                            ),
                        ],
                    )
                    ensure_attrs.assert_called_once_with(TEST_BONE)
                    set_attrs.assert_called_once()
                    node, attributes = set_attrs.call_args.args
                    self.assertEqual(node, TEST_BONE)
                    self.assertEqual(attributes[ATTR_MMD_BONE_NAME], "センター")
                    self.assertEqual(attributes[ATTR_MMD_BONE_NAME_EN], "Center")
                    self.assertEqual(attributes[ATTR_MMD_DEFORM_LAYER], 4)
                    self.assertEqual(attributes[ATTR_MMD_BONE_OFFSET], [0.25, -1.5, 0.75])
                    self.assertEqual(
                        attributes[ATTR_MMD_BONE_FLAGS],
                        PmxBoneFlag.ROTATABLE | PmxBoneFlag.DISPLAY | PmxBoneFlag.OPERATABLE,
                    )
                    self.assertNotIn(ATTR_MMD_GRANT_PARENT, attributes)
                    self.assertNotIn(ATTR_MMD_GRANT_RATE, attributes)
                    self.assertEqual(list_item.text(), "3:センター（center_jnt） [Center]")
                    self.assertEqual(list_item.toolTip(), TEST_BONE)
                    self.assertEqual(app_state.status_messages, ["Applied bone changes: center_jnt"])


if __name__ == "__main__":
    unittest.main()
