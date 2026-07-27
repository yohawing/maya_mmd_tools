"""AnimationPresenter headless unit tests — no Maya dependency."""

import json
import unittest
from unittest.mock import patch

from tests.common.maya_stub import install_headless_ui_stubs

install_headless_ui_stubs()

from mmd_tools.ui.presenters.animation_presenter import AnimationPresenter  # noqa: E402


SAMPLE_FRAMES_JSON = json.dumps(
    [
        {
            "name": "Root",
            "name_english": "Root",
            "special_flag": 1,
            "elements": [{"type": 0, "index": 0}],
        },
        {
            "name": "表情",
            "name_english": "Exp",
            "special_flag": 1,
            "elements": [{"type": 1, "index": 0}, {"type": 1, "index": 1}],
        },
        {
            "name": "体(上)",
            "name_english": "Upper Body",
            "special_flag": 0,
            "elements": [
                {"type": 0, "index": 3},
                {"type": 0, "index": 4},
            ],
        },
    ],
    ensure_ascii=False,
)


# --- Fake Qt widgets ---


class _FakeSignal:
    def __init__(self):
        self.callbacks = []

    def connect(self, cb):
        self.callbacks.append(cb)

    def disconnect(self, cb):
        if cb in self.callbacks:
            self.callbacks.remove(cb)

    def emit(self, *args):
        for cb in self.callbacks:
            cb(*args)


class _FakeButton:
    def __init__(self):
        self.clicked = _FakeSignal()
        self.text = ""
        self.enabled = True

    def setText(self, text):
        self.text = text

    def setEnabled(self, enabled):
        self.enabled = enabled


class _FakeComboBox:
    def __init__(self):
        self._items = []
        self._index = -1
        self.currentTextChanged = _FakeSignal()

    def clear(self):
        self._items.clear()
        self._index = -1

    def addItem(self, text, data=None):
        self._items.append((text, data))

    def findText(self, text):
        for i, (t, _) in enumerate(self._items):
            if t == text:
                return i
        return -1

    def setCurrentIndex(self, idx):
        self._index = idx

    def blockSignals(self, block):
        pass

    def setSizeAdjustPolicy(self, policy):
        pass

    def setToolTip(self, _):
        pass


class _FakeLabel:
    def __init__(self, text=""):
        self._text = text

    def text(self):
        return self._text

    def setText(self, text):
        self._text = text

    def setAlignment(self, _):
        pass


class _FakeTreeItem:
    def __init__(self, texts=None):
        self._texts = texts or [""]
        self._data = {}
        self._children = []
        self._expanded = False

    def text(self, col=0):
        return self._texts[col] if col < len(self._texts) else ""

    def data(self, col, role):
        return self._data.get((col, role))

    def setData(self, col, role, value):
        self._data[(col, role)] = value

    def addChild(self, child):
        self._children.append(child)

    def setExpanded(self, expanded):
        self._expanded = expanded

    def childCount(self):
        return len(self._children)

    def child(self, idx):
        return self._children[idx]


import mmd_tools.ui.qt_compat as _qt_compat  # noqa: E402

_qt_compat.QTreeWidgetItem = _FakeTreeItem


class _FakeTreeWidget:
    def __init__(self):
        self._items = []
        self.itemClicked = _FakeSignal()
        self.itemPressed = _FakeSignal()

    def clear(self):
        self._items.clear()

    def addTopLevelItem(self, item):
        self._items.append(item)

    def topLevelItemCount(self):
        return len(self._items)

    def topLevelItem(self, idx):
        return self._items[idx]

    def setHeaderHidden(self, _):
        pass


class _FakeLayoutItem:
    def __init__(self, widget=None):
        self._widget = widget

    def widget(self):
        return self._widget


class _FakeWidget:
    def deleteLater(self):
        pass


class _FakeLayout:
    def __init__(self):
        self._items: list[_FakeLayoutItem] = [_FakeLayoutItem()]  # stretch

    def count(self):
        return len(self._items)

    def takeAt(self, index):
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return _FakeLayoutItem()

    def insertWidget(self, index, widget):
        self._items.insert(index, _FakeLayoutItem(widget))


class _FakeBodyPicker:
    def __init__(self):
        self.region_clicked = _FakeSignal()
        self.regions_selected = _FakeSignal()
        self.goto_finger_clicked = _FakeSignal()
        self.mirror_selection_clicked = _FakeSignal()
        self.reset_pose_clicked = _FakeSignal()
        self.select_all_clicked = _FakeSignal()
        self.clear_selection_clicked = _FakeSignal()
        self.ik_toggled = _FakeSignal()
        self.ik_enable_toggle_clicked = _FakeSignal()
        self.selected_regions = []
        self.tooltip = ""
        self.additive_selection = False
        self.region_labels = {}
        self.region_tooltips = {}
        self.hidden_regions = set()
        self.region_dim_levels = {}

    def set_selected_regions(self, region_ids):
        self.selected_regions = list(region_ids)

    def setToolTip(self, text):
        self.tooltip = text

    def update_region_texts(self, *, labels=None, tooltips=None):
        self.region_labels.update(labels or {})
        self.region_tooltips.update(tooltips or {})

    def set_hidden_regions(self, region_ids):
        self.hidden_regions = set(region_ids)

    def set_region_dim_levels(self, levels):
        self.region_dim_levels = dict(levels)


class _FakeFingerPicker:
    def __init__(self):
        self.region_clicked = _FakeSignal()
        self.regions_selected = _FakeSignal()
        self.goto_body_clicked = _FakeSignal()
        self.mirror_selection_clicked = _FakeSignal()
        self.selected_regions = []
        self.additive_selection = False
        self.region_tooltips = {}

    def set_selected_regions(self, region_ids):
        self.selected_regions = list(region_ids)

    def update_region_texts(self, *, labels=None, tooltips=None):
        self.region_tooltips.update(tooltips or {})


class _FakeTabWidget:
    def __init__(self):
        self._current = 0
        self.enabled = True

    def setObjectName(self, _):
        pass

    def setCurrentIndex(self, idx):
        self._current = idx

    def currentIndex(self):
        return self._current

    def setEnabled(self, enabled):
        self.enabled = enabled


class _FakeCheckBox:
    def __init__(self, label=""):
        self._label = label
        self._checked = True
        self.stateChanged = _FakeSignal()

    def isChecked(self):
        return self._checked

    def setChecked(self, val):
        self._checked = val

    def setEnabled(self, enabled):
        self.enabled = bool(enabled)

    def setVisible(self, visible):
        self.visible = bool(visible)


