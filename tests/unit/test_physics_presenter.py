"""PhysicsPresenterのMaya非依存ロジックを検証するテスト。"""

import unittest

from tests.common.maya_stub import install_headless_ui_stubs

install_headless_ui_stubs()

from mmd_tools.ui.presenters.physics_presenter import PhysicsPresenter  # noqa: E402

TEST_MODEL = "test_mmd_model"


class _FakeSignal:
    def __init__(self):
        self._callbacks = []

    def connect(self, callback):
        self._callbacks.append(callback)


class _FakeList:
    def __init__(self):
        self.clear_calls = 0
        self.items = []

    def clear(self):
        self.clear_calls += 1
        self.items.clear()

    def addItem(self, item):
        self.items.append(item)


class _FakeView:
    def __init__(self):
        self.rigid_body_list = _FakeList()
        self.joint_list = _FakeList()


class _FakeAppState:
    def __init__(self, current_model_root=None):
        self.current_model_root = current_model_root
        self.current_model_changed = _FakeSignal()


class _FakeMayaAdapter:
    def __init__(self, exists=True, rigid_bodies=None, joints=None):
        self.exists = exists
        self.rigid_bodies = rigid_bodies if rigid_bodies is not None else []
        self.joints = joints if joints is not None else []
        self.calls = []

    def object_exists(self, node):
        self.calls.append(("object_exists", node))
        return self.exists

    def ls(self, *args, **kwargs):
        self.calls.append(("ls", args, kwargs))
        node_type = kwargs.get("type")
        if node_type == "nRigid":
            return self.rigid_bodies
        if node_type == "constraint":
            return self.joints
        return []


def _make_presenter(model=TEST_MODEL, adapter=None):
    view = _FakeView()
    app_state = _FakeAppState(model)
    adapter = adapter or _FakeMayaAdapter()
    presenter = PhysicsPresenter(view, app_state, maya_adapter=adapter)
    return presenter, view, app_state, adapter


class TestPhysicsPresenter(unittest.TestCase):
    def test_load_physics_clears_and_returns_when_no_model(self):
        presenter, view, _, adapter = _make_presenter(model=None)

        presenter.load_physics()

        self.assertEqual(view.rigid_body_list.clear_calls, 1)
        self.assertEqual(view.joint_list.clear_calls, 1)
        self.assertEqual(adapter.calls, [])
        self.assertEqual(view.rigid_body_list.items, [])
        self.assertEqual(view.joint_list.items, [])

    def test_load_physics_returns_when_model_does_not_exist(self):
        adapter = _FakeMayaAdapter(exists=False)
        presenter, view, _, adapter = _make_presenter(adapter=adapter)

        presenter.load_physics()

        self.assertEqual(adapter.calls, [("object_exists", TEST_MODEL)])
        self.assertEqual(view.rigid_body_list.items, [])
        self.assertEqual(view.joint_list.items, [])

    def test_load_physics_adds_rigid_bodies_and_joints_from_adapter_ls(self):
        adapter = _FakeMayaAdapter(rigid_bodies=["rb1", "rb2"], joints=["joint1"])
        presenter, view, _, adapter = _make_presenter(adapter=adapter)

        presenter.load_physics()

        self.assertEqual(
            adapter.calls,
            [
                ("object_exists", TEST_MODEL),
                ("ls", (), {"type": "nRigid"}),
                ("ls", (), {"type": "constraint"}),
            ],
        )
        self.assertEqual(view.rigid_body_list.items, ["rb1", "rb2"])
        self.assertEqual(view.joint_list.items, ["joint1"])


if __name__ == "__main__":
    unittest.main()
