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

    assert mock.call(stateWithoutFlush=False) in cmds.undoInfo.call_args_list
    assert mock.call(stateWithoutFlush=True) in cmds.undoInfo.call_args_list
    assert mock.call(modified=False) not in cmds.file.call_args_list
    assert mock.call(modified=True) not in cmds.file.call_args_list


def test_probe_reports_unavailable_when_create_fails():
    """createNode failure is treated as node_type_unavailable, not success."""
    cmds = mock.Mock()
    cmds.createNode.side_effect = RuntimeError("Unknown object type: mmdBoneMorphAccum")
    cmds.objExists.return_value = False

    with mock.patch.object(bone_morph_runtime, "cmds", cmds):
        availability = bone_morph_runtime.probe_bone_morph_accum_availability()

    assert availability["available"] is False
    assert availability["code"] == "node_type_unavailable"
    assert availability["reason"] == "node_type_unavailable"
    assert "create_failed" in availability["detail"]
    cmds.delete.assert_not_called()


def test_probe_deletes_unknown_node_and_reports_unavailable():
    """Unknown node returned by createNode is deleted and fails soft."""
    probe_name = bone_morph_runtime._PROBE_NODE_NAME
    cmds = mock.Mock()
    cmds.createNode.return_value = probe_name
    cmds.nodeType.return_value = "unknown"
    cmds.objExists.return_value = True
    cmds.attributeQuery.return_value = True

    with mock.patch.object(bone_morph_runtime, "cmds", cmds):
        availability = bone_morph_runtime.probe_bone_morph_accum_availability()

    assert availability["available"] is False
    assert availability["code"] == "node_type_unavailable"
    assert availability["actual_type"] == "unknown"
    assert "unknown_or_wrong_type" in availability["detail"]
    cmds.delete.assert_called_once_with(probe_name)


def test_probe_reports_missing_required_attributes():
    """Missing required attrs fail soft and delete the temporary probe."""
    probe_name = bone_morph_runtime._PROBE_NODE_NAME
    cmds = mock.Mock()
    cmds.createNode.return_value = probe_name
    cmds.nodeType.return_value = bone_morph_runtime.ACCUM_NODE_TYPE
    cmds.objExists.return_value = True

    def attr_exists(attr, node=None, exists=False):
        return attr not in ("rotateOffsetQuat", "baseRotate", "outputRotate")

    cmds.attributeQuery.side_effect = attr_exists

    with mock.patch.object(bone_morph_runtime, "cmds", cmds):
        availability = bone_morph_runtime.probe_bone_morph_accum_availability()

    assert availability["available"] is False
    assert availability["code"] == "node_type_unavailable"
    assert availability["missing_attributes"] == [
        "rotateOffsetQuat",
        "baseRotate",
        "outputRotate",
    ]
    assert "missing_attributes" in availability["detail"]
    cmds.delete.assert_called_once_with(probe_name)


def test_create_accumulator_deletes_unknown_or_invalid_node():
    """Per-joint create rejects unknown/invalid nodes without artifacts."""
    cmds = mock.Mock()
    cmds.createNode.return_value = "joint_boneMorphAccum"
    cmds.nodeType.return_value = "unknown"
    cmds.objExists.return_value = True

    with mock.patch.object(bone_morph_runtime, "cmds", cmds):
        node = bone_morph_runtime._create_accumulator("joint")

    assert node is None
    cmds.delete.assert_called_once_with("joint_boneMorphAccum")