class _FakeView:
    TAB_BODY = 0
    TAB_FINGER = 1
    TAB_MORPH = 2
    TAB_OTHER = 3

    def __init__(self):
        self.model_combo = _FakeComboBox()
        self.refresh_btn = _FakeButton()
        self.clear_btn = _FakeButton()
        self.select_all_btn = _FakeButton()
        self.status_label = _FakeLabel()
        self.display_frame_tree = _FakeTreeWidget()
        self.body_picker = _FakeBodyPicker()
        self.finger_picker = _FakeFingerPicker()
        self.morph_groups_layout = _FakeLayout()
        self.picker_tabs = _FakeTabWidget()
        self.vis_checkboxes = {
            k: _FakeCheckBox(k)
            for k in ("mesh", "joints", "colliders")
        }
        self.tool_buttons = {
            k: _FakeButton()
            for k in ("copy", "paste", "mirror", "reset", "clean", "bake")
        }

    def tr(self, key, _category=None):
        return {
            "reset": "Reset Pose",
            "reset_pose_tooltip": "Reset selected joints to their bind pose",
            "no_selectable_bones": "No selectable bones",
            "selected_all_bones": "Selected all bones ({count})",
            "select_all_failed": "Failed to select all bones",
            "node_not_found": "Not found: {name}",
            "unassigned_bones": "Unassigned: {names}",
            "selection_failed": "Selection failed: {names}",
            "no_joints_selected": "No joints selected",
            "copied_pose": "Copied pose ({count} joints)",
            "copy_failed": "Copy failed: {error}",
            "no_pose_copied": "No pose copied",
            "pasted_pose": "Pasted pose ({count} joints)",
            "paste_failed": "Paste failed: {error}",
            "reset_pose_applied": "Reset Pose ({count} joints)",
            "reset_pose_failed": "Reset Pose failed: {error}",
            "ik_not_found": "{side} IK was not found",
            "ik_enabled": "{side} IK enabled",
            "ik_disabled": "{side} IK disabled",
            "ik_toggle_failed": "IK toggle failed: {error}",
            "mirrored_pose": "Mirrored pose",
            "mirror_failed": "Mirror failed: {error}",
            "baked_animation": "Baked animation",
            "bake_failed": "Bake failed: {error}",
            "cleaned_curves": "Cleaned curves",
            "clean_failed": "Clean failed: {error}",
        }.get(key, key)

    def current_language(self):
        return "en"


class _FakeAppState:
    def __init__(self, model_root=None):
        self.current_model_changed = _FakeSignal()
        self.model_list_updated = _FakeSignal()
        self._current_model_root = model_root
        self.cache_clear_count = 0

    @property
    def current_model_root(self):
        return self._current_model_root

    @current_model_root.setter
    def current_model_root(self, value):
        self._current_model_root = value

    def refresh_model_list(self):
        pass

    def clear_cache(self):
        self.cache_clear_count += 1


class _FakeAdapter:
    def __init__(self, joints_by_index=None, display_json=None,
                 blend_shapes=None, bone_names=None, morph_data=None):
        self._joints_by_index = joints_by_index or {}
        self._display_json = display_json
        self._blend_shapes = blend_shapes or {}
        self._bone_names = bone_names or {}
        self._morph_data = morph_data
        self.selected = []
        self._set_attrs = {}
        self._attrs: dict[tuple[str, str], object] = {}
        self._connections: list[tuple[str, str, bool]] = []
        self._incoming_connections: dict[str, list[str]] = {}
        self._node_types: dict[str, str] = {}
        self._transforms: dict[str, tuple[list, list]] = {}
        self._undo_chunks: list[str] = []

    def ls(self, nodes=None, type=None, selection=False):
        if selection:
            return list(self.selected)
        return nodes if nodes else []

    def list_relatives(self, node, **kwargs):
        if kwargs.get("parent"):
            return [node.rsplit("|", 1)[0] or node]
        if kwargs.get("children") and kwargs.get("type") == "transform":
            return [
                f"|{node}|Geometry",
                f"|{node}|Skeleton",
                f"|{node}|Physics",
            ]
        node_type = kwargs.get("type")
        if node_type == "mesh":
            return list(self._blend_shapes.keys()) if self._blend_shapes else []
        return list(self._joints_by_index.values())

    def list_history(self, node):
        return list(self._blend_shapes.get(node, {}).keys())

    def node_type(self, node):
        if node in self._node_types:
            return self._node_types[node]
        for mesh_bs in self._blend_shapes.values():
            if node in mesh_bs:
                return mesh_bs[node]["type"]
        return "transform"

    def alias_attr(self, node, query=False):
        for mesh_bs in self._blend_shapes.values():
            if node in mesh_bs and "aliases" in mesh_bs[node]:
                return mesh_bs[node]["aliases"]
        return []

    def attribute_exists(self, attr, node):
        if attr == "mmd_bone_index":
            return node in self._joints_by_index.values()
        if attr == "mmd_bone_name":
            return node in self._bone_names
        if attr == "mmd_display_frames_json":
            return self._display_json is not None
        if attr == "mmd_blendshape_morph_names_json":
            for mesh_bs in self._blend_shapes.values():
                if node in mesh_bs and "morph_json" in mesh_bs[node]:
                    return True
        if attr == "mmdMorphData":
            return self._morph_data is not None
        if (node, attr) in self._attrs:
            return True
        return False

    def get_attr(self, attr_path):
        node, attr = attr_path.rsplit(".", 1)
        if (node, attr) in self._attrs:
            return self._attrs[(node, attr)]
        if attr == "mmd_bone_index":
            for idx, name in self._joints_by_index.items():
                if name == node:
                    return idx
            return -1
        if attr == "mmd_bone_name":
            return self._bone_names.get(node)
        if attr == "mmd_display_frames_json":
            return self._display_json
        if attr == "mmd_blendshape_morph_names_json":
            for mesh_bs in self._blend_shapes.values():
                if node in mesh_bs and "morph_json" in mesh_bs[node]:
                    return json.dumps(mesh_bs[node]["morph_json"])
        if attr == "mmdMorphData":
            return json.dumps(self._morph_data, ensure_ascii=False)
        return None

    def set_attr(self, attr_path, value):
        self._set_attrs[attr_path] = value
        node, attr = attr_path.rsplit(".", 1)
        self._attrs[(node, attr)] = value

    def add_attr(self, node, longName=None, attributeType=None, **kwargs):
        self._attrs[(node, longName)] = False

    def delete_attr(self, attr_path):
        node, attr = attr_path.rsplit(".", 1)
        self._attrs.pop((node, attr), None)

    def connect_attr(self, source, destination, force=False):
        self._connections.append((source, destination, force))
        self._incoming_connections[destination] = [source]

    def list_connections(self, node, **kwargs):
        if kwargs.get("source") and kwargs.get("plugs"):
            return self._incoming_connections.get(node, [])
        return []

    def select(self, nodes, replace=True):
        if replace:
            self.selected = list(nodes)
        else:
            self.selected = list(dict.fromkeys([*self.selected, *nodes]))

    def xform(self, node, **kwargs):
        if kwargs.get("query"):
            t, r = self._transforms.get(node, ([0, 0, 0], [0, 0, 0]))
            if kwargs.get("translation"):
                return list(t)
            if kwargs.get("rotation"):
                return list(r)
            return None
        t = kwargs.get("translation")
        if t is not None:
            self._transforms.setdefault(node, ([0, 0, 0], [0, 0, 0]))
            self._transforms[node] = (list(t), self._transforms[node][1])
        r = kwargs.get("rotation")
        if r is not None:
            self._transforms.setdefault(node, ([0, 0, 0], [0, 0, 0]))
            self._transforms[node] = (self._transforms[node][0], list(r))

    def undo_info(self, **kwargs):
        if kwargs.get("openChunk"):
            self._undo_chunks.append(kwargs.get("chunkName", ""))


