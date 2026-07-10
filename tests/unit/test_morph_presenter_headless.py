"""MorphPresenterのMaya非依存ロジックとadapter-routingを検証するテスト。"""

import json
import unittest
from unittest.mock import patch

from tests.common.maya_stub import install_headless_ui_stubs

install_headless_ui_stubs()

from mmd_tools.ui.presenters import morph_presenter as morph_presenter_module  # noqa: E402
from mmd_tools.ui.translations import UITranslator  # noqa: E402

MorphPresenter = morph_presenter_module.MorphPresenter
UITranslator.instance().set_language("en")

TEST_MODEL = "test_mmd_model"


class _FakeSignal:
    def __init__(self):
        self._callbacks = []

    def connect(self, callback):
        self._callbacks.append(callback)


class _FakeItem:
    def __init__(self, text):
        self._text = text
        self.hidden = False

    def text(self):
        return self._text

    def setHidden(self, hidden):
        self.hidden = hidden


class _FakeList:
    def __init__(self):
        self.currentItemChanged = _FakeSignal()
        self.clear_calls = 0
        self.items = []
        self._current_item = None

    def clear(self):
        self.clear_calls += 1
        self.items.clear()

    def addItem(self, item):
        self.items.append(item)

    def count(self):
        return len(self.items)

    def item(self, index):
        return self.items[index]

    def currentItem(self):
        return self._current_item

    def setCurrentItem(self, item):
        self._current_item = item


class _FakeButton:
    def __init__(self):
        self.clicked = _FakeSignal()


class _FakeLineEdit:
    def __init__(self, text=""):
        self.textChanged = _FakeSignal()
        self._text = text
        self.set_text_calls = []
        self.clear_calls = 0

    def text(self):
        return self._text

    def setText(self, text):
        self._text = text
        self.set_text_calls.append(text)

    def clear(self):
        self._text = ""
        self.clear_calls += 1


class _FakeComboBox:
    def __init__(self, current_text=""):
        self.currentIndexChanged = _FakeSignal()
        self.items = []
        self._current_text = current_text
        self._current_index = 0
        self.set_index_calls = []
        self.set_text_calls = []
        if current_text:
            self.items.append(current_text)

    def clear(self):
        self.items.clear()
        self._current_text = ""
        self._current_index = 0

    def addItems(self, items):
        self.items.extend(items)
        if not self._current_text and items:
            self._current_text = items[0]

    def addItem(self, item):
        self.items.append(item)

    def removeItem(self, index):
        del self.items[index]

    def findText(self, text):
        try:
            return self.items.index(text)
        except ValueError:
            return -1

    def currentText(self):
        return self._current_text

    def setCurrentText(self, text):
        self._current_text = text
        self.set_text_calls.append(text)
        if text not in self.items:
            self.items.append(text)

    def currentIndex(self):
        return self._current_index

    def setCurrentIndex(self, index):
        self._current_index = index
        self.set_index_calls.append(index)


class _FakeSlider:
    def __init__(self):
        self.valueChanged = _FakeSignal()
        self.value = 0
        self.set_value_calls = []

    def setValue(self, value):
        self.value = value
        self.set_value_calls.append(value)


class _FakeLabel:
    def __init__(self):
        self.text = ""
        self.styles = []

    def setText(self, text):
        self.text = text

    def setStyleSheet(self, stylesheet):
        self.styles.append(stylesheet)


class _FakeCheckBox:
    def __init__(self, checked=False):
        self._checked = checked

    def isChecked(self):
        return self._checked


class _FakeSpinBox:
    def __init__(self, value=1.0):
        self._value = value

    def value(self):
        return self._value


class _FakeTable:
    def __init__(self):
        self.row_count_calls = []

    def setRowCount(self, row_count):
        self.row_count_calls.append(row_count)


