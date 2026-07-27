"""Issue #97 Maya commandPort probe for bone-morph/IK control-rig authoring.

The probe deliberately accepts an external PMX path (the Issue #97 model) but
only writes diagnostics under ``build/e2e``.  It imports the model, records the
analyzer and accumulator graph facts, enters ``CONTROL_OWNED``, toggles one
bone morph while moving the left foot IK and leg FK controls, and verifies the
single-writer/cycle/bake lifecycle.  Missing roles or morph routes are kept as
explicit blockers in the JSON report; the checks are never weakened to make a
fixture pass.

Usage::

    python tests/viewport/e2e_mmd_control_rig_bone_morph.py --maya 2024
    python tests/viewport/e2e_mmd_control_rig_bone_morph.py --maya 2024 \
        --model "F:/MMD/ref/EL-Pr235(KIRIYA)/伐谷るこに.pmx"
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import math
import os
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Iterable, Mapping

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from tests.common import maya_commandport

COMMAND_PORT = 7747
COMPLETION_MARKER = "//-- MMD_CONTROL_RIG_BONE_MORPH_DONE --//"
TEST_TIMEOUT = 600.0
EPSILON = 1.0e-5
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _numbers(value: Any) -> list[float]:
    if value is None or isinstance(value, (str, bytes)):
        return []
    if isinstance(value, Iterable):
        result: list[float] = []
        for item in value:
            result.extend(_numbers(item))
        return result
    try:
        return [float(value)]
    except (TypeError, ValueError):
        return []


def _delta(left: Iterable[float], right: Iterable[float]) -> float:
    return math.sqrt(sum((a - b) ** 2 for a, b in zip(left, right)))


def _matrix(node: str, cmds) -> list[float]:
    return _numbers(cmds.xform(node, query=True, worldSpace=True, matrix=True))


def _cycle(label: str, cmds) -> dict[str, Any]:
    return {
        "label": label,
        "evaluationOn": bool(cmds.cycleCheck(query=True, evaluation=True)),
        "cyclePlugs": sorted(str(value) for value in (cmds.cycleCheck(all=True, list=True) or [])),
    }


def _joint_by_name(root: str, name: str, cmds) -> str | None:
    for joint in cmds.listRelatives(root, allDescendents=True, type="joint", fullPath=True) or []:
        if not cmds.attributeQuery("mmd_bone_name", node=joint, exists=True):
            continue
        if str(cmds.getAttr(f"{joint}.mmd_bone_name")) == str(name):
            return str(joint)
    return None


def _graph_evidence(root: str, cmds, chain_names: Iterable[str]) -> dict[str, Any]:
    """Capture all accumulator connections touching the authoring chain."""

    names = set(str(value) for value in chain_names)
    joints: dict[str, str] = {}
    for name in sorted(names):
        joint = _joint_by_name(root, name, cmds)
        if joint:
            joints[name] = joint
    rows = []
    for node in sorted(str(value) for value in (cmds.ls(type="mmdBoneMorphAccum", long=True) or [])):
        target = str(cmds.getAttr(f"{node}.mmd_target_joint")) if cmds.attributeQuery("mmd_target_joint", node=node, exists=True) else ""
        target_name = ""
        if target:
            target_name = str(cmds.getAttr(f"{target}.mmd_bone_name")) if cmds.objExists(target) and cmds.attributeQuery("mmd_bone_name", node=target, exists=True) else ""
        if target_name not in names and target not in joints.values():
            continue
        connections: dict[str, list[str]] = {}
        for attr in ("baseTranslate", "baseRotate", "outputTranslate", "outputRotate"):
            plug = f"{node}.{attr}"
            connections[attr] = sorted(str(value) for value in (cmds.listConnections(plug, source=True, destination=True, plugs=True) or []))
        contribution_weights = []
        for index in cmds.getAttr(f"{node}.contribution", multiIndices=True) or []:
            plug = f"{node}.contribution[{index}].weight"
            contribution_weights.extend(str(value) for value in (cmds.listConnections(plug, source=True, destination=False, plugs=True) or []))
        rows.append({"node": node, "targetJoint": target, "targetName": target_name, "connections": connections, "contributionWeights": sorted(contribution_weights)})
    return {"joints": joints, "accumulators": rows}


def _morph_candidates(root: str, cmds, relevant_names: set[str]) -> list[dict[str, Any]]:
    candidates = []
    for node in sorted(str(value) for value in (cmds.ls(type="network", long=True) or [])):
        if not cmds.attributeQuery("mmd_morph_type", node=node, exists=True):
            continue
        if str(cmds.getAttr(f"{node}.mmd_morph_type")) != "bone":
            continue
        roots = cmds.listConnections(f"{node}.mmd_model_root", source=True, destination=False) or [] if cmds.attributeQuery("mmd_model_root", node=node, exists=True) else []
        if roots and str((cmds.ls(roots[0], long=True) or [roots[0]])[0]) != str((cmds.ls(root, long=True) or [root])[0]):
            continue
        try:
            offsets = json.loads(cmds.getAttr(f"{node}.mmd_bone_morph_offsets_json") or "[]")
        except (TypeError, ValueError, RuntimeError):
            offsets = []
        targets = []
        for offset in offsets if isinstance(offsets, list) else []:
            try:
                index = int(offset["bone_index"])
            except (KeyError, TypeError, ValueError):
                continue
            joint = None
            for candidate in cmds.listRelatives(root, allDescendents=True, type="joint", fullPath=True) or []:
                if cmds.attributeQuery("mmd_bone_index", node=candidate, exists=True) and int(cmds.getAttr(f"{candidate}.mmd_bone_index")) == index:
                    joint = str(candidate)
                    break
            name = str(cmds.getAttr(f"{joint}.mmd_bone_name")) if joint and cmds.attributeQuery("mmd_bone_name", node=joint, exists=True) else ""
            targets.append({"boneIndex": index, "boneName": name, "joint": joint})
        if any(item["boneName"] in relevant_names or "足ＩＫ" in item["boneName"] or "足IK" in item["boneName"] for item in targets):
            candidates.append({"node": node, "name": str(cmds.getAttr(f"{node}.mmd_morph_name")) if cmds.attributeQuery("mmd_morph_name", node=node, exists=True) else node, "targets": targets})
    return candidates


def _sole_writer_rows(root: str, cmds, role_joints: Mapping[str, str]) -> list[dict[str, Any]]:
    rows = []
    for role, joint in sorted(role_joints.items()):
        for attr in ("translate", "rotate"):
            plug = f"{joint}.{attr}"
            sources = sorted(str(value) for value in (cmds.listConnections(plug, source=True, destination=False, plugs=True) or []))
            rows.append({"role": role, "plug": plug, "sources": sources, "sourceTypes": sorted({str(cmds.nodeType(value.split('.', 1)[0])) for value in sources}), "pass": len(sources) <= 1 and not any("_CTRL." in value for value in sources)})
    return rows


def run_probe(log_path: str, model_path: str, report_path: str) -> None:
    """Run the Maya-side Issue #97 graph/authoring probe."""

    import maya.cmds as cmds

    log_file = Path(log_path)
    report_file = Path(report_path)
    report: dict[str, Any] = {
        "kind": "mmd-control-rig-issue-97-bone-morph",
        "status": "error",
        "mayaVersion": None,
        "model": str(model_path),
        "analyzer": {},
        "graphEvidence": {},
        "morphToggle": {},
        "controlEdits": {},
        "ownership": {},
        "lifecycle": {},
        "cycles": [],
        "blockers": [],
        "errors": [],
    }

    def log(message: str) -> None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        with log_file.open("a", encoding="utf-8") as handle:
            handle.write(str(message) + "\n")
        print(message)

    try:
        report["mayaVersion"] = str(cmds.about(version=True))
        plugin_path = _PROJECT_ROOT / "mmd_tools" / "plugin_main.py"
        maya_major = str(cmds.about(version=True)).split(".", 1)[0]
        cpp_plugin = _PROJECT_ROOT / "plug-ins" / maya_major / "Debug" / "mmd_tools_cpp.mll"
        dll_handles = []
        if cpp_plugin.is_file():
            os.environ["PATH"] = str(cpp_plugin.parent) + os.pathsep + os.environ.get("PATH", "")
            if hasattr(os, "add_dll_directory"):
                dll_handles.append(os.add_dll_directory(str(cpp_plugin.parent)))
            if not cmds.pluginInfo(str(cpp_plugin), query=True, loaded=True):
                cmds.loadPlugin(str(cpp_plugin), quiet=True)
        if not cmds.pluginInfo(plugin_path.stem, query=True, loaded=True):
            cmds.loadPlugin(str(plugin_path), quiet=True)
        cmds.file(new=True, force=True)
        from mmd_tools.io.mmd_importer import import_mmd_file
        from mmd_tools.core.mmd_control_rig_analyzer import analyze_mmd_control_rig
        from mmd_tools.core.mmd_control_rig_builder import (
            CONTROL_RIG_ATTACHED,
            CONTROL_RIG_BAKED,
            CONTROL_RIG_CONTROL_OWNED,
            CONTROL_RIG_EDIT,
            build_mmd_control_rig,
        )
        from mmd_tools.core.mmd_control_rig_motion import (
            bake_mmd_control_rig,
            enter_mmd_control_rig_edit,
            restore_mmd_control_rig_attached,
        )
        from mmd_tools.converters.bone_morph_runtime import probe_bone_morph_accum_availability

        root = str(import_mmd_file(str(model_path), options={"setup_rig": True, "setup_bone_orientation": True, "import_physics": False, "import_morphs": True, "use_cpp_fast_load": False}))
        if not root:
            raise RuntimeError("PMX import returned no model root")
        report["cycles"].append(_cycle("after_import", cmds))
        availability = probe_bone_morph_accum_availability()
        report["graphEvidence"]["availability"] = availability
        spec = analyze_mmd_control_rig(root, cmds_module=cmds)
        report["analyzer"] = spec.to_dict()
        chain_names = {"左足", "左ひざ", "左足ＩＫ", "左足IK", "左足IK親"}
        report["graphEvidence"]["authoringChain"] = _graph_evidence(root, cmds, chain_names)
        candidates = _morph_candidates(root, cmds, chain_names)
        report["graphEvidence"]["boneMorphCandidates"] = candidates
        if not availability.get("available"):
            report["blockers"].append({"code": "mmdBoneMorphAccum_unavailable", "evidence": availability})
        if not candidates:
            report["blockers"].append({"code": "relevant_bone_morph_missing", "expected": sorted(chain_names), "graph": report["graphEvidence"]["authoringChain"]})
        role_map = spec.roles_by_name
        for role in ("left_foot_ik", "left_leg"):
            binding = role_map.get(role)
            if binding is None or binding.binding is None or binding.status in {"missing", "blocked"}:
                report["blockers"].append({"code": f"analyzer_{role}_blocked", "role": binding.to_dict() if binding else None})
        if report["blockers"]:
            report["status"] = "blocked"
            log("BLOCKED: exact model/analyzer graph blockers recorded")
        else:
            rig = build_mmd_control_rig(root, cmds_module=cmds, spec=spec)
            report["lifecycle"]["build"] = {"state": rig.state, "owner": rig.owner, "controls": sorted(rig.controls)}
            if rig.state != CONTROL_RIG_ATTACHED:
                raise RuntimeError(f"unexpected build state: {rig.state}")
            edit = enter_mmd_control_rig_edit(root, cmds_module=cmds)
            report["ownership"]["afterEnter"] = {"state": edit.get("state"), "owner": edit.get("owner")}
            if edit.get("owner") != CONTROL_RIG_CONTROL_OWNED or edit.get("state") != CONTROL_RIG_EDIT:
                raise RuntimeError("enter EDIT did not establish CONTROL_OWNED")
            report["cycles"].append(_cycle("after_enter_control_owned", cmds))
            morph = candidates[0]
            morph_node = morph["node"]
            target_joints = [item["joint"] for item in morph["targets"] if item.get("joint")]
            cmds.currentTime(1, edit=True)
            weight_plug = f"{morph_node}.weight"
            weight_sources = [str(value) for value in (cmds.listConnections(weight_plug, source=True, destination=False, plugs=True) or [])]
            if len(weight_sources) > 1:
                raise RuntimeError(f"bone morph weight has multiple writers: {weight_sources}")
            # Group morphs legitimately drive a bone-morph network weight.  A
            # probe must still exercise the actual accumulator, so temporarily
            # detach that one source, toggle 0/1, then restore the exact edge.
            if weight_sources:
                cmds.disconnectAttr(weight_sources[0], weight_plug)
            try:
                cmds.setAttr(weight_plug, 0.0)
                cmds.dgdirty(allPlugs=True)
                cmds.refresh(force=True)
                zero_matrices = {joint: _matrix(joint, cmds) for joint in target_joints}
                cmds.setAttr(weight_plug, 1.0)
                cmds.dgdirty(allPlugs=True)
                cmds.refresh(force=True)
                one_matrices = {joint: _matrix(joint, cmds) for joint in target_joints}
            finally:
                if weight_sources and not cmds.isConnected(weight_sources[0], weight_plug):
                    cmds.connectAttr(weight_sources[0], weight_plug, force=False)
            target_deltas = {joint: _delta(zero_matrices.get(joint, []), one_matrices.get(joint, [])) for joint in target_joints}
            report["morphToggle"] = {"node": morph_node, "name": morph.get("name"), "weightSourceRestored": weight_sources, "zero": zero_matrices, "nonzero": one_matrices, "targetDeltas": target_deltas, "effectiveTargetChanged": max(target_deltas.values(), default=0.0) > EPSILON}
            role_joints = {}
            for role in ("left_foot_ik", "left_leg"):
                binding = role_map[role].binding
                role_joints[role] = str(binding.joint)
            controls_before = {role: _matrix(rig.controls[role], cmds) for role in role_joints if role in rig.controls}
            foot = rig.controls.get("left_foot_ik")
            leg = rig.controls.get("left_leg")
            if not foot or not leg:
                raise RuntimeError("required left_foot_ik/left_leg controls are absent")
            cmds.setAttr(f"{foot}.translateX", float(cmds.getAttr(f"{foot}.translateX")) + 0.25)
            cmds.setAttr(f"{leg}.rotateY", float(cmds.getAttr(f"{leg}.rotateY")) + math.radians(8.0))
            cmds.dgdirty(allPlugs=True)
            cmds.refresh(force=True)
            controls_after = {role: _matrix(rig.controls[role], cmds) for role in role_joints if role in rig.controls}
            target_after_control = {role: _matrix(joint, cmds) for role, joint in role_joints.items()}
            report["controlEdits"] = {"controlWorldDeltas": {role: _delta(controls_before.get(role, []), controls_after.get(role, [])) for role in role_joints}, "targetWorld": target_after_control, "effectiveTargetChanged": any(_delta(controls_before.get(role, []), controls_after.get(role, [])) > EPSILON for role in role_joints)}
            report["ownership"]["afterEdits"] = {"soleWriter": _sole_writer_rows(root, cmds, role_joints), "graphEvidence": _graph_evidence(root, cmds, chain_names)}
            report["cycles"].append(_cycle("after_morph_and_controls", cmds))
            baked = bake_mmd_control_rig(root, cmds_module=cmds)
            report["lifecycle"]["bake"] = {"state": baked.get("state"), "owner": baked.get("owner")}
            restored = restore_mmd_control_rig_attached(root, cmds_module=cmds)
            report["lifecycle"]["restore"] = {"state": restored.get("state"), "owner": restored.get("owner")}
            report["cycles"].append(_cycle("after_bake_restore", cmds))
            baseline = set(report["cycles"][0]["cyclePlugs"])
            report["newCyclePlugs"] = sorted({plug for state in report["cycles"] for plug in state["cyclePlugs"] if plug not in baseline})
            if report["newCyclePlugs"]:
                raise RuntimeError(f"new cycle plugs detected: {report['newCyclePlugs']}")
            if not report["morphToggle"].get("effectiveTargetChanged"):
                raise RuntimeError("bone morph weight 0->1 did not change an effective target")
            if not report["controlEdits"].get("effectiveTargetChanged"):
                raise RuntimeError("control edits did not change an effective target")
            if not all(row["pass"] for row in report["ownership"]["afterEdits"]["soleWriter"]):
                raise RuntimeError("control-rig edit introduced a non-single writer")
            if report["lifecycle"]["bake"].get("state") != CONTROL_RIG_BAKED or report["lifecycle"]["restore"].get("state") != CONTROL_RIG_ATTACHED:
                raise RuntimeError("bake/restore lifecycle state mismatch")
            report["status"] = "pass"
            log("PASS: Issue #97 bone-morph/IK authoring gates passed")
        if report["status"] == "blocked":
            log("BLOCKED: report preserves exact analyzer blockers and graph evidence")
    except Exception:
        report["errors"].append(traceback.format_exc())
        log(f"EXCEPTION:\n{traceback.format_exc()}")
    finally:
        report_file.parent.mkdir(parents=True, exist_ok=True)
        report_file.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        log(f"RESULT_JSON: {json.dumps(report, ensure_ascii=False, sort_keys=True)}")
        log(COMPLETION_MARKER)


