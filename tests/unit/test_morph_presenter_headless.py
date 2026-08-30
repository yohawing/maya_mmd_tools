"""MorphPresenterのMaya非依存ロジックとadapter-routingを検証するテスト。"""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from tests.common.maya_stub import install_headless_ui_stubs

install_headless_ui_stubs()

from mmd_tools.ui.presenters import morph_presenter as morph_presenter_module  # noqa: E402
from mmd_tools.ui.translations import UITranslator  # noqa: E402
from mmd_tools.core.model_authoring_spec import (  # noqa: E402
    MmdModelAuthoringSpec,
    MmdModelSpec,
    MmdMorphSpec,
)
from mmd_tools.core.morph_binding_resolver import MorphBinding  # noqa: E402
from mmd_tools.core.morph_read_projection import (  # noqa: E402
    MorphAuthoringReadSnapshot,
    MorphBindingProjection,
    MorphBlendShapeReadProjection,
)
from mmd_tools.core.morph_topology import MorphTopologyInspection  # noqa: E402

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
        self.sliderPressed = _FakeSignal()
        self.sliderReleased = _FakeSignal()
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


class _FakeView:
    def __init__(self):
        self.morph_list = _FakeList()

        self.refresh_morphs_btn = _FakeButton()
        self.reset_slider_btn = _FakeButton()
        self.reset_all_btn = _FakeButton()
        self.apply_btn = _FakeButton()
        self.reset_btn = _FakeButton()

        self.search_edit = _FakeLineEdit()
        self.morph_name_jp_edit = _FakeLineEdit()
        self.morph_name_en_edit = _FakeLineEdit()
        self.panel_combo = _FakeComboBox()
        self.morph_type_combo = _FakeComboBox()

        self.morph_slider = _FakeSlider()
        self.morph_value_label = _FakeLabel()

        self.invert_check = _FakeCheckBox()
        self.multiplier_spin = _FakeSpinBox()

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
        self.connection_errors = set()

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
        if key in self.ls_results:
            return self.ls_results[key]
        if len(args) == 1 and isinstance(args[0], str) and args[0] in self.existing:
            return [args[0]]
        return []

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
        if node in self.connection_errors:
            raise RuntimeError(f"No object matches name: {node}")
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


class _FakePreviewCoordinator:
    def __init__(self, adapter):
        self.adapter = adapter
        self.calls = []

    def begin_morph_preview(self, root, targets):
        session = SimpleNamespace(root=root, targets=tuple(targets))
        self.calls.append(("begin_preview", root, tuple(targets)))
        return session

    def update_morph_preview(self, session, value):
        self.calls.append(("update_preview", session.targets, value))
        for plug in session.targets:
            self.adapter.set_attr(plug, value)
        return len(session.targets)

    def commit_morph_preview(self, session):
        self.calls.append(("commit_preview", session.targets))
        return len(session.targets)

    def rollback_morph_preview(self, session):
        self.calls.append(("rollback_preview", session.targets))

    def set_morph_preview(self, root, targets, value):
        self.calls.append(("set_preview", root, tuple(targets), value))
        for plug in targets:
            self.adapter.set_attr(plug, value)
        return len(targets)

    def reset_morph_preview(self, root, targets):
        self.calls.append(("reset_preview", root, tuple(targets)))
        for plug in targets:
            self.adapter.set_attr(plug, 0.0)
        return len(targets)


class _FakeSnapshotProvider:
    def __init__(self, snapshot, before_return=None):
        self.snapshot = snapshot
        self.before_return = before_return
        self.calls = []

    def read_morph_authoring_snapshot(self, root):
        self.calls.append(root)
        if self.before_return is not None:
            self.before_return()
        return self.snapshot


