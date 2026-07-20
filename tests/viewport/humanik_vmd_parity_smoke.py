"""SOURCE/VMD IK reproduction-matrix smoke for ``HUMANIK-SOURCE-VMD-IK-PARITY-1``.

This is a **diagnosis harness only** — it never edits ``mmd_tools/`` production
code.  It reproduces the reported bug where importing a VMD after (or around)
HumanIK ``setup_and_characterize`` / ``enter_source_mode`` can leave the MMD
rig in a pose that no longer matches the MMD-owned evaluation result (CCDIK
output not applied, connections severed, etc.).

For a single PMX fixture and VMD motion, the smoke runs five scenarios, each
starting from a **fresh Maya scene**:

* ``baseline``        — VMD import only, no HumanIK at all.  This is the
  reference every other scenario is diffed against.
* ``char_then_vmd``   — ``setup_and_characterize`` -> VMD import.
* ``vmd_then_char``   — VMD import -> ``setup_and_characterize``.
* ``char_source_vmd`` — ``setup_and_characterize`` -> ``enter_source_mode`` ->
  VMD import.
* ``vmd_then_source`` — VMD import -> ``setup_and_characterize`` ->
  ``enter_source_mode``.

An optional sixth scenario, ``char_fail_restore_then_vmd``, runs only when
``--inject-restore-failure`` is passed.  It engineers a minimal trigger for
the suspected ``HumanIkStanceTransaction.restore()`` failure path
(``mmd_tools/core/humanik_stance.py``).  A plain attribute connection made
before ``setup_and_characterize`` was tried first and did not reproduce the
failure: the model's default pose already satisfies the stance's horizontal
T-pose tolerance, so ``HumanIkStanceTransaction.enter()`` applies only a
near-identity delta to the re-posed arm joint and no HIK-assigned joint's
``rotate``/``translate`` drifts from its ``prepare()``-time snapshot by more
than ``STANCE_ATTRIBUTE_WRITE_TOLERANCE`` -- so ``_restore_attribute_if_changed``
never even reaches its locked/incoming check. The scenario instead uses the
``stance_transaction_factory`` hook on ``HumanIkFrontendSession`` (the same
public injection point ``tests/unit/test_humanik_frontend.py`` uses with a
fully fake stance) to wrap the *real* ``HumanIkStanceTransaction`` in a thin
subclass, ``_RestoreFailureStanceTransaction``, that -- only after the real
``enter()`` completes -- deterministically bumps the ``RightForeArm``
HIK-assigned joint's ``rotateX`` away from its ``prepare()``-time snapshot
and connects an ``animCurveTA`` to it, so ``_attribute_write_state`` reports
a real incoming connection once ``_restore_attribute_if_changed`` observes
the (now guaranteed) residual.  This does not modify ``mmd_tools/`` -- it
only calls the real transaction's own public ``enter``/``restore`` and
otherwise defers to it. This scenario is not part of ``SCENARIOS`` and never
runs by default; the report gains an ``injectedScenarios`` section only when
requested.

At a fixed frame set (clamped to the motion length), each scenario captures:

1. Per HIK-assignment joint: world matrix and JO-aware skin matrix
   (``skinCluster.bindPreMatrix * joint.worldMatrix``).
2. Scene topology after the scenario's operations complete: ``mmdCcdIk``
   ``enabled`` + outgoing ``outputRotate`` connections, ``mmdAppend``
   outgoing connections, and incoming-connection counts on each HIK joint's
   ``translate``/``rotate``.
3. HumanIK character state where applicable (lock state, retarget input
   source/type) via the ``HIKCharacterNode`` nodes present in the scene.
4. IK goal evidence for each ``mmdCcdIk`` node (``goalWorldMatrix``
   translation).  A true goal/effector residual is not cheaply available
   without resolving the native chain JSON to a Maya joint, so this is noted
   rather than computed.

Each non-baseline scenario is diffed against ``baseline`` on world matrices,
skin matrices, and topology.  The report's ``firstDivergence`` field records
the first scenario/frame/joint/category where the two disagree.

Usage::

    mayapy tests/viewport/humanik_vmd_parity_smoke.py \\
        --model "F:/MMD/pmx/.../model.pmx" \\
        --motion tests/data/mmt_test_model_ik_test_motion.vmd \\
        --evaluation off --out build/reports/humanik_vmd_parity_smoke.json
"""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import maya.cmds as cmds
import maya.standalone

