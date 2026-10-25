"""Verify public skeleton reads against a real PMX in an isolated Maya GUI."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import traceback

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
MARKER = "//-- PUBLIC_API_SMOKE_DONE --//"


def run_check(config_path):
    """Compare imported metadata and bind poses without touching user scenes."""
    from maya import cmds

    config = json.loads(Path(config_path).read_text(encoding="utf-8"))
    output = Path(config["out"])
    report = {"status": "fail", "checks": {}, "errors": [], "model": config["model"]}

    def check(name, condition, detail=None):
        report["checks"][name] = {"pass": bool(condition), "detail": detail}
        if not condition:
            raise AssertionError(name + ": " + repr(detail))

    def matrix(joint):
        return tuple(cmds.xform(joint, query=True, worldSpace=True, matrix=True))

    def snapshot(joints):
        return {
            "nodes": sorted(cmds.ls(uuid=True) or []),
            "selection": cmds.ls(selection=True, long=True) or [],
            "time": cmds.currentTime(query=True),
            "modified": cmds.file(query=True, modified=True),
            "undo": cmds.undoInfo(query=True, undoName=True),
            "matrices": {joint: matrix(joint) for joint in joints},
            "connections": {joint: sorted(cmds.listConnections(
                joint, source=True, destination=True, plugs=True, connections=True,
            ) or []) for joint in joints},
        }

    try:
        report["maya"] = cmds.about(version=True)
        cmds.loadPlugin(str(ROOT / "mmd_tools/plugin_main.py"), quiet=True)
        from mmd_tools.services.scene_model_service import SceneModelService
        from mmd_tools.core.mmd_parser import parse_pmx_file
        from mmd_tools.io.mmd_importer import import_mmd_file

        pmx = parse_pmx_file(config["model"])
        cmds.file(new=True, force=True)
        root = import_mmd_file(config["model"], scale=1.0, options={
            "setup_rig": False, "setup_bone_orientation": False,
            "import_physics": False, "create_mmd_shaders": False,
            "use_namespace": True, "custom_namespace": "public_api_fixture",
        })
        root = cmds.ls(root, long=True)[0]
        joints = cmds.listRelatives(root, allDescendents=True, type="joint", fullPath=True) or []
        cmds.select(root, replace=True)
        before = snapshot(joints)
        scene = SceneModelService()
        models = scene.list_mmd_models()
        skeleton = scene.get_model_skeleton(root)
        check("initial_reads_preserve_scene", before == snapshot(joints))
        check("model_list", isinstance(models, list) and root in models, models)
        check("canonical_root", skeleton["model_root"] == root, skeleton["model_root"])
        bones = skeleton["bones"]
        check("bone_count", len(bones) == len(pmx.bones), len(bones))
        check("bone_ids", [bone["index"] for bone in bones] == list(range(len(pmx.bones))))
        check("namespaced_joints", all(":" in bone["joint"].rsplit("|", 1)[-1] for bone in bones))
        metadata_matches = all(
            bone["name"] == source.name
            and bone["name_english"] == source.name_english
            and bone["parent_index"] == source.parent_bone_index
            and tuple(bone["pmx_rest_position"]) == tuple(source.position)
            for bone, source in zip(bones, pmx.bones)
        )
        check("pmx_identity_and_rest_positions", metadata_matches)
        check("bind_matrix_shape", all(len(bone["bind_world_matrix"]) == 16 for bone in bones))
        bind_error = max(abs(a - b) for bone in bones
                         for a, b in zip(bone["bind_world_matrix"], matrix(bone["joint"])))
        check("imported_bind_matches_world", bind_error < 1e-6, bind_error)

        animated_joint = next(bone["joint"] for bone in bones
                              if cmds.getAttr(bone["joint"] + ".translateX", settable=True))
        initial = cmds.getAttr(animated_joint + ".translateX")
        cmds.setKeyframe(animated_joint, attribute="translateX", time=0, value=initial)
        cmds.setKeyframe(animated_joint, attribute="translateX", time=10, value=initial + 2.0)
        cmds.currentTime(10)
        before = snapshot(joints)
        changed_pose = scene.get_model_skeleton(root)
        repeated_models = scene.list_mmd_models()
        check("animated_reads_preserve_scene", before == snapshot(joints))
        check("animated_model_list", repeated_models == models)
        check("saved_bind_is_pose_independent", changed_pose == skeleton)
        original_bind = next(bone["bind_world_matrix"] for bone in bones if bone["joint"] == animated_joint)
        pose_delta = max(abs(a - b) for a, b in zip(original_bind, matrix(animated_joint)))
        check("live_pose_changed", pose_delta > 1.0, pose_delta)
        report["status"] = "pass"
    except Exception:
        report["errors"].append(traceback.format_exc())
    finally:
        (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        (output / "run.log").write_text(json.dumps(report, ensure_ascii=False) + "\n" + MARKER + "\n", encoding="utf-8")


def main():
    """Use the shared background launcher and its normal-exit gate."""
    from tests.viewport.maya_e2e_harness import run_maya_e2e

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--maya", default="2024")
    parser.add_argument("--port", type=int, default=7743)
    parser.add_argument("--model", default=str(ROOT / "tests/data/mmt_test_model.pmx"))
    parser.add_argument("--out", default=str(ROOT / "build/reports/public-api-smoke"))
    parser.add_argument("--timeout", type=float, default=300)
    args = parser.parse_args()
    output = Path(args.out).resolve()
    output.mkdir(parents=True, exist_ok=True)
    config = output / "config.json"
    config.write_text(json.dumps({"model": str(Path(args.model).resolve()), "out": str(output)}, ensure_ascii=False), encoding="utf-8")
    command = "import sys\nsys.path.insert(0, {!r})\nfrom tools.maya_gui.public_api_smoke import run_check\nrun_check({!r})".format(str(ROOT), str(config))
    report = run_maya_e2e(
        project_root=ROOT, version=args.maya, out_dir=output, port=args.port,
        timeout=args.timeout, log_path=output / "run.log", report_path=output / "report.json",
        command=command, marker=MARKER, send_label="public-api-smoke",
        stale_paths=[output / "run.log", output / "report.json"],
        env_overrides={"MAYA_SKIP_USERSETUP_PY": "1"},
    )
    print(json.dumps(report, indent=2, ensure_ascii=True))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
