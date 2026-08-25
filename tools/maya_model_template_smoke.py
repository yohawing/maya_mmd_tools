"""Validate packaged model templates in a real mayapy scene.

The smoke creates each curated template in a fresh scene, verifies strict
metadata read-back and SceneModelService discovery, and checks that the
generated cube has a root-joint skinCluster.  The semistandard template is
also edited, exported to PMX, freshly imported, and compared for bone and mesh
deformation metadata.  It is intentionally headless and does not require a
Maya GUI.
"""

from __future__ import annotations

import argparse
from dataclasses import replace
import json
from pathlib import Path
import tempfile
from typing import Any


def _roundtrip_semistandard(result: Any) -> dict[str, Any]:
    """Edit, export, and freshly import the semistandard template."""
    from maya import cmds

    from mmd_tools.adapters.maya_authoring_factory import build_maya_authoring_composition
    from mmd_tools.converters.authoring_export_bridge import project_authoring_spec
    from mmd_tools.converters.export_scene_collector import ExportSceneCollector
    from mmd_tools.core.mmd_parser import parse_pmx_file
    from mmd_tools.io.pmx_exporter import PmxExporter
    from mmd_tools.io.pmx_importer import import_pmx_file
    from mmd_tools.validation.export_validator import validate_model_data

    composition = build_maya_authoring_composition(cmds_module=cmds)
    current = composition.coordinator.read_spec(result.root)
    upper_body_2 = next(bone for bone in current.bones if bone.name == "上半身2")
    world = cmds.xform(
        upper_body_2.binding_identity,
        query=True,
        worldSpace=True,
        translation=True,
    )
    edited = composition.coordinator.replace_bone(
        result.root,
        replace(upper_body_2, name_english="Upper Body 2 Edited"),
        world,
    )
    oracle = ExportSceneCollector().collect_from_model_root(result.root)
    projected = project_authoring_spec(edited, oracle)
    validation = validate_model_data(projected, "pmx")
    if validation.is_blocking:
        raise RuntimeError(f"semistandard export validation failed: {validation.issues!r}")

    before_vertices = [tuple(vertex["position"]) for vertex in projected["vertices"]]
    with tempfile.TemporaryDirectory(prefix="mmd-template-roundtrip-") as temp_dir:
        output = Path(temp_dir) / "semistandard-edited.pmx"
        PmxExporter(native_parts_exporter=None).export_pmx_model(str(output), projected)
        parsed = parse_pmx_file(str(output), use_native_pmx_parse=False)
        if len(parsed.bones) != len(edited.bones):
            raise RuntimeError("semistandard PMX export changed the bone count")
        parsed_upper = next(bone for bone in parsed.bones if bone.name == "上半身2")
        if parsed_upper.name_english != "Upper Body 2 Edited":
            raise RuntimeError("semistandard PMX export lost the authoring edit")

        cmds.file(new=True, force=True)
        fresh_root = import_pmx_file(
            parsed,
            str(output),
            scale=1.0,
            options={"import_physics": False},
        )
        if not fresh_root:
            raise RuntimeError("semistandard fresh import did not return a model root")
        fresh_composition = build_maya_authoring_composition(cmds_module=cmds)
        fresh = fresh_composition.coordinator.read_spec(fresh_root)
        fresh_upper = next(bone for bone in fresh.bones if bone.name == "上半身2")
        if fresh_upper.name_english != "Upper Body 2 Edited":
            raise RuntimeError("semistandard fresh import lost the authoring edit")
        if len(fresh.bones) != len(edited.bones):
            raise RuntimeError("semistandard fresh import changed the bone count")
        fresh_payload = ExportSceneCollector().collect_from_model_root(fresh_root)
        after_vertices = [tuple(vertex["position"]) for vertex in fresh_payload["vertices"]]
        if len(before_vertices) != len(after_vertices) or any(
            any(abs(float(a) - float(b)) > 1e-5 for a, b in zip(before, after))
            for before, after in zip(before_vertices, after_vertices)
        ):
            raise RuntimeError("semistandard fresh import changed skinned mesh rest vertices")

    return {
        "edited_bone": "上半身2",
        "edited_name_english": "Upper Body 2 Edited",
        "exported_bone_count": len(parsed.bones),
        "fresh_import_bone_count": len(fresh.bones),
        "fresh_import_vertex_count": len(after_vertices),
    }