from mmd_tools.core.humanik_builder import resolve_scene_humanik_assignments
from mmd_tools.core.humanik_constraints import collect_humanik_constraint_facts
from mmd_tools.core.humanik_frontend import HumanIkFrontendSession
from mmd_tools.core.humanik_stance import HumanIkStanceTransaction


DEFAULT_MODEL = r"F:\MMD\pmx\【珊瑚宫心海】_by_原神_32c242c2043da5bac0d24f1b07a2f3f8\珊瑚宫心海.pmx"
DEFAULT_MOTION_CANDIDATES = (
    "tests/data/mmt_test_model_ik_test_motion.vmd",
    "tests/data/mmt_test_model_test_motion.vmd",
)
DEFAULT_FRAMES = (0, 1, 15, 30, 60)
MATRIX_MAX_TOLERANCE = 1.0e-3

SCENARIOS = (
    "baseline",
    "char_then_vmd",
    "vmd_then_char",
    "char_source_vmd",
    "vmd_then_source",
)


def _default_motion() -> Optional[str]:
    for candidate in DEFAULT_MOTION_CANDIDATES:
        if Path(candidate).is_file():
            return candidate
    return None


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=DEFAULT_MODEL, help="PMX fixture to import fresh for every scenario.")
    default_motion = _default_motion()
    parser.add_argument(
        "--motion",
        default=default_motion,
        required=default_motion is None,
        help="VMD motion fixture (required if no known tests/data VMD exists).",
    )
    parser.add_argument("--out", default="build/reports/humanik_vmd_parity_smoke.json")
    parser.add_argument("--evaluation", choices=("off", "serial", "parallel"), default="off")
    parser.add_argument(
        "--frames",
        default=",".join(str(value) for value in DEFAULT_FRAMES),
        help="Comma-separated frame list, clamped to the motion length.",
    )
    parser.add_argument(
        "--inject-restore-failure",
        action="store_true",
        help=(
            "Also run the char_fail_restore_then_vmd scenario, which engineers a "
            "HumanIkStanceTransaction.restore() failure (see module docstring) and "
            "reports the result under injectedScenarios. Off by default."
        ),
    )
    return parser.parse_args()


def _load_plugin() -> None:
    path = Path(__file__).resolve().parents[2] / "mmd_tools" / "plugin_main.py"
    if not cmds.pluginInfo(path.stem, query=True, loaded=True):
        cmds.loadPlugin(str(path), quiet=True)


def _long_name(node: str) -> str:
    values = cmds.ls(node, long=True) or []
    return str(values[0] if values else node)


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


def _import_motion(motion: Path, model: Path, target_model: str) -> None:
    """Import a VMD in rig mode (not bake_mode) so the live CCDIK rig is exercised."""
    from mmd_tools.io.mmd_importer import import_mmd_file

    if not import_mmd_file(
        str(motion),
        options={
            "target_model": target_model,
            "pmx_path": str(model),
            "bake_mode": False,
            "clear_existing_motion": True,
            "use_native_pmx_parse": False,
            "require_native_pmx_parse": False,
        },
    ):
        raise RuntimeError(f"VMD import failed: {motion}")


def _probe_motion_end_frame(model: Path, motion: Path) -> int:
    """Import once into a throwaway scene solely to read the resulting frame range."""
    cmds.file(new=True, force=True)
    _load_plugin()
    root = _import_model(model)
    _import_motion(motion, model, root)
    end = cmds.playbackOptions(query=True, animationEndTime=True)
    return int(end) if end is not None else max(DEFAULT_FRAMES)


def _matrix_values(plug: str) -> List[float]:
    return [float(value) for value in cmds.getAttr(plug)]


def _matrix_error(left: Sequence[float], right: Sequence[float]) -> Tuple[float, float]:
    differences = [abs(float(a) - float(b)) for a, b in zip(left, right)]
    return (max(differences, default=0.0), statistics.fmean(differences) if differences else 0.0)


