"""Selected ordinary camera routing for Maya VMD Bake Timeline export."""

import unittest

from mmd_tools.actions.bake_timeline_vmd_export_action import (
    BakeTimelineVmdExportError,
)
from mmd_tools.adapters.maya_vmd_prepare_backend import MayaVmdExportBackend


class _FakeCmds:
    def __init__(self):
        self.selection = []
        self.node_types = {}
        self.children = {}
        self.parents = {}
        self.tagged = []

    def ls(
        self,
        pattern=None,
        selection=False,
        long=False,
        objectsOnly=False,
        **_kwargs,
    ):
        if selection:
            return list(self.selection)
        if objectsOnly and pattern == "*.mmd_camera":
            return list(self.tagged)
        if pattern in self.node_types:
            return [pattern]
        return []

    def nodeType(self, node):  # noqa: N802
        return self.node_types.get(node)

    def listRelatives(  # noqa: N802
        self,
        node,
        parent=False,
        shapes=False,
        type=None,
        **_kwargs,
    ):
        if parent:
            value = self.parents.get(node)
            return [value] if value else []
        values = list(self.children.get(node, [])) if shapes else []
        return [value for value in values if type is None or self.node_types.get(value) == type]


class TestMayaVmdPrepareBackendCameraSelection(unittest.TestCase):
    def setUp(self):
        self.cmds = _FakeCmds()
        self.cmds.node_types.update(
            {
                "render_camera": "transform",
                "render_cameraShape": "camera",
            }
        )
        self.cmds.children["render_camera"] = ["render_cameraShape"]
        self.cmds.parents["render_cameraShape"] = "render_camera"
        self.backend = MayaVmdExportBackend(cmds_module=self.cmds)

    def test_resolves_selected_camera_transform_when_tag_is_missing(self):
        self.cmds.selection = ["render_camera"]

        result = self.backend._resolve_camera_options(
            {"export_target": "camera"}
        )

        self.assertEqual(result["cameras"], ["render_camera"])

    def test_resolves_selected_camera_shape_to_its_transform(self):
        self.cmds.selection = ["render_cameraShape"]

        result = self.backend._resolve_camera_options(
            {"export_target": "camera"}
        )

        self.assertEqual(result["cameras"], ["render_camera"])

    def test_keeps_tagged_camera_route_in_preference_to_selection(self):
        self.cmds.selection = ["render_camera"]
        self.cmds.node_types["mmd_camera"] = "transform"
        self.cmds.tagged = ["mmd_camera"]

        result = self.backend._resolve_camera_options(
            {"export_target": "camera"}
        )

        self.assertEqual(result["cameras"], ["mmd_camera"])

    def test_requires_exactly_one_selected_camera_without_tag(self):
        with self.assertRaisesRegex(
            BakeTimelineVmdExportError,
            "exactly one selected Maya camera",
        ):
            self.backend._resolve_camera_options({"export_target": "camera"})

    def test_character_only_export_does_not_require_camera_selection(self):
        result = self.backend._resolve_camera_options(
            {"export_target": "character"}
        )

        self.assertNotIn("cameras", result)

    def test_camera_export_options_allow_missing_current_model(self):
        result = self.backend._validated_options(
            {
                "export_strategy": "bake_timeline",
                "export_target": "camera",
            }
        )

        self.assertEqual(result["export_target"], "camera")

    def test_character_export_options_still_require_current_model(self):
        with self.assertRaisesRegex(
            BakeTimelineVmdExportError,
            "current_model_root is required for character VMD export",
        ):
            self.backend._validated_options(
                {
                    "export_strategy": "bake_timeline",
                    "export_target": "character",
                }
            )


if __name__ == "__main__":
    unittest.main()
