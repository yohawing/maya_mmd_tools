"""Compare Maya skeletal deformation against an mmd-anim/PMX skinning oracle.

This is stricter than Bake-vs-Rig parity: it computes expected skinned vertex
positions from PMX raw positions, PMX skin weights, mmd-anim runtime world
matrices, and the actual Maya REST bind matrices, then compares Maya mesh world
positions by stored PMX source vertex index.

Use ``--skeletal-only`` to exclude morph output when isolating VMD bone, IK,
or append motion.  The mode rejects VMD inputs that drive PMX bone morphs;
VMD morph output is otherwise covered by dedicated morph tests.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from pathlib import Path
from typing import Any

import maya.api.OpenMaya as om
import maya.cmds as cmds
import maya.standalone

from mesh_oracle_utils import distance, mesh_points, source_indices, visible_mesh_transforms

ROOT = Path(__file__).resolve().parents[2]
_DLL_DIRECTORY_HANDLES: list[Any] = []


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pmx", default="tests/data/mmt_test_model.pmx")
    parser.add_argument("--vmd", default="tests/data/mmt_test_model_test_motion.vmd")
    parser.add_argument("--out", default="build/reports/mmd_anim_mesh_oracle_compare.json")
    parser.add_argument(
        "--frame",
        action="append",
        type=int,
        default=None,
        help="Frame to compare; repeat for multiple frames (default: 0,30,60)",
    )
    parser.add_argument("--threshold", type=float, default=0.1)
    parser.add_argument("--mode", choices=["bake", "rig"], default="bake")
    parser.add_argument(
        "--namespace",
        default=None,
        help="Optional custom namespace for the imported PMX model.",
    )
    parser.add_argument(
        "--capture",
        default=None,
        help="Optional PNG path for an offscreen post-comparison viewport capture.",
    )
    parser.add_argument(
        "--evaluation-mode",
        choices=["off", "serial", "parallel"],
        default="off",
        help="Maya evaluationManager mode used for scene import and capture.",
    )
    parser.add_argument(
        "--skeletal-only",
        action="store_true",
        help="Disable PMX morph import after rejecting VMD motion that drives bone morphs.",
    )
    parser.add_argument(
        "--bind-source",
        choices=["maya", "pmx"],
        default="maya",
        help="Bind matrices for oracle. 'pmx' uses raw PMX REST pose and does not trust Maya skinCluster bindPreMatrix.",
    )
    return parser.parse_args()


def _initialize() -> None:
    try:
        maya.standalone.initialize(name="python")
    except RuntimeError:
        pass
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))


def _load_rig_plugins() -> None:
    """Load the native rig nodes before importing a ``mode=rig`` scene."""

    maya_major = str(cmds.about(version=True)).split(".", 1)[0]
    configured = os.environ.get("MMD_TOOLS_CPP_PLUGIN", "")
    cpp_plugin = Path(configured) if configured else (
        ROOT / "plug-ins" / maya_major / "Debug" / "mmd_tools_cpp.mll"
    )
    if not cpp_plugin.is_file():
        raise RuntimeError(
            "Native rig plugin is required for mode=rig mesh oracle: "
            f"{cpp_plugin}"
        )
    plugin_dir = str(cpp_plugin.parent)
    if plugin_dir not in os.environ.get("PATH", "").split(os.pathsep):
        os.environ["PATH"] = plugin_dir + os.pathsep + os.environ.get("PATH", "")
    if hasattr(os, "add_dll_directory"):
        _DLL_DIRECTORY_HANDLES.append(os.add_dll_directory(plugin_dir))
    if not cmds.pluginInfo(str(cpp_plugin), query=True, loaded=True):
        cmds.loadPlugin(str(cpp_plugin), quiet=True)


def _load_mmd_plugin() -> None:
    """Load Python MMD nodes required by PMX morph import in every oracle mode."""

    from tests.common.maya_plugin_setup import load_mmd_tools_plugin

    load_mmd_tools_plugin(ROOT, cmds_module=cmds)


def _maya_point_from_mmd(point: tuple[float, float, float]) -> tuple[float, float, float]:
    return (point[0], point[1], -point[2])


def _mat_point_mul(matrix: om.MMatrix, point: tuple[float, float, float]) -> tuple[float, float, float]:
    p = om.MPoint(point[0], point[1], point[2]) * matrix
    return (p.x, p.y, p.z)


def _rotation_only(matrix: om.MMatrix) -> om.MMatrix:
    return om.MMatrix([
        [matrix[0], matrix[1], matrix[2], 0.0],
        [matrix[4], matrix[5], matrix[6], 0.0],
        [matrix[8], matrix[9], matrix[10], 0.0],
        [0.0, 0.0, 0.0, 1.0],
    ])


def _pmx_local_axis_world_matrix(bone: Any) -> om.MMatrix:
    x_axis = om.MVector(bone.x_axis_direction[0], bone.x_axis_direction[1], -bone.x_axis_direction[2])
    x_axis.normalize()
    z_axis = om.MVector(bone.z_axis_direction[0], bone.z_axis_direction[1], -bone.z_axis_direction[2])
    z_axis.normalize()
    y_axis = z_axis ^ x_axis
    y_axis.normalize()
    return om.MMatrix([
        [x_axis.x, x_axis.y, x_axis.z, 0.0],
        [y_axis.x, y_axis.y, y_axis.z, 0.0],
        [z_axis.x, z_axis.y, z_axis.z, 0.0],
        [0.0, 0.0, 0.0, 1.0],
    ])


def _with_translation(matrix: om.MMatrix, point: tuple[float, float, float]) -> om.MMatrix:
    return om.MMatrix([
        [matrix[0], matrix[1], matrix[2], 0.0],
        [matrix[4], matrix[5], matrix[6], 0.0],
        [matrix[8], matrix[9], matrix[10], 0.0],
        [point[0], point[1], point[2], 1.0],
    ])


def _compute_bind_world_matrices(pmx: Any) -> list[om.MMatrix]:
    bind_matrices: list[om.MMatrix] = []
    for bone in pmx.bones:
        bind_matrices.append(_with_translation(om.MMatrix(), _maya_point_from_mmd(tuple(bone.position))))
    return bind_matrices


def _compute_oracle_vertices(
    pmx_path: Path,
    vmd_path: Path,
    frames: list[int],
    bind_world_matrices: list[om.MMatrix] | None = None,
) -> dict[int, list[tuple[float, float, float]]]:
    from mmd_tools.converters.vmd_converter import VmdConverter
    from mmd_tools.core.mmd_parser import parse_pmx_file
    from mmd_tools.core.native.mmd_anim_runtime import MmdRuntimeClip, MmdRuntimeInstance, MmdRuntimeModel

    pmx = parse_pmx_file(str(pmx_path))
    bind_world_matrices = bind_world_matrices or _compute_bind_world_matrices(pmx)

    pmx_bytes = pmx_path.read_bytes()
    vmd_bytes = vmd_path.read_bytes()
    model = MmdRuntimeModel.from_pmx_bytes(pmx_bytes)
    if model is None:
        raise RuntimeError("mmd-anim model creation failed")
    clip = MmdRuntimeClip.from_vmd_bytes_for_model(model, vmd_bytes)
    if clip is None:
        model.free()
        raise RuntimeError("mmd-anim clip creation failed")
    instance = MmdRuntimeInstance.for_model(model)
    if instance is None:
        clip.free()
        model.free()
        raise RuntimeError("mmd-anim instance creation failed")

    result: dict[int, list[tuple[float, float, float]]] = {}
    try:
        for frame in frames:
            if not instance.evaluate_clip_frame(clip, float(frame)):
                raise RuntimeError(f"mmd-anim evaluate failed at frame {frame}")
            runtime_matrices = instance.get_world_matrices() or []
            maya_matrices = [
                om.MMatrix(VmdConverter._convert_mmd_world_matrix_to_maya(list(matrix)))
                for matrix in runtime_matrices
            ]
            vertices: list[tuple[float, float, float]] = []
            for vertex in pmx.vertices:
                accum = [0.0, 0.0, 0.0]
                for bone_index, weight in _vertex_weights(vertex):
                    if weight == 0.0 or bone_index < 0 or bone_index >= len(maya_matrices):
                        continue
                    local = _mat_point_mul(
                        bind_world_matrices[bone_index].inverse(),
                        _maya_point_from_mmd(tuple(vertex.position)),
                    )
                    transformed = _mat_point_mul(maya_matrices[bone_index], local)
                    accum[0] += transformed[0] * weight
                    accum[1] += transformed[1] * weight
                    accum[2] += transformed[2] * weight
                vertices.append((accum[0], accum[1], accum[2]))
            result[frame] = vertices
    finally:
        instance.free()
        clip.free()
        model.free()
    return result


def _vertex_weights(vertex: Any) -> list[tuple[int, float]]:
    t = int(vertex.weight_transform_type)
    if t == 0:
        return [(int(vertex.bone_indices[0]), 1.0)]
    if t in (1, 3):
        w0 = float(vertex.bone_weights[0])
        return [(int(vertex.bone_indices[0]), w0), (int(vertex.bone_indices[1]), 1.0 - w0)]
    if t in (2, 4):
        return [(int(i), float(w)) for i, w in zip(vertex.bone_indices, vertex.bone_weights)]
    raise RuntimeError(f"Unsupported PMX weight type for oracle: {t}")


def _capture_joint_world_matrices(bone_count: int) -> list[om.MMatrix]:
    bind_matrices = [om.MMatrix() for _ in range(bone_count)]
    for joint in cmds.ls(type="joint", long=True) or []:
        if not cmds.attributeQuery("mmd_bone_index", node=joint, exists=True):
            continue
        bone_index = int(cmds.getAttr(f"{joint}.mmd_bone_index"))
        if 0 <= bone_index < bone_count:
            bind_matrices[bone_index] = om.MMatrix(cmds.getAttr(f"{joint}.worldMatrix[0]"))
    return bind_matrices


def _capture_skin_bind_world_matrices(root: str, bone_count: int) -> list[om.MMatrix]:
    bind_matrices = _capture_joint_world_matrices(bone_count)
    skin_clusters: list[str] = []
    for mesh in visible_mesh_transforms(root, require_skin_cluster=True):
        history = cmds.listHistory(mesh, pruneDagObjects=True) or []
        for node in history:
            if cmds.nodeType(node) == "skinCluster" and node not in skin_clusters:
                skin_clusters.append(node)

    for skin_cluster in skin_clusters:
        influences = cmds.skinCluster(skin_cluster, query=True, influence=True) or []
        for logical_index, joint in enumerate(influences):
            if not cmds.objExists(joint) or not cmds.attributeQuery("mmd_bone_index", node=joint, exists=True):
                continue
            bone_index = int(cmds.getAttr(f"{joint}.mmd_bone_index"))
            if not (0 <= bone_index < bone_count):
                continue
            try:
                bind_pre = om.MMatrix(cmds.getAttr(f"{skin_cluster}.bindPreMatrix[{logical_index}]"))
            except Exception:
                continue
            bind_matrices[bone_index] = bind_pre.inverse()
    return bind_matrices


def _active_vmd_morphs_drive_bone_morphs(pmx_data: Any, vmd_data: Any) -> list[str]:
    """Return VMD morph names that resolve to a PMX bone morph through groups."""

    from mmd_tools.core.pmx_data.morph import PmxMorphType

    morphs = list(getattr(pmx_data, "morphs", []) or [])
    by_name = {str(morph.get_name()): index for index, morph in enumerate(morphs)}

    def _contains_bone_morph(index: int, seen: set[int]) -> bool:
        if index in seen or not (0 <= index < len(morphs)):
            return False
        seen.add(index)
        morph = morphs[index]
        if getattr(morph, "morph_type", None) == PmxMorphType.BoneMorph:
            return True
        if getattr(morph, "morph_type", None) != PmxMorphType.GroupMorph:
            return False
        return any(
            _contains_bone_morph(int(offset.get("morph_index", -1)), seen)
            for offset in (getattr(morph, "offsets", []) or [])
            if isinstance(offset, dict)
        )

    active_names = {str(frame.morph_name) for frame in (getattr(vmd_data, "morph_frames", []) or [])}
    return sorted(
        name for name in active_names if name in by_name and _contains_bone_morph(by_name[name], set())
    )


def _import_scene(
    pmx_path: Path,
    vmd_path: Path,
    mode: str,
    bone_count: int,
    *,
    skeletal_only: bool,
    custom_namespace: str | None = None,
) -> tuple[str, list[om.MMatrix]]:
    from mmd_tools.core import settings
    from mmd_tools.io.mmd_importer import import_mmd_file

    cmds.file(new=True, force=True)
    if mode == "rig":
        _load_rig_plugins()
    _load_mmd_plugin()
    settings.set("import.model.create_mmd_shaders", False)
    settings.set("import.rig.add_semi_standard_bones", False)
    root = import_mmd_file(
        str(pmx_path),
        options={
            "setup_rig": mode == "rig",
            "setup_bone_orientation": mode == "rig",
            "import_physics": False,
            "import_morphs": not skeletal_only,
            "custom_namespace": custom_namespace,
        },
    )
    if not root:
        raise RuntimeError("PMX import failed")
    bind_world_matrices = _capture_skin_bind_world_matrices(root, bone_count)
    cmds.select(root, replace=True)
    ok = import_mmd_file(
        str(vmd_path),
        options={
            "target_model": root,
            "pmx_path": str(pmx_path),
            "bake_mode": mode == "bake",
        },
    )
    if not ok:
        raise RuntimeError("VMD import failed")
    return root, bind_world_matrices


def _capture_maya_by_source_index(root: str, frames: list[int]) -> dict[int, dict[int, tuple[float, float, float]]]:
    meshes = visible_mesh_transforms(root, require_skin_cluster=True)
    result: dict[int, dict[int, tuple[float, float, float]]] = {}
    for frame in frames:
        cmds.currentTime(frame, edit=True)
        try:
            cmds.refresh(force=True)
        except Exception:
            pass
        frame_points: dict[int, tuple[float, float, float]] = {}
        for mesh in meshes:
            points = mesh_points(mesh)
            indices = source_indices(mesh)
            if len(points) != len(indices):
                raise RuntimeError(f"{mesh}: point/source-index count mismatch {len(points)} != {len(indices)}")
            for source_index, point in zip(indices, points):
                frame_points[int(source_index)] = point
        result[frame] = frame_points
    return result


def _capture_viewport(root: str, frame: int, requested_path: Path) -> Path:
    """Capture the evaluated model from a fitted standalone perspective camera."""
    requested_path = requested_path.resolve()
    requested_path.parent.mkdir(parents=True, exist_ok=True)
    bounds = cmds.exactWorldBoundingBox(root)
    center = tuple((bounds[index] + bounds[index + 3]) * 0.5 for index in range(3))
    extent = max(bounds[index + 3] - bounds[index] for index in range(3)) or 10.0

    camera = "persp"
    cmds.setAttr(f"{camera}.translate", center[0] + extent * 1.5, center[1] + extent * 0.4, center[2] + extent * 2.0, type="double3")
    target = cmds.spaceLocator(name="mmdOracleCaptureTarget")[0]
    cmds.setAttr(f"{target}.translate", *center, type="double3")
    constraint = cmds.aimConstraint(
        target,
        camera,
        aimVector=(0.0, 0.0, -1.0),
        upVector=(0.0, 1.0, 0.0),
        worldUpType="scene",
    )[0]
    cmds.delete(constraint, target)
    cmds.currentTime(frame, edit=True)
    cmds.playblast(
        filename=str(requested_path.with_suffix("")),
        frame=frame,
        format="image",
        compression="png",
        offScreen=True,
        offScreenViewportUpdate=True,
        viewer=False,
        width=960,
        height=720,
        forceOverwrite=True,
        showOrnaments=False,
        percent=100,
    )
    candidates = sorted(
        requested_path.parent.glob(f"{requested_path.stem}*.png"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not candidates or candidates[0].stat().st_size <= 0:
        raise RuntimeError(f"viewport capture was not produced for {requested_path}")
    return candidates[0]


def _compare(
    maya_points: dict[int, dict[int, tuple[float, float, float]]],
    oracle: dict[int, list[tuple[float, float, float]]],
    frames: list[int],
    threshold: float,
) -> dict[str, Any]:
    per_frame = {}
    failed = False
    all_distances = []
    for frame in frames:
        frame_distances = []
        missing = 0
        for source_index, maya_point in maya_points[frame].items():
            if source_index >= len(oracle[frame]):
                missing += 1
                continue
            frame_distances.append(distance(maya_point, oracle[frame][source_index]))
        all_distances.extend(frame_distances)
        max_dist = max(frame_distances) if frame_distances else None
        mean = statistics.fmean(frame_distances) if frame_distances else None
        p95 = sorted(frame_distances)[int(len(frame_distances) * 0.95)] if frame_distances else None
        frame_failed = max_dist is None or max_dist > threshold or missing > 0
        failed = failed or frame_failed
        per_frame[str(frame)] = {
            "compared_vertices": len(frame_distances),
            "missing": missing,
            "max": round(max_dist, 6) if max_dist is not None else None,
            "mean": round(mean, 6) if mean is not None else None,
            "p95": round(p95, 6) if p95 is not None else None,
            "failed": frame_failed,
        }
    return {
        "passed": not failed,
        "threshold": threshold,
        "overall_max": round(max(all_distances), 6) if all_distances else None,
        "overall_mean": round(statistics.fmean(all_distances), 6) if all_distances else None,
        "frames": per_frame,
    }


def main() -> int:
    args = _parse_args()
    _initialize()
    cmds.evaluationManager(mode=args.evaluation_mode)
    pmx_path = (ROOT / args.pmx).resolve() if not Path(args.pmx).is_absolute() else Path(args.pmx)
    vmd_path = (ROOT / args.vmd).resolve() if not Path(args.vmd).is_absolute() else Path(args.vmd)
    frames = list(dict.fromkeys(args.frame if args.frame is not None else [0, 30, 60]))
    from mmd_tools.core.mmd_parser import parse_mmd_file, parse_pmx_file

    pmx_data = parse_pmx_file(str(pmx_path))
    if args.skeletal_only:
        driven_bone_morphs = _active_vmd_morphs_drive_bone_morphs(
            pmx_data,
            parse_mmd_file(str(vmd_path)),
        )
        if driven_bone_morphs:
            raise ValueError(
                "--skeletal-only cannot disable PMX bone morphs driven by this VMD: "
                + ", ".join(driven_bone_morphs)
            )
    root, maya_bind_world_matrices = _import_scene(
        pmx_path,
        vmd_path,
        args.mode,
        len(pmx_data.bones),
        skeletal_only=args.skeletal_only,
        custom_namespace=args.namespace,
    )
    bind_world_matrices = None if args.bind_source == "pmx" else maya_bind_world_matrices
    oracle = _compute_oracle_vertices(pmx_path, vmd_path, frames, bind_world_matrices)
    maya_points = _capture_maya_by_source_index(root, frames)
    comparison = _compare(maya_points, oracle, frames, args.threshold)
    capture_path = (
        _capture_viewport(root, frames[0], Path(args.capture))
        if args.capture
        else None
    )
    report = {
        "status": "passed" if comparison["passed"] else "failed",
        "oracle": {
            "identity": "mmd_anim_mesh_oracle_compare",
            "runtime": "mmd-anim",
            "mode": args.mode,
            "bind_source": args.bind_source,
        },
        "pmx": str(pmx_path),
        "vmd": str(vmd_path),
        "mode": args.mode,
        "evaluationMode": args.evaluation_mode,
        "namespace": args.namespace,
        "capture": str(capture_path) if capture_path else None,
        "skeletalOnly": bool(args.skeletal_only),
        "bind_source": args.bind_source,
        "comparison": comparison,
    }
    out = Path(args.out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md = out.with_suffix(".md")
    lines = [
        "# mmd-anim Mesh Oracle Compare",
        "",
        f"- status: `{report['status']}`",
        f"- mode: `{args.mode}`",
        f"- bind source: `{args.bind_source}`",
        f"- pmx: `{pmx_path}`",
        f"- vmd: `{vmd_path}`",
        f"- overall max: `{comparison['overall_max']}`",
        f"- overall mean: `{comparison['overall_mean']}`",
        "",
    ]
    for frame, data in comparison["frames"].items():
        lines.append(
            f"- frame {frame}: max=`{data['max']}`, mean=`{data['mean']}`, "
            f"p95=`{data['p95']}`, vertices=`{data['compared_vertices']}`, failed=`{data['failed']}`"
        )
    md.write_text("\n".join(lines), encoding="utf-8")
    print(f"Report JSON: {out}")
    print(f"Report Markdown: {md}")
    print(f"Status: {report['status']}")
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