def _skin_clusters(root: str) -> List[str]:
    clusters: List[str] = []
    shapes = cmds.listRelatives(root, allDescendents=True, type="mesh", fullPath=True) or []
    for shape in shapes:
        for node in cmds.listHistory(shape, pruneDagObjects=True) or []:
            if cmds.nodeType(node) == "skinCluster" and node not in clusters:
                clusters.append(str(node))
    return sorted(clusters)


def _skin_influences(root: str) -> Dict[str, Tuple[str, int]]:
    result: Dict[str, Tuple[str, int]] = {}
    for skin in _skin_clusters(root):
        for logical_index in cmds.getAttr(f"{skin}.matrix", multiIndices=True) or []:
            sources = cmds.listConnections(
                f"{skin}.matrix[{logical_index}]", source=True, destination=False, plugs=True
            ) or []
            if not sources:
                continue
            joint = _long_name(str(sources[0]).rsplit(".", 1)[0])
            result.setdefault(joint, (skin, int(logical_index)))
    return result


def _skin_matrix(joint: str, skin: str, logical_index: int) -> List[float]:
    bind_pre = _matrix_values(f"{skin}.bindPreMatrix[{logical_index}]")
    world = _matrix_values(f"{joint}.worldMatrix[0]")
    import maya.api.OpenMaya as om

    product = om.MMatrix(bind_pre) * om.MMatrix(world)
    return [float(product[index]) for index in range(16)]


def _resolve_joint_map(root: str) -> Dict[int, Dict[str, Any]]:
    """Resolve the HIK slot -> joint mapping (no characterization required)."""
    result = resolve_scene_humanik_assignments(root)
    skin = _skin_influences(root)
    mapping: Dict[int, Dict[str, Any]] = {}
    for assignment in result.assignments:
        joint = _long_name(assignment.joint)
        skin_info = skin.get(joint)
        mapping[int(assignment.hik_index)] = {
            "hikSlot": int(assignment.hik_index),
            "hikBone": str(assignment.hik_bone),
            "mmdBone": str(assignment.mmd_bone),
            "joint": joint,
            "skin": skin_info[0] if skin_info else None,
            "logicalIndex": skin_info[1] if skin_info else None,
        }
    return mapping


def _capture_frames(joint_map: Mapping[int, Mapping[str, Any]], frames: Sequence[int]) -> Dict[str, Any]:
    per_frame: List[Dict[str, Any]] = []
    for frame in frames:
        cmds.currentTime(frame, edit=True)
        cmds.refresh(force=True)
        joints: Dict[str, Any] = {}
        for slot, info in sorted(joint_map.items()):
            joint = str(info["joint"])
            world = _matrix_values(f"{joint}.worldMatrix[0]")
            skin_matrix = (
                _skin_matrix(joint, str(info["skin"]), int(info["logicalIndex"]))
                if info.get("skin") is not None
                else None
            )
            joints[str(slot)] = {
                "hikBone": info["hikBone"],
                "joint": joint,
                "worldMatrix": world,
                "skinMatrix": skin_matrix,
            }
        per_frame.append({"frame": int(frame), "joints": joints})
    return {"frames": per_frame}


def _incoming_sources(destination: str) -> List[str]:
    return sorted(
        {str(value) for value in (cmds.listConnections(destination, source=True, destination=False, plugs=True) or [])}
    )


def _incoming_connection_count(joint: str, attr: str) -> int:
    sources: set = set()
    for candidate in (attr, f"{attr}X", f"{attr}Y", f"{attr}Z"):
        plug = f"{joint}.{candidate}"
        if not cmds.attributeQuery(candidate, node=joint, exists=True):
            continue
        sources.update(_incoming_sources(plug))
    return len(sources)


