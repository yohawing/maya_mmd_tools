"""PhysicsPresenterのMaya非依存ロジックを検証するテスト。"""

import unittest

from tests.common.maya_stub import install_headless_ui_stubs

install_headless_ui_stubs()

from mmd_tools.core.physics_scene_query import (  # noqa: E402
    JointSceneRef,
    PhysicsSceneRefs,
    RigidBodySceneRef,
)
from mmd_tools.ui.presenters.physics_presenter import PhysicsPresenter  # noqa: E402
from mmd_tools.ui.qt_compat import Qt  # noqa: E402

TEST_MODEL = "|test_mmd_model"


class _FakeSignal:
    def __init__(self):
        self._callbacks = []

    def connect(self, callback):
        self._callbacks.append(callback)

    def emit(self, *args):
        for callback in self._callbacks:
            callback(*args)


class _FakeList:
    def __init__(self):
        self.clear_calls = 0
        self.items = []
        self.itemSelectionChanged = _FakeSignal()
        self._selected_items = []

    def clear(self):
        self.clear_calls += 1
        self.items.clear()
        self._selected_items.clear()

    def addItem(self, item):
        self.items.append(item)

    def selectedItems(self):
        return list(self._selected_items)

    def select_items(self, *items):
        self._selected_items = list(items)
        self.itemSelectionChanged.emit()


class _FakeButton:
    def __init__(self):
        self.clicked = _FakeSignal()


class _FakeCheck:
    def __init__(self, checked=True):
        self._checked = checked
        self.toggled = _FakeSignal()

    def isChecked(self):
        return self._checked

    def set_checked(self, checked):
        self._checked = checked
        self.toggled.emit(checked)

    def setChecked(self, checked):
        self._checked = checked


class _FakeLabel:
    def __init__(self):
        self.text = ""

    def setText(self, text):
        self.text = text


class _FakeView:
    def __init__(self):
        self.rigid_body_list = _FakeList()
        self.joint_list = _FakeList()
        self.refresh_btn = _FakeButton()
        self.collider_visible_check = _FakeCheck()
        self.detail_name_value = _FakeLabel()
        self.detail_type_value = _FakeLabel()
        self.detail_shape_value = _FakeLabel()
        self.detail_bodies_value = _FakeLabel()
        self.detail_node_value = _FakeLabel()


class _FakeAppState:
    def __init__(self, current_model_root=None):
        self.current_model_root = current_model_root
        self.current_model_changed = _FakeSignal()


class _FakeMayaAdapter:
    def __init__(self, exists=True, existing_nodes=None):
        self.exists = exists
        self.existing_nodes = set(existing_nodes or [])
        self.calls = []
        self.attrs = {}
        self.incoming_connections = {}

    def object_exists(self, node):
        self.calls.append(("object_exists", node))
        return self.exists and (not self.existing_nodes or node in self.existing_nodes or node == TEST_MODEL)

    def select(self, nodes, replace=True):
        self.calls.append(("select", tuple(nodes), replace))

    def set_attr(self, *args, **kwargs):
        self.calls.append(("set_attr", args, kwargs))
        node, attr = args[0].rsplit(".", 1)
        self.attrs[(node, attr)] = args[1]

    def get_attr(self, attr_path):
        node, attr = attr_path.rsplit(".", 1)
        return self.attrs[(node, attr)]

    def attribute_exists(self, attr, node):
        return (node, attr) in self.attrs

    def add_attr(self, node, longName=None, attributeType=None, **kwargs):
        self.calls.append(("add_attr", node, longName, attributeType))
        self.attrs[(node, longName)] = False

    def list_relatives(self, node, **kwargs):
        if kwargs.get("type") == "mmdRigidBodyLocator":
            return [item for item in self.existing_nodes if item.startswith(node)]
        return []

    def list_connections(self, node, **kwargs):
        if kwargs.get("source") and kwargs.get("plugs"):
            return self.incoming_connections.get(node, [])
        return []

    def connect_attr(self, source, destination, force=False):
        self.calls.append(("connect_attr", source, destination, force))
        self.incoming_connections[destination] = [source]


class _FakePhysicsReader:
    def __init__(self, refs=None):
        self.refs = refs or PhysicsSceneRefs((), ())
        self.calls = []

    def collect(self, root):
        self.calls.append(root)
        return self.refs


def _rigid(transform, index, name, shape_type=0, physics_mode=0, bone=-1, locator_shape=None):
    return RigidBodySceneRef(
        transform=transform,
        bullet_shape=f"{transform}|bulletRigidBodyShape",
        index=index,
        name=name,
        name_english=name,
        shape_type=shape_type,
        physics_mode=physics_mode,
        related_bone_index=bone,
        locator_shape=locator_shape,
    )


def _joint(transform, name, joint_type=0, body_a=-1, body_b=-1):
    return JointSceneRef(
        transform=transform,
        constraint_shape=f"{transform}|bulletRigidBodyConstraintShape",
        name=name,
        name_english=name,
        joint_type=joint_type,
        rigid_body_a_index=body_a,
        rigid_body_b_index=body_b,
    )


def _make_presenter(model=TEST_MODEL, adapter=None, reader=None):
    view = _FakeView()
    app_state = _FakeAppState(model)
    adapter = adapter or _FakeMayaAdapter()
    reader = reader or _FakePhysicsReader()
    presenter = PhysicsPresenter(view, app_state, maya_adapter=adapter, physics_reader=reader)
    return presenter, view, app_state, adapter, reader


