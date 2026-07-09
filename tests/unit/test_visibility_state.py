import unittest

from mmd_tools.core.constants import ATTR_MMD_SHOW_PHYSICS_COLLIDERS
from mmd_tools.core.visibility_state import ensure_visibility_attrs


class _FakeAdapter:
    def __init__(self, existing_attrs=None):
        self.attrs = dict(existing_attrs or {})
        self.calls = []

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


if __name__ == "__main__":
    unittest.main()
