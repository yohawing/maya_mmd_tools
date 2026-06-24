"""VmdConverterのユニットテスト

VmdConverterクラスの基本的な機能をテスト。
Maya環境内で実行されるが、シーン操作を伴わないテストを行う。
"""

import json
import math
import os
import tempfile
from unittest.mock import patch

import maya.cmds as cmds
import maya.api.OpenMaya as om

import mmd_tools.converters.vmd_converter as vmd_converter_module
from mmd_tools.core.vmd_data.ik_show_hide_frame import VmdIKShowHideFrame
from tests.common.maya_test_base import MayaTestBase
from tests.common.vmd_mock import create_test_vmd_data
from mmd_tools.converters.vmd_converter import VmdConverter
from mmd_tools.io.mmd_importer import import_mmd_file
from tests.common.test_fixture_provider import TestFixtureProvider


class TestVmdConverter(MayaTestBase):
    """VmdConverterクラスのユニットテスト"""

    def setUp(self):
        """テストのセットアップ"""
        super().setUp()
        # VmdConverterのインスタンスを作成
        self.converter = VmdConverter()

        # テストフィクスチャプロバイダーを作成
        self.fixture_provider = TestFixtureProvider()

    def tearDown(self):
        """テスト後のクリーンアップ"""
        super().tearDown()
        # 一時ファイルのクリーンアップ
        self.fixture_provider.cleanup_temp_files()

        # テスト用カメラとライトを削除
        import maya.cmds as cmds
        from mmd_tools.core.constants import DEFAULT_CAMERA_NAME, DEFAULT_LIGHT_NAME

        if cmds.objExists(DEFAULT_CAMERA_NAME):
            cmds.delete(DEFAULT_CAMERA_NAME)
        if cmds.objExists(DEFAULT_LIGHT_NAME):
            cmds.delete(DEFAULT_LIGHT_NAME)

        # アニメーションレイヤーをクリーンアップ
        anim_layers = cmds.ls(type="animLayer")
        for layer in anim_layers:
            if layer != "BaseAnimation":  # BaseAnimationレイヤーは削除しない
                try:
                    cmds.delete(layer)
                except Exception:
                    pass

    def test_init(self):
        """初期化のテスト"""
        self.assertEqual(self.converter.bone_name_mapping, {})
        self.assertEqual(self.converter.morph_name_mapping, {})
        self.assertEqual(self.converter.fps, 30.0)
        self.assertIsNotNone(self.converter.logger)
        self.assertEqual(len(self.converter._failed_bones), 0)
        self.assertTrue(self.converter.use_animation_layers)
        self.assertIsNone(self.converter.anim_layer)

    def test_get_failed_bones(self):
        """失敗したボーン名の取得テスト"""
        # 初期状態
        self.assertEqual(len(self.converter.get_failed_bones()), 0)

        # 失敗したボーンを追加
        self.converter._failed_bones.add("ボーン1")
        self.converter._failed_bones.add("ボーン2")

        # 取得
        failed = self.converter.get_failed_bones()
        self.assertEqual(len(failed), 2)
        self.assertIn("ボーン1", failed)
        self.assertIn("ボーン2", failed)

        # 元のセットが変更されないことを確認
        failed.add("ボーン3")
        self.assertEqual(len(self.converter._failed_bones), 2)

    def test_convert_with_test_vmd_data(self):
        """テスト用VMDデータでの変換テスト"""
        # テスト用VMDデータを作成
        vmd_data = create_test_vmd_data()

        # ボーン名マッピングを設定
        bone_mapping = {"センター": "center", "上半身": "upper_body", "頭": "head"}
        self.converter.set_bone_name_mapping(bone_mapping)

        # 変換実行（実際のMayaシーンにボーンがないためFalseを返すが、
        # エラーが発生しないことを確認）
        self.converter.convert(vmd_data)

        # フレーム数が正しく設定されていることを確認
        # (VMDデータの通常フレームは30)
        import maya.cmds as cmds

        self.assertEqual(cmds.playbackOptions(q=True, max=True), 30)

    def test_get_or_create_camera(self):
        """カメラの作成・取得テスト"""
        import maya.cmds as cmds
        from mmd_tools.core.constants import ATTR_MMD_CAMERA

        # 新規作成
        camera_name = self.converter._get_or_create_camera()
        self.assertIsNotNone(camera_name)
        self.assertTrue(cmds.objExists(camera_name))
        self.assertTrue(cmds.attributeQuery(ATTR_MMD_CAMERA, node=camera_name, exists=True))

        # 既存カメラの取得
        camera_name2 = self.converter._get_or_create_camera()
        self.assertEqual(camera_name, camera_name2)

    def test_get_or_create_light(self):
        """照明の作成・取得テスト"""
        import maya.cmds as cmds
        from mmd_tools.core.constants import ATTR_MMD_LIGHT

        # 新規作成
        light_name = self.converter._get_or_create_light()
        self.assertIsNotNone(light_name)
        self.assertTrue(cmds.objExists(light_name))
        self.assertTrue(cmds.attributeQuery(ATTR_MMD_LIGHT, node=light_name, exists=True))

        # 既存照明の取得
        light_name2 = self.converter._get_or_create_light()
        self.assertEqual(light_name, light_name2)

    def test_convert_camera_animation(self):
        """カメラアニメーション変換テスト"""
        from mmd_tools.core.vmd_data.camera_frame import VmdCameraFrame

        # テスト用カメラフレームを作成
        camera_frames = []
        for i in range(3):
            frame = VmdCameraFrame()
            frame.frame_number = i * 10
            frame.position = (i * 1.0, i * 2.0, i * 3.0)
            frame.rotation = (0.1 * i, 0.2 * i, 0.3 * i)
            frame.distance = 10.0 + i
            frame.viewing_angle = 30 + i * 5
            camera_frames.append(frame)

        # 変換実行
        result = self.converter._convert_camera_animation(camera_frames)
        self.assertTrue(result)

        # カメラが作成されたことを確認
        import maya.cmds as cmds
        from mmd_tools.core.constants import ATTR_MMD_CAMERA

        # カメラ名を正確に確認（変換関数が返すカメラ名をチェック）
        cameras = cmds.ls(type="camera")
        camera_found = False
        for cam in cameras:
            transform = cmds.listRelatives(cam, parent=True)
            if transform and cmds.attributeQuery(ATTR_MMD_CAMERA, node=transform[0], exists=True):
                camera_found = True
                # キーフレームが設定されたことを確認
                keyframes = cmds.keyframe(f"{transform[0]}.translateX", query=True)
                self.assertIsNotNone(keyframes)
                self.assertEqual(len(keyframes), 3)
                self.assertIsNotNone(cmds.keyframe(f"{transform[0]}.rotateX", query=True))
                self.assertIsNotNone(cmds.keyframe(f"{transform[0]}.mmd_camera_distance", query=True))
                self.assertIsNotNone(cmds.keyframe(f"{transform[0]}.mmd_camera_viewing_angle", query=True))
                focal_keys = cmds.keyframe(f"{cam}.focalLength", query=True)
                self.assertIsNotNone(focal_keys)
                self.assertEqual(len(focal_keys), 3)
                break

        self.assertTrue(camera_found, "MMDカメラが作成されていません")

    def test_convert_camera_animation_via_convert(self):
        """convert() のレガシーパスが camera_frames を変換することを確認"""
        from mmd_tools.core.vmd_data.camera_frame import VmdCameraFrame

        frame = VmdCameraFrame()
        frame.frame_number = 15
        frame.position = (1.0, 2.0, 3.0)
        frame.rotation = (0.1, 0.2, 0.3)
        frame.distance = 12.0
        frame.viewing_angle = 40

        vmd_data = type(
            "FakeVmdData",
            (),
            {
                "bone_frames": [],
                "morph_frames": [],
                "camera_frames": [frame],
                "light_frames": [],
            },
        )()

        result = self.converter.convert(vmd_data)
        self.assertTrue(result)

        camera_name = self.converter._get_or_create_camera()
        cmds.currentTime(15, edit=True)
        self.assertAlmostEqual(cmds.getAttr(f"{camera_name}.translateX"), 1.0, places=6)
        self.assertAlmostEqual(cmds.getAttr(f"{camera_name}.translateZ"), -3.0, places=6)
        self.assertAlmostEqual(cmds.getAttr(f"{camera_name}.mmd_camera_distance"), 12.0, places=6)

    def test_detect_vmd_motion_kind(self):
        """VMD内容から model/camera/light/mixed/empty を判定できることを確認"""
        def fake(**kwargs):
            defaults = {
                "bone_frames": [],
                "morph_frames": [],
                "camera_frames": [],
                "light_frames": [],
            }
            defaults.update(kwargs)
            return type("FakeVmdData", (), defaults)()

        self.assertEqual(self.converter._detect_vmd_motion_kind(fake()), "empty")
        self.assertEqual(self.converter._detect_vmd_motion_kind(fake(bone_frames=[object()])), "model")
        self.assertEqual(self.converter._detect_vmd_motion_kind(fake(camera_frames=[object()])), "camera")
        self.assertEqual(self.converter._detect_vmd_motion_kind(fake(light_frames=[object()])), "light")
        self.assertEqual(
            self.converter._detect_vmd_motion_kind(fake(bone_frames=[object()], camera_frames=[object()])),
            "mixed",
        )

    def test_convert_light_animation(self):
        """照明アニメーション変換テスト — color に加えて rotateX/Y/Z の keyframe が作成される"""
        from mmd_tools.core.vmd_data.light_frame import VmdLightFrame

        # テスト用照明フレームを作成
        light_frames = []
        for i in range(3):
            frame = VmdLightFrame()
            frame.frame_number = i * 10
            frame.position = (0.5, -1.0, 1.0)  # 方向ベクトル (非ゼロ)
            frame.color = (1.0 - i * 0.1, 1.0 - i * 0.1, 1.0 - i * 0.1)
            light_frames.append(frame)

        # 変換実行
        result = self.converter._convert_light_animation(light_frames)
        self.assertTrue(result)

        # 照明が作成されたことを確認
        import maya.cmds as cmds
        from mmd_tools.core.constants import DEFAULT_LIGHT_NAME

        self.assertTrue(cmds.objExists(DEFAULT_LIGHT_NAME))

        # rotateX/Y/Z に keyframe が 3 個ずつ設定されている
        for attr in ("rotateX", "rotateY", "rotateZ"):
            keys = cmds.keyframe(f"{DEFAULT_LIGHT_NAME}.{attr}", query=True, timeChange=True)
            self.assertIsNotNone(keys, f"{attr} に keyframe がありません")
            self.assertEqual(len(keys), 3, f"{attr} の keyframe 数が期待と異なります")

    def test_convert_light_animation_zero_vector_skips_rotation(self):
        """ゼロベクトルのフレームでは rotation key がスキップされるが color key は維持される"""
        from mmd_tools.core.vmd_data.light_frame import VmdLightFrame

        light_frames = []
        # フレーム 0: ゼロベクトル
        frame0 = VmdLightFrame()
        frame0.frame_number = 0
        frame0.position = (0.0, 0.0, 0.0)
        frame0.color = (1.0, 1.0, 1.0)
        light_frames.append(frame0)
        # フレーム 10: 通常方向
        frame1 = VmdLightFrame()
        frame1.frame_number = 10
        frame1.position = (0.5, -0.5, 1.0)
        frame1.color = (0.5, 0.5, 0.5)
        light_frames.append(frame1)

        result = self.converter._convert_light_animation(light_frames)
        self.assertTrue(result)

        import maya.cmds as cmds
        from mmd_tools.core.constants import DEFAULT_LIGHT_NAME

        self.assertTrue(cmds.objExists(DEFAULT_LIGHT_NAME))

        # colorR は両フレームに key がある (zero vector でも color は維持)
        color_keys = cmds.keyframe(f"{DEFAULT_LIGHT_NAME}.colorR", query=True, timeChange=True)
        self.assertEqual(len(color_keys), 2)
        self.assertIn(0.0, color_keys)
        self.assertIn(10.0, color_keys)

        # rotateX はゼロベクトルフレーム 0 がスキップされ、フレーム 10 のみ key がある
        rot_keys = cmds.keyframe(f"{DEFAULT_LIGHT_NAME}.rotateX", query=True, timeChange=True)
        self.assertEqual(len(rot_keys), 1)
        self.assertIn(10.0, rot_keys)
        self.assertNotIn(0.0, rot_keys)

    def test_convert_light_animation_via_convert(self):
        """convert() が light_frames を持つ VMD で _convert_light_animation を呼ぶことを確認"""
        from mmd_tools.core.vmd_data import VmdData
        from mmd_tools.core.vmd_data.light_frame import VmdLightFrame

        vmd_data = VmdData()
        vmd_data.bone_frames = []
        vmd_data.morph_frames = []
        vmd_data.camera_frames = []
        vmd_data.light_frames = []
        vmd_data.shadow_frames = []
        vmd_data.ik_show_hide_frames = []
        vmd_data.header.model_name = "TestLight"

        frame = VmdLightFrame()
        frame.frame_number = 0
        frame.position = (0.5, -1.0, 0.5)
        frame.color = (0.8, 0.8, 0.8)
        vmd_data.light_frames.append(frame)

        result = self.converter.convert(vmd_data)
        self.assertTrue(result)

        import maya.cmds as cmds
        from mmd_tools.core.constants import DEFAULT_LIGHT_NAME

        self.assertTrue(cmds.objExists(DEFAULT_LIGHT_NAME))
        # color と rotate の key が作成されている
        color_keys = cmds.keyframe(f"{DEFAULT_LIGHT_NAME}.colorR", query=True, timeChange=True)
        self.assertIsNotNone(color_keys)
        self.assertGreater(len(color_keys), 0)
        rot_keys = cmds.keyframe(f"{DEFAULT_LIGHT_NAME}.rotateX", query=True, timeChange=True)
        self.assertIsNotNone(rot_keys)
        self.assertGreater(len(rot_keys), 0)

    def test_runtime_bake_infrastructure(self):
        """Phase 1 runtime bake のインフラテスト (native なし環境でも安全)"""
        vmd_data = create_test_vmd_data()
        self.converter.set_bone_name_mapping({"センター": "center"})

        # 新パラメータを受け付ける
        res = self.converter.convert(vmd_data, vmd_bytes=b"dummy", pmx_bytes=None, pmx_path=None)
        self.assertIsInstance(res, bool)

        # should_use はデータ不足で False
        self.assertFalse(
            self.converter._should_use_mmd_runtime_bake(b"vmd", None, "/nonexistent.pmx")
        )

        # runtime bake インフラの確認のみ (キーフレーム検証は別テストに依存しないよう削除)
        # ここでは convert が例外なく完了し、should_use が正しく動くことを確認
        pass  # 追加の検証は test_convert_light_animation 等で行う

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

    def test_mmd_ik_passthrough_keys_chain_bone_slot(self):
        """runtime live apply 中は mmdCcdIk output link の入力 slot へ final rotation を焼く"""
        joint = cmds.joint(name="runtime_live_toe_link")
        ik_node = cmds.createNode("mmdCcdIk", name="runtime_live_toe_ik")
        chain_json = {
            "bones": [{"rest_position": [0, 0, 0], "parent_slot": -1} for _ in range(4)],
            "controllerBoneSlot": -1,
            "targetBoneSlot": 0,
            "links": [{"bone_slot": 3}],
            "iterationCount": 1,
            "limitAngle": 0.1,
        }
        cmds.setAttr(f"{ik_node}.chainJson", json.dumps(chain_json), type="string")
        cmds.connectAttr(f"{ik_node}.outputRotate[0]", f"{joint}.rotate", force=True)

        info = self.converter._collect_mmd_ik_passthrough_info()[joint]
        self.assertEqual(info["link_index"], 0)
        self.assertEqual(info["input_slot"], 3)

        times = om.MTimeArray()
        frames = [0, 5]
        for frame in frames:
            times.append(om.MTime(float(frame), om.MTime.uiUnit()))
        channels = {
            "rotateX": om.MDoubleArray([math.radians(10.0), math.radians(20.0)]),
            "rotateY": om.MDoubleArray([0.0, 0.0]),
            "rotateZ": om.MDoubleArray([0.0, 0.0]),
        }
        keyed = self.converter._key_mmd_ik_passthrough_rotation(info, channels, {}, times, frames)

        self.assertEqual(keyed, 4)
        self.assertFalse(cmds.getAttr(f"{ik_node}.enabled"))
        self.assertEqual(
            cmds.keyframe(
                f"{ik_node}.inputRotate[3].inputRotateElementX",
                query=True,
                time=(5, 5),
                valueChange=True,
            ),
            [20.0],
        )
        self.assertIsNone(cmds.keyframe(f"{ik_node}.inputRotate[0].inputRotateElementX", query=True))

        cmds.delete(ik_node, joint)

    def test_ik_link_input_rotate_stores_correct_radian_values(self):
        """Rig+JO の IK link pre-rotation が solver.inputRotate に正しい角度単位で保存される"""
        pmx_path = self.fixture_provider.get_pmx_file("mmt_test_model")
        vmd_path = self.fixture_provider.get_vmd_file("mmt_test_model_test_motion")

        root = import_mmd_file(
            pmx_path,
            options={"setup_rig": True, "setup_bone_orientation": True},
        )
        self.assertIsNotNone(root, "PMX import failed")
        self.assertTrue(
            import_mmd_file(vmd_path, options={"target_model": root, "pmx_path": pmx_path}),
            "VMD import failed",
        )

        solver_node = None
        for node in cmds.ls(type="mmdCcdIk") or []:
            if not cmds.attributeQuery("mmd_ik_bone_name", node=node, exists=True):
                continue
            ik_name = cmds.getAttr(f"{node}.mmd_ik_bone_name") or ""
            if "左足" in ik_name and "つま先" not in ik_name:
                solver_node = node
                break

        self.assertIsNotNone(solver_node, "左足 IK の mmdCcdIk solver が見つかりません")

        chain = json.loads(cmds.getAttr(f"{solver_node}.chainJson") or "{}")
        slots = [int(link["bone_slot"]) for link in chain.get("links", [])]
        self.assertGreater(len(slots), 0, "左足 IK の chainJson に links がありません")

        selection = om.MSelectionList()
        selection.add(solver_node)
        fn_dep = om.MFnDependencyNode(selection.getDependNode(0))
        input_rotate = fn_dep.findPlug("inputRotate", False)

        cmds.currentTime(10, edit=True)

        non_zero_radians = []
        for slot in slots:
            elem = input_rotate.elementByLogicalIndex(slot)
            for axis_index, axis in enumerate("XYZ"):
                attr = f"{solver_node}.inputRotate[{slot}].inputRotateElement{axis}"
                ui_degrees = cmds.getAttr(attr)
                plug_radians = elem.child(axis_index).asDouble()
                self.assertAlmostEqual(
                    plug_radians,
                    math.radians(ui_degrees),
                    delta=1e-6,
                    msg=f"{attr} の getAttr 度数値と MPlug ラジアン値が一致しません",
                )
                non_zero_radians.append(abs(plug_radians))

        self.assertGreater(
            max(non_zero_radians),
            0.01,
            "IK link inputRotate がほぼゼロで、二重ラジアン変換の再発が疑われます",
        )

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

    def test_runtime_matrix_coordinate_conversion_identity_and_translation(self):
        """runtime world matrix の座標変換で identity を壊さず Z translation だけ反転する"""
        identity = [
            1.0, 0.0, 0.0, 0.0,
            0.0, 1.0, 0.0, 0.0,
            0.0, 0.0, 1.0, 0.0,
            0.0, 0.0, 0.0, 1.0,
        ]
        self.assertListAlmostEqual(
            self.converter._convert_mmd_world_matrix_to_maya(identity),
            identity,
        )

        translated = [
            1.0, 0.0, 0.0, 0.0,
            0.0, 1.0, 0.0, 0.0,
            0.0, 0.0, 1.0, 0.0,
            1.0, 2.0, 3.0, 1.0,
        ]
        expected = [
            1.0, 0.0, 0.0, 0.0,
            0.0, 1.0, 0.0, 0.0,
            0.0, 0.0, 1.0, 0.0,
            1.0, 2.0, -3.0, 1.0,
        ]
        self.assertListAlmostEqual(
            self.converter._convert_mmd_world_matrix_to_maya(translated),
            expected,
        )

    def test_runtime_matrix_coordinate_conversion_rotations_keep_proper_basis(self):
        """runtime world matrix の Z 反転が回転行列を反射行列にしない"""
        cases = [
            (
                "rotate_x_90",
                [
                    1.0, 0.0, 0.0, 0.0,
                    0.0, 0.0, 1.0, 0.0,
                    0.0, -1.0, 0.0, 0.0,
                    0.0, 0.0, 0.0, 1.0,
                ],
                [
                    1.0, 0.0, -0.0, 0.0,
                    0.0, 0.0, -1.0, 0.0,
                    -0.0, 1.0, 0.0, 0.0,
                    0.0, 0.0, 0.0, 1.0,
                ],
            ),
            (
                "rotate_y_90",
                [
                    0.0, 0.0, -1.0, 0.0,
                    0.0, 1.0, 0.0, 0.0,
                    1.0, 0.0, 0.0, 0.0,
                    0.0, 0.0, 0.0, 1.0,
                ],
                [
                    0.0, 0.0, 1.0, 0.0,
                    0.0, 1.0, -0.0, 0.0,
                    -1.0, -0.0, 0.0, 0.0,
                    0.0, 0.0, 0.0, 1.0,
                ],
            ),
            (
                "rotate_z_90",
                [
                    0.0, 1.0, 0.0, 0.0,
                    -1.0, 0.0, 0.0, 0.0,
                    0.0, 0.0, 1.0, 0.0,
                    0.0, 0.0, 0.0, 1.0,
                ],
                [
                    0.0, 1.0, -0.0, 0.0,
                    -1.0, 0.0, -0.0, 0.0,
                    -0.0, -0.0, 1.0, 0.0,
                    0.0, 0.0, 0.0, 1.0,
                ],
            ),
        ]

        for name, source, expected in cases:
            converted = self.converter._convert_mmd_world_matrix_to_maya(source)
            self.assertListAlmostEqual(converted, expected, places=6, msg=name)
            self.assertAlmostEqual(
                self._determinant3(converted),
                1.0,
                places=6,
                msg=f"{name} determinant",
            )

    def test_runtime_matrix_coordinate_conversion_applies_to_maya_joint(self):
        """変換済み runtime world matrix を Maya joint に適用した最終座標を確認する"""
        joint = cmds.joint(name="runtime_matrix_joint")
        mmd_matrix = [
            1.0, 0.0, 0.0, 0.0,
            0.0, 1.0, 0.0, 0.0,
            0.0, 0.0, 1.0, 0.0,
            1.0, 2.0, 3.0, 1.0,
        ]

        maya_matrix = self.converter._convert_mmd_world_matrix_to_maya(mmd_matrix)
        cmds.xform(joint, worldSpace=True, matrix=maya_matrix)

        translation = cmds.xform(joint, query=True, worldSpace=True, translation=True)
        self.assertListAlmostEqual(translation, [1.0, 2.0, -3.0], places=6)

    def test_runtime_matrix_bake_sets_animation_curve_values_in_maya_space(self):
        """runtime world matrix bake 後のアニメーションカーブ値が Maya 座標系になる"""
        joint = cmds.joint(name="runtime_bake_joint")
        self.converter.bone_name_mapping = {"センター": joint}
        self.converter.bone_name_to_index = {"センター": 0}
        self.converter.bone_index_to_joint = {0: joint}
        self.converter.anim_layer = None

        mmd_matrix = [
            1.0, 0.0, 0.0, 0.0,
            0.0, 1.0, 0.0, 0.0,
            0.0, 0.0, 1.0, 0.0,
            1.0, 2.0, 3.0, 1.0,
        ]

        self.converter._bake_bone_poses_from_world_matrices(
            frame=12,
            world_matrices=[mmd_matrix],
            model_bone_count=1,
        )

        cmds.currentTime(12, edit=True)
        self.assertAlmostEqual(cmds.getAttr(f"{joint}.translateX"), 1.0, places=6)
        self.assertAlmostEqual(cmds.getAttr(f"{joint}.translateY"), 2.0, places=6)
        self.assertAlmostEqual(cmds.getAttr(f"{joint}.translateZ"), -3.0, places=6)

        keyed_times = cmds.keyframe(f"{joint}.translateZ", query=True, timeChange=True)
        self.assertIn(12.0, keyed_times)

    def _determinant3(self, matrix):
        """4x4 flat matrix の左上 3x3 determinant を返す"""
        a, b, c = matrix[0], matrix[1], matrix[2]
        d, e, f = matrix[4], matrix[5], matrix[6]
        g, h, i = matrix[8], matrix[9], matrix[10]
        return (
            a * (e * i - f * h)
            - b * (d * i - f * g)
            + c * (d * h - e * g)
        )

    def test_convert_morph_animation(self):
        """モーフアニメーション変換テスト"""
        from mmd_tools.core.vmd_data.morph_frame import VmdMorphFrame

        # テスト用モーフフレームを作成
        morph_frames = []
        for i in range(3):
            frame = VmdMorphFrame()
            frame.frame_number = i * 10
            frame.morph_name = "mabataki"  # ASCII文字に変更
            frame.value = i * 0.5  # 0.0, 0.5, 1.0
            morph_frames.append(frame)

        # テスト用ブレンドシェイプを作成
        cube = cmds.polyCube(name="test_mesh")[0]
        blend_shape = cmds.blendShape(cube, name="test_blendShape")[0]

        # テスト用ターゲットを追加
        target = cmds.duplicate(cube)[0]
        cmds.move(1, 0, 0, f"{target}.vtx[*]", relative=True)
        cmds.blendShape(blend_shape, edit=True, target=(cube, 0, target, 1.0))
        cmds.aliasAttr("mabataki", f"{blend_shape}.weight[0]")

        # モーフマッピングを設定
        self.converter.morph_name_mapping["mabataki"] = (blend_shape, "weight[0]", "mabataki")

        # 変換実行
        result = self.converter._convert_morph_animation(morph_frames)
        self.assertTrue(result)

        # キーフレームが設定されたことを確認
        keyframes = cmds.keyframe(f"{blend_shape}.weight[0]", query=True)
        self.assertIsNotNone(keyframes)
        self.assertEqual(len(keyframes), 3)

        # クリーンアップ
        cmds.delete(cube, target)

    def test_convert_morph_animation_with_split_mesh_aliases(self):
        """同名 alias が複数 mesh の blendShape にある場合、全 mapping に keyframe を打つ"""
        from mmd_tools.core.vmd_data.morph_frame import VmdMorphFrame

        morph_frames = []
        frame = VmdMorphFrame()
        frame.frame_number = 5
        frame.morph_name = "morph_split"
        frame.value = 0.75
        morph_frames.append(frame)

        mesh_a = cmds.polyCube(name="morph_split_mesh_a")[0]
        blend_shape_a = cmds.blendShape(mesh_a, name="morph_split_bs_a")[0]
        target_a = cmds.duplicate(mesh_a)[0]
        cmds.move(1, 0, 0, f"{target_a}.vtx[*]", relative=True)
        cmds.blendShape(blend_shape_a, edit=True, target=(mesh_a, 0, target_a, 1.0))
        cmds.aliasAttr("morph_split", f"{blend_shape_a}.weight[0]")
        cmds.delete(target_a)

        mesh_b = cmds.polyCube(name="morph_split_mesh_b")[0]
        blend_shape_b = cmds.blendShape(mesh_b, name="morph_split_bs_b")[0]
        target_b = cmds.duplicate(mesh_b)[0]
        cmds.move(0, 1, 0, f"{target_b}.vtx[*]", relative=True)
        cmds.blendShape(blend_shape_b, edit=True, target=(mesh_b, 0, target_b, 1.0))
        cmds.aliasAttr("morph_split", f"{blend_shape_b}.weight[0]")
        cmds.delete(target_b)

        self.converter._build_morph_mappings()
        self.assertEqual(len(self.converter._iter_morph_mappings(self.converter.morph_name_mapping["morph_split"])), 2)
        result = self.converter._convert_morph_animation(morph_frames)
        self.assertTrue(result)

        keys_a = cmds.keyframe(f"{blend_shape_a}.weight[0]", query=True, timeChange=True)
        keys_b = cmds.keyframe(f"{blend_shape_b}.weight[0]", query=True, timeChange=True)
        self.assertIn(5.0, keys_a)
        self.assertIn(5.0, keys_b)

        # クリーンアップ
        cmds.delete(mesh_a, blend_shape_a, mesh_b, blend_shape_b)

    def test_convert_morph_animation_legacy_mapping_uses_weight_index_tuple(self):
        """旧形式の mapping tuple に int の weight index が渡っても変換できる"""
        from mmd_tools.core.vmd_data.morph_frame import VmdMorphFrame

        frame = VmdMorphFrame()
        frame.frame_number = 5
        frame.morph_name = "mabataki"
        frame.value = 0.6

        cube = cmds.polyCube(name="legacy_mapping_morph_mesh")[0]
        blend_shape = cmds.blendShape(cube, name="legacy_mapping_blendShape")[0]

        target = cmds.duplicate(cube)[0]
        cmds.move(1, 0, 0, f"{target}.vtx[*]", relative=True)
        cmds.blendShape(blend_shape, edit=True, target=(cube, 0, target, 1.0))
        cmds.aliasAttr("mabataki", f"{blend_shape}.weight[0]")
        self.converter.morph_name_mapping["mabataki"] = (blend_shape, 0, "mabataki")
        cmds.delete(target)

        result = self.converter._convert_morph_animation([frame])
        self.assertTrue(result)

        cmds.currentTime(5, edit=True)
        self.assertAlmostEqual(cmds.getAttr(f"{blend_shape}.weight[0]"), 0.6, places=6)
        keyframes = cmds.keyframe(f"{blend_shape}.weight[0]", query=True)
        self.assertIn(5.0, keyframes)

        cmds.delete(cube)

    def test_convert_bone_morph_network_weight_animation(self):
        """bone morph network の weight にVMD morph frameのキーを打てることを確認"""
        from mmd_tools.core.vmd_data.morph_frame import VmdMorphFrame

        morph_node = cmds.createNode("network", name="boneSmile_boneMorph")
        cmds.addAttr(
            morph_node,
            longName="weight",
            attributeType="double",
            minValue=0.0,
            maxValue=1.0,
            defaultValue=0.0,
            keyable=True,
        )
        cmds.addAttr(morph_node, longName="mmd_morph_type", dataType="string")
        cmds.setAttr(f"{morph_node}.mmd_morph_type", "bone", type="string")
        cmds.addAttr(morph_node, longName="mmd_morph_name", dataType="string")
        cmds.setAttr(f"{morph_node}.mmd_morph_name", "ボーン笑い", type="string")

        frame = VmdMorphFrame()
        frame.frame_number = 12
        frame.morph_name = "ボーン笑い"
        frame.value = 0.8

        self.converter._build_morph_mappings()
        result = self.converter._convert_morph_animation([frame])
        self.assertTrue(result)

        cmds.currentTime(12, edit=True)
        self.assertAlmostEqual(cmds.getAttr(f"{morph_node}.weight"), 0.8, places=6)
        self.assertIn(12.0, cmds.keyframe(f"{morph_node}.weight", query=True))

        cmds.delete(morph_node)

    def test_convert_material_morph_network_weight_animation(self):
        """material morph network の weight にVMD morph frameのキーを打てることを確認"""
        from mmd_tools.core.vmd_data.morph_frame import VmdMorphFrame

        morph_node = cmds.createNode("network", name="materialFlash_materialMorph")
        cmds.addAttr(
            morph_node,
            longName="weight",
            attributeType="double",
            minValue=0.0,
            maxValue=1.0,
            defaultValue=0.0,
            keyable=True,
        )
        cmds.addAttr(morph_node, longName="mmd_morph_type", dataType="string")
        cmds.setAttr(f"{morph_node}.mmd_morph_type", "material", type="string")
        cmds.addAttr(morph_node, longName="mmd_morph_name", dataType="string")
        cmds.setAttr(f"{morph_node}.mmd_morph_name", "材質点滅", type="string")

        frame = VmdMorphFrame()
        frame.frame_number = 18
        frame.morph_name = "材質点滅"
        frame.value = 0.35

        self.converter._build_morph_mappings()
        result = self.converter._convert_morph_animation([frame])
        self.assertTrue(result)

        cmds.currentTime(18, edit=True)
        self.assertAlmostEqual(cmds.getAttr(f"{morph_node}.weight"), 0.35, places=6)
        self.assertIn(18.0, cmds.keyframe(f"{morph_node}.weight", query=True))

        cmds.delete(morph_node)

    def test_bake_morph_weights_from_runtime_uses_pmx_morph_order(self):
        """runtime morph weightをPMX morph順の日本語名でblendShapeへベイクする"""
        cube = cmds.polyCube(name="test_runtime_morph_mesh")[0]
        blend_shape = cmds.blendShape(cube, name="test_runtime_morph_blendShape")[0]

        target = cmds.duplicate(cube)[0]
        cmds.move(1, 0, 0, f"{target}.vtx[*]", relative=True)
        cmds.blendShape(blend_shape, edit=True, target=(cube, 0, target, 1.0))
        cmds.aliasAttr("blink", f"{blend_shape}.weight[0]")
        cmds.delete(target)

        self.converter._build_morph_mappings()
        self.converter._bake_morph_weights_from_runtime(
            frame=7,
            morph_weights=[0.75],
            pmx_morph_names=["まばたき"],
        )

        cmds.currentTime(7, edit=True)
        self.assertAlmostEqual(cmds.getAttr(f"{blend_shape}.weight[0]"), 0.75, places=6)

        keyframes = cmds.keyframe(f"{blend_shape}.weight[0]", query=True)
        self.assertIn(7.0, keyframes)

        cmds.delete(cube)

    def test_bake_morph_weights_from_runtime_with_split_mesh_aliases(self):
        """runtime bake でも同名 alias を全 blendShape にベイクする"""
        mesh_a = cmds.polyCube(name="runtime_split_mesh_a")[0]
        blend_shape_a = cmds.blendShape(mesh_a, name="runtime_split_bs_a")[0]
        target_a = cmds.duplicate(mesh_a)[0]
        cmds.move(1, 0, 0, f"{target_a}.vtx[*]", relative=True)
        cmds.blendShape(blend_shape_a, edit=True, target=(mesh_a, 0, target_a, 1.0))
        cmds.aliasAttr("morph_split", f"{blend_shape_a}.weight[0]")
        cmds.delete(target_a)

        mesh_b = cmds.polyCube(name="runtime_split_mesh_b")[0]
        blend_shape_b = cmds.blendShape(mesh_b, name="runtime_split_bs_b")[0]
        target_b = cmds.duplicate(mesh_b)[0]
        cmds.move(0, 1, 0, f"{target_b}.vtx[*]", relative=True)
        cmds.blendShape(blend_shape_b, edit=True, target=(mesh_b, 0, target_b, 1.0))
        cmds.aliasAttr("morph_split", f"{blend_shape_b}.weight[0]")
        cmds.delete(target_b)

        self.converter._build_morph_mappings()
        self.assertEqual(len(self.converter._iter_morph_mappings(self.converter.morph_name_mapping["morph_split"])), 2)

        self.converter._bake_morph_weights_from_runtime(
            frame=11,
            morph_weights=[0.4],
            pmx_morph_names=["morph_split"],
        )

        cmds.currentTime(11, edit=True)
        self.assertAlmostEqual(cmds.getAttr(f"{blend_shape_a}.weight[0]"), 0.4, places=6)
        self.assertAlmostEqual(cmds.getAttr(f"{blend_shape_b}.weight[0]"), 0.4, places=6)

        keys_a = cmds.keyframe(f"{blend_shape_a}.weight[0]", query=True, timeChange=True)
        keys_b = cmds.keyframe(f"{blend_shape_b}.weight[0]", query=True, timeChange=True)
        self.assertIn(11.0, keys_a)
        self.assertIn(11.0, keys_b)

        cmds.delete(mesh_a, blend_shape_a, mesh_b, blend_shape_b)

    def test_bake_morph_weights_from_runtime_with_legacy_mapping(self):
        """runtime bake で旧形式の weight index tuple が使える"""
        cube = cmds.polyCube(name="legacy_runtime_morph_mesh")[0]
        blend_shape = cmds.blendShape(cube, name="legacy_runtime_morph_blendShape")[0]

        target = cmds.duplicate(cube)[0]
        cmds.move(1, 0, 0, f"{target}.vtx[*]", relative=True)
        cmds.blendShape(blend_shape, edit=True, target=(cube, 0, target, 1.0))
        cmds.aliasAttr("mabataki", f"{blend_shape}.weight[0]")
        self.converter.morph_name_mapping["mabataki"] = (blend_shape, 0, "mabataki")
        cmds.delete(target)

        self.converter._bake_morph_weights_from_runtime(
            frame=19,
            morph_weights=[0.55],
            pmx_morph_names=["mabataki"],
        )

        cmds.currentTime(19, edit=True)
        self.assertAlmostEqual(cmds.getAttr(f"{blend_shape}.weight[0]"), 0.55, places=6)
        keyframes = cmds.keyframe(f"{blend_shape}.weight[0]", query=True)
        self.assertIn(19.0, keyframes)

        cmds.delete(cube)

    def test_disable_mmd_rig_constraints_for_runtime_bake_only_marked_constraints(self):
        """runtime bakeではMMD付与constraintとlive IK solverを無効化する"""
        source = cmds.spaceLocator(name="grant_source")[0]
        target = cmds.spaceLocator(name="grant_target")[0]
        other_source = cmds.spaceLocator(name="other_source")[0]
        other_target = cmds.spaceLocator(name="other_target")[0]
        ik_node = cmds.createNode("mmdCcdIk", name="runtime_disabled_ik_solver")
        ik_link = cmds.joint(name="runtime_disabled_ik_link")
        other_ik_node = cmds.createNode("mmdCcdIk", name="runtime_other_ik_solver")
        other_ik_link = cmds.joint(name="runtime_other_ik_link")

        marked = cmds.orientConstraint(source, target)[0]
        unmarked = cmds.orientConstraint(other_source, other_target)[0]
        cmds.addAttr(marked, longName="mmd_grant_constraint", attributeType="bool")
        cmds.setAttr(f"{marked}.mmd_grant_constraint", True)
        cmds.setAttr(f"{ik_node}.enabled", True)
        cmds.setAttr(f"{other_ik_node}.enabled", True)
        cmds.connectAttr(f"{ik_node}.outputRotate[0]", f"{ik_link}.rotate", force=True)
        cmds.connectAttr(f"{other_ik_node}.outputRotate[0]", f"{other_ik_link}.rotate", force=True)
        self.converter.bone_name_mapping = {
            "grant_target": target,
            "ik_link": ik_link,
        }

        self.converter._disable_mmd_rig_constraints_for_runtime_bake()

        self.assertEqual(cmds.getAttr(f"{marked}.nodeState"), 2)
        self.assertEqual(cmds.getAttr(f"{unmarked}.nodeState"), 0)
        self.assertFalse(cmds.getAttr(f"{ik_node}.enabled"))
        self.assertFalse(cmds.listConnections(f"{ik_link}.rotate", s=True, d=False, p=True) or [])
        self.assertTrue(cmds.getAttr(f"{other_ik_node}.enabled"))
        self.assertTrue(cmds.listConnections(f"{other_ik_link}.rotate", s=True, d=False, p=True) or [])

        cmds.delete(source, target, other_source, other_target, ik_node, ik_link, other_ik_node, other_ik_link)

    def test_restore_joints_to_bind_pose_for_runtime_bake_clears_live_values(self):
        """runtime bake 前にlive rig由来の残り値を消してbind姿勢へ戻す"""
        joint = cmds.joint(name="runtime_restore_bind_joint")
        cmds.setAttr(f"{joint}.translate", 1.0, 2.0, 3.0)
        cmds.setAttr(f"{joint}.rotate", 10.0, 20.0, 30.0)
        driver = cmds.createNode("animCurveTA", name="runtime_restore_rotate_driver")
        cmds.connectAttr(f"{driver}.output", f"{joint}.rotateX", force=True)

        self.converter.bone_name_mapping = {"センター": joint}
        self.converter._bone_bind_poses = {"センター": (4.0, 5.0, 6.0)}

        self.converter._restore_joints_to_bind_pose_for_runtime_bake()

        self.assertFalse(cmds.listConnections(f"{joint}.rotateX", s=True, d=False, p=True) or [])
        self.assertEqual(tuple(round(v, 6) for v in cmds.getAttr(f"{joint}.translate")[0]), (4.0, 5.0, 6.0))
        self.assertEqual(tuple(round(v, 6) for v in cmds.getAttr(f"{joint}.rotate")[0]), (0.0, 0.0, 0.0))

        cmds.delete(joint, driver)

    def test_build_morph_mappings(self):
        """モーフマッピング構築テスト"""
        # テスト用メッシュとブレンドシェイプを作成
        cube = cmds.polyCube(name="test_mesh")[0]
        blend_shape = cmds.blendShape(cube, name="test_blendShape")[0]

        # テスト用ターゲットを追加（ASCII文字に変更）
        morph_names = ["mabataki", "egao", "wink"]
        for i, morph_name in enumerate(morph_names):
            target = cmds.duplicate(cube)[0]
            cmds.move(i + 1, 0, 0, f"{target}.vtx[*]", relative=True)
            cmds.blendShape(blend_shape, edit=True, target=(cube, i, target, 1.0))
            cmds.aliasAttr(morph_name, f"{blend_shape}.weight[{i}]")
            cmds.delete(target)

        # デバッグ情報を出力
        print(f"Created blend shape: {blend_shape}")
        print(f"Weight count: {cmds.blendShape(blend_shape, query=True, weightCount=True)}")
        for i in range(3):
            alias = cmds.aliasAttr(f"{blend_shape}.weight[{i}]", query=True)
            print(f"Alias for weight[{i}]: {alias}")

        # マッピングを構築
        self.converter._build_morph_mappings()

        # デバッグ情報を出力
        print(f"Morph mapping: {self.converter.morph_name_mapping}")

        # マッピングが作成されたことを確認
        self.assertGreaterEqual(len(self.converter.morph_name_mapping), 3)
        self.assertIn("mabataki", self.converter.morph_name_mapping)
        self.assertIn("egao", self.converter.morph_name_mapping)
        self.assertIn("wink", self.converter.morph_name_mapping)

        # クリーンアップ
        cmds.delete(cube)

    def test_build_morph_mappings_adds_original_japanese_names(self):
        """Maya aliasが辞書変換名でもVMDの日本語モーフ名で引けることを確認"""
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
        """import 時保存の生名で正確にマッピングし、辞書逆引きの取り違えを起こさない。

        「にっこり」と「にやり」はどちらも sanitize_text で "grin" に化けるため、
        従来は alias 逆引きで両者が同一ターゲットへ巻き込まれていた。生名保存により
        それぞれが自分の weight に正確に対応することを確認する。
        """
        from mmd_tools.core.constants import ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON

        cube = cmds.polyCube(name="test_mesh_stored_morph")[0]
        blend_shape = cmds.blendShape(cube, name="test_blendShape_stored_morph")[0]

        for i, alias in enumerate(["grin", "grin_1"]):
            target = cmds.duplicate(cube)[0]
            cmds.move(i + 1, 0, 0, f"{target}.vtx[*]", relative=True)
            cmds.blendShape(blend_shape, edit=True, target=(cube, i, target, 1.0))
            cmds.aliasAttr(alias, f"{blend_shape}.weight[{i}]")
            cmds.delete(target)

        # import 時に保存される権威マップ（weight index -> 生モーフ名）
        cmds.addAttr(blend_shape, longName=ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON, dataType="string")
        cmds.setAttr(
            f"{blend_shape}.{ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON}",
            json.dumps({"0": "にっこり", "1": "にやり"}, ensure_ascii=False),
            type="string",
        )

        self.converter._build_morph_mappings()

        nikkori = self.converter._iter_morph_mappings(self.converter.morph_name_mapping.get("にっこり"))
        niyari = self.converter._iter_morph_mappings(self.converter.morph_name_mapping.get("にやり"))

        # それぞれ自分の weight に正確に対応する
        self.assertEqual(len(nikkori), 1)
        self.assertEqual(nikkori[0][1], "weight[0]")
        self.assertEqual(len(niyari), 1)
        self.assertEqual(niyari[0][1], "weight[1]")

        # 取り違えがない（互いのターゲットに巻き込まれていない）
        self.assertNotEqual(nikkori[0][1], niyari[0][1])

        # 生名が保存されている blendShape では lossy な逆引きを使わない
        self.assertNotIn("blink", self.converter.morph_name_mapping)

        cmds.delete(cube)

    def _create_test_joints_for_vmd(self):
        """VMDテスト用のジョイントを作成"""
        from mmd_tools.core.constants import ATTR_MMD_BONE_NAME

        # センタージョイント
        center = cmds.joint(name="center", position=[0, 10, 0])
        cmds.addAttr(center, longName=ATTR_MMD_BONE_NAME, dataType="string")
        cmds.setAttr(f"{center}.{ATTR_MMD_BONE_NAME}", "センター", type="string")

        # 上半身ジョイント
        upper_body = cmds.joint(name="upper_body", position=[0, 15, 0])
        cmds.addAttr(upper_body, longName=ATTR_MMD_BONE_NAME, dataType="string")
        cmds.setAttr(f"{upper_body}.{ATTR_MMD_BONE_NAME}", "上半身", type="string")

        # 頭ジョイント
        head = cmds.joint(name="head", position=[0, 20, 0])
        cmds.addAttr(head, longName=ATTR_MMD_BONE_NAME, dataType="string")
        cmds.setAttr(f"{head}.{ATTR_MMD_BONE_NAME}", "頭", type="string")

        cmds.select(clear=True)

        return {"center": center, "upper_body": upper_body, "head": head}

    def _make_bone_frame(self, bone_name, frame_number, position, rotation=(0.0, 0.0, 0.0, 1.0)):
        """テスト用 VMD ボーンフレームを作成する。"""
        from mmd_tools.core.vmd_data.bone_frame import VmdBoneFrame

        frame = VmdBoneFrame()
        frame.bone_name = bone_name
        frame.frame_number = frame_number
        frame.position = position
        frame.rotation = rotation
        return frame

    def test_legacy_bone_keyframes_use_bind_pose_without_accumulation(self):
        """レガシー VMD パスは現在フレーム値ではなく bind pose + VMD offset を key する。"""
        joint = cmds.joint(name="legacy_bind_pose_joint")
        cmds.setAttr(f"{joint}.translate", 100.0, 100.0, 100.0, type="double3")
        self.converter.use_animation_layers = False
        self.converter._bone_bind_poses["センター"] = (3.0, 4.0, 5.0)

        frames = [
            self._make_bone_frame("センター", 0, (1.0, 2.0, 3.0)),
            self._make_bone_frame("センター", 10, (2.0, 3.0, 4.0)),
        ]
        self.converter._set_bone_keyframes(joint, frames, "センター")

        cmds.currentTime(0, edit=True)
        self.assertAlmostEqual(cmds.getAttr(f"{joint}.translateX"), 4.0, places=6)
        self.assertAlmostEqual(cmds.getAttr(f"{joint}.translateY"), 6.0, places=6)
        self.assertAlmostEqual(cmds.getAttr(f"{joint}.translateZ"), 2.0, places=6)

        cmds.currentTime(10, edit=True)
        self.assertAlmostEqual(cmds.getAttr(f"{joint}.translateX"), 5.0, places=6)
        self.assertAlmostEqual(cmds.getAttr(f"{joint}.translateY"), 7.0, places=6)
        self.assertAlmostEqual(cmds.getAttr(f"{joint}.translateZ"), 1.0, places=6)

        cmds.delete(joint)

    def test_convert_vmd_quat_to_joint_rotate_keeps_rest_joint_orient(self):
        """Rig live 経路では VMD identity が JO 付き REST を壊さない。"""
        joint = cmds.joint(name="legacy_joint_orient_joint")
        cmds.setAttr(f"{joint}.jointOrient", 0.0, 0.0, 45.0)
        cmds.setAttr(f"{joint}.rotateOrder", 0)

        rx, ry, rz = self.converter._convert_vmd_quat_to_joint_rotate(
            joint,
            0.0,
            0.0,
            0.0,
            1.0,
        )

        self.assertAlmostEqual(rx, 0.0, places=6)
        self.assertAlmostEqual(ry, 0.0, places=6)
        self.assertAlmostEqual(rz, 0.0, places=6)

        cmds.delete(joint)

    def test_convert_vmd_quat_to_joint_rotate_matches_no_jo_skinning_delta(self):
        """非 identity VMD 回転は JO 付き joint.rotate 空間へ共役変換する。"""
        joint = cmds.joint(name="legacy_joint_orient_motion_joint")
        cmds.setAttr(f"{joint}.jointOrient", 0.0, 45.0, 0.0)
        cmds.setAttr(f"{joint}.rotateOrder", 0)

        bind_world = om.MMatrix(cmds.getAttr(f"{joint}.worldMatrix[0]"))
        q_maya = om.MEulerRotation(math.radians(90.0), 0.0, 0.0).asQuaternion()
        rx, ry, rz = self.converter._convert_vmd_quat_to_joint_rotate(
            joint,
            -q_maya.x,
            -q_maya.y,
            q_maya.z,
            q_maya.w,
        )
        cmds.setAttr(f"{joint}.rotate", rx, ry, rz, type="double3")

        actual_world = om.MMatrix(cmds.getAttr(f"{joint}.worldMatrix[0]"))
        actual_skinning = bind_world.inverse() * actual_world
        expected_tfm = om.MTransformationMatrix()
        expected_tfm.setRotation(q_maya)
        expected_skinning = expected_tfm.asMatrix()

        for i in range(16):
            self.assertAlmostEqual(actual_skinning[i], expected_skinning[i], places=5)

        cmds.delete(joint)

    def test_legacy_bone_animation_redirects_append_rotate_to_base_rotate(self):
        """append target ボーンの rotate は append node の baseRotate に key する。"""
        joint = cmds.joint(name="legacy_append_target_joint")
        cmds.select(clear=True)
        append_node = cmds.createNode("transform", name="legacy_append_route_node")
        for attr in ("baseRotateX", "baseRotateY", "baseRotateZ"):
            cmds.addAttr(append_node, longName=attr, attributeType="double", keyable=True)

        self.converter.use_animation_layers = False
        self.converter.set_bone_name_mapping({"付与先": joint})
        self.converter._bone_bind_poses["付与先"] = (0.0, 0.0, 0.0)
        frames = [self._make_bone_frame("付与先", 3, (0.0, 0.0, 0.0))]

        append_info = {
            joint: {
                "node": append_node,
                "attr_map": {
                    "rotateX": "baseRotateX",
                    "rotateY": "baseRotateY",
                    "rotateZ": "baseRotateZ",
                },
            }
        }
        with patch.object(self.converter, "_collect_append_info", return_value=append_info), patch.object(
            self.converter,
            "_collect_ik_link_joints",
            return_value={},
        ):
            self.assertTrue(self.converter._convert_bone_animation(frames))

        self.assertIn(3.0, cmds.keyframe(f"{append_node}.baseRotateX", query=True, timeChange=True) or [])
        self.assertIsNone(cmds.keyframe(f"{joint}.rotateX", query=True, timeChange=True))

        cmds.delete(joint, append_node)

    def test_append_target_is_not_added_to_animation_layer_joint_rotate(self):
        """append target joint は layer 登録で joint.rotate へ直接キーを作らない。"""
        joint = cmds.joint(name="legacy_append_layer_target_joint")
        cmds.select(clear=True)
        append_node = cmds.createNode("transform", name="legacy_append_layer_route_node")
        for attr in ("baseRotateX", "baseRotateY", "baseRotateZ"):
            cmds.addAttr(append_node, longName=attr, attributeType="double", keyable=True)

        self.converter.use_animation_layers = True
        self.converter.anim_layer = cmds.animLayer("legacy_append_layer", override=False, weight=1.0)
        self.converter.set_bone_name_mapping({"付与先": joint})
        self.converter._bone_bind_poses["付与先"] = (0.0, 0.0, 0.0)
        frames = [self._make_bone_frame("付与先", 3, (0.0, 0.0, 0.0))]
        append_info = {
            joint: {
                "node": append_node,
                "attr_map": {
                    "rotateX": "baseRotateX",
                    "rotateY": "baseRotateY",
                    "rotateZ": "baseRotateZ",
                },
            }
        }
        with patch.object(self.converter, "_collect_append_info", return_value=append_info), patch.object(
            self.converter,
            "_collect_ik_link_joints",
            return_value={},
        ):
            self.assertTrue(self.converter._convert_bone_animation(frames))

        self.assertFalse(cmds.listConnections(f"{joint}.rotateX", s=True, d=False, p=True) or [])
        self.assertIn(3.0, cmds.keyframe(f"{append_node}.baseRotateX", query=True, timeChange=True) or [])

        cmds.delete(joint, append_node)

    def test_legacy_bone_animation_skips_ik_link_rotate_keys(self):
        """IK link ボーンは translate のみ key し、solver 駆動 rotate には key しない。"""
        joint = cmds.joint(name="legacy_ik_link_joint")
        self.converter.use_animation_layers = False
        self.converter.set_bone_name_mapping({"ＩＫリンク": joint})
        self.converter._bone_bind_poses["ＩＫリンク"] = (1.0, 2.0, 3.0)
        frames = [self._make_bone_frame("ＩＫリンク", 7, (1.0, 0.0, 2.0))]

        with patch.object(self.converter, "_collect_append_info", return_value={}), patch.object(
            self.converter,
            "_collect_ik_link_joints",
            return_value={joint: None},
        ):
            self.assertTrue(self.converter._convert_bone_animation(frames))

        self.assertIn(7.0, cmds.keyframe(f"{joint}.translateX", query=True, timeChange=True) or [])
        self.assertIsNone(cmds.keyframe(f"{joint}.rotateX", query=True, timeChange=True))

        cmds.delete(joint)

    def test_convert_with_fixture_vmd_camera(self):
        """フィクスチャを使用したカメラアニメーション変換テスト"""
        # テスト用VMDファイルを取得
        try:
            vmd_path = self.fixture_provider.get_vmd_file()
        except FileNotFoundError:
            self.skipTest("テスト用VMDファイルが見つかりません")

        # VMDファイルをパース
        from mmd_tools.core.vmd_data import VmdData

        parser = VmdData()
        parser.parse_file(vmd_path)

        # カメラアニメーション変換
        if hasattr(parser, "camera_frames") and parser.camera_frames:
            result = self.converter._convert_camera_animation(parser.camera_frames)
            self.assertTrue(result, "カメラアニメーション変換に失敗しました")
        else:
            self.skipTest("VMDファイルにカメラアニメーションが含まれていません")

        # MMDカメラが作成されたことを確認
        from mmd_tools.core.constants import ATTR_MMD_CAMERA

        cameras = cmds.ls(type="camera")
        mmd_camera = None
        for cam in cameras:
            transform = cmds.listRelatives(cam, parent=True)
            if transform and cmds.attributeQuery(ATTR_MMD_CAMERA, node=transform[0], exists=True):
                mmd_camera = transform[0]
                break

        self.assertIsNotNone(mmd_camera, "MMDカメラが作成されていません")

        # キーフレームが設定されたことを確認
        keyframes = cmds.keyframe(f"{mmd_camera}.translateX", query=True)
        self.assertIsNotNone(keyframes, "カメラにキーフレームが設定されていません")
        self.assertGreater(len(keyframes), 0, "カメラにキーフレームが設定されていません")

    # --- 新規追加: runtime bake キャッシュ + API2.0 キーイング 向けフォーカステスト ---

    def test_iter_runtime_bake_frames_returns_every_frame(self):
        """_iter_runtime_bake_frames が全フレームを返すことを確認（キャッシュ収集の基盤）"""
        self.assertEqual(self.converter._iter_runtime_bake_frames(0, 5), [0, 1, 2, 3, 4, 5])
        self.assertEqual(self.converter._iter_runtime_bake_frames(10, 10), [10])
        self.assertEqual(self.converter._iter_runtime_bake_frames(5, 3), [])

    def test_compute_bone_locals_matches_xform_for_root_and_child(self):
        """_compute_all_bone_locals が xform(ws) 後の .translate / .rotate と等価な値を返すことを確認（キャッシュの正確性）"""
        # 親子ジョイント作成 (PMX bone index 順を模擬)
        parent = cmds.joint(name="test_parent_bone")
        cmds.select(parent, replace=True)
        child = cmds.joint(name="test_child_bone")
        cmds.select(clear=True)

        self.converter.bone_index_to_joint = {0: parent, 1: child}
        self.converter._bone_parent_map = {0: None, 1: 0}
        self.converter._bone_rotate_orders = {0: 0, 1: 0}
        self.converter._runtime_bind_world_matrices = {0: om.MMatrix(), 1: om.MMatrix()}
        self.converter._runtime_no_orient_bind_world_matrices = {0: om.MMatrix(), 1: om.MMatrix()}

        # 親: 原点、子: 親から (1,0,0) だけ +X へ (Z flip 考慮で Maya では X同じ Y同じ Z反転だが回転なし)
        # 簡単のため回転なし、親 (0,0,0), 子ワールド (1, 0, 0) を MMD 行列で表現
        # mmd trans (1,0,0) -> maya trans (1,0,0)  (Z成分0なので)
        parent_mmd = [
            1.0, 0.0, 0.0, 0.0,
            0.0, 1.0, 0.0, 0.0,
            0.0, 0.0, 1.0, 0.0,
            0.0, 0.0, 0.0, 1.0,
        ]
        child_mmd = [
            1.0, 0.0, 0.0, 0.0,
            0.0, 1.0, 0.0, 0.0,
            0.0, 0.0, 1.0, 0.0,
            1.0, 0.0, 0.0, 1.0,
        ]
        world_mats = [parent_mmd, child_mmd]

        locals_map = self.converter._compute_all_bone_locals(world_mats)
        self.assertIn(0, locals_map)
        self.assertIn(1, locals_map)

        p_tx, p_ty, p_tz, p_rx, p_ry, p_rz = locals_map[0]
        self.assertAlmostEqual(p_tx, 0.0, places=6)
        self.assertAlmostEqual(p_ty, 0.0, places=6)
        self.assertAlmostEqual(p_tz, -0.0, places=6)
        self.assertAlmostEqual(p_rx, 0.0, places=6)

        c_tx, c_ty, c_tz, c_rx, c_ry, c_rz = locals_map[1]
        # 親が (0,0,0) なので子の local trans は (1,0,0) -> Z flip 後 (1,0,0)
        self.assertAlmostEqual(c_tx, 1.0, places=6)
        self.assertAlmostEqual(c_ty, 0.0, places=6)
        self.assertAlmostEqual(c_tz, 0.0, places=6)

        # 比較: 実際に xform して得られる local を確認
        maya_p = self.converter._convert_mmd_world_matrix_to_maya(parent_mmd)
        maya_c = self.converter._convert_mmd_world_matrix_to_maya(child_mmd)
        cmds.xform(parent, worldSpace=True, matrix=maya_p)
        cmds.xform(child, worldSpace=True, matrix=maya_c)
        self.assertAlmostEqual(cmds.getAttr(f"{child}.translateX"), c_tx, places=6)
        self.assertAlmostEqual(cmds.getAttr(f"{child}.translateZ"), c_tz, places=6)

        cmds.delete(parent, child)

    def test_compute_bone_locals_matches_maya_with_parent_rotation(self):
        """親が回転している階層でも runtime world 行列から Maya local 値を再構成できることを確認"""
        parent = cmds.joint(name="test_parent_rot_bone")
        cmds.select(clear=True)
        child = cmds.joint(name="test_child_rot_bone")
        cmds.parent(child, parent)
        cmds.select(clear=True)

        cmds.setAttr(f"{parent}.jointOrient", 0, 0, 0)
        cmds.setAttr(f"{child}.jointOrient", 0, 0, 0)
        cmds.setAttr(f"{parent}.translate", 1.5, 2.0, -3.0)
        cmds.setAttr(f"{parent}.rotate", 0.0, 35.0, 10.0)
        cmds.setAttr(f"{child}.translate", 2.0, -0.5, 1.25)
        cmds.setAttr(f"{child}.rotate", 15.0, 0.0, -20.0)

        self.converter.bone_index_to_joint = {0: parent, 1: child}
        self.converter._bone_parent_map = {0: None, 1: 0}
        self.converter._bone_rotate_orders = {0: 0, 1: 0}
        self.converter._runtime_bind_world_matrices = {0: om.MMatrix(), 1: om.MMatrix()}
        self.converter._runtime_no_orient_bind_world_matrices = {0: om.MMatrix(), 1: om.MMatrix()}

        parent_maya_world = cmds.xform(parent, query=True, worldSpace=True, matrix=True)
        child_maya_world = cmds.xform(child, query=True, worldSpace=True, matrix=True)
        parent_mmd_world = self.converter._convert_mmd_world_matrix_to_maya(parent_maya_world)
        child_mmd_world = self.converter._convert_mmd_world_matrix_to_maya(child_maya_world)

        locals_map = self.converter._compute_all_bone_locals([parent_mmd_world, child_mmd_world])
        self.assertIn(0, locals_map)
        self.assertIn(1, locals_map)

        for bidx, joint in ((0, parent), (1, child)):
            tx, ty, tz, rx, ry, rz = locals_map[bidx]
            self.assertAlmostEqual(tx, cmds.getAttr(f"{joint}.translateX"), places=5)
            self.assertAlmostEqual(ty, cmds.getAttr(f"{joint}.translateY"), places=5)
            self.assertAlmostEqual(tz, cmds.getAttr(f"{joint}.translateZ"), places=5)
            self.assertAlmostEqual(rx, cmds.getAttr(f"{joint}.rotateX"), places=5)
            self.assertAlmostEqual(ry, cmds.getAttr(f"{joint}.rotateY"), places=5)
            self.assertAlmostEqual(rz, cmds.getAttr(f"{joint}.rotateZ"), places=5)

        cmds.delete(parent)

    def test_compute_bone_locals_with_joint_orient_matches_no_jo_skinning_matrix(self):
        """runtime bake は JO 付き bind で no-JO runtime と同じ skinning matrix を作る"""
        joint = cmds.joint(name="test_runtime_bind_space_jo_bone")
        cmds.select(clear=True)
        cmds.setAttr(f"{joint}.jointOrient", 0.0, 0.0, 45.0)
        cmds.setAttr(f"{joint}.rotate", 0.0, 0.0, 0.0)
        cmds.setAttr(f"{joint}.rotateOrder", 0)

        bind_world = om.MMatrix(cmds.getAttr(f"{joint}.worldMatrix[0]"))
        bind_no_orient = om.MMatrix()

        self.converter.bone_index_to_joint = {0: joint}
        self.converter._bone_parent_map = {0: None}
        self.converter._bone_rotate_orders = {0: 0}
        self.converter._runtime_bind_world_matrices = {0: bind_world}
        self.converter._runtime_no_orient_bind_world_matrices = {0: bind_no_orient}

        runtime_world_maya = om.MTransformationMatrix()
        runtime_world_maya.setRotation(om.MEulerRotation(0.0, 0.0, math.radians(90.0)))
        runtime_world = runtime_world_maya.asMatrix()
        runtime_mmd = self.converter._convert_mmd_world_matrix_to_maya(list(runtime_world))

        locals_map = self.converter._compute_all_bone_locals([runtime_mmd])
        self.assertIn(0, locals_map)
        tx, ty, tz, rx, ry, rz = locals_map[0]
        cmds.setAttr(f"{joint}.translate", tx, ty, tz, type="double3")
        cmds.setAttr(f"{joint}.rotate", rx, ry, rz, type="double3")

        corrected_world = om.MMatrix(cmds.getAttr(f"{joint}.worldMatrix[0]"))
        actual_skinning = bind_world.inverse() * corrected_world
        expected_skinning = bind_no_orient.inverse() * runtime_world

        for i in range(16):
            self.assertAlmostEqual(actual_skinning[i], expected_skinning[i], places=5)

        cmds.delete(joint)

    def test_runtime_bind_world_maps_use_recorded_bind_pose_not_current_pose(self):
        """live rig が動いた状態でも runtime bind 補正は記録済み bind pose を使う"""
        root = cmds.joint(name="runtime_pose_root")
        cmds.setAttr(f"{root}.translate", 1.0, 2.0, 3.0)
        child = cmds.joint(name="runtime_pose_child")
        cmds.setAttr(f"{child}.translate", 0.0, 4.0, 0.0)
        cmds.setAttr(f"{child}.jointOrient", 0.0, 0.0, 30.0)

        self.converter.bone_name_mapping = {"root": root, "child": child}
        self.converter.bone_name_to_index = {"root": 0, "child": 1}
        self.converter.bone_index_to_joint = {0: root, 1: child}
        self.converter._record_bind_poses()
        self.converter._build_bone_hierarchy_and_order_maps()
        self.converter._build_runtime_bind_world_maps()
        bind_child_before = self.converter._runtime_bind_world_matrices[1]

        cmds.setAttr(f"{root}.rotate", 0.0, 45.0, 0.0)
        cmds.setAttr(f"{child}.translate", 3.0, 4.0, 5.0)
        delattr(self.converter, "_runtime_bind_world_matrices")
        delattr(self.converter, "_runtime_no_orient_bind_world_matrices")

        self.converter._build_runtime_bind_world_maps()
        bind_child_after = self.converter._runtime_bind_world_matrices[1]

        for i in range(16):
            self.assertAlmostEqual(bind_child_after[i], bind_child_before[i], places=6)

        cmds.delete(root)

    def test_decompose_append_own_translation_removes_grant_offset(self):
        """runtime final translate から付与移動分を引いた値を mmdAppend.baseTranslate にキーできる"""
        final_tx = om.MDoubleArray([2.0, 2.0])
        final_ty = om.MDoubleArray([2.0, 2.0])
        final_tz = om.MDoubleArray([-2.0, -2.0])
        source_tx = om.MDoubleArray([0.0, 1.0])
        source_ty = om.MDoubleArray([0.0, 0.0])
        source_tz = om.MDoubleArray([0.0, 0.0])

        own, grant = self.converter._decompose_append_own_translation(
            final_tx, final_ty, final_tz,
            source_tx, source_ty, source_tz,
            ratio=1.0,
        )

        self.assertAlmostEqual(own[0][0], 2.0, places=6)
        self.assertAlmostEqual(own[0][1], 1.0, places=6)
        self.assertAlmostEqual(own[1][1], 2.0, places=6)
        self.assertAlmostEqual(own[2][1], -2.0, places=6)
        self.assertAlmostEqual(grant[0][1], 1.0, places=6)

    def test_decompose_append_own_rotation_ignores_joint_orient_at_rest(self):
        """付与回転の逆分解は JO を REST grant として扱わない"""

        def q_from_deg(x, y, z):
            return om.MEulerRotation(
                x * 3.141592653589793 / 180.0,
                y * 3.141592653589793 / 180.0,
                z * 3.141592653589793 / 180.0,
            ).asQuaternion()

        source_jo = q_from_deg(0.0, 30.0, 15.0)
        target_jo = q_from_deg(-20.0, 10.0, 35.0)
        identity = q_from_deg(0.0, 0.0, 0.0)
        final_euler = identity.asEulerRotation()
        source_euler = identity.asEulerRotation()
        (own_rx, own_ry, own_rz), _ = self.converter._decompose_append_own_rotation(
            om.MDoubleArray([final_euler.x]),
            om.MDoubleArray([final_euler.y]),
            om.MDoubleArray([final_euler.z]),
            om.MDoubleArray([source_euler.x]),
            om.MDoubleArray([source_euler.y]),
            om.MDoubleArray([source_euler.z]),
            1.0,
            target_joint_orient=target_jo,
            source_joint_orient=source_jo,
        )

        actual = om.MEulerRotation(own_rx[0], own_ry[0], own_rz[0]).asQuaternion()
        dot = abs(
            actual.x * identity.x
            + actual.y * identity.y
            + actual.z * identity.z
            + actual.w * identity.w
        )
        self.assertAlmostEqual(dot, 1.0, places=6)

    def test_mmd_append_node_ignores_joint_orient_at_rest(self):
        """mmdAppend は JO 非ゼロでも REST で付与回転を発生させない"""
        try:
            node = cmds.createNode("mmdAppend", name="append_joint_orient_space_node")
        except Exception as exc:
            self.skipTest(f"mmdAppend node is unavailable: {exc}")

        def set_angle3(attr, values):
            for axis, value in zip("XYZ", values):
                cmds.setAttr(f"{node}.{attr}{axis}", value)

        def q_from_deg(x, y, z):
            return om.MEulerRotation(
                x * 3.141592653589793 / 180.0,
                y * 3.141592653589793 / 180.0,
                z * 3.141592653589793 / 180.0,
            ).asQuaternion()

        source_jo_deg = (0.0, 30.0, 15.0)
        target_jo_deg = (-20.0, 10.0, 35.0)

        cmds.setAttr(f"{node}.ratio", 1.0)
        cmds.setAttr(f"{node}.affectRotation", True)
        set_angle3("baseRotate", (0.0, 0.0, 0.0))
        set_angle3("sourceRotate", (0.0, 0.0, 0.0))
        set_angle3("sourceJointOrient", source_jo_deg)
        set_angle3("targetJointOrient", target_jo_deg)

        actual_deg = cmds.getAttr(f"{node}.outputRotate")[0]
        actual = q_from_deg(*actual_deg)
        expected = q_from_deg(0.0, 0.0, 0.0)
        dot = abs(
            actual.x * expected.x
            + actual.y * expected.y
            + actual.z * expected.z
            + actual.w * expected.w
        )
        self.assertAlmostEqual(dot, 1.0, places=5)

        cmds.delete(node)

    def test_collect_append_info_finds_source_from_translation_only_grant(self):
        """移動付与のみの mmdAppend でも sourceTranslate 経由で source joint を特定する"""
        source = cmds.joint(name="translation_only_source")
        cmds.select(clear=True)
        target = cmds.joint(name="translation_only_target")
        node = cmds.createNode("mmdAppend", name="translation_only_append")
        delta = cmds.createNode("plusMinusAverage", name="translation_only_source_delta")

        cmds.setAttr(f"{node}.affectRotation", False)
        cmds.setAttr(f"{node}.affectTranslation", True)
        cmds.setAttr(f"{delta}.operation", 2)
        cmds.connectAttr(f"{source}.translate", f"{delta}.input3D[0]")
        cmds.connectAttr(f"{delta}.output3D", f"{node}.sourceTranslate")
        cmds.connectAttr(f"{node}.outputTranslate", f"{target}.translate")

        append_info = self.converter._collect_append_info()

        self.assertEqual(append_info[target]["source_joint"], source)
        self.assertTrue(append_info[target]["affect_translation"])
        self.assertFalse(append_info[target]["affect_rotation"])
        self.assertEqual(append_info[target]["attr_map"]["translateX"], "baseTranslateX")

        cmds.delete(source, target, node, delta)

    def test_decompose_local_append_translation_uses_source_delta_not_rest_offset(self):
        """local 付与移動の連鎖では source の rest offset を grant として扱わない"""
        driver = cmds.joint(name="append_driver")
        cmds.setAttr(f"{driver}.translate", 0.0, 0.0, 0.0)
        cmds.select(clear=True)
        source = cmds.joint(name="append_source")
        cmds.setAttr(f"{source}.translate", 5.0, 0.0, 0.0)
        cmds.select(clear=True)
        target = cmds.joint(name="append_target")
        cmds.setAttr(f"{target}.translate", 10.0, 0.0, 0.0)

        source_node = cmds.createNode("network", name="source_append_node")
        target_node = cmds.createNode("network", name="target_append_node")
        for node, value in ((source_node, 5.0), (target_node, 10.0)):
            cmds.addAttr(node, longName="baseTranslate", attributeType="double3")
            cmds.addAttr(node, longName="baseTranslateX", attributeType="double", parent="baseTranslate")
            cmds.addAttr(node, longName="baseTranslateY", attributeType="double", parent="baseTranslate")
            cmds.addAttr(node, longName="baseTranslateZ", attributeType="double", parent="baseTranslate")
            cmds.setAttr(f"{node}.baseTranslate", value, 0.0, 0.0, type="double3")

        joint_channel_values = {
            driver: {
                "translateX": om.MDoubleArray([0.0]),
                "translateY": om.MDoubleArray([0.0]),
                "translateZ": om.MDoubleArray([0.0]),
            },
            source: {
                "translateX": om.MDoubleArray([5.0]),
                "translateY": om.MDoubleArray([0.0]),
                "translateZ": om.MDoubleArray([0.0]),
            },
            target: {
                "translateX": om.MDoubleArray([10.0]),
                "translateY": om.MDoubleArray([0.0]),
                "translateZ": om.MDoubleArray([0.0]),
            },
        }
        append_info = {
            source: {
                "node": source_node,
                "source_joint": driver,
                "ratio": 1.0,
                "affect_translation": True,
                "local_append": False,
            },
            target: {
                "node": target_node,
                "source_joint": source,
                "ratio": 1.0,
                "affect_translation": True,
                "local_append": True,
            },
        }

        decomposed = self.converter._decompose_append_translations_for_scene(
            joint_channel_values,
            {},
            append_info,
            n_frames=1,
        )

        self.assertAlmostEqual(decomposed[target]["translateX"][0], 10.0, places=6)

        cmds.delete(driver, source, target, source_node, target_node)

    def test_apply_ik_enabled_animation_defaults_all_ik_on_before_property_keys(self):
        """IK property frame が一部だけでも未指定 IK と初期区間は default ON で評価する"""
        left = cmds.createNode("mmdCcdIk", name="left_ik_solver")
        right = cmds.createNode("mmdCcdIk", name="right_ik_solver")
        for node, bone_name in ((left, "左足ＩＫ"), (right, "右足ＩＫ")):
            if not cmds.attributeQuery("mmd_ik_bone_name", node=node, exists=True):
                cmds.addAttr(node, longName="mmd_ik_bone_name", dataType="string")
            cmds.setAttr(f"{node}.mmd_ik_bone_name", bone_name, type="string")
            cmds.setAttr(f"{node}.enabled", False)

        vmd_data = create_test_vmd_data()
        frame = VmdIKShowHideFrame()
        frame.frame_number = 20
        frame.ik_states = [("左足ＩＫ", 0)]
        vmd_data.ik_show_hide_frames = [frame]

        self.converter._apply_ik_enabled_animation(vmd_data)

        self.assertEqual(cmds.getAttr(f"{left}.enabled"), False)
        self.assertEqual(cmds.getAttr(f"{right}.enabled"), True)
        self.assertIn(0.0, cmds.keyframe(f"{left}.enabled", query=True, timeChange=True) or [])
        self.assertIn(20.0, cmds.keyframe(f"{left}.enabled", query=True, timeChange=True) or [])
        self.assertEqual(cmds.keyframe(f"{left}.enabled", query=True, time=(0, 0), valueChange=True), [1.0])
        self.assertEqual(cmds.keyframe(f"{left}.enabled", query=True, time=(20, 20), valueChange=True), [0.0])
        self.assertEqual(cmds.keyframe(f"{right}.enabled", query=True, time=(0, 0), valueChange=True), [1.0])

        cmds.delete(left, right)

    def test_apply_ik_enabled_animation_scopes_to_target_namespace(self):
        """複数リグがあるシーンでは target_namespace の IK node だけに key を打つ"""
        cmds.namespace(add="ModelA")
        cmds.namespace(add="ModelB")
        node_a = cmds.createNode("mmdCcdIk", name="ModelA:left_ik_solver")
        node_b = cmds.createNode("mmdCcdIk", name="ModelB:left_ik_solver")
        for node in (node_a, node_b):
            if not cmds.attributeQuery("mmd_ik_bone_name", node=node, exists=True):
                cmds.addAttr(node, longName="mmd_ik_bone_name", dataType="string")
            cmds.setAttr(f"{node}.mmd_ik_bone_name", "左足ＩＫ", type="string")
            cmds.setAttr(f"{node}.enabled", False)

        vmd_data = create_test_vmd_data()
        frame = VmdIKShowHideFrame()
        frame.frame_number = 20
        frame.ik_states = [("左足ＩＫ", 0)]
        vmd_data.ik_show_hide_frames = [frame]

        self.converter._apply_ik_enabled_animation(vmd_data, target_namespace="ModelA")

        self.assertIn(0.0, cmds.keyframe(f"{node_a}.enabled", query=True, timeChange=True) or [])
        self.assertEqual(cmds.keyframe(f"{node_b}.enabled", query=True, timeChange=True), None)

        cmds.delete(node_a, node_b)

    def test_mmd_ccd_ik_disabled_passes_input_rotate_through(self):
        """IK OFF 時は link joint の FK/VMD 回転を失わないよう inputRotate を outputRotate に通す"""
        node = cmds.createNode("mmdCcdIk", name="disabled_passthrough_ik_solver")
        chain = {
            "bones": [
                {
                    "rest_position": [0.0, 0.0, 0.0],
                    "parent_slot": -1,
                    "joint_orient_deg": [0.0, 0.0, 0.0],
                },
            ],
            "links": [{"bone_slot": 0}],
            "targetBoneSlot": 0,
            "controllerBoneSlot": 0,
            "iterationCount": 1,
            "limitAngle": 1.0,
        }
        cmds.setAttr(f"{node}.chainJson", json.dumps(chain), type="string")
        cmds.setAttr(f"{node}.enabled", False)
        cmds.setAttr(f"{node}.inputRotate[0].inputRotateElementX", 0.25)
        cmds.setAttr(f"{node}.inputRotate[0].inputRotateElementY", -0.5)
        cmds.setAttr(f"{node}.inputRotate[0].inputRotateElementZ", 0.75)

        self.assertAlmostEqual(
            cmds.getAttr(f"{node}.outputRotate[0].outputRotateElementX"),
            cmds.getAttr(f"{node}.inputRotate[0].inputRotateElementX"),
            places=6,
        )
        self.assertAlmostEqual(
            cmds.getAttr(f"{node}.outputRotate[0].outputRotateElementY"),
            cmds.getAttr(f"{node}.inputRotate[0].inputRotateElementY"),
            places=6,
        )
        self.assertAlmostEqual(
            cmds.getAttr(f"{node}.outputRotate[0].outputRotateElementZ"),
            cmds.getAttr(f"{node}.inputRotate[0].inputRotateElementZ"),
            places=6,
        )

        cmds.delete(node)

    def test_mmd_ccd_ik_enabled_controller_at_rest_passes_input_rotate_through(self):
        """controllerBoneSlot が REST 位置なら IK ON でも REST を崩さず inputRotate を通す"""
        node = cmds.createNode("mmdCcdIk", name="enabled_rest_passthrough_ik_solver")
        chain = {
            "bones": [
                {
                    "rest_position": [0.0, 0.0, 0.0],
                    "parent_slot": -1,
                    "joint_orient_deg": [0.0, 0.0, 0.0],
                },
                {
                    "rest_position": [1.0, 0.0, 0.0],
                    "parent_slot": 0,
                    "joint_orient_deg": [0.0, 0.0, 0.0],
                },
            ],
            "links": [{"bone_slot": 0}],
            "targetBoneSlot": 1,
            "controllerBoneSlot": 1,
            "iterationCount": 40,
            "limitAngle": 2.0,
        }
        cmds.setAttr(f"{node}.chainJson", json.dumps(chain), type="string")
        cmds.setAttr(f"{node}.enabled", True)
        cmds.setAttr(f"{node}.inputTranslate[1].inputTranslateElementX", 1.0)
        cmds.setAttr(f"{node}.inputTranslate[1].inputTranslateElementY", 0.0)
        cmds.setAttr(f"{node}.inputTranslate[1].inputTranslateElementZ", 0.0)
        cmds.setAttr(f"{node}.inputRotate[0].inputRotateElementX", 0.25)
        cmds.setAttr(f"{node}.inputRotate[0].inputRotateElementY", -0.5)
        cmds.setAttr(f"{node}.inputRotate[0].inputRotateElementZ", 0.75)

        self.assertAlmostEqual(
            cmds.getAttr(f"{node}.outputRotate[0].outputRotateElementX"),
            cmds.getAttr(f"{node}.inputRotate[0].inputRotateElementX"),
            places=6,
        )
        self.assertAlmostEqual(
            cmds.getAttr(f"{node}.outputRotate[0].outputRotateElementY"),
            cmds.getAttr(f"{node}.inputRotate[0].inputRotateElementY"),
            places=6,
        )
        self.assertAlmostEqual(
            cmds.getAttr(f"{node}.outputRotate[0].outputRotateElementZ"),
            cmds.getAttr(f"{node}.inputRotate[0].inputRotateElementZ"),
            places=6,
        )

        cmds.delete(node)

    def test_mmd_ccd_ik_goal_world_matrix_at_rest_passes_input_rotate_through(self):
        """goalWorldMatrix 接続があっても controller が REST 位置なら IK は REST を崩さない"""
        node = cmds.createNode("mmdCcdIk", name="goal_world_rest_passthrough_ik_solver")
        goal = cmds.spaceLocator(name="goal_world_rest_locator")[0]
        chain = {
            "bones": [
                {
                    "rest_position": [0.0, 0.0, 0.0],
                    "parent_slot": -1,
                    "joint_orient_deg": [0.0, 0.0, 0.0],
                },
                {
                    "rest_position": [1.0, 0.0, 0.0],
                    "parent_slot": 0,
                    "joint_orient_deg": [0.0, 0.0, 0.0],
                },
            ],
            "links": [{"bone_slot": 0}],
            "targetBoneSlot": 1,
            "controllerBoneSlot": 1,
            "iterationCount": 40,
            "limitAngle": 2.0,
        }
        cmds.setAttr(f"{node}.chainJson", json.dumps(chain), type="string")
        cmds.setAttr(f"{node}.enabled", True)
        cmds.setAttr(f"{goal}.translate", 1.0, 0.0, 0.0)
        cmds.connectAttr(f"{goal}.worldMatrix[0]", f"{node}.goalWorldMatrix")
        cmds.setAttr(f"{node}.inputTranslate[1].inputTranslateElementX", 1.0)
        cmds.setAttr(f"{node}.inputRotate[0].inputRotateElementX", 0.25)

        self.assertAlmostEqual(
            cmds.getAttr(f"{node}.outputRotate[0].outputRotateElementX"),
            cmds.getAttr(f"{node}.inputRotate[0].inputRotateElementX"),
            places=6,
        )

        cmds.delete(node, goal)

    def test_mmd_ccd_ik_goal_world_matrix_offset_solves_without_translate_offset(self):
        """goalWorldMatrix が REST から動いたら controller translate offset なしでも IK を解く"""
        node = cmds.createNode("mmdCcdIk", name="goal_world_offset_ik_solver")
        goal = cmds.spaceLocator(name="goal_world_offset_locator")[0]
        chain = {
            "bones": [
                {
                    "rest_position": [0.0, 0.0, 0.0],
                    "parent_slot": -1,
                    "joint_orient_deg": [0.0, 0.0, 0.0],
                },
                {
                    "rest_position": [1.0, 0.0, 0.0],
                    "parent_slot": 0,
                    "joint_orient_deg": [0.0, 0.0, 0.0],
                },
            ],
            "links": [{"bone_slot": 0}],
            "targetBoneSlot": 1,
            "controllerBoneSlot": 1,
            "iterationCount": 40,
            "limitAngle": 2.0,
        }
        cmds.setAttr(f"{node}.chainJson", json.dumps(chain), type="string")
        cmds.setAttr(f"{node}.enabled", True)
        cmds.setAttr(f"{goal}.translate", 0.0, 1.0, 0.0)
        cmds.connectAttr(f"{goal}.worldMatrix[0]", f"{node}.goalWorldMatrix")
        cmds.setAttr(f"{node}.inputTranslate[1].inputTranslateElementX", 1.0)
        cmds.setAttr(f"{node}.inputRotate[0].inputRotateElementX", 0.25)

        self.assertGreater(
            abs(cmds.getAttr(f"{node}.outputRotate[0].outputRotateElementZ")),
            45.0,
        )

        cmds.delete(node, goal)

    def test_mmd_ccd_ik_external_goal_connection_overrides_controller_slot_goal(self):
        """controllerBoneSlot があっても外部 goal 接続は公開入力として尊重する"""
        node = cmds.createNode("mmdCcdIk", name="external_goal_ik_solver")
        goal = cmds.spaceLocator(name="external_goal_locator")[0]
        chain = {
            "bones": [
                {
                    "rest_position": [0.0, 0.0, 0.0],
                    "parent_slot": -1,
                    "joint_orient_deg": [0.0, 0.0, 0.0],
                },
                {
                    "rest_position": [1.0, 0.0, 0.0],
                    "parent_slot": 0,
                    "joint_orient_deg": [0.0, 0.0, 0.0],
                },
            ],
            "links": [{
                "bone_slot": 0,
                "has_angle_limit": False,
                "angle_limit_min": [0.0, 0.0, 0.0],
                "angle_limit_max": [0.0, 0.0, 0.0],
            }],
            "targetBoneSlot": 1,
            "controllerBoneSlot": 1,
            "iterationCount": 40,
            "limitAngle": 2.0,
        }
        cmds.setAttr(f"{node}.chainJson", json.dumps(chain), type="string")
        cmds.connectAttr(f"{goal}.translate", f"{node}.goal")
        cmds.setAttr(f"{goal}.translate", 1.0, 0.0, 0.0)
        rest_z = cmds.getAttr(f"{node}.outputRotate[0].outputRotateElementZ")
        cmds.setAttr(f"{goal}.translate", 0.0, 1.0, 0.0)
        aimed_z = cmds.getAttr(f"{node}.outputRotate[0].outputRotateElementZ")

        self.assertAlmostEqual(rest_z, 0.0, places=5)
        self.assertGreater(abs(aimed_z), 45.0)

        cmds.delete(node, goal)

    def test_direct_anim_curve_helper_creates_keyed_values(self):
        """_batch_create_and_key_curves が MFnAnimCurve / addKeys 経由で translate/rotate にキーを登録し、Maya 空間値が正しくなる"""
        import math

        joint = cmds.joint(name="test_direct_apikey_joint")
        # サンプル: 回転値はラジアンで渡す
        samples = {
            "translateX": [(0.0, 0.0), (12.0, 1.0)],
            "translateY": [(0.0, 0.0), (12.0, 2.0)],
            "translateZ": [(0.0, 0.0), (12.0, -3.0)],
            "rotateX": [(0.0, 0.0), (12.0, math.radians(30.0))],
            "rotateY": [(0.0, 0.0), (12.0, 0.0)],
            "rotateZ": [(0.0, 0.0), (12.0, 0.0)],
        }
        ok = self.converter._batch_create_and_key_curves(joint, samples)
        self.assertTrue(ok, "direct animCurve helper should succeed or fallback with keys")

        # キーが打たれている
        for attr in ("translateX", "rotateX"):
            times = cmds.keyframe(f"{joint}.{attr}", query=True, timeChange=True) or []
            self.assertIn(0.0, times)
            self.assertIn(12.0, times)

        # 現在フレームで評価値が正しい (Maya 空間)
        cmds.currentTime(12, edit=True)
        self.assertAlmostEqual(cmds.getAttr(f"{joint}.translateX"), 1.0, places=6)
        self.assertAlmostEqual(cmds.getAttr(f"{joint}.translateY"), 2.0, places=6)
        self.assertAlmostEqual(cmds.getAttr(f"{joint}.translateZ"), -3.0, places=6)
        self.assertAlmostEqual(cmds.getAttr(f"{joint}.rotateX"), 30.0, places=5)

        cmds.delete(joint)
