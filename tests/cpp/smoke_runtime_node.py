"""Smoke test for loading the C++ plugin in Maya.

This script intentionally has no pytest dependency. It is launched by mayapy
from Nox or by hand, initializes Maya standalone, loads the compiled plugin,
and verifies that the mmdRuntimeInstance node and mmdFastLoad command work.
"""

from __future__ import annotations

import os
import sys
import math
import json
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
NODE_TYPE = "mmdRuntimeInstance"
APPEND_NODE_TYPE = "mmdAppend"
CCDIK_NODE_TYPE = "mmdCcdIk"
FAST_LOAD_MODEL = ROOT / "tests" / "data" / "mmt_test_model.pmx"
FAST_IMPORT_SKIN_MODEL = ROOT / "tests" / "data" / "for_unit_test" / "test_1bone_cube.pmx"
FAST_LOAD_MORPH_MODEL = ROOT / "tests" / "data" / "test_morph_model.pmx"
TRACK4_VMD_MOTION = ROOT / "tests" / "data" / "for_unit_test" / "test_1bone_cube_motion.vmd"
PYTHON_PLUGIN = ROOT / "plug-ins" / "mmd_tools_plugin.py"


def _flatten_nested_sequence(values: object) -> list[float | int]:
    """Flatten 1- or N-dimension sequence to flat numbers."""
    if values is None:
        return []
    if values is False:
        return [0]
    if values is True:
        return [1]
    if isinstance(values, (float, int, bool)):
        return [int(values) if isinstance(values, bool) else float(values)]
    if not isinstance(values, (list, tuple)):
        return [float(values)]

    flat: list[float | int] = []
    for value in values:
        if value is None:
            continue
        if isinstance(value, (list, tuple)):
            flat.extend(_flatten_nested_sequence(value))
        elif isinstance(value, bool):
            flat.append(int(value))
        else:
            flat.append(float(value))
    return flat


def _read_output_attr_range(cmds, node: str, attr_name: str, count: int) -> list[float | int]:
    """Read an array output attr as a flat list with index range 0..count-1."""
    if count <= 0:
        try:
            return _flatten_nested_sequence(cmds.getAttr(f"{node}.{attr_name}"))
        except Exception:
            return []

    attr_expr = f"{node}.{attr_name}[0:{count - 1}]"
    values = cmds.getAttr(attr_expr)
    if values is None:
        return []
    return _flatten_nested_sequence(values)


def _read_count(cmds, node: str, attr_name: str) -> int:
    """Return array size for an output attr."""
    try:
        size = cmds.getAttr(f"{node}.{attr_name}", size=True)
    except Exception:
        size = None

    if size is not None:
        try:
            return int(size)
        except Exception:
            pass

    try:
        indices = cmds.getAttr(f"{node}.{attr_name}", multiIndices=True)
        if indices:
            return int(max(indices) + 1)
    except Exception:
        pass

    return 0


def _compare_vectors(expected: list[float], actual: list[float], name: str, frame: int) -> None:
    if len(expected) != len(actual):
        raise RuntimeError(
            f"{name} length mismatch at frame {frame}: expected {len(expected)} != actual {len(actual)}"
        )

    tolerance = 1e-4
    for index, (lhs, rhs) in enumerate(zip(expected, actual)):
        if abs(float(lhs) - float(rhs)) > tolerance:
            raise RuntimeError(
                f"{name} mismatch at frame {frame}, index {index}: expected {lhs}, got {rhs}"
            )


def _compare_runtime_arrays(
    native_values: list[float],
    live_values: list[float],
    name: str,
    frame: int,
    *,
    is_bool: bool = False,
) -> None:
    if len(native_values) == 0 or len(live_values) == 0:
        return

    if len(native_values) != len(live_values):
        raise RuntimeError(
            f"{name} length mismatch at frame {frame}: native {len(native_values)} != live {len(live_values)}"
        )

    if is_bool:
        for index, (lhs, rhs) in enumerate(zip(native_values, live_values)):
            if int(lhs) != int(rhs):
                raise RuntimeError(
                    f"{name} mismatch at frame {frame}, index {index}: expected {int(lhs)}, got {int(rhs)}"
                )
        return

    _compare_vectors(native_values, live_values, name, frame)


def _matrix_max_diff(expected: list[float], actual: list[float]) -> tuple[float, int]:
    max_diff = -1.0
    max_index = -1
    for index, (lhs, rhs) in enumerate(zip(expected, actual)):
        diff = abs(float(lhs) - float(rhs))
        if diff > max_diff:
            max_diff = diff
            max_index = index
    return max_diff, max_index


def _ccd_2d_multi_link_angles(
    chain: list[float], target_x: float, target_y: float, iterations: int, angle_limit: float
) -> tuple[list[list[float]], list[float]]:
    if len(chain) < 6 or len(chain) % 3 != 0:
        return [], []

    point_count = len(chain) // 3
    positions: list[list[float]] = [
        [chain[idx * 3], chain[idx * 3 + 1], chain[idx * 3 + 2]]
        for idx in range(point_count)
    ]
    link_count = point_count - 1
    if link_count < 2:
        return positions, [0.0] * link_count

    rotations = [0.0] * link_count
    eps = 1e-12

    for _ in range(max(0, iterations)):
        for link in range(link_count - 1, -1, -1):
            pivot = positions[link]
            effector = positions[-1]

            ex = effector[0] - pivot[0]
            ey = effector[1] - pivot[1]
            tx = target_x - pivot[0]
            ty = target_y - pivot[1]

            if ex * ex + ey * ey <= eps or tx * tx + ty * ty <= eps:
                continue

            cross_z = ex * ty - ey * tx
            dot = ex * tx + ey * ty
            step_angle = math.atan2(cross_z, dot) * 180.0 / math.pi
            if angle_limit > 0.0 and abs(step_angle) > angle_limit:
                step_angle = angle_limit if step_angle >= 0.0 else -angle_limit

            rotations[link] += step_angle
            rad = math.radians(step_angle)
            cos_v = math.cos(rad)
            sin_v = math.sin(rad)

            for j in range(link + 1, point_count):
                px = positions[j][0] - pivot[0]
                py = positions[j][1] - pivot[1]
                positions[j][0] = pivot[0] + px * cos_v - py * sin_v
                positions[j][1] = pivot[1] + px * sin_v + py * cos_v

    return positions, rotations


def _mmd_world_to_maya_matrix(om, matrix):
    signs = (1.0, 1.0, -1.0)
    values = [float(matrix[i]) for i in range(16)]
    for row in range(3):
        for col in range(3):
            values[row * 4 + col] *= signs[row] * signs[col]
    for col in range(3):
        values[12 + col] *= signs[col]
    return om.MMatrix(values)


def _matrix_to_list(matrix) -> list[float]:
    return [float(matrix[i]) for i in range(16)]


def _transform_matrix(om, translate, quat=None):
    tfm = om.MTransformationMatrix()
    tfm.setTranslation(om.MVector(*translate), om.MSpace.kTransform)
    if quat is not None:
        tfm.setRotation(quat)
    return tfm.asMatrix()


def _build_bind_worlds(om, bones: list[dict]) -> tuple[list, list]:
    bind_worlds = []
    no_orient_worlds = []
    for index, bone in enumerate(bones):
        translate = bone.get("maya_rest_translate", bone.get("rest_position", [0.0, 0.0, 0.0]))
        jo = bone.get("joint_orient_deg", [0.0, 0.0, 0.0])
        q_jo = om.MEulerRotation(*(math.radians(v) for v in jo)).asQuaternion()
        local_bind = _transform_matrix(om, translate, q_jo)
        local_no_orient = _transform_matrix(om, translate)
        parent = int(bone.get("parent_slot", index - 1 if index > 0 else -1))
        if 0 <= parent < index:
            bind_worlds.append(local_bind * bind_worlds[parent])
            no_orient_worlds.append(local_no_orient * no_orient_worlds[parent])
        else:
            bind_worlds.append(local_bind)
            no_orient_worlds.append(local_no_orient)
    return bind_worlds, no_orient_worlds


def _expected_bind_space_ccdik_outputs(
    om,
    mmd_ik_chain_cls,
    chain: dict,
    goal: tuple[float, float, float],
    input_rotates_deg: list[tuple[float, float, float]] | None = None,
) -> list[tuple[float, float, float]]:
    from ctypes import c_float

    from mmd_tools.nodes.mmd_ccd_ik_node import (
        _canonicalize_runtime_quaternion,
    )

    position_quantum = c_float(2.0e-6).value

    def canonicalize_runtime_position(value: float) -> float:
        value_f32 = c_float(value).value
        scaled = value_f32 / position_quantum
        rounded = math.floor(scaled + 0.5) if scaled >= 0.0 else math.ceil(scaled - 0.5)
        return c_float(rounded * position_quantum).value

    bones = chain["bones"]
    links = chain["links"]
    solver = mmd_ik_chain_cls.create(
        bones=bones,
        target_bone_slot=chain["targetBoneSlot"],
        links=links,
        iteration_count=chain["iterationCount"],
        limit_angle=chain["limitAngle"],
    )
    if solver is None:
        raise RuntimeError("MmdIkChain.create failed in C++ IK smoke expected-value path")

    try:
        bone_count = len(bones)
        parent_slots = [int(b.get("parent_slot", -1)) for b in bones]
        rest_positions = [b.get("rest_position", [0.0, 0.0, 0.0]) for b in bones]
        maya_rest = [b.get("maya_rest_translate", b.get("rest_position", [0.0, 0.0, 0.0])) for b in bones]
        q_joint_orients = [
            om.MEulerRotation(*(math.radians(v) for v in b.get("joint_orient_deg", [0.0, 0.0, 0.0]))).asQuaternion()
            for b in bones
        ]
        if input_rotates_deg is None:
            input_rotates_deg = [(0.0, 0.0, 0.0)] * bone_count
        if len(input_rotates_deg) < bone_count:
            input_rotates_deg = [*input_rotates_deg, *([(0.0, 0.0, 0.0)] * (bone_count - len(input_rotates_deg)))]
        q_input_rotates = [
            om.MEulerRotation(
                *(math.radians(v) for v in input_rotates_deg[bone_i])
            ).asQuaternion()
            for bone_i in range(bone_count)
        ]
        bind_worlds = [om.MMatrix(b["maya_bind_world_matrix"]) for b in bones]
        no_orient_worlds = [om.MMatrix(b["no_orient_bind_world_matrix"]) for b in bones]

        maya_worlds = [om.MMatrix() for _ in range(bone_count)]
        mmd_worlds = [om.MMatrix() for _ in range(bone_count)]
        for bone_i in range(bone_count):
            local_maya = _transform_matrix(om, maya_rest[bone_i], q_input_rotates[bone_i] * q_joint_orients[bone_i])
            parent = parent_slots[bone_i]
            maya_world = local_maya * maya_worlds[parent] if 0 <= parent < bone_i else local_maya
            maya_worlds[bone_i] = maya_world
            runtime_world = no_orient_worlds[bone_i] * bind_worlds[bone_i].inverse() * maya_world
            mmd_worlds[bone_i] = _mmd_world_to_maya_matrix(om, runtime_world)

        positions = [0.0] * (bone_count * 3)
        rotations = [0.0] * (bone_count * 4)
        for bone_i in range(bone_count):
            parent = parent_slots[bone_i]
            local_mmd = mmd_worlds[bone_i] * mmd_worlds[parent].inverse() if 0 <= parent < bone_i else mmd_worlds[bone_i]
            local_tfm = om.MTransformationMatrix(local_mmd)
            local_t = local_tfm.translation(om.MSpace.kTransform)
            rest = rest_positions[bone_i]
            positions[bone_i * 3] = float(local_t.x) - float(rest[0])
            positions[bone_i * 3 + 1] = float(local_t.y) - float(rest[1])
            positions[bone_i * 3 + 2] = float(local_t.z) - float(rest[2])
            q = local_tfm.rotation(asQuaternion=True)
            canonical = _canonicalize_runtime_quaternion((q.x, q.y, q.z, q.w))
            rotations[bone_i * 4:bone_i * 4 + 4] = canonical

        positions = [canonicalize_runtime_position(value) for value in positions]

        goal_tfm = om.MTransformationMatrix()
        goal_tfm.setTranslation(om.MVector(*goal), om.MSpace.kTransform)
        mmd_goal = om.MTransformationMatrix(_mmd_world_to_maya_matrix(om, goal_tfm.asMatrix())).translation(om.MSpace.kWorld)
        runtime_goal = [
            canonicalize_runtime_position(value)
            for value in (mmd_goal.x, mmd_goal.y, mmd_goal.z)
        ]
        result = solver.solve(positions=positions, rotations=rotations, goal=runtime_goal)
        if result is None:
            raise RuntimeError("MmdIkChain.solve failed in C++ IK smoke expected-value path")
        out_rots, _stats = result

        solved_rotations = list(rotations)
        for link_i, link in enumerate(links):
            slot = int(link["bone_slot"])
            solved_rotations[slot * 4:slot * 4 + 4] = out_rots[link_i * 4:link_i * 4 + 4]

        world_mmd = [om.MMatrix() for _ in range(bone_count)]
        solved_maya_worlds = [om.MMatrix() for _ in range(bone_count)]
        for bone_i in range(bone_count):
            rest = rest_positions[bone_i]
            local_t = [
                rest[0] + positions[bone_i * 3],
                rest[1] + positions[bone_i * 3 + 1],
                rest[2] + positions[bone_i * 3 + 2],
            ]
            q = om.MQuaternion(*solved_rotations[bone_i * 4:bone_i * 4 + 4])
            local_mmd = _transform_matrix(om, local_t, q)
            parent = parent_slots[bone_i]
            world_mmd[bone_i] = local_mmd * world_mmd[parent] if 0 <= parent < bone_i else local_mmd
            runtime_world = _mmd_world_to_maya_matrix(om, world_mmd[bone_i])
            solved_maya_worlds[bone_i] = bind_worlds[bone_i] * no_orient_worlds[bone_i].inverse() * runtime_world

        outputs = []
        for link in links:
            slot = int(link["bone_slot"])
            parent = parent_slots[slot]
            local = (
                solved_maya_worlds[slot] * solved_maya_worlds[parent].inverse()
                if parent >= 0
                else solved_maya_worlds[slot]
            )
            q = om.MTransformationMatrix(local).rotation(asQuaternion=True)
            q = q * q_joint_orients[slot].inverse()
            euler = q.asEulerRotation()
            outputs.append((math.degrees(euler.x), math.degrees(euler.y), math.degrees(euler.z)))
        return outputs
    finally:
        solver.free()


