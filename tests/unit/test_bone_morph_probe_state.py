"""Bone morph availability probe scene-state regression tests."""

from unittest import mock

from tests.common.maya_stub import install_maya_stub

install_maya_stub()

from mmd_tools.converters import bone_morph_runtime  # noqa: E402


def test_probe_restores_clean_scene_only_after_successful_cleanup():
    cmds = mock.Mock()
    cmds.file.side_effect = lambda **kwargs: False if kwargs.get("query") else None
    cmds.undoInfo.side_effect = lambda **kwargs: True if kwargs.get("query") else None
    cmds.createNode.return_value = bone_morph_runtime._PROBE_NODE_NAME
    cmds.nodeType.return_value = "unknown"
    cmds.objExists.side_effect = [True, False]

    with mock.patch.object(bone_morph_runtime, "cmds", cmds):
        bone_morph_runtime.probe_bone_morph_accum_availability()

    assert mock.call(modified=False) in cmds.file.call_args_list
    assert mock.call(stateWithoutFlush=False) in cmds.undoInfo.call_args_list
    assert mock.call(stateWithoutFlush=True) in cmds.undoInfo.call_args_list


def test_probe_keeps_scene_dirty_when_temporary_node_cleanup_fails():
    cmds = mock.Mock()
    cmds.file.side_effect = lambda **kwargs: False if kwargs.get("query") else None
    cmds.undoInfo.side_effect = lambda **kwargs: True if kwargs.get("query") else None
    cmds.createNode.return_value = bone_morph_runtime._PROBE_NODE_NAME
    cmds.nodeType.return_value = "unknown"
    cmds.objExists.return_value = True
    cmds.delete.side_effect = RuntimeError("delete failed")

    with mock.patch.object(bone_morph_runtime, "cmds", cmds):
        bone_morph_runtime.probe_bone_morph_accum_availability()

    assert mock.call(modified=False) not in cmds.file.call_args_list


def test_probe_does_not_restore_clean_state_when_create_raises_after_side_effect():
    cmds = mock.Mock()
    cmds.file.side_effect = lambda **kwargs: False if kwargs.get("query") else None
    cmds.undoInfo.side_effect = lambda **kwargs: True if kwargs.get("query") else None
    cmds.createNode.side_effect = RuntimeError("creation failed after mutation")
    cmds.objExists.return_value = False

    with mock.patch.object(bone_morph_runtime, "cmds", cmds):
        bone_morph_runtime.probe_bone_morph_accum_availability()

    assert mock.call(modified=False) not in cmds.file.call_args_list
