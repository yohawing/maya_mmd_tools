"""Standard animation layers must preserve solver-driven MMD joint poses."""

from pathlib import Path

from maya import cmds

from tests.common.maya_test_base import MayaTestBase

ROOT = Path(__file__).resolve().parents[2]
FRAMES = tuple(range(61))


def create_scene():
    from mmd_tools.io.mmd_importer import import_mmd_file
    from mmd_tools.services.settings_service import SettingsService

    cmds.file(new=True, force=True)
    options = SettingsService().build_pmx_import_options()
    options.update(import_physics=False, separate_meshes_by_material=False)
    root = import_mmd_file(str(ROOT / "tests/data/mmt_test_model.pmx"), options=options)
    import_motion(root)
    return root


def import_motion(root, clear_existing_motion=False):
    from mmd_tools.io.mmd_importer import import_mmd_file
    from mmd_tools.services.settings_service import SettingsService

    options = SettingsService().build_vmd_import_options(root)
    options["clear_existing_motion"] = clear_existing_motion
    result = import_mmd_file(str(ROOT / "tests/data/mmt_test_model_test_motion.vmd"), options=options)
    if not result:
        raise AssertionError("VMD import returned no model")


def matrices(root):
    return {joint: cmds.xform(joint, query=True, worldSpace=True, matrix=True)
            for joint in cmds.listRelatives(root, allDescendents=True, type="joint", fullPath=True) or []}


def matrix_delta(left, right):
    if set(left) != set(right):
        raise AssertionError("Joint identities changed")
    return max(abs(a-b) for joint in left for a, b in zip(left[joint], right[joint]))


def samples(root, frames=FRAMES):
    """Sample without adding timeline operations to the user's Undo history."""
    was_enabled = cmds.undoInfo(query=True, state=True)
    cmds.undoInfo(stateWithoutFlush=False)
    try:
        result = {}
        for frame in frames:
            cmds.currentTime(frame, edit=True)
            result[str(frame)] = matrices(root)
        return result
    finally:
        cmds.undoInfo(stateWithoutFlush=was_enabled)


def samples_delta(left, right):
    return max(matrix_delta(left[frame], right[frame]) for frame in left)


def add_joint_layer(root, override=False):
    """Use the standard Add Selected Objects operation at frame 30."""
    cmds.currentTime(30)
    cmds.undoInfo(openChunk=True, chunkName="Add MMD joint animation layer")
    try:
        layer = cmds.animLayer("JointLayer", override=override)
        cmds.select(cmds.listRelatives(root, allDescendents=True, type="joint", fullPath=True), replace=True)
        cmds.animLayer(layer, edit=True, addSelectedObjects=True)
        for other in cmds.ls(type="animLayer") or []:
            cmds.animLayer(other, edit=True, selected=other == layer, preferred=other == layer)
    finally:
        cmds.undoInfo(closeChunk=True)
    return layer


def ownership(root):
    """Compare canonical model joints, not collector aliases or blend nodes."""
    from mmd_tools.converters.vmd_ik_passthrough import collect_mmd_ik_passthrough_info
    from mmd_tools.converters.vmd_legacy_bone_routes import collect_ik_link_joints
    from mmd_tools.core.mmd_control_rig_analyzer import analyze_mmd_control_rig

    joints = cmds.listRelatives(root, allDescendents=True, type="joint", fullPath=True) or []
    passthrough = collect_mmd_ik_passthrough_info()
    links = collect_ik_link_joints()
    spec = analyze_mmd_control_rig(root)
    return {
        "passthrough": {joint: passthrough[joint] for joint in joints if joint in passthrough},
        "links": {joint: links[joint] for joint in joints if joint in links},
        "bindings": {bone.joint: {"kind": bone.input_kind, "plugs": list(bone.authored_plugs),
                                  "solvers": list(bone.ik_solvers), "blockers": list(bone.blockers)}
                     for bone in spec.bones},
    }


