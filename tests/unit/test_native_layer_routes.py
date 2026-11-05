"""Exact native family provenance, separate from physical connection records."""

import unittest

from mmd_tools.core.native_layer_routes import (
    NativeLayerRouteError,
    resolve_joint_input_source,
    source_joint_destinations,
)
from mmd_tools.core.mmd_control_rig_analyzer import (
    INPUT_SOLVER_OUTPUT, INPUT_UNSUPPORTED,
    MmdControlRigBoneFact, MmdControlRigConnectionFact,
    _collect_incoming_connections, classify_mmd_control_rig,
)


class _Graph:
    def __init__(self):
        self.types = {"solver": "mmdCcdIk", "other": "mmdCcdIk", "append": "mmdAppend",
                      "|model|joint": "joint", "|other|joint": "joint",
                      "layer": "animBlendNodeAdditiveRotation", "layer2": "animBlendNodeAdditiveRotation",
                      "curve": "animCurveTA", "external": "plusMinusAverage"}
        self.sources = {}

    def nodeType(self, node):
        return self.types[node]

    def ls(self, node, **_kwargs):
        return [node] if node in self.types else []

    def connectionInfo(self, plug, **kwargs):
        if kwargs.get("isExactDestination"):
            return plug in self.sources
        if plug in self.sources:
            return self.sources[plug]
        if plug[-1:] in "XYZ":
            parent = plug[:-1]
            if parent in self.sources:
                source = self.sources[parent]
                return source + (".outputRotateElement" if ".outputRotate[" in source else "") + plug[-1]
        return ""

    def listConnections(self, plug, **_kwargs):
        return [dest for dest, source in self.sources.items() if source == plug]

    def listHistory(self, _node, **_kwargs):
        return []

    def native(self, *, layered=False, joint="|model|joint", source="solver.outputRotate[1]"):
        for axis in "XYZ":
            child = source + (".outputRotateElement" if ".outputRotate[" in source else "") + axis
            if layered:
                self.sources[f"layer.inputA{axis}"] = child
                self.sources[f"layer2.inputA{axis}"] = f"layer.output{axis}"
                self.sources[f"{joint}.rotate{axis}"] = f"layer2.output{axis}"
            else:
                self.sources[f"{joint}.rotate{axis}"] = child


class TestNativeLayerRoutes(unittest.TestCase):
    def test_old_parent_new_xyz_and_layer_base_resolve_same_native_owner(self):
        for source in ("solver.outputRotate[1]", "append.outputRotate"):
            for layout in ("parent", "xyz", "layers"):
                with self.subTest(source=source, layout=layout):
                    graph = _Graph()
                    if layout == "parent":
                        graph.sources["|model|joint.rotate"] = source
                    else:
                        graph.native(source=source, layered=layout == "layers")
                    self.assertEqual(resolve_joint_input_source("|model|joint", "rotate", cmds_module=graph), source)
                    self.assertEqual(source_joint_destinations(source, "rotate", cmds_module=graph), ["|model|joint"])

    def test_mixed_solver_or_axis_cannot_be_a_native_family(self):
        for replacement in ("other.outputRotate[1].outputRotateElementY", "solver.outputRotate[1].outputRotateElementX", "curve.output", ""):
            with self.subTest(replacement=replacement):
                graph = _Graph()
                graph.native()
                graph.sources["|model|joint.rotateY"] = replacement
                with self.assertRaises(NativeLayerRouteError):
                    resolve_joint_input_source("|model|joint", "rotate", cmds_module=graph)

    def test_external_layer_value_is_rejected(self):
        graph = _Graph()
        graph.native(layered=True)
        graph.sources["layer.inputBX"] = "external.output1D"
        with self.assertRaisesRegex(NativeLayerRouteError, "Unsupported animation-layer"):
            resolve_joint_input_source("|model|joint", "rotate", cmds_module=graph)

    def test_input_b_is_not_a_native_base_route(self):
        graph = _Graph()
        for axis in "XYZ":
            graph.sources[f"layer.inputB{axis}"] = f"solver.outputRotate[1].outputRotateElement{axis}"
            graph.sources[f"|model|joint.rotate{axis}"] = f"layer.output{axis}"
        with self.assertRaises(NativeLayerRouteError):
            resolve_joint_input_source("|model|joint", "rotate", cmds_module=graph)

    def test_unrelated_joint_destination_is_not_hidden(self):
        graph = _Graph()
        graph.native()
        graph.native(joint="|other|joint")
        self.assertEqual(source_joint_destinations("solver.outputRotate[1]", "rotate", cmds_module=graph), ["|model|joint", "|other|joint"])

    def test_ordinary_animation_has_no_native_provenance(self):
        graph = _Graph()
        for axis in "XYZ":
            graph.sources[f"layer.inputA{axis}"] = "curve.output"
            graph.sources[f"|model|joint.rotate{axis}"] = f"layer.output{axis}"
        self.assertIsNone(resolve_joint_input_source("|model|joint", "rotate", cmds_module=graph))

    def test_physical_connections_stay_exact_for_journals(self):
        for layout in ("parent", "xyz", "layers"):
            graph = _Graph()
            if layout == "parent":
                graph.sources["|model|joint.rotate"] = "solver.outputRotate[1]"
            else:
                graph.native(layered=layout == "layers")
            incoming = _collect_incoming_connections(graph, "|model|joint")
            self.assertEqual(len(incoming), 1 if layout == "parent" else 3)
            for row in incoming:
                self.assertEqual(graph.sources[row.destination_plug], row.source_plug)
            logical = MmdControlRigConnectionFact("solver.outputRotate[1]", "|model|joint.rotate", "mmdCcdIk")
            fact = MmdControlRigBoneFact("|model|joint", "aux", incoming=incoming, native_inputs=(logical,))
            binding = classify_mmd_control_rig("|model", [fact]).bones[0]
            self.assertEqual(binding.input_kind, INPUT_SOLVER_OUTPUT)
            self.assertEqual(binding.incoming, incoming)
            self.assertNotIn("nativeInputs", binding.to_dict())

    def test_invalid_native_route_blocks_solver_role_too(self):
        fact = MmdControlRigBoneFact(
            "|model|joint", "左足", native_input_blockers=("foreign native owner",),
            solver_input_plugs=("solver.inputRotate[1].inputRotateElementX",),
        )
        spec = classify_mmd_control_rig("|model", [fact])
        self.assertEqual(spec.bones[0].input_kind, INPUT_UNSUPPORTED)
        self.assertTrue(spec.roles_by_name["left_leg"].blockers)


if __name__ == "__main__":
    unittest.main()