_USER_ROLE = 0x0100


class TestAnimationPresenter(unittest.TestCase):
    def _make(
        self,
        joints=None,
        display_json=None,
        model_root=None,
        bone_names=None,
        morph_data=None,
    ):
        view = _FakeView()
        app_state = _FakeAppState(model_root=model_root)
        adapter = _FakeAdapter(
            joints_by_index=joints or {},
            display_json=display_json,
            bone_names=bone_names,
            morph_data=morph_data,
        )
        presenter = AnimationPresenter(view, app_state, maya_adapter=adapter)
        return presenter, view, app_state, adapter

    def test_initial_no_model(self):
        presenter, view, _, _ = self._make()
        self.assertEqual(view.display_frame_tree.topLevelItemCount(), 0)

    def test_model_change_populates_tree(self):
        joints = {0: "center", 3: "upper_body", 4: "neck"}
        presenter, view, app_state, _ = self._make(
            joints=joints,
            display_json=SAMPLE_FRAMES_JSON,
        )

        app_state.current_model_changed.emit("test_model")

        self.assertEqual(view.display_frame_tree.topLevelItemCount(), 3)
        root_group = view.display_frame_tree.topLevelItem(0)
        self.assertIn("Root", root_group.text(0))
        self.assertEqual(root_group.childCount(), 1)

    def test_scene_change_reloads_same_named_model_after_cache_clear(self):
        presenter, _, app_state, _ = self._make(model_root="test_model")

        with patch.object(presenter, "_reload_for_model") as reload_model:
            presenter.refresh_for_scene_change()

        self.assertEqual(app_state.cache_clear_count, 1)
        reload_model.assert_called_once_with("test_model")

    def test_tree_bone_item_has_user_data(self):
        joints = {0: "center"}
        presenter, view, _, _ = self._make(
            joints=joints,
            display_json=SAMPLE_FRAMES_JSON,
            model_root="test_model",
        )

        root_group = view.display_frame_tree.topLevelItem(0)
        child = root_group.child(0)
        self.assertEqual(child.data(0, _USER_ROLE), "center")

    def test_tree_unresolved_item_has_no_user_data(self):
        presenter, view, _, _ = self._make(
            joints={},
            display_json=SAMPLE_FRAMES_JSON,
            model_root="test_model",
        )

        root_group = view.display_frame_tree.topLevelItem(0)
        child = root_group.child(0)
        self.assertIsNone(child.data(0, _USER_ROLE))
        self.assertIn("#0", child.text(0))

    def test_click_item_selects_in_maya(self):
        joints = {0: "center"}
        presenter, view, _, adapter = self._make(
            joints=joints,
            display_json=SAMPLE_FRAMES_JSON,
            model_root="test_model",
        )

        root_group = view.display_frame_tree.topLevelItem(0)
        child = root_group.child(0)
        view.display_frame_tree.itemPressed.emit(child, 0)

        self.assertEqual(adapter.selected, ["center"])
        self.assertEqual(view.status_label.text(), "center")

    def test_display_groups_start_collapsed_including_morphs(self):
        _presenter, view, _, _ = self._make(
            joints={0: "center"},
            display_json=SAMPLE_FRAMES_JSON,
            model_root="test_model",
        )

        self.assertTrue(
            all(
                not view.display_frame_tree.topLevelItem(index)._expanded
                for index in range(view.display_frame_tree.topLevelItemCount())
            )
        )

    def test_click_unresolved_item_does_nothing(self):
        presenter, view, _, adapter = self._make(
            joints={},
            display_json=SAMPLE_FRAMES_JSON,
            model_root="test_model",
        )

        root_group = view.display_frame_tree.topLevelItem(0)
        child = root_group.child(0)
        presenter.on_display_frame_item_clicked(child)

        self.assertEqual(adapter.selected, [])

    def test_clear_button_clears_selection(self):
        joints = {0: "center"}
        presenter, view, _, adapter = self._make(
            joints=joints,
            display_json=SAMPLE_FRAMES_JSON,
            model_root="test_model",
        )

        view.status_label.setText("center")
        presenter.on_clear_clicked()

        self.assertEqual(adapter.selected, [])
        self.assertEqual(view.status_label.text(), "")

    def test_model_change_to_empty_clears_tree(self):
        joints = {0: "center"}
        presenter, view, app_state, _ = self._make(
            joints=joints,
            display_json=SAMPLE_FRAMES_JSON,
            model_root="test_model",
        )
        self.assertGreater(view.display_frame_tree.topLevelItemCount(), 0)

        app_state.current_model_changed.emit("")

        self.assertEqual(view.display_frame_tree.topLevelItemCount(), 0)

    def test_fallback_flat_list_when_no_display_frames(self):
        joints = {0: "center", 1: "upper_body"}
        presenter, view, _, _ = self._make(
            joints=joints,
            display_json=None,
            model_root="test_model",
        )

        self.assertEqual(view.display_frame_tree.topLevelItemCount(), 1)
        group = view.display_frame_tree.topLevelItem(0)
        self.assertEqual(group.childCount(), 2)

    def test_special_flag_group_count(self):
        joints = {0: "center"}
        presenter, view, _, _ = self._make(
            joints=joints,
            display_json=SAMPLE_FRAMES_JSON,
            model_root="test_model",
        )

        special = [
            view.display_frame_tree.topLevelItem(i)
            for i in range(view.display_frame_tree.topLevelItemCount())
        ]
        self.assertEqual(view.display_frame_tree.topLevelItemCount(), 3)
        self.assertIn("Root", special[0].text(0))
        self.assertIn("表情", special[1].text(0))

    def test_item_display_text_strips_namespace_and_path(self):
        joints = {0: "|root|ns:center_jnt"}
        presenter, view, _, _ = self._make(
            joints=joints,
            display_json=json.dumps([{
                "name": "Root",
                "name_english": "Root",
                "special_flag": 0,
                "elements": [{"type": 0, "index": 0}],
            }]),
            model_root="test_model",
        )

        child = view.display_frame_tree.topLevelItem(0).child(0)
        self.assertEqual(child.text(0), "center_jnt")

    def test_display_uses_japanese_bone_and_morph_metadata_names(self):
        with patch(
            "mmd_tools.ui.presenters.animation_presenter"
            ".AnimationPresenter._populate_morph_groups"
        ):
            _presenter, view, _, _ = self._make(
                joints={0: "|root|ns:center_jnt"},
                bone_names={"|root|ns:center_jnt": "センター"},
                display_json=SAMPLE_FRAMES_JSON,
                morph_data=[
                    {"index": 0, "name_jp": "笑い", "name_en": "Smile", "panel": 2, "type": 1},
                    {"index": 1, "name_jp": "まばたき", "name_en": "Blink", "panel": 2, "type": 1},
                ],
                model_root="test_model",
            )

        self.assertEqual(view.display_frame_tree.topLevelItem(0).child(0).text(0), "センター")
        expressions = view.display_frame_tree.topLevelItem(1)
        self.assertEqual(expressions.child(0).text(0), "笑い")
        self.assertEqual(expressions.child(1).text(0), "まばたき")

    def test_model_combo_updated_on_model_list_signal(self):
        presenter, view, app_state, _ = self._make()

        presenter.on_model_list_updated(["model_A", "model_B"])

        self.assertEqual(len(view.model_combo._items), 2)