def main() -> int:
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    parser = argparse.ArgumentParser(description="Issue #97 MMD control-rig bone morph probe")
    parser.add_argument("--maya", default="2024")
    parser.add_argument("--model", default=r"F:\MMD\ref\EL-Pr235(KIRIYA)\伐谷るこに.pmx")
    parser.add_argument("--port", type=int, default=COMMAND_PORT)
    parser.add_argument("--timeout", type=float, default=TEST_TIMEOUT)
    parser.add_argument("--out-dir", default=str(_PROJECT_ROOT / "build" / "e2e"))
    args = parser.parse_args()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / f"mmd_control_rig_bone_morph_maya{args.maya}.json"
    log_path = out_dir / f"mmd_control_rig_bone_morph_maya{args.maya}.log"
    maya_commandport.remove_stale_logs([report_path, log_path])
    model = Path(args.model).expanduser().resolve()
    if not model.is_file():
        logger.error("model not found: %s", model)
        return 2
    proc = None
    maya_owned = False
    try:
        if maya_commandport.is_port_open(args.port):
            raise RuntimeError(f"commandPort :{args.port} is already open")
        proc = maya_commandport.launch_maya(version=args.maya, project_root=_PROJECT_ROOT, output_dir=out_dir, port=args.port, launch_mode="explorer" if sys.platform == "win32" else "direct")
        maya_owned = True
        maya_commandport.wait_for_port(args.port, timeout=120, process=proc)
        command = ("import sys\nfrom pathlib import Path\n" f"project_root=Path(r'{_PROJECT_ROOT.as_posix()}')\n" "sys.path.insert(0,str(project_root)) if str(project_root) not in sys.path else None\n" "from tests.viewport.e2e_mmd_control_rig_bone_morph import run_probe\n" f"run_probe(r'{log_path.as_posix()}',r'{model.as_posix()}',r'{report_path.as_posix()}')\n")
        maya_commandport.send_python(args.port, command, label="<issue-97-bone-morph>")
        start = time.time()
        with log_path.open("a+", encoding="utf-8") as handle:
            handle.seek(0)
            while time.time() - start < args.timeout:
                line = handle.readline()
                if line:
                    print(line, end="")
                    if COMPLETION_MARKER in line:
                        break
                else:
                    time.sleep(0.5)
        if not report_path.is_file():
            raise TimeoutError(f"report missing: {report_path}")
        report = json.loads(report_path.read_text(encoding="utf-8"))
        logger.info("status=%s report=%s", report.get("status"), report_path)
        return 0 if report.get("status") == "pass" else (1 if report.get("status") == "blocked" else 2)
    except (FileNotFoundError, TimeoutError, RuntimeError, ValueError) as exc:
        logger.error("probe blocked: %s", exc)
        return 2
    finally:
        if maya_owned:
            maya_commandport.quit_maya(args.port)
            time.sleep(2.0)
            maya_commandport.close_process_logs(proc)


if __name__ == "__main__":
    sys.exit(main())
