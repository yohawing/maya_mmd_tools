"""E2E integration test for AnimationPresenter with real Maya scene.

Imports a PMX model via mayapy and verifies the full data path:
display frame tree, body/finger picker bone selection, morph slider
weight control, and pose copy/paste/reset.
"""


from maya import cmds

from tests.common.maya_test_base import MayaTestBase
from tests.common.test_fixture_provider import TestFixtureProvider
from mmd_tools.adapters.maya_cmds_adapter import MayaCmdsAdapter
from mmd_tools.core.constants import (
    ATTR_MMD_DISPLAY_FRAMES_JSON,
    ATTR_MMD_SHOW_JOINTS,
    ATTR_MMD_SHOW_MESH,
)
from mmd_tools.io.pmx_importer import import_pmx_file
from mmd_tools.core.mmd_parser import parse_pmx_file


# -- Minimal FakeView for headless presenter testing --


class _Signal:
    def __init__(self):
        self._cbs = []

    def connect(self, cb):
        self._cbs.append(cb)

    def disconnect(self, cb):
        if cb in self._cbs:
            self._cbs.remove(cb)

    def emit(self, *a):
        for cb in self._cbs:
            cb(*a)


class _Btn:
    def __init__(self):
        self.clicked = _Signal()


class _Combo:
    def __init__(self):
        self._items = []
        self._index = -1
        self.currentTextChanged = _Signal()

    def clear(self):
        self._items.clear()

    def addItem(self, t, d=None):
        self._items.append((t, d))

    def findText(self, t):
        for i, (x, _) in enumerate(self._items):
            if x == t:
                return i
        return -1

    def setCurrentIndex(self, i):
        self._index = i

    def blockSignals(self, _):
        pass

    def setSizeAdjustPolicy(self, _):
        pass

    def setToolTip(self, _):
        pass


class _Label:
    def __init__(self, t=""):
        self._t = t

    def text(self):
        return self._t

    def setText(self, t):
        self._t = t


class _TreeItem:
    def __init__(self, texts=None):
        self._texts = texts or [""]
        self._data = {}
        self._children = []
        self._expanded = False

    def text(self, col=0):
        return self._texts[col] if col < len(self._texts) else ""

    def data(self, col, role):
        return self._data.get((col, role))

    def setData(self, col, role, v):
        self._data[(col, role)] = v

    def addChild(self, c):
        self._children.append(c)

    def setExpanded(self, e):
        self._expanded = e

    def childCount(self):
        return len(self._children)

    def child(self, i):
        return self._children[i]


class _Tree:
    def __init__(self):
        self._items = []
        self.itemClicked = _Signal()
        self.itemPressed = _Signal()

    def clear(self):
        self._items.clear()

    def addTopLevelItem(self, item):
        self._items.append(item)

    def topLevelItemCount(self):
        return len(self._items)

    def topLevelItem(self, i):
        return self._items[i]

    def setHeaderHidden(self, _):
        pass


class _LayoutItem:
    def __init__(self, w=None):
        self._w = w

    def widget(self):
        return self._w


class _FakeWidget:
    def deleteLater(self):
        pass


class _Layout:
    def __init__(self):
        self._items = [_LayoutItem()]

    def count(self):
        return len(self._items)

    def takeAt(self, i):
        return self._items.pop(i) if 0 <= i < len(self._items) else _LayoutItem()

    def insertWidget(self, i, w):
        self._items.insert(i, _LayoutItem(w))


class _Picker:
    def __init__(self):
        self.region_clicked = _Signal()
        self.regions_selected = _Signal()
        self.goto_finger_clicked = _Signal()
        self.goto_body_clicked = _Signal()
        self.ik_toggled = _Signal()
        self.ik_enable_toggle_clicked = _Signal()
        self.selected_regions = []
        self.additive_selection = False

    def set_selected_regions(self, region_ids):
        self.selected_regions = list(region_ids)

    def update_region_texts(self, **_kwargs):
        pass


class _CheckBox:
    def __init__(self):
        self.stateChanged = _Signal()


class _TabWidget:
    def __init__(self):
        self._current = 0

    def setCurrentIndex(self, i):
        self._current = i


