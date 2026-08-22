"""Pure evidence helpers for the KIRIYA EyeCtrl Maya oracle."""

import unittest
from types import SimpleNamespace

from tests.viewport.e2e_mmd_control_rig_bone_morph import (
    DEPENDENCY_BAKE_REASON,
    _dependency_warning_evidence,
    _eye_motion_witness,
    _eye_pose_parity,
    _eye_ctrl_vmd_witness,
)


class EyeCtrlOracleEvidenceTests(unittest.TestCase):
    @staticmethod
    def _frame(frame, position=(0.0, 0.0, 0.0), rotation=(0.0, 0.0, 0.0, 1.0)):
        return SimpleNamespace(
            bone_name="両目",
            frame_number=frame,
            position=position,
            rotation=rotation,
        )

    def test_primary_name_track_witness_requires_motion_at_requested_frames(self):
        witness = _eye_ctrl_vmd_witness(
            SimpleNamespace(
                bone_frames=[
                    self._frame(0),
                    self._frame(5, rotation=(0.0, 0.1, 0.0, 0.99)),
                    self._frame(10, rotation=(0.0, -0.2, 0.0, 0.98)),
                    SimpleNamespace(
                        bone_name="EyeCtrl",
                        frame_number=10,
                        position=(100.0, 100.0, 100.0),
                        rotation=(0.0, 0.0, 0.0, 1.0),
                    ),
                ]
            ),
            (0, 5, 10),
        )

        self.assertTrue(witness["pass"])
        self.assertEqual(witness["primaryName"], "両目")
        self.assertEqual(witness["englishName"], "EyeCtrl")
        self.assertEqual(witness["boneIndex"], 14)
        self.assertEqual(sorted(witness["frames"]), ["0", "10", "5"])
        self.assertGreater(witness["motionDelta"], 1.0e-5)

    def test_dependency_warning_evidence_keeps_path_reason_frame_and_key_count(self):
        issue = SimpleNamespace(
            path="scene.control_rig.direct_vmd_export.両目.dependency_bake",
            severity="warning",
            reason=DEPENDENCY_BAKE_REASON,
            details={
                "route": "dependency_bake",
                "bone": "両目",
                "frame_range": [0, 10],
                "generated_key_count": 11,
            },
        )

        self.assertEqual(
            _dependency_warning_evidence([issue]),
            [
                {
                    "path": "scene.control_rig.direct_vmd_export.両目.dependency_bake",
                    "severity": "warning",
                    "reason": DEPENDENCY_BAKE_REASON,
                    "frameRange": [0, 10],
                    "generatedKeyCount": 11,
                    "details": {
                        "route": "dependency_bake",
                        "bone": "両目",
                        "frame_range": [0, 10],
                        "generated_key_count": 11,
                    },
                }
            ],
        )

    def test_dependency_warning_evidence_rejects_other_bone(self):
        issue = SimpleNamespace(
            path="scene.control_rig.direct_vmd_export.左目.dependency_bake",
            severity="warning",
            reason="wrong bone",
            details={
                "route": "dependency_bake",
                "bone": "左目",
                "frame_range": [0, 10],
                "generated_key_count": 11,
            },
        )
        with self.assertRaises(RuntimeError):
            _dependency_warning_evidence([issue])

    def test_dependency_warning_evidence_rejects_wrong_reason_only(self):
        issue = SimpleNamespace(
            path="scene.control_rig.direct_vmd_export.両目.dependency_bake",
            severity="warning",
            reason="Reason: This bone has no dedicated Control Rig mapping, but the reason is wrong.",
            details={
                "route": "dependency_bake",
                "bone": "両目",
                "frame_range": [0, 10],
                "generated_key_count": 11,
            },
        )
        with self.assertRaises(RuntimeError):
            _dependency_warning_evidence([issue])

    def test_dependency_warning_evidence_rejects_invalid_structured_facts(self):
        issue = SimpleNamespace(
            path="scene.control_rig.direct_vmd_export.両目.dependency_bake",
            severity="error",
            reason="wrong severity",
            details={
                "route": "dependency_bake",
                "bone": "両目",
                "frame_range": [0, 9],
                "generated_key_count": 10,
            },
        )
        with self.assertRaises(RuntimeError):
            _dependency_warning_evidence([issue])

    def test_fresh_eye_pose_parity_reports_world_and_skin_maxima(self):
        identity = [1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0]
        pose = {
            "world": {
                "0": {"EyeCtrl": identity, "leftEye": identity, "rightEye": identity}
            },
            "skin": {
                "0": {
                    "leftEye": [{"logicalIndex": 8, "matrix": identity}],
                    "rightEye": [{"logicalIndex": 9, "matrix": identity}],
                }
            },
        }
        parity = _eye_pose_parity(pose, pose, (0,))

        self.assertTrue(parity["pass"])
        self.assertEqual(parity["maxWorldMatrixError"], 0.0)
        self.assertEqual(parity["maxSkinMatrixError"], 0.0)

        changed = {**pose, "world": {"0": {**pose["world"]["0"], "EyeCtrl": identity[:-1] + [1.01]}}}
        self.assertFalse(_eye_pose_parity(pose, changed, (0,))["pass"])

    def test_motion_witness_requires_margin_over_parity_tolerance_for_every_observable(self):
        strong_world = {"leftEye": 0.051, "rightEye": 0.051}
        strong_skin = {"leftEye": 0.051, "rightEye": 0.051}
        self.assertTrue(_eye_motion_witness(0.051, strong_world, strong_skin))
        self.assertFalse(_eye_motion_witness(0.05, strong_world, strong_skin))
        self.assertFalse(
            _eye_motion_witness(0.051, {**strong_world, "rightEye": 0.05}, strong_skin)
        )
        self.assertFalse(
            _eye_motion_witness(0.051, strong_world, {**strong_skin, "leftEye": 0.05})
        )


if __name__ == "__main__":
    unittest.main()
