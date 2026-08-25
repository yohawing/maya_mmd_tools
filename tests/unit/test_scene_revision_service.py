"""Unit coverage for the fail-closed scene revision watch."""

import unittest

from mmd_tools.services.scene_revision_service import SceneRevisionService


class _FakeUuid:
    def __init__(self, value):
        self.value = value

    def asString(self):
        return self.value


class _FakeNode:
    def __init__(self, value):
        self.uuid = value


class _FakeFn:
    def __init__(self, node):
        self._node = node

    def uuid(self):
        return _FakeUuid(self._node.uuid)


class _FakePlug:
    def __init__(self, node):
        self._node = node

    def node(self):
        return self._node


class _CallbackOwner:
    def __init__(self, name, log):
        self.name = name
        self.log = log
        self._next_id = 0

    def _add(self, method, args):
        self._next_id += 1
        callback_id = f"{self.name}:{self._next_id}"
        self.log.append(("add", self.name, method, args, callback_id))
        return callback_id

    def _remove(self, callback_id):
        self.log.append(("remove", self.name, callback_id))


class _FakeNodeMessage(_CallbackOwner):
    kConnectionMade = 1
    kConnectionBroken = 2
    kAttributeEval = 4
    kAttributeSet = 8
    kAttributeLocked = 16
    kAttributeUnlocked = 32
    kAttributeAdded = 64
    kAttributeRemoved = 128
    kAttributeRenamed = 256
    kAttributeKeyable = 512
    kAttributeUnkeyable = 1024
    kIncomingDirection = 2048
    kAttributeArrayAdded = 4096
    kAttributeArrayRemoved = 8192
    kOtherPlugSet = 16384

    def addAttributeChangedCallback(self, *args):
        return self._add("attribute", args)

    def addNameChangedCallback(self, *args):
        return self._add("name", args)

    def addNodeDestroyedCallback(self, *args):
        return self._add("destroyed", args)


class _FakeDagMessage(_CallbackOwner):
    def addAllDagChangesCallback(self, *args):
        return self._add("dag", args)


class _FakeDgMessage(_CallbackOwner):
    def addConnectionCallback(self, *args):
        return self._add("connection", args)

    def addNodeAddedCallback(self, *args):
        return self._add("added", args)

    def addNodeRemovedCallback(self, *args):
        return self._add("removed", args)


class _FakeSceneMessage(_CallbackOwner):
    kBeforeOpen = "before-open"
    kAfterOpen = "after-open"
    kBeforeNew = "before-new"
    kAfterNew = "after-new"

    def addCallback(self, *args):
        return self._add("scene", args)


class _FakeEventMessage(_CallbackOwner):
    def addEventCallback(self, *args):
        return self._add("event", args)


class _FakeAnimMessage(_CallbackOwner):
    def __init__(self, name, log):
        super().__init__(name, log)
        self.flush_count = 0
        self.callbacks = []
        self.pending_edits = []

    def addAnimCurveEditedCallback(self, *args):
        callback_id = self._add("anim-curve", args)
        self.callbacks.append((callback_id, args))
        return callback_id

    def queue_edit(self, *objects):
        self.pending_edits.append(list(objects))

    def flushAnimKeyframeEditedCallbacks(self):
        self.flush_count += 1
        pending = self.pending_edits
        self.pending_edits = []
        for objects in pending:
            for _callback_id, (callback, client_data) in list(self.callbacks):
                callback(objects, client_data)


class _FakeMessage(_CallbackOwner):
    def removeCallback(self, callback_id):
        self._remove(callback_id)


class _FakeSelectionList:
    def __init__(self, nodes):
        self.nodes = nodes

    def add(self, value):
        self.value = value

    def getDependNode(self, _index):
        return self.nodes[self.value]


class _FakeOpenMaya:
    def __init__(self, nodes=None, *, fail=None):
        self.nodes = nodes or {}
        self.log = []
        self.MNodeMessage = _FakeNodeMessage("node", self.log)
        self.MDagMessage = _FakeDagMessage("dag", self.log)
        self.MDGMessage = _FakeDgMessage("dg", self.log)
        self.MSceneMessage = _FakeSceneMessage("scene", self.log)
        self.MEventMessage = _FakeEventMessage("event", self.log)
        self.MMessage = _FakeMessage("message", self.log)
        self.MFnDependencyNode = _FakeFn
        self.MSelectionList = lambda: _FakeSelectionList(self.nodes)
        self.fail = fail


class _FakeOpenMayaAnim:
    def __init__(self, log):
        self.MAnimMessage = _FakeAnimMessage("anim", log)