def _snapshot(root=TEST_MODEL, morphs=(), controller="controller", topology=None):
    specs = []
    projections = []
    blend_shapes = []
    for row in morphs:
        name = row["name"]
        index = row["index"]
        morph_type = row.get("morph_type", "vertex")
        identity = row.get("binding_identity", "morphNode{}".format(index))
        specs.append(
            MmdMorphSpec(
                name=name,
                name_english=row.get("name_english", ""),
                index=index,
                panel=row.get("panel", 4),
                morph_type=morph_type,
                offsets=tuple(row.get("offsets", ())),
                binding_identity=identity,
            )
        )
        bindings = []
        for node, alias, target_index in row.get("bindings", ()):
            blend_shapes.append(node)
            bindings.append(
                MorphBinding(
                    raw_pmx_name=name,
                    global_morph_index=index,
                    blend_shape_identity=node,
                    alias=alias,
                    logical_target_index=target_index,
                    weight_plug="{}.weight[{}]".format(node, target_index),
                    controller_identity=controller,
                    controller_slot=index,
                )
            )
        runtime_targets = row.get("runtime_targets")
        if runtime_targets is None:
            if controller:
                runtime_targets = ("{}.inputWeight[{}]".format(controller, index),)
            elif morph_type in {"bone", "material"} and row.get("runtime_supported", True):
                runtime_targets = ("{}.weight".format(identity),)
            else:
                runtime_targets = ()
        projections.append(
            MorphBindingProjection(
                raw_pmx_name=name,
                global_morph_index=index,
                binding_identity=identity,
                bindings=tuple(bindings),
                warnings=(),
                runtime_preview_plugs=tuple(runtime_targets),
                runtime_supported=row.get("runtime_supported", True),
                unsupported_reason=row.get("unsupported_reason", ""),
            )
        )
    inspection = topology or MorphTopologyInspection({}, {}, ())
    return MorphAuthoringReadSnapshot(
        spec=MmdModelAuthoringSpec(
            model=MmdModelSpec(name="model"),
            morphs=tuple(specs),
        ),
        projection=MorphBlendShapeReadProjection(
            root_identity=root,
            controller_identity=controller,
            owned_mesh_identities=("|meshShape",),
            owned_blend_shape_identities=tuple(dict.fromkeys(blend_shapes)),
            morphs=tuple(sorted(projections, key=lambda item: item.global_morph_index)),
            owned_non_intermediate_mesh_identities=("|meshShape",),
        ),
        topology_inspection=inspection,
    )


def _runtime_only_snapshot(root=TEST_MODEL, name="smile", targets=(("|face", 0),)):
    bindings = tuple(
        MorphBinding(
            raw_pmx_name=name,
            global_morph_index=0,
            blend_shape_identity=node,
            alias=name,
            logical_target_index=index,
            weight_plug="{}.weight[{}]".format(node, index),
            controller_identity="",
            controller_slot=0,
        )
        for node, index in targets
    )
    return MorphAuthoringReadSnapshot(
        spec=None,
        projection=MorphBlendShapeReadProjection(
            root_identity=root,
            controller_identity="",
            owned_mesh_identities=("|meshShape",),
            owned_blend_shape_identities=tuple(node for node, _index in targets),
            morphs=(
                MorphBindingProjection(
                    raw_pmx_name=name,
                    global_morph_index=0,
                    binding_identity=bindings[0].weight_plug,
                    bindings=bindings,
                    warnings=(),
                    runtime_preview_plugs=tuple(binding.weight_plug for binding in bindings),
                    runtime_supported=True,
                    semantic_registered=False,
                ),
            ),
        ),
        topology_inspection=MorphTopologyInspection({}, {}, ()),
    )


