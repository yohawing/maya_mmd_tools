"""Focused contracts for selected-morph value patch routing."""

from dataclasses import replace

import pytest

from tests.common.maya_stub import install_maya_stub

install_maya_stub()

from mmd_tools.adapters.maya_morph_authoring import apply_morph_value_patch  # noqa: E402
from mmd_tools.core.morph_authoring import classify_morph_change  # noqa: E402
from mmd_tools.core.model_authoring_spec import MmdMorphSpec  # noqa: E402
from tests.unit.test_maya_morph_authoring import FakeAdapter, FakeRegistry  # noqa: E402


def _morph(
    *,
    morph_type: str = "bone",
    offsets: tuple[dict[str, object], ...] = (),
    name: str = "Morph",
    index: int = 0,
    binding: str = "morph",
) -> MmdMorphSpec:
    return MmdMorphSpec(
        name=name,
        name_english=name,
        index=index,
        panel=4,
        morph_type=morph_type,
        offsets=offsets,
        binding_identity=binding,
    )


def test_classifier_rejects_topology_and_accepts_numeric_payloads() -> None:
    old = _morph(
        offsets=({"bone_index": 1, "translation": [0, 0, 0], "rotation": [0, 0, 0, 1]},)
    )
    assert classify_morph_change(old, old) == "noop"
    assert classify_morph_change(old, replace(old, name="Edited")) == "value"
    assert classify_morph_change(old, replace(old, panel=2)) == "value"
    assert classify_morph_change(
        old,
        replace(
            old,
            offsets=({"bone_index": 1, "translation": [1, 0, 0], "rotation": [0, 0, 0, 1]},),
        ),
    ) == "value"
    assert classify_morph_change(
        old,
        replace(
            old,
            offsets=({"bone_index": 2, "translation": [1, 0, 0], "rotation": [0, 0, 0, 1]},),
        ),
    ) == "structural"
    assert classify_morph_change(old, replace(old, morph_type="material", offsets=())) == "structural"
    assert classify_morph_change(old, replace(old, index=1)) == "structural"
    assert classify_morph_change(
        _morph(morph_type="group", offsets=({"morph_index": 1, "morph_rate": 0.5},)),
        _morph(morph_type="group", offsets=({"morph_index": 1, "morph_rate": 0.8},)),
    ) == "structural"


def test_selected_adapter_writes_only_changed_morph_attrs() -> None:
    adapter = FakeAdapter()
    adapter.types["morph"] = "network"
    adapter.attrs.update(
        {
            ("morph", "mmd_morph_index"): 0,
            ("morph", "mmd_morph_type"): "bone",
            ("morph", "mmd_morph_name"): "Morph",
            ("morph", "mmd_morph_name_en"): "Morph",
            ("morph", "mmd_morph_panel"): 4,
            ("morph", "mmd_bone_morph_offsets_raw_json"): "[{\"bone_index\":1,\"translation\":[0,0,0],\"rotation\":[0,0,0,1]}]",
        }
    )
    old = _morph(offsets=({"bone_index": 1, "translation": [0, 0, 0], "rotation": [0, 0, 0, 1]},))
    new = replace(old, name="Edited", panel=2)
    apply_morph_value_patch("|Model", old, new, adapter, registry_api=FakeRegistry(["morph"]))
    written = {
        call[1][0].rsplit(".", 1)[1]
        for call in adapter.calls
        if call[0] == "set_attr"
    }
    assert written == {"mmd_morph_name", "mmd_morph_panel"}


