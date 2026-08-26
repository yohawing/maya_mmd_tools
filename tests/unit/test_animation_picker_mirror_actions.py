"""Focused tests for UUID-scoped Mirror Pose and Mirror Select helpers."""

from __future__ import annotations

import unittest

from mmd_tools.ui.mirror_actions import (
    MirrorActionError,
    MirrorEntry,
    MirrorMapping,
    MirrorPoseTransaction,
    build_mirror_pairs,
    mirrored_transform_values,
)
class _FakeCmds:
    def __init__(self):
        self.uuid = "model-uuid"
        self.paths = {
            "|model": "|model",
            "|model|L_arm": "|model|L_arm",
            "|model|R_arm": "|model|R_arm",
        }
        self.values = {}
        self.incoming = {}
        self.locks = {}
        self.keyframes = []
        self.keyframe_edits = []
        self.curve_keys = {}
        self.set_attr_calls = []
        self.time = 12.0
        for node, offset in (("|model|L_arm", 1.0), ("|model|R_arm", 10.0)):
            for channel, value in zip(
                ("translateX", "translateY", "translateZ", "rotateX", "rotateY", "rotateZ"),
                (offset, offset + 1.0, offset + 2.0, offset + 3.0, offset + 4.0, offset + 5.0),
            ):
                self.values[f"{node}.{channel}"] = value
                self.incoming[f"{node}.{channel}"] = []
                self.locks[f"{node}.{channel}"] = False
        self.fail_plug = None
        self.undo_values = None
        self.undo_keyframes = None
        self.undo_curve_keys = None

    def ls(self, node=None, long=False, uuid=False, **_kwargs):
        if uuid:
            return [self.uuid] if node == "|model" else [f"uuid:{node}"] if node in self.paths else []
        if node is None:
            return []
        nodes = node if isinstance(node, (list, tuple)) else [node]
        if long:
            return [self.paths[value] for value in nodes if value in self.paths]
        return list(nodes)

    def getAttr(self, plug, lock=False):
        return self.locks[plug] if lock else self.values[plug]

    def setAttr(self, plug, value=None, lock=None, **_kwargs):
        if lock is not None:
            self.locks[plug] = bool(lock)
            return
        if plug == self.fail_plug:
            self.fail_plug = None
            raise RuntimeError("setAttr failure")
        self.set_attr_calls.append(plug)
        self.values[plug] = value

    def listConnections(self, plug, source=False, destination=False, plugs=False):
        if source and not destination and plugs:
            return list(self.incoming.get(plug, ()))
        return []

    @staticmethod
    def nodeType(node):
        return "animCurveTA" if node.startswith("animCurve") else "transform"

    def currentTime(self, query=False):
        return self.time if query else None

    def setKeyframe(self, node, time, value=None, attribute=None):
        plug = f"{node}.{attribute}" if attribute else node
        if value is None:
            value = self.values[plug]
        self.keyframes.append((plug, tuple(time), float(value)))
        curve = plug
        if plug in self.incoming and self.incoming[plug]:
            curve = self.incoming[plug][0].rsplit(".", 1)[0]
            self.values[plug] = float(value)
        self.curve_keys.setdefault(curve, {})[float(time[0])] = float(value)
        for plug, incoming in self.incoming.items():
            if incoming == [f"{curve}.output"]:
                self.values[plug] = float(value)

    def keyframe(
        self,
        node,
        edit=False,
        time=None,
        valueChange=None,
        absolute=False,
    ):
        if not edit or not absolute:
            raise AssertionError("fake only supports absolute key edits")
        self.keyframe_edits.append((node, tuple(time), float(valueChange)))
        self.curve_keys.setdefault(node, {})[float(time[0])] = float(valueChange)
        for plug, incoming in self.incoming.items():
            if incoming == [f"{node}.output"]:
                self.values[plug] = float(valueChange)

    def undo(self):
        if self.undo_values is not None:
            self.values = dict(self.undo_values)
        if self.undo_keyframes is not None:
            self.keyframes = list(self.undo_keyframes)
        if self.undo_curve_keys is not None:
            self.curve_keys = {
                curve: dict(keys) for curve, keys in self.undo_curve_keys.items()
            }


class _FakeAdapter:
    def __init__(self, cmds):
        self._cmds = cmds
        self.undo_chunks = []

    def undo_info(self, **kwargs):
        if kwargs.get("openChunk"):
            self.undo_chunks.append(kwargs.get("chunkName"))


