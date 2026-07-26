"""Unit tests for the out-of-band HumanIK Control Rig warn-only detector.

These tests run both against the stubbed ``maya`` package (no real Maya
available) and against real ``mayapy`` (nox's ``tests`` session, where
``maya.cmds``/``maya.api.OpenMaya`` are the genuine modules). They therefore
always patch specific attributes via ``unittest.mock.patch`` rather than
relying on ``maya.cmds``/``maya.api.OpenMaya`` themselves being ``MagicMock``
instances -- ``patch`` replaces an attribute on the target module regardless
of whether the original is a stub or a real built-in function.

Callback *registration* is only exercised at the Python-call level here (did
``register_humanik_control_rig_watch`` call ``MDGMessage.addNodeAddedCallback``
with the right node type/callback?). Whether Maya's real HIK actually fires
that callback on a genuine ``HIKControlSetNode`` node creation is covered by the
``standard_ui_warning`` stage of ``tests/viewport/e2e_humanik_control_rig_cycle.py``.
The deferred handler's classification/warning logic is covered here with
fakes, independent of whether a real node-added event ever fires.
"""

import unittest
from unittest.mock import MagicMock, patch

from tests.common.maya_stub import install_maya_stub

install_maya_stub(profile="minimal")

from mmd_tools.core import humanik_control_rig_watch as watch


class FakeBinding:
    """Minimal stand-in for ``HumanIkFrontendBinding``."""

    def __init__(self, character, joints):
        self.character = character
        self.assignments = [MagicMock(joint=joint) for joint in joints]
        self.control_rig_created = False


class FakeOpenMaya:
    """Fake ``maya.api.OpenMaya`` for tests.

    Real API 2.0 classes (``MDGMessage``, ``MMessage``) are immutable
    extension types under actual ``mayapy`` -- ``unittest.mock.patch`` cannot
    monkeypatch their static methods there. ``register_humanik_control_rig_watch``/
    ``deregister_humanik_control_rig_watch`` accept an injectable ``om_module``
    for exactly this reason (see their docstrings), so tests use this fake
    instead of patching the real module.
    """

    def __init__(self):
        self.MDGMessage = MagicMock()
        self.MMessage = MagicMock()


class TestRegisterDeregister(unittest.TestCase):
    """Registration/deregistration against an injected fake OpenMaya module."""

    def setUp(self):
        watch._node_added_callback_id = None
        self.om = FakeOpenMaya()

    def tearDown(self):
        watch._node_added_callback_id = None

    def test_register_calls_add_node_added_callback_and_returns_true(self):
        self.om.MDGMessage.addNodeAddedCallback.return_value = "cb-id"

        result = watch.register_humanik_control_rig_watch(om_module=self.om)

        self.assertTrue(result)
        self.assertEqual(watch._node_added_callback_id, "cb-id")
        args, _kwargs = self.om.MDGMessage.addNodeAddedCallback.call_args
        self.assertEqual(args[0], watch._on_hik_control_set_node_added)
        # Registered against the always-present "dependNode" base type, not
        # "HIKControlSetNode" directly -- see register_humanik_control_rig_watch's
        # docstring for why (HIK's node types are not registered in the DG
        # until HIK's own plugin loads, which does not happen at mmd_tools
        # plugin-load time).
        self.assertEqual(args[1], "dependNode")

    def test_register_is_idempotent(self):
        self.om.MDGMessage.addNodeAddedCallback.return_value = "cb-id"

        self.assertTrue(watch.register_humanik_control_rig_watch(om_module=self.om))
        self.assertTrue(watch.register_humanik_control_rig_watch(om_module=self.om))
        self.om.MDGMessage.addNodeAddedCallback.assert_called_once()

    def test_register_failure_is_swallowed_and_returns_false(self):
        self.om.MDGMessage.addNodeAddedCallback.side_effect = RuntimeError("boom")
        result = watch.register_humanik_control_rig_watch(om_module=self.om)
        self.assertFalse(result)
        self.assertIsNone(watch._node_added_callback_id)

    def test_deregister_calls_remove_callback_and_is_idempotent(self):
        watch._node_added_callback_id = "cb-id"

        watch.deregister_humanik_control_rig_watch(om_module=self.om)
        self.om.MMessage.removeCallback.assert_called_once_with("cb-id")
        self.assertIsNone(watch._node_added_callback_id)

        self.om.MMessage.removeCallback.reset_mock()
        watch.deregister_humanik_control_rig_watch(om_module=self.om)
        self.om.MMessage.removeCallback.assert_not_called()


