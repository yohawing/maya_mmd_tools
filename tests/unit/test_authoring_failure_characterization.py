"""Characterize legacy authoring paths that can leave partial Maya state.

These tests intentionally describe the current behavior.  They are failure
injection probes for the follow-up transaction tasks, not production fixes.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from unittest.mock import Mock, patch

from tests.common.maya_stub import install_headless_ui_stubs

install_headless_ui_stubs()

from mmd_tools.ui.presenters.display_pane_presenter import DisplayPanePresenter  # noqa: E402
from mmd_tools.ui.presenters.material_presenter import MaterialPresenter  # noqa: E402
from tests.unit.test_display_pane_presenter import (  # noqa: E402
    _Adapter as _DisplayAdapter,
    _AppState as _DisplayAppState,
    _View as _DisplayView,
)
from tests.unit.test_morph_presenter_headless import (  # noqa: E402
    _FakeMayaAdapter,
    _make_presenter,
)
from tests.unit.test_maya_model_authoring_coordinator import (  # noqa: E402
    _coordinator as _make_coordinator,
)


class _HistoryState:
    """Small deterministic stand-in for Maya's undo/redo state."""

    def __init__(self, *, spec=None, attrs=None, dg=None, restore_hook=None):
        self.spec = spec
        self.attrs = dict(attrs or {})
        self.dg = dict(dg or {})
        self.dg_calls = []
        self._restore_hook = restore_hook
        self._history = []
        self._cursor = -1

    def snapshot(self):
        return {
            "spec": self.spec,
            "attrs": deepcopy(self.attrs),
            "dg": deepcopy(self.dg),
        }

    def _restore(self, snapshot):
        self.spec = snapshot["spec"]
        self.attrs = deepcopy(snapshot["attrs"])
        self.dg = deepcopy(snapshot["dg"])
        if self._restore_hook is not None:
            self._restore_hook(snapshot)

    def _record(self, before):
        self._history = self._history[: self._cursor + 1]
        self._history.append((before, self.snapshot()))
        self._cursor += 1

    def set_attr(self, plug, value):
        before = self.snapshot()
        self.attrs[plug] = value
        self._record(before)

    def undo(self):
        if self._cursor < 0:
            return
        before, _after = self._history[self._cursor]
        self._restore(before)
        self._cursor -= 1

    def redo(self):
        if self._cursor + 1 >= len(self._history):
            return
        self._cursor += 1
        _before, after = self._history[self._cursor]
        self._restore(after)


def _material_snapshot(state, presenter):
    return {
        "spec": state.spec,
        "attrs": {
            key: state.attrs.get(key)
            for key in (
                "material0.mmd_material_name",
                "material0.mmd_edge_size",
                "material0.technique",
                "material0.EdgeSize",
            )
        },
        "dg": {
            **deepcopy(state.dg),
            "mutations": tuple(state.dg_calls),
        },
        "presenter_authoring_fingerprint": presenter.material_data.get("_authoring_fingerprint"),
    }


def _display_snapshot(adapter):
    plug = "model_root.mmd_display_frames_json"
    return {
        "spec": None,
        "attrs": ({plug: adapter.attrs[plug]} if plug in adapter.attrs else {}),
        "dg": {
            "connections": (),
            "mutations": tuple(adapter.dg_calls),
        },
    }


def _morph_snapshot(adapter, plugs):
    return {
        "spec": None,
        "attrs": {plug: adapter.attr_values.get(plug) for plug in plugs},
        "dg": {
            "connections": tuple((plug, "meshShape") for plug in plugs),
            "mutations": tuple(adapter.dg_calls),
        },
    }