class _FakeView:
    def __init__(self):
        self.morph_list = _FakeList()
        self.group_list = _FakeList()

        self.refresh_morphs_btn = _FakeButton()
        self.select_in_maya_btn = _FakeButton()
        self.add_group_btn = _FakeButton()
        self.remove_group_btn = _FakeButton()
        self.reset_slider_btn = _FakeButton()
        self.reset_all_btn = _FakeButton()
        self.connect_btn = _FakeButton()
        self.disconnect_btn = _FakeButton()
        self.auto_connect_btn = _FakeButton()
        self.select_blend_shape_btn = _FakeButton()
        self.apply_btn = _FakeButton()
        self.reset_btn = _FakeButton()
        self.save_preset_btn = _FakeButton()
        self.load_preset_btn = _FakeButton()
        self.delete_preset_btn = _FakeButton()

        self.search_edit = _FakeLineEdit()
        self.morph_name_jp_edit = _FakeLineEdit()
        self.morph_name_en_edit = _FakeLineEdit()
        self.blend_shape_edit = _FakeLineEdit()
        self.target_name_edit = _FakeLineEdit()
        self.panel_combo = _FakeComboBox()
        self.morph_type_combo = _FakeComboBox()
        self.group_combo = _FakeComboBox()
        self.preset_combo = _FakeComboBox("なし")

        self.morph_slider = _FakeSlider()
        self.morph_value_label = _FakeLabel()
        self.connection_status_label = _FakeLabel()
        self.offset_count_label = _FakeLabel()

        self.invert_check = _FakeCheckBox()
        self.multiplier_spin = _FakeSpinBox()
        self.offset_table = _FakeTable()

        self.details_enabled_calls = []
        self.tr_calls = []

    def set_morph_details_enabled(self, enabled):
        self.details_enabled_calls.append(enabled)

    def tr(self, key, context):
        self.tr_calls.append((key, context))
        return f"{context}:{key}"


class _FakeAppState:
    def __init__(self, current_model_root=None):
        self.current_model_root = current_model_root
        self.current_model_changed = _FakeSignal()
        self.statuses = []

    def emit_status(self, message, level=None):
        self.statuses.append((message, level))


class _FakeMayaAdapter:
    def __init__(self):
        self.calls = []
        self.existing = set()
        self.attr_exists = set()
        self.attr_values = {}
        self.relatives = {}
        self.history = {}
        self.ls_results = {}
        self.aliases = {}
        self.node_types = {}

    def object_exists(self, node):
        self.calls.append(("object_exists", node))
        return node in self.existing

    def attribute_exists(self, attr, node):
        self.calls.append(("attribute_exists", attr, node))
        return (node, attr) in self.attr_exists

    def get_attr(self, attr_path):
        self.calls.append(("get_attr", attr_path))
        return self.attr_values[attr_path]

    def set_attr(self, attr_path, value):
        self.calls.append(("set_attr", attr_path, value))
        self.attr_values[attr_path] = value

    def list_relatives(self, node, *args, **kwargs):
        self.calls.append(("list_relatives", node, args, kwargs))
        return self.relatives.get((node, tuple(sorted(kwargs.items()))), [])

    def list_history(self, node):
        self.calls.append(("list_history", node))
        return self.history.get(node, [])

    def ls(self, *args, **kwargs):
        self.calls.append(("ls", args, kwargs))
        key = (_freeze(args), tuple(sorted(kwargs.items())))
        return self.ls_results.get(key, [])

    def alias_attr(self, node, **kwargs):
        self.calls.append(("alias_attr", node, kwargs))
        return self.aliases.get(node, [])

    def select(self, node, **kwargs):
        self.calls.append(("select", node, kwargs))

    def node_type(self, node):
        self.calls.append(("node_type", node))
        return self.node_types.get(node, "")


def _make_presenter(model=None, adapter=None):
    view = _FakeView()
    app_state = _FakeAppState(None)
    adapter = adapter or _FakeMayaAdapter()
    presenter = MorphPresenter(view, app_state, maya_adapter=adapter)
    app_state.current_model_root = model
    return presenter, view, app_state, adapter


def _freeze(value):
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, tuple):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, dict):
        return tuple(sorted((key, _freeze(item)) for key, item in value.items()))
    return value


