import unittest

from mmd_tools.core.constants import ATTR_MMD_SHOW_PHYSICS_COLLIDERS
from mmd_tools.core.visibility_state import ensure_visibility_attrs, sync_visibility_connections


class _FakeAdapter:
    def __init__(self, existing_attrs=None):
        self.attrs = dict(existing_attrs or {})
        self.calls = []
        self.relatives = {}
        self.connections = {}

    def attribute_exists(self, attr, node):
        return (node, attr) in self.attrs

    def add_attr(self, node, **kwargs):
        self.calls.append(("add_attr", node, kwargs))
        self.attrs[(node, kwargs["longName"])] = False

    def get_attr(self, attr_path):
        node, attr = attr_path.rsplit(".", 1)
        return self.attrs[(node, attr)]

    def set_attr(self, attr_path, value, **kwargs):
        self.calls.append(("set_attr", attr_path, value, kwargs))
        node, attr = attr_path.rsplit(".", 1)
        self.attrs[(node, attr)] = value

    def list_relatives(self, node, **kwargs):
        return self.relatives.get((node, kwargs.get("type")), [])

    def list_connections(self, node, **kwargs):
        if kwargs.get("source") and kwargs.get("plugs"):
            return self.connections.get(node, [])
        return []

    def connect_attr(self, source, destination, force=False):
        self.calls.append(("connect_attr", source, destination, force))
        self.connections[destination] = [source]


class TestVisibilityState(unittest.TestCase):
    def test_ensure_visibility_attrs_creates_keyable_root_attrs(self):
        adapter = _FakeAdapter()

        ensure_visibility_attrs(adapter, "model_root")

        add_calls = [call for call in adapter.calls if call[0] == "add_attr"]
        self.assertTrue(add_calls)
        self.assertTrue(all(call[2].get("keyable") is True for call in add_calls))
        self.assertIn(("model_root", ATTR_MMD_SHOW_PHYSICS_COLLIDERS), adapter.attrs)

    def test_ensure_visibility_attrs_makes_existing_root_attrs_keyable(self):
        adapter = _FakeAdapter({("model_root", ATTR_MMD_SHOW_PHYSICS_COLLIDERS): True})

        ensure_visibility_attrs(adapter, "model_root")

        self.assertIn(
            (
                "set_attr",
                f"model_root.{ATTR_MMD_SHOW_PHYSICS_COLLIDERS}",
                True,
                {"keyable": True},
            ),
            adapter.calls,
        )

    def test_sync_colliders_connects_physics_group_locator_and_curve(self):
        adapter = _FakeAdapter({("model_root", ATTR_MMD_SHOW_PHYSICS_COLLIDERS): False})
        adapter.relatives[("model_root", "mmdRigidBodyLocator")] = ["|model_root|rb|rb_colliderLocatorShape"]
        adapter.relatives[("model_root", "transform")] = [
            "|model_root|Physics",
            "|model_root|rb",
            "|model_root|rb|rb_colliderCurve",
        ]

        sync_visibility_connections(adapter, "model_root", "colliders")

        self.assertIn(
            (
                "connect_attr",
                f"model_root.{ATTR_MMD_SHOW_PHYSICS_COLLIDERS}",
                "|model_root|Physics.visibility",
                False,
            ),
            adapter.calls,
        )
        self.assertIn(
            (
                "connect_attr",
                f"model_root.{ATTR_MMD_SHOW_PHYSICS_COLLIDERS}",
                "|model_root|rb|rb_colliderLocatorShape.drawEnabled",
                False,
            ),
            adapter.calls,
        )
        self.assertIn(
            (
                "connect_attr",
                f"model_root.{ATTR_MMD_SHOW_PHYSICS_COLLIDERS}",
                "|model_root|rb|rb_colliderCurve.visibility",
                False,
            ),
            adapter.calls,
        )

    def test_sync_colliders_does_not_overwrite_existing_physics_visibility_source(self):
        adapter = _FakeAdapter({("model_root", ATTR_MMD_SHOW_PHYSICS_COLLIDERS): False})
        adapter.relatives[("model_root", "transform")] = ["|model_root|Physics"]
        adapter.connections["|model_root|Physics.visibility"] = ["other_driver.outValue"]

        sync_visibility_connections(adapter, "model_root", "colliders")

        connect_calls = [call for call in adapter.calls if call[0] == "connect_attr"]
        self.assertEqual(connect_calls, [])
        self.assertEqual(
            adapter.connections["|model_root|Physics.visibility"],
            ["other_driver.outValue"],
        )
        self.assertFalse(any(call[0] == "connect_attr" and call[3] is True for call in adapter.calls))


if __name__ == "__main__":
    unittest.main()
