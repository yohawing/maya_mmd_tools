"""Validate packaged model templates in a real mayapy scene.

The smoke creates each curated template in a fresh scene, verifies strict
metadata read-back and SceneModelService discovery, and checks that the
generated cube has a root-joint skinCluster.  It is intentionally headless and
does not require a Maya GUI.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


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
    return {
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, help="optional UTF-8 JSON report path")
    args = parser.parse_args()
    import maya.standalone

    maya.standalone.initialize(name="python")
    results = [_run_template(template_id) for template_id in ("pmx20-semistandard-v1", "pmx20-basic-v1")]
    report = {"status": "pass", "templates": results}
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
