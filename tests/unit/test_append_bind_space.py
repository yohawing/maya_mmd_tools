"""Regression tests for native mmdAppend bind-space wiring."""

from unittest.mock import call, patch

from tests.common.maya_stub import install_headless_ui_stubs

install_headless_ui_stubs()

from mmd_tools.converters import rig_converter  # noqa: E402
from mmd_tools.converters.rig_converter import RigConverter  # noqa: E402


def test_configure_append_bind_space_captures_target_and_joint_parent():
    identity = [
        1.0, 0.0, 0.0, 0.0,
        0.0, 1.0, 0.0, 0.0,
        0.0, 0.0, 1.0, 0.0,
        0.0, 0.0, 0.0, 1.0,
    ]
    target_bind = identity.copy()
    target_bind[12:15] = [1.0, 2.0, 3.0]
    parent_bind = identity.copy()
    parent_bind[12:15] = [4.0, 5.0, 6.0]

    def get_attr(plug):
        return target_bind if plug == "target.worldMatrix[0]" else parent_bind

    with patch.object(rig_converter.cmds, "attributeQuery", return_value=True), patch.object(
        rig_converter.cmds, "getAttr", side_effect=get_attr
    ), patch.object(
        rig_converter.cmds, "listRelatives", return_value=["parent"]
    ), patch.object(
        rig_converter.cmds, "nodeType", return_value="joint"
    ), patch.object(rig_converter.cmds, "setAttr") as set_attr:
        RigConverter._configure_append_bind_space("append", "target")

    set_attr.assert_has_calls(
        [
            call("append.targetMayaBindWorldMatrix", *target_bind, type="matrix"),
            call("append.targetNoOrientBindWorldMatrix", *identity[:12], 1.0, 2.0, 3.0, 1.0, type="matrix"),
            call("append.parentMayaBindWorldMatrix", *parent_bind, type="matrix"),
            call("append.parentNoOrientBindWorldMatrix", *identity[:12], 4.0, 5.0, 6.0, 1.0, type="matrix"),
            call("append.useTargetBindMatrices", True),
        ]
    )