class TestBodyPickerPresenter(unittest.TestCase):
    def _make_with_bones(self, bone_names=None, model_root="test_model"):
        view = _FakeView()
        app_state = _FakeAppState(model_root=model_root)
        joints_by_index = {}
        bn = bone_names or {}
        for i, joint in enumerate(bn.keys()):
            joints_by_index[i] = joint
        adapter = _FakeAdapter(
            joints_by_index=joints_by_index,
            bone_names=bn,
        )
        with patch(
            "mmd_tools.ui.presenters.animation_presenter"
            ".AnimationPresenter._populate_morph_groups"
        ), patch(
            "mmd_tools.ui.presenters.animation_presenter"
            ".AnimationPresenter._populate_display_frame_tree"
        ):
            presenter = AnimationPresenter(view, app_state, maya_adapter=adapter)
        return presenter, view, app_state, adapter

    def test_region_click_selects_bone(self):
        presenter, view, _, adapter = self._make_with_bones(
            bone_names={"head_jnt": "頭", "neck_jnt": "首"},
        )
        presenter.on_body_region_clicked("head")
        self.assertEqual(adapter.selected, ["head_jnt"])
        self.assertEqual(view.status_label.text(), "頭")
        self.assertEqual(view.body_picker.selected_regions, ["head"])

    def test_leg_ik_toggle_hides_only_that_sides_fk_regions(self):
        presenter, view, _, adapter = self._make_with_bones()
        presenter._ik_nodes_by_side = {
            "left": "left_leg_ik_solver",
            "right": "right_leg_ik_solver",
        }
        adapter._attrs["left_leg_ik_solver", "enabled"] = False
        adapter._attrs["right_leg_ik_solver", "enabled"] = False

        view.body_picker.ik_toggled.emit("left", True)

        self.assertTrue(adapter._attrs["left_leg_ik_solver", "enabled"])
        self.assertEqual(
            view.body_picker.hidden_regions,
            {
                "left_lower_leg",
                "left_foot",
                "left_toe_ik",
                "right_ik",
                "right_toe_ik",
            },
        )
        self.assertNotIn("left_upper_leg", view.body_picker.hidden_regions)
        self.assertNotIn("left_ik", view.body_picker.hidden_regions)
        self.assertEqual(view.body_picker.region_dim_levels, {"ik_enable_right": 0.65})
        self.assertIn("L IK enabled", view.status_label.text())

    def test_ik_enable_toggle_switches_one_sides_solvers_without_hiding_thighs(self):
        presenter, view, _, adapter = self._make_with_bones()
        presenter._ik_nodes_by_side = {
            "left": "left_leg_ik_solver",
            "right": "right_leg_ik_solver",
        }
        presenter._toe_ik_nodes_by_side = {
            "left": "left_toe_ik_solver",
            "right": "right_toe_ik_solver",
        }
        adapter._attrs["left_leg_ik_solver", "enabled"] = False
        adapter._attrs["right_leg_ik_solver", "enabled"] = False
        adapter._attrs["left_toe_ik_solver", "enabled"] = False
        adapter._attrs["right_toe_ik_solver", "enabled"] = False

        view.body_picker.ik_enable_toggle_clicked.emit("left")

        self.assertTrue(adapter._attrs["left_leg_ik_solver", "enabled"])
        self.assertFalse(adapter._attrs["right_leg_ik_solver", "enabled"])
        self.assertTrue(adapter._attrs["left_toe_ik_solver", "enabled"])
        self.assertFalse(adapter._attrs["right_toe_ik_solver", "enabled"])
        self.assertEqual(
            view.body_picker.hidden_regions,
            {
                "left_lower_leg",
                "left_foot",
                "left_toe",
                "right_ik",
                "right_toe_ik",
            },
        )
        self.assertEqual(view.body_picker.region_dim_levels, {"ik_enable_right": 0.65})

        view.body_picker.ik_enable_toggle_clicked.emit("left")

        self.assertFalse(adapter._attrs["left_leg_ik_solver", "enabled"])
        self.assertFalse(adapter._attrs["right_leg_ik_solver", "enabled"])
        self.assertFalse(adapter._attrs["left_toe_ik_solver", "enabled"])
        self.assertFalse(adapter._attrs["right_toe_ik_solver", "enabled"])
        self.assertEqual(
            view.body_picker.hidden_regions,
            {"left_ik", "right_ik", "left_toe_ik", "right_toe_ik"},
        )
        self.assertEqual(
            view.body_picker.region_dim_levels,
            {"ik_enable_left": 0.65, "ik_enable_right": 0.65},
        )

    def test_ik_enable_toggle_rolls_back_when_one_solver_update_fails(self):
        presenter, view, _, adapter = self._make_with_bones()
        presenter._ik_nodes_by_side = {
            "left": "left_leg_ik_solver",
        }
        presenter._toe_ik_nodes_by_side = {"left": "left_toe_ik_solver"}
        adapter._attrs["left_leg_ik_solver", "enabled"] = False
        adapter._attrs["left_toe_ik_solver", "enabled"] = False
        set_attr = adapter.set_attr

        def fail_toe_solver(attr_path, value):
            if attr_path == "left_toe_ik_solver.enabled":
                raise RuntimeError("toe solver is not settable")
            set_attr(attr_path, value)

        adapter.set_attr = fail_toe_solver

        view.body_picker.ik_enable_toggle_clicked.emit("left")

        self.assertFalse(adapter._attrs["left_leg_ik_solver", "enabled"])
        self.assertFalse(adapter._attrs["left_toe_ik_solver", "enabled"])
        self.assertEqual(
            view.body_picker.hidden_regions,
            {"left_ik", "right_ik", "left_toe_ik", "right_toe_ik"},
        )
        self.assertIn("IK toggle failed", view.status_label.text())

    def test_body_picker_targets_owned_control_in_every_rig_state(self):
        presenter, _view, _app_state, adapter = self._make_with_bones(
            bone_names={"head_jnt": "頭"},
        )
        metadata = {
            "state": "ATTACHED",
            "controls": {"master": "master-control-uuid"},
            "bindings": {"master": {"jointUuid": "head-joint-uuid"}},
        }

        def resolve_node(node, **_kwargs):
            return {
                "head_jnt": ["|model|head_jnt"],
                "head-joint-uuid": ["|model|head_jnt"],
                "master-control-uuid": ["|MMD_CONTROLS_GRP|master_ctrl"],
            }.get(node, [node])

        with patch(
            "mmd_tools.core.mmd_control_rig_builder.read_mmd_control_rig_metadata",
            return_value=metadata,
        ), patch("maya.cmds.ls", side_effect=resolve_node):
            for state in ("ATTACHED", "EDIT", "BAKED"):
                metadata["state"] = state
                presenter.on_body_region_clicked("head")
                self.assertEqual(adapter.selected, ["|MMD_CONTROLS_GRP|master_ctrl"])

    def test_body_picker_falls_back_to_joint_for_missing_or_invalid_metadata(self):
        presenter, _view, _app_state, adapter = self._make_with_bones(
            bone_names={"head_jnt": "頭"},
        )

        with patch(
            "mmd_tools.core.mmd_control_rig_builder.read_mmd_control_rig_metadata",
            side_effect=(None, {"state": "UNKNOWN"}),
        ):
            presenter.on_body_region_clicked("head")
            self.assertEqual(adapter.selected, ["head_jnt"])
            presenter.on_body_region_clicked("head")
            self.assertEqual(adapter.selected, ["head_jnt"])

    def test_picker_tooltip_uses_english_metadata_outside_japanese(self):
        presenter, view, _, adapter = self._make_with_bones(
            bone_names={"head_jnt": "頭"},
        )
        adapter._attrs[("head_jnt", "mmd_bone_name_en")] = "Head"

        presenter._build_picker_english_tooltips()
        presenter._retranslate_picker_bone_tooltips()

        self.assertEqual(view.body_picker.region_tooltips["head"], "Head")

        view.current_language = lambda: "ja"
        presenter._retranslate_picker_bone_tooltips()
        self.assertEqual(view.body_picker.region_tooltips["head"], "頭")

    def test_body_picker_tooltip_prefers_ui_translation_for_fixed_regions(self):
        presenter, view, _, _adapter = self._make_with_bones()
        view.current_language = lambda: "zh_cn"
        translate = view.tr
        view.tr = lambda key, category=None: (
            "腰" if (key, category) == ("waist", "animation_picker") else translate(key, category)
        )

        presenter._retranslate_picker_bone_tooltips()

        self.assertEqual(view.body_picker.region_tooltips["waist"], "腰")

    def test_region_click_unmapped_bone(self):
        presenter, view, _, adapter = self._make_with_bones(bone_names={})
        presenter.on_body_region_clicked("head")
        self.assertEqual(adapter.selected, [])
        self.assertIn("Unassigned", view.status_label.text())

    def test_goto_finger_switches_tab(self):
        presenter, view, _, _ = self._make_with_bones()
        presenter.on_goto_finger()
        self.assertEqual(view.picker_tabs._current, view.TAB_FINGER)

    def test_goto_body_switches_tab(self):
        presenter, view, _, _ = self._make_with_bones()
        presenter.on_goto_body()
        self.assertEqual(view.picker_tabs._current, view.TAB_BODY)

    def test_finger_region_click_selects_bone(self):
        presenter, view, _, adapter = self._make_with_bones(
            bone_names={"left_thumb_jnt": "左親指０"},
        )
        presenter.on_finger_region_clicked("left_thumb_0")
        self.assertEqual(adapter.selected, ["left_thumb_jnt"])
        self.assertEqual(view.status_label.text(), "左親指０")
        self.assertEqual(view.finger_picker.selected_regions, ["left_thumb_0"])

    def test_rectangle_selection_selects_multiple_bones(self):
        presenter, view, _, adapter = self._make_with_bones(
            bone_names={"head_jnt": "頭", "neck_jnt": "首", "arm_jnt": "左腕"},
        )

        view.body_picker.regions_selected.emit(["head", "neck", "left_upper_arm"])

        self.assertEqual(adapter.selected, ["head_jnt", "neck_jnt", "arm_jnt"])
        self.assertEqual(
            set(view.body_picker.selected_regions),
            {"head", "neck", "left_upper_arm"},
        )

    def test_select_all_selects_every_model_joint(self):
        presenter, view, _, adapter = self._make_with_bones(
            bone_names={"head_jnt": "頭", "neck_jnt": "首", "arm_jnt": "左腕"},
        )

        view.select_all_btn.clicked.emit()

        self.assertEqual(adapter.selected, ["head_jnt", "neck_jnt", "arm_jnt"])
        self.assertIn("Selected all bones", view.status_label.text())

    def test_shift_click_adds_picker_bone_to_current_selection(self):
        presenter, view, _, adapter = self._make_with_bones(
            bone_names={"head_jnt": "頭", "neck_jnt": "首"},
        )
        adapter.selected = ["head_jnt"]
        view.body_picker.additive_selection = True

        view.body_picker.region_clicked.emit("neck")

        self.assertEqual(adapter.selected, ["head_jnt", "neck_jnt"])
        self.assertEqual(set(view.body_picker.selected_regions), {"head", "neck"})

    def test_shift_rectangle_adds_regions_to_current_selection(self):
        presenter, view, _, adapter = self._make_with_bones(
            bone_names={"head_jnt": "頭", "neck_jnt": "首", "arm_jnt": "左腕"},
        )
        adapter.selected = ["head_jnt"]
        view.body_picker.additive_selection = True

        view.body_picker.regions_selected.emit(["neck", "left_upper_arm"])

        self.assertEqual(adapter.selected, ["head_jnt", "neck_jnt", "arm_jnt"])

    def test_body_picker_all_button_selects_every_model_joint(self):
        presenter, view, _, adapter = self._make_with_bones(
            bone_names={"head_jnt": "頭", "neck_jnt": "首"},
        )

        view.body_picker.select_all_clicked.emit()

        self.assertEqual(adapter.selected, ["head_jnt", "neck_jnt"])

    def test_body_picker_clear_button_clears_selection(self):
        presenter, view, _, adapter = self._make_with_bones(
            bone_names={"head_jnt": "頭"},
        )
        adapter.selected = ["head_jnt"]

        view.body_picker.clear_selection_clicked.emit()

        self.assertEqual(adapter.selected, [])

    def test_mirror_selection(self):
        presenter, view, _, adapter = self._make_with_bones(
            bone_names={"left_arm_jnt": "左腕", "right_arm_jnt": "右腕"},
        )
        adapter.selected = ["left_arm_jnt"]
        presenter.on_mirror_selection()
        self.assertIn("right_arm_jnt", adapter.selected)

    def test_bone_name_map_cleared_on_model_clear(self):
        presenter, _, app_state, _ = self._make_with_bones(
            bone_names={"head_jnt": "頭"},
        )
        self.assertGreater(len(presenter._bone_name_to_joint), 0)
        with patch(
            "mmd_tools.ui.presenters.animation_presenter"
            ".AnimationPresenter._populate_morph_groups"
        ):
            app_state.current_model_changed.emit("")
        self.assertEqual(len(presenter._bone_name_to_joint), 0)


