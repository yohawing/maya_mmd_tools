"""Measure TestModel physics, reset repeatability and native gravity in Maya."""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import sys
import traceback

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
MARKER = "TEST_MODEL_PHYSICS_COMPLETE"


def run_probe(config_path):
    from maya import cmds
    from maya.api import OpenMaya as om
    from mmd_tools.io.mmd_importer import import_mmd_file
    from mmd_tools.core.mmd_parser import parse_pmx_file
    from mmd_tools.core.native.mmd_anim_runtime_handles import (
        MmdRuntimeModel, MmdRuntimeInstance, MmdRuntimePhysicsWorld,
    )
    from tests.release.test_native_physics_collision import _make_rigidbody_desc

    config = json.loads(Path(config_path).read_text(encoding="utf-8"))
    out = Path(config["out"])
    report = {"status": "fail", "cases": []}
    try:
        cmds.loadPlugin(config["plugin"], quiet=True)
        cmds.loadPlugin(str(ROOT / "plug-ins/mmd_tools_plugin.py"), quiet=True)
        model_path = ROOT / "tests/data/yw_test_model.pmx"
        pmx = parse_pmx_file(str(model_path), use_native_pmx_parse=False)
        report["model"] = str(model_path)
        report["bodies"] = len(pmx.rigid_bodies)
        report["dynamicBodies"] = sum(body.physics_mode != 0 for body in pmx.rigid_bodies)
        head_index = max((i for i, body in enumerate(pmx.rigid_bodies) if body.shape_type == 0),
                         key=lambda i: pmx.rigid_bodies[i].size[0])

        def hair_head_penetration(states):
            """Measure the actual TestModel capsule/sphere geometry in PMX space."""
            head = pmx.rigid_bodies[head_index]
            center = om.MVector(*states[head_index][0])
            maximum = 0.0
            for index, body in enumerate(pmx.rigid_bodies):
                if body.physics_mode == 0 or body.shape_type != 2:
                    continue
                if not (body.collision_mask & (1 << head.group) and head.collision_mask & (1 << body.group)):
                    continue
                position, rotation = states[index]
                axis = om.MVector(0, 1, 0).rotateBy(om.MQuaternion(*rotation))
                delta = center - om.MVector(*position)
                along = max(-body.size[1] / 2, min(body.size[1] / 2, delta * axis))
                distance = (delta - axis * along).length()
                maximum = max(maximum, body.size[0] + head.size[0] - distance)
            return maximum
        cmds.file(new=True, force=True)
        root = import_mmd_file(str(model_path), options={"import_physics": True, "setup_rig": True})
        assert root
        solver, = cmds.ls(type="mmdPhysicsSolver")
        world, = cmds.ls(type="mmdPhysicsWorldShape")
        selection = om.MSelectionList()
        selection.add(solver)
        node = om.MFnDependencyNode(selection.getDependNode(0)).userNode()
        mesh, = [shape for shape in cmds.listRelatives(root, allDescendents=True, type="mesh", fullPath=True)
                 if not cmds.getAttr(shape + ".intermediateObject")]
        mesh_selection = om.MSelectionList()
        mesh_selection.add(mesh)
        mesh_fn = om.MFnMesh(mesh_selection.getDagPath(0))
        cmds.setAttr(world + ".enable", False)
        for frame in (0, 1, 30):
            cmds.currentTime(frame)
            assert cmds.getAttr(solver + ".outStatus") == "disabled"
        report["offInitialized"] = node._initialized
        assert not node._initialized
        default_gravity = cmds.getAttr(world + ".gravity")[0]
        report["sceneGravity"] = default_gravity
        cmds.setAttr(world + ".enable", True)
        for fps, unit in ((30, "ntsc"), (60, "ntscf")):
            cmds.currentUnit(time=unit)
            for gravity in (default_gravity, (0.0, -98.0, 0.0)):
                cmds.setAttr(world + ".gravity", *gravity)
                runs = []
                rotation_runs = []
                penetrations = []
                for repeat in range(2):
                    generation = cmds.getAttr(world + ".resetGeneration")
                    cmds.setAttr(world + ".resetGeneration", generation + 1)
                    samples = []
                    rotations = []
                    for frame in range(fps + 1):
                        cmds.currentTime(frame)
                        status = cmds.getAttr(solver + ".outStatus")
                        assert status in ("reset", "stepped", "cached", "pose-updated"), status
                        states = node._world.copy_rigidbody_states()
                        assert states and all(math.isfinite(v) for pos, quat in states for v in (*pos, *quat))
                        mesh_fn.getPoints(om.MSpace.kWorld)
                        after_mesh = node._world.copy_rigidbody_states()
                        same_frame_delta = max(abs(a-b) for (pa, qa), (pb, qb) in zip(states, after_mesh)
                                               for a, b in zip((*pa, *qa), (*pb, *qb)))
                        assert same_frame_delta < 1e-6, (frame, same_frame_delta)
                        samples.append([list(pos) for pos, _ in states])
                        rotations.append([list(quat) for _, quat in states])
                        penetrations.append(hair_head_penetration(states))
                    runs.append(samples)
                    rotation_runs.append(rotations)
                drift = max(abs(a-b) for fa, fb in zip(*runs) for pa, pb in zip(fa, fb) for a, b in zip(pa, pb))
                rotation_drift = max(min(max(abs(a-b) for a, b in zip(qa, qb)),
                                         max(abs(a+b) for a, b in zip(qa, qb)))
                                     for fa, fb in zip(*rotation_runs) for qa, qb in zip(fa, fb))
                report["cases"].append({"fps": fps, "gravity": list(gravity),
                                        "repeatMaxDelta": drift, "positions": runs[0],
                                        "repeatMaxQuaternionDelta": rotation_drift, "rotations": rotation_runs[0],
                                        "maxHairHeadPenetration": max(penetrations),
                                        "nativeGravity": node._world.get_gravity()})
        report["cycles"] = cmds.cycleCheck(all=True, list=True) or []
        motion = ROOT / "tests/data/yw_test_model_control_rig_vmd.vmd"
        assert import_mmd_file(str(motion), options={"target_model": root, "bake_mode": False})
        cmds.setAttr(world + ".enable", True)
        motion_samples = []
        for frame in range(61):
            cmds.currentTime(frame)
            mesh_fn.getPoints(om.MSpace.kWorld)
            status = cmds.getAttr(solver + ".outStatus")
            assert status in ("reset", "stepped", "cached", "pose-updated"), status
            motion_samples.append({"frame": frame, "status": status,
                                   "hairHeadPenetration": hair_head_penetration(node._world.copy_rigidbody_states()),
                                   "positions": [list(pos) for pos, _ in node._world.copy_rigidbody_states()]})
        report["motion"] = {"path": str(motion), "samples": motion_samples}
        cmds.file(rename=str(out / "testmodel-physics.ma"))
        cmds.file(save=True, type="mayaAscii", force=True)
        # Isolate acceleration from joints/damping using TestModel's runtime model.
        model = MmdRuntimeModel.from_pmx_bytes(model_path.read_bytes())
        instance = MmdRuntimeInstance.for_model(model)
        probe_world = None
        try:
            instance.set_physics_mode(1)
            instance.evaluate_rest_pose()
            # Sphere, dynamic, unbound; all other source-model settings remain irrelevant.
            desc = _make_rigidbody_desc(0, (0.5, 0, 0), (0, 100, 0), 1, mass=1)
            report["freeFall"] = []
            for gravity in (None, default_gravity):
                probe_world = MmdRuntimePhysicsWorld.from_descriptors([desc], [])
                if gravity is not None:
                    assert probe_world.set_gravity(gravity)
                actual_gravity = probe_world.get_gravity()
                probe_world.reset(instance)
                for _ in range(60):
                    assert probe_world.step_runtime(instance, 1.0 / 60.0) is not None
                y = probe_world.copy_rigidbody_states()[0][0][1]
                report["freeFall"].append({"gravity": actual_gravity, "dropAfterOneSecond": 100-y})
                probe_world.free()
                probe_world = None
        finally:
            if probe_world is not None:
                probe_world.free()
            instance.free()
            model.free()
        report["checks"] = {
            "defaultGravityMatchesNative": all(abs(a-b) < 1e-4 for a, b in
                                               zip(default_gravity, report["freeFall"][0]["gravity"])),
            "repeatWithinTolerance": all(max(row["repeatMaxDelta"], row["repeatMaxQuaternionDelta"]) < 1e-5
                                         for row in report["cases"]),
            "noCycles": not report["cycles"],
        }
        report["status"] = "pass" if all(report["checks"].values()) else "fail"
        if report["status"] == "fail":
            report["error"] = "Failed checks: " + ", ".join(k for k, v in report["checks"].items() if not v)
    except Exception:
        report["error"] = traceback.format_exc()
    finally:
        (out / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        (out / "probe.log").write_text(MARKER + "\n", encoding="utf-8")
        cmds.quit(force=True)


def main():
    from tests.viewport.maya_e2e_harness import run_maya_e2e

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--maya", default="2024")
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--port", type=int, default=7797)
    args = parser.parse_args()
    out = args.out_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    plugin = ROOT / f"plug-ins/{args.maya}/Release/mmd_tools_cpp.mll"
    config = out / "input.json"
    config.write_text(json.dumps({"plugin": str(plugin), "out": str(out)}), encoding="utf-8")
    result = run_maya_e2e(
        project_root=ROOT, version=args.maya, out_dir=out, port=args.port, timeout=600,
        log_path=out / "probe.log", report_path=out / "report.json", marker=MARKER,
        command=("from maya import cmds\nfrom tools.probes.test_model_physics import run_probe\n"
                 f"cmds.evalDeferred(lambda: run_probe({str(config)!r}), lowestPriority=True)"),
        send_label="test-model-physics", stale_paths=(out / "probe.log", out / "report.json"),
        env_overrides={"MAYA_VP2_DEVICE_OVERRIDE": "VirtualDeviceDx11", "MMD_TOOLS_CPP_PLUGIN": str(plugin),
                       "PATH": str(plugin.parent) + os.pathsep + os.environ.get("PATH", "")},
    )
    print(json.dumps({"status": result["status"], "error": result.get("error")}))
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
