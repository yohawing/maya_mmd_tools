"""Compare HumanIK setup/target/restore for normal and arm-IK PMX variants."""

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import traceback

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.viewport.maya_e2e_harness import run_maya_e2e  # noqa: E402
from tools.render_override.common import require_requested_plugin  # noqa: E402

MARKER = "HUMANIK ARM VARIANT FINISHED"


def snapshot(cmds, root):
    """Capture all imported joints, including writers outside HIK assignments."""
    result = {}
    for joint in sorted(cmds.listRelatives(root, allDescendents=True, type="joint", fullPath=True) or []):
        connections = cmds.listConnections(joint, source=True, destination=False,
                                           plugs=True, connections=True) or []
        result[joint] = {
            "matrix": list(cmds.xform(joint, query=True, worldSpace=True, matrix=True)),
            "inputs": sorted(zip(connections[::2], connections[1::2])),
        }
    if not result:
        raise RuntimeError("Imported model has no joints")
    return result


def compare(before, after):
    """Keep restoration failures separate from setup and target failures."""
    changed = []
    maximum = 0.0
    for joint in sorted(set(before) | set(after)):
        left, right = before.get(joint), after.get(joint)
        if left is None or right is None:
            changed.append({"joint": joint, "missing": True})
            continue
        if (len(left["matrix"]) != 16 or len(right["matrix"]) != 16
                or not all(math.isfinite(value) for value in left["matrix"] + right["matrix"])):
            changed.append({"joint": joint, "invalidMatrix": True})
            continue
        delta = max(abs(a - b) for a, b in zip(left["matrix"], right["matrix"]))
        maximum = max(maximum, delta)
        # Pose deltas are diagnostic; topology restoration is the acceptance gate.
        if left["inputs"] != right["inputs"]:
            changed.append({"joint": joint, "matrixDelta": delta,
                            "inputsBefore": left["inputs"], "inputsAfter": right["inputs"]})
    return {"status": "fail" if changed else "pass", "jointCount": len(before),
            "maxMatrixDelta": maximum, "changed": changed}