def _run_template(template_id: str) -> dict[str, Any]:
    from maya import cmds

    from mmd_tools.adapters.maya_model_template_initializer import MayaModelTemplateInitializer
    from mmd_tools.adapters.maya_cmds_adapter import MayaCmdsAdapter
    from mmd_tools.core.model_template import load_model_template
    from mmd_tools.services.scene_model_service import SceneModelService

    cmds.file(new=True, force=True)
    adapter = MayaCmdsAdapter(cmds_module=cmds)
    result = MayaModelTemplateInitializer(adapter).create(
        template_id,
        f"Smoke {template_id}",
        f"Smoke {template_id}",
    )
    listed = SceneModelService(cmds_adapter=adapter).list_mmd_models()
    joints = cmds.listRelatives(result.root, allDescendents=True, type="joint", fullPath=True) or []
    meshes = cmds.listRelatives(
        result.root,
        allDescendents=True,
        type="mesh",
        fullPath=True,
        noIntermediate=True,
    ) or []
    listed_root = result.root if result.root in listed else result.root.rsplit("|", 1)[-1]
    if len(listed) != 1 or listed_root not in listed:
        raise RuntimeError(f"{template_id}: SceneModelService listability failed: {listed!r}")
    if len(joints) != len(result.spec.bones):
        raise RuntimeError(f"{template_id}: expected {len(result.spec.bones)} joints, got {len(joints)}")
    for bone_index in (0, min(53, len(result.spec.bones) - 1), len(result.spec.bones) - 1):
        bone = next(item for item in result.spec.bones if item.index == bone_index)
        world = cmds.xform(bone.binding_identity, query=True, worldSpace=True, translation=True)
        expected = (bone.rest_position[0], bone.rest_position[1], -bone.rest_position[2])
        if any(abs(float(actual) - float(target)) > 1e-5 for actual, target in zip(world, expected)):
            raise RuntimeError(f"{template_id}: world rest mismatch for bone {bone_index}: {world!r} != {expected!r}")
    if len(meshes) != 1:
        raise RuntimeError(f"{template_id}: expected one cube mesh, got {meshes!r}")
    skin_clusters = [
        node for node in (cmds.listHistory(meshes[0]) or []) if cmds.nodeType(node) == "skinCluster"
    ]
    if len(skin_clusters) != 1:
        raise RuntimeError(f"{template_id}: expected one skinCluster, got {skin_clusters!r}")
    influences = cmds.skinCluster(skin_clusters[0], query=True, influence=True) or []
    root_joint = next(bone.binding_identity for bone in result.spec.bones if bone.index == 0)
    if root_joint not in influences and root_joint.rsplit("|", 1)[-1] not in influences:
        raise RuntimeError(f"{template_id}: cube is not skinned to root: {influences!r}")
    template = load_model_template(template_id)
    report = {
        "template_id": template_id,
        "root": result.root,
        "listed": listed,
        "bone_count": len(result.spec.bones),
        "material_count": len(result.spec.materials),
        "mesh": meshes[0],
        "skin_cluster": skin_clusters[0],
        "fingerprint": result.fingerprint,
        "template_fingerprint": template.spec.fingerprint(),
    }
    if template_id == "pmx20-semistandard-v1":
        report["roundtrip"] = _roundtrip_semistandard(result)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, help="optional UTF-8 JSON report path")
    args = parser.parse_args()
    import maya.standalone

    maya.standalone.initialize(name="python")
    from maya import cmds

    from tests.common.maya_plugin_setup import load_mmd_tools_plugin

    load_mmd_tools_plugin(Path(__file__).resolve().parents[1], cmds_module=cmds)
    results = [_run_template(template_id) for template_id in ("pmx20-semistandard-v1", "pmx20-basic-v1")]
    report = {"status": "pass", "templates": results}
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
