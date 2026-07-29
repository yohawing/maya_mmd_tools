"""Tests for Experimental sparse VMD rotation time-curve authoring."""

from __future__ import annotations

import math
from unittest.mock import patch

import maya.cmds as cmds

from mmd_tools.converters.vmd_scene_collector import VmdSceneCollector
from mmd_tools.converters.vmd_registered_sparse import RegisteredSparseBoneFrame
from mmd_tools.converters.vmd_rotation_time_curve import (
    apply_vmd_rotation_time_curve,
    capture_vmd_rotation_time_curve_snapshot,
    commit_vmd_rotation_time_curve_disable,
    delete_vmd_rotation_time_curves_for_controls,
    detach_and_delete_vmd_rotation_time_curve,
    record_vmd_rotation_time_curve_metadata,
    restore_vmd_rotation_time_curve_snapshot,
    rotation_time_curve_interpolation_by_bone,
    stage_vmd_rotation_time_curve_disable,
)
from mmd_tools.core import mmd_control_rig_motion
from tests.common.maya_test_base import MayaTestBase


def _interpolation_bytes(rotation=(13, 102, 38, 127)) -> bytes:
    values = [20] * 64
    for index, value in zip((3, 7, 11, 15), rotation):
        values[index] = value
    return bytes(values)


