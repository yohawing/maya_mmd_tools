"""Compare Bake and Rig influence skinning matrices for a PMX/VMD import."""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from pathlib import Path

import maya.api.OpenMaya as om
import maya.cmds as cmds
import maya.standalone

ROOT = Path(__file__).resolve().parents[2]
ATTR_MMD_BONE_INDEX = "mmd_bone_index"
ATTR_MMD_BONE_NAME = "mmd_bone_name"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pmx", default="tests/data/mmt_test_model.pmx")
    parser.add_argument("--vmd", default="build/local_assets/addiction_tda.vmd")
    parser.add_argument("--out", default="build/reports/bake_rig_skinning_compare.json")
    parser.add_argument("--frame", action="append", type=int, default=[0])
    return parser.parse_args()


def _initialize() -> None:
    try:
        maya.standalone.initialize(name="python")
    except RuntimeError:
        pass
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))


def _resolve(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (ROOT / path).resolve()


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
        },
    )
    if not root:
        raise RuntimeError(f"PMX import failed: {pmx_path}")
    cmds.select(root, replace=True)
    if not import_mmd_file(str(vmd_path), options={"target_model": root, "pmx_path": str(pmx_path)}):
        raise RuntimeError(f"VMD import failed: {vmd_path}")
    return root


def _joint_bone_id(joint: str) -> tuple[int | None, str]:
    bone_index = None
    bone_name = ""
    if cmds.attributeQuery(ATTR_MMD_BONE_INDEX, node=joint, exists=True):
        try:
            bone_index = int(cmds.getAttr(f"{joint}.{ATTR_MMD_BONE_INDEX}"))
        except Exception:
            bone_index = None
    if cmds.attributeQuery(ATTR_MMD_BONE_NAME, node=joint, exists=True):
        try:
            bone_name = cmds.getAttr(f"{joint}.{ATTR_MMD_BONE_NAME}") or ""
        except Exception:
            bone_name = ""
    return bone_index, bone_name


def _skin_clusters(root: str) -> list[str]:
    clusters: list[str] = []
    shapes = cmds.listRelatives(root, allDescendents=True, type="mesh", fullPath=True) or []
    for shape in shapes:
        for node in cmds.listHistory(shape, pruneDagObjects=True) or []:
            if cmds.nodeType(node) == "skinCluster" and node not in clusters:
                clusters.append(node)
    return clusters


def _influence_indices(skin_cluster: str) -> list[tuple[str, int]]:
    indices: list[tuple[str, int]] = []
    for plug in cmds.listConnections(f"{skin_cluster}.matrix", s=True, d=False, p=True) or []:
        try:
            joint = plug.split(".", 1)[0]
            destinations = cmds.listConnections(plug, s=False, d=True, p=True) or []
            for dest in destinations:
                if not dest.startswith(f"{skin_cluster}.matrix["):
                    continue
                logical_index = int(dest.split("[", 1)[1].split("]", 1)[0])
                indices.append((joint, logical_index))
        except Exception:
            continue
    return indices


def _skinning_matrices(root: str) -> dict[int, dict[str, object]]:
    result: dict[int, dict[str, object]] = {}
    for skin_cluster in _skin_clusters(root):
        for joint, logical_index in _influence_indices(skin_cluster):
            if not cmds.objExists(joint):
                continue
            bone_index, bone_name = _joint_bone_id(joint)
            if bone_index is None:
                continue
            bind_pre = om.MMatrix(cmds.getAttr(f"{skin_cluster}.bindPreMatrix[{logical_index}]"))
            world = om.MMatrix(cmds.getAttr(f"{joint}.worldMatrix[0]"))
            bind_world_from_pre = bind_pre.inverse()
            skinning = bind_pre * world
            bind_delta = _matrix_distance(
                [float(bind_world_from_pre[i]) for i in range(16)],
                [float(world[i]) for i in range(16)],
            )
            result[bone_index] = {
                "name": bone_name,
                "joint": joint,
                "skin_cluster": skin_cluster,
                "logical_index": logical_index,
                "matrix": [float(skinning[i]) for i in range(16)],
                "translate": [float(world[12]), float(world[13]), float(world[14])],
                "bind_world_from_pre_translate": [
                    float(bind_world_from_pre[12]),
                    float(bind_world_from_pre[13]),
                    float(bind_world_from_pre[14]),
                ],
                "bind_world_delta": bind_delta,
                "joint_orient": list(cmds.getAttr(f"{joint}.jointOrient")[0]),
                "rotate": list(cmds.getAttr(f"{joint}.rotate")[0]),
            }
    return result


def _matrix_distance(a: list[float], b: list[float]) -> float:
    return math.sqrt(sum((x - y) * (x - y) for x, y in zip(a, b)))


def main() -> int:
    args = _parse_args()
    _initialize()
    pmx_path = _resolve(args.pmx)
    vmd_path = _resolve(args.vmd)

    reports = []
    for frame in args.frame:
        cmds.file(new=True, force=True)
        bake_root = _import_scene(pmx_path, vmd_path, "bake")
        cmds.currentTime(frame, edit=True)
        cmds.refresh(force=True)
        bake = _skinning_matrices(bake_root)

        cmds.file(new=True, force=True)
        rig_root = _import_scene(pmx_path, vmd_path, "rig")
        cmds.currentTime(frame, edit=True)
        cmds.refresh(force=True)
        rig = _skinning_matrices(rig_root)

        diffs = []
        for bone_index in sorted(set(bake) & set(rig)):
            distance = _matrix_distance(bake[bone_index]["matrix"], rig[bone_index]["matrix"])
            diffs.append({
                "bone_index": bone_index,
                "name": bake[bone_index]["name"],
                "matrix_distance": round(distance, 6),
                "bake_translate": [round(v, 6) for v in bake[bone_index]["translate"]],
                "rig_translate": [round(v, 6) for v in rig[bone_index]["translate"]],
                "rig_bind_world_from_pre_translate": [
                    round(v, 6) for v in rig[bone_index]["bind_world_from_pre_translate"]
                ],
                "rig_bind_world_delta": round(rig[bone_index]["bind_world_delta"], 6),
                "bake_rotate": [round(v, 6) for v in bake[bone_index]["rotate"]],
                "rig_rotate": [round(v, 6) for v in rig[bone_index]["rotate"]],
                "rig_joint_orient": [round(v, 6) for v in rig[bone_index]["joint_orient"]],
            })
        diffs.sort(key=lambda item: item["matrix_distance"], reverse=True)
        distances = [float(item["matrix_distance"]) for item in diffs]
        reports.append({
            "frame": frame,
            "count": len(diffs),
            "max": max(distances) if distances else None,
            "mean": statistics.fmean(distances) if distances else None,
            "top": diffs[:20],
        })

    report = {
        "pmx": str(pmx_path),
        "vmd": str(vmd_path),
        "frames": reports,
    }
    out = _resolve(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out.with_suffix(".md").write_text(
        "\n".join([
            "# Bake/Rig Skinning Compare",
            "",
            *[
                (
                    f"- frame {frame_report['frame']}: count=`{frame_report['count']}`, "
                    f"max=`{frame_report['max']}`, mean=`{frame_report['mean']}`, "
                    f"top=`{frame_report['top'][:5]}`"
                )
                for frame_report in reports
            ],
        ]),
        encoding="utf-8",
    )
    print(f"Report JSON: {out}")
    print(f"Report Markdown: {out.with_suffix('.md')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
