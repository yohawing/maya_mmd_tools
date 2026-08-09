"""One-shot Go to Bind Pose action tests without Maya runtime."""

from mmd_tools.actions.go_to_bind_pose_action import GoToBindPoseAction


class _FakeCmds:
    def __init__(self):
        self.exists = True
        self.joints = ["|model|jointA", "|model|jointB"]
        self.restore_calls = []
        self.connection_calls = []
        self.undo_calls = []

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
            self.restore_calls.append((node, kwargs))
        return None

    def listConnections(self, *_args, **_kwargs):
        return []

    def disconnectAttr(self, *args, **kwargs):
        self.connection_calls.append((args, kwargs))

    def connectAttr(self, *args, **kwargs):
        self.connection_calls.append((args, kwargs))

    def undoInfo(self, **kwargs):
        self.undo_calls.append(kwargs)


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
        {"openChunk": True, "chunkName": "MMD Go to Bind Pose"},
        {"closeChunk": True},
    ]


def test_execute_has_no_mode_or_return_to_motion_api():
    action = GoToBindPoseAction(_FakeCmds())

    assert not hasattr(action, "active")
    assert not hasattr(action, "return_to_motion")


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
