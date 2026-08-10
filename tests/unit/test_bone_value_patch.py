"""Focused contracts for selected-bone value patch routing."""

from dataclasses import replace

import pytest

from mmd_tools.adapters.maya_bone_authoring import apply_bone_value_patch
from mmd_tools.core.bone_authoring import classify_bone_change
from mmd_tools.core.model_authoring_spec import MmdBoneSpec
from mmd_tools.core.pmx_data.bone import PmxBoneFlag
from tests.unit.test_maya_model_authoring_coordinator import _coordinator
from tests.unit.test_maya_scene_metadata_backend import _backend, _bone


def test_classifier_is_explicit_about_value_structural_and_mixed_edits() -> None:
    old = MmdBoneSpec("bone", index=0, binding_identity="|root|bone")
    assert classify_bone_change(old, old) == "noop"
    assert classify_bone_change(old, replace(old, name="edited")) == "value"
    assert classify_bone_change(old, replace(old, flags=int(PmxBoneFlag.DISPLAY))) == "value"
    assert classify_bone_change(old, replace(old, parent_index=2)) == "structural"
    assert classify_bone_change(old, replace(old, grant_parent_index=2)) == "structural"
    assert classify_bone_change(
        old,
        replace(old, name="edited", parent_index=2),
    ) == "structural"


def test_selected_adapter_writes_only_changed_name_axis_and_rest_attributes() -> None:
    cmds, backend = _backend()
    _bone(cmds, "|root|bone", 0, int(PmxBoneFlag.AXIS_FIXED))
    cmds.attrs[("|root|bone", "mmd_fixed_axis")] = [(1.0, 0.0, 0.0)]
    cmds.attrs[("|root|bone", "mmd_axis_direction")] = [(1.0, 0.0, 0.0)]
    old = backend.read_bone_value("|root", "|root|bone", 0)
    new = replace(
        old,
        name="edited",
        fixed_axis=(0.0, 1.0, 0.0),
        rest_position=(1.0, 2.0, 3.0),
    )
    cmds.write_history.clear()
    apply_bone_value_patch("|root", old, new, cmds)
    assert set(cmds.write_history) == {
        "|root|bone.mmd_bone_name",
        "|root|bone.mmd_fixed_axis",
        "|root|bone.mmd_axis_direction",
        "|root|bone.mmd_pmx_rest_position",
    }


def test_selected_reader_ignores_default_axis_attrs_when_flags_are_off() -> None:
    cmds, backend = _backend()
    _bone(cmds, "|root|bone", 0, 0)
    selected = backend.read_bone_value("|root", "|root|bone", 0)
    assert selected.fixed_axis is None
    assert selected.local_axis_x is None
    assert selected.local_axis_z is None


def test_coordinator_narrow_path_does_not_call_full_read_or_metadata_hooks() -> None:
    coordinator, backend, _materials, _bones = _coordinator()
    previous = backend.scene.bones[1]
    target = replace(previous, name="edited")

    def fail_full_read(_root: str):
        raise AssertionError("full read must not be used by selected value patch")

    coordinator._metadata.read_spec = fail_full_read  # type: ignore[method-assign]
    result = coordinator.apply_bone_value_patch("|root", target)

    assert result.name == "edited"
    assert backend.events == ["begin:bone_value", "commit:bone_value"]


def test_coordinator_narrow_failure_rolls_back_without_full_read() -> None:
    coordinator, backend, _materials, _bones = _coordinator()
    previous = backend.scene.bones[1]
    coordinator._metadata.commit_bone_value_patch = (  # type: ignore[method-assign]
        lambda *_args: (_ for _ in ()).throw(RuntimeError("fingerprint mismatch"))
    )
    with pytest.raises(Exception, match="apply_bone_value_patch failed"):
        coordinator.apply_bone_value_patch("|root", replace(previous, name="edited"))
    assert backend.events == ["begin:bone_value", "rollback"]


def test_coordinator_noop_does_not_open_narrow_transaction() -> None:
    coordinator, backend, _materials, _bones = _coordinator()
    previous = backend.scene.bones[1]
    assert coordinator.apply_bone_value_patch("|root", previous) == previous
    assert backend.events == []


def test_backend_commit_mismatch_rolls_back_selected_preimage() -> None:
    cmds, backend = _backend()
    _bone(cmds, "|root|bone", 0, 0)
    old = backend.read_bone_value("|root", "|root|bone", 0)
    new = replace(old, name="edited")
    backend.begin_bone_value_patch("|root", "|root|bone", old, new)
    apply_bone_value_patch("|root", old, new, cmds)
    cmds.attrs[("|root|bone", "mmd_bone_name")] = "tampered"
    with pytest.raises(Exception, match="fingerprint mismatch"):
        backend.commit_bone_value_patch("|root", "|root|bone", new)
    backend.rollback_write("|root")
    assert cmds.attrs[("|root|bone", "mmd_bone_name")] == "bone0"


def test_capture_rest_uses_selected_reader_writer_without_full_read() -> None:
    coordinator, backend, _materials, bones = _coordinator()
    coordinator._metadata.read_spec = lambda _root: (_ for _ in ()).throw(  # type: ignore[method-assign]
        AssertionError("capture_rest must not full-read")
    )
    result = coordinator.capture_rest("|root", 1, "|root|spare")
    assert result.rest_position == (2.0, 3.0, 4.0)
    assert bones.events == ["capture", "patch"]
    assert backend.events == ["begin:bone_value", "commit:bone_value"]