def test_material_semantic_commit_remains_after_outline_failure_and_undo_redo():
    """Current order commits semantic Material state before outline mutation."""

    coordinator, backend, _materials, _bones = _make_coordinator()
    # This fixture intentionally exercises the presenter's complete-spec
    # replace route; its narrow reader is not provided by the shared fake.
    coordinator.read_material_value = None  # type: ignore[method-assign]
    old = backend.scene.materials[0]
    new = replace(old, name="New", edge_size=1.0)
    state = _HistoryState(
        spec=backend.scene,
        attrs={
            "material0.mmd_material_name": old.name,
            "material0.mmd_edge_size": old.edge_size,
            "material0.technique": "flat",
            "material0.EdgeSize": old.edge_size,
        },
        dg={"material_output": "material0.outColor->mesh.inColor"},
    )
    pending = []
    original_begin = backend.begin_write
    original_commit = backend.commit_write

    def begin_write(root):
        pending.append(state.snapshot())
        return original_begin(root)

    def commit_write(root):
        result = original_commit(root)
        state.spec = backend.scene
        material = state.spec.materials[0]
        state.attrs.update(
            {
                "material0.mmd_material_name": material.name,
                "material0.mmd_edge_size": material.edge_size,
            }
        )
        state._record(pending.pop())
        return result

    backend.begin_write = begin_write  # type: ignore[method-assign]
    backend.commit_write = commit_write  # type: ignore[method-assign]
    state._restore_hook = lambda snapshot: setattr(backend, "scene", snapshot["spec"])

    view = Mock()
    view.material_list.count.return_value = 0
    view.shader_outline_check.isChecked.return_value = True
    app_state = Mock()
    app_state.current_model_root = None
    app_state.current_model_changed = Mock()
    app_state.emit_status = Mock()
    maya_adapter = Mock()
    maya_adapter.node_type.return_value = "dx11Shader"
    presenter = MaterialPresenter(
        view,
        app_state,
        maya_adapter=maya_adapter,
        authoring_coordinator=coordinator,
    )
    app_state.current_model_root = "|root"
    presenter.current_material = "material0"
    presenter.current_material_index = 0
    presenter.material_data = {
        "shader_outline_enabled": False,
        "edge_size": old.edge_size,
    }
    presenter.has_unsaved_changes = True
    presenter._material_from_authoring_controls = lambda _prior: new

    start = _material_snapshot(state, presenter)
    outline_calls = []

    def fail_outline(_shader, _enabled, _edge_size):
        # ``apply_shader_outline`` currently sets technique before later attrs.
        outline_calls.append((_shader, _enabled, _edge_size))
        state.set_attr("material0.technique", "outline")
        raise RuntimeError("injected outline failure")

    with patch("mmd_tools.converters.mesh_converter.apply_shader_outline", fail_outline):
        assert presenter.apply_changes() is None

    failure = _material_snapshot(state, presenter)
    assert backend.events == [
        "begin",
        "rebase",
        "apply:model",
        "apply:bones",
        "apply:materials",
        "apply:morphs",
        "commit",
    ]
    assert outline_calls == [("material0", True, 1.0)]
    assert start["spec"].materials[0].name == "Material"
    assert failure["spec"].materials[0].name == "New"
    assert failure["attrs"] == {
        "material0.mmd_material_name": "New",
        "material0.mmd_edge_size": 1.0,
        "material0.technique": "outline",
        "material0.EdgeSize": old.edge_size,
    }
    assert failure["dg"] == start["dg"]
    assert state.dg_calls == []
    assert presenter.has_unsaved_changes is True

    state.undo()
    after_undo = _material_snapshot(state, presenter)
    assert after_undo["spec"].materials[0].name == "New"
    assert after_undo["attrs"]["material0.technique"] == "flat"
    assert after_undo["attrs"]["material0.mmd_material_name"] == "New"
    assert after_undo["dg"] == start["dg"]

    state.redo()
    after_redo = _material_snapshot(state, presenter)
    assert after_redo == failure


class _DisplayFailureAdapter(_DisplayAdapter):
    """Display adapter with chunk-level undo and an injected setAttr error."""

    def __init__(self):
        super().__init__()
        self.dg_calls = []
        self.set_attempts = []
        self._chunk_before = None
        self._history = []
        self._cursor = -1

    def undo_info(self, **kwargs):
        if kwargs.get("openChunk"):
            self._chunk_before = deepcopy(self.attrs)
        if kwargs.get("closeChunk") and self._chunk_before is not None:
            before = self._chunk_before
            after = deepcopy(self.attrs)
            self._history = self._history[: self._cursor + 1]
            self._history.append((before, after))
            self._cursor += 1
            self._chunk_before = None
        super().undo_info(**kwargs)

    def set_attr(self, plug, _value, **_kwargs):
        self.set_attempts.append(plug)
        raise RuntimeError("injected display setAttr failure")

    def undo(self):
        if self._cursor < 0:
            return
        before, _after = self._history[self._cursor]
        self.attrs = deepcopy(before)
        self._cursor -= 1

    def redo(self):
        if self._cursor + 1 >= len(self._history):
            return
        self._cursor += 1
        _before, after = self._history[self._cursor]
        self.attrs = deepcopy(after)