class TestVmdRotationTimeCurve(MayaTestBase):
    def _quaternion_control(self):
        control = cmds.createNode("transform", name="vmd_rotation_time_control")
        values = {"X": (179.0, -179.0), "Y": (20.0, -15.0), "Z": (-25.0, 30.0)}
        for axis in "XYZ":
            cmds.setKeyframe(
                control, attribute=f"rotate{axis}", time=0, value=values[axis][0]
            )
            cmds.setKeyframe(
                control, attribute=f"rotate{axis}", time=30, value=values[axis][1]
            )
        plugs = [f"{control}.rotate{axis}" for axis in "XYZ"]
        cmds.rotationInterpolation(*plugs, convert="quaternionSlerp")
        return control, plugs

    def test_sparse_time_curve_drives_quaternion_siblings(self):
        control, plugs = self._quaternion_control()
        frames = [
            {"frame_number": 0, "interpolation": _interpolation_bytes()},
            {"frame_number": 30, "interpolation": _interpolation_bytes()},
        ]
        record = apply_vmd_rotation_time_curve(frames, plugs, "下半身")
        time_curve = (cmds.ls(record["rotationTimeCurveUuid"], long=True) or [None])[0]

        self.assertEqual(cmds.nodeType(time_curve), "animCurveTT")
        self.assertEqual(cmds.keyframe(time_curve, query=True, timeChange=True), [0.0, 30.0])
        self.assertEqual(record["keyCount"], 2)
        interpolation = rotation_time_curve_interpolation_by_bone(
            {
                "rotationInterpolationMode": "vmd_time_curve_experimental",
                "rotationTimeCurves": [record],
            }
        )
        self.assertEqual(interpolation["下半身"][30], _interpolation_bytes())
        for plug in plugs:
            curve = cmds.listConnections(plug, source=True, destination=False)[0]
            self.assertEqual(
                cmds.listConnections(
                    f"{curve}.input", source=True, destination=False, plugs=True
                ),
                [f"{time_curve}.output"],
            )
            self.assertEqual(cmds.rotationInterpolation(curve, query=True), "quaternionSlerp")

        cmds.currentTime(3, edit=True)
        x1, y1, x2, y2 = (value / 127.0 for value in (13, 102, 38, 127))
        low, high = 0.0, 1.0
        for _ in range(50):
            u = (low + high) * 0.5
            inv = 1.0 - u
            x = (3 * inv * inv * u * x1) + (3 * inv * u * u * x2) + (u**3)
            if x < 0.1:
                low = u
            else:
                high = u
        u = (low + high) * 0.5
        inv = 1.0 - u
        expected = 30.0 * (
            (3 * inv * inv * u * y1) + (3 * inv * u * u * y2) + (u**3)
        )
        self.assertTrue(
            math.isclose(cmds.getAttr(f"{time_curve}.output"), expected, abs_tol=1.0e-5)
        )
        cmds.delete(control, time_curve)

    def test_registered_semantic_frames_keep_raw_export_interpolation(self):
        control, plugs = self._quaternion_control()
        raw = _interpolation_bytes((10, 80, 50, 120))
        semantic = {
            "translate_x": (0.0, 0.0, 1.0, 1.0),
            "translate_y": (0.0, 0.0, 1.0, 1.0),
            "translate_z": (0.0, 0.0, 1.0, 1.0),
            "rotation": tuple(value / 127.0 for value in (10, 80, 50, 120)),
        }
        frames = [
            RegisteredSparseBoneFrame(
                "下半身", 1, frame, (0.0, 0.0, 0.0), (0.0, 0.0, 0.0, 1.0),
                semantic, raw,
            )
            for frame in (0, 30)
        ]

        record = apply_vmd_rotation_time_curve(frames, plugs, "下半身")
        interpolation = rotation_time_curve_interpolation_by_bone(
            {
                "rotationInterpolationMode": "vmd_time_curve_experimental",
                "rotationTimeCurves": [record],
            }
        )

        self.assertEqual(interpolation["下半身"][30], raw)
        time_curve = cmds.ls(record["rotationTimeCurveUuid"], long=True)[0]
        cmds.delete(control, time_curve)

    def test_time_curve_uses_maya_times_but_exports_vmd_frames(self):
        control, plugs = self._quaternion_control()
        frames = [
            {"frame_number": 0, "interpolation": _interpolation_bytes()},
            {"frame_number": 30, "interpolation": _interpolation_bytes()},
        ]
        record = apply_vmd_rotation_time_curve(
            frames,
            plugs,
            "下半身",
            time_converter=lambda frame: frame * 2.0,
        )
        time_curve = cmds.ls(record["rotationTimeCurveUuid"], long=True)[0]

        self.assertEqual(
            cmds.keyframe(time_curve, query=True, timeChange=True),
            [0.0, 60.0],
        )
        interpolation = rotation_time_curve_interpolation_by_bone(
            {
                "rotationInterpolationMode": "vmd_time_curve_experimental",
                "rotationTimeCurves": [record],
            }
        )
        self.assertEqual(interpolation["下半身"][30], _interpolation_bytes())
        cmds.delete(control, time_curve)

    def test_new_time_curve_is_deleted_when_authoring_fails(self):
        control, plugs = self._quaternion_control()
        frames = [
            {"frame_number": 0, "interpolation": _interpolation_bytes()},
            {"frame_number": 30, "interpolation": _interpolation_bytes()},
        ]
        before = set(cmds.ls(type="animCurveTT") or [])

        with patch(
            "mmd_tools.converters.vmd_rotation_time_curve._set_marker",
            side_effect=RuntimeError("injected marker failure"),
        ):
            with self.assertRaisesRegex(RuntimeError, "injected marker failure"):
                apply_vmd_rotation_time_curve(frames, plugs, "下半身")

        self.assertEqual(set(cmds.ls(type="animCurveTT") or []), before)
        cmds.delete(control)

    def test_snapshot_restores_existing_curve_and_deletes_failed_new_curve(self):
        control, plugs = self._quaternion_control()
        frames = [
            {"frame_number": 0, "interpolation": _interpolation_bytes()},
            {"frame_number": 30, "interpolation": _interpolation_bytes()},
        ]
        record = apply_vmd_rotation_time_curve(frames, plugs, "下半身")
        snapshot = capture_vmd_rotation_time_curve_snapshot(
            {"rotationTimeCurves": [record]}
        )
        time_curve = cmds.ls(record["rotationTimeCurveUuid"], long=True)[0]
        original_destinations = sorted(
            cmds.listConnections(
                f"{time_curve}.output",
                source=False,
                destination=True,
                plugs=True,
            )
            or []
        )
        cmds.setKeyframe(time_curve, time=15, value=2.0)
        stage_vmd_rotation_time_curve_disable(snapshot)
        restore_vmd_rotation_time_curve_snapshot(snapshot)
        self.assertEqual(cmds.keyframe(time_curve, query=True, timeChange=True), [0.0, 30.0])
        self.assertEqual(
            sorted(
                cmds.listConnections(
                    f"{time_curve}.output",
                    source=False,
                    destination=True,
                    plugs=True,
                )
                or []
            ),
            original_destinations,
        )

        second, second_plugs = self._quaternion_control()
        failed = apply_vmd_rotation_time_curve(frames, second_plugs, "上半身")
        failed_curve = cmds.ls(failed["rotationTimeCurveUuid"], long=True)[0]
        other, other_plugs = self._quaternion_control()
        unrelated = apply_vmd_rotation_time_curve(frames, other_plugs, "頭")
        unrelated_curve = cmds.ls(unrelated["rotationTimeCurveUuid"], long=True)[0]
        restore_vmd_rotation_time_curve_snapshot(snapshot, [failed])
        self.assertFalse(cmds.objExists(failed_curve))
        self.assertTrue(cmds.objExists(unrelated_curve))
        cmds.delete(control, second, other, time_curve, unrelated_curve)

    def test_metadata_records_uuid_owned_curves(self):
        existing = {
            "boneName": "上半身",
            "controlUuid": "existing-control-uuid",
            "rotationTimeCurveUuid": "existing-curve-uuid",
        }
        metadata = {
            "state": "EDIT",
            "owner": "CONTROL_OWNED",
            "rotationTimeCurves": [existing],
        }
        records = [
            {
                "boneName": "下半身",
                "controlUuid": "control-uuid",
                "rotationTimeCurveUuid": "curve-uuid",
            }
        ]
        with patch(
            "mmd_tools.core.mmd_control_rig_builder.read_mmd_control_rig_metadata",
            return_value=metadata,
        ), patch(
            "mmd_tools.core.mmd_control_rig_builder._write_metadata"
        ) as write_metadata:
            record_vmd_rotation_time_curve_metadata("|model", records)

        written = write_metadata.call_args.args[2]
        self.assertEqual(written["rotationInterpolationMode"], "vmd_time_curve_experimental")
        self.assertEqual(
            written["rotationTimeCurves"],
            [records[0], existing],
        )

        with patch(
            "mmd_tools.core.mmd_control_rig_builder.read_mmd_control_rig_metadata",
            return_value=metadata,
        ), patch(
            "mmd_tools.core.mmd_control_rig_builder._write_metadata"
        ) as replace_metadata:
            record_vmd_rotation_time_curve_metadata(
                "|model", records, replace_existing=True
            )

        self.assertEqual(
            replace_metadata.call_args.args[2]["rotationTimeCurves"], records
        )

    def test_export_rejects_rewired_owned_sibling(self):
        control, plugs = self._quaternion_control()
        frames = [
            {"frame_number": 0, "interpolation": _interpolation_bytes()},
            {"frame_number": 30, "interpolation": _interpolation_bytes()},
        ]
        record = apply_vmd_rotation_time_curve(frames, plugs, "下半身")
        time_curve = cmds.ls(record["rotationTimeCurveUuid"], long=True)[0]
        curve = cmds.ls(record["rotationCurveUuids"][0], long=True)[0]
        cmds.disconnectAttr(f"{time_curve}.output", f"{curve}.input")
        cmds.connectAttr("time1.outTime", f"{curve}.input")

        with self.assertRaisesRegex(RuntimeError, "not driven"):
            rotation_time_curve_interpolation_by_bone(
                {
                    "rotationInterpolationMode": "vmd_time_curve_experimental",
                    "rotationTimeCurves": [record],
                }
            )
        cmds.delete(control, time_curve)

    def test_sparse_bake_preserves_time_warped_pose(self):
        control, plugs = self._quaternion_control()
        joint = cmds.createNode("transform", name="vmd_rotation_time_joint")
        frames = [
            {"frame_number": 0, "interpolation": _interpolation_bytes()},
            {"frame_number": 30, "interpolation": _interpolation_bytes()},
        ]
        apply_vmd_rotation_time_curve(frames, plugs, "下半身")
        sources = {}
        rows = []
        for axis, plug in zip("XYZ", plugs):
            control_source = cmds.listConnections(
                plug, source=True, destination=False, plugs=True
            )[0]
            target = f"{joint}.rotate{axis}"
            mmd_curve = cmds.createNode("animCurveTA")
            cmds.setKeyframe(mmd_curve, time=0, value=0.0)
            cmds.setKeyframe(mmd_curve, time=30, value=0.0)
            cmds.connectAttr(plug, target)
            sources[plug] = control_source
            rows.append(
                {
                    "control": plug,
                    "target": target,
                    "source": f"{mmd_curve}.output",
                    "controlSource": control_source,
                    "routeClass": mmd_control_rig_motion.ROUTE_SAME_BASIS,
                }
            )
        cmds.currentTime(3, edit=True)
        before = cmds.xform(joint, query=True, worldSpace=True, matrix=True)
        mmd_control_rig_motion._commit_control_rotation_group(cmds, rows, sources)
        after = cmds.xform(joint, query=True, worldSpace=True, matrix=True)
        self.assertLess(max(abs(a - b) for a, b in zip(before, after)), 1.0e-7)
        cmds.delete(control, joint)

    def test_sparse_bake_supports_partial_existing_destination_curves(self):
        control, plugs = self._quaternion_control()
        joint = cmds.createNode("transform", name="vmd_rotation_partial_joint")
        frames = [
            {"frame_number": 0, "interpolation": _interpolation_bytes()},
            {"frame_number": 30, "interpolation": _interpolation_bytes()},
        ]
        record = apply_vmd_rotation_time_curve(frames, plugs, "下半身")
        time_curve = cmds.ls(record["rotationTimeCurveUuid"], long=True)[0]
        sources = {}
        rows = []
        for index, (axis, plug) in enumerate(zip("XYZ", plugs)):
            control_source = cmds.listConnections(
                plug, source=True, destination=False, plugs=True
            )[0]
            target = f"{joint}.rotate{axis}"
            mmd_source = None
            if index < 2:
                mmd_curve = cmds.createNode("animCurveTA")
                cmds.setKeyframe(mmd_curve, time=0, value=0.0)
                cmds.setKeyframe(mmd_curve, time=30, value=0.0)
                mmd_source = f"{mmd_curve}.output"
            cmds.connectAttr(plug, target)
            sources[plug] = control_source
            rows.append(
                {
                    "control": plug,
                    "target": target,
                    "source": mmd_source,
                    "controlSource": control_source,
                    "routeClass": mmd_control_rig_motion.ROUTE_SAME_BASIS,
                }
            )

        mmd_control_rig_motion._commit_control_rotation_group(cmds, rows, sources)

        for axis in "XYZ":
            curve = cmds.listConnections(
                f"{joint}.rotate{axis}", source=True, destination=False
            )[0]
            self.assertEqual(
                cmds.listConnections(
                    f"{curve}.input", source=True, destination=False, plugs=True
                ),
                [f"{time_curve}.output"],
            )
        cmds.delete(control, joint, time_curve)

    def test_collector_restores_original_interpolation_bytes(self):
        joint = cmds.joint(name="vmd_rotation_time_export_joint")
        cmds.addAttr(joint, longName="mmd_bone_name", dataType="string")
        cmds.setAttr(f"{joint}.mmd_bone_name", "下半身", type="string")
        for axis in "XYZ":
            cmds.setKeyframe(joint, attribute=f"rotate{axis}", time=0, value=0.0)
            cmds.setKeyframe(joint, attribute=f"rotate{axis}", time=30, value=10.0)
        frames = VmdSceneCollector().collect_bone_frames(
            [joint],
            rotation_interpolation={"下半身": {30: _interpolation_bytes()}},
            time_converter=lambda value: value,
        )
        arriving = next(frame for frame in frames if frame["frame_number"] == 30)
        self.assertEqual(arriving["interpolation"], _interpolation_bytes())
        cmds.delete(joint)

    def test_removal_restores_default_time_input(self):
        control, plugs = self._quaternion_control()
        frames = [
            {"frame_number": 0, "interpolation": _interpolation_bytes()},
            {"frame_number": 30, "interpolation": _interpolation_bytes()},
        ]
        record = apply_vmd_rotation_time_curve(frames, plugs, "下半身")
        time_curve = cmds.ls(record["rotationTimeCurveUuid"], long=True)[0]
        driven_curves = [
            cmds.listConnections(plug, source=True, destination=False)[0]
            for plug in plugs
        ]

        detach_and_delete_vmd_rotation_time_curve(cmds, time_curve)

        self.assertFalse(cmds.objExists(time_curve))
        for curve in driven_curves:
            self.assertEqual(
                cmds.listConnections(
                    f"{curve}.input", source=True, destination=False, plugs=True
                ),
                ["time1.outTime"],
            )
        cmds.delete(control)

    def test_root_scoped_removal_deletes_only_matching_time_curve(self):
        control, plugs = self._quaternion_control()
        other, other_plugs = self._quaternion_control()
        frames = [
            {"frame_number": 0, "interpolation": _interpolation_bytes()},
            {"frame_number": 30, "interpolation": _interpolation_bytes()},
        ]
        record = apply_vmd_rotation_time_curve(frames, plugs, "下半身")
        other_record = apply_vmd_rotation_time_curve(frames, other_plugs, "上半身")
        time_curve = cmds.ls(record["rotationTimeCurveUuid"], long=True)[0]
        other_curve = cmds.ls(other_record["rotationTimeCurveUuid"], long=True)[0]

        deleted = delete_vmd_rotation_time_curves_for_controls([control])

        self.assertEqual(deleted, [time_curve.lstrip("|")])
        self.assertFalse(cmds.objExists(time_curve))
        self.assertTrue(cmds.objExists(other_curve))
        for plug in plugs:
            curve = cmds.listConnections(plug, source=True, destination=False)[0]
            self.assertEqual(
                cmds.listConnections(
                    f"{curve}.input", source=True, destination=False, plugs=True
                ),
                ["time1.outTime"],
            )
        cmds.delete(control, other, other_curve)

    def test_normal_mode_reimport_stages_and_commits_time_curve_removal(self):
        control, plugs = self._quaternion_control()
        frames = [
            {"frame_number": 0, "interpolation": _interpolation_bytes()},
            {"frame_number": 30, "interpolation": _interpolation_bytes()},
        ]
        record = apply_vmd_rotation_time_curve(frames, plugs, "下半身")
        snapshot = capture_vmd_rotation_time_curve_snapshot(
            {"rotationTimeCurves": [record]}
        )
        time_curve = cmds.ls(record["rotationTimeCurveUuid"], long=True)[0]

        staged = stage_vmd_rotation_time_curve_disable(snapshot)

        self.assertEqual(staged, [time_curve])
        for plug in plugs:
            curve = cmds.listConnections(plug, source=True, destination=False)[0]
            self.assertEqual(
                cmds.listConnections(
                    f"{curve}.input", source=True, destination=False, plugs=True
                ),
                ["time1.outTime"],
            )
        metadata = {
            "state": "EDIT",
            "rotationInterpolationMode": "vmd_time_curve_experimental",
            "rotationTimeCurves": [record],
        }
        with patch(
            "mmd_tools.core.mmd_control_rig_builder.read_mmd_control_rig_metadata",
            return_value=metadata,
        ), patch(
            "mmd_tools.core.mmd_control_rig_builder._write_metadata"
        ) as write_metadata:
            commit_vmd_rotation_time_curve_disable("|model", staged)

        written = write_metadata.call_args.args[2]
        self.assertNotIn("rotationInterpolationMode", written)
        self.assertNotIn("rotationTimeCurves", written)
        self.assertFalse(cmds.objExists(time_curve))
        cmds.delete(control)

    def test_curve_input_snapshot_restores_failed_rewire(self):
        control, plugs = self._quaternion_control()
        curve = cmds.listConnections(plugs[0], source=True, destination=False)[0]
        cmds.connectAttr("time1.outTime", f"{curve}.input", force=True)
        states = mmd_control_rig_motion._capture_curve_input_states(
            cmds, [f"{curve}.output"]
        )
        alternate_time = cmds.createNode("animCurveTT")
        cmds.connectAttr(f"{alternate_time}.output", f"{curve}.input", force=True)

        mmd_control_rig_motion._restore_curve_input_states(cmds, states)

        self.assertEqual(
            cmds.listConnections(
                f"{curve}.input", source=True, destination=False, plugs=True
            ),
            ["time1.outTime"],
        )
        cmds.delete(control, alternate_time)