class _View:
    TAB_BODY = 0
    TAB_FINGER = 1
    TAB_MORPH = 2
    TAB_OTHER = 3

    def __init__(self):
        self.model_combo = _Combo()
        self.refresh_btn = _Btn()
        self.clear_btn = _Btn()
        self.select_all_btn = _Btn()
        self.status_label = _Label()
        self.display_frame_tree = _Tree()
        self.body_picker = _Picker()
        self.finger_picker = _Picker()
        self.morph_groups_layout = _Layout()
        self.picker_tabs = _TabWidget()
        self.vis_checkboxes = {
            k: _CheckBox()
            for k in ("mesh", "joints", "colliders")
        }
        self.tool_buttons = {
            k: _Btn()
            for k in ("copy", "paste", "mirror", "reset", "clean", "bake")
        }

    def current_language(self):
        """Match the locale endpoint exposed by the production AnimationTab."""

        return "ja"

    def tr(self, key, _category=None):
        """Return stable test messages through the production translation API."""

        return {
            "copied_pose": "Copied pose ({count} joints)",
            "pasted_pose": "Pasted pose ({count} joints)",
            "reset_pose_applied": "Reset Pose ({count} joints)",
            "rest_pose_applied": "Reset Pose ({count} joints)",
        }.get(key, key)


class _AppState:
    def __init__(self, root=None):
        self.current_model_changed = _Signal()
        self.model_list_updated = _Signal()
        self._root = root

    @property
    def current_model_root(self):
        return self._root

    @current_model_root.setter
    def current_model_root(self, v):
        self._root = v

    def refresh_model_list(self):
        pass


# ---- Tests ----

_POPULATE_PATH = (
    "mmd_tools.ui.presenters.animation_presenter"
    ".AnimationPresenter._populate_morph_groups"
)


