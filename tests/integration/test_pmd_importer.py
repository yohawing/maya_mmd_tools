"""PMD import pipeline integration smoke tests."""

from __future__ import annotations

import os

from maya import cmds

from mmd_tools.core import settings
from mmd_tools.core.constants import (
    ATTR_MMD_AMBIENT_COLOR,
    ATTR_MMD_BONE_INDEX,
    ATTR_MMD_BONE_NAME,
    ATTR_MMD_DIFFUSE_COLOR,
    ATTR_MMD_EDGE_FLAG,
    ATTR_MMD_MATERIAL,
    ATTR_MMD_MATERIAL_INDEX,
    ATTR_MMD_MATERIAL_NAME,
    ATTR_MMD_SHININESS,
    ATTR_MMD_SPECULAR_COLOR,
    ATTR_MMD_TOON_TEXTURE_INDEX,
)
from mmd_tools.converters.material_shader_parameters import ATTR_MMD_DIFFUSE_ALPHA
from mmd_tools.core.pmd_data import PmdData
from mmd_tools.core.vmd_data import VmdData
from mmd_tools.core.vmd_data.bone_frame import VmdBoneFrame
from mmd_tools.io.mmd_importer import import_mmd_file
from tests.common.maya_test_base import MayaTestBase
from tests.common.pmd_mock import PmdMock


