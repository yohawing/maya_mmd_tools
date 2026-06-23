"""Optional real-asset Bake/Rig parity tests.

These tests are skipped by default because they depend on local, licensed MMD
assets. Set MAYA_MMD_TOOLS_REAL_PMX and MAYA_MMD_TOOLS_REAL_VMD to verify a
real PMX/VMD pair against the same mmd-anim oracle used by fixture tests.
"""

import os
import unittest

import maya.cmds as cmds

from tests.common.maya_test_base import MayaTestBase
from tests.integration.test_bake_rig_parity import (
    VERTEX_ANIM_THRESHOLD,
    WORLD_POS_THRESHOLD,
    WORLD_ROT_THRESHOLD_DEG,
    _capture_bone_world_transforms_by_index,
    _capture_runtime_oracle_world_transforms,
    _capture_vertex_positions,
    _euclidean,
    _find_mesh_transforms,
    _import_model_with_options,
    _quat_angle_deg,
)

DEFAULT_FRAMES = (0, 30, 60, 90)


def _real_asset_paths():
    pmx_path = os.environ.get("MAYA_MMD_TOOLS_REAL_PMX")
    vmd_path = os.environ.get("MAYA_MMD_TOOLS_REAL_VMD")
    if not pmx_path or not vmd_path:
        raise unittest.SkipTest(
            "Set MAYA_MMD_TOOLS_REAL_PMX and MAYA_MMD_TOOLS_REAL_VMD to run real-asset parity tests"
        )
    if not os.path.exists(pmx_path):
        raise unittest.SkipTest(f"Real PMX not found: {pmx_path}")
    if not os.path.exists(vmd_path):
        raise unittest.SkipTest(f"Real VMD not found: {vmd_path}")
    return pmx_path, vmd_path


def _real_asset_frames():
    value = os.environ.get("MAYA_MMD_TOOLS_REAL_FRAMES")
    if not value:
        return list(DEFAULT_FRAMES)
    frames = [int(part.strip()) for part in value.split(",") if part.strip()]
    if not frames:
        raise unittest.SkipTest("MAYA_MMD_TOOLS_REAL_FRAMES did not contain any frame numbers")
    return frames


class TestRealAssetBakeRigParity(MayaTestBase):
    """Optional gate for a real PMX/VMD pair."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.pmx_path, cls.vmd_path = _real_asset_paths()
        cls.frames = _real_asset_frames()

    def _assert_scene_matches_oracle(self, label):
        actual = _capture_bone_world_transforms_by_index(self.frames)
        oracle = _capture_runtime_oracle_world_transforms(self.pmx_path, self.vmd_path, self.frames)
        common_bones = set(actual) & set(oracle)
        self.assertGreater(len(common_bones), 0, "共通ボーンが見つからない")

        pos_outliers = []
        rot_outliers = []
        for bone_index in sorted(common_bones):
            joint = next(iter(actual[bone_index].values()))["joint"]
            for frame in self.frames:
                actual_frame = actual[bone_index].get(frame)
                oracle_frame = oracle[bone_index].get(frame)
                if not actual_frame or not oracle_frame:
                    continue
                dist = _euclidean(actual_frame["pos"], oracle_frame["pos"])
                if dist > WORLD_POS_THRESHOLD:
                    pos_outliers.append((joint, bone_index, frame, dist))
                angle = _quat_angle_deg(actual_frame["quat"], oracle_frame["quat"])
                if angle > WORLD_ROT_THRESHOLD_DEG:
                    rot_outliers.append((joint, bone_index, frame, angle))

        failures = []
        if pos_outliers:
            pos_outliers.sort(key=lambda item: -item[3])
            failures.append(f"{label} world position mismatch ({len(pos_outliers)} outliers)")
            failures.extend(
                f"  bone[{bone_index}] {joint} @ frame {frame}: {dist:.4f} units"
                for joint, bone_index, frame, dist in pos_outliers[:20]
            )
        if rot_outliers:
            rot_outliers.sort(key=lambda item: -item[3])
            failures.append(f"{label} world rotation mismatch ({len(rot_outliers)} outliers)")
            failures.extend(
                f"  bone[{bone_index}] {joint} @ frame {frame}: {angle:.2f} deg"
                for joint, bone_index, frame, angle in rot_outliers[:20]
            )
        if failures:
            self.fail("\n".join(failures))

    def _capture_vertices(self, setup_rig, setup_bone_orientation):
        root = _import_model_with_options(
            self.pmx_path,
            self.vmd_path,
            setup_rig=setup_rig,
            setup_bone_orientation=setup_bone_orientation,
        )
        meshes = _find_mesh_transforms(root)
        self.assertTrue(meshes, "メッシュが見つからない")
        result = _capture_vertex_positions(meshes, self.frames)
        cmds.file(new=True, force=True)
        return result

    def test_real_asset_bake_matches_mmd_anim_oracle(self):
        _import_model_with_options(
            self.pmx_path,
            self.vmd_path,
            setup_rig=False,
            setup_bone_orientation=False,
        )
        self._assert_scene_matches_oracle("RealAsset:Bake")

    def test_real_asset_rig_matches_mmd_anim_oracle(self):
        _import_model_with_options(
            self.pmx_path,
            self.vmd_path,
            setup_rig=True,
            setup_bone_orientation=True,
        )
        self._assert_scene_matches_oracle("RealAsset:Rig")

    def test_real_asset_bake_and_rig_mesh_vertices_match(self):
        bake_vertices = self._capture_vertices(setup_rig=False, setup_bone_orientation=False)
        rig_vertices = self._capture_vertices(setup_rig=True, setup_bone_orientation=True)

        outliers = []
        for frame in self.frames:
            bake_points = bake_vertices.get(frame, [])
            rig_points = rig_vertices.get(frame, [])
            self.assertEqual(len(bake_points), len(rig_points), f"frame {frame}: vertex count mismatch")
            for index, (bake_point, rig_point) in enumerate(zip(bake_points, rig_points)):
                dist = _euclidean(bake_point, rig_point)
                if dist > VERTEX_ANIM_THRESHOLD:
                    outliers.append((frame, index, dist))

        if outliers:
            outliers.sort(key=lambda item: -item[2])
            lines = [f"RealAsset Bake/Rig vertex mismatch ({len(outliers)} outliers)"]
            lines.extend(
                f"  vertex[{index}] @ frame {frame}: {dist:.4f} units"
                for frame, index, dist in outliers[:20]
            )
            self.fail("\n".join(lines))