class TestHikControlSetNodeUuidFilter(unittest.TestCase):
    """``_on_hik_control_set_node_added``/``_hik_control_set_node_uuid`` must
    filter by type name themselves, since the callback is registered against
    the always-present ``dependNode`` base type (see
    ``register_humanik_control_rig_watch``'s docstring for why a
    ``HIKControlSetNode``-filtered registration is unreliable), and must
    capture a UUID rather than a name (``hikCreateControlRig()`` renames the
    node from its default name to ``"{character}_ControlRig"`` before the
    deferred handler runs -- see ``_on_hik_control_set_node_added``'s
    docstring)."""

    def _fake_om(self, type_name, uuid_string="11111111-1111-1111-1111-111111111111"):
        om = MagicMock()
        node_fn_instance = MagicMock()
        node_fn_instance.typeName = type_name
        node_fn_instance.uuid.return_value.asString.return_value = uuid_string
        om.MFnDependencyNode.return_value = node_fn_instance
        return om

    def test_matching_type_returns_uuid(self):
        om = self._fake_om("HIKControlSetNode", uuid_string="abc-123")
        result = watch._hik_control_set_node_uuid(object(), om_module=om)
        self.assertEqual(result, "abc-123")

    def test_non_matching_type_returns_none(self):
        om = self._fake_om("HIKState2SK")
        result = watch._hik_control_set_node_uuid(object(), om_module=om)
        self.assertIsNone(result)

    def test_on_node_added_schedules_deferred_only_for_matching_type(self):
        om = self._fake_om("HIKControlSetNode", uuid_string="abc-123")
        with patch("maya.utils.executeDeferred") as execute_deferred:
            watch._on_hik_control_set_node_added(object(), om_module=om)
        execute_deferred.assert_called_once_with(
            watch._handle_new_hik_control_set_node, "abc-123"
        )

    def test_on_node_added_ignores_non_matching_type(self):
        om = self._fake_om("transform")
        with patch("maya.utils.executeDeferred") as execute_deferred:
            watch._on_hik_control_set_node_added(object(), om_module=om)
        execute_deferred.assert_not_called()


class TestResolveCharacter(unittest.TestCase):
    def test_returns_first_connected_hik_character_node(self):
        cmds = MagicMock()
        cmds.listConnections.return_value = ["Character1"]
        result = watch._resolve_character_for_hik_control_set_node("HIKControlSetNode1", cmds)
        self.assertEqual(result, "Character1")
        cmds.listConnections.assert_called_once_with("HIKControlSetNode1", type="HIKCharacterNode")

    def test_returns_none_when_no_connections(self):
        cmds = MagicMock()
        cmds.listConnections.return_value = []
        self.assertIsNone(watch._resolve_character_for_hik_control_set_node("HIKControlSetNode1", cmds))