def test_selected_adapter_updates_only_matching_runtime_target_slots() -> None:
    adapter = FakeAdapter()
    adapter.ls = lambda node=None, **kwargs: (  # type: ignore[method-assign]
        ["|Model"]
        if node in {"|Model", "Model"}
        else [node]
        if node in adapter.types and not kwargs.get("type")
        else [
            item
            for item, kind in adapter.types.items()
            if kwargs.get("type") in {None, kind}
        ]
    )
    adapter.types.update({"morph": "network", "accum": "mmdBoneMorphAccum", "joint": "joint"})
    adapter.all_node_types = lambda: ["network", "mmdBoneMorphAccum"]  # type: ignore[method-assign]
    adapter.attrs.update(
        {
            ("morph", "mmd_morph_index"): 0,
            ("morph", "mmd_morph_type"): "bone",
            ("morph", "mmd_morph_name"): "Morph",
            ("morph", "mmd_morph_name_en"): "Morph",
            ("morph", "mmd_morph_panel"): 4,
            ("morph", "mmd_bone_morph_offsets_raw_json"): "[{\"bone_index\":0,\"translation\":[0,0,0],\"rotation\":[0,0,0,1]}]",
            ("accum", "contribution"): [0],
            ("accum", "mmd_target_joint"): "joint",
            ("joint", "mmd_bone_index"): 0,
        }
    )
    adapter.connections["accum.contribution[0].weight"] = ["morph.weight"]
    old = _morph(offsets=({"bone_index": 0, "translation": [0, 0, 0], "rotation": [0, 0, 0, 1]},))
    new = replace(old, offsets=({"bone_index": 0, "translation": [1, 0, 0], "rotation": [0, 0, 0, 1]},))
    apply_morph_value_patch("|Model", old, new, adapter, registry_api=FakeRegistry(["morph"]))
    assert any("accum.contribution[0].translateOffset" in call[1][0] for call in adapter.calls)
    assert not any("accum.contribution[1]" in call[1][0] for call in adapter.calls)


def test_selected_adapter_rejects_unowned_binding_without_registry_creation() -> None:
    adapter = FakeAdapter()
    adapter.types["morph"] = "network"
    old = _morph()
    registry = FakeRegistry([])
    with pytest.raises(Exception, match="not registry-owned"):
        apply_morph_value_patch("|Model", old, replace(old, name="Edited"), adapter, registry_api=registry)


def test_selected_runtime_target_count_mismatch_fails_closed() -> None:
    adapter = FakeAdapter()
    adapter.ls = lambda node=None, **kwargs: (  # type: ignore[method-assign]
        ["|Model"]
        if node in {"|Model", "Model"}
        else [node]
        if node in adapter.types and not kwargs.get("type")
        else [
            item
            for item, kind in adapter.types.items()
            if kwargs.get("type") in {None, kind}
        ]
    )
    adapter.types.update({"morph": "network", "accum": "mmdBoneMorphAccum", "joint": "joint"})
    adapter.all_node_types = lambda: ["network", "mmdBoneMorphAccum"]  # type: ignore[method-assign]
    adapter.attrs.update(
        {
            ("morph", "mmd_morph_index"): 0,
            ("morph", "mmd_morph_type"): "bone",
            ("morph", "mmd_morph_name"): "Morph",
            ("morph", "mmd_morph_name_en"): "Morph",
            ("morph", "mmd_morph_panel"): 4,
            ("morph", "mmd_bone_morph_offsets_raw_json"): "[{\"bone_index\":0,\"translation\":[0,0,0],\"rotation\":[0,0,0,1]}]",
            ("accum", "contribution"): [0, 1],
            ("accum", "mmd_target_joint"): "joint",
            ("joint", "mmd_bone_index"): 0,
        }
    )
    adapter.connections["accum.contribution[0].weight"] = ["morph.weight"]
    adapter.connections["accum.contribution[1].weight"] = ["morph.weight"]
    old = _morph(offsets=({"bone_index": 0, "translation": [0, 0, 0], "rotation": [0, 0, 0, 1]},))
    new = replace(old, offsets=({"bone_index": 0, "translation": [1, 0, 0], "rotation": [0, 0, 0, 1]},))
    with pytest.raises(Exception, match="count mismatch"):
        apply_morph_value_patch("|Model", old, new, adapter, registry_api=FakeRegistry(["morph"]))