def _capture_topology(joint_map: Mapping[int, Mapping[str, Any]]) -> Dict[str, Any]:
    """Snapshot mmdCcdIk/mmdAppend connection topology and HIK joint fan-in."""
    facts = collect_humanik_constraint_facts()
    ccdik_nodes = sorted(cmds.ls(type="mmdCcdIk") or [])
    append_nodes = sorted(cmds.ls(type="mmdAppend") or [])
    fact_by_node = {fact.node: fact for fact in facts}
    ccdik_rows = []
    for node in ccdik_nodes:
        fact = fact_by_node.get(node)
        try:
            enabled = bool(cmds.getAttr(f"{node}.enabled"))
        except Exception:
            enabled = None
        ccdik_rows.append(
            {
                "node": node,
                "enabled": enabled,
                "writes": list(fact.writes) if fact else [],
                "reads": list(fact.reads) if fact else [],
            }
        )
    append_rows = []
    for node in append_nodes:
        fact = fact_by_node.get(node)
        append_rows.append(
            {
                "node": node,
                "writes": list(fact.writes) if fact else [],
                "reads": list(fact.reads) if fact else [],
            }
        )
    joint_fanin = {}
    for slot, info in sorted(joint_map.items()):
        joint = str(info["joint"])
        joint_fanin[str(slot)] = {
            "joint": joint,
            "hikBone": info["hikBone"],
            "incomingTranslateCount": _incoming_connection_count(joint, "translate"),
            "incomingRotateCount": _incoming_connection_count(joint, "rotate"),
        }
    return {
        "mmdCcdIk": ccdik_rows,
        "mmdAppend": append_rows,
        "hikJointFanIn": joint_fanin,
    }


def _capture_hik_state(mel_module) -> Dict[str, Any]:
    from mmd_tools.core.humanik_builder import get_humanik_definition_lock_state

    characters = sorted(cmds.ls(type="HIKCharacterNode") or [])
    rows = []
    for character in characters:
        try:
            lock_state = get_humanik_definition_lock_state(character, mel_module)
        except Exception as exc:
            lock_state = f"error: {exc}"
        try:
            input_source = str(mel_module.eval(f'hikGetRetargetCharacterInput("{character}")') or "")
        except Exception as exc:
            input_source = f"error: {exc}"
        try:
            raw_input_type = mel_module.eval(f'hikGetInputType("{character}")')
            input_type = int(raw_input_type) if raw_input_type is not None else None
        except Exception as exc:
            input_type = f"error: {exc}"
        rows.append(
            {
                "character": character,
                "lockState": lock_state,
                "inputSource": input_source,
                "inputType": input_type,
            }
        )
    return {"characters": rows, "characterCount": len(characters)}


def _capture_ik_goal_evidence() -> Dict[str, Any]:
    rows = []
    for node in sorted(cmds.ls(type="mmdCcdIk") or []):
        row: Dict[str, Any] = {"node": node}
        try:
            goal_world_matrix = _matrix_values(f"{node}.goalWorldMatrix")
            row["goalWorldPosition"] = goal_world_matrix[12:15]
        except Exception as exc:
            row["goalWorldPosition"] = None
            row["goalWorldMatrixError"] = str(exc)
        row["effectorWorldPosition"] = None
        row["residualNote"] = (
            "Effector world position requires resolving chainJson bone_slot -> Maya joint "
            "(not stored on the node); skipped as not cheaply available."
        )
        rows.append(row)
    return {"rows": rows}


def _run_scenario(
    scenario: str,
    model: Path,
    motion: Path,
    frames: Sequence[int],
    evaluation_mode: str,
    mel_module,
) -> Dict[str, Any]:
    cmds.file(new=True, force=True)
    _load_plugin()
    cmds.evaluationManager(mode=evaluation_mode)
    root = _import_model(model)
    ops: List[str] = []
    session: Optional[HumanIkFrontendSession] = None

    def _characterize() -> None:
        nonlocal session
        session = HumanIkFrontendSession()
        session.setup_and_characterize(root)
        ops.append("characterize")

    def _enter_source() -> None:
        assert session is not None
        session.enter_source_mode(root)
        ops.append("enter_source_mode")

    def _vmd() -> None:
        _import_motion(motion, model, root)
        ops.append("vmd_import")

    if scenario == "baseline":
        _vmd()
    elif scenario == "char_then_vmd":
        _characterize()
        _vmd()
    elif scenario == "vmd_then_char":
        _vmd()
        _characterize()
    elif scenario == "char_source_vmd":
        _characterize()
        _enter_source()
        _vmd()
    elif scenario == "vmd_then_source":
        _vmd()
        _characterize()
        _enter_source()
    else:
        raise ValueError(f"Unknown scenario: {scenario}")

    joint_map = _resolve_joint_map(root)
    if not joint_map:
        raise RuntimeError(f"No HIK assignments resolved for scenario={scenario}")
    frame_capture = _capture_frames(joint_map, frames)
    topology = _capture_topology(joint_map)
    hik_state = _capture_hik_state(mel_module)
    ik_goal = _capture_ik_goal_evidence()
    return {
        "scenario": scenario,
        "operations": ops,
        "root": root,
        "assignmentCount": len(joint_map),
        "frames": frame_capture,
        "topology": topology,
        "hikState": hik_state,
        "ikGoalEvidence": ik_goal,
    }


