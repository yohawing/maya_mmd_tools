"""Startup capability scans tolerate legacy and partially generated plugs."""

from unittest.mock import patch

from tests.common.maya_stub import install_headless_ui_stubs

install_headless_ui_stubs()

from mmd_tools.ui.presenters.physics_presenter import _resolve_message_name  # noqa: E402


def test_missing_physics_message_attribute_is_unbound_without_querying_connections():
    with (
        patch("mmd_tools.ui.presenters.physics_presenter.cmds.objExists", return_value=False),
        patch("mmd_tools.ui.presenters.physics_presenter.cmds.listConnections") as connections,
    ):
        assert _resolve_message_name("legacyRigidShape", "relatedBone") == ""

    connections.assert_not_called()


def test_invalid_physics_message_plug_is_unbound_during_startup_scan():
    with (
        patch("mmd_tools.ui.presenters.physics_presenter.cmds.objExists", return_value=True),
        patch(
            "mmd_tools.ui.presenters.physics_presenter.cmds.listConnections",
            side_effect=RuntimeError("No object matches name"),
        ),
    ):
        assert _resolve_message_name("partialRigidShape", "relatedBone") == ""
