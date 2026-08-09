"""One-shot Go to Bind Pose action tests without Maya runtime."""

from mmd_tools.actions.go_to_bind_pose_action import GoToBindPoseAction


class _FakeCmds:
    def __init__(self):
        self.exists = True
        self.joints = ["|model|jointA", "|model|jointB"]
        self.restore_calls = []
        self.connection_calls = []
        self.undo_calls = []
        self.undo_state = True
        self.fail_undo_query = False
        self.fail_undo_disable = False
        self.fail_undo_enable_once = False
        self.fail_restore = False
        self.disconnect_count = 0
        self.fail_disconnect_once_at = None
        self.fail_connect_once = False
        self.selection = ["|model|jointA"]
        self.time = 12.0
        self.connections = {}
        self.values = {}
        self.locks = {}
        for joint in self.joints:
            for attribute in ("translate", "rotate", "scale"):
                defaults = (1.0, 2.0, 3.0) if attribute == "translate" else ((1.0, 1.0, 1.0) if attribute == "scale" else (4.0, 5.0, 6.0))
                for axis, value in zip("XYZ", defaults):
                    self.values[f"{joint}.{attribute}{axis}"] = value
            for axis in ("XY", "XZ", "YZ"):
                self.values[f"{joint}.shear{axis}"] = 0.0
            self.values[f"{joint}.offsetParentMatrix"] = ((1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0),)

    def objExists(self, _node):
        return self.exists

    def listRelatives(self, _node, **_kwargs):
        return list(self.joints)

    def dagPose(self, node, **kwargs):
        if kwargs.get("query") and kwargs.get("bindPose"):
            return ["model_bindPose"]
        if kwargs.get("query") and kwargs.get("members"):
            return list(self.joints)
        if kwargs.get("restore"):
            if self.fail_restore:
                raise RuntimeError("cannot realize pose")
            self.restore_calls.append((node, kwargs))
            for joint in self.joints:
                for axis in "XYZ":
                    self.values[f"{joint}.rotate{axis}"] = 0.0
        return None

    def listConnections(self, plug, **kwargs):
        if kwargs.get("connections"):
            pairs = []
            for destination, sources in self.connections.items():
                if destination.rsplit(".", 1)[0] != plug:
                    continue
                for source in sources:
                    pairs.extend((destination, source))
            return pairs
        return list(self.connections.get(plug, ()))

    def disconnectAttr(self, source, destination):
        self.disconnect_count += 1
        if self.fail_disconnect_once_at == self.disconnect_count:
            self.fail_disconnect_once_at = None
            raise RuntimeError("disconnect injection")
        self.connection_calls.append(((source, destination), {}))
        self.connections[destination] = [item for item in self.connections.get(destination, ()) if item != source]

    def connectAttr(self, source, destination, **kwargs):
        if self.fail_connect_once:
            self.fail_connect_once = False
            raise RuntimeError("connect injection")
        self.connection_calls.append(((source, destination), kwargs))
        self.connections.setdefault(destination, []).append(source)

    def isConnected(self, source, destination):
        return source in self.connections.get(destination, ())

    def ls(self, node=None, **kwargs):
        if kwargs.get("selection"):
            return list(self.selection)
        if kwargs.get("uuid"):
            return ["model_uuid"] if node == "model" else [f"uuid_{node}"]
        if node == "model":
            return ["|model"]
        if node == "model_uuid":
            return ["|model"]
        if isinstance(node, str) and node.startswith("uuid_"):
            return [node[5:]]
        return [node]

    def currentTime(self, value=None, **kwargs):
        if kwargs.get("query"):
            return self.time
        self.time = float(value)

    def select(self, nodes, **_kwargs):
        self.selection = list(nodes)

    def getAttr(self, plug, **kwargs):
        if kwargs.get("lock"):
            return self.locks.get(plug, False)
        return self.values[plug]

    def setAttr(self, plug, *values, **kwargs):
        if "lock" in kwargs:
            self.locks[plug] = bool(kwargs["lock"])
            return
        self.values[plug] = tuple(values) if kwargs.get("type") == "matrix" else values[0]

    def undoInfo(self, **kwargs):
        self.undo_calls.append(kwargs)
        if kwargs.get("query") and kwargs.get("state"):
            if self.fail_undo_query:
                raise RuntimeError("undo query injection")
            return self.undo_state
        if "stateWithoutFlush" in kwargs:
            if not kwargs["stateWithoutFlush"] and self.fail_undo_disable:
                raise RuntimeError("undo disable injection")
            if kwargs["stateWithoutFlush"] and self.fail_undo_enable_once:
                self.fail_undo_enable_once = False
                raise RuntimeError("undo enable injection")
            self.undo_state = bool(kwargs["stateWithoutFlush"])


def test_execute_restores_one_bind_pose_without_touching_connections():
    cmds = _FakeCmds()

    result = GoToBindPoseAction(cmds).execute("model")

    assert result.succeeded
    assert result.joint_count == 2
    assert cmds.restore_calls == [
        ("model_bindPose", {"restore": True, "global": True})
    ]
    assert cmds.connection_calls == []
    assert cmds.undo_calls == [
        {"query": True, "state": True},
        {"stateWithoutFlush": False},
        {"query": True, "state": True},
        {"stateWithoutFlush": True},
        {"query": True, "state": True},
    ]