class TestIkAnimationLayers(MayaTestBase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.load_plugin(str(ROOT / "mmd_tools/plugin_main.py"))

    def test_additive_and_override_addition_preserve_creation_frame(self):
        for override in (False, True):
            with self.subTest(override=override):
                root = create_scene()
                cmds.currentTime(30)
                before = matrices(root)
                add_joint_layer(root, override=override)
                cmds.currentTime(30, edit=True, update=True)
                self.assertLess(matrix_delta(before, matrices(root)), 1e-5)

    def test_additive_mute_weight_and_undo_preserve_original_motion(self):
        root = create_scene()
        before = samples(root)
        layer = add_joint_layer(root)
        self.assertLess(samples_delta(before, samples(root)), 1e-5)
        cmds.undo()
        self.assertFalse(cmds.objExists(layer))
        self.assertLess(samples_delta(before, samples(root)), 1e-5)
        cmds.redo()
        self.assertLess(samples_delta(before, samples(root)), 1e-5)
        cmds.setAttr(layer + ".mute", True)
        self.assertLess(samples_delta(before, samples(root)), 1e-5)
        cmds.setAttr(layer + ".mute", False)
        cmds.setAttr(layer + ".weight", 0.0)
        self.assertLess(samples_delta(before, samples(root)), 1e-5)

    def test_native_collector_and_analyzer_ownership_survive_joint_layer(self):
        root = create_scene()
        before = ownership(root)
        self.assertGreater(len(before["passthrough"]), 0)
        self.assertEqual(set(before["passthrough"]), set(before["links"]))
        add_joint_layer(root)
        after = ownership(root)
        self.assertEqual(after["passthrough"], before["passthrough"])
        self.assertEqual(after["links"], before["links"])
        changes = {joint: {"before": binding, "after": after["bindings"].get(joint)}
                   for joint, binding in before["bindings"].items() if binding != after["bindings"].get(joint)}
        self.assertFalse(changes, changes)

    def test_vmd_replacement_preserves_manual_layer_and_original_motion(self):
        from tests.integration.test_pose_layers import select_layer, curve_snapshot

        for override in (False, True):
            with self.subTest(override=override):
                root = create_scene()
                original = samples(root)
                layer = add_joint_layer(root, override=override)
                joint = next(joint for joint in original["30"] if joint.endswith("|upper_body"))
                plug = joint + ".rotateZ"
                cmds.setKeyframe(plug, time=30, value=cmds.getAttr(plug) + 5.0, animLayer=layer)
                cmds.currentTime(30, edit=True, update=True)
                manual_curves = cmds.animLayer(layer, query=True, animCurves=True) or []
                snapshot = curve_snapshot()
                manual_keys = {curve: snapshot[curve] for curve in manual_curves}
                self.assertTrue(manual_keys)
                layered = samples(root)
                base = cmds.animLayer(query=True, root=True)
                layer_order = cmds.animLayer(base, query=True, children=True) or []
                cmds.setAttr(layer + ".mute", True)
                select_layer(cmds.animLayer(query=True, root=True))
                self.assertLess(samples_delta(original, samples(root)), 1e-5)
                cmds.currentTime(30)
                import_motion(root, clear_existing_motion=True)
                self.assertTrue(cmds.objExists(layer))
                snapshot = curve_snapshot()
                self.assertEqual({curve: snapshot.get(curve) for curve in manual_keys}, manual_keys)
                self.assertLess(samples_delta(original, samples(root)), 1e-5)
                cmds.setAttr(layer + ".mute", False)
                self.assertLess(samples_delta(layered, samples(root)), 1e-5)
                self.assertEqual(cmds.animLayer(base, query=True, children=True) or [], layer_order)

    def test_unknown_layer_writer_is_not_treated_as_native_ownership(self):
        from mmd_tools.core.native_layer_routes import NativeLayerRouteError, resolve_joint_input_source

        root = create_scene()
        add_joint_layer(root)
        joint = next(j for j in matrices(root) if j.endswith("|right_leg"))
        self.assertIsNotNone(resolve_joint_input_source(joint, "rotate", cmds_module=cmds))
        source = cmds.connectionInfo(joint + ".rotateX", sourceFromDestination=True)
        blend = source.split(".", 1)[0]
        self.assertTrue(cmds.nodeType(blend).startswith("animBlendNode"))
        writer = cmds.createNode("plusMinusAverage")
        cmds.connectAttr(writer + ".output1D", blend + ".inputBX", force=True)
        with self.assertRaises(NativeLayerRouteError):
            resolve_joint_input_source(joint, "rotate", cmds_module=cmds)
        from mmd_tools.core.mmd_control_rig_analyzer import analyze_mmd_control_rig

        binding = next(bone for bone in analyze_mmd_control_rig(root).bones if bone.joint == joint)
        self.assertEqual(binding.input_kind, "unsupported")