def test_material_runtime_patch_filters_target_and_all_material_offsets() -> None:
    adapter = FakeAdapter()
    adapter.ls = lambda node=None, **kwargs: (  # type: ignore[method-assign]
        ["|Model"]
        if node in {"|Model", "Model"}
        else [node]
        if node in adapter.types and not kwargs.get("type")
        else [item for item, kind in adapter.types.items() if kwargs.get("type") in {None, kind}]
    )
    adapter.types.update(
        {"morph": "network", "eval": "mmdMaterialMorphEval", "shader": "standardSurface"}
    )
    adapter.all_node_types = lambda: ["network", "mmdMaterialMorphEval"]  # type: ignore[method-assign]
    adapter.attrs.update(
        {
            ("morph", "mmd_morph_index"): 0,
            ("morph", "mmd_morph_type"): "material",
            ("morph", "mmd_morph_name"): "Morph",
            ("morph", "mmd_morph_name_en"): "Morph",
            ("morph", "mmd_morph_panel"): 4,
            ("morph", "mmd_material_morph_offsets_json"): "[]",
            ("eval", "contribution"): [0, 1],
            ("eval", "mmd_target_shader"): "shader",
            ("shader", "mmd_material_index"): 2,
        }
    )
    adapter.connections["eval.contribution[0].weight"] = ["morph.weight"]
    adapter.connections["eval.contribution[1].weight"] = ["morph.weight"]
    offset = {
        "material_index": -1,
        "operation_type": 1,
        "diffuse": [0.2, 0.3, 0.4, 0.5],
        "specular": [0.1, 0.2, 0.3],
        "specular_coefficient": 0.4,
        "ambient": [0.1, 0.2, 0.3],
        "edge_color": [0.1, 0.2, 0.3, 0.4],
        "edge_size": 0.5,
        "texture_factor": [0.1, 0.2, 0.3, 0.4],
        "sphere_texture_factor": [0.1, 0.2, 0.3, 0.4],
        "toon_texture_factor": [0.1, 0.2, 0.3, 0.4],
    }
    targeted_offset = dict(offset)
    targeted_offset["material_index"] = 2
    old = _morph(morph_type="material", offsets=(offset, targeted_offset))
    changed_offset = dict(offset)
    changed_offset["diffuse"] = [0.3, 0.3, 0.4, 0.5]
    changed_targeted = dict(targeted_offset)
    changed_targeted["diffuse"] = [0.4, 0.3, 0.4, 0.5]
    new = replace(old, offsets=(changed_offset, changed_targeted))
    apply_morph_value_patch("|Model", old, new, adapter, registry_api=FakeRegistry(["morph"]))
    assert sum(
        1
        for call in adapter.calls
        if call[0] == "set_attr" and "eval.contribution" in call[1][0]
    ) >= 2


def test_vertex_runtime_patch_writes_exact_selected_target() -> None:
    adapter = FakeAdapter()
    adapter.types.update({"morph": "network", "bs": "blendShape", "mesh": "mesh"})
    adapter.attrs.update(
        {
            ("morph", "mmd_morph_index"): 0,
            ("morph", "mmd_morph_type"): "vertex",
            ("morph", "mmd_morph_name"): "Morph",
            ("morph", "mmd_morph_name_en"): "Morph",
            ("morph", "mmd_morph_panel"): 4,
            ("morph", "mmd_vertex_morph_offsets_raw_json"): "[{\"vertex_index\":0,\"position_offset\":[0,0,0]}]",
            ("|Model", "mmd_import_scale"): 1.0,
            ("bs", "mmd_blendshape_morph_names_json"): '{"3":{"name":"Morph","index":0}}',
            ("mesh", "vertexCount"): 1,
        }
    )
    adapter.connections["|Model.mmd_morph_controller"] = ["controller"]
    adapter.types["controller"] = "mmdMorphController"
    adapter.connections["controller.outputWeight[0]"] = ["bs.weight[3]"]
    adapter.attrs[("bs", "geometry")] = ["mesh"]
    adapter.attrs[("bs", "geometryIndices")] = [0]
    old = _morph(
        morph_type="vertex",
        offsets=({"vertex_index": 0, "position_offset": [0, 0, 0]},),
    )
    new = replace(old, offsets=({"vertex_index": 0, "position_offset": [1, 2, 3]},))
    apply_morph_value_patch("|Model", old, new, adapter, registry_api=FakeRegistry(["morph"]))
    paths = {call[1][0] for call in adapter.calls if call[0] == "set_attr"}
    assert any(path.endswith("inputComponentsTarget") for path in paths)
    assert any(path.endswith("inputPointsTarget") for path in paths)


