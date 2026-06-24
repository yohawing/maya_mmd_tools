"""Bake vs Rig パリティ統合テスト

Bake モード (setup_rig=False, setup_bone_orientation=False) と
Rig モード (setup_rig=True, setup_bone_orientation=True) で
同一 PMX+VMD をインポートし、以下を比較する:

1. ボーンワールド変換 (worldMatrix → quaternion / position)
2. メッシュ頂点位置 (MFnMesh.getPoints kWorld)

PMX LOCAL_AXIS の jointOrient は Bake/Rig の両モードで同一に設定される。
アニメーション補正は jointOrient を読んで回転値を変換するため、ボーンの
ワールド変換と bind pose の skinCluster デルタが一致する。
"""

import math
import os
import unittest

import maya.cmds as cmds
import maya.api.OpenMaya as om

from mmd_tools.core import settings
from mmd_tools.core.native.mmd_anim_runtime import (
    MmdRuntimeClip,
    MmdRuntimeInstance,
    MmdRuntimeModel,
    is_mmd_runtime_available,
)
from mmd_tools.core.vmd_data import VmdData
from mmd_tools.converters.vmd_converter import VmdConverter
from mmd_tools.io.mmd_importer import import_mmd_file
from tests.common.maya_test_base import MayaTestBase
from tests.common.test_fixture_provider import TestFixtureProvider

TESTS_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")

PMX_FILE = os.path.join(TESTS_DATA_DIR, "mmt_test_model.pmx")
VMD_FILE = os.path.join(TESTS_DATA_DIR, "mmt_test_model_test_motion.vmd")
FOR_UNIT_TEST_DIR = os.path.join(TESTS_DATA_DIR, "for_unit_test")

FRAMES = [0, 10, 20, 30]
ORACLE_CASES = [
    {
        "name": "one_bone_cube",
        "pmx": os.path.join(FOR_UNIT_TEST_DIR, "test_1bone_cube.pmx"),
        "vmd": os.path.join(FOR_UNIT_TEST_DIR, "test_1bone_cube_motion.vmd"),
        "frames": [0, 10, 20, 30],
    },
    {
        "name": "append_bone",
        "pmx": os.path.join(FOR_UNIT_TEST_DIR, "test_append_bone.pmx"),
        "vmd": os.path.join(FOR_UNIT_TEST_DIR, "test_append_bone.vmd"),
        "frames": [0, 10, 20, 30],
    },
    {
        "name": "given_bone",
        "pmx": os.path.join(FOR_UNIT_TEST_DIR, "test_given_bone.pmx"),
        "vmd": os.path.join(FOR_UNIT_TEST_DIR, "test_given_bone.vmd"),
        "frames": [0, 10, 20, 30],
    },
    {
        "name": "mmt_ik_smoke",
        "pmx": PMX_FILE,
        "vmd": os.path.join(TESTS_DATA_DIR, "mmt_test_model_ik_test_motion.vmd"),
        "frames": [0, 10, 20, 30],
    },
    {
        "name": "mmt_motion",
        "pmx": PMX_FILE,
        "vmd": VMD_FILE,
        "frames": FRAMES,
    },
]
VERTEX_CASES = [
    {
        "name": "one_bone_cube",
        "pmx": os.path.join(FOR_UNIT_TEST_DIR, "test_1bone_cube.pmx"),
        "vmd": os.path.join(FOR_UNIT_TEST_DIR, "test_1bone_cube_motion.vmd"),
        "frames": [0, 10, 20, 30],
    },
    {
        "name": "mmt_motion",
        "pmx": PMX_FILE,
        "vmd": VMD_FILE,
        "frames": FRAMES,
    },
]

WORLD_ROT_THRESHOLD_DEG = 0.5
WORLD_POS_THRESHOLD = 0.05
VERTEX_REST_THRESHOLD = 0.05
VERTEX_ANIM_THRESHOLD = 0.1


def _quat_angle_deg(q1, q2):
    """2 つの MQuaternion 間の角度差 (degrees)"""
    dot = q1.x * q2.x + q1.y * q2.y + q1.z * q2.z + q1.w * q2.w
    dot = max(-1.0, min(1.0, abs(dot)))
    return math.degrees(2.0 * math.acos(dot))


