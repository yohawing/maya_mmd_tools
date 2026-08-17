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
from mmd_tools.adapters.maya_scene_metadata_backend import MayaSceneMetadataBackend  # noqa: E402
from tests.unit.test_display_pane_presenter import (  # noqa: E402
    _Adapter as _DisplayAdapter,
    _AppState as _DisplayAppState,
    _View as _DisplayView,
)
from tests.unit.test_morph_presenter_headless import (  # noqa: E402
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


def test_material_outline_failure_rolls_back_semantic_and_preview_state_atomically():
    """A preview failure restores the semantic and DX11 preimage together."""

    coordinator, backend, materials, _bones = _make_coordinator()
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
    events = []

    coordinator._metadata.read_material_value = lambda *_args: old

    def begin_patch(_root, _binding, _old, _new, outline_enabled):
        assert outline_enabled is True
        pending.append(state.snapshot())
        events.append("begin")

    def semantic_patch(_root, _old, target):
        state.spec = replace(state.spec, materials=(target,))
        state.attrs["material0.mmd_material_name"] = target.name
        state.attrs["material0.mmd_edge_size"] = target.edge_size
        events.append("semantic")
        return target

    def outline_patch(shader, enabled, edge_size):
        events.append("outline")
        from mmd_tools.converters.mesh_converter import apply_shader_outline

        apply_shader_outline(shader, enabled, edge_size)
        return {}

    def rollback(_root):
        state._restore(pending.pop())
        backend.scene = state.spec
        events.append("rollback")

    backend.begin_material_value_patch = begin_patch
    backend.commit_material_value_patch = lambda *_args: events.append("commit")
    backend.rollback_write = rollback
    materials.apply_material_value_patch = semantic_patch
    materials.apply_material_outline = outline_patch

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
        "shader_type": "dx11Shader",
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
    assert events == ["begin", "semantic", "outline", "rollback"]
    assert outline_calls == [("material0", True, 1.0)]
    assert failure == start
    assert presenter.has_unsaved_changes is True


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
        if kwargs.get("query") and kwargs.get("state"):
            return True
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

    def ls(self, node, **kwargs):
        if kwargs.get("long"):
            return [node]
        return [node]

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


def test_display_add_attr_success_then_set_attr_failure_rolls_back_atomically():
    """Display Apply removes a newly-created attr when its value write fails."""

    view = _DisplayView()
    app_state = _DisplayAppState()
    adapter = _DisplayFailureAdapter()
    coordinator, _fake_backend, _materials, _bones = _make_coordinator()
    coordinator._backend = MayaSceneMetadataBackend(adapter)
    presenter = DisplayPanePresenter(
        view,
        app_state,
        maya_adapter=adapter,
        authoring_coordinator=coordinator,
    )
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
    assert failure == start
    assert adapter.dg_calls == []
    assert adapter.set_attempts == ["model_root.mmd_display_frames_json"]
    assert adapter.undo_calls == [
        {"openChunk": True, "chunkName": "Edit Display Frames"},
        {"closeChunk": True},
    ]


class _MorphFailureAdapter:
    """Minimal Maya command surface for a real backend preview transaction."""

    def __init__(self, fail_plug):
        self.fail_plug = fail_plug
        self.nodes = {"|root"}
        self.existing = set()
        self.aliases = {}
        self.attr_values = {}
        self.dg_calls = []
        self.set_attempts = []
        self.undo_chunk_open = False
        self._chunk_before = None
        self.undo_count = 0

    def object_exists(self, node):
        return node in self.nodes or node in self.attr_values

    def ls(self, node, long=False, **_kwargs):
        if node in self.nodes:
            return [node]
        if node in {plug.rsplit(".", 1)[0] for plug in self.attr_values}:
            return [node]
        return []

    def undo_info(self, **kwargs):
        if kwargs.get("query") and kwargs.get("state"):
            return True
        if kwargs.get("openChunk"):
            self.undo_chunk_open = True
            self._chunk_before = deepcopy(self.attr_values)
            return None
        if kwargs.get("closeChunk"):
            self.undo_chunk_open = False
            return None
        raise AssertionError("unexpected undo_info call")

    def get_attr(self, attr_path, **kwargs):
        if kwargs.get("lock"):
            return False
        return self.attr_values[attr_path]

    def set_attr(self, attr_path, value):
        self.set_attempts.append(attr_path)
        if attr_path == self.fail_plug:
            raise RuntimeError("injected preview setAttr failure")
        self.attr_values[attr_path] = value

    def undo(self):
        assert not self.undo_chunk_open
        self.attr_values = deepcopy(self._chunk_before)
        self._chunk_before = None
        self.undo_count += 1


class _BackendPreviewCoordinator:
    """Use the production backend lifecycle around one synchronous preview."""

    def __init__(self, backend):
        self.backend = backend

    def set_morph_preview(self, root, targets, value):
        session = self.backend.begin_morph_preview(root, targets)
        try:
            result = self.backend.apply_morph_preview(
                root, session, (value,) * len(session.targets)
            )
            self.backend.commit_morph_preview(root, session)
            return result
        except Exception:
            self.backend.rollback_morph_preview(root, session)
            raise


def test_morph_preview_multi_plug_failure_rolls_back_partial_weight_once():
    """Canonical multi-target preview restores its preimage after partial write."""

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
    backend = MayaSceneMetadataBackend(adapter)
    presenter, _view, _app_state, _ = _make_presenter(model="|root", adapter=adapter)
    presenter._preview_coordinator = _BackendPreviewCoordinator(backend)
    presenter.current_morph = "smile"
    presenter.morph_data = {
        "smile": {
            "runtime_targets": [first, second],
        }
    }
    plugs = (first, second)
    start = _morph_snapshot(adapter, plugs)

    presenter.on_morph_slider_changed(80)

    failure = _morph_snapshot(adapter, plugs)
    assert start["attrs"] == {first: 0.1, second: 0.2}
    assert failure["attrs"] == start["attrs"]
    assert failure["dg"] == start["dg"]
    assert adapter.set_attempts == [first, second]
    assert adapter.undo_count == 1
    assert backend._write_transaction is None
    assert adapter.undo_chunk_open is False