def test_display_add_attr_success_then_set_attr_failure_is_undoable_partial_state():
    """Current Display Apply leaves an empty newly-added attr after setAttr fails."""

    view = _DisplayView()
    app_state = _DisplayAppState()
    adapter = _DisplayFailureAdapter()
    presenter = DisplayPanePresenter(view, app_state, maya_adapter=adapter)
    presenter.refresh()
    adapter.attrs.pop("model_root.mmd_display_frames_json")
    presenter.frames[2]["name_english"] = "Changed"
    start = _display_snapshot(adapter)

    assert presenter.apply() is False
    failure = _display_snapshot(adapter)
    assert start == {
        "spec": None,
        "attrs": {},
        "dg": {"connections": (), "mutations": ()},
    }
    assert failure["attrs"] == {"model_root.mmd_display_frames_json": ""}
    assert failure["dg"] == start["dg"]
    assert adapter.dg_calls == []
    assert adapter.set_attempts == ["model_root.mmd_display_frames_json"]
    assert adapter.undo_calls == [
        {"openChunk": True, "chunkName": "Edit Display Frames"},
        {"closeChunk": True},
    ]

    adapter.undo()
    after_undo = _display_snapshot(adapter)
    assert after_undo == start

    adapter.redo()
    after_redo = _display_snapshot(adapter)
    assert after_redo == failure


class _MorphFailureAdapter(_FakeMayaAdapter):
    """Legacy multi-plug adapter: the second setAttr fails after the first."""

    def __init__(self, fail_plug):
        super().__init__()
        self.dg_calls = []
        self.set_attempts = []
        self.fail_plug = fail_plug
        self._history = []
        self._cursor = -1

    def set_attr(self, attr_path, value):
        self.set_attempts.append(attr_path)
        if attr_path == self.fail_plug:
            raise RuntimeError("injected legacy preview setAttr failure")
        before = deepcopy(self.attr_values)
        super().set_attr(attr_path, value)
        after = deepcopy(self.attr_values)
        self._history = self._history[: self._cursor + 1]
        self._history.append((before, after))
        self._cursor += 1

    def undo(self):
        if self._cursor < 0:
            return
        before, _after = self._history[self._cursor]
        self.attr_values = deepcopy(before)
        self._cursor -= 1

    def redo(self):
        if self._cursor + 1 >= len(self._history):
            return
        self._cursor += 1
        _before, after = self._history[self._cursor]
        self.attr_values = deepcopy(after)


def test_legacy_morph_preview_multi_plug_failure_keeps_partial_weight_after_undo_redo():
    """Current legacy preview catches each setAttr independently."""

    first = "faceBlendShapeA.weight[0]"
    second = "faceBlendShapeB.weight[0]"
    adapter = _MorphFailureAdapter(second)
    adapter.existing.update({
        "faceBlendShapeA",
        first,
        "faceBlendShapeB",
        second,
    })
    adapter.aliases["faceBlendShapeA"] = ["smile_a", "weight[0]"]
    adapter.aliases["faceBlendShapeB"] = ["smile_b", "weight[0]"]
    adapter.attr_values.update({first: 0.1, second: 0.2})
    presenter, _view, _app_state, _ = _make_presenter(adapter=adapter)
    presenter.current_morph = "smile"
    presenter.morph_data = {
        "smile": {
            "blend_shape_targets": [
                {"node": "faceBlendShapeA", "target": "smile_a", "weight_attr": "weight[0]"},
                {"node": "faceBlendShapeB", "target": "smile_b", "weight_attr": "weight[0]"},
            ]
        }
    }
    plugs = (first, second)
    start = _morph_snapshot(adapter, plugs)

    presenter.on_morph_slider_changed(80)

    failure = _morph_snapshot(adapter, plugs)
    assert start["attrs"] == {first: 0.1, second: 0.2}
    assert failure["attrs"] == {first: 0.8, second: 0.2}
    assert failure["dg"] == start["dg"]
    assert adapter.dg_calls == []
    assert adapter.set_attempts == [first, second]

    adapter.undo()
    after_undo = _morph_snapshot(adapter, plugs)
    assert after_undo == start

    adapter.redo()
    after_redo = _morph_snapshot(adapter, plugs)
    assert after_redo == failure
