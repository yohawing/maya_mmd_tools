"""Compare Bake and Rig output for a minimal MMD leg-IK VMD.

This probe is intentionally narrower than the full parity suite.  The fixture
`mmt_test_model_ik_test_motion.vmd` contains one bone frame that moves the left
leg IK controller, so frame 0 is enough to catch a solver that reaches the ankle
while bending the knee through the wrong side.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import maya.api.OpenMaya as om
import maya.cmds as cmds
import maya.standalone


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BONES = ("left_leg_ik", "left_leg", "left_knee", "left_ankle", "left_toe", "toe2_L")


def _resolve(path: str) -> Path:
    p = Path(path)
    return p if p.is_absolute() else (ROOT / p).resolve()


def _initialize() -> None:
    try:
        maya.standalone.initialize(name="python")
    except RuntimeError:
        pass
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))


def _import_scene(pmx_path: Path, vmd_path: Path, mode: str) -> str:
    from mmd_tools.core import settings
    from mmd_tools.io.mmd_importer import import_mmd_file

    settings.set("import.model.create_mmd_shaders", False)
    settings.set("import.rig.add_semi_standard_bones", False)
    root = import_mmd_file(
        str(pmx_path),
        options={
            "setup_rig": mode == "rig",
            "setup_bone_orientation": mode == "rig",
            "import_physics": False,
            "create_mmd_shaders": False,
        },
    )
    if not root:
        raise RuntimeError(f"PMX import failed: {pmx_path}")
    cmds.select(root, replace=True)
    if not import_mmd_file(str(vmd_path), options={"target_model": root, "pmx_path": str(pmx_path)}):
        raise RuntimeError(f"VMD import failed: {vmd_path}")
    return root


def _world_transform(node: str) -> dict[str, object]:
    wm = cmds.xform(node, query=True, worldSpace=True, matrix=True)
    mat = om.MMatrix(wm)
    tfm = om.MTransformationMatrix(mat)
    quat = tfm.rotation(asQuaternion=True)
    pos = tfm.translation(om.MSpace.kWorld)
    return {
        "world_translate": [float(pos.x), float(pos.y), float(pos.z)],
        "world_quat": [float(quat.x), float(quat.y), float(quat.z), float(quat.w)],
        "rotate": [float(v) for v in cmds.getAttr(f"{node}.rotate")[0]],
        "joint_orient": [float(v) for v in cmds.getAttr(f"{node}.jointOrient")[0]],
    }


def _skin_clusters(root: str) -> list[str]:
    clusters: list[str] = []
    shapes = cmds.listRelatives(root, allDescendents=True, type="mesh", fullPath=True) or []
    for shape in shapes:
        for node in cmds.listHistory(shape, pruneDagObjects=True) or []:
            if cmds.nodeType(node) == "skinCluster" and node not in clusters:
                clusters.append(node)
    return clusters


def _joint_bone_index(joint: str) -> int | None:
    if not cmds.attributeQuery("mmd_bone_index", node=joint, exists=True):
        return None
    try:
        return int(cmds.getAttr(f"{joint}.mmd_bone_index"))
    except Exception:
        return None


def _skinning_matrices(root: str) -> dict[int, list[float]]:
    result: dict[int, list[float]] = {}
    for skin_cluster in _skin_clusters(root):
        for plug in cmds.listConnections(f"{skin_cluster}.matrix", s=True, d=False, p=True) or []:
            joint = plug.split(".", 1)[0]
            bone_index = _joint_bone_index(joint)
            if bone_index is None:
                continue
            destinations = cmds.listConnections(plug, s=False, d=True, p=True) or []
            for dest in destinations:
                if not dest.startswith(f"{skin_cluster}.matrix["):
                    continue
                logical_index = int(dest.split("[", 1)[1].split("]", 1)[0])
                bind_pre = om.MMatrix(cmds.getAttr(f"{skin_cluster}.bindPreMatrix[{logical_index}]"))
                world = om.MMatrix(cmds.getAttr(f"{joint}.worldMatrix[0]"))
                skinning = bind_pre * world
                result[bone_index] = [float(skinning[i]) for i in range(16)]
    return result


def _capture(pmx_path: Path, vmd_path: Path, mode: str, frame: int, bones: tuple[str, ...]) -> dict[str, object]:
    cmds.file(new=True, force=True)
    root = _import_scene(pmx_path, vmd_path, mode)
    cmds.currentTime(frame, edit=True)
    cmds.refresh(force=True)
    return {
        "root": root,
        "bones": {bone: _world_transform(bone) for bone in bones if cmds.objExists(bone)},
        "skinning": _skinning_matrices(root),
        "ik_nodes": {
            node: {
                "enabled": bool(cmds.getAttr(f"{node}.enabled")) if cmds.attributeQuery("enabled", node=node, exists=True) else None,
                "outputRotate": [
                    [float(v) for v in cmds.getAttr(f"{node}.outputRotate[{index}]")[0]]
                    for index in range(8)
                    if cmds.objExists(f"{node}.outputRotate[{index}]")
                ],
            }
            for node in (cmds.ls(type="mmdCcdIk") or [])
        },
    }


def _distance(a: list[float], b: list[float]) -> float:
    return math.sqrt(sum((x - y) * (x - y) for x, y in zip(a, b)))


def _quat_angle_deg(a: list[float], b: list[float]) -> float:
    dot = abs(sum(x * y for x, y in zip(a, b)))
    dot = max(-1.0, min(1.0, dot))
    return math.degrees(2.0 * math.acos(dot))


def _matrix_distance(a: list[float], b: list[float]) -> float:
    return math.sqrt(sum((x - y) * (x - y) for x, y in zip(a, b)))


def run_probe(
    pmx_path: Path,
    vmd_path: Path,
    out_path: Path,
    frame: int,
    position_threshold: float,
    rotation_threshold_deg: float,
    skinning_threshold: float,
) -> int:
    bake = _capture(pmx_path, vmd_path, "bake", frame, DEFAULT_BONES)
    rig = _capture(pmx_path, vmd_path, "rig", frame, DEFAULT_BONES)

    bone_diffs = []
    for bone in DEFAULT_BONES:
        if bone not in bake["bones"] or bone not in rig["bones"]:
            continue
        bake_bone = bake["bones"][bone]
        rig_bone = rig["bones"][bone]
        pos_dist = _distance(bake_bone["world_translate"], rig_bone["world_translate"])
        rot_angle = _quat_angle_deg(bake_bone["world_quat"], rig_bone["world_quat"])
        bone_diffs.append({
            "bone": bone,
            "position_distance": pos_dist,
            "rotation_angle_deg": rot_angle,
            "bake": bake_bone,
            "rig": rig_bone,
        })

    skinning_diffs = []
    for bone_index in sorted(set(bake["skinning"]) & set(rig["skinning"])):
        skinning_diffs.append({
            "bone_index": bone_index,
            "matrix_distance": _matrix_distance(bake["skinning"][bone_index], rig["skinning"][bone_index]),
        })
    skinning_diffs.sort(key=lambda item: item["matrix_distance"], reverse=True)

    max_pos = max((item["position_distance"] for item in bone_diffs), default=0.0)
    max_rot = max((item["rotation_angle_deg"] for item in bone_diffs), default=0.0)
    max_skin = max((item["matrix_distance"] for item in skinning_diffs), default=0.0)
    passed = (
        max_pos <= position_threshold
        and max_rot <= rotation_threshold_deg
        and max_skin <= skinning_threshold
    )

    report = {
        "pmx": str(pmx_path),
        "vmd": str(vmd_path),
        "frame": frame,
        "thresholds": {
            "position": position_threshold,
            "rotation_deg": rotation_threshold_deg,
            "skinning_matrix": skinning_threshold,
        },
        "summary": {
            "max_position_distance": max_pos,
            "max_rotation_angle_deg": max_rot,
            "max_skinning_matrix_distance": max_skin,
            "status": "passed" if passed else "failed",
        },
        "bone_diffs": bone_diffs,
        "top_skinning_diffs": skinning_diffs[:20],
        "rig_ik_nodes": rig["ik_nodes"],
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# IK Motion Bake/Rig Probe",
        "",
        f"- PMX: `{pmx_path}`",
        f"- VMD: `{vmd_path}`",
        f"- frame: `{frame}`",
        f"- max position distance: `{max_pos:.6f}`",
        f"- max rotation angle deg: `{max_rot:.6f}`",
        f"- max skinning matrix distance: `{max_skin:.6f}`",
        f"- status: `{'passed' if passed else 'failed'}`",
        "",
        "## Bone Diffs",
        "",
    ]
    for item in bone_diffs:
        lines.append(
            f"- `{item['bone']}` pos=`{item['position_distance']:.6f}` "
            f"rot=`{item['rotation_angle_deg']:.6f}` "
            f"bake_t=`{tuple(round(v, 6) for v in item['bake']['world_translate'])}` "
            f"rig_t=`{tuple(round(v, 6) for v in item['rig']['world_translate'])}` "
            f"bake_r=`{tuple(round(v, 6) for v in item['bake']['rotate'])}` "
            f"rig_r=`{tuple(round(v, 6) for v in item['rig']['rotate'])}`"
        )
    lines.extend(["", "## Top Skinning Diffs", ""])
    for item in skinning_diffs[:10]:
        lines.append(f"- bone `{item['bone_index']}` matrix=`{item['matrix_distance']:.6f}`")
    out_path.with_suffix(".md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"Report JSON: {out_path}")
    print(f"Report Markdown: {out_path.with_suffix('.md')}")
    print(f"Status: {'passed' if passed else 'failed'}")
    return 0 if passed else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pmx", default="tests/data/mmt_test_model.pmx")
    parser.add_argument("--vmd", default="tests/data/mmt_test_model_ik_test_motion.vmd")
    parser.add_argument("--out", default="build/reports/ik_motion_bake_rig_probe.json")
    parser.add_argument("--frame", type=int, default=0)
    parser.add_argument("--position-threshold", type=float, default=0.05)
    parser.add_argument("--rotation-threshold-deg", type=float, default=0.5)
    parser.add_argument("--skinning-threshold", type=float, default=0.05)
    args = parser.parse_args()

    _initialize()
    return run_probe(
        _resolve(args.pmx),
        _resolve(args.vmd),
        _resolve(args.out),
        args.frame,
        args.position_threshold,
        args.rotation_threshold_deg,
        args.skinning_threshold,
    )


if __name__ == "__main__":
    raise SystemExit(main())