RESTORE_FAILURE_TARGET_HIK_BONE = "RightForeArm"
RESTORE_FAILURE_ROTATE_BUMP_DEGREES = 15.0


class _RestoreFailureStanceTransaction(HumanIkStanceTransaction):
    """Real ``HumanIkStanceTransaction`` that engineers a deterministic restore() failure.

    Delegates every step to the production implementation. Only after the
    real ``enter()`` has isolated writer edges and applied the canonical
    stance does this subclass bump the ``RightForeArm`` HIK-assigned joint's
    ``rotateX`` (one of ``restore_joints``, captured pre-stance by the real
    ``prepare()``) away from its snapshot and connect an ``animCurveTA`` to
    it. That guarantees ``_restore_attribute_if_changed`` (in
    ``mmd_tools/core/humanik_stance.py``) sees both a residual over
    ``STANCE_ATTRIBUTE_WRITE_TOLERANCE`` and a live incoming connection when
    the real ``restore()`` runs, so it raises -- without this test file
    editing ``mmd_tools/`` itself. See the module docstring for the earlier
    (unsuccessful) plain-connection attempt and why it did not reproduce.
    """

    injected_trigger: Optional[Dict[str, Any]] = None

    def enter(self) -> "HumanIkStanceTransaction":
        result = super().enter()
        cmds = self.cmds
        target_joint = None
        for joint, info in self.restore_joints.items():
            if info.get("hikBone") == RESTORE_FAILURE_TARGET_HIK_BONE:
                target_joint = joint
                break
        if target_joint is None:
            raise RuntimeError(
                f"Cannot engineer restore-failure trigger: no restore_joints entry for "
                f"hikBone={RESTORE_FAILURE_TARGET_HIK_BONE}"
            )
        baseline_rotate_x = float(cmds.getAttr(f"{target_joint}.rotateX"))
        bumped_value = baseline_rotate_x + RESTORE_FAILURE_ROTATE_BUMP_DEGREES
        curve = cmds.createNode("animCurveTA")
        cmds.setKeyframe(curve, time=0, value=bumped_value)
        cmds.connectAttr(f"{curve}.output", f"{target_joint}.rotateX", force=True)
        self.injected_trigger = {
            "targetJoint": target_joint,
            "targetHikBone": RESTORE_FAILURE_TARGET_HIK_BONE,
            "animCurve": curve,
            "baselineRotateXDegrees": baseline_rotate_x,
            "bumpedRotateXDegrees": bumped_value,
        }
        return result