class TestMorphPresenterHeadless(unittest.TestCase):
    def test_init_connects_signals_without_maya_adapter_calls(self):
        presenter, view, app_state, adapter = _make_presenter()

        self.assertIs(presenter.maya_adapter, adapter)
        self.assertEqual(app_state.current_model_changed._callbacks, [presenter.on_current_model_changed])
        self.assertEqual(view.morph_list.currentItemChanged._callbacks, [presenter.on_morph_selected])
        self.assertEqual(view.refresh_morphs_btn.clicked._callbacks, [presenter.load_morphs])
        self.assertEqual(view.save_preset_btn.clicked._callbacks, [presenter.save_preset])
        self.assertIsNone(presenter.blend_shape_node)
        self.assertIsNone(presenter.current_morph)
        self.assertEqual(presenter.morph_data, {})
        self.assertEqual(adapter.calls, [])

    def test_load_morphs_no_model_clears_state_and_returns_before_adapter(self):
        presenter, view, _, adapter = _make_presenter(model=None)
        presenter.morph_data = {"stale": {"group": "目"}}
        presenter.group_morphs = {"目": ["stale"]}
        presenter.current_morph = "stale"

        presenter.load_morphs()

        self.assertEqual(view.morph_list.clear_calls, 1)
        self.assertEqual(view.details_enabled_calls, [False])
        self.assertEqual(presenter.morph_data, {})
        self.assertEqual(presenter.group_morphs, {})
        self.assertIsNone(presenter.current_morph)
        self.assertEqual(adapter.calls, [])

    def test_load_morphs_routes_through_adapter_and_populates_view(self):
        adapter = _FakeMayaAdapter()
        adapter.existing.add(TEST_MODEL)
        adapter.attr_exists.update({(TEST_MODEL, "mmdMorphData"), (TEST_MODEL, "mmdMorphPresets")})
        adapter.attr_values[f"{TEST_MODEL}.mmdMorphData"] = json.dumps(
            {
                "smile": {"name_jp": "笑顔", "name_en": "smile", "panel": 1, "type": 0, "group": "目"},
            },
            ensure_ascii=False,
        )
        adapter.attr_values[f"{TEST_MODEL}.mmdMorphPresets"] = json.dumps({"custom_pose": {"smile": 0.7}})
        mesh_kwargs = tuple(sorted({"allDescendents": True, "type": "mesh"}.items()))
        adapter.relatives[(TEST_MODEL, mesh_kwargs)] = ["faceShape"]
        adapter.history["faceShape"] = ["skinCluster1", "faceBlendShape"]
        adapter.ls_results[((("skinCluster1", "faceBlendShape"),), (("type", "blendShape"),))] = ["faceBlendShape"]
        adapter.aliases["faceBlendShape"] = ["smile", "weight[0]", "blink", "weight[1]"]
        presenter, view, _, _ = _make_presenter(model=TEST_MODEL, adapter=adapter)

        presenter.load_morphs()

        self.assertIn(("object_exists", TEST_MODEL), adapter.calls)
        self.assertIn(("attribute_exists", "mmdMorphData", TEST_MODEL), adapter.calls)
        self.assertIn(("get_attr", f"{TEST_MODEL}.mmdMorphData"), adapter.calls)
        self.assertIn(
            ("list_relatives", TEST_MODEL, (), {"allDescendents": True, "type": "mesh"}),
            adapter.calls,
        )
        self.assertIn(("list_history", "faceShape"), adapter.calls)
        self.assertIn(("ls", (["skinCluster1", "faceBlendShape"],), {"type": "blendShape"}), adapter.calls)
        self.assertIn(("alias_attr", "faceBlendShape", {"query": True}), adapter.calls)
        self.assertEqual(presenter.blend_shape_node, "faceBlendShape")
        self.assertEqual(presenter.morph_data["smile"]["blend_shape_node"], "faceBlendShape")
        self.assertNotIn("blink", presenter.morph_data)
        self.assertEqual([item.text() for item in view.morph_list.items], ["smile"])
        self.assertEqual(view.preset_combo.items, ["なし", "笑顔", "ウィンク", "驚き", "悲しみ", "custom_pose"])

    def test_load_morphs_falls_back_to_blendshape_raw_names_and_split_targets(self):
        adapter = _FakeMayaAdapter()
        adapter.existing.update(
            {
                TEST_MODEL,
                "faceBlendShapeA",
                "faceBlendShapeA.weight[0]",
                "faceBlendShapeB",
                "faceBlendShapeB.weight[0]",
            }
        )
        mesh_kwargs = tuple(sorted({"allDescendents": True, "type": "mesh"}.items()))
        adapter.relatives[(TEST_MODEL, mesh_kwargs)] = ["faceShapeA", "faceShapeB"]
        adapter.history["faceShapeA"] = ["faceBlendShapeA"]
        adapter.history["faceShapeB"] = ["faceBlendShapeB"]
        adapter.ls_results[((("faceBlendShapeA",),), (("type", "blendShape"),))] = ["faceBlendShapeA"]
        adapter.ls_results[((("faceBlendShapeB",),), (("type", "blendShape"),))] = ["faceBlendShapeB"]
        adapter.attr_exists.update(
            {
                ("faceBlendShapeA", "mmd_blendshape_morph_names_json"),
                ("faceBlendShapeB", "mmd_blendshape_morph_names_json"),
            }
        )
        adapter.attr_values.update(
            {
                "faceBlendShapeA.mmd_blendshape_morph_names_json": json.dumps({"0": "笑顔"}, ensure_ascii=False),
                "faceBlendShapeB.mmd_blendshape_morph_names_json": json.dumps({"0": "笑顔"}, ensure_ascii=False),
                "faceBlendShapeA.weight[0]": 0.4,
                "faceBlendShapeB.weight[0]": 0.4,
            }
        )
        adapter.aliases["faceBlendShapeA"] = ["smile_alias", "weight[0]"]
        adapter.aliases["faceBlendShapeB"] = ["smile_alias_split", "weight[0]"]
        presenter, view, _, _ = _make_presenter(model=TEST_MODEL, adapter=adapter)

        presenter.load_morphs()
        presenter.current_morph = "笑顔"
        presenter.on_morph_slider_changed(65)

        self.assertEqual([item.text() for item in view.morph_list.items], ["笑顔"])
        self.assertEqual(presenter.morph_data["笑顔"]["name_jp"], "笑顔")
        self.assertEqual(
            presenter.morph_data["笑顔"]["blend_shape_targets"],
            [
                {"node": "faceBlendShapeA", "target": "smile_alias", "weight_attr": "weight[0]"},
                {"node": "faceBlendShapeB", "target": "smile_alias_split", "weight_attr": "weight[0]"},
            ],
        )
        self.assertIn(("set_attr", "faceBlendShapeA.weight[0]", 0.65), adapter.calls)
        self.assertIn(("set_attr", "faceBlendShapeB.weight[0]", 0.65), adapter.calls)

    def test_stale_blendshape_alias_is_skipped_with_actionable_warning(self):
        adapter = _FakeMayaAdapter()
        adapter.existing.add("faceBlendShape")
        presenter, _, _, _ = _make_presenter(adapter=adapter)
        data = {
            "blend_shape_node": "faceBlendShape",
            "blend_shape_target": "Mouth_A01",
        }

        with self.assertLogs(morph_presenter_module.logger.name, level="WARNING") as logs:
            presenter._set_blend_shape_weight(data, 0.5, "あ")

        message = "\n".join(logs.output)
        self.assertIn("morph=あ", message)
        self.assertIn("node=faceBlendShape", message)
        self.assertIn("plug=Mouth_A01", message)
        self.assertNotIn(("set_attr", "faceBlendShape.Mouth_A01", 0.5), adapter.calls)

    def test_reassigned_alias_uses_current_weight_index_not_stored_index(self):
        adapter = _FakeMayaAdapter()
        adapter.existing.update({"faceBlendShape", "faceBlendShape.weight[2]"})
        adapter.aliases["faceBlendShape"] = ["Mouth_A01", "weight[2]"]
        presenter, _, _, _ = _make_presenter(adapter=adapter)
        data = {
            "blend_shape_node": "faceBlendShape",
            "blend_shape_target": "Mouth_A01",
            "blend_shape_weight_attr": "weight[0]",
        }

        presenter._set_blend_shape_weight(data, 0.75, "あ")

        self.assertIn(("set_attr", "faceBlendShape.weight[2]", 0.75), adapter.calls)
        self.assertNotIn(("set_attr", "faceBlendShape.weight[0]", 0.75), adapter.calls)

    def test_load_morphs_falls_back_to_network_morph_nodes_for_display(self):
        adapter = _FakeMayaAdapter()
        adapter.existing.update({TEST_MODEL, "boneSmileNode", "materialFlashNode"})
        mesh_kwargs = tuple(sorted({"allDescendents": True, "type": "mesh"}.items()))
        adapter.relatives[(TEST_MODEL, mesh_kwargs)] = []
        adapter.ls_results[((), (("type", "network"),))] = ["boneSmileNode", "materialFlashNode", "plainNetwork"]
        adapter.attr_exists.update(
            {
                ("boneSmileNode", "mmd_morph_type"),
                ("boneSmileNode", "mmd_morph_name"),
                ("boneSmileNode", "mmd_morph_name_en"),
                ("materialFlashNode", "mmd_morph_type"),
                ("materialFlashNode", "mmd_morph_name"),
                ("plainNetwork", "mmd_morph_type"),
            }
        )
        adapter.attr_values.update(
            {
                "boneSmileNode.mmd_morph_type": "bone",
                "boneSmileNode.mmd_morph_name": "ボーン笑い",
                "boneSmileNode.mmd_morph_name_en": "bone_smile",
                "boneSmileNode.weight": 0.25,
                "materialFlashNode.mmd_morph_type": "material",
                "materialFlashNode.mmd_morph_name": "材質点滅",
                "materialFlashNode.weight": 0.0,
                "plainNetwork.mmd_morph_type": "other",
            }
        )
        presenter, view, _, _ = _make_presenter(model=TEST_MODEL, adapter=adapter)

        presenter.load_morphs()
        presenter.on_morph_selected(_FakeItem("ボーン笑い"), None)

        self.assertEqual([item.text() for item in view.morph_list.items], ["ボーン笑い", "材質点滅"])
        self.assertEqual(presenter.morph_data["ボーン笑い"]["type"], 10)
        self.assertEqual(presenter.morph_data["ボーン笑い"]["name_en"], "bone_smile")
        self.assertEqual(presenter.morph_data["材質点滅"]["type"], 11)
        self.assertEqual(view.blend_shape_edit._text, "boneSmileNode")
        self.assertEqual(view.target_name_edit._text, "weight")
        self.assertEqual(view.connection_status_label.text, "Metadata only")
        self.assertEqual(view.morph_slider.set_value_calls, [25])

    def test_organize_and_filter_morphs_by_group_are_pure_logic(self):
        presenter, view, _, _ = _make_presenter()
        presenter.morph_data = {
            "eyebrow_up": {"group": "眉"},
            "eye_close": {"group": "目"},
            "mouth_open": {"group": "口"},
            "custom": {"group": "カスタム"},
            "fallback": {},
        }

        presenter._organize_morphs_by_group()
        presenter.filter_morphs_by_group("その他")

        self.assertEqual(presenter.group_morphs["眉"], ["eyebrow_up"])
        self.assertEqual(presenter.group_morphs["目"], ["eye_close"])
        self.assertEqual(presenter.group_morphs["口"], ["mouth_open"])
        self.assertEqual(presenter.group_morphs["その他"], ["fallback"])
        self.assertEqual(presenter.group_morphs["カスタム"], ["custom"])
        self.assertEqual([item.text() for item in view.morph_list.items], ["fallback"])

        view.morph_list.items = [_FakeItem("smile"), _FakeItem("wink"), _FakeItem("sad")]
        presenter.filter_morphs("s")

        self.assertEqual([item.hidden for item in view.morph_list.items], [False, True, False])

    def test_on_morph_selected_loads_details_via_adapter_and_updates_view(self):
        adapter = _FakeMayaAdapter()
        adapter.existing.update({"faceBlendShape", "faceBlendShape.weight[0]"})
        adapter.attr_values["faceBlendShape.weight[0]"] = 0.42
        adapter.aliases["faceBlendShape"] = ["smile", "weight[0]"]
        presenter, view, _, _ = _make_presenter(adapter=adapter)
        presenter.morph_data = {
            "smile": {
                "name_jp": "笑顔",
                "name_en": "smile",
                "panel": 1,
                "type": 0,
                "group": "目",
                "blend_shape_node": "faceBlendShape",
                "blend_shape_target": "smile",
                "blend_shape_weight_attr": "weight[0]",
            }
        }

        presenter.on_morph_selected(_FakeItem("smile"), None)

        self.assertEqual(presenter.current_morph, "smile")
        self.assertEqual(view.details_enabled_calls, [True])
        self.assertEqual(view.morph_name_jp_edit._text, "笑顔")
        self.assertEqual(view.morph_name_en_edit._text, "smile")
        self.assertEqual(view.panel_combo.set_index_calls, [1])
        self.assertEqual(view.group_combo.set_text_calls, ["目"])
        self.assertEqual(view.blend_shape_edit._text, "faceBlendShape")
        self.assertEqual(view.target_name_edit._text, "smile")
        self.assertEqual(view.connection_status_label.text, "Connected")
        self.assertEqual(view.morph_slider.set_value_calls, [42])
        self.assertEqual(view.morph_value_label.text, "42%")
        self.assertEqual(view.offset_table.row_count_calls, [0])
        self.assertEqual(view.offset_count_label.text, "labels:offset_not_supported")
        self.assertIn(("object_exists", "faceBlendShape"), adapter.calls)
        self.assertIn(("get_attr", "faceBlendShape.weight[0]"), adapter.calls)

    def test_reset_all_morphs_only_sets_nonzero_existing_targets(self):
        adapter = _FakeMayaAdapter()
        adapter.existing.update(
            {"faceBlendShape", "faceBlendShape.weight[0]", "faceBlendShape.weight[1]"}
        )
        adapter.attr_values.update(
            {"faceBlendShape.weight[0]": 0.8, "faceBlendShape.weight[1]": 0.0}
        )
        adapter.aliases["faceBlendShape"] = ["smile", "weight[0]", "blink", "weight[1]"]
        presenter, view, app_state, _ = _make_presenter(adapter=adapter)
        presenter.morph_data = {
            "smile": {
                "blend_shape_node": "faceBlendShape",
                "blend_shape_target": "smile",
                "blend_shape_weight_attr": "weight[0]",
            },
            "blink": {
                "blend_shape_node": "faceBlendShape",
                "blend_shape_target": "blink",
                "blend_shape_weight_attr": "weight[1]",
            },
            "missing": {"blend_shape_node": "missingBlendShape", "blend_shape_target": "missing"},
            "unconnected": {},
        }

        presenter.reset_all_morphs()

        self.assertIn(("get_attr", "faceBlendShape.weight[0]"), adapter.calls)
        self.assertIn(("get_attr", "faceBlendShape.weight[1]"), adapter.calls)
        self.assertIn(("object_exists", "missingBlendShape"), adapter.calls)
        self.assertIn(("set_attr", "faceBlendShape.weight[0]", 0), adapter.calls)
        self.assertNotIn(("set_attr", "faceBlendShape.weight[1]", 0), adapter.calls)
        self.assertEqual(view.morph_slider.set_value_calls, [0])
        self.assertEqual(app_state.statuses, [("Reset 1 morph(s)", None)])

    def test_save_preset_serializes_nonzero_values_and_preserves_existing_presets(self):
        adapter = _FakeMayaAdapter()
        adapter.existing.update(
            {TEST_MODEL, "faceBlendShape", "faceBlendShape.weight[0]", "faceBlendShape.weight[1]"}
        )
        adapter.attr_exists.add((TEST_MODEL, "mmdMorphPresets"))
        adapter.attr_values.update(
            {
                "faceBlendShape.weight[0]": 0.8,
                "faceBlendShape.weight[1]": 0.0,
                f"{TEST_MODEL}.mmdMorphPresets": json.dumps({"old_pose": {"smile": 0.2}}),
            }
        )
        adapter.aliases["faceBlendShape"] = ["smile", "weight[0]", "blink", "weight[1]"]
        presenter, view, app_state, _ = _make_presenter(model=TEST_MODEL, adapter=adapter)
        view.preset_combo.setCurrentText("custom_pose")
        presenter.morph_data = {
            "smile": {
                "blend_shape_node": "faceBlendShape",
                "blend_shape_target": "smile",
                "blend_shape_weight_attr": "weight[0]",
            },
            "blink": {
                "blend_shape_node": "faceBlendShape",
                "blend_shape_target": "blink",
                "blend_shape_weight_attr": "weight[1]",
            },
        }

        with patch.object(morph_presenter_module, "set_attribute") as set_attribute, patch.object(
            morph_presenter_module, "set_custom_attributes"
        ) as set_custom_attributes:
            presenter.save_preset()

        set_custom_attributes.assert_not_called()
        set_attribute_args = set_attribute.call_args[0]
        self.assertEqual(set_attribute_args[0], TEST_MODEL)
        self.assertEqual(set_attribute_args[1], "mmdMorphPresets")
        self.assertEqual(set_attribute_args[3], "string")
        saved_presets = json.loads(set_attribute_args[2])
        self.assertEqual(saved_presets, {"old_pose": {"smile": 0.2}, "custom_pose": {"smile": 0.8}})
        self.assertIn(("get_attr", "faceBlendShape.weight[0]"), adapter.calls)
        self.assertIn(("get_attr", "faceBlendShape.weight[1]"), adapter.calls)
        self.assertIn(("get_attr", f"{TEST_MODEL}.mmdMorphPresets"), adapter.calls)
        self.assertIn("custom_pose", view.preset_combo.items)
        self.assertEqual(app_state.statuses, [("Saved preset 'custom_pose'", None)])

    def test_load_preset_parses_json_and_applies_existing_connected_morphs(self):
        adapter = _FakeMayaAdapter()
        adapter.existing.update({TEST_MODEL, "faceBlendShape", "faceBlendShape.weight[0]"})
        adapter.attr_exists.add((TEST_MODEL, "mmdMorphPresets"))
        adapter.attr_values[f"{TEST_MODEL}.mmdMorphPresets"] = json.dumps(
            {"custom_pose": {"smile": 0.75, "blink": 0.5, "unknown": 1.0}}
        )
        adapter.aliases["faceBlendShape"] = ["smile", "weight[0]"]
        presenter, view, app_state, _ = _make_presenter(model=TEST_MODEL, adapter=adapter)
        view.preset_combo.setCurrentText("custom_pose")
        presenter.current_morph = "smile"
        presenter.morph_data = {
            "smile": {
                "blend_shape_node": "faceBlendShape",
                "blend_shape_target": "smile",
                "blend_shape_weight_attr": "weight[0]",
            },
            "blink": {"blend_shape_node": "missingBlendShape", "blend_shape_target": "blink"},
        }

        presenter.load_preset()

        self.assertIn(("object_exists", TEST_MODEL), adapter.calls)
        self.assertIn(("attribute_exists", "mmdMorphPresets", TEST_MODEL), adapter.calls)
        self.assertIn(("get_attr", f"{TEST_MODEL}.mmdMorphPresets"), adapter.calls)
        self.assertIn(("set_attr", "faceBlendShape.weight[0]", 0.75), adapter.calls)
        self.assertNotIn(("set_attr", "missingBlendShape.blink", 0.5), adapter.calls)
        self.assertEqual(view.morph_slider.set_value_calls, [75])
        self.assertEqual(app_state.statuses, [("Applied preset 'custom_pose'", None)])

    def test_apply_changes_serializes_morph_data_without_real_scene(self):
        adapter = _FakeMayaAdapter()
        adapter.existing.add(TEST_MODEL)
        presenter, view, app_state, _ = _make_presenter(model=TEST_MODEL, adapter=adapter)
        presenter.current_morph = "smile"
        presenter.morph_data = {
            "smile": {"name_jp": "旧", "name_en": "old", "panel": 0, "type": 0, "group": "その他"},
        }
        view.morph_name_jp_edit.setText("新")
        view.morph_name_en_edit.setText("new")
        view.panel_combo.setCurrentIndex(2)
        view.morph_type_combo.setCurrentIndex(1)
        view.group_combo.setCurrentText("目")

        with patch.object(morph_presenter_module, "set_custom_attributes") as set_custom_attributes:
            presenter.apply_changes()

        saved_json = set_custom_attributes.call_args[0][1]["mmdMorphData"]
        self.assertEqual(
            json.loads(saved_json)["smile"],
            {"name_jp": "新", "name_en": "new", "panel": 2, "type": 1, "group": "目"},
        )
        self.assertEqual(presenter.group_morphs["目"], ["smile"])
        self.assertIn(("object_exists", TEST_MODEL), adapter.calls)
        self.assertEqual(app_state.statuses, [("Applied morph changes: smile", None)])


if __name__ == "__main__":
    unittest.main()
