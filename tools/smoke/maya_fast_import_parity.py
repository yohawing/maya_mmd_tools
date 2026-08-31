"""Red-first Maya scene-contract gate for Python import versus C++ FastLoad.

The Python PMX importer is the canonical user-visible contract.  The C++ VP2
route may add one ``mmdRenderShape`` implementation node, but it must not
change the model's public hierarchy, geometry, deformers, materials, metadata,
or evaluated values.  This probe intentionally exits non-zero while parity is
incomplete and writes every mismatch to a JSON report.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import traceback
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _long_name(cmds: Any, node: str) -> str:
    values = cmds.ls(node, long=True) or []
    if not values:
        raise RuntimeError(f"node does not exist: {node}")
    return str(values[0])


def _relative_path(root: str, node: str) -> str:
    if node == root:
        return "<root>"
    prefix = root + "|"
    value = node[len(prefix):] if node.startswith(prefix) else node
    return "|".join(part.split(":")[-1] for part in value.split("|"))


def _safe_attr(cmds: Any, plug: str) -> Any:
    try:
        value = cmds.getAttr(plug)
    except Exception:
        return None
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)


def _mesh_signature(cmds: Any, om: Any, root: str, mesh: str) -> dict[str, Any]:
    selection = om.MSelectionList()
    selection.add(mesh)
    dag = selection.getDagPath(0)
    mesh_fn = om.MFnMesh(dag)
    normals = mesh_fn.getVertexNormals(True, om.MSpace.kObject)
    zero_normals = 0
    non_finite_normals = 0
    for normal in normals:
        length = math.sqrt(normal.x * normal.x + normal.y * normal.y + normal.z * normal.z)
        if not math.isfinite(length):
            non_finite_normals += 1
        elif length <= 1.0e-12:
            zero_normals += 1

    shading_groups = sorted(
        str(value).split(":")[-1]
        for value in (cmds.listConnections(mesh, type="shadingEngine") or [])
    )
    shaders: list[dict[str, Any]] = []
    for shading_group in shading_groups:
        for shader in cmds.listConnections(
            f"{shading_group}.surfaceShader", source=True, destination=False
        ) or []:
            shaders.append(
                {
                    "type": str(cmds.nodeType(shader)),
                    "materialIndex": _safe_attr(cmds, f"{shader}.mmd_material_index"),
                    "materialName": _safe_attr(cmds, f"{shader}.mmd_material_name"),
                    "materialNameEn": _safe_attr(cmds, f"{shader}.mmd_material_name_en"),
                }
            )
    return {
        "path": _relative_path(root, mesh),
        "vertices": int(cmds.polyEvaluate(mesh, vertex=True) or 0),
        "edges": int(cmds.polyEvaluate(mesh, edge=True) or 0),
        "faces": int(cmds.polyEvaluate(mesh, face=True) or 0),
        "uvs": int(cmds.polyEvaluate(mesh, uv=True) or 0),
        "zeroLengthVertexNormals": zero_normals,
        "nonFiniteVertexNormals": non_finite_normals,
        "intermediate": bool(cmds.getAttr(f"{mesh}.intermediateObject")),
        "shadingGroups": shading_groups,
        "shaders": sorted(shaders, key=lambda item: json.dumps(item, sort_keys=True)),
    }


def _blendshape_signature(cmds: Any, root: str, node: str) -> dict[str, Any]:
    aliases = cmds.aliasAttr(node, query=True) or []
    return {
        "name": str(node).split(":")[-1],
        "weightCount": int(cmds.blendShape(node, query=True, weightCount=True) or 0),
        "aliases": [str(aliases[index]) for index in range(0, len(aliases), 2)],
        "outputs": sorted(
            _relative_path(root, _long_name(cmds, value))
            for value in (cmds.listConnections(node, source=False, destination=True, type="mesh") or [])
            if cmds.objExists(value)
        ),
    }


def _snapshot(cmds: Any, om: Any, root_value: str) -> dict[str, Any]:
    root = _long_name(cmds, root_value)
    descendants = [
        str(value)
        for value in (cmds.listRelatives(root, allDescendents=True, fullPath=True) or [])
    ]
    dag_nodes = [root, *descendants]
    rows: list[dict[str, Any]] = []
    for node in dag_nodes:
        node_type = str(cmds.nodeType(node))
        row = {"path": _relative_path(root, node), "type": node_type}
        if cmds.attributeQuery("visibility", node=node, exists=True):
            row["visibility"] = bool(cmds.getAttr(f"{node}.visibility"))
        rows.append(row)

    meshes = sorted(
        (_mesh_signature(cmds, om, root, node) for node in dag_nodes if cmds.nodeType(node) == "mesh"),
        key=lambda item: item["path"],
    )
    history = set()
    for mesh in [node for node in dag_nodes if cmds.nodeType(node) == "mesh"]:
        history.update(cmds.listHistory(mesh, pruneDagObjects=True) or [])
    blend_shapes = sorted(
        (_blendshape_signature(cmds, root, node) for node in history if cmds.nodeType(node) == "blendShape"),
        key=lambda item: item["name"],
    )
    skin_clusters = sorted(
        {
            str(node).split(":")[-1]
            for node in history
            if cmds.nodeType(node) == "skinCluster"
        }
    )
    joints = sorted(
        _relative_path(root, node) for node in dag_nodes if cmds.nodeType(node) == "joint"
    )
    target_dags = sorted(
        row["path"]
        for row in rows
        if row["type"] in {"transform", "mesh"} and "_target" in row["path"]
    )
    implementation_nodes = sorted(
        row["path"] for row in rows if row["type"] == "mmdRenderShape"
    )
    semantic_dag = [row for row in rows if row["type"] != "mmdRenderShape"]
    root_attrs = {}
    for attr in cmds.listAttr(root, userDefined=True) or []:
        if str(attr).startswith("mmd_"):
            root_attrs[str(attr)] = _safe_attr(cmds, f"{root}.{attr}")
    return {
        "root": root,
        "dag": semantic_dag,
        "dagTypeCounts": dict(sorted(Counter(row["type"] for row in semantic_dag).items())),
        "implementationNodes": implementation_nodes,
        "targetDags": target_dags,
        "meshes": meshes,
        "blendShapes": blend_shapes,
        "skinClusters": skin_clusters,
        "joints": joints,
        "rootMmdAttrs": root_attrs,
    }


def _run_import(cmds: Any, om: Any, model: Path, route: str, scale: float) -> dict[str, Any]:
    cmds.file(new=True, force=True)
    from mmd_tools.io.mmd_importer import import_mmd_file

    common = {
        "scale": scale,
        "import_physics": False,
        "import_morphs": True,
        "create_mmd_shaders": False,
        "create_mmd_control_rig": False,
        "custom_namespace": f"parity_{route}",
    }
    if route == "python":
        common["use_cpp_fast_load"] = False
    else:
        common.update(
            {
                "use_cpp_fast_load": True,
                "cpp_fast_load_mesh_only": False,
                "use_cpp_vp2_ownership": True,
            }
        )
    root = import_mmd_file(str(model), options=common)
    if not root:
        raise RuntimeError(f"{route} import returned no root")
    return _snapshot(cmds, om, str(root))


def _compare(python: dict[str, Any], cpp: dict[str, Any]) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []

    def exact(name: str, left: Any, right: Any) -> None:
        checks.append({"name": name, "pass": left == right, "python": left, "cpp": right})

    def empty(name: str, route: str, actual: Any) -> None:
        checks.append(
            {"name": name, "pass": actual == [], "route": route, "actual": actual, "expected": []}
        )

    empty("no-python-temporary-target-dags", "python", python["targetDags"])
    empty("no-cpp-temporary-target-dags", "cpp", cpp["targetDags"])
    exact("dag-type-counts", python["dagTypeCounts"], cpp["dagTypeCounts"])
    exact("dag-hierarchy", python["dag"], cpp["dag"])
    exact("mesh-topology-materials", python["meshes"], cpp["meshes"])
    exact("blendshape-contract", python["blendShapes"], cpp["blendShapes"])
    exact("skin-cluster-contract", python["skinClusters"], cpp["skinClusters"])
    exact("joint-hierarchy", python["joints"], cpp["joints"])
    exact("root-metadata", python["rootMmdAttrs"], cpp["rootMmdAttrs"])
    exact(
        "python-zero-length-normals",
        sum(mesh["zeroLengthVertexNormals"] for mesh in python["meshes"]),
        0,
    )
    exact(
        "cpp-zero-length-normals",
        sum(mesh["zeroLengthVertexNormals"] for mesh in cpp["meshes"]),
        0,
    )
    return checks


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-json", type=Path)
    parser.add_argument("--model", type=Path)
    parser.add_argument("--plugin", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--scale", type=float, default=1.0)
    args = parser.parse_args()
    if args.input_json:
        document = json.loads(args.input_json.read_text(encoding="utf-8"))
        args.model = Path(document["model"])
        args.plugin = Path(document["plugin"])
        args.report = Path(document["report"])
        args.scale = float(document.get("scale", args.scale))
    if args.model is None or args.plugin is None or args.report is None:
        parser.error("provide --input-json or all of --model/--plugin/--report")
    report: dict[str, Any] = {
        "schemaVersion": 1,
        "status": "red",
        "model": str(args.model.resolve()),
        "plugin": str(args.plugin.resolve()),
        "checks": [],
        "errors": [],
    }

    import maya.standalone
    maya.standalone.initialize(name="python")
    try:
        import maya.cmds as cmds
        from maya.api import OpenMaya as om

        os.environ["PATH"] = str(args.plugin.parent) + os.pathsep + os.environ.get("PATH", "")
        cmds.loadPlugin(str(ROOT / "plug-ins" / "mmd_tools_plugin.py"), quiet=True)
        cmds.loadPlugin(str(args.plugin.resolve()), quiet=True)
        report["python"] = _run_import(cmds, om, args.model.resolve(), "python", args.scale)
        report["cpp"] = _run_import(cmds, om, args.model.resolve(), "cpp", args.scale)
        report["checks"] = _compare(report["python"], report["cpp"])
        report["status"] = "green" if all(check["pass"] for check in report["checks"]) else "red"
    except Exception as exc:
        report["errors"].append(str(exc))
        report["traceback"] = traceback.format_exc()
    finally:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print("FAST_IMPORT_PARITY_REPORT=" + json.dumps(report, ensure_ascii=False), flush=True)
        maya.standalone.uninitialize()
    return 0 if report["status"] == "green" else 1


if __name__ == "__main__":
    raise SystemExit(main())