def _run_injected_restore_failure_scenario(
    model: Path,
    motion: Path,
    frames: Sequence[int],
    evaluation_mode: str,
    mel_module,
) -> Dict[str, Any]:
    """Run char_fail_restore_then_vmd: engineer a stance-restore failure, then VMD import.

    Unlike ``_run_scenario``, this scenario expects ``setup_and_characterize`` to
    raise. The exception is caught and recorded; the scenario continues on to
    VMD import and capture so the resulting topology/frame divergence (if any)
    versus baseline can be inspected as the hypothesis test for
    ``HUMANIK-SOURCE-VMD-IK-PARITY-1``.
    """
    cmds.file(new=True, force=True)
    _load_plugin()
    cmds.evaluationManager(mode=evaluation_mode)
    root = _import_model(model)
    ops: List[str] = ["inject_restore_failure_trigger_armed"]

    created_stances: List[_RestoreFailureStanceTransaction] = []

    def _stance_factory(*args, **kwargs) -> _RestoreFailureStanceTransaction:
        stance = _RestoreFailureStanceTransaction(*args, **kwargs)
        created_stances.append(stance)
        return stance

    characterize_error: Optional[str] = None
    session: Optional[HumanIkFrontendSession] = None
    try:
        session = HumanIkFrontendSession(stance_transaction_factory=_stance_factory)
        session.setup_and_characterize(root)
        ops.append("characterize")
    except Exception as exc:  # expected: stance.restore() raises inside setup_and_characterize
        characterize_error = str(exc)
        ops.append(f"characterize_raised: {characterize_error}")

    trigger = created_stances[0].injected_trigger if created_stances else None

    joint_map = _resolve_joint_map(root)
    topology_after_characterize = _capture_topology(joint_map) if joint_map else None

    vmd_error: Optional[str] = None
    try:
        _import_motion(motion, model, root)
        ops.append("vmd_import")
    except Exception as exc:
        vmd_error = str(exc)
        ops.append(f"vmd_import_raised: {vmd_error}")

    if not joint_map:
        joint_map = _resolve_joint_map(root)
    frame_capture = _capture_frames(joint_map, frames) if joint_map else {"frames": []}
    topology = _capture_topology(joint_map) if joint_map else {"mmdCcdIk": [], "mmdAppend": [], "hikJointFanIn": {}}
    hik_state = _capture_hik_state(mel_module)
    ik_goal = _capture_ik_goal_evidence()
    return {
        "scenario": "char_fail_restore_then_vmd",
        "operations": ops,
        "root": root,
        "trigger": trigger,
        "characterizeRaised": characterize_error is not None,
        "characterizeError": characterize_error,
        "vmdImportRaised": vmd_error is not None,
        "vmdImportError": vmd_error,
        "topologyAfterCharacterize": topology_after_characterize,
        "assignmentCount": len(joint_map),
        "frames": frame_capture,
        "topology": topology,
        "hikState": hik_state,
        "ikGoalEvidence": ik_goal,
    }


def _diff_matrix_frames(
    baseline_frames: Sequence[Mapping[str, Any]],
    scenario_frames: Sequence[Mapping[str, Any]],
) -> Tuple[List[Dict[str, Any]], Optional[Dict[str, Any]]]:
    rows = []
    first_divergence = None
    baseline_by_frame = {int(row["frame"]): row for row in baseline_frames}
    for scenario_row in scenario_frames:
        frame = int(scenario_row["frame"])
        baseline_row = baseline_by_frame.get(frame)
        if baseline_row is None:
            continue
        for slot, scenario_joint in sorted(scenario_row["joints"].items(), key=lambda item: int(item[0])):
            baseline_joint = baseline_row["joints"].get(slot)
            if baseline_joint is None:
                continue
            world_max, world_mean = _matrix_error(baseline_joint["worldMatrix"], scenario_joint["worldMatrix"])
            skin_max = skin_mean = None
            if baseline_joint.get("skinMatrix") is not None and scenario_joint.get("skinMatrix") is not None:
                skin_max, skin_mean = _matrix_error(baseline_joint["skinMatrix"], scenario_joint["skinMatrix"])
            row = {
                "frame": frame,
                "hikSlot": int(slot),
                "hikBone": scenario_joint["hikBone"],
                "joint": scenario_joint["joint"],
                "worldMatrixMax": world_max,
                "worldMatrixMean": world_mean,
                "skinMatrixMax": skin_max,
                "skinMatrixMean": skin_mean,
            }
            rows.append(row)
            exceeds = world_max > MATRIX_MAX_TOLERANCE or (skin_max is not None and skin_max > MATRIX_MAX_TOLERANCE)
            if exceeds and first_divergence is None:
                first_divergence = {
                    "category": "matrix",
                    "frame": frame,
                    "hikSlot": int(slot),
                    "hikBone": row["hikBone"],
                    "joint": row["joint"],
                    "worldMatrixMax": world_max,
                    "skinMatrixMax": skin_max,
                }
    return rows, first_divergence