class TestPmdImporter(MayaTestBase):
    """PMD model import plus VMD conversion smoke tests."""

    def setUp(self):
        super().setUp()
        settings.set("import.model.create_mmd_shaders", False)
        settings.set("import.physics.import_physics", False)
        settings.set("import.light.create_controller", False)

    def _write_full_pmd(self, file_name: str = "mock_full.pmd") -> str:
        path = self.get_temp_filename(file_name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(PmdMock.create_full_pmd())
        return path

    def _write_vmd_for_first_bone(self, root: str, frame_number: int = 10) -> str:
        joints = cmds.listRelatives(root, allDescendents=True, type="joint") or []
        indexed = []
        for joint in joints:
            if not cmds.attributeQuery(ATTR_MMD_BONE_INDEX, node=joint, exists=True):
                continue
            indexed.append((int(cmds.getAttr(f"{joint}.{ATTR_MMD_BONE_INDEX}")), joint))
        self.assertTrue(indexed, "PMD import did not create indexed MMD joints")

        _bone_index, joint = sorted(indexed)[0]
        bone_name = cmds.getAttr(f"{joint}.{ATTR_MMD_BONE_NAME}")
        self.assertTrue(bone_name)

        frame0 = VmdBoneFrame()
        frame0.bone_name = bone_name
        frame0.frame_number = 0
        frame0.position = (0.0, 0.0, 0.0)
        frame0.rotation = (0.0, 0.0, 0.0, 1.0)

        frame1 = VmdBoneFrame()
        frame1.bone_name = bone_name
        frame1.frame_number = frame_number
        frame1.position = (1.0, 2.0, 3.0)
        frame1.rotation = (0.0, 0.0, 0.0, 1.0)

        vmd = VmdData()
        vmd.bone_frames = [frame0, frame1]

        path = self.get_temp_filename("mock_motion.vmd")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        vmd.write_file(path)
        return path

    def test_full_pmd_import_creates_mesh_material_morph_bones_and_skin(self):
        """Full PMD mock import creates the representative model pipeline nodes."""
        pmd_path = self._write_full_pmd()

        root = import_mmd_file(
            pmd_path,
            options={
                "import_physics": False,
                "create_mmd_shaders": False,
            },
        )

        self.assertTrue(root and cmds.objExists(root), "PMD import did not create a root")
        mesh_shapes = cmds.listRelatives(root, allDescendents=True, type="mesh") or []
        self.assertTrue(mesh_shapes, "PMD import did not create mesh shapes")
        self.assertTrue(cmds.ls(type="joint"), "PMD import did not create joints")
        self.assertTrue(cmds.ls(type="skinCluster"), "PMD import did not create a skinCluster")
        self.assertTrue(cmds.ls(type="blendShape"), "PMD import did not create morph blendShapes")

        scene_materials = set(cmds.ls(materials=True) or [])
        default_materials = {"lambert1", "standardSurface1", "particleCloud1"}
        self.assertTrue(
            scene_materials - default_materials,
            "PMD import did not create material assignments",
        )

    def test_full_pmd_import_preserves_material_semantic_attributes(self):
        """PMD material fields remain readable from imported MMD shader nodes."""
        pmd_path = self._write_full_pmd("material_semantics.pmd")
        source = PmdData().parse_file(pmd_path)

        root = import_mmd_file(
            pmd_path,
            options={
                "import_physics": False,
                "create_mmd_shaders": False,
            },
        )
        self.assertTrue(root and cmds.objExists(root))

        shaders = [
            node
            for node in (cmds.ls() or [])
            if cmds.attributeQuery(ATTR_MMD_MATERIAL, node=node, exists=True)
            and cmds.getAttr(f"{node}.{ATTR_MMD_MATERIAL}")
        ]
        shaders.sort(key=lambda node: int(cmds.getAttr(f"{node}.{ATTR_MMD_MATERIAL_INDEX}")))
        self.assertEqual(len(shaders), len(source.materials))

        for shader, material in zip(shaders, source.materials):
            self.assertEqual(
                cmds.getAttr(f"{shader}.{ATTR_MMD_MATERIAL_INDEX}"),
                material.material_index,
            )
            self.assertEqual(
                cmds.getAttr(f"{shader}.{ATTR_MMD_MATERIAL_NAME}"),
                material.name,
            )
            self.assertListAlmostEqual(
                cmds.getAttr(f"{shader}.{ATTR_MMD_DIFFUSE_COLOR}")[0],
                material.diffuse[:3],
                places=5,
            )
            self.assertAlmostEqual(
                cmds.getAttr(f"{shader}.{ATTR_MMD_DIFFUSE_ALPHA}"),
                material.diffuse[3],
                places=5,
            )
            for attr, expected in (
                (ATTR_MMD_SPECULAR_COLOR, material.specular),
                (ATTR_MMD_AMBIENT_COLOR, material.ambient),
            ):
                self.assertListAlmostEqual(
                    cmds.getAttr(f"{shader}.{attr}")[0],
                    expected,
                    places=5,
                )
            self.assertAlmostEqual(
                cmds.getAttr(f"{shader}.{ATTR_MMD_SHININESS}"),
                material.specular_power,
                places=5,
            )
            self.assertEqual(
                cmds.getAttr(f"{shader}.{ATTR_MMD_TOON_TEXTURE_INDEX}"),
                material.toon_texture_index,
            )
            self.assertEqual(
                cmds.getAttr(f"{shader}.{ATTR_MMD_EDGE_FLAG}"),
                material.edge_flag,
            )

    def test_full_pmd_accepts_vmd_bone_motion(self):
        """Imported PMD joints can receive VMD bone keyframes."""
        pmd_path = self._write_full_pmd()
        root = import_mmd_file(pmd_path, options={"import_physics": False})
        self.assertTrue(root and cmds.objExists(root))

        vmd_path = self._write_vmd_for_first_bone(root)
        result = import_mmd_file(
            vmd_path,
            options={
                "target_model": root,
                "bake_mode": False,
            },
        )
        self.assertTrue(result, "VMD import failed for imported PMD model")

        indexed_joints = []
        for joint in cmds.listRelatives(root, allDescendents=True, type="joint") or []:
            if cmds.attributeQuery(ATTR_MMD_BONE_INDEX, node=joint, exists=True):
                indexed_joints.append((int(cmds.getAttr(f"{joint}.{ATTR_MMD_BONE_INDEX}")), joint))
        target_joint = sorted(indexed_joints)[0][1]

        keys = cmds.keyframe(f"{target_joint}.translateX", query=True, timeChange=True) or []
        self.assertIn(10.0, keys)