class TestPhysicsPresenter(unittest.TestCase):
    def test_load_physics_clears_and_returns_when_no_model(self):
        presenter, view, _, adapter, reader = _make_presenter(model=None)

        presenter.load_physics()

        self.assertEqual(view.rigid_body_list.clear_calls, 1)
        self.assertEqual(view.joint_list.clear_calls, 1)
        self.assertEqual(adapter.calls, [])
        self.assertEqual(reader.calls, [])
        self.assertEqual(view.rigid_body_list.items, [])
        self.assertEqual(view.joint_list.items, [])

    def test_load_physics_returns_when_model_does_not_exist(self):
        adapter = _FakeMayaAdapter(exists=False)
        presenter, view, _, adapter, reader = _make_presenter(adapter=adapter)

        presenter.load_physics()

        self.assertEqual(adapter.calls, [("object_exists", TEST_MODEL)])
        self.assertEqual(reader.calls, [])
        self.assertEqual(view.rigid_body_list.items, [])
        self.assertEqual(view.joint_list.items, [])

    def test_load_physics_adds_root_scoped_bullet_metadata_items(self):
        refs = PhysicsSceneRefs(
            rigid_bodies=(
                _rigid("|root|rb2", 2, "skirt", shape_type=1, physics_mode=2, bone=9),
                _rigid("|root|rb5", 5, "hair", shape_type=2, physics_mode=1, bone=3),
            ),
            joints=(
                _joint("|root|jointB", "jointB", joint_type=4, body_a=2, body_b=5),
            ),
        )
        reader = _FakePhysicsReader(refs)
        presenter, view, _, adapter, reader = _make_presenter(reader=reader)

        presenter.load_physics()

        self.assertEqual(adapter.calls[0], ("object_exists", TEST_MODEL))
        self.assertEqual(reader.calls, [TEST_MODEL])
        self.assertEqual([item.text() for item in view.rigid_body_list.items], [
            "2: skirt (box, mode=2, bone=9)",
            "5: hair (capsule, mode=1, bone=3)",
        ])
        self.assertEqual(view.rigid_body_list.items[0].data(Qt.UserRole), "|root|rb2")
        self.assertEqual([item.text() for item in view.joint_list.items], [
            "jointB (type=4, A=2, B=5)",
        ])
        self.assertEqual(view.joint_list.items[0].data(Qt.UserRole), "|root|jointB")

    def test_rigid_body_selection_selects_user_role_transform(self):
        refs = PhysicsSceneRefs(rigid_bodies=(_rigid("|root|rb2", 2, "skirt"),), joints=())
        adapter = _FakeMayaAdapter(existing_nodes={"|root|rb2"})
        presenter, view, _, adapter, _ = _make_presenter(adapter=adapter, reader=_FakePhysicsReader(refs))
        presenter.load_physics()

        view.rigid_body_list.select_items(view.rigid_body_list.items[0])

        self.assertEqual(adapter.calls[-2:], [
            ("object_exists", "|root|rb2"),
            ("select", ("|root|rb2",), True),
        ])
        self.assertEqual(view.detail_name_value.text, "skirt")
        self.assertEqual(view.detail_type_value.text, "Rigid body")
        self.assertEqual(view.detail_shape_value.text, "sphere, mode=0")
        self.assertEqual(view.detail_bodies_value.text, "bone=-1")
        self.assertEqual(view.detail_node_value.text, "|root|rb2")

    def test_joint_selection_ignores_missing_transform(self):
        refs = PhysicsSceneRefs(rigid_bodies=(), joints=(_joint("|root|jointB", "jointB"),))
        adapter = _FakeMayaAdapter(existing_nodes={"|root|other"})
        presenter, view, _, adapter, _ = _make_presenter(adapter=adapter, reader=_FakePhysicsReader(refs))
        presenter.load_physics()

        view.joint_list.select_items(view.joint_list.items[0])

        self.assertNotIn(("select", ("|root|jointB",), True), adapter.calls)
        self.assertEqual(view.detail_name_value.text, "jointB")
        self.assertEqual(view.detail_type_value.text, "Joint")
        self.assertEqual(view.detail_shape_value.text, "type=0")
        self.assertEqual(view.detail_bodies_value.text, "A=-1, B=-1")

    def test_refresh_button_and_model_change_reload_physics(self):
        presenter, _, app_state, _, reader = _make_presenter()

        app_state.current_model_changed.emit(TEST_MODEL)
        presenter.view.refresh_btn.clicked.emit()

        self.assertEqual(reader.calls, [TEST_MODEL, TEST_MODEL])

    def test_collider_visibility_toggle_sets_locator_shapes_only(self):
        refs = PhysicsSceneRefs(
            rigid_bodies=(
                _rigid("|root|rb2", 2, "skirt", locator_shape="|root|rb2|mmdRigidBodyLocator"),
                _rigid("|root|rb5", 5, "hair", locator_shape=None),
            ),
            joints=(),
        )
        adapter = _FakeMayaAdapter(existing_nodes={"|root|rb2|mmdRigidBodyLocator"})
        presenter, view, _, adapter, _ = _make_presenter(adapter=adapter, reader=_FakePhysicsReader(refs))
        presenter.load_physics()

        view.collider_visible_check.set_checked(False)

        self.assertIn(
            ("set_attr", (f"{TEST_MODEL}.mmd_show_physics_colliders", False), {}),
            adapter.calls,
        )
        self.assertIn(
            (
                "connect_attr",
                f"{TEST_MODEL}.mmd_show_physics_colliders",
                "|root|rb2|mmdRigidBodyLocator.drawEnabled",
                False,
            ),
            adapter.calls,
        )


if __name__ == "__main__":
    unittest.main()