class TestMirrorPairing(unittest.TestCase):
    def test_japanese_english_and_namespace_names_pair_by_metadata(self):
        entries = [
            MirrorEntry("left-uuid", "|charA|左腕", "|charA|左腕", ("左腕",)),
            MirrorEntry("right-uuid", "|charA|右腕", "|charA|右腕", ("右腕",)),
            MirrorEntry("left-finger", "|charA|hand_L", "|charA|hand_L", ("Hand_L",)),
            MirrorEntry("right-finger", "|charA|hand_R", "|charA|hand_R", ("Hand_R",)),
        ]
        pairs = build_mirror_pairs(entries)
        self.assertEqual(pairs["left-uuid"].identity, "right-uuid")
        self.assertEqual(pairs["left-finger"].identity, "right-finger")
        self.assertEqual(build_mirror_pairs(entries)["left-uuid"].node, "|charA|右腕")

    def test_center_unpaired_and_ambiguous_selection_fail_closed(self):
        entries = [
            MirrorEntry("center", "|char|センター", "|char|センター", ("センター",)),
            MirrorEntry("left", "|char|左腕", "|char|左腕", ("左腕",)),
        ]
        self.assertEqual(build_mirror_pairs(entries), {})
        ambiguous = entries + [
            MirrorEntry("right-a", "|char|右腕A", "|char|右腕A", ("右腕",)),
            MirrorEntry("right-b", "|char|右腕B", "|char|右腕B", ("右腕",)),
        ]
        self.assertEqual(build_mirror_pairs(ambiguous), {})

    def test_non_identity_control_bases_mirror_in_bone_space(self):
        quarter_turn_z = (0.0, 0.0, 2**-0.5, 2**-0.5)
        translation, rotation = mirrored_transform_values(
            (1.0, 2.0, 3.0),
            (30.0, 0.0, 0.0),
            source_basis=quarter_turn_z,
            target_basis=quarter_turn_z,
        )

        self.assertEqual(translation, (-1.0, 2.0, 3.0))
        self.assertAlmostEqual(rotation[0], -30.0, places=6)
        self.assertAlmostEqual(rotation[1], 0.0, places=6)
        self.assertAlmostEqual(rotation[2], 0.0, places=6)

