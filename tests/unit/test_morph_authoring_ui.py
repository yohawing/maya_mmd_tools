"""Coordinator-only semantic authoring checks for MorphPresenter."""

from dataclasses import dataclass, field

from tests.common.maya_stub import install_headless_ui_stubs

install_headless_ui_stubs()

from mmd_tools.core.model_authoring_spec import (  # noqa: E402
    MmdModelAuthoringSpec,
    MmdModelSpec,
    MmdMorphSpec,
)
from mmd_tools.ui.presenters.morph_presenter import MorphPresenter  # noqa: E402
from tests.unit.test_morph_presenter_headless import (  # noqa: E402
    _FakeAppState,
    _FakeButton,
    _FakeComboBox,
    _FakeMayaAdapter,
    _FakeView,
)


@dataclass
class _Coordinator:
    spec: MmdModelAuthoringSpec
    calls: list[tuple[str, tuple]] = field(default_factory=list)

    def read_spec(self, root):
        self.calls.append(("read", (root,)))
        return self.spec

    def create_morph(self, *args):
        self.calls.append(("create", args))

    def replace_morph(self, *args):
        self.calls.append(("replace", args))

    def replace_morph_offsets(self, *args):
        self.calls.append(("offsets", args))

    def delete_morph(self, *args):
        self.calls.append(("delete", args))

    def move_morph(self, *args):
        self.calls.append(("move", args))

    def reindex_morphs(self, *args):
        self.calls.append(("reindex", args))


def _spec(kind="bone", offsets=()):
    return MmdModelAuthoringSpec(
        model=MmdModelSpec(name="Model"),
        morphs=(
            MmdMorphSpec(
                name="Morph",
                name_english="Morph EN",
                index=0,
                panel=4,
                morph_type=kind,
                offsets=offsets,
                binding_identity="morphNode",
                runtime_capability="unsupported" if kind in {"flip", "impulse"} else "supported",
                loss_policy="reject" if kind in {"flip", "impulse"} else "none",
            ),
        ),
    )


def _view():
    view = _FakeView()
    view.create_type_choice = "group"
    view.create_type_capabilities = ()
    view.choose_create_morph_type = lambda capabilities: (
        setattr(view, "create_type_capabilities", tuple(capabilities))
        or view.create_type_choice
    )
    view.create_morph_btn = _FakeButton()
    view.delete_morph_btn = _FakeButton()
    view.move_morph_up_btn = _FakeButton()
    view.move_morph_down_btn = _FakeButton()
    view.work_offset_combo = _FakeComboBox()
    view.create_work_material_btn = _FakeButton()
    view.apply_work_material_btn = _FakeButton()
    view.clear_work_material_btn = _FakeButton()
    view.authoring_enabled = None
    view.work_controls = (False, (), "")

    def set_authoring_controls_enabled(enabled, tooltip="", reason_key=""):
        view.authoring_enabled = (enabled, tooltip)

    view.set_authoring_controls_enabled = set_authoring_controls_enabled
    view.set_work_material_controls = (
        lambda enabled, offsets=(), tooltip="": setattr(
            view, "work_controls", (enabled, tuple(offsets), tooltip)
        )
    )
    return view


def _presenter(kind="bone", offsets=()):
    view = _view()
    app_state = _FakeAppState("|Model")
    adapter = _FakeMayaAdapter()
    adapter.existing.add("|Model")
    coordinator = _Coordinator(_spec(kind, offsets))
    presenter = MorphPresenter(
        view,
        app_state,
        maya_adapter=adapter,
        authoring_coordinator=coordinator,
    )
    presenter.load_morphs = lambda: None
    presenter._authoring_spec = coordinator.spec
    presenter._authoring_morphs_by_index = {0: coordinator.spec.morphs[0]}
    presenter._authoring_ready = True
    presenter.morph_data = {
        "key": {"index": 0, "name_jp": "Morph", "name_en": "Morph EN", "type": 10}
    }
    presenter.current_morph = "key"
    return presenter, view, app_state, adapter, coordinator


def test_injected_coordinator_enables_semantic_controls_and_metadata_replace():
    presenter, view, _state, adapter, coordinator = _presenter()
    view.morph_name_jp_edit.setText("更新")
    view.morph_name_en_edit.setText("Updated")
    view.panel_combo.setCurrentIndex(2)

    presenter.apply_changes()

    assert view.authoring_enabled == (True, "")
    operation, args = coordinator.calls[-1]
    assert operation == "replace"
    assert args[0] == "|Model"
    assert args[1].name == "更新"
    assert args[1].panel == 2
    assert not any(call[0] == "set_attr" for call in adapter.calls)