def probe(config_path):
    """Run both variants in fresh scenes and always attempt production restore."""
    from maya import cmds, mel
    from mmd_tools.core.humanik_frontend import HumanIkFrontendSession
    from mmd_tools.core.humanik_utils import mel_string
    from mmd_tools.io.mmd_importer import import_mmd_file

    config = json.loads(Path(config_path).read_text(encoding="utf-8"))
    out = Path(config["output"])
    report = {"status": "fail", "conditions": config, "cases": []}
    try:
        plugin = require_requested_plugin(cmds, config["plugin"], print)
        report["pluginSha256"] = hashlib.sha256(plugin.read_bytes()).hexdigest()
        cmds.loadPlugin(str(ROOT / "plug-ins/mmd_tools_plugin.py"), quiet=True)
        for label in ("normal", "arm"):
            cmds.file(new=True, force=True)
            session = HumanIkFrontendSession()
            case = {"variant": label, "status": "fail", "setup": {}, "target": "not_run",
                    "cyclesByStage": {}}
            report["cases"].append(case)
            roots, before = {}, {}
            try:
                for role, path in (("source", config["normal"]), ("target", config[label])):
                    root = import_mmd_file(path, options={"use_namespace": True,
                                           "custom_namespace": role, "setup_rig": True,
                                           "import_physics": False})
                    if not root:
                        raise RuntimeError(f"Import failed: {role}")
                    roots[role] = str(root)
                    cmds.currentTime(0, edit=True)
                    before[role] = snapshot(cmds, root)
                case["roots"] = roots
                case["cyclesByStage"]["import"] = cmds.cycleCheck(all=True, list=True) or []
                case["targetOwnershipBeforeSetup"] = session.inspect_target_ownership(roots["target"], profile="full")
                for role, root in roots.items():
                    try:
                        binding = session.setup_and_characterize(root, profile="full")
                        case["setup"][role] = {"status": "pass", "binding": binding.to_dict()}
                    except Exception:
                        case["setup"][role] = {"status": "fail", "error": traceback.format_exc()}
                        pending = session._pending_stances.get(root)
                        if pending is not None:
                            case["setup"][role]["stance"] = pending.to_dict()
                case["cyclesByStage"]["setup"] = cmds.cycleCheck(all=True, list=True) or []
                if all(row["status"] == "pass" for row in case["setup"].values()):
                    try:
                        session.enter_source_mode(roots["source"])
                        session.enter_target_mode(roots["target"])
                        case["capturedInputType"] = session.active_preview.restore_state.input_type
                        cmds.currentTime(1, edit=True)
                        snapshot(cmds, roots["target"])
                        case["target"] = "pass"
                    except Exception:
                        case["target"] = "fail"
                        case["targetError"] = traceback.format_exc()
                case["cyclesByStage"]["target"] = cmds.cycleCheck(all=True, list=True) or []
                case["diagnostics"] = session.diagnostics(roots["target"])
            except Exception:
                case["error"] = traceback.format_exc()
            finally:
                try:
                    session.restore_mmd_rig()
                    case["restoreCall"] = "pass"
                except Exception:
                    case["restoreCall"] = "fail"
                    case["restoreError"] = traceback.format_exc()
                cmds.currentTime(0, edit=True)
                case["restoration"] = {role: compare(before[role], snapshot(cmds, root))
                                       for role, root in roots.items() if role in before}
                case["cycles"] = cmds.cycleCheck(all=True, list=True) or []
                case["cyclesByStage"]["restore"] = case["cycles"]
                cmds.file(rename=str(out / (label + ".mb")))
                cmds.file(save=True, type="mayaBinary", force=True)
            if (case["target"] == "pass" and case["restoreCall"] == "pass"
                    and len(case["restoration"]) == 2
                    and all(row["status"] == "pass" for row in case["restoration"].values())
                    and not any(case["cyclesByStage"].values())
                    and "error" not in case):
                case["status"] = "pass"
                # Exercise reuse and the rollback immediately after HIK has
                # acquired its writer connections, before a preview is returned.
                from unittest.mock import patch
                from mmd_tools.core import humanik_preview

                def restored_state():
                    cmds.currentTime(0, edit=True)
                    poses = {role: compare(before[role], snapshot(cmds, root))
                             for role, root in roots.items()}
                    cycles = cmds.cycleCheck(all=True, list=True) or []
                    return {"status": "pass" if not cycles and all(
                        row["status"] == "pass" for row in poses.values()) else "fail",
                        "restoration": poses, "cycles": cycles}

                try:
                    session.enter_target_mode(roots["target"])
                    cmds.currentTime(1, edit=True)
                    snapshot(cmds, roots["target"])
                    session.restore_mmd_rig()
                    case["repeat"] = restored_state()
                    connect = humanik_preview.connect_humanik_source

                    def fail_after_connect(*args, **kwargs):
                        connect(*args, **kwargs)
                        raise RuntimeError("injected post-connect failure")

                    with patch.object(humanik_preview, "connect_humanik_source", fail_after_connect):
                        try:
                            session.enter_target_mode(roots["target"])
                        except RuntimeError as error:
                            if str(error) != "injected post-connect failure":
                                raise
                        else:
                            raise AssertionError("injected failure did not occur")
                    case["rollback"] = restored_state()
                    # Reproduce the legacy empty-source retarget state too.
                    def fail_after_empty_source(*args, **kwargs):
                        connect(*args, **kwargs)
                        mel.eval(f'hikSetCharacterInput({mel_string(args[0])}, "");')
                        raise RuntimeError("injected empty-source failure")

                    with patch.object(humanik_preview, "connect_humanik_source", fail_after_empty_source):
                        try:
                            session.enter_target_mode(roots["target"])
                        except RuntimeError as error:
                            if str(error) != "injected empty-source failure":
                                raise
                        else:
                            raise AssertionError("empty-source failure did not occur")
                    case["emptySourceRollback"] = restored_state()
                    if case["repeat"]["status"] != "pass" or case["rollback"]["status"] != "pass":
                        case["status"] = "fail"
                    if case["emptySourceRollback"]["status"] != "pass":
                        case["status"] = "fail"
                except Exception:
                    case["status"] = "fail"
                    case["lifecycleError"] = traceback.format_exc()
                finally:
                    session.restore_mmd_rig()
        if len(report["cases"]) == 2 and all(case["status"] == "pass" for case in report["cases"]):
            report["status"] = "pass"
    except Exception:
        report["error"] = traceback.format_exc()
    finally:
        (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        (out / "probe.log").write_text(MARKER + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--maya", choices=("2024", "2026"), default="2024")
    parser.add_argument("--normal", type=Path, required=True)
    parser.add_argument("--arm", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--port", type=int, default=7798)
    args = parser.parse_args()
    out = args.out_dir.resolve()
    if not out.is_relative_to(ROOT / "build"):
        parser.error("output must be inside build")
    for model in (args.normal, args.arm):
        if not model.is_file():
            parser.error(f"model not found: {model}")
    out.mkdir(parents=True, exist_ok=True)
    plugin = ROOT / f"plug-ins/{args.maya}/Release/mmd_tools_cpp.mll"
    config = out / "config.json"
    config.write_text(json.dumps({"normal": str(args.normal.resolve()), "arm": str(args.arm.resolve()),
                                 "plugin": str(plugin), "output": str(out), "maya": args.maya,
                                 "modelHashes": {key: hashlib.sha256(path.read_bytes()).hexdigest()
                                                 for key, path in (("normal", args.normal), ("arm", args.arm))}}),
                      encoding="utf-8")
    report = run_maya_e2e(
        project_root=ROOT, version=args.maya, out_dir=out, port=args.port, timeout=1200,
        log_path=out / "probe.log", report_path=out / "report.json", marker=MARKER,
        command=("from maya import cmds\nfrom tools.probes.humanik_arm_variant import probe\n"
                 f"cmds.evalDeferred(lambda: probe({str(config)!r}), lowestPriority=True)\n"),
        send_label="humanik-arm-variant", stale_paths=(out / "probe.log", out / "report.json"),
        env_overrides={"MAYA_SKIP_USERSETUP_PY": "1", "MAYA_VP2_DEVICE_OVERRIDE": "VirtualDeviceDx11",
                       "MMD_TOOLS_CPP_PLUGIN": str(plugin),
                       "PATH": str(plugin.parent) + os.pathsep + os.environ.get("PATH", "")},
    )
    print(json.dumps({"status": report["status"], "error": report.get("error")}, ensure_ascii=True))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