class TestHandleNewHikControlSetNode(unittest.TestCase):
    """Deferred-handler classification/warning logic, fully faked.

    The handler receives a UUID string, not a name (see
    ``TestHikControlSetNodeUuidFilter``'s docstring for why), and resolves
    the node's *current* name via ``cmds.ls(uuid)`` -- these tests patch
    ``cmds.ls`` accordingly rather than ``cmds.objExists``. The handler never
    mutates the scene -- these tests only assert warning/no-warning and
    retry behavior.
    """

    NODE_UUID = "abc-123"

    def setUp(self):
        self._ls_patch = patch("maya.cmds.ls", return_value=["Character1_ControlRig"])
        self._warning_patch = patch("maya.cmds.warning")
        self.ls = self._ls_patch.start()
        self.warning = self._warning_patch.start()
        self.addCleanup(self._ls_patch.stop)
        self.addCleanup(self._warning_patch.stop)

    def test_transient_node_is_ignored(self):
        self.ls.return_value = []
        with patch.object(watch, "_resolve_character_for_hik_control_set_node") as resolve:
            watch._handle_new_hik_control_set_node(self.NODE_UUID)
            resolve.assert_not_called()
        self.warning.assert_not_called()

    def test_no_character_found_is_ignored(self):
        with patch.object(
            watch, "_resolve_character_for_hik_control_set_node", return_value=None
        ), patch.object(watch, "_find_frontend_binding_for_character") as find_binding:
            watch._handle_new_hik_control_set_node(
                self.NODE_UUID, retry=watch.MAX_CHARACTER_RESOLUTION_RETRIES
            )
            find_binding.assert_not_called()
        self.warning.assert_not_called()

    def test_no_character_found_reschedules_instead_of_giving_up(self):
        """Empirically (E2E reruns against real Maya 2024/2026), HIK does not
        always finish wiring ``HIKControlSetNode -> HIKCharacterNode`` by the
        time this deferred handler first runs -- see
        ``_handle_new_hik_control_set_node``'s docstring. A miss on an early
        attempt must reschedule itself rather than permanently abandoning a
        real, still-forming Control Rig.
        """
        with patch.object(
            watch, "_resolve_character_for_hik_control_set_node", return_value=None
        ), patch.object(watch, "_find_frontend_binding_for_character") as find_binding, patch(
            "maya.utils.executeDeferred"
        ) as execute_deferred:
            watch._handle_new_hik_control_set_node(self.NODE_UUID, retry=0)
            find_binding.assert_not_called()
            execute_deferred.assert_called_once_with(
                watch._handle_new_hik_control_set_node, self.NODE_UUID, 1
            )

    def test_no_character_found_stops_rescheduling_after_max_retries(self):
        with patch.object(
            watch, "_resolve_character_for_hik_control_set_node", return_value=None
        ), patch.object(watch, "_find_frontend_binding_for_character") as find_binding, patch(
            "maya.utils.executeDeferred"
        ) as execute_deferred:
            watch._handle_new_hik_control_set_node(
                self.NODE_UUID, retry=watch.MAX_CHARACTER_RESOLUTION_RETRIES
            )
            find_binding.assert_not_called()
            execute_deferred.assert_not_called()

    def test_plugin_owned_transaction_stays_silent(self):
        with patch.object(
            watch, "_resolve_character_for_hik_control_set_node", return_value="Character1"
        ), patch.object(
            watch, "get_active_control_rig_transaction", return_value=MagicMock()
        ), patch.object(watch, "_find_frontend_binding_for_character") as find_binding:
            watch._handle_new_hik_control_set_node(self.NODE_UUID)
            find_binding.assert_not_called()
        self.warning.assert_not_called()

    def test_no_frontend_binding_stays_silent(self):
        with patch.object(
            watch, "_resolve_character_for_hik_control_set_node", return_value="Character1"
        ), patch.object(
            watch, "get_active_control_rig_transaction", return_value=None
        ), patch.object(
            watch, "_find_frontend_binding_for_character", return_value=None
        ):
            watch._handle_new_hik_control_set_node(self.NODE_UUID)
        self.warning.assert_not_called()

    def test_out_of_band_rig_warns_without_mutating_binding_state(self):
        binding = FakeBinding("Character1", ["|hips", "|left_foot"])
        with patch.object(
            watch, "_resolve_character_for_hik_control_set_node", return_value="Character1"
        ), patch.object(
            watch, "get_active_control_rig_transaction", return_value=None
        ), patch.object(
            watch, "_find_frontend_binding_for_character", return_value=binding
        ):
            watch._handle_new_hik_control_set_node(self.NODE_UUID)

        # Warn-only: no adoption, no scene mutation, no binding state change.
        self.assertFalse(binding.control_rig_created)
        self.warning.assert_called_once()
        message = self.warning.call_args[0][0]
        self.assertIn("outside MMD Tools", message)
        self.assertIn("Character1", message)
        self.assertIn("mmdCcdIk", message)

    def test_out_of_band_rig_notifies_registered_callback(self):
        """FakeBinding has no ``model_root`` attribute; the handler must fall
        back to ``None`` for it (``getattr(..., default=None)``) rather than
        raising, since production bindings always have it but test doubles
        here intentionally do not."""
        binding = FakeBinding("Character1", ["|hips", "|left_foot"])
        callback = MagicMock()
        watch.register_control_rig_warning_callback(callback)
        self.addCleanup(watch.deregister_control_rig_warning_callback, callback)
        try:
            with patch.object(
                watch, "_resolve_character_for_hik_control_set_node", return_value="Character1"
            ), patch.object(
                watch, "get_active_control_rig_transaction", return_value=None
            ), patch.object(
                watch, "_find_frontend_binding_for_character", return_value=binding
            ):
                watch._handle_new_hik_control_set_node(self.NODE_UUID)
        finally:
            watch.deregister_control_rig_warning_callback(callback)

        callback.assert_called_once()
        args, kwargs = callback.call_args
        self.assertIn("outside MMD Tools", args[0])
        self.assertEqual(kwargs["character"], "Character1")
        self.assertIsNone(kwargs["model_root"])
        # The default logger/cmds.warning path must still run alongside the
        # callback -- registering a callback is additive, never a replacement.
        self.warning.assert_called_once()

    def test_callback_exception_does_not_suppress_default_warning_or_other_callbacks(self):
        binding = FakeBinding("Character1", ["|hips"])
        broken_callback = MagicMock(side_effect=RuntimeError("boom"))
        healthy_callback = MagicMock()
        watch.register_control_rig_warning_callback(broken_callback)
        watch.register_control_rig_warning_callback(healthy_callback)
        try:
            with patch.object(
                watch, "_resolve_character_for_hik_control_set_node", return_value="Character1"
            ), patch.object(
                watch, "get_active_control_rig_transaction", return_value=None
            ), patch.object(
                watch, "_find_frontend_binding_for_character", return_value=binding
            ):
                watch._handle_new_hik_control_set_node(self.NODE_UUID)
        finally:
            watch.deregister_control_rig_warning_callback(broken_callback)
            watch.deregister_control_rig_warning_callback(healthy_callback)

        self.warning.assert_called_once()
        healthy_callback.assert_called_once()


class TestWarningCallbackRegistration(unittest.TestCase):
    """Registration/deregistration of the pluggable warning callback API."""

    def tearDown(self):
        watch._warning_callbacks.clear()

    def test_register_is_idempotent(self):
        callback = MagicMock()
        watch.register_control_rig_warning_callback(callback)
        watch.register_control_rig_warning_callback(callback)
        self.assertEqual(watch._warning_callbacks.count(callback), 1)

    def test_deregister_missing_callback_is_a_no_op(self):
        callback = MagicMock()
        watch.deregister_control_rig_warning_callback(callback)  # must not raise
        self.assertNotIn(callback, watch._warning_callbacks)

    def test_deregister_removes_registered_callback(self):
        callback = MagicMock()
        watch.register_control_rig_warning_callback(callback)
        watch.deregister_control_rig_warning_callback(callback)
        self.assertNotIn(callback, watch._warning_callbacks)


if __name__ == "__main__":
    unittest.main()
