"""VMD runtime bake routing and source resolution tests."""

import os
import tempfile
from unittest.mock import patch

import maya.cmds as cmds

import mmd_tools.converters.vmd_converter as vmd_converter_module
from mmd_tools.converters.vmd_converter import VmdConverter
from tests.common.maya_test_base import MayaTestBase
from tests.common.vmd_mock import create_test_vmd_data


class TestVmdRuntimeBakeRouting(MayaTestBase):
    """Runtime bake entrypoint, routing, and source recovery tests."""

    def setUp(self):
        super().setUp()
        self.converter = VmdConverter()

    def test_runtime_bake_infrastructure(self):
        """Phase 1 runtime bake のインフラテスト (native なし環境でも安全)"""
        vmd_data = create_test_vmd_data()
        self.converter.set_bone_name_mapping({"センター": "center"})

        res = self.converter.convert(vmd_data, vmd_bytes=b"dummy", pmx_bytes=None, pmx_path=None)
        self.assertIsInstance(res, bool)

        self.assertFalse(self.converter._should_use_mmd_runtime_bake(b"vmd", None, "/nonexistent.pmx"))

    def test_should_use_mmd_runtime_bake_accepts_bake_pmx_rejects_pmd(self):
        """Bake mode は PMX 入力で runtime bake を使い、PMD 入力では無効"""
        with tempfile.TemporaryDirectory() as temp_dir:
            pmx_path = os.path.join(temp_dir, "model.pmx")
            pmd_path = os.path.join(temp_dir, "model.pmd")
            open(pmx_path, "wb").close()
            open(pmd_path, "wb").close()

            with patch.object(vmd_converter_module, "HAS_MMD_RUNTIME", True), patch.object(
                vmd_converter_module,
                "is_mmd_runtime_available",
                return_value=True,
            ):
                self.assertTrue(
                    self.converter._should_use_mmd_runtime_bake(
                        vmd_bytes=b"vmd",
                        pmx_bytes=None,
                        pmx_path=pmx_path,
                        bake_mode=True,
                    )
                )
                self.assertFalse(
                    self.converter._should_use_mmd_runtime_bake(
                        vmd_bytes=b"vmd",
                        pmx_bytes=None,
                        pmx_path=pmd_path,
                        bake_mode=True,
                    )
                )
                self.assertTrue(
                    self.converter._should_use_mmd_runtime_bake(
                        vmd_bytes=b"vmd",
                        pmx_bytes=b"pmx",
                        pmx_path=pmd_path,
                        bake_mode=True,
                    )
                )

    def test_live_rig_target_uses_sparse_vmd_path(self):
        """Rig mode でも VMD import は runtime dense bake に逃げない"""
        joint = cmds.joint(name="runtime_live_rig_target_joint")
        ik_node = cmds.createNode("mmdCcdIk", name="runtime_live_rig_ik")
        cmds.connectAttr(f"{ik_node}.outputRotate[0]", f"{joint}.rotate", force=True)
        self.converter.bone_name_mapping = {"左足ＩＫ": joint}

        with tempfile.TemporaryDirectory() as temp_dir:
            pmx_path = os.path.join(temp_dir, "model.pmx")
            open(pmx_path, "wb").close()

            with patch.object(vmd_converter_module, "HAS_MMD_RUNTIME", True), patch.object(
                vmd_converter_module,
                "is_mmd_runtime_available",
                return_value=True,
            ):
                self.assertFalse(
                    self.converter._should_use_mmd_runtime_bake(
                        vmd_bytes=b"vmd",
                        pmx_bytes=None,
                        pmx_path=pmx_path,
                        live_rig_target=True,
                    )
                )

        cmds.delete(ik_node, joint)

    def test_resolve_runtime_bake_sources_uses_vmd_source_file_and_scene_pmx(self):
        """convert 直呼びでも VmdData.source_file と model root の mmd_source_file から runtime 入力を復元する"""
        with tempfile.TemporaryDirectory() as temp_dir:
            vmd_path = os.path.join(temp_dir, "motion.vmd")
            pmx_path = os.path.join(temp_dir, "model.pmx")
            with open(vmd_path, "wb") as file:
                file.write(b"vmd-bytes")
            with open(pmx_path, "wb") as file:
                file.write(b"pmx-bytes")

            root = cmds.group(empty=True, name="runtime_source_model_root")
            cmds.addAttr(root, longName="mmd_source_file", dataType="string")
            cmds.setAttr(f"{root}.mmd_source_file", pmx_path, type="string")
            vmd_data = create_test_vmd_data()
            vmd_data.source_file = vmd_path

            vmd_bytes, pmx_bytes, resolved_pmx_path = self.converter._resolve_runtime_bake_sources(
                vmd_data,
                vmd_bytes=None,
                pmx_bytes=None,
                pmx_path=None,
            )

            self.assertEqual(vmd_bytes, b"vmd-bytes")
            self.assertIsNone(pmx_bytes)
            self.assertEqual(resolved_pmx_path, pmx_path)

            cmds.delete(root)
