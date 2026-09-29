"""Real Maya curves, including edited TT nodes, are the only export authority."""

import math
from pathlib import Path

from maya import cmds

from mmd_tools.converters.vmd_scene_collector import VmdSceneCollector
from mmd_tools.converters.vmd_rotation_time_curve import apply_vmd_rotation_time_curve
from mmd_tools.converters.vmd_curve_fit import bezier_value, _slerp, _angle
from mmd_tools.io.vmd_exporter import VmdExporter
from mmd_tools.core.vmd_data import VmdData
from mmd_tools.adapters.native_vmd_batch_sampler import NativeVmdBatchSampler
from tests.common.maya_test_base import MayaTestBase


class TestVmdSceneCurveExport(MayaTestBase):
    def _evaluate(self, frames, time):
        left, right = frames[0], frames[-1]
        for a, b in zip(frames, frames[1:]):
            if a.frame_number <= time <= b.frame_number:
                left, right = a, b
                break
        x = (time-left.frame_number)/(right.frame_number-left.frame_number)
        controls = [tuple(right.interpolation[axis+4*i] for i in range(4)) for axis in range(4)]
        position = [left.position[j]+(right.position[j]-left.position[j])*bezier_value(controls[j], x) for j in range(3)]
        rotation = _slerp(left.rotation, right.rotation, bezier_value(controls[3], x))
        return position, rotation

    def test_saved_scene_without_source_exports_edited_tt_and_keys_at_all_fps(self):
        old_unit = cmds.currentUnit(query=True, time=True)
        try:
            for unit, fps in (("film", 24), ("ntsc", 30), ("ntscf", 60)):
                with self.subTest(unit=unit):
                    cmds.file(new=True, force=True)
                    cmds.currentUnit(time=unit)
                    joint = cmds.createNode("joint", name="curveExportBone")
                    cmds.addAttr(joint, longName="mmd_bone_name", dataType="string")
                    cmds.setAttr(joint+".mmd_bone_name", "bone", type="string")
                    for axis in "XYZ":
                        for t, value in ((0, 0), (fps, 45 if axis == "Y" else 0)):
                            cmds.setKeyframe(joint, attribute="rotate"+axis, time=t, value=value)
                    plugs = [joint+".rotate"+axis for axis in "XYZ"]
                    cmds.rotationInterpolation(*plugs, convert="quaternionSlerp")
                    frames = [{"frame_number":t, "interpolation":{"rotation":(.1, .7, .4, .95)}} for t in (0,30)]
                    record = apply_vmd_rotation_time_curve(frames, plugs, "bone", time_converter=lambda t:t*fps/30)
                    tt = cmds.ls(record["rotationTimeCurveUuid"])[0]
                    # Stale data in a legacy scene must not override these edits.
                    cmds.addAttr(tt, longName="mmdVmdRotationInterpolationJson", dataType="string")
                    cmds.setAttr(tt+".mmdVmdRotationInterpolationJson", "invalid stale source", type="string")
                    cmds.keyTangent(tt, edit=True, inTangentType="linear", outTangentType="linear")
                    cmds.setKeyframe(joint, attribute="rotateY", time=fps, value=60)
                    for t, value in ((0,0),(fps,2)):
                        cmds.setKeyframe(joint, attribute="translateX", time=t, value=value,
                                         inTangentType="linear", outTangentType="linear")
                    scene = str(Path(self.temp_dir)/"curves.ma")
                    cmds.file(rename=scene)
                    cmds.file(save=True, type="mayaAscii")
                    cmds.file(scene, open=True, force=True)
                    rows = VmdSceneCollector().collect_bone_frames([joint], bone_bind_poses={"bone":(0,0,0)})
                    self.assertTrue(rows)
                    native_rows = []
                    class Sink:
                        def begin_section(self, section):
                            pass

                        def write_frame(self, section, frame):
                            if section == "bones":
                                native_rows.append(frame)
                    VmdSceneCollector(bone_channel_sampler=NativeVmdBatchSampler(cmds)).collect_to_sink(
                        {"joints": [joint], "frame_range": (0, fps), "cameras": [], "lights": [], "export_strategy": "bake_timeline",
                         "bone_bind_poses": {"bone": (0,0,0)}}, Sink())
                    self.assertTrue(native_rows)
                    path = str(Path(self.temp_dir)/"curves.vmd")
                    VmdExporter().export_vmd_animation(path, {"bone_frames":native_rows})
                    vmd = VmdData().parse_file(path)
                    output = sorted(vmd.bone_frames, key=lambda f:f.frame_number)
                    self.assertEqual(output[-1].frame_number, 30)
                    for t in (0.25, 1.3, 4.75, 10.5, 17.2, 29.5):
                        p,q = self._evaluate(output,t)
                        maya_time=t*fps/30
                        angle=math.radians(cmds.getAttr(joint+".rotateY",time=maya_time))
                        expected=(0.,-math.sin(angle/2),0.,math.cos(angle/2))
                        self.assertLessEqual(abs(p[0]-cmds.getAttr(joint+".translateX",time=maya_time)), .001)
                        self.assertLessEqual(_angle(q,expected),math.radians(.1))
        finally:
            cmds.currentUnit(time=old_unit)

    def test_unrepresentable_edited_morph_fails_with_track_and_interval(self):
        old_unit = cmds.currentUnit(query=True, time=True)
        cmds.currentUnit(time="ntsc")
        try:
            mesh = cmds.polyCube()[0]
            target = cmds.duplicate(mesh)[0]
            blend = cmds.blendShape(target, mesh)[0]
            for t,value in ((0,0),(1,1)):
                cmds.setKeyframe(blend, attribute="weight[0]", time=t, value=value)
            cmds.keyTangent(blend+".weight[0]", edit=True, inTangentType="flat", outTangentType="flat")
            with self.assertRaisesRegex(ValueError,"frames 0..1"):
                VmdSceneCollector().collect_morph_frames([blend])
        finally:
            cmds.currentUnit(time=old_unit)

    def test_native_morph_sampling_resolves_roundtrip_time_roundoff(self):
        mesh = cmds.polyCube()[0]
        target = cmds.duplicate(mesh)[0]
        blend = cmds.blendShape(target, mesh)[0]
        for t, value in ((0, 0), (3, .2), (15, 1)):
            cmds.setKeyframe(blend, attribute="weight[0]", time=t, value=value,
                             inTangentType="linear", outTangentType="linear")
        rows = VmdSceneCollector().collect_morph_frames(
            [blend], time_converter=lambda t:t*.8, dense_sample=True,
            dense_frame_samples=list(range(16)), timeline_evaluation=True,
            morph_channel_sampler=NativeVmdBatchSampler(cmds))
        self.assertTrue(rows)
        self.assertEqual(rows[-1]["frame_number"], 12)
        self.assertAlmostEqual(rows[-1]["weight"], 1.)

    def test_ik_step_edits_export_and_fractional_transition_is_rejected(self):
        old_unit = cmds.currentUnit(query=True, time=True)
        cmds.currentUnit(time="ntsc")
        try:
            node = cmds.createNode("network", name="ikSceneAuthority")
            cmds.addAttr(node, longName="enabled", attributeType="bool", keyable=True)
            for t, value in ((0, 0), (2, 1)):
                cmds.setKeyframe(node, attribute="enabled", time=t, value=value,
                                 inTangentType="step", outTangentType="step")
            options = dict(start_frame=0, end_frame=2,
                           ik_routes_by_name={"legIK":(node,"enabled")})
            rows = VmdSceneCollector().collect_ik_show_hide_frames("model", **options)
            self.assertEqual([(r["frame_number"], r["ik_states"]) for r in rows],
                             [(0, [("legIK",False)]), (2, [("legIK",True)])])
            cmds.keyframe(node+".enabled", edit=True, time=(2,2), timeChange=1.5)
            with self.assertRaisesRegex(ValueError,"IK step.*legIK"):
                VmdSceneCollector().collect_ik_show_hide_frames("model", **options)
        finally:
            cmds.currentUnit(time=old_unit)
