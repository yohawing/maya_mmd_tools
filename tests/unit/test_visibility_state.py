import unittest

from mmd_tools.core.constants import (
    ATTR_MMD_SHOW_JOINTS,
    ATTR_MMD_SHOW_MESH,
    ATTR_MMD_SHOW_PHYSICS_COLLIDERS,
)
from mmd_tools.core.visibility_state import (
    HIDDEN,
    REFERENCE,
    VISIBLE,
    VisibilityState,
    ensure_visibility_attrs,
    get_visibility_category,
    get_visibility_state,
    resolve_visibility_group,
    set_visibility_category,
    set_visibility_state,
    sync_visibility_connections,
)


class _FakeAdapter:
    def __init__(self, existing_attrs=None):
        self.attrs = dict(existing_attrs or {})
        self.calls = []
        self.relatives = {}
        self.connections = {}
        self.settable = {}

    def attribute_exists(self, attr, node):
        return (node, attr) in self.attrs

    def add_attr(self, node, **kwargs):
        self.calls.append(("add_attr", node, kwargs))
        self.attrs[(node, kwargs["longName"])] = False

    def delete_attr(self, attr_path):
        self.calls.append(("delete_attr", attr_path))
        node, attr = attr_path.rsplit(".", 1)
        self.attrs.pop((node, attr), None)

    def get_attr(self, attr_path):
        node, attr = attr_path.rsplit(".", 1)
        return self.attrs[(node, attr)]

    def set_attr(self, attr_path, value, **kwargs):
        self.calls.append(("set_attr", attr_path, value, kwargs))
        node, attr = attr_path.rsplit(".", 1)
        self.attrs[(node, attr)] = value

    def is_attr_settable(self, attr_path):
        return self.settable.get(attr_path, True)

    def list_relatives(self, node, **kwargs):
        return self.relatives.get((node, kwargs.get("type")), [])

    def list_connections(self, node, **kwargs):
        if kwargs.get("source") and kwargs.get("plugs"):
            return self.connections.get(node, [])
        return []

    def connect_attr(self, source, destination, force=False):
        self.calls.append(("connect_attr", source, destination, force))
        self.connections[destination] = [source]
        source_node, source_attr = source.rsplit(".", 1)
        destination_node, destination_attr = destination.rsplit(".", 1)
        if (source_node, source_attr) in self.attrs:
            self.attrs[(destination_node, destination_attr)] = self.attrs[
                (source_node, source_attr)
            ]


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

    def test_ensure_visibility_attrs_removes_discontinued_attrs(self):
        adapter = _FakeAdapter(
            {
                ("model_root", "mmd_show_ik"): True,
                ("model_root", "mmd_show_controllers"): True,
            }
        )

        ensure_visibility_attrs(adapter, "model_root")

        self.assertNotIn(("model_root", "mmd_show_ik"), adapter.attrs)
        self.assertNotIn(("model_root", "mmd_show_controllers"), adapter.attrs)

    def test_sync_mesh_and_joints_connects_direct_parent_groups(self):
        adapter = _FakeAdapter()
        adapter.relatives[("model_root", "transform")] = [
            "|model_root|Geometry",
            "|model_root|Skeleton",
            "|model_root|Physics",
        ]

        sync_visibility_connections(adapter, "model_root")

        self.assertIn(
            (
                "connect_attr",
                f"model_root.{ATTR_MMD_SHOW_MESH}",
                "|model_root|Geometry.visibility",
                False,
            ),
            adapter.calls,
        )
        self.assertIn(
            (
                "connect_attr",
                f"model_root.{ATTR_MMD_SHOW_JOINTS}",
                "|model_root|Skeleton.visibility",
                False,
            ),
            adapter.calls,
        )

    def _group_adapter(self, group="|model_root|Geometry"):
        adapter = _FakeAdapter(
            {
                ("model_root", ATTR_MMD_SHOW_MESH): True,
                (group, "visibility"): True,
                (group, "overrideEnabled"): False,
                (group, "overrideDisplayType"): 1,
            }
        )
        adapter.relatives[("model_root", "transform")] = [group]
        return adapter, group

    def test_get_visibility_state_hidden_wins_over_drawing_override(self):
        adapter, group = self._group_adapter()
        adapter.attrs[(group, "visibility")] = False
        adapter.attrs[(group, "overrideEnabled")] = True
        adapter.attrs[(group, "overrideDisplayType")] = 2

        self.assertIs(get_visibility_state(adapter, "model_root", "mesh"), HIDDEN)

    def test_get_visibility_state_requires_enabled_reference_override(self):
        adapter, group = self._group_adapter()
        adapter.attrs[(group, "overrideDisplayType")] = 2
        self.assertIs(get_visibility_state(adapter, "model_root", "mesh"), VISIBLE)

        adapter.attrs[(group, "overrideEnabled")] = True
        self.assertIs(get_visibility_state(adapter, "model_root", "mesh"), REFERENCE)

    def test_set_visibility_state_writes_root_authority_and_override(self):
        adapter, group = self._group_adapter()

        self.assertTrue(set_visibility_state(adapter, "model_root", "mesh", REFERENCE))
        self.assertTrue(adapter.attrs[("model_root", ATTR_MMD_SHOW_MESH)])
        self.assertTrue(adapter.attrs[(group, "overrideEnabled")])
        self.assertEqual(adapter.attrs[(group, "overrideDisplayType")], 2)

        self.assertTrue(set_visibility_state(adapter, "model_root", "mesh", VISIBLE))
        self.assertEqual(adapter.attrs[(group, "overrideDisplayType")], 0)

    def test_set_hidden_changes_only_root_authority(self):
        adapter, group = self._group_adapter()
        before = (
            adapter.attrs[(group, "overrideEnabled")],
            adapter.attrs[(group, "overrideDisplayType")],
        )

        self.assertTrue(set_visibility_state(adapter, "model_root", "mesh", HIDDEN))
        self.assertFalse(adapter.attrs[("model_root", ATTR_MMD_SHOW_MESH)])
        self.assertEqual(
            before,
            (
                adapter.attrs[(group, "overrideEnabled")],
                adapter.attrs[(group, "overrideDisplayType")],
            ),
        )

    def test_group_resolver_is_namespace_safe_and_fails_on_ambiguity(self):
        adapter, group = self._group_adapter("|model_root|char:Geometry")
        self.assertEqual(resolve_visibility_group(adapter, "model_root", "mesh"), group)

        adapter.relatives[("model_root", "transform")].append("|model_root|other:Geometry")
        self.assertIsNone(resolve_visibility_group(adapter, "model_root", "mesh"))
        self.assertIs(get_visibility_state(adapter, "model_root", "mesh"), VisibilityState.VISIBLE)
        self.assertFalse(set_visibility_state(adapter, "model_root", "mesh", HIDDEN))

    def test_bool_api_remains_root_attribute_compatible(self):
        adapter, _group = self._group_adapter()

        set_visibility_category(adapter, "model_root", "mesh", False)
        self.assertFalse(get_visibility_category(adapter, "model_root", "mesh"))
        set_visibility_category(adapter, "model_root", "mesh", True)
        self.assertTrue(get_visibility_category(adapter, "model_root", "mesh"))

    def test_state_writes_do_not_force_foreign_override_driver(self):
        adapter, group = self._group_adapter()
        adapter.connections[f"{group}.overrideEnabled"] = ["foreign.output"]

        self.assertFalse(set_visibility_state(adapter, "model_root", "mesh", REFERENCE))
        self.assertFalse(adapter.attrs[(group, "overrideEnabled")])
        self.assertEqual(adapter.attrs[(group, "overrideDisplayType")], 1)

    def test_state_writer_connects_unconnected_group_visibility(self):
        adapter, group = self._group_adapter()

        self.assertTrue(set_visibility_state(adapter, "model_root", "mesh", HIDDEN))
        self.assertEqual(
            adapter.connections[f"{group}.visibility"],
            [f"model_root.{ATTR_MMD_SHOW_MESH}"],
        )
        self.assertFalse(adapter.attrs[(group, "visibility")])
        self.assertIs(get_visibility_state(adapter, "model_root", "mesh"), HIDDEN)

    def test_foreign_group_visibility_fails_before_root_mutation(self):
        adapter, group = self._group_adapter()
        adapter.connections[f"{group}.visibility"] = ["foreign.output"]

        self.assertFalse(set_visibility_state(adapter, "model_root", "mesh", HIDDEN))
        self.assertTrue(adapter.attrs[("model_root", ATTR_MMD_SHOW_MESH)])
        self.assertTrue(adapter.attrs[(group, "visibility")])
        self.assertEqual(
            adapter.connections[f"{group}.visibility"], ["foreign.output"]
        )

    def test_locked_override_fails_before_root_mutation(self):
        adapter, group = self._group_adapter()
        adapter.settable[f"{group}.overrideDisplayType"] = False

        self.assertFalse(set_visibility_state(adapter, "model_root", "mesh", REFERENCE))
        self.assertTrue(adapter.attrs[("model_root", ATTR_MMD_SHOW_MESH)])
        self.assertFalse(adapter.attrs[(group, "overrideEnabled")])
        self.assertEqual(adapter.attrs[(group, "overrideDisplayType")], 1)


if __name__ == "__main__":
    unittest.main()
