"""Measure Citlali root-motion, skin-product, and mesh parity.

The probe deliberately moves the imported model root by a known non-zero
world-space delta.  It records root/joint matrices, every skinCluster
``bindPreMatrix * joint.worldMatrix`` product, and representative world-space
mesh vertices before and after the move.  The moved scene is then saved and
reopened so the same topology and skin-product observations can be compared.

No root zeroing, animation baking, source-PMX writes, or production source
changes are performed.  This is an evidence probe for
``TODO ROOT-MOVE-SKIN-PARITY-1`` rather than a correction.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple

import maya.api.OpenMaya as om
import maya.cmds as cmds

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PMX = "build/fixtures/citlali_ascii_file/citlali.pmx"
DEFAULT_OUT = "build/reports/root_move_skin_parity_probe.json"
DEFAULT_DELTA = (17.5, -8.25, 11.0)
REQUIRED_JOINT_LABELS = ("Hips", "LeftArm", "LeftLeg")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pmx", default=DEFAULT_PMX, help="ASCII-path Citlali PMX fixture.")
    parser.add_argument("--out", default=DEFAULT_OUT, help="JSON report path under build/.")
    parser.add_argument(
        "--delta",
        default=",".join(str(value) for value in DEFAULT_DELTA),
        help="Non-zero world-space root translation delta as X,Y,Z.",
    )
    parser.add_argument(
        "--vertices-per-mesh",
        type=int,
        default=8,
        help="Number of representative world-space vertices sampled per mesh.",
    )
    return parser.parse_args()


def _resolve(value: str) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (ROOT / path).resolve()


def _parse_delta(raw: str) -> List[float]:
    values = [float(part.strip()) for part in str(raw).split(",") if part.strip()]
    if len(values) != 3:
        raise ValueError("--delta must contain exactly three comma-separated numbers")
    if math.sqrt(sum(value * value for value in values)) <= 1.0e-9:
        raise ValueError("--delta must be non-zero; root zeroing is intentionally prohibited")
    return values


def _load_plugin() -> None:
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from tests.common.maya_plugin_setup import load_mmd_tools_plugin

    load_mmd_tools_plugin(ROOT)


def _import_model(path: Path) -> str:
    from mmd_tools.io.mmd_importer import import_mmd_file

    root = import_mmd_file(
        str(path),
        options={
            "use_namespace": True,
            "setup_rig": True,
            "import_physics": False,
            "create_mmd_shaders": False,
            "use_native_pmx_parse": False,
            "require_native_pmx_parse": False,
        },
    )
    if not root:
        raise RuntimeError(f"PMX import failed: {path}")
    return str(root)


def _matrix(node: str, plug: str = "worldMatrix[0]") -> om.MMatrix:
    return om.MMatrix(cmds.getAttr(f"{node}.{plug}"))


def _matrix_values(value: om.MMatrix) -> List[float]:
    return [float(value[index]) for index in range(16)]


def _matrix_distance(left: Sequence[float], right: Sequence[float]) -> float:
    return math.sqrt(sum((float(a) - float(b)) ** 2 for a, b in zip(left, right)))


def _translation(value: om.MMatrix) -> List[float]:
    return [float(value[12]), float(value[13]), float(value[14])]


def _translation_delta(before: Sequence[float], after: Sequence[float]) -> List[float]:
    return [float(after[index]) - float(before[index]) for index in range(3)]


def _norm_name(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _joint_labels(root: str) -> Dict[str, str]:
    """Resolve required English labels from MMD metadata or Maya leaf names."""
    joints = cmds.listRelatives(root, allDescendents=True, type="joint", fullPath=True) or []
    candidates: List[Tuple[str, str]] = []
    for joint in joints:
        values = [str(joint).split("|")[-1]]
        for attr in ("mmd_bone_name_en", "mmd_bone_name"):
            if cmds.attributeQuery(attr, node=joint, exists=True):
                try:
                    values.append(str(cmds.getAttr(f"{joint}.{attr}") or ""))
                except Exception:
                    pass
        candidates.append((str(joint), " ".join(values)))

    aliases = {
        "Hips": ("hips", "pelvis", "center"),
        "LeftArm": ("leftarm", "leftupperarm", "leftshoulder"),
        "LeftLeg": ("leftleg", "leftlowerleg", "leftshin"),
    }
    result: Dict[str, str] = {}
    for label, tokens in aliases.items():
        for joint, raw_names in candidates:
            normalized = _norm_name(raw_names)
            if any(_norm_name(token) in normalized for token in tokens):
                result[label] = joint
                break
    return result


def _mesh_shapes(root: str) -> List[str]:
    shapes: List[str] = []
    for shape in cmds.listRelatives(root, allDescendents=True, type="mesh", fullPath=True) or []:
        try:
            if cmds.getAttr(f"{shape}.intermediateObject"):
                continue
        except Exception:
            pass
        shapes.append(str(shape))
    return sorted(shapes)


def _skin_clusters(root: str) -> List[str]:
    clusters: List[str] = []
    for shape in _mesh_shapes(root):
        for node in cmds.listHistory(shape, pruneDagObjects=True) or []:
            if cmds.nodeType(node) == "skinCluster" and node not in clusters:
                clusters.append(str(node))
    return sorted(clusters)


def _influence_indices(skin_cluster: str) -> List[Tuple[str, int]]:
    pairs: set[Tuple[str, int]] = set()
    plugs = cmds.listConnections(
        f"{skin_cluster}.matrix", source=True, destination=False, plugs=True
    ) or []
    for plug in plugs:
        joint = str(plug).split(".", 1)[0]
        destinations = cmds.listConnections(plug, source=False, destination=True, plugs=True) or []
        for destination in destinations:
            match = re.search(r"\.matrix\[(\d+)\]", str(destination))
            if match and str(destination).startswith(f"{skin_cluster}.matrix["):
                pairs.add((joint, int(match.group(1))))
    return sorted(pairs, key=lambda item: (item[1], item[0]))


def _skin_observation(root: str) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    topology: List[Dict[str, Any]] = []
    for cluster in _skin_clusters(root):
        for joint, logical_index in _influence_indices(cluster):
            if not cmds.objExists(joint):
                continue
            bind_pre = _matrix(cluster, f"bindPreMatrix[{logical_index}]")
            world = _matrix(joint)
            product = bind_pre * world
            bind_values = _matrix_values(bind_pre)
            world_values = _matrix_values(world)
            product_values = _matrix_values(product)
            rows.append(
                {
                    "skinCluster": cluster,
                    "joint": joint,
                    "logicalIndex": logical_index,
                    "bindPreMatrix": bind_values,
                    "jointWorldMatrix": world_values,
                    "skinProduct": product_values,
                }
            )
            topology.append(
                {
                    "skinCluster": cluster,
                    "joint": joint,
                    "logicalIndex": logical_index,
                    "matrixConnection": f"{joint}.worldMatrix[0] -> {cluster}.matrix[{logical_index}]",
                    "bindPrePlug": f"{cluster}.bindPreMatrix[{logical_index}]",
                }
            )
    return {
        "rows": rows,
        "topology": topology,
        "clusters": _skin_clusters(root),
    }


def _mesh_observation(root: str, vertices_per_mesh: int) -> Dict[str, Any]:
    if vertices_per_mesh < 1:
        raise ValueError("--vertices-per-mesh must be >= 1")
    rows: List[Dict[str, Any]] = []
    for shape in _mesh_shapes(root):
        selection = om.MSelectionList()
        selection.add(shape)
        fn_mesh = om.MFnMesh(selection.getDagPath(0))
        points = fn_mesh.getPoints(om.MSpace.kWorld)
        samples = [
            {
                "index": index,
                "position": [float(point.x), float(point.y), float(point.z)],
            }
            for index, point in enumerate(points[:vertices_per_mesh])
        ]
        rows.append({"shape": shape, "vertexCount": len(points), "samples": samples})
    return {"meshes": rows}


def _observation(root: str, labels: Mapping[str, str], vertices_per_mesh: int) -> Dict[str, Any]:
    root_world = _matrix(root)
    joints: Dict[str, Any] = {}
    for label, joint in labels.items():
        world = _matrix(joint)
        joints[label] = {
            "joint": joint,
            "worldMatrix": _matrix_values(world),
            "translation": _translation(world),
        }
    return {
        "root": {
            "node": root,
            "worldMatrix": _matrix_values(root_world),
            "translation": _translation(root_world),
        },
        "joints": joints,
        "skin": _skin_observation(root),
        "mesh": _mesh_observation(root, vertices_per_mesh),
    }


def _skin_diff(before: Mapping[str, Any], after: Mapping[str, Any]) -> Dict[str, Any]:
    before_rows = {
        (row["skinCluster"], row["joint"], row["logicalIndex"]): row
        for row in before.get("rows", [])
    }
    after_rows = {
        (row["skinCluster"], row["joint"], row["logicalIndex"]): row
        for row in after.get("rows", [])
    }
    rows: List[Dict[str, Any]] = []
    for key in sorted(set(before_rows) | set(after_rows), key=str):
        left, right = before_rows.get(key, {}), after_rows.get(key, {})
        rows.append(
            {
                "skinCluster": key[0],
                "joint": key[1],
                "logicalIndex": key[2],
                "bindPreMatrixDistance": _matrix_distance(
                    left.get("bindPreMatrix", []), right.get("bindPreMatrix", [])
                ),
                "jointWorldMatrixDistance": _matrix_distance(
                    left.get("jointWorldMatrix", []), right.get("jointWorldMatrix", [])
                ),
                "skinProductDistance": _matrix_distance(
                    left.get("skinProduct", []), right.get("skinProduct", [])
                ),
            }
        )
    return {
        "rows": rows,
        "maxBindPreMatrixDistance": max((row["bindPreMatrixDistance"] for row in rows), default=0.0),
        "maxJointWorldMatrixDistance": max((row["jointWorldMatrixDistance"] for row in rows), default=0.0),
        "maxSkinProductDistance": max((row["skinProductDistance"] for row in rows), default=0.0),
        "topologyEqual": before.get("topology") == after.get("topology") and before.get("clusters") == after.get("clusters"),
    }


def _mesh_diff(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    expected_delta: Sequence[float] | None = None,
) -> Dict[str, Any]:
    before_rows = {row["shape"]: row for row in before.get("meshes", [])}
    after_rows = {row["shape"]: row for row in after.get("meshes", [])}
    rows: List[Dict[str, Any]] = []
    for shape in sorted(set(before_rows) | set(after_rows)):
        left_samples = {row["index"]: row["position"] for row in before_rows.get(shape, {}).get("samples", [])}
        right_samples = {row["index"]: row["position"] for row in after_rows.get(shape, {}).get("samples", [])}
        vertex_rows: List[Dict[str, Any]] = []
        for index in sorted(set(left_samples) & set(right_samples)):
            delta = _translation_delta(left_samples[index], right_samples[index])
            residual = _translation_delta(expected_delta or [0.0, 0.0, 0.0], delta)
            vertex_rows.append({"index": index, "delta": delta, "expectedDeltaResidual": residual})
        rows.append({"shape": shape, "vertices": vertex_rows})
    residuals = [
        abs(value)
        for row in rows
        for vertex in row["vertices"]
        for value in vertex["expectedDeltaResidual"]
    ]
    return {
        "rows": rows,
        "maxExpectedDeltaResidual": max(residuals, default=0.0),
        "topologyEqual": sorted(before_rows) == sorted(after_rows),
    }


def _move_diff(
    before: Mapping[str, Any], after: Mapping[str, Any], requested_delta: Sequence[float]
) -> Dict[str, Any]:
    before_root = before["root"]
    after_root = after["root"]
    actual_delta = _translation_delta(before_root["translation"], after_root["translation"])
    joint_rows: List[Dict[str, Any]] = []
    for label in sorted(set(before.get("joints", {})) | set(after.get("joints", {}))):
        left, right = before.get("joints", {}).get(label, {}), after.get("joints", {}).get(label, {})
        delta = _translation_delta(left.get("translation", []), right.get("translation", []))
        joint_rows.append(
            {
                "label": label,
                "joint": right.get("joint", left.get("joint")),
                "translationDelta": delta,
                "rootDeltaResidual": _translation_delta(actual_delta, delta),
                "worldMatrixDistance": _matrix_distance(left.get("worldMatrix", []), right.get("worldMatrix", [])),
            }
        )
    return {
        "requestedDelta": list(requested_delta),
        "actualRootDelta": actual_delta,
        "rootDeltaResidual": _translation_delta(requested_delta, actual_delta),
        "rootWorldMatrixDistance": _matrix_distance(before_root.get("worldMatrix", []), after_root.get("worldMatrix", [])),
        "joints": joint_rows,
        "skin": _skin_diff(before.get("skin", {}), after.get("skin", {})),
        "mesh": _mesh_diff(before.get("mesh", {}), after.get("mesh", {}), actual_delta),
    }


def _run(args: argparse.Namespace) -> Dict[str, Any]:
    pmx = _resolve(args.pmx)
    delta = _parse_delta(args.delta)
    scene_path = _resolve(str(Path(args.out).with_name(Path(args.out).stem + "_scene.ma")))
    report: Dict[str, Any] = {
        "status": "error",
        "probe": "TODO-ROOT-MOVE-SKIN-PARITY-1",
        "mayaVersion": str(cmds.about(version=True)),
        "pmx": str(pmx),
        "requestedRootDelta": delta,
        "rootZeroOrBakePerformed": False,
        "errors": [],
    }
    try:
        if not pmx.is_file():
            raise FileNotFoundError(f"Citlali PMX fixture not found: {pmx}")
        if args.vertices_per_mesh < 1:
            raise ValueError("--vertices-per-mesh must be >= 1")
        cmds.file(new=True, force=True)
        root = _import_model(pmx)
        labels = _joint_labels(root)
        report["modelRoot"] = root
        report["jointSelection"] = labels
        missing = [label for label in REQUIRED_JOINT_LABELS if label not in labels]
        if missing:
            raise RuntimeError(f"Required joints not found after production import: {missing}")
        before = _observation(root, labels, args.vertices_per_mesh)
        if not before["mesh"]["meshes"]:
            raise RuntimeError("Production import produced no renderable mesh shapes")
        cmds.xform(root, relative=True, worldSpace=True, translation=delta)
        moved = _observation(root, labels, args.vertices_per_mesh)
        move_diff = _move_diff(before, moved, delta)
        scene_path.parent.mkdir(parents=True, exist_ok=True)
        cmds.file(rename=str(scene_path))
        cmds.file(save=True, type="mayaAscii", force=True)
        cmds.file(str(scene_path), open=True, force=True)
        reopened_root = root if cmds.objExists(root) else None
        if reopened_root is None:
            raise RuntimeError("Scene reopen lost model root")
        reopened_labels = _joint_labels(reopened_root)
        reopened = _observation(reopened_root, reopened_labels, args.vertices_per_mesh)
        reopen_skin = _skin_diff(moved.get("skin", {}), reopened.get("skin", {}))
        reopen_mesh = _mesh_diff(moved.get("mesh", {}), reopened.get("mesh", {}))
        reopened_joint_residual = max(
            (
                _matrix_distance(
                    moved.get("joints", {}).get(label, {}).get("worldMatrix", []),
                    reopened.get("joints", {}).get(label, {}).get("worldMatrix", []),
                )
                for label in set(moved.get("joints", {})) & set(reopened.get("joints", {}))
            ),
            default=0.0,
        )
        report["observations"] = {"beforeMove": before, "afterMove": moved, "afterReopen": reopened}
        report["moveDiff"] = move_diff
        report["sceneReopen"] = {
            "scenePath": str(scene_path),
            "jointSelection": reopened_labels,
            "skin": reopen_skin,
            "mesh": reopen_mesh,
            "jointWorldMatrixMaxResidual": reopened_joint_residual,
            "rootWorldMatrixDistance": _matrix_distance(
                moved["root"]["worldMatrix"], reopened["root"]["worldMatrix"]
            ),
            "skinTopologyEqual": reopen_skin["topologyEqual"],
            "meshTopologyEqual": reopen_mesh["topologyEqual"],
        }
        report["status"] = "pass"
    except Exception as exc:
        report["errors"].append(str(exc))
    return report


def main() -> int:
    args = _parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass
    import maya.standalone

    try:
        maya.standalone.initialize(name="python")
    except RuntimeError:
        pass
    report_path = _resolve(args.out)
    try:
        _load_plugin()
        report = _run(args)
    except Exception as exc:
        report = {
            "status": "error",
            "probe": "TODO-ROOT-MOVE-SKIN-PARITY-1",
            "pmx": str(_resolve(args.pmx)),
            "errors": [str(exc)],
        }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    try:
        maya.standalone.uninitialize()
    except Exception:
        pass
    return 0 if report.get("status") == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