def test_vertex_name_only_patch_updates_alias_and_mapping() -> None:
    adapter = FakeAdapter()
    adapter.types.update({"morph": "network", "bs": "blendShape", "mesh": "mesh"})
    adapter.attrs.update(
        {
            ("morph", "mmd_morph_index"): 0,
            ("morph", "mmd_morph_type"): "vertex",
            ("morph", "mmd_morph_name"): "Morph",
            ("morph", "mmd_morph_name_en"): "Morph",
            ("morph", "mmd_morph_panel"): 4,
            ("|Model", "mmd_import_scale"): 1.0,
            ("bs", "mmd_blendshape_morph_names_json"): '{"3":{"name":"Morph","index":0}}',
            ("bs", "geometry"): ["mesh"],
            ("bs", "geometryIndices"): [0],
            ("mesh", "vertexCount"): 1,
        }
    )
    adapter.aliases["bs.weight[3]"] = "Morph"
    adapter.connections["|Model.mmd_morph_controller"] = ["controller"]
    adapter.types["controller"] = "mmdMorphController"
    adapter.connections["controller.outputWeight[0]"] = ["bs.weight[3]"]
    old = _morph(
        morph_type="vertex",
        offsets=({"vertex_index": 0, "position_offset": [0, 0, 0]},),
    )
    new = replace(old, name="Edited")

    apply_morph_value_patch("|Model", old, new, adapter, registry_api=FakeRegistry(["morph"]))

    assert adapter.aliases["bs.weight[3]"] == "Edited"
    assert adapter.attrs[("bs", "mmd_blendshape_morph_names_json")] == '{"3":{"name":"Edited","index":0}}'


def test_bone_runtime_patch_reuses_bind_orientation_converter(monkeypatch) -> None:
    import mmd_tools.converters.bone_morph_runtime as runtime

    adapter = FakeAdapter()
    adapter.ls = lambda node=None, **kwargs: (  # type: ignore[method-assign]
        ["|Model"]
        if node in {"|Model", "Model"}
        else [node]
        if node in adapter.types and not kwargs.get("type")
        else [item for item, kind in adapter.types.items() if kwargs.get("type") in {None, kind}]
    )
    adapter.types.update({"morph": "network", "accum": "mmdBoneMorphAccum", "joint": "joint"})
    adapter.all_node_types = lambda: ["network", "mmdBoneMorphAccum"]  # type: ignore[method-assign]
    adapter.attrs.update(
        {
            ("morph", "mmd_morph_index"): 0,
            ("morph", "mmd_morph_type"): "bone",
            ("morph", "mmd_morph_name"): "Morph",
            ("morph", "mmd_morph_name_en"): "Morph",
            ("morph", "mmd_morph_panel"): 4,
            ("morph", "mmd_bone_morph_offsets_raw_json"): "[{\"bone_index\":0,\"translation\":[0,0,0],\"rotation\":[0,0,0,1]}]",
            ("accum", "contribution"): [0],
            ("accum", "mmd_target_joint"): "joint",
            ("joint", "mmd_bone_index"): 0,
        }
    )
    adapter.connections["accum.contribution[0].weight"] = ["morph.weight"]
    calls = []
    monkeypatch.setattr(
        runtime,
        "pmx_bone_offset_to_runtime_values",
        lambda translation, rotation, joint: (calls.append((translation, rotation, joint)) or ((1, 2, 3), (4, 5, 6, 7))),
    )
    old = _morph(offsets=({"bone_index": 0, "translation": [0, 0, 0], "rotation": [0, 0, 0, 1]},))
    new = replace(old, offsets=({"bone_index": 0, "translation": [1, 0, 0], "rotation": [0, 0, 0, 1]},))
    apply_morph_value_patch("|Model", old, new, adapter, registry_api=FakeRegistry(["morph"]))
    assert calls == [((1.0, 0.0, 0.0), (0.0, 0.0, 0.0, 1.0), "joint")]
    assert any(call[1][1:5] == (4, 5, 6, 7) for call in adapter.calls if call[0] == "set_attr")