def test_execute_exposes_reversible_session_api():
    action = GoToBindPoseAction(_FakeCmds())

    assert not action.active
    assert callable(action.return_to_motion)


def test_return_to_motion_restores_driver_value_lock_time_and_selection():
    cmds = _FakeCmds()
    plug = "|model|jointA.rotateX"
    cmds.connections[plug] = ["curve.output"]
    cmds.locks[plug] = True
    original = cmds.values[plug]
    action = GoToBindPoseAction(cmds)

    entered = action.execute("model")
    assert entered.succeeded and action.active
    assert cmds.connections[plug] == []
    cmds.time = 30.0
    cmds.selection = []

    restored = action.return_to_motion()

    assert restored.succeeded and not action.active
    assert cmds.connections[plug] == ["curve.output"]
    assert cmds.values[plug] == original
    assert cmds.locks[plug]
    assert cmds.time == 12.0
    assert cmds.selection == ["|model|jointA"]


def test_enter_failure_rolls_back_every_isolated_edge_and_lock():
    cmds = _FakeCmds()
    plug = "|model|jointA.rotateX"
    cmds.connections[plug] = ["curve.output"]
    cmds.locks[plug] = True
    cmds.fail_restore = True
    action = GoToBindPoseAction(cmds)

    result = action.execute("model")

    assert not result.succeeded
    assert not action.active
    assert cmds.connections[plug] == ["curve.output"]
    assert cmds.locks[plug]


def test_return_fails_closed_when_a_foreign_writer_appears():
    cmds = _FakeCmds()
    action = GoToBindPoseAction(cmds)
    assert action.execute("model").succeeded
    cmds.connections["|model|jointA.rotateY"] = ["foreign.output"]

    result = action.return_to_motion()

    assert not result.succeeded
    assert result.active and action.active
    assert cmds.connections["|model|jointA.rotateY"] == ["foreign.output"]


def test_partial_isolation_failure_restores_every_edge():
    cmds = _FakeCmds()
    first = "|model|jointA.rotateX"
    second = "|model|jointA.rotateY"
    cmds.connections[first] = ["curveX.output"]
    cmds.connections[second] = ["curveY.output"]
    cmds.fail_disconnect_once_at = 2

    result = GoToBindPoseAction(cmds).execute("model")

    assert not result.succeeded
    assert cmds.connections[first] == ["curveX.output"]
    assert cmds.connections[second] == ["curveY.output"]


def test_return_connect_failure_rolls_back_to_active_bind_session():
    cmds = _FakeCmds()
    plug = "|model|jointA.rotateX"
    cmds.connections[plug] = ["curve.output"]
    action = GoToBindPoseAction(cmds)
    assert action.execute("model").succeeded
    cmds.fail_connect_once = True

    failed = action.return_to_motion()

    assert not failed.succeeded and action.active
    assert cmds.connections[plug] == []
    assert action.return_to_motion().succeeded


def test_incomplete_bind_pose_is_rejected_before_writer_isolation():
    cmds = _FakeCmds()
    original_dag_pose = cmds.dagPose

    def incomplete_pose(node, **kwargs):
        if kwargs.get("query") and kwargs.get("members"):
            return ["|model|jointA"]
        return original_dag_pose(node, **kwargs)

    cmds.dagPose = incomplete_pose
    result = GoToBindPoseAction(cmds).execute("model")

    assert not result.succeeded
    assert "covers every" in result.error
    assert cmds.connection_calls == []


def test_execute_fails_before_mutation_when_undo_state_cannot_be_queried():
    cmds = _FakeCmds()
    cmds.connections["|model|jointA.rotateX"] = ["curve.output"]
    cmds.fail_undo_query = True

    result = GoToBindPoseAction(cmds).execute("model")

    assert not result.succeeded
    assert cmds.connections["|model|jointA.rotateX"] == ["curve.output"]
    assert cmds.connection_calls == []


def test_execute_fails_before_mutation_when_undo_cannot_be_suppressed():
    cmds = _FakeCmds()
    cmds.connections["|model|jointA.rotateX"] = ["curve.output"]
    cmds.fail_undo_disable = True

    result = GoToBindPoseAction(cmds).execute("model")

    assert not result.succeeded
    assert cmds.connections["|model|jointA.rotateX"] == ["curve.output"]
    assert cmds.connection_calls == []


def test_transient_undo_reenable_failure_recovers_original_state():
    cmds = _FakeCmds()
    cmds.fail_undo_enable_once = True

    result = GoToBindPoseAction(cmds).execute("model")

    assert result.succeeded
    assert cmds.undo_state is True


def test_execute_fails_cleanly_without_model_joints_or_bind_pose():
    cmds = _FakeCmds()
    cmds.exists = False
    assert not GoToBindPoseAction(cmds).execute("model").succeeded

    cmds.exists = True
    cmds.joints = []
    assert not GoToBindPoseAction(cmds).execute("model").succeeded

    cmds.joints = ["|model|joint"]
    cmds.dagPose = lambda *_args, **_kwargs: []
    result = GoToBindPoseAction(cmds).execute("model")
    assert not result.succeeded
    assert "no bind pose" in result.error
