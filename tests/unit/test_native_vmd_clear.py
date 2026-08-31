"""Contracts for the optional native VMD curve-clear path."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from tests.common.maya_stub import install_maya_stub

install_maya_stub()

from mmd_tools.adapters.native_vmd_clear import (  # noqa: E402
    COMMAND_MMD_VMD_CLEAR_CURVES,
    NativeVmdClearAdapter,
    NativeVmdClearMutationError,
    NativeVmdClearPrepareError,
    NativeVmdClearProtocolError,
    NativeVmdClearTransportError,
    NativeVmdClearUnavailableError,
    NativeVmdClearUnsupportedError,
)
from mmd_tools.converters import vmd_import_state as import_state  # noqa: E402
from mmd_tools.converters.vmd_context import VmdImportStateContext  # noqa: E402


def _result(
    plugs=("|model|controller.inputWeight[0]",),
    *,
    ok=True,
    phase="complete",
    mutated=True,
    reason="",
    curve_count=None,
    removed_count=None,
):
    if curve_count is None:
        curve_count = len(plugs) if ok else 0
    if removed_count is None:
        removed_count = 3 * len(plugs) if ok else 0
    return json.dumps(
        {
            "version": 1,
            "command": COMMAND_MMD_VMD_CLEAR_CURVES,
            "ok": ok,
            "phase": phase,
            "mutated": mutated,
            "plugs": [{"plug": plug, "removed_count": 3 if ok else 0} for plug in plugs],
            "curve_count": curve_count,
            "removed_count": removed_count,
            "reason": reason,
        },
        ensure_ascii=False,
    )


class _Cmds:
    def __init__(self, result=None, error=None):
        self.calls = []
        self.result = result
        self.error = error

    def mmdVmdClearCurves(self, payload=None):
        self.calls.append(json.loads(payload))
        if self.error is not None:
            raise self.error
        return self.result


def test_adapter_sends_one_versioned_batch_with_canonical_plugs():
    plugs = ["|model|joint.translateX", "|model|controller.inputWeight[0]"]
    cmds = _Cmds(_result(plugs))

    result = NativeVmdClearAdapter(cmds).clear(plugs)

    assert result["removed_count"] == 6
    assert cmds.calls == [{"version": 1, "plugs": plugs}]


@pytest.mark.parametrize(
    "raw_result",
    [
        "not-json",
        json.dumps({"version": 2}),
        json.dumps(
            {
                "version": 1,
                "command": COMMAND_MMD_VMD_CLEAR_CURVES,
                "ok": True,
                "phase": "complete",
                "mutated": True,
                "plugs": [{"plug": "x.a", "removed_count": 1, "curve_count": 1}],
                "curve_count": 1,
                "removed_count": 1,
                "reason": "",
            }
        ),
        json.dumps(
            {
                "version": 1,
                "command": COMMAND_MMD_VMD_CLEAR_CURVES,
                "ok": False,
                "phase": "prepare",
                "mutated": False,
                "plugs": [{"plug": "x.a", "removed_count": 1}],
                "curve_count": 0,
                "removed_count": 0,
                "reason": "bad plug",
            }
        ),
        json.dumps(
            {
                "version": 1,
                "command": COMMAND_MMD_VMD_CLEAR_CURVES,
                "ok": False,
                "phase": "mutation",
                "mutated": True,
                "plugs": [{"plug": "x.a", "removed_count": 1}],
                "curve_count": 1,
                "removed_count": 1,
                "reason": "curve remove failed",
            }
        ),
        json.dumps(
            {
                "version": 1,
                "command": COMMAND_MMD_VMD_CLEAR_CURVES,
                "ok": True,
                "phase": "complete",
                "mutated": True,
                "plugs": [{"plug": "different.a", "removed_count": 1}],
                "curve_count": 1,
                "removed_count": 1,
                "reason": "",
            }
        ),
        _result(["x.a"], phase="prepare"),
        _result(["x.a"], reason="unexpected"),
        _result(["x.a"], mutated=False),
        _result(["x.a"], removed_count=2),
        _result(
            ["x.a"],
            ok=False,
            phase="complete",
            mutated=False,
            reason="bad phase",
        ),
        '{"version":1,"version":1,"command":"mmdVmdClearCurves",'
        '"ok":true,"phase":"complete","mutated":true,"plugs":[],'
        '"curve_count":0,"removed_count":0,"reason":""}',
    ],
)
def test_adapter_rejects_malformed_result(raw_result):
    with pytest.raises(NativeVmdClearProtocolError):
        NativeVmdClearAdapter(_Cmds(raw_result)).clear(["x.a"])


def test_adapter_rejects_duplicate_and_noncanonical_request_plugs():
    adapter = NativeVmdClearAdapter(_Cmds(_result(["x.a"])))

    with pytest.raises(ValueError):
        adapter.clear(["x.a", "x.a"])
    with pytest.raises(ValueError):
        adapter.clear(["x"])


def test_unavailable_command_is_reported_without_invocation():
    class NoCommand:
        pass

    adapter = NativeVmdClearAdapter(NoCommand())
    with patch(
        "mmd_tools.core.cpp_plugin_locator.running_maya_major_version",
        return_value="2024",
    ), patch(
        "mmd_tools.core.cpp_plugin_locator.plugin_candidate_paths",
        return_value=[],
    ):
        with pytest.raises(NativeVmdClearUnavailableError):
            adapter.clear(["x.a"])


def test_plugin_is_loaded_through_the_shared_locator_once():
    class LoadableCmds:
        pass

    cmds = LoadableCmds()
    plugin_path = "F:/native/plug-ins/2024/Release/mmd_tools_cpp.mll"

    def register(_path, command_cmds, **_kwargs):
        command_cmds.mmdVmdClearCurves = lambda **_payload: _result(["x.a"])
        return True

    adapter = NativeVmdClearAdapter(cmds)
    with patch(
        "mmd_tools.core.cpp_plugin_locator.running_maya_major_version",
        return_value="2024",
    ), patch(
        "mmd_tools.core.cpp_plugin_locator.plugin_candidate_paths",
        return_value=[plugin_path],
    ), patch(
        "mmd_tools.core.cpp_plugin_locator.find_plugin_path",
        return_value=plugin_path,
    ), patch(
        "mmd_tools.core.cpp_plugin_locator.prepare_plugin_directory"
    ) as prepare, patch(
        "mmd_tools.core.cpp_plugin_locator.load_plugin",
        side_effect=register,
    ) as load:
        adapter.clear(["x.a"])
        adapter.clear(["x.a"])

    prepare.assert_called_once_with(plugin_path)
    load.assert_called_once_with(plugin_path, cmds, prepare=False)


def test_prepare_unsupported_failure_is_typed_for_fallback():
    raw = _result(["x.a"], ok=False, phase="prepare", mutated=False, reason="unsupported_curve")

    with pytest.raises(NativeVmdClearUnsupportedError):
        NativeVmdClearAdapter(_Cmds(raw)).clear(["x.a"])


def test_mutation_failure_is_fatal():
    raw = _result(
        ["x.a"],
        ok=False,
        phase="mutation",
        mutated=True,
        reason="curve_remove_failed",
        curve_count=1,
        removed_count=2,
    )

    with pytest.raises(NativeVmdClearMutationError):
        NativeVmdClearAdapter(_Cmds(raw)).clear(["x.a"])


def test_transport_failure_is_fatal():
    with pytest.raises(NativeVmdClearTransportError):
        NativeVmdClearAdapter(_Cmds(error=RuntimeError("transport"))).clear(["x.a"])


def _clear_context():
    return VmdImportStateContext(
        logger=MagicMock(),
        bone_name_mapping={"center": "joint"},
        bone_bind_poses={},
        morph_name_mapping={},
        collect_append_info=lambda: {},
        iter_morph_mappings=lambda _entry: [],
        set_refresh_suspended=lambda _value: None,
    )


def _clear_patches(cut_keyable_attrs):
    def fake_cut(node, attrs, **kwargs):
        native_plugs = kwargs.get("native_plugs")
        if native_plugs is not None:
            for attribute in attrs:
                native_plugs.add(f"{node}.{attribute}")
        return 1

    if cut_keyable_attrs is None:
        cut_keyable_attrs = MagicMock()
    if cut_keyable_attrs.side_effect is None:
        cut_keyable_attrs.side_effect = fake_cut
    return (
        patch.object(import_state, "root_owned_joints", return_value={"joint"}),
        patch.object(import_state, "_capture_fallback_rest_translates", return_value={}),
        patch.object(import_state, "delete_vmd_rotation_time_curves_for_controls", return_value=[]),
        patch.object(
            import_state,
            "collect_clearable_authoring_attrs",
            return_value={"physical": {"input"}},
        ),
        patch.object(import_state, "read_mmd_control_rig_metadata", return_value=None),
        patch.object(import_state, "_ls_mmd_ccd_ik_nodes", return_value=[]),
        patch.object(import_state, "_restore_joints_to_rest"),
        patch.object(import_state, "_anim_layer_is_exclusively_owned_by", return_value=True),
        patch.object(import_state, "_anim_layer_targets_morph_controller", return_value=False),
        patch.object(import_state.cmds, "objExists", return_value=False),
        patch.object(import_state, "cut_keyable_attrs", side_effect=cut_keyable_attrs or fake_cut),
    )


def _run_clear_with_native(native, cut=None):
    cut = cut or MagicMock(return_value=1)
    patches = _clear_patches(cut)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7], patches[8], patches[9], patches[10], patch.object(import_state, "NativeVmdClearAdapter", return_value=native):
        import_state.clear_existing_motion(_clear_context(), "missing_layer", target_model="root")
    return cut


def test_clear_existing_motion_uses_one_native_batch_for_safe_routes():
    native = MagicMock()
    native.clear.return_value = _result(["physical.input"])
    cut = _run_clear_with_native(native)

    native.clear.assert_called_once_with(["physical.input"])
    assert all(
        call.kwargs.get("native_plugs") is not None
        for call in cut.call_args_list
        if call.args[0] == "physical"
    )


@pytest.mark.parametrize(
    "fallback_error",
    [
        NativeVmdClearUnavailableError("missing"),
        NativeVmdClearUnsupportedError("old binary"),
        NativeVmdClearPrepareError("bad plug"),
    ],
)
def test_clear_existing_motion_falls_back_only_before_native_mutation(fallback_error):
    native = MagicMock()
    native.clear.side_effect = fallback_error
    with patch.object(import_state, "_clear_animation_curve_plugs") as fallback:
        _run_clear_with_native(native)

    fallback.assert_called_once_with(["physical.input"])


@pytest.mark.parametrize(
    "fatal_error",
    [
        NativeVmdClearMutationError("partial"),
        NativeVmdClearProtocolError("malformed"),
        NativeVmdClearTransportError("transport"),
    ],
)
def test_clear_existing_motion_never_falls_back_after_native_failure(fatal_error):
    native = MagicMock()
    native.clear.side_effect = fatal_error
    with patch.object(import_state, "_clear_animation_curve_plugs") as fallback:
        with pytest.raises(type(fatal_error)):
            _run_clear_with_native(native)

    fallback.assert_not_called()


def test_control_rig_transaction_keeps_python_clear_and_skips_native():
    native = MagicMock()
    cut = MagicMock(return_value=1)
    patches = _clear_patches(cut)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7], patches[8], patches[9], patches[10], patch.object(import_state, "NativeVmdClearAdapter", return_value=native):
        import_state.clear_existing_motion(
            _clear_context(),
            "missing_layer",
            target_model="root",
            preserve_curve_nodes=True,
        )

    native.clear.assert_not_called()
    assert all("native_plugs" not in call.kwargs for call in cut.call_args_list)