SAMPLE_BLEND_SHAPES = {
    "body_mesh": {
        "blendShape1": {
            "type": "blendShape",
            "morph_json": {"0": "笑い", "1": "怒り", "2": "まばたき"},
            "aliases": ["笑い", "weight[0]", "怒り", "weight[1]", "まばたき", "weight[2]"],
        }
    }
}


class TestAnimationPresenterMorph(unittest.TestCase):
    _POPULATE_PATH = (
        "mmd_tools.ui.presenters.animation_presenter"
        ".AnimationPresenter._populate_morph_groups"
    )

    def _make_with_morphs(
        self,
        blend_shapes=None,
        model_root="test_model",
        morph_data=None,
    ):
        view = _FakeView()
        app_state = _FakeAppState(model_root=model_root)
        adapter = _FakeAdapter(blend_shapes=blend_shapes or {}, morph_data=morph_data)
        with patch(self._POPULATE_PATH):
            presenter = AnimationPresenter(view, app_state, maya_adapter=adapter)
        adapter._set_attrs.clear()
        adapter._connections.clear()
        return presenter, view, app_state, adapter

    def test_collect_morph_infos(self):
        presenter, _, _, _ = self._make_with_morphs(
            blend_shapes=SAMPLE_BLEND_SHAPES,
        )
        infos = presenter._collect_morph_infos("test_model")
        names = [m.name for m in infos]
        self.assertIn("笑い", names)
        self.assertIn("怒り", names)
        self.assertIn("まばたき", names)

    def test_authoritative_morph_metadata_supplies_panel_and_global_index(self):
        morph_data = [
            {"index": 19, "name_jp": "笑い", "name_en": "Smile", "panel": 2, "type": 1},
        ]
        blend_shapes = {
            "body_mesh": {
                "blendShape1": {
                    "type": "blendShape",
                    "morph_json": {"0": {"name": "笑い", "index": 19}},
                }
            }
        }
        presenter, _, _, _ = self._make_with_morphs(
            blend_shapes=blend_shapes,
            morph_data=morph_data,
        )

        info = presenter._read_morph_metadata("test_model")[19]
        self.assertEqual(info.name, "笑い")
        self.assertEqual(info.panel, 2)
        self.assertEqual(presenter._morph_targets["笑い"], [("blendShape1", 0)])

    def test_morph_targets_tracked(self):
        presenter, _, _, _ = self._make_with_morphs(
            blend_shapes=SAMPLE_BLEND_SHAPES,
        )
        self.assertEqual(presenter._morph_targets["笑い"], [("blendShape1", 0)])
        self.assertEqual(presenter._morph_targets["怒り"], [("blendShape1", 1)])

    def test_collect_empty_blend_shapes(self):
        presenter, _, _, _ = self._make_with_morphs(blend_shapes={})
        infos = presenter._collect_morph_infos("test_model")
        self.assertEqual(len(infos), 0)

    def test_clear_morph_tab(self):
        presenter, view, _, _ = self._make_with_morphs(
            blend_shapes=SAMPLE_BLEND_SHAPES,
        )
        presenter._morph_sliders["test"] = object()
        presenter._morph_targets["test"] = "bs1"

        presenter._clear_morph_tab()

        self.assertEqual(len(presenter._morph_sliders), 0)
        self.assertEqual(len(presenter._morph_targets), 0)

    def test_morph_slider_sets_weight(self):
        presenter, _, _, adapter = self._make_with_morphs(
            blend_shapes=SAMPLE_BLEND_SHAPES,
        )
        presenter._on_morph_slider_changed("笑い", 50, _FakeLabel("0"))

        self.assertIn("blendShape1.weight[0]", adapter._set_attrs)
        self.assertAlmostEqual(adapter._set_attrs["blendShape1.weight[0]"], 0.5)
        self.assertEqual(adapter._undo_chunks, ["Edit MMD Morph"])

    def test_controller_plug_is_primary_authority_over_blendshape_fallback(self):
        presenter, _, _, adapter = self._make_with_morphs(
            blend_shapes=SAMPLE_BLEND_SHAPES,
        )
        presenter._morph_controller = "morphController"
        presenter._morph_indices["笑い"] = 19

        presenter._on_morph_weight_changed("笑い", 0.375)

        self.assertEqual(
            adapter._set_attrs["morphController.inputWeight[19]"], 0.375
        )
        self.assertNotIn("blendShape1.weight[0]", adapter._set_attrs)

    def test_animation_state_distinguishes_current_key_and_interpolation(self):
        presenter, _, _, adapter = self._make_with_morphs(
            blend_shapes=SAMPLE_BLEND_SHAPES,
        )
        adapter.current_time = lambda: 12.0
        current_key = {"value": True}

        def keyframe(target, **kwargs):
            if kwargs.get("name"):
                return ["animCurve1"]
            if kwargs.get("keyframeCount"):
                return 1 if current_key["value"] else 0
            return []

        adapter.keyframe = keyframe
        plug = "blendShape1.weight[0]"

        self.assertEqual(presenter._morph_animation_state(plug), "key")
        current_key["value"] = False
        self.assertEqual(presenter._morph_animation_state(plug), "animated")

        adapter.keyframe = lambda *_args, **_kwargs: []
        self.assertEqual(presenter._morph_animation_state(plug), "static")

    def test_animation_state_checks_every_split_legacy_target(self):
        presenter, _, _, adapter = self._make_with_morphs(
            blend_shapes=SAMPLE_BLEND_SHAPES,
        )
        adapter.current_time = lambda: 12.0

        def keyframe(target, **kwargs):
            if kwargs.get("name"):
                return ["animCurveSecond"] if target == "second.weight[0]" else []
            if kwargs.get("keyframeCount"):
                return 1
            return []

        adapter.keyframe = keyframe
        self.assertEqual(
            presenter._morph_animation_state(
                ("first.weight[0]", "second.weight[0]")
            ),
            "key",
        )

    def test_refresh_does_not_overwrite_uncommitted_numeric_input(self):
        presenter, view, _, adapter = self._make_with_morphs(
            blend_shapes=SAMPLE_BLEND_SHAPES,
        )

        class EditingRow:
            plugs = ()

            class Editor:
                is_editing = True

            editor = Editor()

            def set_value(self, _value):
                raise AssertionError("editing value was overwritten")

            def set_animation_state(self, _state):
                pass

        presenter._morph_rows = {"笑い": EditingRow()}
        view.isVisible = lambda: True
        view.picker_tabs.setCurrentIndex(view.TAB_MORPH)
        adapter.current_time = lambda: 1.0
        presenter._last_morph_refresh_time = 1.0

        presenter._refresh_morph_rows()

    def test_morph_slider_unknown_morph_noop(self):
        presenter, _, _, adapter = self._make_with_morphs(
            blend_shapes=SAMPLE_BLEND_SHAPES,
        )
        presenter._on_morph_slider_changed("unknown", 50, _FakeLabel("0"))
        self.assertEqual(len(adapter._set_attrs), 0)

    def test_model_clear_resets_morph_state(self):
        presenter, _, app_state, _ = self._make_with_morphs(
            blend_shapes=SAMPLE_BLEND_SHAPES,
        )
        with patch(self._POPULATE_PATH):
            app_state.current_model_changed.emit("")
        self.assertEqual(len(presenter._morph_targets), 0)
        self.assertEqual(len(presenter._morph_rows), 0)

    def test_split_morph_drives_all_nodes(self):
        split_bs = {
            "mesh_face": {
                "bs_face": {
                    "type": "blendShape",
                    "morph_json": {"0": "笑い"},
                }
            },
            "mesh_hair": {
                "bs_hair": {
                    "type": "blendShape",
                    "morph_json": {"0": "笑い"},
                }
            },
        }
        presenter, _, _, adapter = self._make_with_morphs(blend_shapes=split_bs)
        self.assertEqual(len(presenter._morph_targets["笑い"]), 2)

        presenter._on_morph_slider_changed("笑い", 100, _FakeLabel("0"))

        self.assertAlmostEqual(adapter._set_attrs["bs_face.weight[0]"], 1.0)
        self.assertAlmostEqual(adapter._set_attrs["bs_hair.weight[0]"], 1.0)