def _euclidean(a, b):
    return math.sqrt(sum((ai - bi) ** 2 for ai, bi in zip(a, b)))


def _get_mesh_vertices_world(mesh_shape):
    """MFnMesh.getPoints(kWorld) で全頂点のワールド座標を取得"""
    sel = om.MSelectionList()
    sel.add(mesh_shape)
    dag = sel.getDagPath(0)
    fn_mesh = om.MFnMesh(dag)
    return fn_mesh.getPoints(om.MSpace.kWorld)


def _capture_bone_world_transforms(frames):
    """全 MMD ボーンのワールド変換を各フレームでキャプチャ"""
    joints = [j for j in cmds.ls(type="joint")
              if cmds.attributeQuery("mmd_bone_index", node=j, exists=True)]
    result = {}
    for frame in frames:
        cmds.currentTime(frame, edit=True)
        cmds.refresh(force=True)
        for jnt in joints:
            wm = cmds.xform(jnt, query=True, worldSpace=True, matrix=True)
            mat = om.MMatrix(wm)
            tfm = om.MTransformationMatrix(mat)
            quat = tfm.rotation(asQuaternion=True)
            pos = tfm.translation(om.MSpace.kWorld)
            result.setdefault(jnt, {})[frame] = {
                "quat": quat,
                "pos": (pos.x, pos.y, pos.z),
            }
    return result


def _capture_bone_world_transforms_by_index(frames):
    """PMX bone index ごとに Maya joint world transform をキャプチャする"""
    joints = [j for j in cmds.ls(type="joint")
              if cmds.attributeQuery("mmd_bone_index", node=j, exists=True)]
    indexed_joints = {}
    for joint in joints:
        indexed_joints[int(cmds.getAttr(f"{joint}.mmd_bone_index"))] = joint

    result = {}
    for frame in frames:
        cmds.currentTime(frame, edit=True)
        cmds.refresh(force=True)
        for bone_index, joint in indexed_joints.items():
            wm = cmds.xform(joint, query=True, worldSpace=True, matrix=True)
            mat = om.MMatrix(wm)
            tfm = om.MTransformationMatrix(mat)
            quat = tfm.rotation(asQuaternion=True)
            pos = tfm.translation(om.MSpace.kWorld)
            result.setdefault(bone_index, {})[frame] = {
                "joint": joint,
                "quat": quat,
                "pos": (pos.x, pos.y, pos.z),
            }
    return result


def _capture_runtime_oracle_world_transforms(pmx_path, vmd_path, frames):
    """mmd-anim runtime の PMX bone index 順 world transform を Maya 座標系で取得する"""
    if not is_mmd_runtime_available():
        raise unittest.SkipTest("mmd-anim runtime is not available")

    with open(pmx_path, "rb") as file:
        pmx_bytes = file.read()
    with open(vmd_path, "rb") as file:
        vmd_bytes = file.read()

    converter = VmdConverter()
    model = MmdRuntimeModel.from_pmx_bytes(pmx_bytes)
    if model is None:
        raise RuntimeError("runtime model creation failed")
    clip = MmdRuntimeClip.from_vmd_bytes_for_model(model, vmd_bytes)
    if clip is None:
        model.free()
        raise RuntimeError("runtime clip creation failed")
    instance = MmdRuntimeInstance.for_model(model)
    if instance is None:
        clip.free()
        model.free()
        raise RuntimeError("runtime instance creation failed")

    result = {}
    try:
        for frame in frames:
            if not instance.evaluate_clip_frame(clip, float(frame)):
                continue
            world_matrices = instance.get_world_matrices() or []
            for bone_index, mmd_matrix in enumerate(world_matrices):
                if not isinstance(mmd_matrix, (list, tuple)) or len(mmd_matrix) != 16:
                    continue
                maya_matrix = om.MMatrix(converter._convert_mmd_world_matrix_to_maya(list(mmd_matrix)))
                tfm = om.MTransformationMatrix(maya_matrix)
                quat = tfm.rotation(asQuaternion=True)
                pos = tfm.translation(om.MSpace.kWorld)
                result.setdefault(bone_index, {})[frame] = {
                    "quat": quat,
                    "pos": (pos.x, pos.y, pos.z),
                }
    finally:
        instance.free()
        clip.free()
        model.free()

    return result


