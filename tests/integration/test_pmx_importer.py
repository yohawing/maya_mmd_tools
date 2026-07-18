"""
PMXインポーターの統合テスト
"""

import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from maya import cmds

from tests.common.maya_test_base import MayaTestBase
from tests.common.test_fixture_provider import TestFixtureProvider
from mmd_tools.core.constants import ATTR_MMD_DISPLAY_FRAMES_JSON
from mmd_tools.core.exceptions import MMDImportException
from mmd_tools.core.native.mmd_anim_runtime import is_native_physics_available
from mmd_tools.core.pmx_data.bone import PmxBoneFlag
from mmd_tools.io import pmx_importer
from mmd_tools.io.pmx_importer import import_pmx_file
from mmd_tools.core.mmd_parser import MMDParseException, parse_pmx_file


# Internal phase details (DEBUG); outer start/completion stay INFO.
_PMX_INTERNAL_PHASE_MESSAGES = (
    "Converting mesh...",
    "Converting morphs...",
    "Converting bones...",
    "Building bone morph runtime graph...",
    "Building material morph runtime graph...",
)


def _message_templates(mock_log):
    # call[0] is args tuple (Py3.7-safe; _Call.args is 3.8+)
    return [call[0][0] for call in mock_log.call_args_list if call[0]]