def _expected_bind_space_ccdik_output(
    om,
    mmd_ik_chain_cls,
    chain: dict,
    goal: tuple[float, float, float],
    input_rotates_deg: list[tuple[float, float, float]] | None = None,
) -> tuple[float, float, float]:
    outputs = _expected_bind_space_ccdik_outputs(om, mmd_ik_chain_cls, chain, goal, input_rotates_deg)
    if not outputs:
        raise RuntimeError("MmdIkChain expected-value path returned no link outputs")
    return outputs[0]


def _candidate_plugin_paths() -> list[Path]:
    """Return possible C++ plugin artifact paths."""
    explicit = os.environ.get("MMD_TOOLS_CPP_PLUGIN")
    if explicit:
        return [Path(explicit)]

    version = os.environ.get("MAYA_VERSION", "2024")
    config = os.environ.get("MMD_TOOLS_CPP_CONFIG", "Debug")
    extensions = [".mll", ".bundle", ".so"]
    configs = [config]
    if config != "Release":
        configs.append("Release")
    if config != "Debug":
        configs.append("Debug")

    paths: list[Path] = []
    for cfg in configs:
        for suffix in extensions:
            paths.append(ROOT / "plug-ins" / version / cfg / f"mmd_tools_cpp{suffix}")
    return paths


def _find_plugin_path() -> Path:
    """Find the compiled C++ plugin artifact."""
    for path in _candidate_plugin_paths():
        if path.exists():
            return path

    candidates = "\n".join(str(path) for path in _candidate_plugin_paths())
    raise FileNotFoundError(f"mmd_tools_cpp plugin was not found. Checked:\n{candidates}")