def test_backend_selected_preimage_mismatch_rolls_back_and_ownership_is_narrow() -> None:
    from tests.unit.test_maya_scene_metadata_backend import _backend, _morph as make_scene_morph, _registry

    cmds, backend = _backend()
    make_scene_morph(
        cmds,
        "morph",
        "bone",
        [{"bone_index": 0, "translation": [0, 0, 0], "rotation": [0, 0, 0, 1]}],
    )
    _registry(cmds, morph_members=["morph"])
    old = backend.read_morph_value("|root", "morph", 0)
    new = replace(old, name="Edited")
    backend.begin_morph_value_patch("|root", "morph", old, new)
    cmds.set_attr("morph.mmd_morph_name", "Tampered", type="string")
    with pytest.raises(Exception, match="fingerprint mismatch"):
        backend.commit_morph_value_patch("|root", "morph", new)
    backend.rollback_write("|root")
    assert cmds.attrs[("morph", "mmd_morph_name")] == old.name
    cmds.connections[("registry.morphMembers", None)] = []
    with pytest.raises(Exception, match="not owned"):
        backend.read_morph_value("|root", "morph", 0)


def test_coordinator_morph_patch_is_selected_only_and_rolls_back() -> None:
    from tests.unit.test_maya_model_authoring_coordinator import _coordinator

    coordinator, backend, _materials, _bones = _coordinator()
    previous = _morph(binding="morph0")
    backend.scene = replace(backend.scene, morphs=(previous,))
    coordinator._metadata.read_spec = lambda _root: (_ for _ in ()).throw(AssertionError("full read forbidden"))
    coordinator._metadata.read_morph_value = lambda _root, _binding, _index: previous
    def begin_morph(*_args):
        backend.active = True
        backend.snapshot = backend.scene
        backend.events.append("begin:morph_value")

    coordinator._backend.begin_morph_value_patch = begin_morph
    coordinator._metadata.commit_morph_value_patch = lambda *_args: backend.events.append("commit:morph_value")
    coordinator._morphs.apply_morph_value_patch = (  # type: ignore[attr-defined]
        lambda _root, _old, new, _adapter: new
    )
    result = coordinator.apply_morph_value_patch("|root", replace(previous, name="Edited"))
    assert result.name == "Edited"
    assert backend.events == ["begin:morph_value", "commit:morph_value"]


def test_coordinator_morph_noop_skips_transaction_and_failure_rolls_back() -> None:
    from tests.unit.test_maya_model_authoring_coordinator import _coordinator

    coordinator, backend, _materials, _bones = _coordinator()
    previous = _morph(binding="morph0")
    backend.scene = replace(backend.scene, morphs=(previous,))
    coordinator._metadata.read_morph_value = lambda _root, _binding, _index: previous
    def begin_morph(*_args):
        backend.active = True
        backend.snapshot = backend.scene
        backend.events.append("begin:morph_value")

    coordinator._backend.begin_morph_value_patch = begin_morph
    coordinator._metadata.commit_morph_value_patch = lambda *_args: (_ for _ in ()).throw(RuntimeError("mismatch"))
    coordinator._morphs.apply_morph_value_patch = (  # type: ignore[attr-defined]
        lambda _root, _old, new, _adapter: new
    )
    assert coordinator.apply_morph_value_patch("|root", previous) == previous
    assert backend.events == []
    with pytest.raises(Exception, match="apply_morph_value_patch failed"):
        coordinator.apply_morph_value_patch("|root", replace(previous, name="Edited"))
    assert backend.events == ["begin:morph_value", "rollback"]


def test_presenter_narrow_apply_updates_selected_row_without_list_reload() -> None:
    from tests.unit.test_morph_authoring_ui import _presenter
    from tests.unit.test_morph_presenter_headless import _FakeItem

    presenter, view, _state, _adapter, coordinator = _presenter()
    prior = coordinator.spec.morphs[0]
    coordinator.read_morph_value = lambda _root, _index, _binding: prior
    coordinator.apply_morph_value_patch = lambda _root, value: value
    item = _FakeItem("0:V|Morph", key="key")
    item.setText = lambda text: setattr(item, "_text", text)  # type: ignore[attr-defined]
    view.morph_list.items = [item]
    view.morph_name_jp_edit.setText("Edited")
    presenter.load_morphs = lambda: (_ for _ in ()).throw(AssertionError("full list reload forbidden"))
    presenter.apply_changes()
    assert item.text().find("Edited") >= 0


def test_presenter_has_no_raw_offset_edit_route() -> None:
    from tests.unit.test_morph_authoring_ui import _presenter

    presenter, _view, _state, _adapter, _coordinator = _presenter(
        "group", ({"morph_index": 1, "morph_rate": 0.5},)
    )
    assert not hasattr(presenter, "apply_offsets")