class TestVisibilityToggle(unittest.TestCase):
    def _make_with_model(self, model_root="test_model"):
        view = _FakeView()
        app_state = _FakeAppState(model_root=model_root)
        adapter = _FakeAdapter(
            joints_by_index={0: "head_jnt"},
            bone_names={"head_jnt": "頭"},
        )
        with patch(
            "mmd_tools.ui.presenters.animation_presenter"
            ".AnimationPresenter._populate_morph_groups"
        ), patch(
            "mmd_tools.ui.presenters.animation_presenter"
            ".AnimationPresenter._populate_display_frame_tree"
        ):
            presenter = AnimationPresenter(view, app_state, maya_adapter=adapter)
        return presenter, view, app_state, adapter

    def test_visibility_toggle_sets_attr(self):
        presenter, _, _, adapter = self._make_with_model()
        presenter._on_visibility_changed("joints", False)
        self.assertEqual(adapter._set_attrs["test_model.mmd_show_joints"], False)
        self.assertIn(
            ("test_model.mmd_show_joints", "|test_model|Skeleton.visibility", False),
            adapter._connections,
        )

    def test_collider_visibility_uses_root_attr_and_draw_enabled(self):
        presenter, _, _, adapter = self._make_with_model()
        adapter._joints_by_index = {0: "head_jnt", 1: "rb_locator_shape"}

        presenter._on_visibility_changed("colliders", False)

        self.assertEqual(adapter._set_attrs["test_model.mmd_show_physics_colliders"], False)
        self.assertIn(
            ("test_model.mmd_show_physics_colliders", "|test_model|Physics.visibility", False),
            adapter._connections,
        )

    def test_visibility_sync_preserves_existing_visibility_driver(self):
        presenter, _, _, adapter = self._make_with_model()
        adapter._connections.clear()
        adapter._incoming_connections["|test_model|Skeleton.visibility"] = ["animCurve1.output"]

        presenter._on_visibility_changed("joints", False)

        self.assertNotIn(
            ("test_model.mmd_show_joints", "|test_model|Skeleton.visibility", False),
            adapter._connections,
        )

    def test_visibility_morphs_is_noop(self):
        presenter, _, _, adapter = self._make_with_model()
        adapter._set_attrs.clear()
        presenter._on_visibility_changed("morphs", False)
        self.assertEqual(len(adapter._set_attrs), 0)

    def test_visibility_no_model_is_noop(self):
        presenter, _, _, adapter = self._make_with_model(model_root=None)
        presenter._on_visibility_changed("joints", False)
        self.assertEqual(len(adapter._set_attrs), 0)

    def test_control_rig_visibility_targets_uuid_owned_group(self):
        presenter, view, _, adapter = self._make_with_model()
        button = _FakeCheckBox("control_rig")
        view.vis_checkboxes["control_rig"] = button
        group = "|test_model|Controls"
        adapter._attrs[(group, "visibility")] = True

        with patch.object(presenter, "_control_rig_group", return_value=group):
            presenter._sync_visibility_controls("test_model")
            presenter._on_visibility_changed("control_rig", False)

        self.assertTrue(button._control_rig_available)
        self.assertTrue(button.isChecked())
        self.assertFalse(adapter._set_attrs[f"{group}.visibility"])


