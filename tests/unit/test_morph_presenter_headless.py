"""MorphPresenterのMaya非依存ロジックとadapter-routingを検証するテスト。"""

import json
import unittest
from unittest.mock import MagicMock, patch

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
    def __init__(self, text, key=None):
        self._text = text
        self._data = {0x0100: key}
        self.hidden = False

    def text(self):
        return self._text

    def setHidden(self, hidden):
        self.hidden = hidden

    def data(self, role):
        return self._data.get(role)


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
        self.enabled = True
        self.tooltip = ""

    def setEnabled(self, enabled):
        self.enabled = enabled

    def setToolTip(self, tooltip):
        self.tooltip = tooltip


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
        self.item_data = []
        if current_text:
            self.items.append(current_text)

    def clear(self):
        self.items.clear()
        self.item_data.clear()
        self._current_text = ""
        self._current_index = 0

    def addItems(self, items):
        self.items.extend(items)
        self.item_data.extend([None] * len(items))
        if not self._current_text and items:
            self._current_text = items[0]

    def addItem(self, item, data=None):
        self.items.append(item)
        self.item_data.append(data)

    def removeItem(self, index):
        del self.items[index]
        del self.item_data[index]

    def itemData(self, index):
        return self.item_data[index]

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
        self.enabled = True
        self.tooltip = ""

    def setValue(self, value):
        self.value = value
        self.set_value_calls.append(value)

    def setEnabled(self, enabled):
        self.enabled = enabled

    def setToolTip(self, tooltip):
        self.tooltip = tooltip


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

        self.refresh_morphs_btn = _FakeButton()
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
        self.preset_combo = _FakeComboBox("なし")

        self.morph_slider = _FakeSlider()
        self.morph_value_label = _FakeLabel()
        self.connection_status_label = _FakeLabel()
        self.offset_count_label = _FakeLabel()

        self.invert_check = _FakeCheckBox()
        self.multiplier_spin = _FakeSpinBox()
        self.offset_table = _FakeTable()

        self.details_enabled_calls = []
        self.controls_enabled_calls = []
        self.tr_calls = []

    def set_morph_details_enabled(self, enabled):
        self.details_enabled_calls.append(enabled)

    def set_morph_controls_enabled(self, enabled, tooltip=""):
        self.controls_enabled_calls.append((enabled, tooltip))
        for widget in (self.morph_slider, self.reset_slider_btn):
            widget.setEnabled(enabled)
            widget.setToolTip(tooltip)

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
        self.connections = {}

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

    def list_connections(self, node, **kwargs):
        self.calls.append(("list_connections", node, kwargs))
        result = []
        source = kwargs.get("source", True)
        destination = kwargs.get("destination", True)
        requested_type = kwargs.get("type")
        for connection in self.connections.get(node, []):
            if destination and not source and connection["source"] == node:
                candidate = connection["destination"]
            elif source and not destination and connection["destination"] == node:
                candidate = connection["source"]
            else:
                continue
            candidate_node = candidate.split(".", 1)[0]
            if requested_type and self.node_types.get(candidate_node) != requested_type:
                continue
            result.append(candidate if kwargs.get("plugs") else candidate_node)
        return result


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

    def test_tab_activation_loads_new_model_once_and_preserves_current_state(self):
        presenter, view, app_state, adapter = _make_presenter()
        app_state.current_model_root = TEST_MODEL
        adapter.existing.add(TEST_MODEL)

        presenter.ensure_morphs_loaded()
        self.assertEqual(presenter._loaded_model_root, TEST_MODEL)

        view.search_edit._text = "keep"
        presenter.current_morph = "selected"
        presenter.ensure_morphs_loaded()

        self.assertEqual(view.search_edit._text, "keep")
        self.assertEqual(presenter.current_morph, "selected")

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
        self.assertEqual([item.text() for item in view.morph_list.items], ["000:V|笑顔"])
        self.assertEqual([item.data(256) for item in view.morph_list.items], ["smile"])
        self.assertEqual(view.preset_combo.items, ["なし", "笑顔", "ウィンク", "驚き", "悲しみ", "custom_pose"])

    def test_list_metadata_preserves_duplicate_and_empty_names(self):
        presenter, _, _, _ = _make_presenter()

        indexed = presenter._index_morph_metadata(
            [
                {"name_jp": "笑顔", "panel": 2, "type": 1, "index": 4},
                {"name_jp": "笑顔", "panel": 3, "type": 1, "index": 7},
                {"name_jp": "", "panel": 4, "type": 1, "index": 9},
            ]
        )

        self.assertEqual(list(indexed), ["笑顔", "笑顔 [7]", "<unnamed> [9]"])
        self.assertEqual([data["index"] for data in indexed.values()], [4, 7, 9])

    def test_display_uses_global_index_type_letter_and_stable_user_role_key(self):
        presenter, view, _, _ = _make_presenter()
        presenter.morph_data = {
            "duplicate [unknown]": {"name_jp": "同名", "type": 8, "index": -1, "_pmx_type_raw": True},
            "duplicate [7]": {"name_jp": "同名", "type": 2, "index": 7, "_pmx_type_raw": True},
            "duplicate [4]": {"name_jp": "同名", "type": 1, "index": 4, "_pmx_type_raw": True},
        }

        presenter._display_all_morphs()

        self.assertEqual(
            [item.text() for item in view.morph_list.items],
            ["004:V|同名", "007:B|同名", "---:M|同名"],
        )
        self.assertEqual(
            [item.data(256) for item in view.morph_list.items],
            ["duplicate [4]", "duplicate [7]", "duplicate [unknown]"],
        )

    def test_duplicate_blendshape_names_bind_by_weight_index_deterministically(self):
        presenter, _, _, _ = _make_presenter()
        presenter.morph_data = presenter._index_morph_metadata(
            [
                {"name_jp": "笑顔", "panel": 2, "type": 1, "index": 4},
                {"name_jp": "笑顔", "panel": 3, "type": 1, "index": 7},
            ]
        )

        self.assertEqual(presenter._resolve_blendshape_metadata_key("笑顔", weight_index=0), "笑顔")
        self.assertEqual(presenter._resolve_blendshape_metadata_key("笑顔", weight_index=1), "笑顔 [7]")
        # A split mesh repeats the local weight index and resolves to the same record.
        self.assertEqual(presenter._resolve_blendshape_metadata_key("笑顔", weight_index=0), "笑顔")

        presenter.morph_data["<unnamed> [9]"] = {
            "name_jp": "",
            "panel": 4,
            "type": 1,
            "index": 9,
        }
        self.assertEqual(
            presenter._resolve_blendshape_metadata_key("", weight_index=2),
            "<unnamed> [9]",
        )

    def test_global_morph_index_wins_across_split_mesh_local_order(self):
        presenter, _, _, _ = _make_presenter()
        presenter.morph_data = presenter._index_morph_metadata(
            [
                {"name_jp": "笑顔", "panel": 2, "type": 1, "index": 4},
                {"name_jp": "笑顔", "panel": 3, "type": 1, "index": 7},
            ]
        )

        self.assertEqual(
            presenter._resolve_blendshape_metadata_key("笑顔", global_index=7, weight_index=0),
            "笑顔 [7]",
        )
        self.assertEqual(
            presenter._resolve_blendshape_metadata_key("笑顔", global_index=4, weight_index=1),
            "笑顔",
        )
        # Another split mesh may use a different local slot for the same PMX morph.
        self.assertEqual(
            presenter._resolve_blendshape_metadata_key("笑顔", global_index=7, weight_index=5),
            "笑顔 [7]",
        )

    def test_raw_pmx_type_ui_mapping_round_trips_without_mutating_storage(self):
        for raw_type, ui_index in morph_presenter_module._PMX_TYPE_TO_UI_INDEX.items():
            self.assertEqual(morph_presenter_module._UI_INDEX_TO_PMX_TYPE[ui_index], raw_type)

        presenter, view, _, _ = _make_presenter()
        presenter.morph_data = presenter._index_morph_metadata(
            [{"name_jp": "material", "panel": 4, "type": 8, "index": 3}]
        )
        presenter.load_morph_details("material")
        self.assertEqual(view.morph_type_combo.currentIndex(), 11)
        self.assertEqual(presenter.morph_data["material"]["type"], 8)

        presenter.current_morph = "material"
        view.morph_type_combo.setCurrentIndex(10)
        presenter.apply_changes()
        self.assertEqual(presenter.morph_data["material"]["type"], 2)

    def test_runtime_capability_disables_unsupported_controls_without_hiding_details(self):
        presenter, view, _, adapter = _make_presenter()
        adapter.existing.update({"vertex_bs", "bone_node", "material_node", "group_node"})
        presenter.morph_data = presenter._index_morph_metadata(
            [
                {"name_jp": "vertex", "panel": 4, "type": 1, "index": 0},
                {"name_jp": "bone", "panel": 4, "type": 2, "index": 1},
                {"name_jp": "material", "panel": 4, "type": 8, "index": 2},
                {"name_jp": "uv", "panel": 4, "type": 3, "index": 3},
                {"name_jp": "flip", "panel": 4, "type": 9, "index": 4},
                {"name_jp": "impulse", "panel": 4, "type": 10, "index": 5},
            ]
        )
        presenter.morph_data["vertex"].update(blend_shape_node="vertex_bs", blend_shape_target="v")
        presenter.morph_data["bone"].update(morph_node="bone_node")
        presenter.morph_data["material"].update(morph_node="material_node")
        adapter.node_types["materialEval"] = "mmdMaterialMorphEval"
        adapter.node_types["materialShader"] = "dx11Shader"
        adapter.attr_exists.add(("materialEval", "mmd_complete_route_ready"))
        adapter.attr_values["materialEval.mmd_complete_route_ready"] = True
        adapter.attr_values["materialEval.mmd_target_shader"] = "materialShader"
        adapter.node_types.update({"groupSum": "plusMinusAverage", "groupScale": "multiplyDivide"})
        adapter.connections["material_node.weight"] = [{
            "source": "material_node.weight",
            "destination": "groupSum.input1D[0]",
        }]
        adapter.connections["groupSum.output1D"] = [{
            "source": "groupSum.output1D",
            "destination": "groupScale.input1X",
        }]
        adapter.connections["groupScale.outputX"] = [{
            "source": "groupScale.outputX",
            "destination": "materialEval.contribution[0].weight",
        }]
        adapter.connections["materialEval.outputDiffuse"] = [{
            "source": "materialEval.outputDiffuse",
            "destination": "materialShader.DiffuseColorRGB",
        }]

        for name in ("vertex", "bone", "material"):
            presenter.on_morph_selected(_FakeItem(name, name), None)
            self.assertEqual(view.controls_enabled_calls[-1], (True, ""))

        for name in ("uv", "flip", "impulse"):
            presenter.on_morph_selected(_FakeItem(name, name), None)
            self.assertEqual(
                view.controls_enabled_calls[-1],
                (False, "tooltips:morph_runtime_unsupported"),
            )
            self.assertTrue(view.details_enabled_calls[-1])
            self.assertEqual(presenter.current_morph, name)

    def test_material_capability_requires_destination_evaluator_plug(self):
        presenter, _, _, adapter = _make_presenter()
        adapter.existing.add("material_node")
        data = {"type": 8, "_pmx_type_raw": True, "morph_node": "material_node"}

        adapter.node_types["wrong"] = "multiplyDivide"
        adapter.connections["material_node.weight"] = [{
            "source": "material_node.weight",
            "destination": "wrong.input1X",
        }]
        self.assertFalse(presenter._morph_controls_supported(data))

        adapter.node_types["materialEval"] = "mmdMaterialMorphEval"
        adapter.connections["material_node.weight"] = [{
            "source": "materialEval.outputDiffuseR",
            "destination": "material_node.weight",
        }]
        self.assertFalse(presenter._morph_controls_supported(data))

        adapter.connections["material_node.weight"] = [{
            "source": "material_node.weight",
            "destination": "materialEval.contribution[0].weight",
        }]
        self.assertFalse(presenter._morph_controls_supported(data))

        adapter.attr_exists.add(("materialEval", "mmd_complete_route_ready"))
        adapter.attr_values["materialEval.mmd_complete_route_ready"] = True
        adapter.attr_values["materialEval.mmd_target_shader"] = "materialShader"
        adapter.node_types["materialShader"] = "GLSLShader"
        adapter.connections["materialEval.outputDiffuse"] = [{
            "source": "materialEval.outputDiffuse",
            "destination": "materialShader.DiffuseColorRGB",
        }]
        self.assertTrue(presenter._morph_controls_supported(data))

    def test_cached_material_capability_does_not_repeat_graph_traversal(self):
        presenter, _, _, adapter = _make_presenter()
        adapter.existing.add("material_node")
        adapter.node_types.update({
            "materialEval": "mmdMaterialMorphEval",
            "materialShader": "GLSLShader",
        })
        adapter.attr_exists.add(("materialEval", "mmd_complete_route_ready"))
        adapter.attr_values.update({
            "materialEval.mmd_complete_route_ready": True,
            "materialEval.mmd_target_shader": "materialShader",
        })
        adapter.connections.update({
            "material_node.weight": [{
                "source": "material_node.weight",
                "destination": "materialEval.contribution[0].weight",
            }],
            "materialEval.outputDiffuse": [{
                "source": "materialEval.outputDiffuse",
                "destination": "materialShader.DiffuseColorRGB",
            }],
        })
        data = {
            "type": 8,
            "_pmx_type_raw": True,
            "index": 2,
            "morph_node": "material_node",
        }
        presenter.morph_data = {"material": data}
        adapter.list_connections = MagicMock(wraps=adapter.list_connections)

        presenter._cache_morph_capabilities()
        calls_after_load = adapter.list_connections.call_count
        self.assertTrue(presenter._morph_controls_supported(data))
        self.assertTrue(presenter._morph_controls_supported(data))

        self.assertGreater(calls_after_load, 0)
        self.assertEqual(adapter.list_connections.call_count, calls_after_load)

        # The remaining assertions exercise the uncached evaluator itself.
        presenter._morph_capability_cache.clear()

        adapter.node_types["unrelated"] = "condition"
        adapter.connections["material_node.weight"] = [{
            "source": "material_node.weight",
            "destination": "unrelated.firstTerm",
        }]
        self.assertFalse(presenter._morph_controls_supported(data))

        adapter.node_types["cycleA"] = "unitConversion"
        adapter.node_types["cycleB"] = "unitConversion"
        adapter.connections["material_node.weight"] = [{
            "source": "material_node.weight",
            "destination": "cycleA.input",
        }]
        adapter.connections["cycleA.output"] = [{
            "source": "cycleA.output", "destination": "cycleB.input",
        }]
        adapter.connections["cycleB.output"] = [{
            "source": "cycleB.output", "destination": "cycleA.input",
        }]
        self.assertFalse(presenter._morph_controls_supported(data))

        adapter.connections["material_node.weight"] = [{
            "source": "material_node.weight",
            "destination": "materialEval.baseDiffuseR",
        }]
        self.assertFalse(presenter._morph_controls_supported(data))

        adapter.connections["material_node.weight"] = [{
            "source": "material_node.weight",
            "destination": "ns:materialEval.contribution[12].weight",
        }]
        adapter.node_types["ns:materialEval"] = "mmdMaterialMorphEval"
        adapter.attr_exists.add(("ns:materialEval", "mmd_complete_route_ready"))
        adapter.attr_values["ns:materialEval.mmd_complete_route_ready"] = True
        adapter.attr_values["ns:materialEval.mmd_target_shader"] = "materialShader"
        adapter.connections["ns:materialEval.outputDiffuse"] = [{
            "source": "ns:materialEval.outputDiffuse",
            "destination": "materialShader.DiffuseColorRGB",
        }]
        self.assertTrue(presenter._morph_controls_supported(data))

    def test_group_capability_requires_referenced_bone_morph(self):
        presenter, view, _, adapter = _make_presenter()
        adapter.existing.update({"bone_node", "material_node", "group_node"})
        presenter.morph_data = presenter._index_morph_metadata(
            [
                {"name_jp": "vertex", "panel": 4, "type": 1, "index": 0},
                {"name_jp": "bone", "panel": 4, "type": 2, "index": 1},
                {"name_jp": "material", "panel": 4, "type": 8, "index": 2},
                {"name_jp": "group", "panel": 4, "type": 0, "index": 3},
            ]
        )
        group = presenter.morph_data["group"]
        group["morph_node"] = "group_node"
        presenter.morph_data["bone"]["morph_node"] = "bone_node"
        presenter.morph_data["material"]["morph_node"] = "material_node"

        group["group_morph_offsets"] = [{"morph_index": 0}, {"morph_index": 2}, {"morph_index": 99}]
        presenter.on_morph_selected(_FakeItem("group", "group"), None)
        self.assertEqual(view.controls_enabled_calls[-1][0], False)

        adapter.node_types.update({
            "materialEval": "mmdMaterialMorphEval",
            "materialShader": "dx11Shader",
            "materialGroupSum": "plusMinusAverage",
            "materialGroupScale": "multiplyDivide",
        })
        adapter.attr_exists.add(("materialEval", "mmd_complete_route_ready"))
        adapter.attr_values.update({
            "materialEval.mmd_complete_route_ready": True,
            "materialEval.mmd_target_shader": "materialShader",
        })
        adapter.connections["material_node.weight"] = [{
            "source": "material_node.weight",
            "destination": "materialGroupSum.input1D[0]",
        }]
        adapter.connections["materialGroupSum.output1D"] = [{
            "source": "materialGroupSum.output1D",
            "destination": "materialGroupScale.input1X",
        }]
        adapter.connections["materialGroupScale.outputX"] = [{
            "source": "materialGroupScale.outputX",
            "destination": "materialEval.contribution[0].weight",
        }]
        adapter.connections["materialEval.outputDiffuse"] = [{
            "source": "materialEval.outputDiffuse",
            "destination": "materialShader.DiffuseColorRGB",
        }]
        group["group_morph_offsets"] = [{"morph_index": 2, "morph_rate": 0.5}]
        presenter.on_morph_selected(_FakeItem("group", "group"), None)
        self.assertEqual(view.controls_enabled_calls[-1], (True, ""))

        adapter.attr_values["materialEval.mmd_complete_route_ready"] = False
        presenter.on_morph_selected(_FakeItem("group", "group"), None)
        self.assertEqual(view.controls_enabled_calls[-1][0], False)

        group["group_morph_offsets"].append({"morph_index": 1, "morph_rate": 0.5})
        presenter.on_morph_selected(_FakeItem("group", "group"), None)
        self.assertEqual(view.controls_enabled_calls[-1], (True, ""))

    def test_group_offsets_are_read_from_network_metadata(self):
        presenter, _, _, adapter = _make_presenter()
        adapter.attr_exists.add(("group_node", "mmd_group_morph_offsets_json"))
        adapter.attr_values["group_node.mmd_group_morph_offsets_json"] = json.dumps(
            [{"morph_index": 7, "morph_rate": 0.25}]
        )

        self.assertEqual(
            presenter._read_group_morph_offsets("group_node"),
            [{"morph_index": 7, "morph_rate": 0.25}],
        )
        adapter.attr_values["group_node.mmd_group_morph_offsets_json"] = "not-json"
        self.assertEqual(presenter._read_group_morph_offsets("group_node"), [])

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

        self.assertEqual([item.text() for item in view.morph_list.items], ["000:V|笑顔"])
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
        adapter.existing.update({TEST_MODEL, "boneSmileNode", "materialFlashNode", "groupPoseNode"})
        mesh_kwargs = tuple(sorted({"allDescendents": True, "type": "mesh"}.items()))
        adapter.relatives[(TEST_MODEL, mesh_kwargs)] = []
        adapter.ls_results[((), (("type", "network"),))] = [
            "boneSmileNode",
            "materialFlashNode",
            "groupPoseNode",
            "plainNetwork",
        ]
        adapter.attr_exists.update(
            {
                ("boneSmileNode", "mmd_morph_type"),
                ("boneSmileNode", "mmd_morph_name"),
                ("boneSmileNode", "mmd_morph_name_en"),
                ("materialFlashNode", "mmd_morph_type"),
                ("materialFlashNode", "mmd_morph_name"),
                ("groupPoseNode", "mmd_morph_type"),
                ("groupPoseNode", "mmd_morph_name"),
                ("groupPoseNode", "mmd_morph_panel"),
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
                "groupPoseNode.mmd_morph_type": "group",
                "groupPoseNode.mmd_morph_name": "グループ表情",
                "groupPoseNode.mmd_morph_panel": 3,
                "groupPoseNode.weight": 0.0,
                "plainNetwork.mmd_morph_type": "other",
            }
        )
        presenter, view, _, _ = _make_presenter(model=TEST_MODEL, adapter=adapter)

        presenter.load_morphs()
        presenter.on_morph_selected(_FakeItem("---:B|ボーン笑い", "ボーン笑い"), None)

        self.assertEqual(
            [item.text() for item in view.morph_list.items],
            ["---:G|グループ表情", "---:B|ボーン笑い", "---:M|材質点滅"],
        )
        self.assertEqual(presenter.morph_data["ボーン笑い"]["type"], 10)
        self.assertEqual(presenter.morph_data["ボーン笑い"]["name_en"], "bone_smile")
        self.assertEqual(presenter.morph_data["材質点滅"]["type"], 11)
        self.assertEqual(presenter.morph_data["グループ表情"]["type"], 12)
        self.assertEqual(presenter.morph_data["グループ表情"]["panel"], 3)
        self.assertEqual(presenter.morph_data["グループ表情"]["mmd_morph_type"], "group")
        self.assertEqual(view.blend_shape_edit._text, "boneSmileNode")
        self.assertEqual(view.target_name_edit._text, "weight")
        self.assertEqual(view.connection_status_label.text, "Metadata only")
        self.assertEqual(view.morph_slider.set_value_calls, [25])

    def test_material_network_slider_drives_weight_with_invert_and_multiplier(self):
        adapter = _FakeMayaAdapter()
        adapter.existing.add("materialFlashNode")
        adapter.attr_values["materialFlashNode.weight"] = 0.0
        presenter, view, _, _ = _make_presenter(adapter=adapter)
        view.invert_check = _FakeCheckBox(checked=True)
        view.multiplier_spin = _FakeSpinBox(value=0.5)
        presenter.current_morph = "材質点滅"
        presenter.morph_data = {
            "材質点滅": {
                "morph_node": "materialFlashNode",
                "morph_weight_attr": "weight",
                "mmd_morph_type": "material",
            }
        }

        presenter.on_morph_slider_changed(40)

        # invert: 1.0 - 0.4 = 0.6, then * 0.5 = 0.3
        self.assertIn(("set_attr", "materialFlashNode.weight", 0.3), adapter.calls)
        self.assertEqual(view.morph_value_label.text, "40%")

    def test_group_network_morph_is_discovered_alongside_bone_and_material(self):
        adapter = _FakeMayaAdapter()
        adapter.existing.update({TEST_MODEL, "groupPoseNode"})
        mesh_kwargs = tuple(sorted({"allDescendents": True, "type": "mesh"}.items()))
        adapter.relatives[(TEST_MODEL, mesh_kwargs)] = []
        adapter.ls_results[((), (("type", "network"),))] = ["groupPoseNode"]
        adapter.attr_exists.update(
            {
                ("groupPoseNode", "mmd_morph_type"),
                ("groupPoseNode", "mmd_morph_name"),
                ("groupPoseNode", "mmd_morph_panel"),
            }
        )
        adapter.attr_values.update(
            {
                "groupPoseNode.mmd_morph_type": "group",
                "groupPoseNode.mmd_morph_name": "グループ表情",
                "groupPoseNode.mmd_morph_panel": 2,
                "groupPoseNode.weight": 0.0,
            }
        )
        presenter, view, _, _ = _make_presenter(model=TEST_MODEL, adapter=adapter)

        presenter.load_morphs()

        self.assertEqual([item.text() for item in view.morph_list.items], ["---:G|グループ表情"])
        self.assertEqual(presenter.morph_data["グループ表情"]["type"], 12)
        self.assertEqual(presenter.morph_data["グループ表情"]["panel"], 2)
        self.assertEqual(presenter.morph_data["グループ表情"]["morph_node"], "groupPoseNode")
        self.assertEqual(presenter.morph_data["グループ表情"]["morph_weight_attr"], "weight")

    def test_reset_all_morphs_resets_network_and_blendshape_weights(self):
        adapter = _FakeMayaAdapter()
        adapter.existing.update(
            {
                "faceBlendShape",
                "faceBlendShape.weight[0]",
                "materialFlashNode",
                "missingNetwork",
            }
        )
        adapter.attr_values.update(
            {
                "faceBlendShape.weight[0]": 0.8,
                "materialFlashNode.weight": 0.55,
            }
        )
        adapter.aliases["faceBlendShape"] = ["smile", "weight[0]"]
        presenter, view, app_state, _ = _make_presenter(adapter=adapter)
        presenter.morph_data = {
            "smile": {
                "blend_shape_node": "faceBlendShape",
                "blend_shape_target": "smile",
                "blend_shape_weight_attr": "weight[0]",
            },
            "材質点滅": {
                "morph_node": "materialFlashNode",
                "morph_weight_attr": "weight",
            },
            "gone": {
                "morph_node": "missingNetwork",
                "morph_weight_attr": "weight",
            },
        }
        # mark missingNetwork as absent for the reset path
        adapter.existing.discard("missingNetwork")

        with self.assertLogs(morph_presenter_module.logger.name, level="WARNING") as logs:
            presenter.reset_all_morphs()

        message = "\n".join(logs.output)
        self.assertIn("morph=gone", message)
        self.assertIn("missingNetwork", message)
        self.assertIn(("set_attr", "faceBlendShape.weight[0]", 0), adapter.calls)
        self.assertIn(("set_attr", "materialFlashNode.weight", 0), adapter.calls)
        self.assertEqual(adapter.attr_values["faceBlendShape.weight[0]"], 0)
        self.assertEqual(adapter.attr_values["materialFlashNode.weight"], 0)
        self.assertEqual(view.morph_slider.set_value_calls, [0])
        self.assertEqual(app_state.statuses, [("Reset 2 morph(s)", None)])

    def test_preset_roundtrip_includes_network_morph_weights(self):
        adapter = _FakeMayaAdapter()
        adapter.existing.update(
            {
                TEST_MODEL,
                "faceBlendShape",
                "faceBlendShape.weight[0]",
                "materialFlashNode",
                "groupPoseNode",
            }
        )
        adapter.attr_exists.add((TEST_MODEL, "mmdMorphPresets"))
        adapter.attr_values.update(
            {
                "faceBlendShape.weight[0]": 0.8,
                "materialFlashNode.weight": 0.4,
                "groupPoseNode.weight": 0.0,
                f"{TEST_MODEL}.mmdMorphPresets": json.dumps({}),
            }
        )
        adapter.aliases["faceBlendShape"] = ["smile", "weight[0]"]
        presenter, view, app_state, _ = _make_presenter(model=TEST_MODEL, adapter=adapter)
        view.preset_combo.setCurrentText("network_pose")
        presenter.morph_data = {
            "smile": {
                "blend_shape_node": "faceBlendShape",
                "blend_shape_target": "smile",
                "blend_shape_weight_attr": "weight[0]",
            },
            "材質点滅": {
                "morph_node": "materialFlashNode",
                "morph_weight_attr": "weight",
            },
            "グループ表情": {
                "morph_node": "groupPoseNode",
                "morph_weight_attr": "weight",
            },
        }

        with patch.object(morph_presenter_module, "set_attribute") as set_attribute:
            presenter.save_preset()

        saved_presets = json.loads(set_attribute.call_args[0][2])
        self.assertEqual(
            saved_presets["network_pose"],
            {"smile": 0.8, "材質点滅": 0.4},
        )

        # Mutate scene weights, then reload preset through the same helper path.
        adapter.attr_values["faceBlendShape.weight[0]"] = 0.0
        adapter.attr_values["materialFlashNode.weight"] = 0.0
        adapter.attr_values[f"{TEST_MODEL}.mmdMorphPresets"] = set_attribute.call_args[0][2]
        presenter.current_morph = "材質点滅"
        presenter.load_preset()

        self.assertEqual(adapter.attr_values["faceBlendShape.weight[0]"], 0.8)
        self.assertEqual(adapter.attr_values["materialFlashNode.weight"], 0.4)
        self.assertEqual(adapter.attr_values["groupPoseNode.weight"], 0.0)
        self.assertEqual(view.morph_slider.set_value_calls[-1], 40)
        self.assertIn(("Saved preset 'network_pose'", None), app_state.statuses)
        self.assertIn(("Applied preset 'network_pose'", None), app_state.statuses)

    def test_morph_weight_helper_does_not_double_write_shared_plug(self):
        adapter = _FakeMayaAdapter()
        adapter.existing.update({"faceBlendShape", "faceBlendShape.weight[0]"})
        adapter.aliases["faceBlendShape"] = ["smile", "weight[0]"]
        presenter, _, _, _ = _make_presenter(adapter=adapter)
        # Pathological case: network reference points at the same plug path as BS.
        data = {
            "blend_shape_node": "faceBlendShape",
            "blend_shape_target": "smile",
            "blend_shape_weight_attr": "weight[0]",
            "morph_node": "faceBlendShape",
            "morph_weight_attr": "weight[0]",
        }

        presenter._set_morph_weight(data, 0.55, "smile")

        set_calls = [call for call in adapter.calls if call[0] == "set_attr"]
        self.assertEqual(set_calls, [("set_attr", "faceBlendShape.weight[0]", 0.55)])

        adapter.calls.clear()
        presenter._morph_controller = "model_morphController"
        data["index"] = 7
        presenter._set_morph_weight(data, 0.25, "smile")
        self.assertEqual(
            [call for call in adapter.calls if call[0] == "set_attr"],
            [("set_attr", "model_morphController.inputWeight[7]", 0.25)],
        )

    def test_organize_morphs_by_panel_not_stale_group(self):
        presenter, view, _, _ = _make_presenter()
        presenter.morph_data = {
            # stale custom group must not override panel classification
            "eyebrow_up": {"panel": 1, "group": "カスタム"},
            "eye_close": {"panel": 2, "group": "その他"},
            "mouth_open": {"panel": 3, "group": "眉"},
            "system_base": {"panel": 0, "group": "その他"},
            "fallback": {},  # missing panel -> Other
        }

        presenter._organize_morphs_by_group()

        self.assertEqual(presenter.group_morphs["眉"], ["eyebrow_up"])
        self.assertEqual(presenter.group_morphs["目"], ["eye_close"])
        self.assertEqual(presenter.group_morphs["口"], ["mouth_open"])
        self.assertEqual(presenter.group_morphs["その他"], ["fallback"])
        self.assertNotIn("カスタム", presenter.group_morphs)
        # panel 0 stays in morph_data but is excluded from filter groups
        self.assertNotIn("system_base", presenter.group_morphs["その他"])

        presenter.morph_data.update(
            {
                "smile_key": {"name_jp": "smile"},
                "wink_key": {"name_jp": "wink"},
                "sad_key": {"name_jp": "sad"},
            }
        )
        view.morph_list.items = [
            _FakeItem("display 1", "smile_key"),
            _FakeItem("display 2", "wink_key"),
            _FakeItem("display 3", "sad_key"),
        ]
        presenter.filter_morphs("s")

        self.assertEqual([item.hidden for item in view.morph_list.items], [False, True, False])

    def test_panel_0_to_4_classification_across_morph_types(self):
        adapter = _FakeMayaAdapter()
        adapter.existing.update(
            {
                TEST_MODEL,
                "faceBlendShape",
                "faceBlendShape.weight[0]",
                "faceBlendShape.weight[1]",
                "boneNode",
                "materialNode",
                "groupNode",
                "systemNode",
            }
        )
        mesh_kwargs = tuple(sorted({"allDescendents": True, "type": "mesh"}.items()))
        adapter.relatives[(TEST_MODEL, mesh_kwargs)] = ["faceShape"]
        adapter.history["faceShape"] = ["faceBlendShape"]
        adapter.ls_results[((("faceBlendShape",),), (("type", "blendShape"),))] = ["faceBlendShape"]
        adapter.ls_results[((), (("type", "network"),))] = [
            "boneNode",
            "materialNode",
            "groupNode",
            "systemNode",
        ]
        adapter.aliases["faceBlendShape"] = ["brow_v", "weight[0]", "other_v", "weight[1]"]
        adapter.attr_exists.update(
            {
                ("faceBlendShape", "mmd_blendshape_morph_names_json"),
                ("boneNode", "mmd_morph_type"),
                ("boneNode", "mmd_morph_name"),
                ("boneNode", "mmd_morph_panel"),
                ("boneNode", "mmd_morph_index"),
                ("materialNode", "mmd_morph_type"),
                ("materialNode", "mmd_morph_name"),
                ("materialNode", "mmd_morph_panel"),
                ("materialNode", "mmd_morph_index"),
                ("groupNode", "mmd_morph_type"),
                ("groupNode", "mmd_morph_name"),
                ("groupNode", "mmd_morph_panel"),
                ("groupNode", "mmd_morph_index"),
                ("systemNode", "mmd_morph_type"),
                ("systemNode", "mmd_morph_name"),
                ("systemNode", "mmd_morph_panel"),
                ("systemNode", "mmd_morph_index"),
            }
        )
        adapter.attr_values.update(
            {
                "faceBlendShape.mmd_blendshape_morph_names_json": json.dumps(
                    {"0": "眉頂点", "1": "その他頂点"},
                    ensure_ascii=False,
                ),
                "faceBlendShape.weight[0]": 0.0,
                "faceBlendShape.weight[1]": 0.0,
                "boneNode.mmd_morph_type": "bone",
                "boneNode.mmd_morph_name": "目ボーン",
                "boneNode.mmd_morph_panel": 2,
                "boneNode.mmd_morph_index": 10,
                "materialNode.mmd_morph_type": "material",
                "materialNode.mmd_morph_name": "口材質",
                "materialNode.mmd_morph_panel": 3,
                "materialNode.mmd_morph_index": 11,
                "groupNode.mmd_morph_type": "group",
                "groupNode.mmd_morph_name": "その他グループ",
                "groupNode.mmd_morph_panel": 4,
                "groupNode.mmd_morph_index": 12,
                "systemNode.mmd_morph_type": "bone",
                "systemNode.mmd_morph_name": "base",
                "systemNode.mmd_morph_panel": 0,
                "systemNode.mmd_morph_index": 0,
            }
        )
        # Seed full mmdMorphData so panel is authoritative and stale group is ignored.
        # allow_metadata_entries becomes False; network/BS only attach by matching name.
        adapter.attr_exists.add((TEST_MODEL, "mmdMorphData"))
        adapter.attr_values[f"{TEST_MODEL}.mmdMorphData"] = json.dumps(
            {
                "眉頂点": {
                    "name_jp": "眉頂点",
                    "panel": 1,
                    "type": 0,
                    "index": 0,
                    "group": "stale_custom",
                },
                "その他頂点": {
                    "name_jp": "その他頂点",
                    "panel": 4,
                    "type": 0,
                    "index": 1,
                    "group": "stale_custom",
                },
                "目ボーン": {
                    "name_jp": "目ボーン",
                    "panel": 2,
                    "type": 10,
                    "index": 10,
                    "group": "stale_custom",
                },
                "口材質": {
                    "name_jp": "口材質",
                    "panel": 3,
                    "type": 11,
                    "index": 11,
                    "group": "stale_custom",
                },
                "その他グループ": {
                    "name_jp": "その他グループ",
                    "panel": 4,
                    "type": 12,
                    "index": 12,
                    "group": "stale_custom",
                },
                "base": {
                    "name_jp": "base",
                    "panel": 0,
                    "type": 10,
                    "index": 0,
                    "group": "stale_custom",
                },
            },
            ensure_ascii=False,
        )
        presenter, view, _, _ = _make_presenter(model=TEST_MODEL, adapter=adapter)

        presenter.load_morphs()

        # All morphs (including system) appear in PMX global-index order.
        self.assertEqual(
            [item.data(256) for item in view.morph_list.items],
            ["base", "眉頂点", "その他頂点", "目ボーン", "口材質", "その他グループ"],
        )
        self.assertEqual(presenter.morph_data["base"]["panel"], 0)
        self.assertEqual(presenter.morph_data["眉頂点"]["panel"], 1)
        self.assertEqual(presenter.morph_data["目ボーン"]["panel"], 2)
        self.assertEqual(presenter.morph_data["口材質"]["panel"], 3)
        self.assertEqual(presenter.morph_data["その他頂点"]["panel"], 4)
        self.assertEqual(presenter.morph_data["その他グループ"]["panel"], 4)
        # Network attach + weight targets wired without inventing panel.
        self.assertEqual(presenter.morph_data["目ボーン"]["morph_node"], "boneNode")
        self.assertEqual(presenter.morph_data["口材質"]["morph_node"], "materialNode")
        self.assertEqual(presenter.morph_data["その他グループ"]["morph_node"], "groupNode")
        self.assertEqual(presenter.morph_data["base"]["morph_node"], "systemNode")
        self.assertTrue(presenter.morph_data["眉頂点"].get("blend_shape_targets"))

        # Stale custom group did not drive classification.
        self.assertEqual(presenter.group_morphs["眉"], ["眉頂点"])
        self.assertEqual(presenter.group_morphs["目"], ["目ボーン"])
        self.assertEqual(presenter.group_morphs["口"], ["口材質"])
        self.assertEqual(
            sorted(presenter.group_morphs["その他"]),
            ["その他グループ", "その他頂点"],
        )
        self.assertNotIn("base", presenter.group_morphs["その他"])

        self.assertIn("base", [item.data(256) for item in view.morph_list.items])

    def test_multi_mesh_namespace_same_name_merges_targets_not_list_items(self):
        adapter = _FakeMayaAdapter()
        adapter.existing.update(
            {
                TEST_MODEL,
                "ns:faceBlendShapeA",
                "ns:faceBlendShapeA.weight[0]",
                "faceBlendShapeB",
                "faceBlendShapeB.weight[3]",
            }
        )
        mesh_kwargs = tuple(sorted({"allDescendents": True, "type": "mesh"}.items()))
        adapter.relatives[(TEST_MODEL, mesh_kwargs)] = ["ns:faceShapeA", "faceShapeB"]
        adapter.history["ns:faceShapeA"] = ["ns:faceBlendShapeA"]
        adapter.history["faceShapeB"] = ["faceBlendShapeB"]
        adapter.ls_results[((("ns:faceBlendShapeA",),), (("type", "blendShape"),))] = [
            "ns:faceBlendShapeA"
        ]
        adapter.ls_results[((("faceBlendShapeB",),), (("type", "blendShape"),))] = [
            "faceBlendShapeB"
        ]
        adapter.attr_exists.update(
            {
                ("ns:faceBlendShapeA", "mmd_blendshape_morph_names_json"),
                ("faceBlendShapeB", "mmd_blendshape_morph_names_json"),
            }
        )
        adapter.attr_values.update(
            {
                "ns:faceBlendShapeA.mmd_blendshape_morph_names_json": json.dumps(
                    {"0": "笑顔"}, ensure_ascii=False
                ),
                "faceBlendShapeB.mmd_blendshape_morph_names_json": json.dumps(
                    {"3": "笑顔"}, ensure_ascii=False
                ),
                "ns:faceBlendShapeA.weight[0]": 0.0,
                "faceBlendShapeB.weight[3]": 0.0,
            }
        )
        adapter.aliases["ns:faceBlendShapeA"] = ["smile_a", "weight[0]"]
        adapter.aliases["faceBlendShapeB"] = ["smile_b", "weight[3]"]
        presenter, view, _, _ = _make_presenter(model=TEST_MODEL, adapter=adapter)

        presenter.load_morphs()

        self.assertEqual([item.text() for item in view.morph_list.items], ["000:V|笑顔"])
        data = presenter.morph_data["笑顔"]
        self.assertEqual(data["panel"], 4)  # invent Other, never System
        self.assertEqual(data["index"], 0)  # first-seen weight index retained
        self.assertEqual(
            data["blend_shape_targets"],
            [
                {"node": "ns:faceBlendShapeA", "target": "smile_a", "weight_attr": "weight[0]"},
                {"node": "faceBlendShapeB", "target": "smile_b", "weight_attr": "weight[3]"},
            ],
        )
        self.assertEqual([item.text() for item in view.morph_list.items], ["000:V|笑顔"])

    def test_existing_panel_metadata_not_overwritten_by_fallback_load(self):
        adapter = _FakeMayaAdapter()
        adapter.existing.update(
            {
                TEST_MODEL,
                "faceBlendShape",
                "faceBlendShape.weight[0]",
                "boneNode",
            }
        )
        mesh_kwargs = tuple(sorted({"allDescendents": True, "type": "mesh"}.items()))
        adapter.relatives[(TEST_MODEL, mesh_kwargs)] = ["faceShape"]
        adapter.history["faceShape"] = ["faceBlendShape"]
        adapter.ls_results[((("faceBlendShape",),), (("type", "blendShape"),))] = ["faceBlendShape"]
        adapter.ls_results[((), (("type", "network"),))] = ["boneNode"]
        adapter.aliases["faceBlendShape"] = ["smile", "weight[0]"]
        adapter.attr_exists.update(
            {
                (TEST_MODEL, "mmdMorphData"),
                ("faceBlendShape", "mmd_blendshape_morph_names_json"),
                ("boneNode", "mmd_morph_type"),
                ("boneNode", "mmd_morph_name"),
                ("boneNode", "mmd_morph_panel"),
                ("boneNode", "mmd_morph_index"),
            }
        )
        adapter.attr_values.update(
            {
                f"{TEST_MODEL}.mmdMorphData": json.dumps(
                    {
                        "笑顔": {
                            "name_jp": "笑顔",
                            "name_en": "smile",
                            "panel": 3,
                            "type": 0,
                            "index": 5,
                            "group": "ユーザー分類",
                        },
                        "ボーン笑い": {
                            "name_jp": "ボーン笑い",
                            "name_en": "bone_smile",
                            "panel": 2,
                            "type": 10,
                            "index": 7,
                            "group": "ユーザー分類",
                        },
                    },
                    ensure_ascii=False,
                ),
                "faceBlendShape.mmd_blendshape_morph_names_json": json.dumps(
                    {"0": "笑顔"}, ensure_ascii=False
                ),
                "faceBlendShape.weight[0]": 0.0,
                "boneNode.mmd_morph_type": "bone",
                "boneNode.mmd_morph_name": "ボーン笑い",
                "boneNode.mmd_morph_panel": 4,  # must not overwrite existing panel=2
                "boneNode.mmd_morph_index": 99,
            }
        )
        presenter, _, _, _ = _make_presenter(model=TEST_MODEL, adapter=adapter)

        presenter.load_morphs()

        self.assertEqual(presenter.morph_data["笑顔"]["panel"], 3)
        self.assertEqual(presenter.morph_data["笑顔"]["index"], 5)
        self.assertNotIn("group", presenter.morph_data["笑顔"])
        self.assertEqual(presenter.morph_data["ボーン笑い"]["panel"], 2)
        self.assertEqual(presenter.morph_data["ボーン笑い"]["index"], 7)
        self.assertEqual(presenter.group_morphs["口"], ["笑顔"])
        self.assertEqual(presenter.group_morphs["目"], ["ボーン笑い"])

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

        presenter.on_morph_selected(_FakeItem("000:V|笑顔", "smile"), None)

        self.assertEqual(presenter.current_morph, "smile")
        self.assertEqual(view.details_enabled_calls, [True])
        self.assertEqual(view.morph_name_jp_edit._text, "笑顔")
        self.assertEqual(view.morph_name_en_edit._text, "smile")
        self.assertEqual(view.panel_combo.set_index_calls, [1])
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
            "smile": {"name_jp": "旧", "name_en": "old", "panel": 0, "type": 0},
        }
        view.morph_name_jp_edit.setText("新")
        view.morph_name_en_edit.setText("new")
        view.panel_combo.setCurrentIndex(2)
        view.morph_type_combo.setCurrentIndex(1)

        with patch.object(morph_presenter_module, "set_custom_attributes") as set_custom_attributes:
            presenter.apply_changes()

        saved_json = set_custom_attributes.call_args[0][1]["mmdMorphData"]
        self.assertEqual(
            json.loads(saved_json)["smile"],
            {"name_jp": "新", "name_en": "new", "panel": 2, "type": 1},
        )
        self.assertEqual(presenter.group_morphs["目"], ["smile"])
        self.assertIn(("object_exists", TEST_MODEL), adapter.calls)
        self.assertEqual(app_state.statuses, [("Applied morph changes: smile", None)])

    def test_apply_changes_rebuilds_capability_cache_after_type_change(self):
        presenter, view, _, _ = _make_presenter()
        presenter.current_morph = "smile"
        data = {"name_jp": "smile", "panel": 3, "type": 0, "index": 1}
        presenter.morph_data = {"smile": data}
        presenter._cache_morph_capabilities()
        self.assertTrue(presenter._morph_controls_supported(data))

        view.morph_name_jp_edit.setText("smile")
        view.morph_name_en_edit.setText("")
        view.panel_combo.setCurrentIndex(3)
        view.morph_type_combo.setCurrentIndex(1)  # UV is unsupported.

        presenter.apply_changes()

        self.assertFalse(presenter._morph_controls_supported(data))


if __name__ == "__main__":
    unittest.main()
