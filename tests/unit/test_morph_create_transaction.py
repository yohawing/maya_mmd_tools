"""Focused narrow Morph creation transaction contracts."""

from types import SimpleNamespace

import pytest

from tests.common.maya_stub import install_headless_ui_stubs, install_maya_stub

install_maya_stub()
install_headless_ui_stubs()

from mmd_tools.adapters.maya_morph_authoring import (  # noqa: E402
    MayaMorphAuthoringError,
    apply_morph_create,
)
from mmd_tools.adapters.maya_scene_metadata_backend import MayaSceneMetadataBackend  # noqa: E402
from mmd_tools.core.model_authoring_spec import MmdMorphSpec  # noqa: E402
from mmd_tools.ui.presenters.morph_presenter import MorphPresenter  # noqa: E402
from tests.unit.test_maya_morph_authoring import FakeAdapter, FakeRegistry  # noqa: E402
from tests.unit.test_maya_model_authoring_coordinator import _coordinator  # noqa: E402
from tests.unit.test_morph_reindex_fast_path import _BackendAdapter  # noqa: E402


def _setup_adapter() -> tuple[FakeAdapter, FakeRegistry]:
    adapter = FakeAdapter()
    adapter.types.update({"morph0": "network", "registry": "network"})
    adapter.attrs.update(
        {
            ("morph0", "mmd_morph_index"): 0,
            ("morph0", "mmd_morph_type"): "bone",
            ("|Model", "mmd_model_registry"): None,
        }
    )
    adapter.connections["|Model.mmd_model_registry"] = ["registry"]
    registry = FakeRegistry(["morph0"])
    return adapter, registry


def test_empty_create_writes_one_binding_and_one_controller_slot() -> None:
    adapter, registry = _setup_adapter()
    morph = MmdMorphSpec("New", name_english="New EN", morph_type="bone", panel=2)
    result = apply_morph_create("|Model", morph, adapter, registry_api=registry)
    assert result.index == 1
    assert result.binding_identity is not None
    assert registry.members[-1] == result.binding_identity
    assert adapter.aliases["controller.inputWeight[1]"] == "morph_1"
    assert adapter.connections["controller.outputWeight[1]"] == [f"{result.binding_identity}.weight"]


def test_nonempty_create_fails_before_any_scene_write() -> None:
    adapter, registry = _setup_adapter()
    morph = MmdMorphSpec(
        "New",
        morph_type="bone",
        offsets=({"bone_index": 0, "translation": (1, 0, 0), "rotation": (0, 0, 0, 1)},),
    )
    with pytest.raises(MayaMorphAuthoringError, match="empty offsets"):
        apply_morph_create("|Model", morph, adapter, registry_api=registry)
    assert registry.members == ["morph0"]
    assert not any(call[0] == "create_node" for call in adapter.calls)


class _Item:
    def __init__(self, key):
        self.key = key

    def setData(self, role, value):
        self.key = value

    def data(self, role):
        return self.key

    def setText(self, text):
        self.text = text


class _List:
    def __init__(self):
        self.items = []
        self.current = None

    def addItem(self, item):
        self.items.append(item)

    def setCurrentItem(self, item):
        self.current = item


def test_presenter_append_created_row_without_reload() -> None:
    presenter = object.__new__(MorphPresenter)
    presenter.morph_data = {}
    presenter._authoring_morphs_by_index = {}
    presenter._authoring_spec = None
    presenter._morphs_by_index = {}
    presenter.view = SimpleNamespace(morph_list=_List())
    presenter.current_morph = None
    created = MmdMorphSpec("Created", morph_type="bone", index=0, binding_identity="morph0")
    presenter._append_created_morph_row(created)
    assert len(presenter.view.morph_list.items) == 1
    assert presenter.current_morph == "Created"
    assert presenter.view.morph_list.current is presenter.view.morph_list.items[0]


def test_coordinator_create_commit_failure_rolls_back_without_full_hooks() -> None:
    coordinator, backend, _, _ = _coordinator()

    def fail_commit(_root, _morph):
        raise RuntimeError("create readback mismatch")

    coordinator._metadata.commit_morph_create = fail_commit
    with pytest.raises(Exception, match="create_morph failed"):
        coordinator.create_morph("|root", MmdMorphSpec("New", morph_type="bone"))
    assert backend.rollback_count == 1
    assert not any(event.startswith("apply:") for event in backend.events)


def test_coordinator_create_invalid_allocated_index_rolls_back() -> None:
    coordinator, backend, _, _ = _coordinator()
    begin = backend.begin_morph_create

    def invalid_index(root, morph):
        begin(root, morph)
        return True

    backend.begin_morph_create = invalid_index
    with pytest.raises(Exception, match="invalid index"):
        coordinator.create_morph("|root", MmdMorphSpec("New", morph_type="bone"))
    assert backend.rollback_count == 1
    assert backend.active is False


class _CreateRegistry:
    def __init__(self, adapter):
        self.adapter = adapter

    def list_model_registry_members(self, root, category):
        return list(self.adapter.connections["|Root|registry.morphMembers"])

    def register_model_members(self, registry, category, members):
        self.adapter.connections["|Root|registry.morphMembers"].extend(members)


def test_backend_create_commit_checks_registry_and_controller_preimage():
    adapter = _BackendAdapter()
    backend = MayaSceneMetadataBackend(adapter)
    registry = _CreateRegistry(adapter)
    morph = MmdMorphSpec("Created", morph_type="bone", index=0)
    backend.begin_morph_create("|Root", morph)
    result = apply_morph_create("|Root", morph, adapter, registry_api=registry)
    backend.commit_morph_create("|Root", result)
    assert adapter.undo_open is False


def test_backend_create_builds_missing_controller_without_topology_readback_mismatch():
    adapter = _BackendAdapter()
    adapter.attrs.pop(("|Root", "mmd_morph_controller"))
    adapter.connections.pop("|Root.mmd_morph_controller")
    backend = MayaSceneMetadataBackend(adapter)
    registry = _CreateRegistry(adapter)
    morph = MmdMorphSpec("Created", morph_type="bone", index=0)
    backend.begin_morph_create("|Root", morph)
    result = apply_morph_create("|Root", morph, adapter, registry_api=registry)
    backend.commit_morph_create("|Root", result)
    assert adapter.undo_open is False
    assert any(kind == "mmdMorphController" for kind in adapter.types.values())