class TestToolsSection(unittest.TestCase):
    _POPULATE_PATH = (
        "mmd_tools.ui.presenters.animation_presenter"
        ".AnimationPresenter._populate_morph_groups"
    )

    def _make(self, model_root="test_model"):
        view = _FakeView()
        app_state = _FakeAppState(model_root=model_root)
        adapter = _FakeAdapter(
            joints_by_index={0: "j1", 1: "j2"},
            bone_names={"j1": "センター", "j2": "上半身"},
        )
        adapter._transforms["j1"] = ([1, 2, 3], [10, 20, 30])
        adapter._transforms["j2"] = ([4, 5, 6], [40, 50, 60])
        with patch(self._POPULATE_PATH):
            presenter = AnimationPresenter(
                view,
                app_state,
                maya_adapter=adapter,
            )
        return presenter, view, app_state, adapter

    def test_copy_pose_stores_clipboard(self):
        presenter, view, _, adapter = self._make()
        adapter.selected = ["j1"]
        presenter._on_tool_clicked("copy")
        self.assertIsNotNone(presenter._pose_clipboard)
        self.assertIn("j1", presenter._pose_clipboard)
        self.assertIn("Copied", view.status_label.text())

    def test_copy_pose_no_selection(self):
        presenter, view, _, _ = self._make()
        presenter._on_tool_clicked("copy")
        self.assertIn("No joints", view.status_label.text())

    def test_paste_pose_applies_clipboard(self):
        presenter, view, _, adapter = self._make()
        adapter.selected = ["j1"]
        presenter._on_tool_clicked("copy")
        adapter._transforms["j1"] = ([0, 0, 0], [0, 0, 0])
        presenter._on_tool_clicked("paste")
        t, r = adapter._transforms["j1"]
        self.assertEqual(t, [1, 2, 3])
        self.assertEqual(r, [10, 20, 30])
        self.assertIn("Pasted", view.status_label.text())

    def test_paste_pose_no_clipboard(self):
        presenter, view, _, _ = self._make()
        presenter._on_tool_clicked("paste")
        self.assertIn("No pose copied", view.status_label.text())

    def test_reset_pose_applies_bind_translation_and_zeroes_rotation(self):
        presenter, view, _, adapter = self._make()
        adapter.selected = ["j1"]
        adapter._attrs["j1", "mmd_vmd_bind_translate"] = "[1.0, 2.0, 3.0]"

        presenter._on_tool_clicked("reset")

        self.assertEqual(adapter._transforms["j1"], ([1.0, 2.0, 3.0], [0, 0, 0]))
        self.assertIn("Reset Pose", view.status_label.text())
        self.assertTrue(view.picker_tabs.enabled)
        self.assertTrue(all(button.enabled for button in view.tool_buttons.values()))

    def test_body_picker_reset_uses_the_same_selection_only_action(self):
        presenter, view, _, adapter = self._make()
        adapter.selected = ["j2"]
        adapter._attrs["j2", "mmd_vmd_bind_translate"] = "[4.0, 5.0, 6.0]"

        view.body_picker.reset_pose_clicked.emit()

        self.assertEqual(adapter._transforms["j2"], ([4.0, 5.0, 6.0], [0, 0, 0]))
        self.assertTrue(view.picker_tabs.enabled)

    def test_reset_pose_without_selection_only_reports_status(self):
        presenter, view, _, adapter = self._make()
        before = {joint: (list(t), list(r)) for joint, (t, r) in adapter._transforms.items()}

        presenter._on_tool_clicked("reset")

        self.assertIn("No joints", view.status_label.text())
        self.assertEqual(adapter._transforms, before)
        self.assertEqual(adapter._undo_chunks, [])

    def test_reset_pose_has_no_shared_mode_state(self):
        presenter, view, _, adapter = self._make()
        self.assertFalse(hasattr(presenter, "rest_pose_manager"))
        self.assertTrue(view.picker_tabs.enabled)
        adapter.selected = ["j1"]
        presenter._on_tool_clicked("reset")
        self.assertTrue(view.picker_tabs.enabled)

    def test_mirror_stub_shows_error(self):
        presenter, view, _, adapter = self._make()
        adapter.selected = ["j1"]
        presenter._on_tool_clicked("mirror")
        self.assertIn("Mirror", view.status_label.text())
        self.assertIn("not yet implemented", view.status_label.text().lower())

    def test_bake_stub_shows_error(self):
        presenter, view, _, adapter = self._make()
        adapter.selected = ["j1"]
        presenter._on_tool_clicked("bake")
        self.assertIn("Bake", view.status_label.text())

    def test_clean_stub_shows_error(self):
        presenter, view, _, adapter = self._make()
        adapter.selected = ["j1"]
        presenter._on_tool_clicked("clean")
        self.assertIn("Clean", view.status_label.text())


if __name__ == "__main__":
    unittest.main()
