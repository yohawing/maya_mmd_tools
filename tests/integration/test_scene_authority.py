"""Integration coverage for scene-global physics and light authorities."""

from pathlib import Path
from types import SimpleNamespace

from maya import cmds

from mmd_tools.converters.light_converter import (
    _get_or_create_light_direction_node,
    create_mmd_light_controller,
    set_mmd_light_direction,
)
from mmd_tools.converters.physics_scene_builder import build_physics_live_graph
from mmd_tools.core.constants import ATTR_MMD_LIGHT
from mmd_tools.core.namespace_utils import NamespaceUtils
from tests.common.maya_test_base import MayaTestBase


class TestSceneAuthority(MayaTestBase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        plugin_path = str(
            Path(__file__).resolve().parents[2] / "mmd_tools" / "plugin_main.py"
        )
        try:
            cmds.loadPlugin(plugin_path)
        except Exception:
            pass

    def test_two_namespaced_models_share_one_world_and_light(self):
        solvers = []
        lights = []
        direction_nodes = []
        for namespace in ("modelA", "modelB"):
            with NamespaceUtils.namespace_context(namespace):
                root = cmds.group(empty=True, name="root")
                cmds.select(clear=True)
                joint = cmds.joint(name="bone")
                cmds.parent(joint, root)
                graph = build_physics_live_graph(
                    rigid_bodies=[
                        SimpleNamespace(physics_mode=1, related_bone_index=0)
                    ],
                    bones=[SimpleNamespace(parent_bone_index=-1)],
                    maya_joints=[joint],
                    root_group=root,
                )
                solvers.append(graph["solver"])
                light = create_mmd_light_controller()
                lights.append(light)
                direction_nodes.append(_get_or_create_light_direction_node(light))
                if namespace == "modelA":
                    initial_color = cmds.getAttr(f"{light}.mmd_light_color")[0]
                    self.assertTrue(
                        all(abs(value - (154.0 / 255.0)) <= 1.0e-6 for value in initial_color)
                    )
                    cmds.setAttr(
                        f"{light}.mmd_light_color",
                        0.2,
                        0.3,
                        0.4,
                        type="float3",
                    )

        world_shapes = cmds.ls(type="mmdPhysicsWorldShape", long=True) or []
        self.assertEqual(world_shapes, ["|MMD_PhysicsWorld|MMD_PhysicsWorldShape"])
        for solver in solvers:
            sources = cmds.listConnections(
                f"{solver}.inWorldSettings",
                source=True,
                destination=False,
                shapes=True,
            ) or []
            self.assertEqual(cmds.ls(sources, long=True), world_shapes)

        self.assertEqual(lights, ["|mmd_light", "|mmd_light"])
        self.assertEqual(
            cmds.ls(f"*.{ATTR_MMD_LIGHT}", objectsOnly=True, long=True) or [],
            ["|mmd_light"],
        )
        self.assertEqual(
            tuple(round(value, 6) for value in cmds.getAttr("|mmd_light.mmd_light_color")[0]),
            (0.2, 0.3, 0.4),
        )
        self.assertEqual(
            set_mmd_light_direction((0.25, -1.0, 0.75), color=(0.6, 0.7, 0.8)),
            "|mmd_light",
        )
        self.assertEqual(
            tuple(round(value, 6) for value in cmds.getAttr("|mmd_light.mmd_light_color")[0]),
            (0.6, 0.7, 0.8),
        )
        self.assertEqual(direction_nodes, ["mmd_light_dirVP", "mmd_light_dirVP"])
        self.assertEqual(cmds.ls(type="vectorProduct", long=True), ["mmd_light_dirVP"])

        light_shapes = cmds.listRelatives(
            "|mmd_light", shapes=True, type="directionalLight", fullPath=True
        ) or []
        self.assertEqual(len(light_shapes), 1)
        color_sources = cmds.listConnections(
            f"{light_shapes[0]}.color",
            source=True,
            destination=False,
            plugs=True,
        ) or []
        self.assertEqual(color_sources, ["mmd_light.mmd_light_color"])
        matrix_sources = cmds.listConnections(
            "mmd_light_dirVP.matrix",
            source=True,
            destination=False,
            plugs=True,
        ) or []
        self.assertEqual(matrix_sources, ["mmd_light.worldMatrix"])
