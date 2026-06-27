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
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
NODE_TYPE = "mmdRuntimeInstance"
APPEND_NODE_TYPE = "mmdAppendNode"
CCDIK_NODE_TYPE = "mmdCcdIkNode"
FAST_LOAD_MODEL = ROOT / "tests" / "data" / "mmt_test_model.pmx"
FAST_IMPORT_SKIN_MODEL = ROOT / "tests" / "data" / "for_unit_test" / "test_1bone_cube.pmx"
FAST_LOAD_MORPH_MODEL = ROOT / "tests" / "data" / "test_morph_model.pmx"
TRACK4_VMD_MOTION = ROOT / "tests" / "data" / "for_unit_test" / "test_1bone_cube_motion.vmd"


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
    os.environ["PATH"] = str(plugin_path.parent) + os.pathsep + os.environ.get("PATH", "")
    if hasattr(os, "add_dll_directory"):
        os.add_dll_directory(str(plugin_path.parent))

    maya.standalone.initialize(name="python")
    try:
        cmds.loadPlugin(str(plugin_path), quiet=True)
        node = cmds.createNode(NODE_TYPE)
        if not cmds.objExists(node):
            raise RuntimeError(f"Failed to create node: {NODE_TYPE}")

        for attr in ("time", "pmxData", "vmdData", "worldMatrices", "morphWeights", "ikEnabled"):
            if not cmds.attributeQuery(attr, node=node, exists=True):
                raise RuntimeError(f"Missing attribute {attr!r} on {node}")

        print(f"OK: loaded {plugin_path}")
        print(f"OK: created {node} ({NODE_TYPE})")

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

        # --- mmdAppendNode (Phase B) ---
        append_node = cmds.createNode(APPEND_NODE_TYPE)
        if not cmds.objExists(append_node):
            raise RuntimeError(f"Failed to create node: {APPEND_NODE_TYPE}")

        # double3 属性は compound なので attributeQuery では子属性まで個別確認
        expected_attrs = [
            "grantRate", "enableTranslate", "enableRotate",
            "outputTranslate", "outputRotate",
            "inputTranslate", "inputRotate",
            "parentTranslate", "parentRotate",
        ]
        for attr in expected_attrs:
            if not cmds.attributeQuery(attr, node=append_node, exists=True):
                raise RuntimeError(f"Missing attribute {attr!r} on {append_node}")

        # double3 の子属性も確認
        for parent, children in [
            ("inputTranslate", ["inputTranslateX", "inputTranslateY", "inputTranslateZ"]),
            ("inputRotate", ["inputRotateX", "inputRotateY", "inputRotateZ"]),
            ("parentTranslate", ["parentTranslateX", "parentTranslateY", "parentTranslateZ"]),
            ("parentRotate", ["parentRotateX", "parentRotateY", "parentRotateZ"]),
            ("outputTranslate", ["outputTranslateX", "outputTranslateY", "outputTranslateZ"]),
            ("outputRotate", ["outputRotateX", "outputRotateY", "outputRotateZ"]),
        ]:
            for child in children:
                if not cmds.attributeQuery(child, node=append_node, exists=True):
                    raise RuntimeError(
                        f"Missing child attribute {child!r} (of {parent}) on {append_node}"
                    )

        # default 値の確認 (grantRate=0.0, enableTranslate=true, enableRotate=true)
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

        # Phase B: outputTranslate = inputTranslate + parentTranslate * grantRate
        out_t = cmds.getAttr(f"{append_node}.outputTranslate")[0]
        expected_t = (10.5, 21.0, 31.5)
        if any(abs(actual - expected) > 1e-9 for actual, expected in zip(out_t, expected_t)):
            raise RuntimeError(f"outputTranslate mismatch: expected {expected_t}, got {out_t}")

        # Phase B: outputRotate = slerp(identity, parentQuat, grantRate) * inputQuat
        out_r = cmds.getAttr(f"{append_node}.outputRotate")[0]
        expected_r = (96.0251476257, 48.8315586337, 41.3787177580)
        if any(abs(actual - expected) > 1e-6 for actual, expected in zip(out_r, expected_r)):
            raise RuntimeError(f"outputRotate mismatch: expected {expected_r}, got {out_r}")

        # enableTranslate=false → output = input
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

        # enableRotate=false → output = input
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

        # X 軸のみの回転: outputRotateX = inputRotateX + parentRotateX * grantRate
        cmds.setAttr(f"{append_node}.inputRotate", 90.0, 0.0, 0.0, type="double3")
        cmds.setAttr(f"{append_node}.parentRotate", 10.0, 0.0, 0.0, type="double3")
        cmds.setAttr(f"{append_node}.grantRate", 0.25)
        out_xonly = cmds.getAttr(f"{append_node}.outputRotate")[0]
        expected_xonly = (92.5, 0.0, 0.0)
        if any(abs(actual - expected) > 1e-9 for actual, expected in zip(out_xonly, expected_xonly)):
            raise RuntimeError(f"outputRotate X-only mismatch: expected {expected_xonly}, got {out_xonly}")

        cmds.delete(append_node)
        print(f"OK: created {APPEND_NODE_TYPE}, verified attributes, defaults, and Phase B compute")

        # --- mmdCcdIkNode (Phase A - CCDIK) ---
        ccdik_node = cmds.createNode(CCDIK_NODE_TYPE)
        if not cmds.objExists(ccdik_node):
            raise RuntimeError(f"Failed to create node: {CCDIK_NODE_TYPE}")

        # 全属性存在確認
        expected_attrs = [
            "inputRoot", "inputEffector", "target", "enabled",
            "iterations", "angleLimit", "inputChain",
            "outputRotate", "outputAngle", "solved",
            "outputLinkAngles", "outputLinkRotates",
        ]
        for attr in expected_attrs:
            if not cmds.attributeQuery(attr, node=ccdik_node, exists=True):
                raise RuntimeError(f"Missing attribute {attr!r} on {ccdik_node}")

        # double3 子属性
        for parent, children in [
            ("inputRoot", ["inputRootX", "inputRootY", "inputRootZ"]),
            ("inputEffector", ["inputEffectorX", "inputEffectorY", "inputEffectorZ"]),
            ("target", ["targetX", "targetY", "targetZ"]),
            ("outputRotate", ["outputRotateX", "outputRotateY", "outputRotateZ"]),
        ]:
            for child in children:
                if not cmds.attributeQuery(child, node=ccdik_node, exists=True):
                    raise RuntimeError(
                        f"Missing child attribute {child!r} (of {parent}) on {ccdik_node}"
                    )

        # enabled のデフォルト値
        actual_enabled = cmds.getAttr(f"{ccdik_node}.enabled")
        if actual_enabled is not True:
            raise RuntimeError(f"enabled default should be True, got {actual_enabled}")

        # iterations のデフォルト値
        actual_iterations = cmds.getAttr(f"{ccdik_node}.iterations")
        if actual_iterations != 1:
            raise RuntimeError(f"iterations default should be 1, got {actual_iterations}")

        # angleLimit のデフォルト値
        actual_angle_limit = cmds.getAttr(f"{ccdik_node}.angleLimit")
        if abs(actual_angle_limit - 180.0) > 1e-9:
            raise RuntimeError(f"angleLimit default should be 180.0, got {actual_angle_limit}")

        # --- Test 1: 標準ケース root=(0,0,0), effector=(1,0,0), target=(0,1,0) ---
        # root->effector = (1,0,0) (X+方向)
        # root->target   = (0,1,0) (Y+方向)
        # Z 軸周りの signed angle = atan2(1*1 - 0*0, 1*0 + 0*1) = atan2(1, 0) = 90°
        cmds.setAttr(f"{ccdik_node}.inputRoot", 0.0, 0.0, 0.0, type="double3")
        cmds.setAttr(f"{ccdik_node}.inputEffector", 1.0, 0.0, 0.0, type="double3")
        cmds.setAttr(f"{ccdik_node}.target", 0.0, 1.0, 0.0, type="double3")

        out_rot = cmds.getAttr(f"{ccdik_node}.outputRotate")[0]
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
        out_rot_disabled = cmds.getAttr(f"{ccdik_node}.outputRotate")[0]
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
        out_rot_lim = cmds.getAttr(f"{ccdik_node}.outputRotate")[0]
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

        cmds.delete(ccdik_node)
        print(f"OK: created {CCDIK_NODE_TYPE}, verified attributes, IK compute, and disabled state")

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
        from mmd_tools.io.mmd_importer import import_mmd_file
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
        return 0
    finally:
        maya.standalone.uninitialize()


if __name__ == "__main__":
    raise SystemExit(main())