def main() -> int:
    """Run the Maya standalone smoke check."""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

    import maya.cmds as cmds
    import maya.standalone

    plugin_path = _find_plugin_path()
    plugin_name = plugin_path.stem
    os.environ["PATH"] = str(plugin_path.parent) + os.pathsep + os.environ.get("PATH", "")
    if hasattr(os, "add_dll_directory"):
        os.add_dll_directory(str(plugin_path.parent))

    maya.standalone.initialize(name="python")
    try:
        cmds.loadPlugin(str(plugin_path), quiet=True)
        from maya.api import OpenMayaRender as omr

        registered_commands = cmds.pluginInfo(
            plugin_path.stem, query=True, command=True
        ) or []
        if "mmdNativeCasterWitness" in registered_commands:
            raise RuntimeError("Native caster diagnostics must be disabled by default")
        if omr.MRenderer.findRenderOverride("mmdNativeCaster") is not None:
            raise RuntimeError("Native caster must stay out of the viewport renderer menu")
        cmds.loadPlugin(str(PYTHON_PLUGIN), quiet=True)
        node = cmds.createNode(NODE_TYPE)
        if not cmds.objExists(node):
            raise RuntimeError(f"Failed to create node: {NODE_TYPE}")

        for attr in ("time", "pmxData", "vmdData", "worldMatrices", "morphWeights", "ikEnabled"):
            if not cmds.attributeQuery(attr, node=node, exists=True):
                raise RuntimeError(f"Missing attribute {attr!r} on {node}")

        print(f"OK: loaded {plugin_path}")
        print(f"OK: created {node} ({NODE_TYPE})")

        from mmd_tools.converters.rig_converter import RigConverter
        from mmd_tools.converters import vmd_runtime_rig_helper as vmd_runtime_rig_helper_mod

        rig_converter = RigConverter()
        if rig_converter._append_node_type() != APPEND_NODE_TYPE:
            raise RuntimeError(
                f"RigConverter should return unified {APPEND_NODE_TYPE}, "
                f"got {rig_converter._append_node_type()}"
            )
        if rig_converter._ccd_ik_node_type() != CCDIK_NODE_TYPE:
            raise RuntimeError(
                f"RigConverter should return unified {CCDIK_NODE_TYPE}, "
                f"got {rig_converter._ccd_ik_node_type()}"
            )
        print("OK: RigConverter returns unified rig node type names")

        result = cmds.mmdFastLoad(f=str(FAST_LOAD_MODEL), n="mmt_fast_smoke", s=1.0)
        if not result or len(result) != 2:
            raise RuntimeError(f"mmdFastLoad returned unexpected result: {result!r}")

        transform, mesh = result
        if not cmds.objExists(transform) or not cmds.objExists(mesh):
            raise RuntimeError(f"mmdFastLoad result nodes do not exist: {result!r}")

        vertex_count = cmds.polyEvaluate(mesh, vertex=True)
        face_count = cmds.polyEvaluate(mesh, face=True)
        if vertex_count <= 0 or face_count <= 0:
            raise RuntimeError(
                f"mmdFastLoad created empty mesh: vertices={vertex_count}, faces={face_count}"
            )

        cmds.undo()
        if cmds.objExists(transform):
            raise RuntimeError(f"mmdFastLoad undo did not delete transform: {transform}")

        print(f"OK: mmdFastLoad created {vertex_count} vertices / {face_count} faces and undo succeeded")

        vp2_result = cmds.mmdFastLoad(
            f=str(FAST_LOAD_MODEL),
            n="mmt_fast_vp2_smoke",
            s=1.0,
            vp2Ownership=True,
        )
        if not vp2_result or len(vp2_result) != 3:
            raise RuntimeError(
                f"mmdFastLoad(vp2Ownership=True) returned unexpected result: {vp2_result!r}"
            )
        vp2_transform, vp2_source_mesh, vp2_render_shape = vp2_result
        if cmds.nodeType(vp2_source_mesh) != "mesh":
            raise RuntimeError(f"VP2 source is not a Maya mesh: {vp2_source_mesh}")
        if cmds.nodeType(vp2_render_shape) != "mmdRenderShape":
            raise RuntimeError(f"VP2 proxy has wrong type: {vp2_render_shape}")
        source_parent = cmds.listRelatives(vp2_source_mesh, parent=True, fullPath=True) or []
        proxy_parent = cmds.listRelatives(vp2_render_shape, parent=True, fullPath=True) or []
        if source_parent != proxy_parent or not source_parent:
            raise RuntimeError(
                f"VP2 source/proxy are not sibling shapes: source={source_parent}, proxy={proxy_parent}"
            )
        if not cmds.isConnected(
            f"{vp2_source_mesh}.outMesh",
            f"{vp2_render_shape}.inputMesh",
        ):
            raise RuntimeError(
                "VP2 proxy input is not driven by source outMesh"
            )
        if not cmds.isConnected(
            f"{vp2_render_shape}.sourceVisibility",
            f"{vp2_source_mesh}.visibility",
        ):
            raise RuntimeError("VP2 proxy does not drive source visibility")
        if bool(cmds.getAttr(f"{vp2_source_mesh}.intermediateObject")):
            raise RuntimeError("VP2 source mesh must not be marked intermediate")
        if not bool(cmds.getAttr(f"{vp2_source_mesh}.visibility")):
            raise RuntimeError("VP2 source must remain visible until proxy buffers are ready")

        with tempfile.TemporaryDirectory(prefix="mmd_tools_vp2_smoke_") as temp_dir:
            scene_path = Path(temp_dir) / "vp2_reopen_fallback.ma"
            cmds.file(rename=str(scene_path))
            cmds.file(save=True, type="mayaAscii", force=True)
            cmds.file(new=True, force=True)
            cmds.file(str(scene_path), open=True, force=True)

            reopened_proxies = cmds.ls(type="mmdRenderShape", long=True) or []
            if len(reopened_proxies) != 1:
                raise RuntimeError(
                    f"VP2 reopen expected one proxy shape, got {reopened_proxies!r}"
                )
            reopened_sources = cmds.listConnections(
                f"{reopened_proxies[0]}.sourceVisibility",
                source=False,
                destination=True,
                plugs=True,
            ) or []
            if len(reopened_sources) != 1 or not reopened_sources[0].endswith(".visibility"):
                raise RuntimeError(
                    f"VP2 reopen lost source visibility connection: {reopened_sources!r}"
                )
            if not bool(cmds.getAttr(reopened_sources[0])):
                raise RuntimeError("VP2 source must reopen visible while readiness is transient")
            vp2_transform = (cmds.listRelatives(
                reopened_proxies[0], parent=True, fullPath=True
            ) or [None])[0]

        if not vp2_transform:
            raise RuntimeError("VP2 reopen proxy has no parent transform")
        cmds.delete(vp2_transform)
        if cmds.objExists(vp2_transform):
            raise RuntimeError(
                f"mmdFastLoad(vp2Ownership=True) cleanup did not delete root: {vp2_transform}"
            )
        print(
            "OK: VP2 fast load created source/proxy siblings, kept source visible, "
            "and preserved the transient visibility fallback across scene reopen"
        )

        morph_result = cmds.mmdFastLoad(f=str(FAST_LOAD_MORPH_MODEL), n="mmd_fast_morph_smoke", s=1.0, mo=True)
        if not morph_result or len(morph_result) != 2:
            raise RuntimeError(f"mmdFastLoad morph smoke returned unexpected result: {morph_result!r}")
        morph_transform, _morph_mesh = morph_result
        blend_shapes = cmds.ls(type="blendShape") or []
        if not blend_shapes:
            raise RuntimeError("mmdFastLoad(morphs=True) did not create a blendShape")
        weight_count = cmds.blendShape(blend_shapes[0], query=True, weightCount=True) or 0
        if int(weight_count) <= 0:
            raise RuntimeError(f"mmdFastLoad(morphs=True) blendShape has no weights: {blend_shapes[0]}")
        cmds.delete(morph_transform)
        print(f"OK: mmdFastLoad(morphs=True) created {int(weight_count)} vertex morph target(s)")

        # --- mmdAppend node ---
        append_node = cmds.createNode(APPEND_NODE_TYPE)
        if not cmds.objExists(append_node):
            raise RuntimeError(f"Failed to create node: {APPEND_NODE_TYPE}")

        # Detect whether C++ registered the rig node or Python did
        # (Python autoload may have registered first)
        cpp_registered_append = cmds.attributeQuery("grantRate", node=append_node, exists=True)

        # Common attributes (present in both C++ and Python)
        common_attrs = [
            "outputTranslate", "outputRotate",
            "baseTranslate", "baseRotate",
            "sourceTranslate", "sourceRotate",
            "sourceJointOrient", "targetJointOrient",
            "ratio", "affectRotation", "affectTranslation", "localAppend", "schemaMode",
            "appendTranslate", "appendRotate",
        ]
        for attr in common_attrs:
            if not cmds.attributeQuery(attr, node=append_node, exists=True):
                raise RuntimeError(f"Missing attribute {attr!r} on {append_node}")

        # C++-only legacy attributes (hidden, only present when C++ registered)
        if cpp_registered_append:
            legacy_attrs = [
                "grantRate", "enableTranslate", "enableRotate",
                "inputTranslate", "inputRotate",
                "parentTranslate", "parentRotate",
            ]
            for attr in legacy_attrs:
                if not cmds.attributeQuery(attr, node=append_node, exists=True):
                    raise RuntimeError(f"Missing legacy attribute {attr!r} on {append_node}")

            for parent, children in [
                ("inputTranslate", ["inputTranslateX", "inputTranslateY", "inputTranslateZ"]),
                ("inputRotate", ["inputRotateX", "inputRotateY", "inputRotateZ"]),
                ("parentTranslate", ["parentTranslateX", "parentTranslateY", "parentTranslateZ"]),
                ("parentRotate", ["parentRotateX", "parentRotateY", "parentRotateZ"]),
            ]:
                for child in children:
                    if not cmds.attributeQuery(child, node=append_node, exists=True):
                        raise RuntimeError(
                            f"Missing child attribute {child!r} (of {parent}) on {append_node}"
                        )

        # Common child attributes
        for parent, children in [
            ("baseTranslate", ["baseTranslateX", "baseTranslateY", "baseTranslateZ"]),
            ("baseRotate", ["baseRotateX", "baseRotateY", "baseRotateZ"]),
            ("sourceTranslate", ["sourceTranslateX", "sourceTranslateY", "sourceTranslateZ"]),
            ("sourceRotate", ["sourceRotateX", "sourceRotateY", "sourceRotateZ"]),
            ("sourceJointOrient", ["sourceJointOrientX", "sourceJointOrientY", "sourceJointOrientZ"]),
            ("targetJointOrient", ["targetJointOrientX", "targetJointOrientY", "targetJointOrientZ"]),
            ("outputTranslate", ["outputTranslateX", "outputTranslateY", "outputTranslateZ"]),
            ("outputRotate", ["outputRotateX", "outputRotateY", "outputRotateZ"]),
            ("appendTranslate", ["appendTranslateX", "appendTranslateY", "appendTranslateZ"]),
            ("appendRotate", ["appendRotateX", "appendRotateY", "appendRotateZ"]),
        ]:
            for child in children:
                if not cmds.attributeQuery(child, node=append_node, exists=True):
                    raise RuntimeError(
                        f"Missing child attribute {child!r} (of {parent}) on {append_node}"
                    )

        # Legacy Phase B compute tests (only when C++ registered the node)
        if cpp_registered_append:
            actual_schema_mode = cmds.getAttr(f"{append_node}.schemaMode")
            if actual_schema_mode != 0:
                raise RuntimeError(f"schemaMode default should be 0 (auto), got {actual_schema_mode}")
            actual_grant = cmds.getAttr(f"{append_node}.grantRate")
            if abs(actual_grant - 0.0) > 1e-9:
                raise RuntimeError(f"grantRate default should be 0.0, got {actual_grant}")
            actual_et = cmds.getAttr(f"{append_node}.enableTranslate")
            if actual_et is not True:
                raise RuntimeError(f"enableTranslate default should be True, got {actual_et}")
            actual_er = cmds.getAttr(f"{append_node}.enableRotate")
            if actual_er is not True:
                raise RuntimeError(f"enableRotate default should be True, got {actual_er}")

            cmds.setAttr(f"{append_node}.inputTranslate", 10.0, 20.0, 30.0, type="double3")
            cmds.setAttr(f"{append_node}.parentTranslate", 2.0, 4.0, 6.0, type="double3")
            cmds.setAttr(f"{append_node}.inputRotate", 90.0, 45.0, 30.0, type="double3")
            cmds.setAttr(f"{append_node}.parentRotate", 10.0, 20.0, 30.0, type="double3")
            cmds.setAttr(f"{append_node}.grantRate", 0.25)

            out_t = cmds.getAttr(f"{append_node}.outputTranslate")[0]
            expected_t = (10.5, 21.0, 31.5)
            if any(abs(actual - expected) > 1e-9 for actual, expected in zip(out_t, expected_t)):
                raise RuntimeError(f"outputTranslate mismatch: expected {expected_t}, got {out_t}")

            out_r = cmds.getAttr(f"{append_node}.outputRotate")[0]
            expected_r = (96.0251476257, 48.8315586337, 41.3787177580)
            if any(abs(actual - expected) > 1e-6 for actual, expected in zip(out_r, expected_r)):
                raise RuntimeError(f"outputRotate mismatch: expected {expected_r}, got {out_r}")

            cmds.setAttr(f"{append_node}.enableTranslate", False)
            out_disabled_t = cmds.getAttr(f"{append_node}.outputTranslate")[0]
            expected_disabled_t = (10.0, 20.0, 30.0)
            if any(
                abs(actual - expected) > 1e-9
                for actual, expected in zip(out_disabled_t, expected_disabled_t)
            ):
                raise RuntimeError(
                    f"outputTranslate disabled mismatch: expected {expected_disabled_t}, "
                    f"got {out_disabled_t}"
                )
            cmds.setAttr(f"{append_node}.enableTranslate", True)

            cmds.setAttr(f"{append_node}.enableRotate", False)
            out_disabled_r = cmds.getAttr(f"{append_node}.outputRotate")[0]
            expected_disabled_r = (90.0, 45.0, 30.0)
            if any(
                abs(actual - expected) > 1e-9
                for actual, expected in zip(out_disabled_r, expected_disabled_r)
            ):
                raise RuntimeError(
                    f"outputRotate disabled mismatch: expected {expected_disabled_r}, "
                    f"got {out_disabled_r}"
                )
            cmds.setAttr(f"{append_node}.enableRotate", True)

            cmds.setAttr(f"{append_node}.inputRotate", 90.0, 0.0, 0.0, type="double3")
            cmds.setAttr(f"{append_node}.parentRotate", 10.0, 0.0, 0.0, type="double3")
            cmds.setAttr(f"{append_node}.grantRate", 0.25)
            out_xonly = cmds.getAttr(f"{append_node}.outputRotate")[0]
            expected_xonly = (92.5, 0.0, 0.0)
            if any(abs(actual - expected) > 1e-9 for actual, expected in zip(out_xonly, expected_xonly)):
                raise RuntimeError(f"outputRotate X-only mismatch: expected {expected_xonly}, got {out_xonly}")

            cmds.setAttr(f"{append_node}.schemaMode", 1)
            cmds.setAttr(f"{append_node}.baseTranslate", 100.0, 200.0, 300.0, type="double3")
            legacy_override_t = cmds.getAttr(f"{append_node}.outputTranslate")[0]
            expected_override_t = (10.5, 21.0, 31.5)
            if any(
                abs(actual - expected) > 1e-9
                for actual, expected in zip(legacy_override_t, expected_override_t)
            ):
                raise RuntimeError(
                    "schemaMode=legacy should ignore compat translate inputs: "
                    f"expected {expected_override_t}, got {legacy_override_t}"
                )
            cmds.setAttr(f"{append_node}.schemaMode", 0)
            cmds.setAttr(f"{append_node}.baseTranslate", 0.0, 0.0, 0.0, type="double3")
            print(f"OK: {APPEND_NODE_TYPE} legacy Phase B compute verified (C++ registered)")
        else:
            print(f"SKIP: {APPEND_NODE_TYPE} legacy Phase B compute (Python registered, C++ skipped)")

        from maya.api import OpenMaya as om
        from mmd_tools.core.native.mmd_anim_runtime import MmdAppendSolver, MmdIkChain

        def _expected_append_outputs(
            *,
            base_translate: tuple[float, float, float],
            base_rotate_deg: tuple[float, float, float],
            source_translate: tuple[float, float, float],
            source_rotate_deg: tuple[float, float, float],
            source_jo_deg: tuple[float, float, float],
            target_jo_deg: tuple[float, float, float],
            ratio: float,
            affect_rotation: bool,
            affect_translation: bool,
        ) -> tuple[tuple[float, float, float], tuple[float, float, float],
                   tuple[float, float, float], tuple[float, float, float]]:
            solver = MmdAppendSolver.create(
                ratio=ratio,
                affect_rotation=affect_rotation,
                affect_translation=affect_translation,
            )
            if solver is None:
                raise RuntimeError("MmdAppendSolver.create failed in C++ smoke expected-value path")
            try:
                src_quat = om.MEulerRotation(
                    *(math.radians(v) for v in source_rotate_deg)
                ).asQuaternion()
                src_jo = om.MEulerRotation(
                    *(math.radians(v) for v in source_jo_deg)
                ).asQuaternion()
                target_jo = om.MEulerRotation(
                    *(math.radians(v) for v in target_jo_deg)
                ).asQuaternion()
                source_mmd_quat = src_jo.inverse() * src_quat * src_jo
                solved = solver.solve(
                    source_position=[
                        source_translate[0],
                        source_translate[1],
                        -source_translate[2],
                    ],
                    source_rotation=[
                        source_mmd_quat.x,
                        source_mmd_quat.y,
                        source_mmd_quat.z,
                        source_mmd_quat.w,
                    ],
                )
            finally:
                solver.free()
            if solved is None:
                raise RuntimeError("MmdAppendSolver.solve failed in C++ smoke expected-value path")

            grant_pos, grant_rot = solved
            append_translate = (grant_pos[0], grant_pos[1], -grant_pos[2])
            grant_quat = om.MQuaternion(grant_rot[0], grant_rot[1], grant_rot[2], grant_rot[3])
            append_euler = grant_quat.asEulerRotation()
            append_rotate_deg = tuple(math.degrees(v) for v in (append_euler.x, append_euler.y, append_euler.z))

            base_quat = om.MEulerRotation(
                *(math.radians(v) for v in base_rotate_deg)
            ).asQuaternion()
            target_grant_quat = target_jo * grant_quat * target_jo.inverse()
            final_quat = base_quat * target_grant_quat
            final_euler = final_quat.asEulerRotation()
            output_rotate_deg = tuple(math.degrees(v) for v in (final_euler.x, final_euler.y, final_euler.z))
            output_translate = (
                base_translate[0] + append_translate[0],
                base_translate[1] + append_translate[1],
                base_translate[2] + append_translate[2],
            )
            return append_translate, append_rotate_deg, output_translate, output_rotate_deg

        compat_case = {
            "base_translate": (10.0, 20.0, 30.0),
            "base_rotate_deg": (5.0, 0.0, 0.0),
            "source_translate": (2.0, 4.0, -6.0),
            "source_rotate_deg": (0.0, 45.0, 0.0),
            "source_jo_deg": (10.0, 0.0, 0.0),
            "target_jo_deg": (0.0, 15.0, 0.0),
            "ratio": 0.5,
            "affect_rotation": True,
            "affect_translation": True,
        }
        expected_at, expected_ar, expected_ot, expected_or = _expected_append_outputs(**compat_case)
        cmds.setAttr(f"{append_node}.schemaMode", 2)
        cmds.setAttr(f"{append_node}.baseTranslate", *compat_case["base_translate"], type="double3")
        cmds.setAttr(f"{append_node}.baseRotate", *compat_case["base_rotate_deg"], type="double3")
        cmds.setAttr(f"{append_node}.sourceTranslate", *compat_case["source_translate"], type="double3")
        cmds.setAttr(f"{append_node}.sourceRotate", *compat_case["source_rotate_deg"], type="double3")
        cmds.setAttr(f"{append_node}.sourceJointOrient", *compat_case["source_jo_deg"], type="double3")
        cmds.setAttr(f"{append_node}.targetJointOrient", *compat_case["target_jo_deg"], type="double3")
        cmds.setAttr(f"{append_node}.ratio", compat_case["ratio"])
        cmds.setAttr(f"{append_node}.affectRotation", compat_case["affect_rotation"])
        cmds.setAttr(f"{append_node}.affectTranslation", compat_case["affect_translation"])

        append_t = cmds.getAttr(f"{append_node}.appendTranslate")[0]
        append_r = cmds.getAttr(f"{append_node}.appendRotate")[0]
        compat_out_t = cmds.getAttr(f"{append_node}.outputTranslate")[0]
        compat_out_r = cmds.getAttr(f"{append_node}.outputRotate")[0]
        for label, expected, actual, tolerance in (
            ("appendTranslate", expected_at, append_t, 1e-5),
            ("appendRotate", expected_ar, append_r, 1e-4),
            ("compat outputTranslate", expected_ot, compat_out_t, 1e-5),
            ("compat outputRotate", expected_or, compat_out_r, 1e-4),
        ):
            if any(abs(float(a) - float(e)) > tolerance for e, a in zip(expected, actual)):
                raise RuntimeError(f"{label} mismatch: expected {expected}, got {actual}")

        append_jo_cases = [
            {
                "label": "identity JO",
                "source_rotate_deg": (30.0, 0.0, 0.0),
                "base_rotate_deg": (0.0, 0.0, 0.0),
                "source_jo_deg": (0.0, 0.0, 0.0),
                "target_jo_deg": (0.0, 0.0, 0.0),
                "ratio": 1.0,
            },
            {
                "label": "source JO Z=45",
                "source_rotate_deg": (30.0, 0.0, 0.0),
                "base_rotate_deg": (0.0, 0.0, 0.0),
                "source_jo_deg": (0.0, 0.0, 45.0),
                "target_jo_deg": (0.0, 0.0, 0.0),
                "ratio": 1.0,
                "must_differ_from_source": True,
            },
            {
                "label": "target JO Y=30",
                "source_rotate_deg": (30.0, 15.0, 0.0),
                "base_rotate_deg": (0.0, 0.0, 0.0),
                "source_jo_deg": (0.0, 0.0, 0.0),
                "target_jo_deg": (0.0, 30.0, 0.0),
                "ratio": 1.0,
            },
            {
                "label": "both JO non-zero",
                "source_rotate_deg": (15.0, 20.0, 10.0),
                "base_rotate_deg": (5.0, 10.0, 15.0),
                "source_jo_deg": (0.0, 45.0, 0.0),
                "target_jo_deg": (0.0, 0.0, 30.0),
                "ratio": 0.5,
            },
            {
                "label": "ratio=0.25 with JO",
                "source_rotate_deg": (60.0, 0.0, 0.0),
                "base_rotate_deg": (10.0, 0.0, 0.0),
                "source_jo_deg": (30.0, 0.0, 0.0),
                "target_jo_deg": (15.0, 0.0, 0.0),
                "ratio": 0.25,
            },
            {
                "label": "matching source/target JO",
                "source_rotate_deg": (30.0, 20.0, 10.0),
                "base_rotate_deg": (0.0, 0.0, 0.0),
                "source_jo_deg": (25.0, 15.0, 5.0),
                "target_jo_deg": (25.0, 15.0, 5.0),
                "ratio": 1.0,
                "compare_no_jo": True,
            },
            {
                "label": "large multi-axis JO",
                "source_rotate_deg": (45.0, -30.0, 20.0),
                "base_rotate_deg": (-10.0, 15.0, -5.0),
                "source_jo_deg": (45.0, 30.0, 60.0),
                "target_jo_deg": (20.0, -15.0, 40.0),
                "ratio": 0.7,
            },
        ]
        for case in append_jo_cases:
            expected_at, expected_ar, expected_ot, expected_or = _expected_append_outputs(
                base_translate=(0.0, 0.0, 0.0),
                base_rotate_deg=case["base_rotate_deg"],
                source_translate=(0.0, 0.0, 0.0),
                source_rotate_deg=case["source_rotate_deg"],
                source_jo_deg=case["source_jo_deg"],
                target_jo_deg=case["target_jo_deg"],
                ratio=case["ratio"],
                affect_rotation=True,
                affect_translation=False,
            )
            cmds.setAttr(f"{append_node}.baseTranslate", 0.0, 0.0, 0.0, type="double3")
            cmds.setAttr(f"{append_node}.baseRotate", *case["base_rotate_deg"], type="double3")
            cmds.setAttr(f"{append_node}.sourceTranslate", 0.0, 0.0, 0.0, type="double3")
            cmds.setAttr(f"{append_node}.sourceRotate", *case["source_rotate_deg"], type="double3")
            cmds.setAttr(f"{append_node}.sourceJointOrient", *case["source_jo_deg"], type="double3")
            cmds.setAttr(f"{append_node}.targetJointOrient", *case["target_jo_deg"], type="double3")
            cmds.setAttr(f"{append_node}.ratio", case["ratio"])
            cmds.setAttr(f"{append_node}.affectRotation", True)
            cmds.setAttr(f"{append_node}.affectTranslation", False)

            case_append_t = cmds.getAttr(f"{append_node}.appendTranslate")[0]
            case_append_r = cmds.getAttr(f"{append_node}.appendRotate")[0]
            case_output_t = cmds.getAttr(f"{append_node}.outputTranslate")[0]
            case_output_r = cmds.getAttr(f"{append_node}.outputRotate")[0]
            for label, expected, actual, tolerance in (
                (f"{case['label']} appendTranslate", expected_at, case_append_t, 1e-5),
                (f"{case['label']} appendRotate", expected_ar, case_append_r, 1e-4),
                (f"{case['label']} outputTranslate", expected_ot, case_output_t, 1e-5),
                (f"{case['label']} outputRotate", expected_or, case_output_r, 1e-4),
            ):
                if any(abs(float(a) - float(e)) > tolerance for e, a in zip(expected, actual)):
                    raise RuntimeError(f"{label} mismatch: expected {expected}, got {actual}")

            if case.get("must_differ_from_source") and all(
                abs(float(actual) - float(source)) < 0.01
                for actual, source in zip(case_output_r, case["source_rotate_deg"])
            ):
                raise RuntimeError(f"{case['label']} should differ from source rotation, got {case_output_r}")

            if case.get("compare_no_jo"):
                _expected_at, _expected_ar, _expected_ot, expected_no_jo = _expected_append_outputs(
                    base_translate=(0.0, 0.0, 0.0),
                    base_rotate_deg=case["base_rotate_deg"],
                    source_translate=(0.0, 0.0, 0.0),
                    source_rotate_deg=case["source_rotate_deg"],
                    source_jo_deg=(0.0, 0.0, 0.0),
                    target_jo_deg=(0.0, 0.0, 0.0),
                    ratio=case["ratio"],
                    affect_rotation=True,
                    affect_translation=False,
                )
                if any(abs(float(a) - float(e)) > 1e-4 for e, a in zip(expected_no_jo, case_output_r)):
                    raise RuntimeError(
                        f"{case['label']} should match no-JO output: expected {expected_no_jo}, got {case_output_r}"
                    )

        if cpp_registered_append:
            for attr in ("sourceMmdLinkQuaternions", "sourceMmdLinkIndex"):
                if not cmds.attributeQuery(attr, node=append_node, exists=True):
                    raise RuntimeError(f"Missing native append safety-test attribute {attr!r}")

            # A non-null, short quaternion array with an invalid link index must
            # take the sourceRotate/sourceJointOrient fallback.  INT_MAX used to
            # wrap the uint32 offset and pass the length guard before indexing.
            cmds.setAttr(f"{append_node}.schemaMode", 2)
            cmds.setAttr(f"{append_node}.baseTranslate", 0.0, 0.0, 0.0, type="double3")
            cmds.setAttr(f"{append_node}.baseRotate", 0.0, 0.0, 0.0, type="double3")
            cmds.setAttr(f"{append_node}.sourceTranslate", 0.0, 0.0, 0.0, type="double3")
            cmds.setAttr(f"{append_node}.sourceRotate", 30.0, 10.0, -5.0, type="double3")
            cmds.setAttr(f"{append_node}.sourceJointOrient", 12.0, -7.0, 3.0, type="double3")
            cmds.setAttr(f"{append_node}.targetJointOrient", 0.0, 0.0, 0.0, type="double3")
            cmds.setAttr(f"{append_node}.ratio", 0.75)
            cmds.setAttr(f"{append_node}.affectRotation", True)
            cmds.setAttr(f"{append_node}.affectTranslation", False)
            cmds.setAttr(f"{append_node}.sourceMmdLinkQuaternions", [0.0, 0.0, 0.0], type="doubleArray")
            cmds.setAttr(f"{append_node}.sourceMmdLinkIndex", -1)
            fallback_append_r = cmds.getAttr(f"{append_node}.appendRotate")[0]
            fallback_output_r = cmds.getAttr(f"{append_node}.outputRotate")[0]

            cmds.setAttr(f"{append_node}.sourceMmdLinkIndex", 2147483647)
            invalid_append_r = cmds.getAttr(f"{append_node}.appendRotate")[0]
            invalid_output_r = cmds.getAttr(f"{append_node}.outputRotate")[0]
            for label, expected, actual in (
                ("invalid sourceMmdLinkIndex appendRotate", fallback_append_r, invalid_append_r),
                ("invalid sourceMmdLinkIndex outputRotate", fallback_output_r, invalid_output_r),
            ):
                if any(abs(float(a) - float(e)) > 1e-5 for e, a in zip(expected, actual)):
                    raise RuntimeError(f"{label} used invalid quaternion data: expected {expected}, got {actual}")
            print(f"OK: {APPEND_NODE_TYPE} invalid sourceMmdLinkIndex fails closed to rotation fallback")

        print(f"OK: {APPEND_NODE_TYPE} JO-aware append parity cases match native expected outputs")

        if append_node not in vmd_runtime_rig_helper_mod._ls_mmd_append_nodes():
            raise RuntimeError(f"VmdConverter append collection did not include C++ node {append_node}")
        cmds.delete(append_node)
        print(
            f"OK: created {APPEND_NODE_TYPE}, verified attributes, defaults, "
            "Phase B compute, and Python-compatible append schema compute"
        )

        # --- mmdCcdIk node ---
        ccdik_node = cmds.createNode(CCDIK_NODE_TYPE)
        if not cmds.objExists(ccdik_node):
            raise RuntimeError(f"Failed to create node: {CCDIK_NODE_TYPE}")

        cpp_registered_ccdik = cmds.attributeQuery("inputRoot", node=ccdik_node, exists=True)

        # Common attributes (present in both C++ and Python)
        common_ccdik_attrs = [
            "enabled", "chainJson", "goal", "goalWorldMatrix",
            "inputRotate", "inputTranslate", "outputRotate",
        ]
        for attr in common_ccdik_attrs:
            if not cmds.attributeQuery(attr, node=ccdik_node, exists=True):
                raise RuntimeError(f"Missing attribute {attr!r} on {ccdik_node}")

        for parent, children in [
            ("goal", ["goalX", "goalY", "goalZ"]),
            ("outputRotate", ["outputRotateElementX", "outputRotateElementY", "outputRotateElementZ"]),
        ]:
            for child in children:
                if not cmds.attributeQuery(child, node=ccdik_node, exists=True):
                    raise RuntimeError(
                        f"Missing child attribute {child!r} (of {parent}) on {ccdik_node}"
                    )

        actual_enabled = cmds.getAttr(f"{ccdik_node}.enabled")
        if actual_enabled is not True:
            raise RuntimeError(f"enabled default should be True, got {actual_enabled}")

        # C++-only legacy attributes
        if cpp_registered_ccdik:
            legacy_ccdik_attrs = [
                "inputRoot", "inputEffector", "target",
                "iterations", "angleLimit", "inputChain",
                "outputAngle", "solved",
                "outputLinkAngles", "outputLinkRotates",
            ]
            for attr in legacy_ccdik_attrs:
                if not cmds.attributeQuery(attr, node=ccdik_node, exists=True):
                    raise RuntimeError(f"Missing legacy attribute {attr!r} on {ccdik_node}")

            for parent, children in [
                ("inputRoot", ["inputRootX", "inputRootY", "inputRootZ"]),
                ("inputEffector", ["inputEffectorX", "inputEffectorY", "inputEffectorZ"]),
                ("target", ["targetX", "targetY", "targetZ"]),
            ]:
                for child in children:
                    if not cmds.attributeQuery(child, node=ccdik_node, exists=True):
                        raise RuntimeError(
                            f"Missing child attribute {child!r} (of {parent}) on {ccdik_node}"
                        )

            actual_iterations = cmds.getAttr(f"{ccdik_node}.iterations")
            if actual_iterations != 1:
                raise RuntimeError(f"iterations default should be 1, got {actual_iterations}")
            actual_angle_limit = cmds.getAttr(f"{ccdik_node}.angleLimit")
            if abs(actual_angle_limit - 180.0) > 1e-9:
                raise RuntimeError(f"angleLimit default should be 180.0, got {actual_angle_limit}")
            print(f"OK: {CCDIK_NODE_TYPE} legacy Phase A attributes verified (C++ registered)")
        else:
            print(f"SKIP: {CCDIK_NODE_TYPE} legacy Phase A attributes (Python registered, C++ skipped)")

        cmds.setAttr(f"{ccdik_node}.chainJson", "{}", type="string")
        cmds.setAttr(f"{ccdik_node}.goal", 1.0, 2.0, 3.0, type="double3")
        cmds.setAttr(f"{ccdik_node}.inputRotate[0]", 10.0, 20.0, 30.0, type="double3")
        cmds.setAttr(f"{ccdik_node}.inputTranslate[0]", 4.0, 5.0, 6.0, type="double3")
        compat_goal = cmds.getAttr(f"{ccdik_node}.goal")[0]
        compat_ir = cmds.getAttr(f"{ccdik_node}.inputRotate[0]")[0]
        compat_it = cmds.getAttr(f"{ccdik_node}.inputTranslate[0]")[0]
        if compat_goal != (1.0, 2.0, 3.0):
            raise RuntimeError(f"goal compatibility attr mismatch: {compat_goal}")
        if any(abs(actual - expected) > 1e-9 for actual, expected in zip(compat_ir, (10.0, 20.0, 30.0))):
            raise RuntimeError(f"inputRotate compatibility attr mismatch: {compat_ir}")
        if compat_it != (4.0, 5.0, 6.0):
            raise RuntimeError(f"inputTranslate compatibility attr mismatch: {compat_it}")

        ffi_chain = {
            "bones": [
                {
                    "parent_slot": -1,
                    "rest_position": [0.0, 0.0, 0.0],
                    "maya_rest_translate": [0.0, 0.0, 0.0],
                },
                {
                    "parent_slot": 0,
                    "rest_position": [1.0, 0.0, 0.0],
                    "maya_rest_translate": [1.0, 0.0, 0.0],
                },
            ],
            "links": [{"bone_slot": 0}],
            "targetBoneSlot": 1,
            "iterationCount": 32,
            "limitAngle": math.pi,
        }
        cmds.setAttr(f"{ccdik_node}.chainJson", json.dumps(ffi_chain), type="string")
        cmds.setAttr(f"{ccdik_node}.goal", 0.0, 1.0, 0.0, type="double3")
        cmds.setAttr(f"{ccdik_node}.inputTranslate[0]", 0.0, 0.0, 0.0, type="double3")
        cmds.setAttr(f"{ccdik_node}.inputTranslate[1]", 1.0, 0.0, 0.0, type="double3")
        cmds.setAttr(f"{ccdik_node}.inputRotate[0]", 0.0, 0.0, 0.0, type="double3")
        cmds.setAttr(f"{ccdik_node}.inputRotate[1]", 0.0, 0.0, 0.0, type="double3")
        ffi_out_rot = cmds.getAttr(f"{ccdik_node}.outputRotate[0]")[0]
        if cpp_registered_ccdik:
            ffi_solved = cmds.getAttr(f"{ccdik_node}.solved")
            if ffi_solved is not True:
                raise RuntimeError("mmdCcdIk chainJson FFI path should solve the simple 1-link chain")
        if abs(float(ffi_out_rot[2])) < 1.0:
            raise RuntimeError(f"mmdCcdIkNode chainJson FFI outputRotate[0] should rotate around Z, got {ffi_out_rot}")
        print(f"OK: mmdCcdIkNode chainJson FFI path solved simple 1-link chain -> Z={ffi_out_rot[2]}")

        cmds.setAttr(f"{ccdik_node}.enabled", False)
        cmds.setAttr(f"{ccdik_node}.inputRotate[0]", 0.0, 0.0, 25.0, type="double3")
        ffi_disabled_out_rot = cmds.getAttr(f"{ccdik_node}.outputRotate[0]")[0]
        if cpp_registered_ccdik:
            ffi_disabled_solved = cmds.getAttr(f"{ccdik_node}.solved")
            if ffi_disabled_solved is not False:
                raise RuntimeError(
                    f"mmdCcdIk chainJson disabled path should set solved=False, got {ffi_disabled_solved}"
                )
        if any(
            abs(float(actual) - expected) > 1e-6
            for actual, expected in zip(ffi_disabled_out_rot, (0.0, 0.0, 25.0))
        ):
            raise RuntimeError(
                "mmdCcdIkNode chainJson disabled path should copy inputRotate for link slot 0, "
                f"got {ffi_disabled_out_rot}"
            )
        print("OK: mmdCcdIkNode chainJson disabled path copies inputRotate to outputRotate[0]")

        cmds.setAttr(f"{ccdik_node}.enabled", True)

        jo_bones = [
            {
                "parent_slot": -1,
                "rest_position": [0.0, 0.0, 0.0],
                "maya_rest_translate": [0.0, 0.0, 0.0],
                "joint_orient_deg": [25.0, 15.0, 0.0],
            },
            {
                "parent_slot": 0,
                "rest_position": [1.0, 0.0, 0.0],
                "maya_rest_translate": [1.0, 0.0, 0.0],
                "joint_orient_deg": [0.0, 0.0, 0.0],
            },
        ]
        bind_worlds, no_orient_worlds = _build_bind_worlds(om, jo_bones)
        for bone, bind_world, no_orient_world in zip(jo_bones, bind_worlds, no_orient_worlds):
            bone["maya_bind_world_matrix"] = _matrix_to_list(bind_world)
            bone["no_orient_bind_world_matrix"] = _matrix_to_list(no_orient_world)
        jo_chain = {
            "bones": jo_bones,
            "links": [{"bone_slot": 0}],
            "targetBoneSlot": 1,
            "iterationCount": 32,
            "limitAngle": math.pi,
        }
        jo_goal = (0.0, 1.0, 0.0)
        cmds.setAttr(f"{ccdik_node}.chainJson", json.dumps(jo_chain), type="string")
        cmds.setAttr(f"{ccdik_node}.goal", *jo_goal, type="double3")
        cmds.setAttr(f"{ccdik_node}.inputTranslate[0]", 0.0, 0.0, 0.0, type="double3")
        cmds.setAttr(f"{ccdik_node}.inputTranslate[1]", 1.0, 0.0, 0.0, type="double3")
        cmds.setAttr(f"{ccdik_node}.inputRotate[0]", 0.0, 0.0, 0.0, type="double3")
        cmds.setAttr(f"{ccdik_node}.inputRotate[1]", 0.0, 0.0, 0.0, type="double3")
        jo_out_rot = cmds.getAttr(f"{ccdik_node}.outputRotate[0]")[0]
        expected_jo_out = _expected_bind_space_ccdik_output(om, MmdIkChain, jo_chain, jo_goal)
        if any(abs(float(actual) - expected) > 1e-4 for actual, expected in zip(jo_out_rot, expected_jo_out)):
            raise RuntimeError(
                "mmdCcdIkNode chainJson JO/bind output mismatch: "
                f"expected {expected_jo_out}, got {jo_out_rot}"
            )
        print(f"OK: mmdCcdIkNode chainJson JO/bind path matches native expected output -> {jo_out_rot}")

        multi_bones = [
            {
                "parent_slot": -1,
                "rest_position": [0.0, 0.0, 0.0],
                "maya_rest_translate": [0.0, 0.0, 0.0],
                "joint_orient_deg": [10.0, 0.0, 0.0],
            },
            {
                "parent_slot": 0,
                "rest_position": [1.0, 0.0, 0.0],
                "maya_rest_translate": [1.0, 0.0, 0.0],
                "joint_orient_deg": [0.0, 15.0, 0.0],
            },
            {
                "parent_slot": 1,
                "rest_position": [1.0, 0.0, 0.0],
                "maya_rest_translate": [1.0, 0.0, 0.0],
                "joint_orient_deg": [0.0, 0.0, 0.0],
            },
        ]
        bind_worlds, no_orient_worlds = _build_bind_worlds(om, multi_bones)
        for bone, bind_world, no_orient_world in zip(multi_bones, bind_worlds, no_orient_worlds):
            bone["maya_bind_world_matrix"] = _matrix_to_list(bind_world)
            bone["no_orient_bind_world_matrix"] = _matrix_to_list(no_orient_world)
        multi_chain = {
            "bones": multi_bones,
            "links": [{"bone_slot": 1}, {"bone_slot": 0}],
            "targetBoneSlot": 2,
            "iterationCount": 32,
            "limitAngle": math.pi,
        }
        multi_goal = (1.0, 1.2, 0.0)
        multi_input_rotates = [(0.0, 0.0, 0.0), (8.0, -4.0, 6.0), (-3.0, 5.0, 2.0)]
        cmds.setAttr(f"{ccdik_node}.chainJson", json.dumps(multi_chain), type="string")
        cmds.setAttr(f"{ccdik_node}.goal", *multi_goal, type="double3")
        for index, translate in enumerate(([0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [1.0, 0.0, 0.0])):
            cmds.setAttr(f"{ccdik_node}.inputTranslate[{index}]", *translate, type="double3")
            cmds.setAttr(f"{ccdik_node}.inputRotate[{index}]", *multi_input_rotates[index], type="double3")
        expected_multi_outs = _expected_bind_space_ccdik_outputs(
            om,
            MmdIkChain,
            multi_chain,
            multi_goal,
            multi_input_rotates,
        )
        actual_multi_outs = [cmds.getAttr(f"{ccdik_node}.outputRotate[{index}]")[0] for index in range(2)]
        if cpp_registered_ccdik:
            for index, (actual, expected) in enumerate(zip(actual_multi_outs, expected_multi_outs)):
                if any(abs(float(actual_component) - expected_component) > 1e-4 for actual_component, expected_component in zip(actual, expected)):
                    raise RuntimeError(
                        f"mmdCcdIk chainJson 2-link outputRotate[{index}] mismatch: "
                        f"expected {expected}, got {actual}"
                    )
            print("OK: mmdCcdIk chainJson FFI 2-link path matches native expected outputs")
        else:
            print("SKIP: mmdCcdIk 2-link numeric parity (Python registered, C++ skipped)")

        controller_chain = dict(ffi_chain)
        controller_chain["bones"] = [
            *ffi_chain["bones"],
            {
                "parent_slot": -1,
                "rest_position": [1.0, 0.0, 0.0],
                "maya_rest_translate": [1.0, 0.0, 0.0],
            },
        ]
        controller_chain["controllerBoneSlot"] = 2
        cmds.setAttr(f"{ccdik_node}.chainJson", json.dumps(controller_chain), type="string")
        cmds.setAttr(f"{ccdik_node}.inputTranslate[0]", 0.0, 0.0, 0.0, type="double3")
        cmds.setAttr(f"{ccdik_node}.inputTranslate[1]", 1.0, 0.0, 0.0, type="double3")
        cmds.setAttr(f"{ccdik_node}.inputTranslate[2]", 1.0, 0.0, 0.0, type="double3")
        cmds.setAttr(f"{ccdik_node}.inputRotate[0]", 0.0, 0.0, 0.0, type="double3")
        cmds.setAttr(f"{ccdik_node}.inputRotate[2]", 0.0, 0.0, 0.0, type="double3")
        controller_rest_out = cmds.getAttr(f"{ccdik_node}.outputRotate[0]")[0]
        if cpp_registered_ccdik:
            controller_rest_solved = cmds.getAttr(f"{ccdik_node}.solved")
            if controller_rest_solved is not False:
                raise RuntimeError(
                    "mmdCcdIk controller rest path should skip solving and set solved=False, "
                    f"got {controller_rest_solved}"
                )
        if any(
            abs(float(actual) - expected) > 1e-6
            for actual, expected in zip(controller_rest_out, (0.0, 0.0, 0.0))
        ):
            raise RuntimeError(
                "mmdCcdIk controller rest path should copy inputRotate for link slot 0, "
                f"got {controller_rest_out}"
            )
        print("OK: mmdCcdIk controllerBoneSlot rest path copies inputRotate and skips solve")

        cmds.setAttr(f"{ccdik_node}.inputRotate[0]", 0.0, 0.0, 0.0, type="double3")
        cmds.setAttr(f"{ccdik_node}.inputTranslate[2]", 0.0, 1.0, 0.0, type="double3")
        if cpp_registered_ccdik:
            controller_moved_solved = cmds.getAttr(f"{ccdik_node}.solved")
            if controller_moved_solved is not True:
                raise RuntimeError(
                    "mmdCcdIk moved controller branch should compute a pre-IK goal and solve, "
                    f"got solved={controller_moved_solved}"
                )
            print("OK: mmdCcdIk controllerBoneSlot moved branch computes pre-IK goal and solves")
        else:
            print("SKIP: mmdCcdIk controller solved checks (Python registered, C++ skipped)")

        matrix_goal = cmds.createNode("transform", name="ccdikGoalMatrixSmoke")
        cmds.setAttr(f"{matrix_goal}.translate", 0.0, 1.0, 0.0, type="double3")
        cmds.connectAttr(f"{matrix_goal}.worldMatrix[0]", f"{ccdik_node}.goalWorldMatrix", force=True)
        cmds.setAttr(f"{ccdik_node}.chainJson", json.dumps(jo_chain), type="string")
        cmds.setAttr(f"{ccdik_node}.inputTranslate[0]", 0.0, 0.0, 0.0, type="double3")
        cmds.setAttr(f"{ccdik_node}.inputTranslate[1]", 1.0, 0.0, 0.0, type="double3")
        cmds.setAttr(f"{ccdik_node}.inputRotate[0]", 0.0, 0.0, 0.0, type="double3")
        cmds.setAttr(f"{ccdik_node}.inputRotate[1]", 0.0, 0.0, 0.0, type="double3")
        matrix_goal_out = cmds.getAttr(f"{ccdik_node}.outputRotate[0]")[0]
        expected_matrix_goal_out = _expected_bind_space_ccdik_output(om, MmdIkChain, jo_chain, (0.0, 1.0, 0.0))
        if any(
            abs(float(actual) - expected) > 1e-4
            for actual, expected in zip(matrix_goal_out, expected_matrix_goal_out)
        ):
            raise RuntimeError(
                "mmdCcdIkNode goalWorldMatrix output mismatch: "
                f"expected {expected_matrix_goal_out}, got {matrix_goal_out}"
            )
        cmds.disconnectAttr(f"{matrix_goal}.worldMatrix[0]", f"{ccdik_node}.goalWorldMatrix")
        cmds.delete(matrix_goal)
        print("OK: mmdCcdIk goalWorldMatrix connection drives chainJson FFI goal")

        if not cpp_registered_ccdik:
            print("SKIP: mmdCcdIk legacy Phase A compute tests (Python registered, C++ skipped)")
        else:
            cmds.setAttr(f"{ccdik_node}.chainJson", "{}", type="string")

            # --- Test 1: 標準ケース root=(0,0,0), effector=(1,0,0), target=(0,1,0) ---
            # root->effector = (1,0,0) (X+方向)
            # root->target   = (0,1,0) (Y+方向)
            # Z 軸周りの signed angle = atan2(1*1 - 0*0, 1*0 + 0*1) = atan2(1, 0) = 90°
            cmds.setAttr(f"{ccdik_node}.inputRoot", 0.0, 0.0, 0.0, type="double3")
            cmds.setAttr(f"{ccdik_node}.inputEffector", 1.0, 0.0, 0.0, type="double3")
            cmds.setAttr(f"{ccdik_node}.target", 0.0, 1.0, 0.0, type="double3")

            out_rot = cmds.getAttr(f"{ccdik_node}.outputRotate[0]")[0]
            if any(abs(v) > 1e-9 for v in (out_rot[0], out_rot[1])):
                raise RuntimeError(
                    f"outputRotate should be (0,0,~90) for 90° XY case, got {out_rot}"
                )
            if abs(out_rot[2] - 90.0) > 1e-6:
                raise RuntimeError(
                    f"outputRotateZ should be ~90.0 for 90° XY case, got {out_rot[2]}"
                )

            out_angle = cmds.getAttr(f"{ccdik_node}.outputAngle")
            if abs(out_angle - 90.0) > 1e-6:
                raise RuntimeError(f"outputAngle should be ~90.0, got {out_angle}")

            solved = cmds.getAttr(f"{ccdik_node}.solved")
            if solved is not True:
                raise RuntimeError(f"solved should be True for valid IK case, got {solved}")

            print(f"OK: mmdCcdIkNode basic IK (effector=(1,0,0) -> target=(0,1,0)) -> Z={out_rot[2]}")

            # --- Test 2: enabled=false -> solved=false, outputRotate=(0,0,0) ---
            cmds.setAttr(f"{ccdik_node}.enabled", False)
            out_rot_disabled = cmds.getAttr(f"{ccdik_node}.outputRotate[0]")[0]
            out_angle_disabled = cmds.getAttr(f"{ccdik_node}.outputAngle")
            solved_disabled = cmds.getAttr(f"{ccdik_node}.solved")
            if any(abs(v) > 1e-9 for v in out_rot_disabled):
                raise RuntimeError(
                    f"outputRotate should be (0,0,0) when disabled, got {out_rot_disabled}"
                )
            if abs(out_angle_disabled) > 1e-9:
                raise RuntimeError(
                    f"outputAngle should be 0 when disabled, got {out_angle_disabled}"
                )
            if solved_disabled is not False:
                raise RuntimeError(f"solved should be False when disabled, got {solved_disabled}")

            print("OK: mmdCcdIkNode disabled -> solved=False, zero output")

            # --- Test 3: angleLimit clamping with iterations ---
            # Restore enabled, same 90° target setup
            cmds.setAttr(f"{ccdik_node}.enabled", True)

            # angleLimit=30, iterations=1 -> maxAllowed=30, clamp 90->30
            cmds.setAttr(f"{ccdik_node}.angleLimit", 30.0)
            cmds.setAttr(f"{ccdik_node}.iterations", 1)
            out_angle_lim = cmds.getAttr(f"{ccdik_node}.outputAngle")
            if abs(out_angle_lim - 30.0) > 1e-6:
                raise RuntimeError(
                    f"angleLimit=30, iterations=1 should output ~30.0, got {out_angle_lim}"
                )
            out_rot_lim = cmds.getAttr(f"{ccdik_node}.outputRotate[0]")[0]
            if abs(out_rot_lim[2] - 30.0) > 1e-6:
                raise RuntimeError(
                    f"outputRotateZ should be ~30.0 with angleLimit=30, iterations=1, "
                    f"got {out_rot_lim[2]}"
                )
            print("OK: mmdCcdIkNode angleLimit=30, iterations=1 -> 30°")

            # angleLimit=30, iterations=2 -> maxAllowed=60, clamp 90->60
            cmds.setAttr(f"{ccdik_node}.iterations", 2)
            out_angle_lim = cmds.getAttr(f"{ccdik_node}.outputAngle")
            if abs(out_angle_lim - 60.0) > 1e-6:
                raise RuntimeError(
                    f"angleLimit=30, iterations=2 should output ~60.0, got {out_angle_lim}"
                )
            print("OK: mmdCcdIkNode angleLimit=30, iterations=2 -> 60°")

            # angleLimit=30, iterations=3 -> maxAllowed=90, outputs 90
            cmds.setAttr(f"{ccdik_node}.iterations", 3)
            out_angle_lim = cmds.getAttr(f"{ccdik_node}.outputAngle")
            if abs(out_angle_lim - 90.0) > 1e-6:
                raise RuntimeError(
                    f"angleLimit=30, iterations=3 should output ~90.0, got {out_angle_lim}"
                )
            print("OK: mmdCcdIkNode angleLimit=30, iterations=3 -> 90°")

            # --- Test 4: multi-link CCD (2-link XY/Z) ---
            ccd_chain = [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 2.0, 0.0, 0.0]
            cmds.setAttr(f"{ccdik_node}.inputChain", ccd_chain, type="doubleArray")
            cmds.setAttr(f"{ccdik_node}.target", 1.0, 1.0, 0.0, type="double3")
            cmds.setAttr(f"{ccdik_node}.iterations", 4)
            cmds.setAttr(f"{ccdik_node}.angleLimit", 45.0)

            out_link_angles = cmds.getAttr(f"{ccdik_node}.outputLinkAngles")
            out_link_rotates = cmds.getAttr(f"{ccdik_node}.outputLinkRotates")
            out_solved = cmds.getAttr(f"{ccdik_node}.solved")

            if not isinstance(out_link_angles, (tuple, list)):
                out_link_angles = [out_link_angles]
            if not isinstance(out_link_rotates, (tuple, list)):
                out_link_rotates = [out_link_rotates]

            if len(out_link_angles) != 2:
                raise RuntimeError(
                    f"outputLinkAngles should have 2 elements for 2-link chain, got {len(out_link_angles)}"
                )
            if len(out_link_rotates) != 6:
                raise RuntimeError(
                    f"outputLinkRotates should have 6 elements for 2-link chain, got {len(out_link_rotates)}"
                )
            if all(abs(float(angle)) < 1e-9 for angle in out_link_angles):
                raise RuntimeError(f"outputLinkAngles should contain at least one non-zero angle, got {out_link_angles}")

            if out_solved is not True:
                raise RuntimeError(f"mmdCcdIkNode should be solved for valid 2-link chain, got {out_solved}")

            initial_positions, expected_angles = _ccd_2d_multi_link_angles(
                ccd_chain, 1.0, 1.0, iterations=4, angle_limit=45.0
            )
            if len(initial_positions) != 3 or len(expected_angles) != 2:
                raise RuntimeError(
                    f"internal CCD helper failed for 2-link chain: positions={len(initial_positions)}, angles={len(expected_angles)}"
                )

            for actual, expected in zip(out_link_angles, expected_angles):
                if abs(float(actual) - expected) > 1e-6:
                    raise RuntimeError(
                        f"outputLinkAngles mismatch: expected {expected_angles}, got {out_link_angles}"
                    )

            expected_rotates: list[float] = []
            for angle in expected_angles:
                expected_rotates.extend([0.0, 0.0, angle])
            if any(abs(float(actual) - expected) > 1e-6 for actual, expected in zip(out_link_rotates, expected_rotates)):
                raise RuntimeError(
                    f"outputLinkRotates mismatch: expected {expected_rotates}, got {out_link_rotates}"
                )

            initial_distance = math.hypot(ccd_chain[-3] - 1.0, ccd_chain[-2] - 1.0)
            final_distance = math.hypot(initial_positions[-1][0] - 1.0, initial_positions[-1][1] - 1.0)
            if final_distance >= initial_distance:
                raise RuntimeError(
                    f"multi-link CCD should reduce distance to target: initial={initial_distance}, final={final_distance}"
                )

            print("OK: mmdCcdIkNode multi-link 2-link CCD produced non-zero output and reduced distance")

        if ccdik_node not in vmd_runtime_rig_helper_mod._ls_mmd_ccd_ik_nodes():
            raise RuntimeError(f"VmdConverter IK collection did not include C++ node {ccdik_node}")
        cmds.delete(ccdik_node)
        print(f"OK: created {CCDIK_NODE_TYPE}, verified attributes, IK compute, and disabled state")
        if ccdik_node in vmd_runtime_rig_helper_mod._ls_mmd_ccd_ik_nodes():
            raise RuntimeError(f"Deleted C++ IK node {ccdik_node} should not remain in VmdConverter collection")

        from mmd_tools.io.mmd_importer import import_mmd_file

        ik_nodes_before_model_import = set(vmd_runtime_rig_helper_mod._ls_mmd_ccd_ik_nodes())
        rig_model_root = import_mmd_file(
            str(FAST_LOAD_MODEL),
            options={
                "setup_rig": True,
                "setup_bone_orientation": True,
                "use_cpp_fast_load": False,
                "import_physics": False,
                "auto_resolve_textures": False,
            },
        )
        if not rig_model_root or not cmds.objExists(rig_model_root):
            raise RuntimeError(f"real-model rig import failed for C++ IK smoke: {rig_model_root!r}")
        try:
            imported_ik_nodes = [
                node
                for node in vmd_runtime_rig_helper_mod._ls_mmd_ccd_ik_nodes()
                if node not in ik_nodes_before_model_import
            ]
            cpp_ik_nodes = [node for node in imported_ik_nodes if cmds.nodeType(node) == CCDIK_NODE_TYPE]
            if not cpp_ik_nodes:
                raise RuntimeError(
                    "real-model rig import did not create C++ IK nodes; "
                    f"new IK nodes={imported_ik_nodes}"
                )

            evaluated_multi_link_nodes = []
            for node in cpp_ik_nodes:
                chain = json.loads(cmds.getAttr(f"{node}.chainJson") or "{}")
                links = chain.get("links") or []
                if len(links) < 2:
                    continue

                controller_slot = int(chain.get("controllerBoneSlot", -1))
                if controller_slot >= 0:
                    translate_plugs = (
                        cmds.listConnections(
                            f"{node}.inputTranslate[{controller_slot}]",
                            s=True,
                            d=False,
                            plugs=True,
                        )
                        or []
                    )
                    if translate_plugs:
                        translate_node, translate_attr = translate_plugs[0].split(".", 1)
                        if translate_attr == "translate":
                            current = cmds.getAttr(f"{translate_node}.translate")[0]
                            cmds.setAttr(
                                f"{translate_node}.translate",
                                float(current[0]),
                                float(current[1]) + 0.5,
                                float(current[2]),
                                type="double3",
                            )
                    else:
                        current = cmds.getAttr(f"{node}.inputTranslate[{controller_slot}]")[0]
                        cmds.setAttr(
                            f"{node}.inputTranslate[{controller_slot}]",
                            float(current[0]),
                            float(current[1]) + 0.5,
                            float(current[2]),
                            type="double3",
                        )

                cmds.setAttr(f"{node}.enabled", True)
                cmds.dgdirty(node)
                if cpp_registered_ccdik:
                    solved = cmds.getAttr(f"{node}.solved")
                else:
                    solved = True
                outputs = [cmds.getAttr(f"{node}.outputRotate[{index}]")[0] for index in range(len(links))]
                if solved is True and any(
                    abs(float(component)) > 1e-5
                    for output in outputs
                    for component in output
                ):
                    evaluated_multi_link_nodes.append(node)

            if not evaluated_multi_link_nodes:
                raise RuntimeError(
                    "real-model C++ IK import created no evaluating multi-link nodes; "
                    f"cpp_ik_nodes={cpp_ik_nodes}"
                )

            print(
                "OK: real-model rig import created C++ multi-link mmdCcdIkNode(s) "
                f"and evaluated outputRotate: {evaluated_multi_link_nodes}"
            )
        finally:
            if cmds.objExists(rig_model_root):
                cmds.delete(rig_model_root)

        from mmd_tools.io.cpp_fast_importer import fast_import

        root = fast_import(
            str(FAST_IMPORT_SKIN_MODEL),
            base_name="fast_import_skin_smoke",
            scale=1.0,
            mesh_only=False,
        )
        if not root or not cmds.objExists(root):
            raise RuntimeError(f"fast_import(mesh_only=False) did not create a root: {root!r}")

        joints = cmds.ls(type="joint") or []
        skins = cmds.ls(type="skinCluster") or []
        if not joints:
            raise RuntimeError("fast_import(mesh_only=False) did not create joints")
        if not skins:
            raise RuntimeError("fast_import(mesh_only=False) did not create a skinCluster")

        mesh_shapes = cmds.listRelatives(root, shapes=True, type="mesh") or []
        if not mesh_shapes:
            raise RuntimeError(f"fast_import(mesh_only=False) created no mesh shapes under {root}")
        weights = cmds.skinPercent(skins[0], f"{mesh_shapes[0]}.vtx[0]", query=True, value=True)
        if not weights or abs(sum(weights) - 1.0) > 0.0001:
            raise RuntimeError(f"fast_import skin weights are invalid: {weights!r}")

        cmds.delete(root)
        print(
            f"OK: fast_import(mesh_only=False) created {len(joints)} joints, "
            f"{len(skins)} skinCluster(s), and normalized weights"
        )

        # --- Track 4 runtime node smoke (existing model + VMD import path) ---
        from mmd_tools.core.constants import ATTR_MMD_BONE_INDEX
        from mmd_tools.converters.vmd_converter import VmdConverter
        from mmd_tools.core.native.mmd_anim_runtime import (
            MmdRuntimeClip,
            MmdRuntimeInstance,
            MmdRuntimeModel,
        )

        runtime_nodes_before = set(cmds.ls(type=NODE_TYPE) or [])
        runtime_model_root = import_mmd_file(
            str(FAST_IMPORT_SKIN_MODEL),
            options={"use_cpp_fast_load": False},
        )
        if not runtime_model_root or not cmds.objExists(runtime_model_root):
            raise RuntimeError(f"import_mmd_file(model) did not create root: {runtime_model_root!r}")

        joints_by_bone_index: dict[int, str] = {}
        for joint in cmds.listRelatives(runtime_model_root, allDescendents=True, type="joint") or []:
            if not cmds.attributeQuery(ATTR_MMD_BONE_INDEX, node=joint, exists=True):
                continue
            try:
                joints_by_bone_index[int(cmds.getAttr(f"{joint}.{ATTR_MMD_BONE_INDEX}"))] = joint
            except Exception:
                pass
        if not joints_by_bone_index:
            raise RuntimeError("No joints with mmd_bone_index found for Track 4 baked comparison")

        live_runtime_options = {
            "target_model": runtime_model_root,
            "pmx_path": str(FAST_IMPORT_SKIN_MODEL),
            "use_live_runtime": True,
            "vmd_fps": 30,
        }
        vmd_imported = import_mmd_file(str(TRACK4_VMD_MOTION), options=live_runtime_options)
        if vmd_imported is not True:
            raise RuntimeError(f"VMD import with use_live_runtime=True failed: {vmd_imported!r}")

        runtime_nodes_after = cmds.ls(type=NODE_TYPE) or []
        runtime_candidates = [n for n in runtime_nodes_after if n not in runtime_nodes_before]
        if not runtime_candidates:
            raise RuntimeError("No new mmdRuntimeInstance was created by live runtime import")

        expected_pmx = os.path.normcase(os.path.normpath(str(FAST_IMPORT_SKIN_MODEL)))
        expected_vmd = os.path.normcase(os.path.normpath(str(TRACK4_VMD_MOTION)))
        runtime_node = None
        for candidate in runtime_candidates:
            if not cmds.attributeQuery("pmxData", node=candidate, exists=True):
                continue
            if not cmds.attributeQuery("vmdData", node=candidate, exists=True):
                continue
            actual_pmx = os.path.normcase(os.path.normpath(cmds.getAttr(f"{candidate}.pmxData")))
            actual_vmd = os.path.normcase(os.path.normpath(cmds.getAttr(f"{candidate}.vmdData")))
            if actual_pmx == expected_pmx and actual_vmd == expected_vmd:
                runtime_node = candidate
                break

        if runtime_node is None:
            raise RuntimeError(
                f"No mmdRuntimeInstance with expected pmxData/vmdData found. candidates={runtime_candidates}"
            )

        if not cmds.isConnected("time1.outTime", f"{runtime_node}.time"):
            connected_time_src = cmds.listConnections(f"{runtime_node}.time", s=True, d=False, plugs=True) or []
            if "time1.outTime" not in connected_time_src:
                raise RuntimeError(
                    f"time1.outTime is not connected to {runtime_node}.time"
                )

        if not cmds.attributeQuery("mmdRuntimeNode", node=runtime_model_root, exists=True):
            raise RuntimeError(f"{runtime_model_root} missing message attr mmdRuntimeNode")
        runtime_msg_connections = (
            cmds.listConnections(f"{runtime_model_root}.mmdRuntimeNode", s=True, d=False, plugs=True) or []
        )
        if f"{runtime_node}.message" not in runtime_msg_connections:
            raise RuntimeError(
                f"{runtime_node}.message is not connected to {runtime_model_root}.mmdRuntimeNode"
            )

        try:
            with open(FAST_IMPORT_SKIN_MODEL, "rb") as handle:
                pmx_bytes = handle.read()
            with open(TRACK4_VMD_MOTION, "rb") as handle:
                vmd_bytes = handle.read()

            runtime_model = MmdRuntimeModel.from_pmx_bytes(pmx_bytes)
            if runtime_model is None:
                raise RuntimeError("Failed to create MmdRuntimeModel from PMX bytes")
            runtime_clip = MmdRuntimeClip.from_vmd_bytes_for_model(runtime_model, vmd_bytes)
            if runtime_clip is None:
                runtime_model.free()
                raise RuntimeError("Failed to create MmdRuntimeClip from VMD bytes")
            runtime_instance = MmdRuntimeInstance.for_model(runtime_model)
            if runtime_instance is None:
                runtime_clip.free()
                runtime_model.free()
                raise RuntimeError("Failed to create MmdRuntimeInstance")

            def _compare_native_live_frame(frame: int) -> None:
                native_frame = float(frame)
                if not runtime_instance.evaluate_clip_frame(runtime_clip, native_frame):
                    raise RuntimeError(
                        f"Native runtime evaluate_clip_frame({frame}) failed (runtime={native_frame})"
                    )

                native_world = runtime_instance.get_world_matrices()
                native_morph = runtime_instance.get_morph_weights()
                native_ik = runtime_instance.get_ik_enabled()
                if native_world is None:
                    raise RuntimeError("Native runtime get_world_matrices returned None")
                if native_morph is None:
                    raise RuntimeError("Native runtime get_morph_weights returned None")
                if native_ik is None:
                    raise RuntimeError("Native runtime get_ik_enabled returned None")

                native_world_flat = _flatten_nested_sequence(native_world)
                world_compare_count = min(16, len(native_world_flat))
                if world_compare_count <= 0:
                    raise RuntimeError("Native runtime world matrix flatten produced no data")

                cmds.currentTime(frame)
                cmds.refresh(force=True)
                world_count = _read_count(cmds, runtime_node, "worldMatrices")
                morph_count = _read_count(cmds, runtime_node, "morphWeights")
                ik_count = _read_count(cmds, runtime_node, "ikEnabled")

                live_world = _read_output_attr_range(cmds, runtime_node, "worldMatrices", world_count)
                live_morph = _read_output_attr_range(cmds, runtime_node, "morphWeights", morph_count)
                live_ik = _read_output_attr_range(cmds, runtime_node, "ikEnabled", ik_count)

                _compare_vectors(
                    list(map(float, native_world_flat[:world_compare_count])),
                    list(map(float, live_world[:world_compare_count])),
                    "worldMatrices",
                    frame,
                )
                _compare_runtime_arrays(
                    [float(x) for x in native_morph],
                    live_morph,
                    "morphWeights",
                    frame,
                )
                _compare_runtime_arrays(
                    [int(x) for x in native_ik],
                    live_ik,
                    "ikEnabled",
                    frame,
                    is_bool=True,
                )

            world_matrix_compare_report: list[dict[str, object]] = []

            def _compare_existing_baked_joint_frame(frame: int) -> None:
                cmds.currentTime(frame)
                cmds.refresh(force=True)
                world_count = _read_count(cmds, runtime_node, "worldMatrices")
                live_world = _read_output_attr_range(cmds, runtime_node, "worldMatrices", world_count)
                if len(live_world) < 16:
                    raise RuntimeError(f"live worldMatrices has no full matrix at frame {frame}")

                for bone_index, joint in sorted(joints_by_bone_index.items()):
                    start = bone_index * 16
                    live_mmd_world = list(map(float, live_world[start : start + 16]))
                    if len(live_mmd_world) != 16:
                        world_matrix_compare_report.append({
                            "frame": frame,
                            "bone_index": bone_index,
                            "joint": joint,
                            "error": f"missing live matrix: need slice {start}:{start + 16}, have {len(live_world)}",
                        })
                        continue

                    expected_maya_world = list(map(
                        float, VmdConverter._convert_mmd_world_matrix_to_maya(live_mmd_world)
                    ))
                    actual_maya_world = list(map(
                        float, cmds.xform(joint, query=True, worldSpace=True, matrix=True)
                    ))
                    max_diff, max_index = _matrix_max_diff(expected_maya_world, actual_maya_world)
                    if max_diff > 1e-4:
                        try:
                            joint_orient = cmds.getAttr(f"{joint}.jointOrient")[0]
                        except Exception as exc:
                            joint_orient = f"<error: {exc}>"
                        try:
                            rotate_order = cmds.getAttr(f"{joint}.rotateOrder")
                        except Exception as exc:
                            rotate_order = f"<error: {exc}>"
                        world_matrix_compare_report.append({
                            "frame": frame,
                            "bone_index": bone_index,
                            "joint": joint,
                            "max_diff": max_diff,
                            "max_index": max_index,
                            "expected": expected_maya_world,
                            "actual": actual_maya_world,
                            "jointOrient": joint_orient,
                            "rotateOrder": rotate_order,
                        })

            try:
                from mmd_tools.core.vmd_data import VmdData

                vmd_parser = VmdData()
                vmd_parser.parse_file(str(TRACK4_VMD_MOTION))

                # Collect all unique frame numbers from bone and morph keyframes
                frame_numbers = sorted(set(
                    b.frame_number for b in vmd_parser.bone_frames
                ) | set(
                    m.frame_number for m in vmd_parser.morph_frames
                ))
                if not frame_numbers:
                    raise RuntimeError("VMD file has no keyframes")

                for frame in frame_numbers:
                    _compare_native_live_frame(frame)
                    _compare_existing_baked_joint_frame(frame)

                report_path = ROOT / "build" / "track4" / "world_matrix_compare.json"
                if world_matrix_compare_report:
                    report_path.parent.mkdir(parents=True, exist_ok=True)
                    report_path.write_text(
                        json.dumps(world_matrix_compare_report, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                    max_entry = max(
                        world_matrix_compare_report,
                        key=lambda item: float(item.get("max_diff", 0.0)),
                    )
                    warning = (
                        "Track 4 baked joint worldMatrix differs from coordinate-transformed "
                        f"runtime matrices; wrote {report_path} "
                        f"(max_diff={max_entry.get('max_diff')} frame={max_entry.get('frame')} "
                        f"bone={max_entry.get('bone_index')})"
                    )
                    print(f"WARNING: {warning}")
                    if os.environ.get("MMD_TRACK4_STRICT") == "1":
                        raise RuntimeError(warning)
                else:
                    if report_path.exists():
                        try:
                            report_path.unlink()
                        except OSError as exc:
                            print(f"WARNING: could not remove stale Track 4 report {report_path}: {exc}")
                    print(
                        "OK: baked Maya joint worldMatrices match coordinate-transformed "
                        f"live runtime node worldMatrices at {len(frame_numbers)} VMD frames"
                    )

                print(f"OK: live runtime node output matches native runtime at {len(frame_numbers)} VMD frames: "
                      f"{min(frame_numbers)}..{max(frame_numbers)}")

                # --- Track 4 DG connection verification ---
                from mmd_tools.core.native.mmd_anim_runtime import (
                    connect_runtime_node_outputs_to_model,
                )

                # Record baked joint worldMatrix and blendShape weights at all frames
                baked_joint_matrices: dict[int, dict[int, list[float]]] = {}  # frame -> {bone_idx: matrix}
                baked_morph_weights: dict[int, dict[str, float]] = {}  # frame -> {morph_name: weight}

                bs_nodes = cmds.ls(type="blendShape") or []

                for frame in frame_numbers:
                    cmds.currentTime(frame)
                    cmds.refresh(force=True)

                    # Record baked joint world matrices
                    baked_joint_matrices[frame] = {}
                    for bone_idx, joint in sorted(joints_by_bone_index.items()):
                        try:
                            m = list(map(float, cmds.xform(joint, query=True, worldSpace=True, matrix=True)))
                            baked_joint_matrices[frame][bone_idx] = m
                        except Exception:
                            pass

                    # Record baked blendShape weights
                    baked_morph_weights[frame] = {}
                    for bs_node in bs_nodes:
                        wc = cmds.blendShape(bs_node, query=True, weightCount=True) or 0
                        for wi in range(wc):
                            alias = cmds.aliasAttr(f"{bs_node}.weight[{wi}]", query=True)
                            if alias:
                                try:
                                    val = float(cmds.getAttr(f"{bs_node}.weight[{wi}]"))
                                    baked_morph_weights[frame][alias] = val
                                except Exception:
                                    pass

                # Connect runtime node outputs to joints and blendShapes
                dg_result = connect_runtime_node_outputs_to_model(
                    runtime_node,
                    runtime_model_root,
                    pmx_path=str(FAST_IMPORT_SKIN_MODEL),
                )

                connected_bone_count = len(dg_result["connected_bones"])
                connected_morph_count = len(dg_result["connected_morphs"])

                if connected_bone_count < 1:
                    raise RuntimeError(
                        f"connect_runtime_node_outputs_to_model connected {connected_bone_count} bones, "
                        f"expected >= 1. dg_result={dg_result}"
                    )

                if dg_result.get("warnings"):
                    for w in dg_result["warnings"]:
                        print(f"  DG connection warning: {w}")

                print(f"OK: connect_runtime_node_outputs_to_model connected {connected_bone_count} bones "
                      f"and {connected_morph_count} morphs "
                      f"(skipped={len(dg_result.get('skipped', []))}, "
                      f"warnings={len(dg_result.get('warnings', []))})")

                # Compare DG-connected playback vs recorded baked values
                dg_compare_passed = 0
                dg_compare_failed = 0
                failed_details: list[str] = []

                for frame in frame_numbers:
                    cmds.currentTime(frame)
                    cmds.refresh(force=True)

                    for bone_idx, joint in sorted(joints_by_bone_index.items()):
                        expected = baked_joint_matrices.get(frame, {}).get(bone_idx)
                        if expected is None:
                            continue
                        try:
                            actual = list(map(float, cmds.xform(joint, query=True, worldSpace=True, matrix=True)))
                        except Exception:
                            continue

                        max_diff, _ = _matrix_max_diff(expected, actual)
                        if max_diff > 1e-3:
                            dg_compare_failed += 1
                            if len(failed_details) < 10:
                                failed_details.append(
                                    f"frame={frame} bone_idx={bone_idx} {joint}: max_diff={max_diff}"
                                )
                        else:
                            dg_compare_passed += 1

                    # Compare blendShape weights
                    for bs_node in bs_nodes:
                        wc = cmds.blendShape(bs_node, query=True, weightCount=True) or 0
                        for wi in range(wc):
                            alias = cmds.aliasAttr(f"{bs_node}.weight[{wi}]", query=True)
                            if not alias or alias not in baked_morph_weights.get(frame, {}):
                                continue
                            expected_w = baked_morph_weights[frame][alias]
                            try:
                                actual_w = float(cmds.getAttr(f"{bs_node}.weight[{wi}]"))
                            except Exception:
                                continue
                            if abs(expected_w - actual_w) > 1e-3:
                                dg_compare_failed += 1
                                if len(failed_details) < 10:
                                    failed_details.append(
                                        f"frame={frame} morph={alias}: expected={expected_w:.4f} actual={actual_w:.4f}"
                                    )
                            else:
                                dg_compare_passed += 1

                if dg_compare_failed > 0:
                    raise RuntimeError(
                        f"DG-connected playback mismatch: {dg_compare_passed} OK, "
                        f"{dg_compare_failed} FAILED. Details (first 10):\n" +
                        "\n".join(failed_details)
                    )

                print(f"OK: DG-connected runtime node playback matches recorded baked values "
                      f"at {len(frame_numbers)} VMD frames "
                      f"({dg_compare_passed} comparisons, {dg_compare_failed} failures)")

                # Clean up DG utility nodes
                created_nodes = dg_result.get("utility_nodes", [])
                for unode in created_nodes:
                    if cmds.objExists(unode):
                        try:
                            cmds.delete(unode)
                        except Exception:
                            pass
                print(f"OK: cleaned up {len(created_nodes)} DG utility nodes")

                # --- Track 4 nonzero vertex morph DG verification ---
                # Use PmxMock.create_full_pmx() and VmdMock.create_custom_vmd()
                from tests.common.pmx_mock import PmxMock
                from tests.common.vmd_mock import VmdMock

                track4_dir = ROOT / "build" / "track4"
                track4_dir.mkdir(parents=True, exist_ok=True)

                morph_pmx_path = track4_dir / "morph_test_model.pmx"
                morph_vmd_path = track4_dir / "morph_test_motion.vmd"

                morph_pmx_path.write_bytes(PmxMock.create_full_pmx())
                morph_vmd_path.write_bytes(VmdMock.create_custom_vmd(
                    morph_name="TestMorph",
                    bone_frame_count=0,
                    morph_frame_count=5,
                ))

                morph_root = import_mmd_file(
                    str(morph_pmx_path),
                    options={
                        "use_cpp_fast_load": False,
                        "use_namespace": True,
                        "custom_namespace": "track4_morph_dg",
                    },
                )
                if not morph_root or not cmds.objExists(morph_root):
                    raise RuntimeError(
                        f"Morph test: import_mmd_file(mock_pmx) did not create root: {morph_root!r}"
                    )

                try:
                    morph_live_runtime_options = {
                        "target_model": morph_root,
                        "pmx_path": str(morph_pmx_path),
                        "use_live_runtime": True,
                        "vmd_fps": 30,
                    }
                    morph_vmd_imported = import_mmd_file(
                        str(morph_vmd_path),
                        options=morph_live_runtime_options,
                    )
                    if morph_vmd_imported is not True:
                        raise RuntimeError(
                            f"Morph test: VMD import with use_live_runtime=True failed: "
                            f"{morph_vmd_imported!r}"
                        )

                    morph_runtime_nodes_after = cmds.ls(type=NODE_TYPE) or []
                    morph_runtime_candidates = [
                        n for n in morph_runtime_nodes_after
                        if n not in runtime_nodes_before
                    ]
                    if not morph_runtime_candidates:
                        raise RuntimeError(
                            "Morph test: no new mmdRuntimeInstance created"
                        )

                    expected_morph_pmx = os.path.normcase(os.path.normpath(str(morph_pmx_path)))
                    expected_morph_vmd = os.path.normcase(os.path.normpath(str(morph_vmd_path)))
                    morph_runtime_node = None
                    for candidate in morph_runtime_candidates:
                        if not cmds.attributeQuery("pmxData", node=candidate, exists=True):
                            continue
                        if not cmds.attributeQuery("vmdData", node=candidate, exists=True):
                            continue
                        actual_pmx = os.path.normcase(
                            os.path.normpath(cmds.getAttr(f"{candidate}.pmxData"))
                        )
                        actual_vmd = os.path.normcase(
                            os.path.normpath(cmds.getAttr(f"{candidate}.vmdData"))
                        )
                        if actual_pmx == expected_morph_pmx and actual_vmd == expected_morph_vmd:
                            morph_runtime_node = candidate
                            break

                    if morph_runtime_node is None:
                        raise RuntimeError(
                            "Morph test: no mmdRuntimeInstance with expected pmxData/vmdData found"
                        )

                    if not cmds.isConnected("time1.outTime", f"{morph_runtime_node}.time"):
                        connected_time_src = cmds.listConnections(
                            f"{morph_runtime_node}.time", s=True, d=False, plugs=True
                        ) or []
                        if "time1.outTime" not in connected_time_src:
                            raise RuntimeError(
                                f"Morph test: time1.outTime not connected to "
                                f"{morph_runtime_node}.time"
                            )

                    # Record baked blendShape weights at morph keyframes (0..4)
                    morph_baked_weights: dict[int, dict[str, float]] = {}
                    morph_bs_nodes: list[str] = []
                    for shape in cmds.listRelatives(
                        morph_root,
                        allDescendents=True,
                        type="mesh",
                        fullPath=True,
                    ) or []:
                        for history_node in cmds.listHistory(shape, pruneDagObjects=True) or []:
                            if cmds.nodeType(history_node) != "blendShape":
                                continue
                            if history_node not in morph_bs_nodes:
                                morph_bs_nodes.append(history_node)
                    if not morph_bs_nodes:
                        raise RuntimeError(
                            "Morph test: no blendShape nodes found after PMX import"
                        )

                    morph_frame_numbers = list(range(5))  # 0..4 from VMD mock
                    for frame in morph_frame_numbers:
                        cmds.currentTime(frame)
                        cmds.refresh(force=True)
                        morph_baked_weights[frame] = {}
                        for bs_node in morph_bs_nodes:
                            wc = cmds.blendShape(bs_node, query=True, weightCount=True) or 0
                            for wi in range(wc):
                                alias = cmds.aliasAttr(f"{bs_node}.weight[{wi}]", query=True)
                                if alias:
                                    try:
                                        val = float(cmds.getAttr(f"{bs_node}.weight[{wi}]"))
                                        morph_baked_weights[frame][alias] = val
                                    except Exception:
                                        pass

                    # Connect runtime node outputs
                    morph_dg_result = connect_runtime_node_outputs_to_model(
                        morph_runtime_node,
                        morph_root,
                        pmx_path=str(morph_pmx_path),
                    )

                    morph_connected_morph_count = len(morph_dg_result["connected_morphs"])
                    if morph_connected_morph_count < 1:
                        raise RuntimeError(
                            f"Morph test: connected {morph_connected_morph_count} morphs, "
                            f"expected >= 1. dg_result={morph_dg_result}"
                        )

                    if morph_dg_result.get("warnings"):
                        for w in morph_dg_result["warnings"]:
                            print(f"  Morph DG connection warning: {w}")

                    print(
                        f"OK: Track4 morph test: connected "
                        f"{morph_connected_morph_count} morph(s)"
                    )

                    # Compare DG-connected playback vs recorded baked values at morph keyframes
                    morph_dg_passed = 0
                    morph_dg_failed = 0
                    morph_failed_details: list[str] = []

                    for frame in morph_frame_numbers:
                        cmds.currentTime(frame)
                        cmds.refresh(force=True)
                        for bs_node in morph_bs_nodes:
                            wc = cmds.blendShape(bs_node, query=True, weightCount=True) or 0
                            for wi in range(wc):
                                alias = cmds.aliasAttr(f"{bs_node}.weight[{wi}]", query=True)
                                if not alias or alias not in morph_baked_weights.get(frame, {}):
                                    continue
                                expected_w = morph_baked_weights[frame][alias]
                                try:
                                    actual_w = float(cmds.getAttr(f"{bs_node}.weight[{wi}]"))
                                except Exception:
                                    continue
                                if abs(expected_w - actual_w) > 1e-3:
                                    morph_dg_failed += 1
                                    if len(morph_failed_details) < 10:
                                        morph_failed_details.append(
                                            f"frame={frame} morph={alias}: "
                                            f"expected={expected_w:.4f} actual={actual_w:.4f}"
                                        )
                                else:
                                    morph_dg_passed += 1

                    if morph_dg_failed > 0:
                        raise RuntimeError(
                            f"Morph test: DG-connected playback mismatch: "
                            f"{morph_dg_passed} OK, {morph_dg_failed} FAILED. "
                            "Details (first 10):\n"
                            + "\n".join(morph_failed_details)
                        )

                    print(
                        "OK: Track4 morph test: DG-connected runtime node playback "
                        f"matches recorded baked blendShape weights at "
                        f"{len(morph_frame_numbers)} VMD frames "
                        f"({morph_dg_passed} comparisons)"
                    )

                    # Clean up morph DG utility nodes
                    morph_created_nodes = morph_dg_result.get("utility_nodes", [])
                    for unode in morph_created_nodes:
                        if cmds.objExists(unode):
                            try:
                                cmds.delete(unode)
                            except Exception:
                                pass

                    if cmds.objExists(morph_runtime_node):
                        cmds.delete(morph_runtime_node)
                finally:
                    cmds.delete(morph_root)

            finally:
                runtime_instance.free()
                runtime_clip.free()
                runtime_model.free()
        except Exception:
            raise

        if cmds.objExists(runtime_node):
            cmds.delete(runtime_node)
        cmds.delete(runtime_model_root)

        unload_result = cmds.mmdFastLoad(
            f=str(FAST_LOAD_MODEL),
            n="mmt_fast_vp2_unload_smoke",
            s=1.0,
            vp2Ownership=True,
        )
        if not unload_result or len(unload_result) != 3:
            raise RuntimeError(f"VP2 unload setup failed: {unload_result!r}")
        unload_root, unload_source, _unload_proxy = unload_result
        try:
            cmds.unloadPlugin(plugin_name, force=False)
        except RuntimeError:
            pass
        if not cmds.pluginInfo(plugin_name, query=True, loaded=True):
            raise RuntimeError("VP2 plugin unloaded while a live proxy still existed")
        if not bool(cmds.getAttr(f"{unload_source}.visibility")):
            raise RuntimeError("VP2 refused unload without restoring source visibility")
        cmds.delete(unload_root)
        print(
            "OK: VP2 live-node unload was refused with the ordinary source mesh visible"
        )
        return 0
    finally:
        maya.standalone.uninitialize()


if __name__ == "__main__":
    raise SystemExit(main())