def _capture_vertex_positions(mesh_transforms, frames):
    """root 以下の全メッシュ頂点のワールド座標を各フレームでキャプチャ"""
    transforms = [mesh_transforms] if isinstance(mesh_transforms, str) else list(mesh_transforms or [])
    result = {}
    for frame in frames:
        cmds.currentTime(frame, edit=True)
        cmds.refresh(force=True)
        frame_points = []
        for mesh_transform in transforms:
            shapes = cmds.listRelatives(mesh_transform, shapes=True, noIntermediate=True, fullPath=True) or []
            for shape in shapes:
                points = _get_mesh_vertices_world(shape)
                frame_points.extend((p.x, p.y, p.z) for p in points)
        result[frame] = frame_points
    return result


def _import_model_with_options(pmx_path, vmd_path, setup_rig, setup_bone_orientation):
    """PMX+VMD を指定オプションでインポートし root を返す"""
    settings.set("import.model.create_mmd_shaders", False)
    settings.set("import.rig.add_semi_standard_bones", False)

    root = import_mmd_file(pmx_path, options={
        "setup_rig": setup_rig,
        "setup_bone_orientation": setup_bone_orientation,
    })
    if root is None:
        raise RuntimeError(f"PMX import failed: {pmx_path}")

    cmds.select(root, replace=True)
    import_mmd_file(vmd_path, options={
        "target_model": root,
        "pmx_path": pmx_path,
    })
    return root


def _find_mesh_transform(root):
    """ルート以下の最初のメッシュ transform を返す"""
    transforms = _find_mesh_transforms(root)
    return transforms[0] if transforms else None


def _find_mesh_transforms(root):
    """ルート以下の全メッシュ transform を安定順で返す"""
    descendants = cmds.listRelatives(root, allDescendents=True, type="mesh", fullPath=True) or []
    transforms = []
    for shape in descendants:
        transform = cmds.listRelatives(shape, parent=True, fullPath=True)
        if transform and transform[0] not in transforms:
            transforms.append(transform[0])
    return sorted(transforms)