class TestSceneRevisionService(unittest.TestCase):
    def setUp(self):
        self.node = _FakeNode("target-uuid")
        self.other = _FakeNode("other-uuid")
        self.om = _FakeOpenMaya({self.node.uuid: self.node, self.other.uuid: self.other})
        self.oma = _FakeOpenMayaAnim(self.om.log)
        self._session_ids = iter(("session-a", "session-b", "session-c", "session-d"))
        self.service = SceneRevisionService(
            self.om,
            self.oma,
            session_id_factory=lambda: next(self._session_ids),
        )

    def test_arm_tracks_session_revision_and_all_callbacks(self):
        watch = self.service.arm([self.node])

        self.assertTrue(watch.usable)
        self.assertEqual(watch.session_id, "session-a")
        self.assertEqual(watch.revision, 0)
        self.assertEqual(len([item for item in self.om.log if item[0] == "add"]), 14)
        self.assertTrue(watch.current)

    def test_attribute_set_and_connection_invalidate_but_eval_is_ignored(self):
        watch = self.service.arm([self.node])
        callback = self.om.MNodeMessage

        self.service._attribute_changed_callback(callback.kAttributeEval, client_data=watch)
        self.assertEqual(self.service.revision, 0)
        self.service._attribute_changed_callback(callback.kAttributeSet, client_data=watch)
        self.assertEqual(self.service.revision, 1)
        self.assertTrue(watch.stale)

        # An already stale handle cannot add a second invalidation.
        self.service._connection_callback(
            _FakePlug(self.node), _FakePlug(self.other), True, watch
        )
        self.assertEqual(self.service.revision, 1)

    def test_attribute_eval_modifiers_and_incoming_array_materialization_are_ignored(self):
        watch = self.service.arm([self.node])
        callback = self.om.MNodeMessage

        # Maya's callback values are bit fields: 2052 is kAttributeEval with
        # kIncomingDirection, not a distinct enum value.
        self.service._attribute_changed_callback(
            callback.kAttributeEval | callback.kIncomingDirection,
            client_data=watch,
        )
        self.assertEqual(self.service.revision, 0)
        self.assertTrue(watch.usable)

        # Runtime array nodes may materialize an incoming element while a
        # frame is evaluated (6144 in Maya 2024).
        self.service._attribute_changed_callback(
            callback.kAttributeArrayAdded | callback.kIncomingDirection,
            client_data=watch,
        )
        self.assertEqual(self.service.revision, 0)
        self.assertTrue(watch.usable)

        self.service._attribute_changed_callback(
            callback.kAttributeArrayAdded,
            client_data=watch,
        )
        self.assertEqual(self.service.revision, 1)
        self.assertTrue(watch.stale)

    def test_incoming_attribute_set_remains_mutation_evidence(self):
        watch = self.service.arm([self.node])
        callback = self.om.MNodeMessage

        self.service._attribute_changed_callback(
            callback.kAttributeSet | callback.kIncomingDirection,
            client_data=watch,
        )
        self.assertEqual(self.service.revision, 1)
        self.assertTrue(watch.stale)

    def test_eval_plus_set_and_array_removed_remain_mutation_evidence(self):
        callback = self.om.MNodeMessage

        watch = self.service.arm([self.node])
        self.service._attribute_changed_callback(
            callback.kAttributeEval | callback.kAttributeSet,
            client_data=watch,
        )
        self.assertEqual(self.service.revision, 1)
        self.assertTrue(watch.stale)

        watch = self.service.arm([self.node])
        self.service._attribute_changed_callback(
            callback.kAttributeArrayRemoved,
            client_data=watch,
        )
        self.assertEqual(self.service.revision, 2)
        self.assertTrue(watch.stale)

    def test_configured_ignored_mutation_bits_do_not_fail_open(self):
        callback = self.om.MNodeMessage
        service = SceneRevisionService(
            self.om,
            self.oma,
            ignored_attribute_flags=(callback.kAttributeSet,),
            session_id_factory=lambda: "ignored-session",
        )
        watch = service.arm([self.node])

        service._attribute_changed_callback(
            callback.kAttributeSet,
            client_data=watch,
        )
        self.assertEqual(service.revision, 0)
        self.assertTrue(watch.usable)

        service._attribute_changed_callback(
            callback.kAttributeSet | callback.kAttributeArrayRemoved,
            client_data=watch,
        )
        self.assertEqual(service.revision, 1)
        self.assertTrue(watch.stale)

    def test_connection_dag_and_global_node_events_filter_by_uuid(self):
        watch = self.service.arm([self.node])

        self.service._connection_callback(
            _FakePlug(self.other), _FakePlug(self.other), True, watch
        )
        self.assertEqual(self.service.revision, 0)
        self.service._connection_callback(
            _FakePlug(self.other), _FakePlug(self.node), False, watch
        )
        self.assertEqual(self.service.revision, 1)

        fresh = self.service.arm([self.node])
        self.service._dag_changed_callback(1, self.other, self.node, fresh)
        self.assertTrue(fresh.stale)
        self.assertEqual(self.service.revision, 2)

        fresh = self.service.arm([self.node])
        self.service._node_added_callback(self.other, fresh)
        self.assertEqual(self.service.revision, 2)
        self.service._node_removed_callback(self.node, fresh)
        self.assertTrue(fresh.stale)

    def test_connection_made_and_broken_invalidate_matching_watch(self):
        made = self.service.arm([self.node])
        self.service._connection_callback(
            _FakePlug(self.node), _FakePlug(self.other), True, made
        )
        self.assertEqual(self.service.revision, 1)
        self.assertTrue(made.stale)

        broken = self.service.arm([self.node])
        self.service._connection_callback(
            _FakePlug(self.node), _FakePlug(self.other), False, broken
        )
        self.assertEqual(self.service.revision, 2)
        self.assertTrue(broken.stale)

    def test_anim_curve_edit_filters_by_dependency_uuid(self):
        watch = self.service.arm([self.node])

        self.service._anim_curve_edited_callback([self.other], watch)
        self.assertEqual(self.service.revision, 0)
        self.service._anim_curve_edited_callback([self.node], watch)
        self.assertEqual(self.service.revision, 1)
        self.assertTrue(watch.stale)

    def test_current_revision_flushes_pending_animation_callbacks(self):
        self.assertEqual(self.service.current_revision(), 0)
        self.assertEqual(self.oma.MAnimMessage.flush_count, 1)

    def test_arm_discards_preexisting_pending_animation_edits(self):
        self.oma.MAnimMessage.queue_edit(self.node)

        watch = self.service.arm([self.node])

        self.assertTrue(watch.usable)
        self.assertTrue(watch.current)
        self.assertEqual(self.service.revision, 0)
        self.assertEqual(self.oma.MAnimMessage.flush_count, 1)
        self.assertEqual(self.service.current_revision(), 0)
        self.assertTrue(watch.usable)
        self.assertEqual(self.oma.MAnimMessage.flush_count, 2)

    def test_current_revision_invalidates_post_arm_pending_animation_edits(self):
        watch = self.service.arm([self.node])
        self.oma.MAnimMessage.queue_edit(self.node)

        revision = self.service.current_revision()

        self.assertEqual(revision, 1)
        self.assertTrue(watch.stale)
        self.assertFalse(watch.usable)
        self.assertEqual(self.oma.MAnimMessage.flush_count, 2)

    def test_arm_flush_failure_returns_disabled_watch_without_callbacks(self):
        def fail_flush():
            raise RuntimeError("flush failed")

        self.oma.MAnimMessage.flushAnimKeyframeEditedCallbacks = fail_flush

        watch = self.service.arm([self.node])

        self.assertTrue(watch.disabled)
        self.assertFalse(watch.usable)
        self.assertEqual(
            [item for item in self.om.log if item[0] == "add"],
            [],
        )

    def test_name_destroy_undo_redo_and_scene_reset_invalidate(self):
        watch = self.service.arm([self.node])
        self.service._name_changed_callback(self.node, watch)
        self.assertTrue(watch.stale)

        watch = self.service.arm([self.node])
        self.service._node_destroyed_callback(self.node, watch)
        self.assertTrue(watch.stale)

        watch = self.service.arm([self.node])
        self.service._undo_redo_callback(watch)
        self.assertTrue(watch.stale)

        old_session = self.service.session_id
        old_revision = self.service.revision
        watch = self.service.arm([self.node])
        self.service._scene_reset_callback("AfterNew", watch)
        self.assertNotEqual(self.service.session_id, old_session)
        self.assertEqual(self.service.revision, old_revision + 1)
        self.assertTrue(watch.stale)

    def test_uuid_dependencies_are_resolved_to_mobjects(self):
        watch = self.service.arm([self.node.uuid])
        self.assertTrue(watch.usable)
        self.assertEqual(watch.dependency_uuids, {self.node.uuid})

    def test_registration_failure_disables_and_cleans_up_partial_callbacks(self):
        class FailingNodeMessage(_FakeNodeMessage):
            def addNameChangedCallback(self, *args):
                raise RuntimeError("registration failure")

        self.om.MNodeMessage = FailingNodeMessage("node", self.om.log)
        watch = self.service.arm([self.node])

        self.assertTrue(watch.disabled)
        self.assertFalse(watch.usable)
        self.assertTrue(watch.closed)
        self.assertTrue(any(item[0] == "remove" for item in self.om.log))

    def test_close_is_idempotent_and_removes_callbacks(self):
        watch = self.service.arm([self.node])
        watch.close()
        remove_count = len([item for item in self.om.log if item[0] == "remove"])
        watch.close()
        self.assertEqual(remove_count, 14)
        self.assertFalse(watch.usable)

    def test_target_lookup_can_be_injected_without_maya_fn(self):
        def lookup(value):
            return value.uuid if isinstance(value, _FakeNode) else value

        service = SceneRevisionService(
            object(), target_uuid_lookup=lookup, session_id_factory=lambda: "s"
        )
        self.assertEqual(service.target_uuid(self.node), self.node.uuid)


if __name__ == "__main__":
    unittest.main()
