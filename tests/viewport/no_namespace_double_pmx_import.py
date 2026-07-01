"""Headless mayapy E2E for no-namespace double PMX import.

Run with mayapy. The harness imports one PMX twice with use_namespace=False and
checks that duplicate center-like joints remain addressable under each model.
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path


def _emit(payload, log_path=None):
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    print(text)
    if log_path:
        with open(log_path, "a", encoding="utf-8", errors="replace") as handle:
            handle.write(text + "\n")


def _initialize_maya():
    import maya.standalone

    try:
        maya.standalone.initialize(name="python")
        return True
    except RuntimeError:
        return False


def _joint_leaf_name(joint):
    return joint.rsplit("|", 1)[-1].rsplit(":", 1)[-1]


def _joint_mmd_name(cmds, joint):
    from mmd_tools.core.constants import ATTR_MMD_BONE_NAME

    try:
        if cmds.attributeQuery(ATTR_MMD_BONE_NAME, node=joint, exists=True):
            return cmds.getAttr(f"{joint}.{ATTR_MMD_BONE_NAME}") or ""
    except Exception:
        pass
    return ""


def _find_center_joint(cmds, root):
    descendants = cmds.listRelatives(root, allDescendents=True, type="joint", fullPath=True) or []
    candidates = []
    for joint in descendants:
        leaf = _joint_leaf_name(joint).lower()
        mmd_name = _joint_mmd_name(cmds, joint)
        if leaf.startswith("center") or mmd_name in {"center", "センター"}:
            candidates.append(joint)
    if not candidates:
        raise AssertionError(f"No center-like joint found under {root}")
    candidates.sort(key=lambda item: item.count("|"))
    return candidates[0]


def _world_translation(cmds, node):
    return [round(float(value), 6) for value in cmds.xform(node, query=True, worldSpace=True, translation=True)]


def run(model_a, model_b=None, log_path=None):
    import maya.cmds as cmds

    project_root = Path(__file__).resolve().parents[2]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    from mmd_tools.core import settings
    from mmd_tools.io.mmd_importer import import_mmd_file

    model_b = model_b or model_a
    cmds.file(new=True, force=True)
    settings.set("import.general.use_namespace", False)

    options = {
        "use_namespace": False,
        "import_physics": False,
        "scale": 1.0,
    }
    root_a = import_mmd_file(str(model_a), options=options)
    root_b = import_mmd_file(str(model_b), options=options)
    if not root_a or not root_b:
        raise AssertionError(f"PMX import failed: root_a={root_a!r}, root_b={root_b!r}")

    center_a = _find_center_joint(cmds, root_a)
    center_b = _find_center_joint(cmds, root_b)
    if not cmds.objExists(center_a):
        raise AssertionError(f"First center joint has stale path: {center_a}")
    if not cmds.objExists(center_b):
        raise AssertionError(f"Second center joint has stale path: {center_b}")
    if center_a == center_b:
        raise AssertionError(f"Duplicate import reused the same center joint path: {center_a}")

    parent_a = cmds.listRelatives(center_a, parent=True, fullPath=True) or []
    parent_b = cmds.listRelatives(center_b, parent=True, fullPath=True) or []
    result = {
        "status": "pass",
        "root_a": root_a,
        "root_b": root_b,
        "center_a": center_a,
        "center_b": center_b,
        "center_a_parent": parent_a[0] if parent_a else "",
        "center_b_parent": parent_b[0] if parent_b else "",
        "center_a_world": _world_translation(cmds, center_a),
        "center_b_world": _world_translation(cmds, center_b),
        "joint_count": len(cmds.ls(type="joint") or []),
    }
    _emit(result, log_path)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-a", "--model", dest="model_a", required=True)
    parser.add_argument("--model-b")
    parser.add_argument("--log")
    args = parser.parse_args()

    initialized = False
    try:
        initialized = _initialize_maya()
        result = run(Path(args.model_a).resolve(), Path(args.model_b).resolve() if args.model_b else None, args.log)
        return 0 if result["status"] == "pass" else 1
    except Exception:
        _emit({"status": "error", "traceback": traceback.format_exc()}, args.log)
        return 1
    finally:
        if initialized:
            try:
                import maya.standalone

                maya.standalone.uninitialize()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