class TestBakeRigBoneParity(MayaTestBase):
    """Bake vs Rig: ボーンワールド変換の一致テスト"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if not os.path.exists(PMX_FILE):
            raise unittest.SkipTest(f"Test fixture not found: {PMX_FILE}")
        if not os.path.exists(VMD_FILE):
            raise unittest.SkipTest(f"Test fixture not found: {VMD_FILE}")

    def setUp(self):
        super().setUp()
        self.fixture_provider = TestFixtureProvider()

    def tearDown(self):
        self.fixture_provider.cleanup_temp_files()
        super().tearDown()

    def _import_bake(self):
        return _import_model_with_options(PMX_FILE, VMD_FILE, setup_rig=False, setup_bone_orientation=False)

    def _import_rig(self):
        return _import_model_with_options(PMX_FILE, VMD_FILE, setup_rig=True, setup_bone_orientation=True)

    def _assert_scene_positions_match_runtime_oracle(self, label, pmx_path=PMX_FILE, vmd_path=VMD_FILE, frames=FRAMES):
        actual = _capture_bone_world_transforms_by_index(frames)
        oracle = _capture_runtime_oracle_world_transforms(pmx_path, vmd_path, frames)

        pos_outliers = []
        common_bones = set(actual.keys()) & set(oracle.keys())
        self.assertGreater(len(common_bones), 0, "共通ボーンが見つからない")

        for bone_index in sorted(common_bones):
            joint = next(iter(actual[bone_index].values()))["joint"]
            for frame in frames:
                actual_frame = actual[bone_index].get(frame)
                oracle_frame = oracle[bone_index].get(frame)
                if not actual_frame or not oracle_frame:
                    continue

                dist = _euclidean(actual_frame["pos"], oracle_frame["pos"])
                if dist > WORLD_POS_THRESHOLD:
                    pos_outliers.append((joint, bone_index, frame, dist))

        failures = []
        if pos_outliers:
            pos_outliers.sort(key=lambda x: -x[3])
            failures.append(f"{label} world position mismatch ({len(pos_outliers)} outliers):")
            for joint, bone_index, frame, dist in pos_outliers[:20]:
                failures.append(
                    f"  bone[{bone_index}] {joint} @ frame {frame}: {dist:.4f} units"
                )

        if failures:
            self.fail("\n".join(failures))

    def test_runtime_bake_matches_mmd_anim_world_positions(self):
        """Bake mode の world position が mmd-anim runtime oracle と一致する"""
        for case in ORACLE_CASES:
            with self.subTest(case=case["name"]):
                try:
                    _import_model_with_options(
                        case["pmx"],
                        case["vmd"],
                        setup_rig=False,
                        setup_bone_orientation=False,
                    )
                    self._assert_scene_positions_match_runtime_oracle(
                        f"Bake:{case['name']}",
                        case["pmx"],
                        case["vmd"],
                        case["frames"],
                    )
                finally:
                    cmds.file(new=True, force=True)

    @unittest.skip("Sparse VMD import no longer uses runtime dense bake; replace with sparse/live-rig oracle gate")
    def test_rig_mode_matches_mmd_anim_world_positions(self):
        """Rig mode の world position が mmd-anim runtime oracle と一致する"""
        for case in ORACLE_CASES:
            with self.subTest(case=case["name"]):
                try:
                    _import_model_with_options(
                        case["pmx"],
                        case["vmd"],
                        setup_rig=True,
                        setup_bone_orientation=True,
                    )
                    self._assert_scene_positions_match_runtime_oracle(
                        f"Rig:{case['name']}",
                        case["pmx"],
                        case["vmd"],
                        case["frames"],
                    )
                finally:
                    cmds.file(new=True, force=True)

    @unittest.skip("Bake/Rig final-pose parity is not a valid sparse VMD/live-rig acceptance gate")
    def test_bone_world_rotation_parity(self):
        """Bake と Rig のボーンワールド回転が一致する"""
        self._import_bake()
        data_a = _capture_bone_world_transforms(FRAMES)
        cmds.file(new=True, force=True)

        self._import_rig()
        data_b = _capture_bone_world_transforms(FRAMES)

        outliers = []
        common_joints = set(data_a.keys()) & set(data_b.keys())
        self.assertGreater(len(common_joints), 0, "共通ボーンが見つからない")

        for jnt in sorted(common_joints):
            for frame in FRAMES:
                fa = data_a[jnt].get(frame)
                fb = data_b[jnt].get(frame)
                if not fa or not fb:
                    continue
                angle = _quat_angle_deg(fa["quat"], fb["quat"])
                if angle > WORLD_ROT_THRESHOLD_DEG:
                    outliers.append((jnt, frame, angle))

        if outliers:
            outliers.sort(key=lambda x: -x[2])
            msg_lines = [f"回転パリティ不一致 ({len(outliers)} outliers):"]
            for jnt, frame, angle in outliers[:20]:
                msg_lines.append(f"  {jnt} @ frame {frame}: {angle:.2f} deg")
            self.fail("\n".join(msg_lines))

    def test_direct_bake_convert_matches_runtime_world_positions(self):
        """Bake convert() 直呼びは保存済み source から runtime bake に戻り oracle と一致する"""
        settings.set("import.model.create_mmd_shaders", False)
        settings.set("import.rig.add_semi_standard_bones", False)

        root = import_mmd_file(PMX_FILE, options={
            "setup_rig": False,
            "setup_bone_orientation": False,
        })
        self.assertIsNotNone(root, "PMX import failed")

        vmd_data = VmdData().parse_file(VMD_FILE)
        converter = VmdConverter()
        converter.use_animation_layers = False
        self.assertTrue(converter.convert(vmd_data, vmd_bytes=None, pmx_bytes=None, pmx_path=None))

        actual = _capture_bone_world_transforms_by_index(FRAMES)
        oracle = _capture_runtime_oracle_world_transforms(PMX_FILE, VMD_FILE, FRAMES)

        outliers = []
        common_bones = set(actual.keys()) & set(oracle.keys())
        self.assertGreater(len(common_bones), 0, "共通ボーンが見つからない")

        for bone_index in sorted(common_bones):
            joint = next(iter(actual[bone_index].values()))["joint"]
            for frame in FRAMES:
                actual_frame = actual[bone_index].get(frame)
                oracle_frame = oracle[bone_index].get(frame)
                if not actual_frame or not oracle_frame:
                    continue
                dist = _euclidean(actual_frame["pos"], oracle_frame["pos"])
                if dist > WORLD_POS_THRESHOLD:
                    outliers.append((joint, bone_index, frame, dist))

        if outliers:
            outliers.sort(key=lambda x: -x[3])
            msg_lines = [f"Bake runtime world position mismatch ({len(outliers)} outliers):"]
            for joint, bone_index, frame, dist in outliers[:20]:
                msg_lines.append(
                    f"  bone[{bone_index}] {joint} @ frame {frame}: {dist:.4f} units"
                )
            self.fail("\n".join(msg_lines))

    @unittest.skip("Bake/Rig final-pose parity is not a valid sparse VMD/live-rig acceptance gate")
    def test_bone_world_position_parity(self):
        """Bake と Rig のボーンワールド位置が一致する"""
        self._import_bake()
        data_a = _capture_bone_world_transforms(FRAMES)
        cmds.file(new=True, force=True)

        self._import_rig()
        data_b = _capture_bone_world_transforms(FRAMES)

        outliers = []
        common_joints = set(data_a.keys()) & set(data_b.keys())
        self.assertGreater(len(common_joints), 0, "共通ボーンが見つからない")

        for jnt in sorted(common_joints):
            for frame in FRAMES:
                fa = data_a[jnt].get(frame)
                fb = data_b[jnt].get(frame)
                if not fa or not fb:
                    continue
                dist = _euclidean(fa["pos"], fb["pos"])
                if dist > WORLD_POS_THRESHOLD:
                    outliers.append((jnt, frame, dist))

        if outliers:
            outliers.sort(key=lambda x: -x[2])
            msg_lines = [f"位置パリティ不一致 ({len(outliers)} outliers):"]
            for jnt, frame, dist in outliers[:20]:
                msg_lines.append(f"  {jnt} @ frame {frame}: {dist:.4f} units")
            self.fail("\n".join(msg_lines))


class TestBakeRigVertexParity(MayaTestBase):
    """Bake vs Rig: メッシュ頂点位置の比較テスト

    3-way 比較:
    - A: Bake (setup_rig=False, setup_bone_orientation=False)
    - B: Rig, orientation flag off (setup_rig=True, setup_bone_orientation=False)
    - C: Rig, orientation flag on  (setup_rig=True, setup_bone_orientation=True) ← 本番設定
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if not os.path.exists(PMX_FILE):
            raise unittest.SkipTest(f"Test fixture not found: {PMX_FILE}")
        if not os.path.exists(VMD_FILE):
            raise unittest.SkipTest(f"Test fixture not found: {VMD_FILE}")

    def setUp(self):
        super().setUp()
        self.fixture_provider = TestFixtureProvider()

    def tearDown(self):
        self.fixture_provider.cleanup_temp_files()
        super().tearDown()

    def _capture(self, setup_rig, setup_bone_orientation, case=None):
        case = case or {"pmx": PMX_FILE, "vmd": VMD_FILE, "frames": FRAMES}
        root = _import_model_with_options(case["pmx"], case["vmd"], setup_rig, setup_bone_orientation)
        meshes = _find_mesh_transforms(root)
        self.assertTrue(meshes, "メッシュが見つからない")
        verts = _capture_vertex_positions(meshes, case["frames"])
        cmds.file(new=True, force=True)
        return verts

    def _compare_vertex_frames(self, verts_a, verts_b, label, frames=FRAMES):
        """フレームごとの頂点比較。(max_dist, mean_dist, frame_results) を返す"""
        frame_results = {}
        overall_max = 0.0
        overall_sum = 0.0
        overall_count = 0
        for frame in frames:
            pa = verts_a.get(frame, [])
            pb = verts_b.get(frame, [])
            if not pa or not pb:
                continue
            n = min(len(pa), len(pb))
            dists = [_euclidean(pa[i], pb[i]) for i in range(n)]
            max_d = max(dists) if dists else 0.0
            mean_d = sum(dists) / len(dists) if dists else 0.0
            frame_results[frame] = {"max": max_d, "mean": mean_d, "n": n}
            overall_max = max(overall_max, max_d)
            overall_sum += sum(dists)
            overall_count += len(dists)
        overall_mean = overall_sum / overall_count if overall_count else 0.0
        return overall_max, overall_mean, frame_results

    def _capture_bone_world_position(self, setup_rig, setup_bone_orientation, bone_names, frame):
        root = _import_model_with_options(PMX_FILE, VMD_FILE, setup_rig, setup_bone_orientation)
        self.assertIsNotNone(root, "PMX/VMD import failed")

        target_joint = None
        for joint in cmds.ls(type="joint") or []:
            if cmds.attributeQuery("mmd_bone_name", node=joint, exists=True):
                bone_name = cmds.getAttr(f"{joint}.mmd_bone_name")
                if bone_name in bone_names:
                    target_joint = joint
                    break
            if any(name in joint for name in bone_names):
                target_joint = joint
                break

        self.assertIsNotNone(target_joint, f"{bone_names} の joint が見つかりません")

        cmds.currentTime(frame, edit=True)
        cmds.refresh(force=True)
        matrix = om.MMatrix(cmds.xform(target_joint, query=True, worldSpace=True, matrix=True))
        position = om.MTransformationMatrix(matrix).translation(om.MSpace.kWorld)
        cmds.file(new=True, force=True)
        return position.x, position.y, position.z

    def test_rest_pose_bake_vs_rig_orientation_flag_off(self):
        """REST ポーズ (frame 0): A vs B で頂点が一致する"""
        rest_frames = [0]

        cmds.file(new=True, force=True)
        root_a = _import_model_with_options(PMX_FILE, VMD_FILE, setup_rig=False, setup_bone_orientation=False)
        meshes_a = _find_mesh_transforms(root_a)
        self.assertTrue(meshes_a)
        verts_a = _capture_vertex_positions(meshes_a, rest_frames)
        cmds.file(new=True, force=True)

        root_b = _import_model_with_options(PMX_FILE, VMD_FILE, setup_rig=True, setup_bone_orientation=False)
        meshes_b = _find_mesh_transforms(root_b)
        self.assertTrue(meshes_b)
        verts_b = _capture_vertex_positions(meshes_b, rest_frames)

        pa = verts_a.get(0, [])
        pb = verts_b.get(0, [])
        self.assertGreater(len(pa), 0, "頂点が取得できない")
        self.assertEqual(len(pa), len(pb), "A/B の頂点数不一致")

        n = len(pa)
        max_d = max(_euclidean(pa[i], pb[i]) for i in range(n))
        self.assertLessEqual(
            max_d, VERTEX_REST_THRESHOLD,
            f"REST ポーズ A vs B: max vertex dist = {max_d:.4f} (threshold {VERTEX_REST_THRESHOLD})"
        )

    def test_rest_pose_bake_vs_rig_with_jo(self):
        """REST ポーズ (frame 0): A vs C (Rig+JO) で頂点が一致する"""
        rest_frames = [0]

        cmds.file(new=True, force=True)
        root_a = _import_model_with_options(PMX_FILE, VMD_FILE, setup_rig=False, setup_bone_orientation=False)
        meshes_a = _find_mesh_transforms(root_a)
        self.assertTrue(meshes_a)
        verts_a = _capture_vertex_positions(meshes_a, rest_frames)
        cmds.file(new=True, force=True)

        root_c = _import_model_with_options(PMX_FILE, VMD_FILE, setup_rig=True, setup_bone_orientation=True)
        meshes_c = _find_mesh_transforms(root_c)
        self.assertTrue(meshes_c)
        verts_c = _capture_vertex_positions(meshes_c, rest_frames)

        pa = verts_a.get(0, [])
        pc = verts_c.get(0, [])
        self.assertGreater(len(pa), 0, "頂点が取得できない")
        self.assertEqual(len(pa), len(pc), "A/C の頂点数不一致")

        n = len(pa)
        max_d = max(_euclidean(pa[i], pc[i]) for i in range(n))
        self.assertLessEqual(
            max_d, VERTEX_REST_THRESHOLD,
            f"REST ポーズ A vs C: max vertex dist = {max_d:.4f} (threshold {VERTEX_REST_THRESHOLD}). "
            f"Rig+JO bind pose should match Bake."
        )

    def test_ik_knee_world_position_bake_vs_rig_parity(self):
        """IK link pre-rotation を含む左ひざ位置が Bake と Rig+JO で一致する。"""
        bone_names = {"左ひざ", "左膝"}
        frame = 10

        bake_pos = self._capture_bone_world_position(
            setup_rig=False,
            setup_bone_orientation=False,
            bone_names=bone_names,
            frame=frame,
        )
        rig_pos = self._capture_bone_world_position(
            setup_rig=True,
            setup_bone_orientation=True,
            bone_names=bone_names,
            frame=frame,
        )

        distance = _euclidean(bake_pos, rig_pos)
        self.assertLessEqual(
            distance,
            WORLD_POS_THRESHOLD,
            f"左ひざ frame {frame} の Bake/Rig+JO world position 差が大きすぎます: "
            f"{distance:.4f} units (threshold {WORLD_POS_THRESHOLD})",
        )

    @unittest.skip("Bake/Rig animated vertex parity is not a valid sparse VMD/live-rig acceptance gate")
    def test_bake_vs_rig_orientation_flag_off_vertex_parity(self):
        """A vs B: アニメーション時も頂点が近い"""
        for case in VERTEX_CASES:
            with self.subTest(case=case["name"]):
                verts_a = self._capture(setup_rig=False, setup_bone_orientation=False, case=case)
                verts_b = self._capture(setup_rig=True, setup_bone_orientation=False, case=case)

                max_d, mean_d, _ = self._compare_vertex_frames(verts_a, verts_b, "A vs B", case["frames"])
                self.assertLessEqual(
                    max_d, VERTEX_ANIM_THRESHOLD,
                    f"A vs B (Bake vs Rig flag-off, {case['name']}): max vertex dist = {max_d:.4f} "
                    f"(threshold {VERTEX_ANIM_THRESHOLD})"
                )

    def test_mmt_motion_bake_vs_rig_with_jo_vertex_parity(self):
        """TestModel の標準 VMD は Bake と Rig+JO の deformed mesh が一致する。"""
        case = {
            "name": "mmt_motion",
            "pmx": PMX_FILE,
            "vmd": VMD_FILE,
            "frames": FRAMES,
        }
        verts_a = self._capture(setup_rig=False, setup_bone_orientation=False, case=case)
        verts_c = self._capture(setup_rig=True, setup_bone_orientation=True, case=case)

        max_d, mean_d, frame_results = self._compare_vertex_frames(verts_a, verts_c, "A vs C", case["frames"])
        self.assertLessEqual(
            max_d,
            VERTEX_ANIM_THRESHOLD,
            f"A vs C (Bake vs Rig+JO, {case['name']}): max vertex dist = {max_d:.4f}, "
            f"mean = {mean_d:.4f}, frames = {frame_results} "
            f"(threshold {VERTEX_ANIM_THRESHOLD})."
        )

    @unittest.skip("Bake/Rig animated vertex parity is not a valid sparse VMD/live-rig acceptance gate")
    def test_bake_vs_rig_with_jo_vertex_parity(self):
        """A (Bake) vs C (Rig, JO=on): アニメーション時も頂点が近い"""
        for case in VERTEX_CASES:
            with self.subTest(case=case["name"]):
                verts_a = self._capture(setup_rig=False, setup_bone_orientation=False, case=case)
                verts_c = self._capture(setup_rig=True, setup_bone_orientation=True, case=case)

                max_d, mean_d, frame_results = self._compare_vertex_frames(verts_a, verts_c, "A vs C", case["frames"])

                self.assertLessEqual(
                    max_d, VERTEX_ANIM_THRESHOLD,
                    f"A vs C (Bake vs Rig+JO, {case['name']}): max vertex dist = {max_d:.4f}, "
                    f"mean = {mean_d:.4f} (threshold {VERTEX_ANIM_THRESHOLD}). "
                    f"Rig+JO mesh deformation should match Bake."
                )


if __name__ == "__main__":
    unittest.main()