def _make_presenter(model=None, adapter=None, snapshot=None, snapshot_provider=None):
    view = _FakeView()
    app_state = _FakeAppState(None)
    adapter = adapter or _FakeMayaAdapter()
    provider = snapshot_provider or _FakeSnapshotProvider(
        snapshot if snapshot is not None else _snapshot(root=model or TEST_MODEL)
    )
    presenter = MorphPresenter(
        view,
        app_state,
        maya_adapter=adapter,
        authoring_coordinator=_FakePreviewCoordinator(adapter),
        morph_snapshot_provider=provider,
    )
    app_state.current_model_root = model
    presenter._loaded_model_root = model or TEST_MODEL
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
    def test_owned_mesh_queries_request_full_dag_paths(self):
        adapter = _FakeMayaAdapter()
        adapter.relatives[(
            TEST_MODEL,
            (("allDescendents", True), ("fullPath", True), ("type", "mesh")),
        )] = ["|test_model|Geometry|body|bodyShape"]
        adapter.attr_values["|test_model|Geometry|body|bodyShape.intermediateObject"] = False
        presenter, _, _, _ = _make_presenter(model=TEST_MODEL, adapter=adapter)

        self.assertEqual(
            presenter._owned_mesh_shapes(),
            ("|test_model|Geometry|body|bodyShape",),
        )
        self.assertIn(
            (
                "list_relatives",
                TEST_MODEL,
                (),
                {"allDescendents": True, "type": "mesh", "fullPath": True},
            ),
            adapter.calls,
        )

    def test_slider_drag_is_one_fixed_target_session_without_scene_reads_per_move(self):
        presenter, view, _, adapter = _make_presenter(model=TEST_MODEL)
        presenter._morph_controller = "controller"
        presenter.current_morph = "smile"
        presenter.morph_data = {"smile": {"index": 3, "name_jp": "smile"}}
        coordinator = presenter.authoring_coordinator

        presenter.begin_morph_slider_drag()
        adapter.calls.clear()
        presenter.on_morph_slider_changed(20)
        presenter.on_morph_slider_changed(70)
        presenter.end_morph_slider_drag()

        self.assertEqual(
            coordinator.calls,
            [
                ("begin_preview", TEST_MODEL, ("controller.inputWeight[3]",)),
                ("update_preview", ("controller.inputWeight[3]",), 0.2),
                ("update_preview", ("controller.inputWeight[3]",), 0.7),
                ("commit_preview", ("controller.inputWeight[3]",)),
            ],
        )
        self.assertFalse(any(call[0] in {"ls", "list_connections"} for call in adapter.calls))

    def test_model_switch_rolls_back_active_drag_without_retargeting(self):
        presenter, _, app_state, _ = _make_presenter(model=TEST_MODEL)
        presenter._morph_controller = "controllerA"
        presenter.current_morph = "smile"
        presenter.morph_data = {"smile": {"index": 1, "name_jp": "smile"}}
        coordinator = presenter.authoring_coordinator
        presenter.load_morphs = Mock()
        presenter.begin_morph_slider_drag()

        presenter.on_current_model_changed("|other")

        self.assertEqual(
            coordinator.calls[-1],
            ("rollback_preview", ("controllerA.inputWeight[1]",)),
        )
        self.assertIsNone(presenter._morph_preview_session)

    def test_reset_current_and_all_route_through_synchronous_transactions(self):
        presenter, view, app_state, _ = _make_presenter(model=TEST_MODEL)
        presenter._morph_controller = "controller"
        presenter.current_morph = "smile"
        presenter.morph_data = {
            "smile": {"index": 1, "name_jp": "smile"},
            "blink": {"index": 2, "name_jp": "blink"},
        }
        coordinator = presenter.authoring_coordinator

        presenter.reset_current_morph()
        presenter.reset_all_morphs()

        self.assertIn(
            ("reset_preview", TEST_MODEL, ("controller.inputWeight[1]",)),
            coordinator.calls,
        )
        self.assertIn(
            (
                "reset_preview",
                TEST_MODEL,
                ("controller.inputWeight[1]", "controller.inputWeight[2]"),
            ),
            coordinator.calls,
        )
        self.assertEqual(view.morph_slider.set_value_calls, [0, 0])
        self.assertEqual(app_state.statuses, [("Reset 2 morph(s)", None)])

    def test_failed_preview_update_restores_cached_ui_without_scene_read(self):
        presenter, view, app_state, adapter = _make_presenter(model=TEST_MODEL)
        presenter._morph_controller = "controller"
        presenter.current_morph = "smile"
        presenter.morph_data = {"smile": {"index": 1, "name_jp": "smile"}}
        presenter._last_morph_preview_value = 30
        view.morph_slider.value = 80
        presenter.authoring_coordinator.set_morph_preview = Mock(
            side_effect=RuntimeError("readback failed")
        )

        presenter.on_morph_slider_changed(80)

        self.assertEqual(view.morph_slider.set_value_calls, [30])
        self.assertEqual(view.morph_value_label.text, "30%")
        self.assertFalse(presenter._morph_preview_dragging)
        self.assertEqual(app_state.statuses, [])
        self.assertEqual(adapter.calls, [])

    def test_failed_drag_begin_restores_cached_ui_and_clears_drag_state(self):
        presenter, view, _, adapter = _make_presenter(model=TEST_MODEL)
        presenter._morph_controller = "controller"
        presenter.current_morph = "smile"
        presenter.morph_data = {"smile": {"index": 1, "name_jp": "smile"}}
        presenter._last_morph_preview_value = 40
        view.morph_slider.value = 40
        presenter.authoring_coordinator.begin_morph_preview = Mock(
            side_effect=RuntimeError("locked")
        )

        presenter.begin_morph_slider_drag()

        self.assertEqual(view.morph_slider.set_value_calls, [40])
        self.assertEqual(view.morph_value_label.text, "40%")
        self.assertFalse(presenter._morph_preview_dragging)
        self.assertIsNone(presenter._morph_preview_session)
        self.assertEqual(adapter.calls, [])

    def test_refresh_pending_detects_unapplied_name_and_panel_edits(self):
        presenter, view, _, _ = _make_presenter()
        presenter.current_morph = "smile"
        presenter._morph_edit_baseline = ("Smile", "", 0, 0)
        view.morph_name_jp_edit.setText("Edited Smile")
        view.panel_combo.setCurrentIndex(2)

        self.assertTrue(presenter._has_pending_refresh_work())

    def test_initial_timer_does_not_bypass_pending_refresh(self):
        presenter, _, app_state, _ = _make_presenter()
        app_state.refresh_generation = 3
        presenter._pending_refresh_generation = 3
        presenter._last_refresh_generation = None
        presenter.load_morphs = Mock()

        presenter._load_initial_morphs()

        presenter.load_morphs.assert_not_called()

    def test_init_connects_signals_without_maya_adapter_calls(self):
        presenter, view, app_state, adapter = _make_presenter()

        self.assertIs(presenter.maya_adapter, adapter)
        self.assertEqual(app_state.current_model_changed._callbacks, [presenter.on_current_model_changed])
        self.assertEqual(view.morph_list.currentItemChanged._callbacks, [presenter.on_morph_selected])
        self.assertEqual(view.refresh_morphs_btn.clicked._callbacks, [presenter.load_morphs])
        self.assertIsNone(presenter.blend_shape_node)
        self.assertIsNone(presenter.current_morph)
        self.assertEqual(presenter.morph_data, {})
        self.assertEqual(adapter.calls, [])

    def _load_snapshot_rows(self, rows):
        adapter = _FakeMayaAdapter()
        adapter.existing.add(TEST_MODEL)
        presenter, view, _, _ = _make_presenter(
            model=TEST_MODEL, adapter=adapter, snapshot=_snapshot(morphs=tuple(rows))
        )
        presenter.load_morphs()
        return presenter, view, adapter

    def test_legacy_network_morph_accepts_short_and_long_root_aliases(self):
        presenter, _, adapter = self._load_snapshot_rows((
            {"name": "LegacyBone", "index": 2, "morph_type": "bone",
             "binding_identity": "|legacyMorph"},))
        self.assertEqual(presenter.morph_data["LegacyBone"]["binding_identity"], "|legacyMorph")
        self.assertFalse(any(call[0] in {"ls", "list_connections"} for call in adapter.calls))

    def test_load_morphs_no_model_clears_state_and_returns_before_adapter(self):
        presenter, view, _, adapter = _make_presenter(model=None)
        presenter.morph_data = {"stale": {}}
        presenter.load_morphs()
        self.assertEqual((presenter.morph_data, presenter.group_morphs), ({}, {}))
        self.assertEqual(view.details_enabled_calls, [False])
        self.assertEqual(adapter.calls, [])

    def test_tab_activation_loads_new_model_once_and_preserves_current_state(self):
        adapter = _FakeMayaAdapter()
        adapter.existing.add(TEST_MODEL)
        provider = _FakeSnapshotProvider(_snapshot())
        presenter, view, state, _ = _make_presenter(adapter=adapter, snapshot_provider=provider)
        state.current_model_root = TEST_MODEL
        presenter.ensure_morphs_loaded()
        view.search_edit._text = "keep"
        presenter.current_morph = "selected"
        presenter.ensure_morphs_loaded()
        self.assertEqual(provider.calls, [TEST_MODEL])
        self.assertEqual((view.search_edit._text, presenter.current_morph), ("keep", "selected"))

    def test_production_coordinator_snapshot_wins_over_optional_provider(self):
        view = _FakeView()
        state = _FakeAppState(TEST_MODEL)
        adapter = _FakeMayaAdapter()
        adapter.existing.add(TEST_MODEL)
        coordinator = _FakePreviewCoordinator(adapter)
        coordinator.read_morph_authoring_snapshot = Mock(return_value=_snapshot())
        unused = _FakeSnapshotProvider(_snapshot())

        presenter = MorphPresenter(
            view,
            state,
            maya_adapter=adapter,
            authoring_coordinator=coordinator,
            morph_snapshot_provider=unused,
        )
        presenter.load_morphs()

        coordinator.read_morph_authoring_snapshot.assert_called_once_with(TEST_MODEL)
        self.assertEqual(unused.calls, [])

    def test_stale_model_snapshot_is_rejected_before_hidden_targets_publish(self):
        adapter = _FakeMayaAdapter()
        adapter.existing.add("|modelB")
        presenter, _, _, _ = _make_presenter(
            model="|modelB",
            adapter=adapter,
            snapshot=_snapshot(
                root="|modelA",
                morphs=({"name": "old", "index": 0},),
                controller="oldController",
            ),
        )

        presenter.load_morphs()

        self.assertEqual(presenter.morph_data, {})
        self.assertIsNone(presenter._loaded_model_root)
        self.assertIsNone(presenter._morph_controller)

    def test_refresh_generation_change_rejects_snapshot_before_publish(self):
        adapter = _FakeMayaAdapter()
        adapter.existing.add(TEST_MODEL)
        state_holder = {}
        provider = _FakeSnapshotProvider(
            _snapshot(morphs=({"name": "old", "index": 0},)),
            before_return=lambda: setattr(state_holder["state"], "refresh_generation", 2),
        )
        presenter, _, state, _ = _make_presenter(
            model=TEST_MODEL,
            adapter=adapter,
            snapshot_provider=provider,
        )
        state_holder["state"] = state
        state.refresh_generation = 1

        presenter.load_morphs()

        self.assertEqual(provider.calls, [TEST_MODEL])
        self.assertEqual(presenter.morph_data, {})
        self.assertIsNone(presenter._loaded_model_root)

    def test_load_morphs_routes_through_adapter_and_populates_view(self):
        presenter, view, adapter = self._load_snapshot_rows((
            {"name": "笑顔", "name_english": "smile", "index": 0, "panel": 1,
             "bindings": (("|faceBlendShape", "smile", 0),)},))
        self.assertEqual(presenter.blend_shape_node, "|faceBlendShape")
        self.assertEqual([item.text() for item in view.morph_list.items], ["0:V|smile"])
        self.assertFalse(any(call[0] in {"list_relatives", "list_history", "alias_attr",
                                        "list_connections"} for call in adapter.calls))

    def test_runtime_only_snapshot_previews_hidden_targets_without_authoring(self):
        adapter = _FakeMayaAdapter()
        adapter.existing.add(TEST_MODEL)
        presenter, view, _, _ = _make_presenter(
            model=TEST_MODEL,
            adapter=adapter,
            snapshot=_runtime_only_snapshot(
                targets=(("|faceA", 0), ("|faceB", 3)),
            ),
        )

        presenter.load_morphs()
        presenter.current_morph = "smile"
        presenter.on_morph_slider_changed(65)

        self.assertFalse(presenter._authoring_ready)
        self.assertEqual([item.text() for item in view.morph_list.items], ["0:V|smile"])
        self.assertIn(("set_attr", "|faceA.weight[0]", 0.65), adapter.calls)
        self.assertIn(("set_attr", "|faceB.weight[3]", 0.65), adapter.calls)

    def test_list_metadata_preserves_duplicate_and_empty_names(self):
        presenter, _, _ = self._load_snapshot_rows((
            {"name": "笑顔", "index": 4}, {"name": "笑顔", "index": 7},
            {"name": "", "index": 9}))
        self.assertEqual(list(presenter.morph_data), ["笑顔", "笑顔#", "Morph [9]"])
        self.assertEqual([row["index"] for row in presenter.morph_data.values()], [4, 7, 9])

    def test_display_uses_global_index_type_letter_and_stable_user_role_key(self):
        presenter, view, _, _ = _make_presenter()
        presenter.morph_data = {
            "m": {"name_jp": "同名", "type": 8, "index": -1, "_pmx_type_raw": True},
            "b": {"name_jp": "同名", "type": 2, "index": 7, "_pmx_type_raw": True},
            "v": {"name_jp": "同名", "type": 1, "index": 4, "_pmx_type_raw": True}}
        presenter._display_all_morphs()
        self.assertEqual(
            [item.text() for item in view.morph_list.items],
            ["4:V|Morph 4", "7:B|Morph 7", "-:M|Morph 2"],
        )
        self.assertEqual([item.data(256) for item in view.morph_list.items], ["v", "b", "m"])

    def test_duplicate_blendshape_names_bind_by_weight_index_deterministically(self):
        presenter, _, _ = self._load_snapshot_rows((
            {"name": "笑顔", "index": 4, "bindings": (("|a", "a", 0),)},
            {"name": "笑顔", "index": 7, "bindings": (("|b", "b", 1),)}))
        self.assertEqual(presenter.morph_data["笑顔"]["blend_shape_weight_attr"], "weight[0]")
        self.assertEqual(presenter.morph_data["笑顔#"]["blend_shape_weight_attr"], "weight[1]")

    def test_global_morph_index_wins_across_split_mesh_local_order(self):
        presenter, _, _ = self._load_snapshot_rows((
            {"name": "A", "index": 7, "bindings": (("|a", "x", 0),)},
            {"name": "B", "index": 4, "bindings": (("|b", "x", 5),)}))
        self.assertEqual(presenter.morph_data["A"]["runtime_targets"],
                         ("controller.inputWeight[7]",))
        self.assertEqual(presenter.morph_data["B"]["runtime_targets"],
                         ("controller.inputWeight[4]",))

    def test_raw_pmx_type_ui_mapping_round_trips_without_mutating_storage(self):
        for raw, ui in morph_presenter_module._PMX_TYPE_TO_UI_INDEX.items():
            self.assertEqual(morph_presenter_module._UI_INDEX_TO_PMX_TYPE[ui], raw)
        presenter, view, _, _ = _make_presenter()
        presenter.morph_data = {"material": {"name_jp": "material", "panel": 4,
                                              "type": 8, "index": 3, "_pmx_type_raw": True}}
        presenter.load_morph_details("material")
        self.assertEqual((view.morph_type_combo.currentIndex(),
                          presenter.morph_data["material"]["type"]), (11, 8))

    def test_runtime_capability_disables_unsupported_controls_without_hiding_details(self):
        presenter, view, _ = self._load_snapshot_rows((
            {"name": "bone", "index": 1, "morph_type": "bone"},
            {"name": "uv", "index": 3, "morph_type": "uv", "runtime_supported": False}))
        presenter.on_morph_selected(_FakeItem("bone", "bone"), None)
        self.assertEqual(view.controls_enabled_calls[-1], (True, ""))
        presenter.on_morph_selected(_FakeItem("uv", "uv"), None)
        self.assertEqual(view.controls_enabled_calls[-1],
                         (False, "tooltips:morph_runtime_unsupported"))
        self.assertTrue(view.details_enabled_calls[-1])

    def test_material_capability_requires_canonical_network_node(self):
        presenter, view, adapter = self._load_snapshot_rows((
            {"name": "material", "index": 2, "morph_type": "material",
             "runtime_supported": False},))
        presenter.on_morph_selected(_FakeItem("material", "material"), None)
        self.assertFalse(view.controls_enabled_calls[-1][0])
        self.assertFalse(any(call[0] == "node_type" for call in adapter.calls))

    def test_material_capability_uses_controller_output_when_network_lookup_misses(self):
        presenter, view, _ = self._load_snapshot_rows((
            {"name": "material", "index": 7, "morph_type": "material"},))
        presenter.on_morph_selected(_FakeItem("material", "material"), None)
        self.assertEqual(view.controls_enabled_calls[-1], (True, ""))
        self.assertEqual(presenter.morph_data["material"]["runtime_targets"],
                         ("controller.inputWeight[7]",))

    def test_owned_network_preview_remains_available_without_controller(self):
        adapter = _FakeMayaAdapter()
        adapter.existing.add(TEST_MODEL)
        presenter, view, _, _ = _make_presenter(
            model=TEST_MODEL,
            adapter=adapter,
            snapshot=_snapshot(
                controller="",
                morphs=(
                    {
                        "name": "bone",
                        "index": 2,
                        "morph_type": "bone",
                        "binding_identity": "|boneMorph",
                    },
                ),
            ),
        )

        presenter.load_morphs()
        presenter.on_morph_selected(_FakeItem("bone", "bone"), None)

        self.assertEqual(view.controls_enabled_calls[-1], (True, ""))
        self.assertEqual(
            presenter.morph_data["bone"]["runtime_targets"],
            ("|boneMorph.weight",),
        )

    def test_sparse_output_weight_failure_is_unsupported_not_startup_error(self):
        presenter, _, adapter = self._load_snapshot_rows((
            {"name": "material", "index": 85, "morph_type": "material",
             "runtime_supported": False},))
        self.assertFalse(presenter._morph_controls_supported(presenter.morph_data["material"]))
        self.assertFalse(any(call[0] == "list_connections" for call in adapter.calls))
        with self.assertRaisesRegex(RuntimeError, "does not support runtime preview"):
            presenter._preview_targets_for_morph("material")

    def test_cached_material_capability_does_not_depend_on_shader_route(self):
        presenter, _, adapter = self._load_snapshot_rows((
            {"name": "material", "index": 2, "morph_type": "material"},))
        data = presenter.morph_data["material"]
        self.assertTrue(presenter._morph_controls_supported(data))
        adapter.existing.clear()
        self.assertTrue(presenter._morph_controls_supported(data))

    def test_group_capability_requires_referenced_bone_morph(self):
        presenter, view, _ = self._load_snapshot_rows((
            {"name": "group", "index": 3, "morph_type": "group",
             "runtime_supported": False},))
        presenter.on_morph_selected(_FakeItem("group", "group"), None)
        self.assertFalse(view.controls_enabled_calls[-1][0])
        presenter, view, _ = self._load_snapshot_rows((
            {"name": "bone", "index": 1, "morph_type": "bone"},
            {"name": "group", "index": 3, "morph_type": "group"}))
        presenter.on_morph_selected(_FakeItem("group", "group"), None)
        self.assertTrue(view.controls_enabled_calls[-1][0])

    def test_group_offsets_are_read_from_network_metadata(self):
        snapshot = _snapshot(morphs=(
            {"name": "group", "index": 3, "morph_type": "group",
             "offsets": ({"morph_index": 7, "morph_rate": 0.25},)},))
        self.assertEqual(dict(snapshot.spec.morphs[0].offsets[0]),
                         {"morph_index": 7, "morph_rate": 0.25})

    def test_load_morphs_falls_back_to_blendshape_raw_names_and_split_targets(self):
        presenter, view, adapter = self._load_snapshot_rows((
            {"name": "笑顔", "index": 0, "bindings": (
                ("|a", "smile_a", 0), ("|b", "smile_b", 0))},))
        presenter.current_morph = "笑顔"
        presenter.on_morph_slider_changed(65)
        self.assertEqual([item.text() for item in view.morph_list.items], ["0:V|Morph 0"])
        self.assertEqual(len(presenter.morph_data["笑顔"]["blend_shape_targets"]), 2)
        self.assertIn(("set_attr", "controller.inputWeight[0]", 0.65), adapter.calls)

    def test_load_morphs_falls_back_to_network_morph_nodes_for_display(self):
        presenter, view, _ = self._load_snapshot_rows((
            {"name": "group", "index": 1, "morph_type": "group"},
            {"name": "bone", "index": 2, "morph_type": "bone"},
            {"name": "material", "index": 3, "morph_type": "material"}))
        self.assertEqual([item.data(256) for item in view.morph_list.items],
                         ["group", "bone", "material"])
        self.assertEqual(presenter.morph_data["bone"]["mmd_morph_type"], "bone")
        self.assertEqual(presenter.morph_data["material"]["mmd_morph_type"], "material")

    def test_material_network_slider_drives_weight_with_invert_and_multiplier(self):
        presenter, view, adapter = self._load_snapshot_rows((
            {"name": "material", "index": 2, "morph_type": "material"},))
        view.invert_check = _FakeCheckBox(checked=True)
        view.multiplier_spin = _FakeSpinBox(value=0.5)
        presenter.current_morph = "material"
        presenter.on_morph_slider_changed(40)
        self.assertIn(("set_attr", "controller.inputWeight[2]", 0.3), adapter.calls)
        self.assertEqual(view.morph_value_label.text, "40%")

    def test_group_network_morph_is_discovered_alongside_bone_and_material(self):
        presenter, view, _ = self._load_snapshot_rows((
            {"name": "group", "index": 5, "morph_type": "group", "panel": 2,
             "binding_identity": "|groupPoseNode"},))
        self.assertEqual([item.text() for item in view.morph_list.items], ["5:G|group"])
        self.assertEqual(presenter.morph_data["group"]["binding_identity"], "|groupPoseNode")

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
        self.assertIn("Reset all morphs failed", message)
        self.assertFalse(any(call[0] == "set_attr" for call in adapter.calls))
        self.assertEqual(view.morph_slider.set_value_calls, [])
        self.assertEqual(app_state.statuses, [])

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
        presenter, view, _ = self._load_snapshot_rows((
            {"name": "base", "index": 0, "panel": 0, "morph_type": "bone"},
            {"name": "眉頂点", "index": 1, "panel": 1},
            {"name": "目ボーン", "index": 2, "panel": 2, "morph_type": "bone"},
            {"name": "口材質", "index": 3, "panel": 3, "morph_type": "material"},
            {"name": "その他頂点", "index": 4, "panel": 4},
            {"name": "その他グループ", "index": 5, "panel": 4, "morph_type": "group"},
        ))
        self.assertEqual([item.data(256) for item in view.morph_list.items],
                         ["base", "眉頂点", "目ボーン", "口材質", "その他頂点", "その他グループ"])
        self.assertEqual(presenter.group_morphs["眉"], ["眉頂点"])
        self.assertEqual(presenter.group_morphs["目"], ["目ボーン"])
        self.assertEqual(presenter.group_morphs["口"], ["口材質"])
        self.assertEqual(sorted(presenter.group_morphs["その他"]),
                         ["その他グループ", "その他頂点"])
        self.assertNotIn("base", presenter.group_morphs["その他"])

    def test_multi_mesh_namespace_same_name_merges_targets_not_list_items(self):
        presenter, view, _ = self._load_snapshot_rows((
            {"name": "笑顔", "index": 0, "bindings": (
                ("|ns:faceBlendShapeA", "smile_a", 0),
                ("|faceBlendShapeB", "smile_b", 3),
            )},
        ))
        self.assertEqual([item.text() for item in view.morph_list.items], ["0:V|Morph 0"])
        self.assertEqual(presenter.morph_data["笑顔"]["blend_shape_targets"], [
            {"node": "|ns:faceBlendShapeA", "target": "smile_a", "weight_attr": "weight[0]"},
            {"node": "|faceBlendShapeB", "target": "smile_b", "weight_attr": "weight[3]"},
        ])

    def test_existing_panel_metadata_not_overwritten_by_fallback_load(self):
        presenter, _, _ = self._load_snapshot_rows((
            {"name": "笑顔", "index": 5, "panel": 3, "name_english": "smile"},
            {"name": "ボーン笑い", "index": 7, "panel": 2, "morph_type": "bone"},
        ))
        self.assertEqual((presenter.morph_data["笑顔"]["panel"],
                          presenter.morph_data["笑顔"]["index"]), (3, 5))
        self.assertEqual((presenter.morph_data["ボーン笑い"]["panel"],
                          presenter.morph_data["ボーン笑い"]["index"]), (2, 7))
        self.assertEqual(presenter.group_morphs["口"], ["笑顔"])
        self.assertEqual(presenter.group_morphs["目"], ["ボーン笑い"])

    def test_on_morph_selected_loads_details_via_adapter_and_updates_view(self):
        adapter = _FakeMayaAdapter()
        adapter.existing.add(TEST_MODEL)
        adapter.attr_values["controller.inputWeight[0]"] = 0.42
        presenter, view, _, _ = _make_presenter(
            model=TEST_MODEL, adapter=adapter,
            snapshot=_snapshot(morphs=({"name": "笑顔", "name_english": "smile",
                                       "index": 0, "panel": 1,
                                       "bindings": (("|faceBlendShape", "smile", 0),)},)),
        )
        presenter.load_morphs()
        adapter.calls.clear()

        presenter.on_morph_selected(_FakeItem("0:V|笑顔 [smile]", "笑顔"), None)

        self.assertEqual(presenter.current_morph, "笑顔")
        self.assertEqual(view.morph_slider.set_value_calls, [42])
        self.assertIn(("get_attr", "controller.inputWeight[0]"), adapter.calls)
        self.assertFalse(any(call[0] in {"list_history", "alias_attr"} for call in adapter.calls))

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

        self.assertFalse(any(call[0] == "set_attr" for call in adapter.calls))
        self.assertEqual(view.morph_slider.set_value_calls, [])
        self.assertEqual(app_state.statuses, [])

    def test_apply_changes_fails_closed_without_coordinator(self):
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

        presenter.apply_changes()

        self.assertEqual(presenter.morph_data["smile"], {"name_jp": "旧", "name_en": "old", "panel": 0, "type": 0})
        self.assertEqual(presenter.group_morphs, {})
        self.assertTrue(app_state.statuses)

    def test_apply_changes_does_not_rebuild_capability_cache_without_coordinator(self):
        presenter, _, _ = self._load_snapshot_rows(
            ({"name": "smile", "index": 1},)
        )
        presenter.current_morph = "smile"
        data = presenter.morph_data["smile"]
        original_type = data["type"]
        self.assertTrue(presenter._morph_controls_supported(data))

        presenter.apply_changes()

        self.assertTrue(presenter._morph_controls_supported(data))
        self.assertEqual(data["type"], original_type)


if __name__ == "__main__":
    unittest.main()