def test_raw_offset_json_editor_is_removed_from_presenter():
    presenter, _view, _state, _adapter, _coordinator = _presenter()
    assert not hasattr(presenter, "apply_offsets")


def test_uv_is_visible_roundtrip_only_and_flip_create_is_policy_rejected():
    presenter, view, state, _adapter, coordinator = _presenter(
        "uv", ({"vertex_index": 0, "uv_offset": (0, 0, 0, 0)},)
    )
    presenter.load_morph_details("key")

    view.create_type_choice = "flip"
    presenter.create_morph()
    assert not any(operation == "create" for operation, _args in coordinator.calls)
    assert state.statuses[-1][1] == "error"


def test_create_delete_and_move_use_only_coordinator_methods():
    presenter, view, _state, adapter, coordinator = _presenter()
    presenter.create_morph()
    presenter.delete_current_morph()
    presenter.move_current_morph(1)

    operations = [operation for operation, _args in coordinator.calls]
    assert operations[-2:] == ["create", "delete"]
    # One-item lists cannot move; critically, no Maya metadata write is used.
    assert not any(call[0] == "set_attr" for call in adapter.calls)


def test_create_menu_exposes_supported_types_and_cancel_is_side_effect_free():
    presenter, view, _state, _adapter, coordinator = _presenter()
    view.create_type_choice = None

    presenter.create_morph()

    capabilities = {
        morph_type: (enabled, reason)
        for morph_type, enabled, reason in view.create_type_capabilities
    }
    for morph_type in (
        "uv",
        "additional_uv1",
        "additional_uv2",
        "additional_uv3",
        "additional_uv4",
        "bone",
        "material",
        "group",
    ):
        assert capabilities[morph_type] == (True, "")
    assert capabilities["flip"][0] is False
    assert capabilities["impulse"][0] is False
    assert not any(operation == "create" for operation, _args in coordinator.calls)


def test_vertex_create_is_disabled_and_rejected_before_coordinator_call():
    presenter, view, state, _adapter, coordinator = _presenter("vertex")

    view.create_type_choice = "vertex"
    presenter.create_morph()

    assert not any(operation == "create" for operation, _args in coordinator.calls)
    assert "target creation" in state.statuses[-1][0]
    vertex = next(item for item in view.create_type_capabilities if item[0] == "vertex")
    assert vertex[1] is False
    assert "at least one owned mesh" in vertex[2]


def test_vertex_offsets_have_no_user_facing_json_policy():
    presenter, _view, _state, _adapter, _coordinator = _presenter("vertex")
    presenter.load_morph_details("key")
    assert not hasattr(presenter, "apply_offsets")


def test_missing_coordinator_disables_authoring_but_keeps_presenter_constructible():
    view = _view()
    MorphPresenter(view, _FakeAppState(), maya_adapter=_FakeMayaAdapter())
    assert view.authoring_enabled[0] is False
    assert "not available" in view.authoring_enabled[1]


@dataclass
class _WorkService:
    calls: list[tuple[str, tuple]] = field(default_factory=list)

    def create(self, *args):
        self.calls.append(("create", args))

    def apply(self, *args):
        self.calls.append(("apply", args))

    def clear(self, *args):
        self.calls.append(("clear", args))


def test_material_work_actions_are_separate_from_preview_and_raw_apply():
    offset = {
        "material_index": 0,
        "operation_type": 1,
        "diffuse": (0, 0, 0, 0),
        "specular": (0, 0, 0),
        "specular_coefficient": 0,
        "ambient": (0, 0, 0),
        "edge_color": (0, 0, 0, 0),
        "edge_size": 0,
        "texture_factor": (0, 0, 0, 0),
        "sphere_texture_factor": (0, 0, 0, 0),
        "toon_texture_factor": (0, 0, 0, 0),
    }
    presenter, view, _state, adapter, _coordinator = _presenter("material", (offset,))
    work = _WorkService()
    presenter.material_morph_work = work
    view.work_offset_combo.addItem("Offset 0", 0)

    presenter.create_work_material()
    presenter.apply_work_material()
    presenter.clear_work_material()

    assert work.calls == [
        ("create", ("|Model", 0, 0)),
        ("apply", ("|Model", 0, 0)),
        ("clear", ("|Model",)),
    ]
    assert not any(call[0] == "set_attr" for call in adapter.calls)