class TestMirrorPoseTransaction(unittest.TestCase):
    def _make(self):
        cmds = _FakeCmds()
        adapter = _FakeAdapter(cmds)
        left = MirrorEntry("left", "|model|L_arm", "|model|L_arm", ("左腕",))
        right = MirrorEntry("right", "|model|R_arm", "|model|R_arm", ("右腕",))
        mapping = MirrorMapping(left, right)
        return cmds, adapter, mapping

    def _transaction(self, adapter, mapping):
        return MirrorPoseTransaction(
            adapter,
            model_root="|model",
            model_uuid="model-uuid",
            mappings=[mapping],
        )

    def test_mirror_sign_contract_preserves_source_and_connections(self):
        cmds, adapter, mapping = self._make()
        source_plug = "|model|L_arm.rotateY"
        cmds.incoming[source_plug] = ["animCurve.output"]
        before_source = dict(cmds.values)

        self.assertEqual(self._transaction(adapter, mapping).apply(), 1)
        self.assertEqual(cmds.values["|model|R_arm.translateX"], -1.0)
        self.assertEqual(cmds.values["|model|R_arm.translateY"], 2.0)
        self.assertEqual(cmds.values["|model|R_arm.rotateX"], 4.0)
        self.assertEqual(cmds.values["|model|R_arm.rotateY"], -5.0)
        self.assertEqual(cmds.values["|model|R_arm.rotateZ"], -6.0)
        self.assertEqual(cmds.values[source_plug], before_source[source_plug])
        self.assertEqual(cmds.incoming[source_plug], ["animCurve.output"])

    def test_child_first_mappings_write_parent_target_first(self):
        cmds, adapter, _mapping = self._make()
        nodes = (
            "|model|L_parent",
            "|model|L_parent|L_child",
            "|model|R_parent",
            "|model|R_parent|R_child",
        )
        for index, node in enumerate(nodes):
            cmds.paths[node] = node
            for channel, value in zip(
                ("translateX", "translateY", "translateZ", "rotateX", "rotateY", "rotateZ"),
                (index + 1.0,) * 6,
            ):
                plug = f"{node}.{channel}"
                cmds.values[plug] = value
                cmds.incoming[plug] = []
                cmds.locks[plug] = False
        parent_mapping = MirrorMapping(
            MirrorEntry("left-parent", nodes[0], nodes[0], ("左親",)),
            MirrorEntry("right-parent", nodes[2], nodes[2], ("右親",)),
        )
        child_mapping = MirrorMapping(
            MirrorEntry("left-child", nodes[1], nodes[1], ("左子",)),
            MirrorEntry("right-child", nodes[3], nodes[3], ("右子",)),
        )
        transaction = MirrorPoseTransaction(
            adapter,
            model_root="|model",
            model_uuid="model-uuid",
            mappings=[child_mapping, parent_mapping],
        )

        self.assertEqual(transaction.apply(), 2)
        target_writes = [
            plug
            for plug in cmds.set_attr_calls
            if plug.startswith((nodes[2] + ".", nodes[3] + "."))
        ]
        self.assertTrue(target_writes)
        self.assertTrue(target_writes[0].startswith(nodes[2] + "."))

    def test_failure_rolls_back_exact_target_values(self):
        cmds, adapter, mapping = self._make()
        original = dict(cmds.values)
        cmds.fail_plug = "|model|R_arm.rotateX"

        with self.assertRaises(MirrorActionError):
            self._transaction(adapter, mapping).apply()
        self.assertEqual(cmds.values, original)
        self.assertEqual(cmds.incoming["|model|R_arm.rotateX"], [])

    def test_uuid_mismatch_and_driven_target_fail_before_write(self):
        cmds, adapter, mapping = self._make()
        cmds.uuid = "other-model"
        with self.assertRaises(MirrorActionError):
            self._transaction(adapter, mapping).apply()

        cmds.uuid = "model-uuid"
        cmds.incoming["|model|R_arm.rotateX"] = ["other.output"]
        before = dict(cmds.values)
        with self.assertRaises(MirrorActionError):
            self._transaction(adapter, mapping).apply()
        self.assertEqual(cmds.values, before)

    def test_direct_anim_curve_target_gets_current_frame_keys(self):
        cmds, adapter, mapping = self._make()
        for channel in ("rotateX", "rotateY", "rotateZ"):
            cmds.incoming[f"|model|R_arm.{channel}"] = [
                f"animCurve_{channel}.output"
            ]
            cmds.curve_keys[f"animCurve_{channel}"] = {8.0: 100.0, 14.0: -100.0}

        self.assertEqual(self._transaction(adapter, mapping).apply(), 1)

        self.assertEqual(
            cmds.keyframe_edits,
            [
                ("animCurve_rotateX", (12.0, 12.0), 4.0),
                ("animCurve_rotateY", (12.0, 12.0), -5.0),
                ("animCurve_rotateZ", (12.0, 12.0), -6.0),
            ],
        )
        self.assertEqual(cmds.curve_keys["animCurve_rotateY"], {8.0: 100.0, 12.0: -5.0, 14.0: -100.0})

    def test_keyed_failure_uses_undo_and_preserves_source_and_adjacent_keys(self):
        cmds, adapter, mapping = self._make()
        source_before = dict(cmds.values)
        target_before = dict(cmds.values)
        cmds.incoming["|model|R_arm.rotateX"] = ["animCurve_rotateX.output"]
        cmds.curve_keys["animCurve_rotateX"] = {8.0: 37.0, 14.0: -12.0}
        cmds.undo_values = dict(cmds.values)
        cmds.undo_keyframes = list(cmds.keyframes)
        cmds.undo_curve_keys = {
            curve: dict(keys) for curve, keys in cmds.curve_keys.items()
        }
        cmds.fail_plug = "|model|R_arm.rotateY"

        with self.assertRaises(MirrorActionError):
            self._transaction(adapter, mapping).apply()

        self.assertEqual(cmds.values, target_before)
        self.assertEqual(cmds.values["|model|L_arm.rotateY"], source_before["|model|L_arm.rotateY"])
        self.assertEqual(cmds.keyframes, [])
        self.assertEqual(cmds.curve_keys["animCurve_rotateX"], {8.0: 37.0, 14.0: -12.0})


if __name__ == "__main__":
    unittest.main()