def _diff_topology(
    baseline_topology: Mapping[str, Any],
    scenario_topology: Mapping[str, Any],
) -> Tuple[List[Dict[str, Any]], Optional[Dict[str, Any]]]:
    mismatches = []
    first_divergence = None

    def _index_by_node(rows: Sequence[Mapping[str, Any]]) -> Dict[str, Mapping[str, Any]]:
        return {str(row["node"]): row for row in rows}

    baseline_ccdik = _index_by_node(baseline_topology["mmdCcdIk"])
    scenario_ccdik = _index_by_node(scenario_topology["mmdCcdIk"])
    for node in sorted(set(baseline_ccdik) | set(scenario_ccdik)):
        base_row = baseline_ccdik.get(node)
        scen_row = scenario_ccdik.get(node)
        if base_row is None or scen_row is None:
            mismatch = {"category": "connection_topology", "node": node, "issue": "node missing in one scenario"}
            mismatches.append(mismatch)
            if first_divergence is None:
                first_divergence = mismatch
            continue
        if bool(base_row["enabled"]) != bool(scen_row["enabled"]):
            mismatch = {
                "category": "ik_enable",
                "node": node,
                "baselineEnabled": base_row["enabled"],
                "scenarioEnabled": scen_row["enabled"],
            }
            mismatches.append(mismatch)
            if first_divergence is None:
                first_divergence = mismatch
        if sorted(base_row["writes"]) != sorted(scen_row["writes"]):
            mismatch = {
                "category": "connection_topology",
                "node": node,
                "baselineWrites": sorted(base_row["writes"]),
                "scenarioWrites": sorted(scen_row["writes"]),
            }
            mismatches.append(mismatch)
            if first_divergence is None:
                first_divergence = mismatch

    baseline_append = _index_by_node(baseline_topology["mmdAppend"])
    scenario_append = _index_by_node(scenario_topology["mmdAppend"])
    for node in sorted(set(baseline_append) | set(scenario_append)):
        base_row = baseline_append.get(node)
        scen_row = scenario_append.get(node)
        if base_row is None or scen_row is None:
            mismatch = {"category": "connection_topology", "node": node, "issue": "node missing in one scenario"}
            mismatches.append(mismatch)
            if first_divergence is None:
                first_divergence = mismatch
            continue
        if sorted(base_row["writes"]) != sorted(scen_row["writes"]):
            mismatch = {
                "category": "connection_topology",
                "node": node,
                "baselineWrites": sorted(base_row["writes"]),
                "scenarioWrites": sorted(scen_row["writes"]),
            }
            mismatches.append(mismatch)
            if first_divergence is None:
                first_divergence = mismatch

    baseline_fanin = baseline_topology["hikJointFanIn"]
    scenario_fanin = scenario_topology["hikJointFanIn"]
    for slot in sorted(set(baseline_fanin) | set(scenario_fanin), key=lambda value: int(value)):
        base_row = baseline_fanin.get(slot)
        scen_row = scenario_fanin.get(slot)
        if base_row is None or scen_row is None:
            continue
        if (
            base_row["incomingTranslateCount"] != scen_row["incomingTranslateCount"]
            or base_row["incomingRotateCount"] != scen_row["incomingRotateCount"]
        ):
            mismatch = {
                "category": "connection_topology",
                "hikSlot": int(slot),
                "joint": scen_row["joint"],
                "baselineTranslateCount": base_row["incomingTranslateCount"],
                "scenarioTranslateCount": scen_row["incomingTranslateCount"],
                "baselineRotateCount": base_row["incomingRotateCount"],
                "scenarioRotateCount": scen_row["incomingRotateCount"],
            }
            mismatches.append(mismatch)
            if first_divergence is None:
                first_divergence = mismatch

    return mismatches, first_divergence


def _diff_scenario(baseline: Mapping[str, Any], scenario: Mapping[str, Any]) -> Dict[str, Any]:
    matrix_rows, matrix_divergence = _diff_matrix_frames(baseline["frames"]["frames"], scenario["frames"]["frames"])
    topology_mismatches, topology_divergence = _diff_topology(baseline["topology"], scenario["topology"])
    # First divergence overall: prefer whichever category occurs at the earliest frame;
    # topology is captured post-hoc (not per frame) so it is treated as occurring
    # "at scenario completion" and only reported first when no matrix divergence exists
    # at an earlier or equal frame.
    first_divergence = None
    if matrix_divergence is not None:
        first_divergence = matrix_divergence
    if topology_divergence is not None and (
        first_divergence is None or "frame" not in first_divergence
    ):
        first_divergence = {**topology_divergence, "frame": None}
    worst_matrix = max((row["worldMatrixMax"] for row in matrix_rows), default=0.0)
    worst_skin = max((row["skinMatrixMax"] for row in matrix_rows if row["skinMatrixMax"] is not None), default=0.0)
    matches = (
        worst_matrix <= MATRIX_MAX_TOLERANCE
        and worst_skin <= MATRIX_MAX_TOLERANCE
        and not topology_mismatches
    )
    return {
        "scenario": scenario["scenario"],
        "matches": matches,
        "worstWorldMatrixResidual": worst_matrix,
        "worstSkinMatrixResidual": worst_skin,
        "topologyMismatchCount": len(topology_mismatches),
        "topologyMismatches": topology_mismatches[:20],
        "firstDivergence": first_divergence,
        "matrixRows": matrix_rows,
    }


