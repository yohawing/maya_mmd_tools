"""VMD morph mapping tests."""

import json

import maya.cmds as cmds

from mmd_tools.converters.vmd_converter import VmdConverter
from mmd_tools.core.constants import ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON
from tests.common.maya_test_base import MayaTestBase


class TestVmdMorphMapping(MayaTestBase):
    """Morph mapping tests for VMD import."""

    def setUp(self):
        super().setUp()
        self.converter = VmdConverter()

    def test_build_morph_mappings(self):
        """BlendShape aliases are collected into morph mappings."""
        cube = cmds.polyCube(name="test_mesh")[0]
        blend_shape = cmds.blendShape(cube, name="test_blendShape")[0]

        for i, morph_name in enumerate(["mabataki", "egao", "wink"]):
            target = cmds.duplicate(cube)[0]
            cmds.move(i + 1, 0, 0, f"{target}.vtx[*]", relative=True)
            cmds.blendShape(blend_shape, edit=True, target=(cube, i, target, 1.0))
            cmds.aliasAttr(morph_name, f"{blend_shape}.weight[{i}]")
            cmds.delete(target)

        self.converter._build_morph_mappings()

        self.assertGreaterEqual(len(self.converter.morph_name_mapping), 3)
        self.assertIn("mabataki", self.converter.morph_name_mapping)
        self.assertIn("egao", self.converter.morph_name_mapping)
        self.assertIn("wink", self.converter.morph_name_mapping)

        cmds.delete(cube)

    def test_invalid_controller_topology_is_reported_instead_of_silently_empty(self):
        root = cmds.group(empty=True, name="invalid_topology_root")
        controller = cmds.createNode("network", name="invalid_topology_controller")
        cmds.addAttr(controller, longName="topologyVersion", attributeType="long")
        cmds.addAttr(controller, longName="groupTopology", dataType="string")
        cmds.setAttr(f"{controller}.topologyVersion", 2)
        cmds.setAttr(f"{controller}.groupTopology", "{}", type="string")
        cmds.addAttr(root, longName="mmd_morph_controller", attributeType="message")
        cmds.connectAttr(f"{controller}.message", f"{root}.mmd_morph_controller")

        with self.assertRaisesRegex(RuntimeError, "morph_topology:version"):
            self.converter._build_morph_mappings(root)

        self.assertTrue(
            any(value.startswith("morph_topology:version:") for value in self.converter.morph_binding_diagnostics)
        )
        cmds.delete(root)

    def test_ambiguous_controller_ownership_is_reported(self):
        root = cmds.group(empty=True, name="ambiguous_topology_root")
        controllers = [
            cmds.createNode("network", name=f"ambiguous_topology_controller_{index}")
            for index in range(2)
        ]
        cmds.addAttr(root, longName="mmd_morph_controller", attributeType="message")
        for index, controller in enumerate(controllers):
            attr = f"controller{index}"
            cmds.addAttr(root, longName=attr, attributeType="message")
            cmds.connectAttr(f"{controller}.message", f"{root}.{attr}")
        # Connect both sources through a multi message boundary matching a
        # corrupted legacy scene.
        cmds.deleteAttr(f"{root}.mmd_morph_controller")
        cmds.addAttr(
            root, longName="mmd_morph_controller", attributeType="message", multi=True
        )
        for index, controller in enumerate(controllers):
            cmds.connectAttr(
                f"{controller}.message", f"{root}.mmd_morph_controller[{index}]"
            )

        with self.assertRaisesRegex(
            RuntimeError, "morph_topology:malformed:controller ownership"
        ):
            self.converter._build_morph_mappings(root)

        self.assertTrue(
            any(
                value.startswith("morph_topology:malformed:controller ownership")
                for value in self.converter.morph_binding_diagnostics
            )
        )
        cmds.delete(root)

    def test_build_morph_mappings_adds_original_japanese_names(self):
        """Dictionary-converted aliases are also reachable by original VMD names."""
        cube = cmds.polyCube(name="test_mesh_jp_morph")[0]
        blend_shape = cmds.blendShape(cube, name="test_blendShape_jp_morph")[0]

        target = cmds.duplicate(cube)[0]
        cmds.move(1, 0, 0, f"{target}.vtx[*]", relative=True)
        cmds.blendShape(blend_shape, edit=True, target=(cube, 0, target, 1.0))
        cmds.aliasAttr("blink", f"{blend_shape}.weight[0]")
        cmds.delete(target)

        self.converter._build_morph_mappings()

        self.assertIn("blink", self.converter.morph_name_mapping)
        self.assertIn("まばたき", self.converter.morph_name_mapping)
        self.assertEqual(
            self.converter._iter_morph_mappings(self.converter.morph_name_mapping["まばたき"]),
            self.converter._iter_morph_mappings(self.converter.morph_name_mapping["blink"]),
        )

        cmds.delete(cube)

    def test_build_morph_mappings_uses_stored_raw_names_without_contamination(self):
        """Stored raw morph names avoid lossy alias reverse-lookup contamination."""
        root = cmds.group(empty=True, name="target_model")
        cube = cmds.polyCube(name="test_mesh_stored_morph")[0]
        cmds.parent(cube, root)
        blend_shape = cmds.blendShape(cube, name="test_blendShape_stored_morph")[0]

        for i, alias in enumerate(["grin", "grin_1"]):
            target = cmds.duplicate(cube)[0]
            cmds.move(i + 1, 0, 0, f"{target}.vtx[*]", relative=True)
            cmds.blendShape(blend_shape, edit=True, target=(cube, i, target, 1.0))
            cmds.aliasAttr(alias, f"{blend_shape}.weight[{i}]")
            cmds.delete(target)

        cmds.addAttr(blend_shape, longName=ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON, dataType="string")
        cmds.setAttr(
            f"{blend_shape}.{ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON}",
            json.dumps(
                {"0": {"name": "にっこり", "index": 7}, "1": {"name": "にやり", "index": 8}},
                ensure_ascii=False,
            ),
            type="string",
        )
        controller = cmds.createNode("network", name="target_morphController")
        cmds.addAttr(controller, longName="inputWeight", attributeType="double", multi=True, keyable=True)
        cmds.addAttr(controller, longName="topologyVersion", attributeType="long")
        cmds.setAttr(f"{controller}.topologyVersion", 1)
        cmds.addAttr(controller, longName="groupTopology", dataType="string")
        cmds.setAttr(f"{controller}.groupTopology", "{}", type="string")
        cmds.addAttr(root, longName="mmd_morph_controller", attributeType="message")
        cmds.connectAttr(f"{controller}.message", f"{root}.mmd_morph_controller")

        self.converter._build_morph_mappings(root)

        nikkori = self.converter._iter_morph_mappings(self.converter.morph_name_mapping.get("にっこり"))
        niyari = self.converter._iter_morph_mappings(self.converter.morph_name_mapping.get("にやり"))

        self.assertEqual(len(nikkori), 1)
        self.assertEqual(nikkori[0][1], "inputWeight[7]")
        self.assertEqual(len(niyari), 1)
        self.assertEqual(niyari[0][1], "inputWeight[8]")
        self.assertNotEqual(nikkori[0][1], niyari[0][1])
        self.assertNotIn("blink", self.converter.morph_name_mapping)

        cmds.delete(root)