class TestAnimationPresenterE2E(MayaTestBase):
    """Integration test: AnimationPresenter with real Maya scene."""

    def setUp(self):
        super().setUp()
        from mmd_tools.core import settings
        settings.set("import.model.create_mmd_shaders", False)
        self.fixture = TestFixtureProvider()
        self._imported_root = None

    def _import_model(self, name="mmt_test_model"):
        pmx_file = self.fixture.get_pmx_file(name)
        parser = parse_pmx_file(pmx_file)
        result = import_pmx_file(parser, pmx_file)
        self.assertTrue(result, "PMX import failed")
        self._imported_root = result
        return result

    def _make_presenter(self, model_root):
        from unittest.mock import patch

        view = _View()
        app_state = _AppState(root=model_root)
        adapter = MayaCmdsAdapter()
        with patch(_POPULATE_PATH):
            from mmd_tools.ui.presenters.animation_presenter import AnimationPresenter
            presenter = AnimationPresenter(view, app_state, maya_adapter=adapter)
        return presenter, view, adapter

    def test_display_frame_tree_populated(self):
        root = self._import_model()
        presenter, view, _ = self._make_presenter(root)

        has_frames = cmds.attributeQuery(
            ATTR_MMD_DISPLAY_FRAMES_JSON, node=root, exists=True
        )
        if has_frames:
            self.assertGreater(
                view.display_frame_tree.topLevelItemCount(), 0,
                "Display frame tree should have groups after import",
            )
        else:
            self.assertGreater(
                view.display_frame_tree.topLevelItemCount(), 0,
                "Fallback flat list should have at least one group",
            )

    def test_root_visibility_attrs_drive_geometry_and_skeleton_groups(self):
        root = self._import_model()
        self.assertFalse(cmds.attributeQuery("mmd_show_ik", node=root, exists=True))
        self.assertFalse(cmds.attributeQuery("mmd_show_controllers", node=root, exists=True))

        geometry = "Geometry"
        skeleton = "Skeleton"
        for attr, group in (
            (ATTR_MMD_SHOW_MESH, geometry),
            (ATTR_MMD_SHOW_JOINTS, skeleton),
        ):
            destinations = cmds.listConnections(
                f"{root}.{attr}", source=False, destination=True, plugs=True
            ) or []
            self.assertEqual(destinations, [f"{group}.visibility"])
            cmds.setAttr(f"{root}.{attr}", False)
            self.assertFalse(cmds.getAttr(f"{group}.visibility"))

    def test_display_frame_item_click_selects_joint(self):
        root = self._import_model()
        presenter, view, adapter = self._make_presenter(root)

        if view.display_frame_tree.topLevelItemCount() == 0:
            self.skipTest("No display frame groups")
        group = view.display_frame_tree.topLevelItem(0)
        if group.childCount() == 0:
            self.skipTest("No items in first group")

        child = group.child(0)
        _USER_ROLE = 0x0100
        node_name = child.data(0, _USER_ROLE)
        if not node_name:
            self.skipTest("First item has no resolved node")

        presenter.on_display_frame_item_clicked(child)
        sel = cmds.ls(selection=True) or []
        self.assertIn(node_name, sel)

    def test_body_picker_region_selects_joint(self):
        root = self._import_model()
        presenter, view, _ = self._make_presenter(root)

        if not presenter._bone_name_to_joint:
            self.skipTest("No bone name map (model has no mmd_bone_name)")

        presenter.on_body_region_clicked("head")
        status = view.status_label.text()
        if "unmapped" in status:
            self.skipTest("頭 bone not in this model")
        sel = cmds.ls(selection=True) or []
        self.assertTrue(len(sel) > 0, "Should select a joint for head region")
        self.assertEqual(view.body_picker.selected_regions, ["head"])

        presenter._select_picker_regions(["head"], picker="body", subtractive=True)

        self.assertEqual(cmds.ls(selection=True) or [], [])
        self.assertEqual(view.body_picker.selected_regions, [])

    def test_morph_picker_uses_japanese_metadata_and_drives_weight(self):
        root = self._import_model("test_morph_model")
        presenter, _, _ = self._make_presenter(root)

        metadata = presenter._read_morph_metadata(root)
        self.assertTrue(metadata, "Morph fixture should expose authoritative metadata")
        self.assertTrue(all(info.name for info in metadata.values()))
        self.assertTrue(presenter._morph_targets, "Vertex morph targets should resolve")

        morph_name = next(iter(presenter._morph_targets))
        blend_shape, weight_index = presenter._morph_targets[morph_name][0]
        presenter._on_morph_slider_changed(morph_name, 50, _Label("0"))

        self.assertAlmostEqual(
            cmds.getAttr(f"{blend_shape}.weight[{weight_index}]"),
            0.5,
            places=4,
        )

    def test_reset_pose_via_tool(self):
        root = self._import_model()
        presenter, view, adapter = self._make_presenter(root)

        joints = cmds.ls(type="joint")
        if not joints:
            self.skipTest("No joints")
        target = joints[0]

        bind_translate = cmds.xform(target, query=True, objectSpace=True, translation=True)
        cmds.xform(target, objectSpace=True, translation=(8, 9, 10))
        cmds.xform(target, rotation=(15, 30, 45))
        cmds.select(target, replace=True)

        presenter._on_tool_clicked("reset")

        r = cmds.xform(target, query=True, rotation=True)
        self.assertAlmostEqual(r[0], 0, places=3)
        self.assertAlmostEqual(r[1], 0, places=3)
        self.assertAlmostEqual(r[2], 0, places=3)
        t = cmds.xform(target, query=True, objectSpace=True, translation=True)
        for actual, expected in zip(t, bind_translate):
            self.assertAlmostEqual(actual, expected, places=3)
        self.assertIn("Reset Pose", view.status_label.text())

    def test_copy_paste_pose_round_trip(self):
        root = self._import_model()
        presenter, view, adapter = self._make_presenter(root)

        joints = cmds.ls(type="joint")
        if not joints:
            self.skipTest("No joints")
        target = joints[0]

        cmds.xform(target, rotation=(10, 20, 30))
        cmds.select(target, replace=True)

        presenter._on_tool_clicked("copy")
        self.assertIn("Copied", view.status_label.text())

        cmds.xform(target, rotation=(0, 0, 0))

        presenter._on_tool_clicked("paste")
        self.assertIn("Pasted", view.status_label.text())

        r = cmds.xform(target, query=True, rotation=True)
        self.assertAlmostEqual(r[0], 10, places=2)
        self.assertAlmostEqual(r[1], 20, places=2)
        self.assertAlmostEqual(r[2], 30, places=2)