class TestPmxImporter(MayaTestBase):
    """PMXインポーターの統合テストクラス"""

    def setUp(self):
        """テストのセットアップ"""
        super().setUp()

        # dx11Shaderの作成を無効化（テスト環境では利用できない場合があるため）
        from mmd_tools.core import settings

        settings.set("import.model.create_mmd_shaders", False)

        self.fixture_provider = TestFixtureProvider()

        # テスト用の一時ファイル
        self.temp_files = []

    def tearDown(self):
        """テストのクリーンアップ"""
        # 一時ファイルの削除
        for temp_file in self.temp_files:
            if os.path.exists(temp_file):
                os.remove(temp_file)

        # TestFixtureProviderの一時ファイルをクリーンアップ
        self.fixture_provider.cleanup_temp_files()

        super().tearDown()

    def test_import_pmx_basic(self):
        """基本的なPMXファイルのインポートテスト"""
        pmx_file = self.fixture_provider.get_pmx_file("mmt_test_model")

        # PMXファイルをパース
        parser = parse_pmx_file(pmx_file)

        # インポート前のシーン状態を記録
        initial_nodes = set(cmds.ls())

        # PMXファイルをインポート（内部 phase ログ境界も検証）
        with patch.object(pmx_importer, "logger") as mock_logger:
            result = import_pmx_file(parser, pmx_file)

        # インポートが成功したことを確認
        self.assertTrue(result)

        # 新しく作成されたノードを確認
        new_nodes = set(cmds.ls()) - initial_nodes
        self.assertGreater(len(new_nodes), 0, "新しいノードが作成されていません")

        # メッシュが作成されたことを確認
        meshes = cmds.ls(type="mesh")
        self.assertGreater(len(meshes), 0, "メッシュが作成されていません")

        # ジョイントが作成されたことを確認
        joints = cmds.ls(type="joint")
        self.assertGreater(len(joints), 0, "ジョイントが作成されていません")

        self.assertTrue(
            cmds.attributeQuery(ATTR_MMD_DISPLAY_FRAMES_JSON, node=result, exists=True),
            "表示枠 metadata が root に保存されていません",
        )
        self.assertTrue(cmds.getAttr(f"{result}.{ATTR_MMD_DISPLAY_FRAMES_JSON}"))

        # テクスチャ file ノードがあれば、パスが有効であることを確認
        file_nodes = cmds.ls(type="file") or []
        for file_node in file_nodes:
            texture_path = cmds.getAttr(f"{file_node}.fileTextureName")
            self.assertTrue(texture_path, f"{file_node}.fileTextureName が空です")

        # Internal phase routing details are DEBUG, not INFO.
        debug_messages = _message_templates(mock_logger.debug)
        info_messages = _message_templates(mock_logger.info)
        for message in _PMX_INTERNAL_PHASE_MESSAGES:
            self.assertIn(message, debug_messages)
            self.assertNotIn(message, info_messages)

        # Outer import boundaries stay INFO.
        self.assertTrue(
            any(
                isinstance(msg, str) and msg.startswith("Starting PMX file import:")
                for msg in info_messages
            )
        )
        self.assertTrue(
            any(
                isinstance(msg, str) and msg.startswith("PMX file import completed:")
                for msg in info_messages
            )
        )

    def test_local_axis_scale_preserves_real_import_world_positions(self):
        """Real PMX import keeps LOCAL_AXIS bone positions at each import scale."""
        pmx_file = self.fixture_provider.get_pmx_file("mmt_test_model")
        bone_index = 1
        for scale in (0.1, 1.0, 10.0):
            with self.subTest(scale=scale):
                cmds.file(new=True, force=True)
                parser = parse_pmx_file(pmx_file)
                bone = parser.bones[bone_index]
                bone.bone_flag |= PmxBoneFlag.LOCAL_AXIS
                bone.x_axis_direction = (4.0, 0.0, 0.0)
                bone.z_axis_direction = (0.0, 3.0, 0.0)

                result = import_pmx_file(
                    parser,
                    pmx_file,
                    scale=scale,
                    options={"setup_rig": False, "import_physics": False},
                )

                self.assertTrue(result)
                matches = [
                    joint
                    for joint in (cmds.ls(type="joint", long=True) or [])
                    if cmds.attributeQuery("mmd_bone_index", node=joint, exists=True)
                    and cmds.getAttr(f"{joint}.mmd_bone_index") == bone_index
                ]
                self.assertEqual(len(matches), 1)
                world_pos = cmds.xform(matches[0], query=True, worldSpace=True, translation=True)
                expected = (
                    bone.position[0] * scale,
                    bone.position[1] * scale,
                    -bone.position[2] * scale,
                )
                for actual, expected_value in zip(world_pos, expected):
                    self.assertAlmostEqual(actual, expected_value, places=5)

    def test_invalid_local_axis_fails_before_real_import_scene_mutation(self):
        """Importer rejects degenerate LOCAL_AXIS before root or mesh creation."""
        pmx_file = self.fixture_provider.get_pmx_file("mmt_test_model")
        parser = parse_pmx_file(pmx_file)
        bone = parser.bones[1]
        bone.bone_flag |= PmxBoneFlag.LOCAL_AXIS
        bone.x_axis_direction = (0.0, 0.0, 0.0)
        bone.z_axis_direction = (0.0, 0.0, 1.0)
        nodes_before = set(cmds.ls(long=True) or [])

        with self.assertRaisesRegex(MMDImportException, "Invalid LOCAL_AXIS"):
            import_pmx_file(parser, pmx_file, options={"import_physics": False})

        self.assertEqual(set(cmds.ls(long=True) or []), nodes_before)

    def test_import_continues_when_bone_morph_runtime_is_unavailable(self):
        """mmdBoneMorphAccum 不可時も PMX import は継続し profile に structured warning を残す。"""
        pmx_file = self.fixture_provider.get_pmx_file("mmt_test_model")
        parser = parse_pmx_file(pmx_file)
        profile = {}
        unavailable_result = {
            "success": False,
            "accumulator_nodes": [],
            "created": 0,
            "reused": 0,
            "contributions": 0,
            "skipped": ["node_type_unavailable"],
            "warnings": [
                {
                    "code": "node_type_unavailable",
                    "reason": "node_type_unavailable",
                    "node_type": "mmdBoneMorphAccum",
                    "detail": "create_failed: simulated",
                    "missing_attributes": [],
                    "actual_type": "",
                }
            ],
        }

        with patch.object(
            pmx_importer,
            "build_bone_morph_graph",
            return_value=unavailable_result,
        ) as mock_build:
            result = import_pmx_file(
                parser,
                pmx_file,
                options={"profile": profile, "import_physics": False},
            )

        self.assertTrue(result)
        mock_build.assert_called_once()
        runtime_profile = profile.get("bone_morph_runtime") or {}
        self.assertFalse(runtime_profile.get("success"))
        self.assertIn("node_type_unavailable", runtime_profile.get("skipped") or [])
        warnings = runtime_profile.get("warnings") or []
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0].get("code"), "node_type_unavailable")
        self.assertEqual(warnings[0].get("reason"), "node_type_unavailable")
        self.assertTrue(cmds.objExists(result))

    def test_material_morph_graph_builds_after_dx11_uniform_sync(self):
        """material morph graph は dx11 uniform sync の後にだけ構築される。"""
        pmx_file = self.fixture_provider.get_pmx_file("mmt_test_model")
        parser = parse_pmx_file(pmx_file)
        call_order = []

        real_sync = pmx_importer.ModelImportPipeline.sync_dx11_uniforms
        real_build = pmx_importer.build_material_morph_graph

        def _tracking_sync(self, mesh_converter, refresh_if_dx11=False):
            call_order.append("sync_dx11")
            return real_sync(self, mesh_converter, refresh_if_dx11=refresh_if_dx11)

        def _tracking_build(root_group):
            call_order.append("material_morph")
            return real_build(root_group)

        with patch.object(
            pmx_importer.ModelImportPipeline,
            "sync_dx11_uniforms",
            _tracking_sync,
        ), patch.object(
            pmx_importer,
            "build_material_morph_graph",
            side_effect=_tracking_build,
        ):
            result = import_pmx_file(
                parser,
                pmx_file,
                options={"import_physics": False},
            )

        self.assertTrue(result)
        self.assertIn("sync_dx11", call_order)
        self.assertIn("material_morph", call_order)
        self.assertLess(
            call_order.index("sync_dx11"),
            call_order.index("material_morph"),
            f"expected sync before material morph, got {call_order}",
        )
        # Single build, no accidental re-entry after wire/setup.
        self.assertEqual(call_order.count("material_morph"), 1)
        self.assertEqual(call_order.count("sync_dx11"), 1)

    def test_import_pmx_with_physics_disabled_keeps_display_metadata(self):
        """import_physics=False では物理 node を作らず表示枠 metadata は保持する。"""
        cmds.file(new=True, force=True)
        pmx_file = self.fixture_provider.get_pmx_file("mmt_test_model")
        parser = parse_pmx_file(pmx_file)

        result = import_pmx_file(parser, pmx_file, options={"import_physics": False})

        self.assertTrue(result)
        self.assertTrue(
            cmds.attributeQuery(ATTR_MMD_DISPLAY_FRAMES_JSON, node=result, exists=True),
            "表示枠 metadata が root に保存されていません",
        )
        self.assertTrue(cmds.getAttr(f"{result}.{ATTR_MMD_DISPLAY_FRAMES_JSON}"))
        self.assertGreater(
            len(cmds.listRelatives(result, allDescendents=True, type="joint", fullPath=True) or []),
            0,
            "physics disabled import でも skeleton は作成されるはずです",
        )
        child_names = {
            child.rsplit("|", 1)[-1]
            for child in (cmds.listRelatives(result, children=True, fullPath=True) or [])
        }
        self.assertNotIn("Physics", child_names)

    @unittest.skipUnless(is_native_physics_available(), "native physics DLL not available")
    def test_import_pmx_with_physics_builds_disabled_passthrough_graph(self):
        """Physics import wires the graph but leaves World OFF and passes through pre-pose."""
        plugin_path = str(Path(__file__).resolve().parents[2] / "mmd_tools" / "plugin_main.py")
        try:
            self.load_plugin(plugin_path)
        except Exception:
            # The plugin may already be loaded by another integration class.
            pass

        pmx_file = str(Path(__file__).resolve().parents[1] / "data" / "physics" / "test_hair_physics.pmx")
        parser = parse_pmx_file(pmx_file)
        dynamic_bones = {
            int(rb.related_bone_index)
            for rb in parser.rigid_bodies
            if int(rb.physics_mode) in (1, 2) and int(rb.related_bone_index) >= 0
        }
        self.assertGreater(len(dynamic_bones), 0, "fixture has no dynamic rigid bodies")

        root = import_pmx_file(
            parser,
            pmx_file,
            options={"import_physics": True, "create_mmd_shaders": False},
        )

        solvers = cmds.ls(type="mmdPhysicsSolver") or []
        drivers = cmds.ls(type="mmdPhysicsBoneDriver") or []
        world_shapes = cmds.ls(type="mmdPhysicsWorldShape", long=True) or []
        self.assertEqual(len(solvers), 1)
        self.assertGreater(len(drivers), 0)
        self.assertEqual(world_shapes, ["|MMD_PhysicsWorld|MMD_PhysicsWorldShape"])
        self.assertFalse(cmds.getAttr(f"{world_shapes[0]}.enable"))
        self.assertTrue(
            root in (cmds.listConnections(f"{solvers[0]}.modelRoot", source=True, destination=False) or [])
        )
        self.assertTrue(cmds.listConnections(f"{solvers[0]}.inTime", source=True, destination=False))
        self.assertTrue(
            cmds.isConnected(
                f"{world_shapes[0]}.message",
                f"{solvers[0]}.inWorldSettings",
            )
        )
        self.assertTrue(
            cmds.isConnected(
                f"{world_shapes[0]}.outSettingsVersion",
                f"{solvers[0]}.inWorldSettingsVersion",
            )
        )
        for driver in drivers:
            target_joint = cmds.getAttr(f"{driver}.mmd_target_joint")
            for source, destination in (
                (f"{solvers[0]}.outBoneMatrices", f"{driver}.inSolverBoneMatrices"),
                (f"{solvers[0]}.outBoneCount", f"{driver}.inSolverBoneCount"),
                (f"{solvers[0]}.outSolved", f"{driver}.inSolved"),
                (f"{driver}.outTranslate", f"{target_joint}.translate"),
                (f"{driver}.outRotate", f"{target_joint}.rotate"),
            ):
                self.assertTrue(
                    cmds.isConnected(source, destination),
                    f"missing physics graph connection: {source} -> {destination}",
                )

        driver = drivers[0]
        target_joint = cmds.getAttr(f"{driver}.mmd_target_joint")
        pre_translate = (1.25, -2.5, 3.75)
        pre_rotate = (4.0, -5.0, 6.0)
        cmds.setAttr(f"{driver}.inPreTranslate", *pre_translate)
        cmds.setAttr(f"{driver}.inPreRotate", *pre_rotate)

        self.assertFalse(cmds.getAttr(f"{solvers[0]}.outSolved"))
        self.assertListAlmostEqual(
            list(cmds.getAttr(f"{driver}.outTranslate")[0]),
            list(pre_translate),
        )
        self.assertListAlmostEqual(
            list(cmds.getAttr(f"{driver}.outRotate")[0]),
            list(pre_rotate),
        )
        self.assertListAlmostEqual(
            list(cmds.getAttr(f"{target_joint}.translate")[0]),
            list(pre_translate),
        )
        self.assertListAlmostEqual(
            list(cmds.getAttr(f"{target_joint}.rotate")[0]),
            list(pre_rotate),
        )

    @unittest.skipUnless(is_native_physics_available(), "native physics DLL not available")
    def test_mixed_mode_bone_omits_kinematic_world_matrix_connection(self):
        """A mode-0/mode-1 mixed bone must not close a solver DG cycle."""
        plugin_path = str(Path(__file__).resolve().parents[2] / "mmd_tools" / "plugin_main.py")
        try:
            self.load_plugin(plugin_path)
        except Exception:
            pass

        from mmd_tools.converters.physics_scene_builder import build_physics_live_graph

        root = cmds.group(empty=True, name="mixed_mode_root")
        cmds.select(clear=True)
        joint = cmds.joint(name="mixed_mode_bone")
        cmds.parent(joint, root)

        graph = build_physics_live_graph(
            rigid_bodies=[
                SimpleNamespace(physics_mode=0, related_bone_index=0),
                SimpleNamespace(physics_mode=1, related_bone_index=0),
            ],
            bones=[SimpleNamespace(parent_bone_index=-1)],
            maya_joints=[joint],
            root_group=root,
        )

        solver = graph["solver"]
        self.assertIsNotNone(solver)
        self.assertEqual(len(graph["drivers"]), 1)
        self.assertFalse(
            cmds.listConnections(
                f"{solver}.inKinematicWorldMatrix[0]",
                source=True,
                destination=False,
                plugs=True,
            ) or [],
            "mixed-mode bone worldMatrix must not feed the solver",
        )
        self.assertTrue(
            cmds.isConnected(
                f"{solver}.outBoneMatrices",
                f"{graph['drivers'][0]}.inSolverBoneMatrices",
            )
        )

    def test_import_pmx_multiple_files(self):
        """全てのPMXモデルが基本的にロード可能かテスト"""

        pmx_files = self.fixture_provider.get_all_pmx_files()

        if len(pmx_files) < 2:
            self.skipTest("複数の PMX fixture が必要です")

        for model_name, file_path in pmx_files.items():
            with self.subTest(model=model_name):
                cmds.file(new=True, force=True)
                initial_nodes = set(cmds.ls())

                try:
                    parser = parse_pmx_file(file_path)
                except (ValueError, MMDParseException):
                    continue
                result = import_pmx_file(parser, file_path)

                self.assertTrue(result)

                new_nodes = set(cmds.ls()) - initial_nodes
                self.assertGreater(len(new_nodes), 0, "新しいノードが作成されていません")

                meshes = cmds.ls(type="mesh")
                if parser.vertices:
                    self.assertGreater(len(meshes), 0, "メッシュが作成されていません")

                joints = cmds.ls(type="joint")
                self.assertGreater(len(joints), 0, "ジョイントが作成されていません")