def main() -> int:
    args = _parse_args()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload: Dict[str, Any] = {"status": "error", "evaluationMode": args.evaluation}
    maya.standalone.initialize(name="python")
    try:
        import maya.mel as mel

        model = Path(args.model).resolve()
        motion = Path(args.motion).resolve()
        if not model.is_file():
            raise FileNotFoundError(f"Model fixture not found: {model}")
        if not motion.is_file():
            raise FileNotFoundError(f"Motion fixture not found: {motion}")
        _load_plugin()
        end_frame = _probe_motion_end_frame(model, motion)
        requested_frames = [int(value) for value in args.frames.split(",") if value.strip()]
        frames = sorted({min(frame, end_frame) for frame in requested_frames})

        scenario_results: Dict[str, Dict[str, Any]] = {}
        for scenario in SCENARIOS:
            scenario_results[scenario] = _run_scenario(scenario, model, motion, frames, args.evaluation, mel)

        baseline = scenario_results["baseline"]
        comparisons = {}
        for scenario in SCENARIOS:
            if scenario == "baseline":
                continue
            comparisons[scenario] = _diff_scenario(baseline, scenario_results[scenario])

        all_match = all(comparison["matches"] for comparison in comparisons.values())

        injected_section: Optional[Dict[str, Any]] = None
        if args.inject_restore_failure:
            injected_result = _run_injected_restore_failure_scenario(model, motion, frames, args.evaluation, mel)
            injected_comparison = _diff_scenario(baseline, injected_result)
            injected_section = {
                "characterizeRaised": injected_result["characterizeRaised"],
                "characterizeError": injected_result["characterizeError"],
                "vmdImportRaised": injected_result["vmdImportRaised"],
                "vmdImportError": injected_result["vmdImportError"],
                "trigger": injected_result["trigger"],
                "operations": injected_result["operations"],
                "topologyAfterCharacterize": injected_result["topologyAfterCharacterize"],
                "comparisonVsBaseline": injected_comparison,
            }

        payload.update(
            {
                "status": "pass" if all_match else "stop",
                "model": str(model),
                "motion": str(motion),
                "mayaVersion": cmds.about(version=True),
                "frames": frames,
                "endFrameProbed": end_frame,
                "acceptanceThresholds": {"matrixMax": MATRIX_MAX_TOLERANCE},
                "scenarios": {
                    scenario: {
                        "operations": result["operations"],
                        "assignmentCount": result["assignmentCount"],
                        "hikState": result["hikState"],
                        "ikGoalEvidence": result["ikGoalEvidence"],
                        "topologySummary": {
                            "mmdCcdIkCount": len(result["topology"]["mmdCcdIk"]),
                            "mmdAppendCount": len(result["topology"]["mmdAppend"]),
                        },
                    }
                    for scenario, result in scenario_results.items()
                },
                "comparisons": comparisons,
            }
        )
        if injected_section is not None:
            payload["injectedScenarios"] = {"char_fail_restore_then_vmd": injected_section}
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(
            json.dumps(
                {
                    "status": payload["status"],
                    "mode": args.evaluation,
                    "firstDivergences": {
                        scenario: comparison["firstDivergence"] for scenario, comparison in comparisons.items()
                    },
                    "injected": (
                        None
                        if injected_section is None
                        else {
                            "characterizeRaised": injected_section["characterizeRaised"],
                            "firstDivergence": injected_section["comparisonVsBaseline"]["firstDivergence"],
                        }
                    ),
                },
                sort_keys=True,
                ensure_ascii=False,
            )
        )
        return 0 if payload["status"] == "pass" else 1
    except Exception as exc:
        payload["error"] = str(exc)
        payload["status"] = "error"
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False))
        return 2
    finally:
        maya.standalone.uninitialize()


if __name__ == "__main__":
    raise SystemExit(main())
