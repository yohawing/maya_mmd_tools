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
import hashlib
import io
import json
import logging
import math
import os
import sys
import traceback
from pathlib import Path
from typing import Any, Iterable, Mapping

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from tests.viewport.maya_e2e_harness import run_maya_e2e

COMMAND_PORT = 7747
COMPLETION_MARKER = "//-- MMD_CONTROL_RIG_BONE_MORPH_DONE --//"
TEST_TIMEOUT = 600.0
EPSILON = 1.0e-5
EYE_PARITY_TOLERANCE = 5.0e-3
EYE_MOTION_WITNESS_THRESHOLD = 10.0 * EYE_PARITY_TOLERANCE
EYE_CTRL_ENGLISH_NAME = "EyeCtrl"
EYE_CTRL_PRIMARY_NAME = "両目"
EYE_CTRL_BONE_INDEX = 14
EYE_CTRL_FRAMES = (0, 5, 10)
DEPENDENCY_BAKE_REASON = (
    "This bone has no dedicated Control Rig mapping, so its evaluated motion was baked."
)
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


def _eye_ctrl_vmd_witness(vmd_data: Any, frames: Iterable[int]) -> dict[str, Any]:
    """Return primary-name EyeCtrl frames and a motion witness."""

    expected = {int(frame) for frame in frames}
    rows = [
        frame
        for frame in getattr(vmd_data, "bone_frames", ()) or ()
        if str(getattr(frame, "bone_name", "")) == EYE_CTRL_PRIMARY_NAME
        and int(getattr(frame, "frame_number", -1)) in expected
    ]
    by_frame = {int(frame.frame_number): frame for frame in rows}
    missing = sorted(expected.difference(by_frame))
    if missing:
        raise RuntimeError(
            f"{EYE_CTRL_PRIMARY_NAME} VMD track is missing frames: {missing}"
        )
    payload = {
        str(frame): {
            "boneName": str(by_frame[frame].bone_name),
            "frame": int(by_frame[frame].frame_number),
            "position": [float(value) for value in by_frame[frame].position],
            "rotation": [float(value) for value in by_frame[frame].rotation],
        }
        for frame in sorted(expected)
    }
    baseline = payload[str(min(expected))]
    candidate = payload[str(max(expected))]
    delta = max(
        [
            abs(left - right)
            for left, right in zip(baseline["position"], candidate["position"])
        ]
        + [
            abs(left - right)
            for left, right in zip(baseline["rotation"], candidate["rotation"])
        ]
    )
    return {
        "primaryName": EYE_CTRL_PRIMARY_NAME,
        "englishName": EYE_CTRL_ENGLISH_NAME,
        "boneIndex": EYE_CTRL_BONE_INDEX,
        "frames": payload,
        "motionDelta": delta,
        "pass": delta > EPSILON,
    }


def _dependency_warning_evidence(issues: Iterable[Any]) -> list[dict[str, Any]]:
    """Validate the one exact structured dependency-bake warning witness.

    The warning reason is retained as one opaque diagnostic string.  Parsing
    legacy semicolon-delimited reason text would let a malformed warning pass
    while appearing to contain structured facts.
    """

    candidates = [
        issue
        for issue in issues or ()
        if ".dependency_bake" in str(getattr(issue, "path", ""))
    ]
    if len(candidates) != 1:
        raise RuntimeError(
            "expected exactly one dependency_bake warning: "
            f"{len(candidates)}"
        )
    issue = candidates[0]
    path = str(getattr(issue, "path", ""))
    if not path.endswith(f".{EYE_CTRL_PRIMARY_NAME}.dependency_bake"):
        raise RuntimeError(f"unexpected dependency_bake warning path: {path}")
    severity = str(getattr(issue, "severity", "")).lower()
    if severity != "warning":
        raise RuntimeError(f"dependency_bake warning is not warning severity: {severity}")
    details = getattr(issue, "details", {}) or {}
    if not isinstance(details, Mapping):
        raise RuntimeError("dependency_bake warning details are not structured")
    details = dict(details)
    if details.get("route") != "dependency_bake":
        raise RuntimeError(f"unexpected dependency_bake route: {details}")
    if details.get("bone") != EYE_CTRL_PRIMARY_NAME:
        raise RuntimeError(f"unexpected dependency_bake bone: {details}")
    frame_range = details.get("frame_range")
    if isinstance(frame_range, tuple):
        frame_range = list(frame_range)
    if frame_range != [0, 10]:
        raise RuntimeError(f"unexpected dependency_bake frame range: {details}")
    if details.get("generated_key_count") != 11:
        raise RuntimeError(f"unexpected dependency_bake key count: {details}")
    raw_reason = str(getattr(issue, "reason", "") or details.get("reason", "")).strip()
    if raw_reason != DEPENDENCY_BAKE_REASON:
        raise RuntimeError(
            "dependency_bake warning reason does not match TODO contract: "
            f"{raw_reason!r}"
        )
    reason = DEPENDENCY_BAKE_REASON
    return [
        {
            "path": path,
            "severity": severity,
            "reason": reason,
            "frameRange": frame_range,
            "generatedKeyCount": details["generated_key_count"],
            "details": details,
        }
    ]


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


def _eye_ctrl_joint(root: str, cmds) -> str:
    """Resolve KIRIYA's EyeCtrl by PMX metadata, not a Maya node name."""

    matches = []
    for joint in cmds.listRelatives(root, allDescendents=True, type="joint", fullPath=True) or []:
        try:
            if not all(
                cmds.attributeQuery(attribute, node=joint, exists=True)
                for attribute in ("mmd_bone_name", "mmd_bone_name_en", "mmd_bone_index")
            ):
                continue
            if (
                str(cmds.getAttr(f"{joint}.mmd_bone_name_en")) == EYE_CTRL_ENGLISH_NAME
                and str(cmds.getAttr(f"{joint}.mmd_bone_name")) == EYE_CTRL_PRIMARY_NAME
                and int(cmds.getAttr(f"{joint}.mmd_bone_index")) == EYE_CTRL_BONE_INDEX
            ):
                matches.append(str(joint))
        except (TypeError, ValueError, RuntimeError):
            continue
    if len(matches) != 1:
        raise RuntimeError(
            "KIRIYA EyeCtrl metadata resolution expected one match: "
            f"{matches}"
        )
    return matches[0]


def _skin_bindings(joint: str, cmds) -> list[dict[str, Any]]:
    """Find skin bindPreMatrix slots driven by one PMX joint."""

    joint_paths = cmds.ls(joint, long=True) or [joint]
    joint_path = str(joint_paths[0])
    bindings = []
    for skin in cmds.ls(type="skinCluster", long=True) or []:
        for logical_index in cmds.getAttr(f"{skin}.matrix", multiIndices=True) or []:
            sources = cmds.listConnections(
                f"{skin}.matrix[{logical_index}]",
                source=True,
                destination=False,
                plugs=True,
            ) or []
            matched = False
            for source in sources:
                source_node = str(source).split(".", 1)[0]
                source_paths = cmds.ls(source_node, long=True) or []
                if source_paths and str(source_paths[0]) == joint_path:
                    matched = True
                    break
            if not matched:
                continue
            bind_pre = _numbers(cmds.getAttr(f"{skin}.bindPreMatrix[{logical_index}]"))
            if len(bind_pre) != 16:
                continue
            bindings.append(
                {
                    "skin": str(skin),
                    "logicalIndex": int(logical_index),
                    "bindPreMatrix": bind_pre,
                }
            )
    return bindings


def _eye_pose_samples(root: str, cmds, frames: Iterable[int]) -> dict[str, Any]:
    """Capture EyeCtrl/eye worlds and JO-aware skin matrices."""

    joints = {
        "EyeCtrl": _eye_ctrl_joint(root, cmds),
        "leftEye": _joint_by_name(root, "左目", cmds),
        "rightEye": _joint_by_name(root, "右目", cmds),
    }
    if any(not joint for joint in joints.values()):
        raise RuntimeError(f"KIRIYA eye joint lookup failed: {joints}")
    bindings = {
        role: _skin_bindings(joint, cmds)
        for role, joint in joints.items()
        if role in {"leftEye", "rightEye"}
    }
    if any(not rows for rows in bindings.values()):
        raise RuntimeError(f"KIRIYA eye skin influence lookup failed: {bindings}")
    import maya.api.OpenMaya as om

    world = {}
    skin = {}
    for frame in frames:
        frame_key = str(int(frame))
        cmds.currentTime(int(frame), edit=True)
        cmds.refresh(force=True)
        world[frame_key] = {
            role: _matrix(joint, cmds) for role, joint in joints.items()
        }
        skin[frame_key] = {}
        for role, rows in bindings.items():
            skin[frame_key][role] = []
            for row in rows:
                bind = _numbers(
                    cmds.getAttr(f"{row['skin']}.bindPreMatrix[{row['logicalIndex']}]")
                )
                current_world = _numbers(
                    cmds.getAttr(f"{joints[role]}.worldMatrix[0]")
                )
                product = om.MMatrix(bind) * om.MMatrix(current_world)
                skin[frame_key][role].append(
                    {
                        "logicalIndex": row["logicalIndex"],
                        "matrix": [float(product[index]) for index in range(16)],
                    }
                )
    return {
        "joints": joints,
        "world": world,
        "bindings": {
            role: [
                {"skin": row["skin"], "logicalIndex": row["logicalIndex"]}
                for row in rows
            ]
            for role, rows in bindings.items()
        },
        "skin": skin,
    }


def _eye_pose_parity(
    source: Mapping[str, Any], fresh: Mapping[str, Any], frames: Iterable[int]
) -> dict[str, Any]:
    """Compare fresh PMX+VMD world and JO-aware skin observables."""

    frame_values = [int(frame) for frame in frames]
    threshold = EYE_PARITY_TOLERANCE
    divergences = []
    rows = []
    max_world = 0.0
    max_skin = 0.0
    for frame in frame_values:
        frame_key = str(frame)
        source_world = source.get("world", {}).get(frame_key, {})
        fresh_world = fresh.get("world", {}).get(frame_key, {})
        frame_row = {"frame": int(frame), "world": {}, "skin": {}}
        for role in ("EyeCtrl", "leftEye", "rightEye"):
            source_matrix = source_world.get(role, [])
            fresh_matrix = fresh_world.get(role, [])
            if len(source_matrix) != 16 or len(fresh_matrix) != 16:
                divergences.append(
                    {"frame": int(frame), "role": role, "kind": "world_shape"}
                )
                error = float("inf")
            else:
                error = max(
                    abs(float(left) - float(right))
                    for left, right in zip(source_matrix, fresh_matrix)
                )
            frame_row["world"][role] = error
            max_world = max(max_world, error)
            if error > threshold:
                divergences.append(
                    {
                        "frame": int(frame),
                        "role": role,
                        "kind": "world",
                        "maxAbsError": error,
                    }
                )
        for role in ("leftEye", "rightEye"):
            source_rows = {
                int(row["logicalIndex"]): row
                for row in source.get("skin", {}).get(frame_key, {}).get(role, [])
            }
            fresh_rows = {
                int(row["logicalIndex"]): row
                for row in fresh.get("skin", {}).get(frame_key, {}).get(role, [])
            }
            if set(source_rows) != set(fresh_rows):
                divergences.append(
                    {
                        "frame": int(frame),
                        "role": role,
                        "kind": "skin_influence_set",
                        "source": sorted(source_rows),
                        "fresh": sorted(fresh_rows),
                    }
                )
            role_errors = []
            for logical_index in sorted(set(source_rows).intersection(fresh_rows)):
                source_matrix = source_rows[logical_index].get("matrix", [])
                fresh_matrix = fresh_rows[logical_index].get("matrix", [])
                if len(source_matrix) != 16 or len(fresh_matrix) != 16:
                    error = float("inf")
                else:
                    error = max(
                        abs(float(left) - float(right))
                        for left, right in zip(source_matrix, fresh_matrix)
                    )
                role_errors.append(error)
                max_skin = max(max_skin, error)
                if error > threshold:
                    divergences.append(
                        {
                            "frame": int(frame),
                            "role": role,
                            "kind": "skin",
                            "logicalIndex": logical_index,
                            "maxAbsError": error,
                        }
                    )
            frame_row["skin"][role] = max(role_errors, default=0.0)
        rows.append(frame_row)
    return {
        "frames": frame_values,
        "threshold": threshold,
        "maxWorldMatrixError": max_world,
        "maxSkinMatrixError": max_skin,
        "divergences": divergences,
        "samples": rows,
        "pass": not divergences,
    }


def _fresh_eye_import_parity(
    model_path: str, output_path: Path, source_pose: Mapping[str, Any], cmds
) -> dict[str, Any]:
    """Import PMX and the published VMD into a fresh Maya scene and compare."""

    from mmd_tools.io.mmd_importer import import_mmd_file

    cmds.file(new=True, force=True)
    fresh_root = import_mmd_file(
        str(model_path),
        options={
            "setup_rig": True,
            "setup_bone_orientation": True,
            "import_physics": False,
            "import_morphs": True,
            "use_cpp_fast_load": False,
        },
    )
    if not fresh_root:
        raise RuntimeError("fresh PMX import returned no model root")
    fresh_root = str(fresh_root)
    imported = import_mmd_file(
        str(output_path),
        options={
            "target_model": fresh_root,
            "pmx_path": str(model_path),
            "bake_mode": False,
            "clear_existing_motion": True,
            "create_mmd_control_rig": False,
        },
    )
    if not imported:
        raise RuntimeError("fresh VMD import returned false")
    fresh_pose = _eye_pose_samples(fresh_root, cmds, EYE_CTRL_FRAMES)
    parity = _eye_pose_parity(source_pose, fresh_pose, EYE_CTRL_FRAMES)
    parity["freshRoot"] = fresh_root
    return parity


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


def _eye_motion_witness(
    eye_ctrl_delta: float,
    eye_world_deltas: Mapping[str, float],
    skin_motion_deltas: Mapping[str, float],
) -> bool:
    """Require every EyeCtrl/eye world and skin witness to be substantial."""

    threshold = EYE_MOTION_WITNESS_THRESHOLD
    return (
        float(eye_ctrl_delta) > threshold
        and all(
            float(eye_world_deltas.get(role, 0.0)) > threshold
            for role in ("leftEye", "rightEye")
        )
        and all(
            float(skin_motion_deltas.get(role, 0.0)) > threshold
            for role in ("leftEye", "rightEye")
        )
    )


def _run_eye_ctrl_oracle(
    root: str, model_path: str, cmds, output_dir: Path
) -> dict[str, Any]:
    """Run the oracle under a deterministic VMD-compatible Maya time unit."""

    original_time_unit = str(cmds.currentUnit(query=True, time=True))
    cmds.currentUnit(time="30fps")
    try:
        return _run_eye_ctrl_oracle_at_30fps(
            root, model_path, cmds, output_dir, original_time_unit
        )
    finally:
        cmds.currentUnit(time=original_time_unit)


def _run_eye_ctrl_oracle_at_30fps(
    root: str, model_path: str, cmds, output_dir: Path, original_time_unit: str
) -> dict[str, Any]:
    """Run the KIRIYA EyeCtrl dependency-bake and publication oracle.

    This deliberately creates a temporary model-local Maya utility network in
    the existing imported model scene after its normal bake/restore lifecycle.
    The utility is not serialized into PMX and is therefore required to exercise
    the collector's ``dependency_baked`` path;
    the published primary ``両目`` track and source-key snapshot are checked
    before the later fresh-import parity gate.
    """

    from mmd_tools.core.vmd_data import VmdData
    from mmd_tools.core.mmd_control_rig_motion import (
        enter_mmd_control_rig_edit,
        restore_mmd_control_rig_attached,
    )
    from mmd_tools.adapters.maya_vmd_prepare_backend import (
        create_maya_bake_timeline_vmd_action,
    )
    from mmd_tools.services.export_workflow_service import (
        ExportWorkflowRequest,
        ExportWorkflowService,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "kiriya_eye_ctrl_one_shot.vmd"
    if not root:
        raise RuntimeError("EyeCtrl oracle received no model root")
    output_preexisting = output_path.exists()
    if output_preexisting:
        output_path.unlink()
    if output_path.exists():
        raise RuntimeError(f"stale EyeCtrl output could not be removed: {output_path}")
    original_current_time = float(cmds.currentTime(query=True))
    original_playback = {
        name: float(cmds.playbackOptions(query=True, **{query: True}))
        for name, query in (
            ("minTime", "minTime"),
            ("maxTime", "maxTime"),
            ("animationStartTime", "animationStartTime"),
            ("animationEndTime", "animationEndTime"),
        )
    }
    edit = None
    utility = None
    curve = None
    restored = None
    cleanup_errors = []

    try:
        edit = enter_mmd_control_rig_edit(root, cmds_module=cmds)
        if str(edit.get("state")) != "EDIT" or str(edit.get("owner")) != "CONTROL_OWNED":
            raise RuntimeError("EyeCtrl oracle could not establish CONTROL_OWNED EDIT")
        eye_ctrl = _eye_ctrl_joint(root, cmds)
        left_eye = _joint_by_name(root, "左目", cmds)
        right_eye = _joint_by_name(root, "右目", cmds)
        if not left_eye or not right_eye:
            raise RuntimeError("EyeCtrl oracle could not resolve left/right eye joints")

        utility = cmds.createNode(
            "plusMinusAverage",
            name="MMT_KIRIYA_EyeCtrl_P0_utility",
        )
        cmds.setAttr(f"{utility}.operation", 1)
        cmds.connectAttr(f"{utility}.output1D", f"{eye_ctrl}.rotateX", force=True)
        cmds.playbackOptions(
            minTime=0,
            maxTime=10,
            animationStartTime=0,
            animationEndTime=10,
        )
        authored_values = {0: 0.0, 5: 270.0, 10: -270.0}
        for frame, degrees in authored_values.items():
            cmds.setKeyframe(
                utility,
                attribute="input1D[0]",
                time=frame,
                value=math.radians(degrees),
            )
        curve_sources = cmds.listConnections(
            f"{utility}.input1D[0]",
            source=True,
            destination=False,
            plugs=False,
        ) or []
        if len(curve_sources) != 1:
            raise RuntimeError(
                "EyeCtrl utility did not create one authored animCurve input: "
                f"{curve_sources}"
            )
        curve = str(curve_sources[0])
        curve_snapshot_before = {
            "times": [
                float(value)
                for value in (cmds.keyframe(curve, query=True, timeChange=True) or [])
            ],
            "values": [
                float(value)
                for value in (cmds.keyframe(curve, query=True, valueChange=True) or [])
            ],
        }
        if curve_snapshot_before["times"] != [0.0, 5.0, 10.0]:
            raise RuntimeError(
                f"EyeCtrl utility source keys were not created at 0/5/10: {curve_snapshot_before}"
            )
        source_pose = _eye_pose_samples(root, cmds, EYE_CTRL_FRAMES)

        eye_motion_delta = _delta(
            source_pose["world"]["0"]["EyeCtrl"],
            source_pose["world"]["10"]["EyeCtrl"],
        )
        skin_motion_deltas = {}
        for role in ("leftEye", "rightEye"):
            before = source_pose["skin"]["0"][role]
            after = source_pose["skin"]["10"][role]
            skin_motion_deltas[role] = max(
                (
                    _delta(
                        before_row.get("matrix", []),
                        after_row.get("matrix", []),
                    )
                    for before_row, after_row in zip(before, after)
                ),
                default=0.0,
            )
        eye_world_deltas = {
            role: _delta(
                source_pose["world"]["0"][role],
                source_pose["world"]["10"][role],
            )
            for role in ("leftEye", "rightEye")
        }
        if not _eye_motion_witness(
            eye_motion_delta, eye_world_deltas, skin_motion_deltas
        ):
            raise RuntimeError(
                "EyeCtrl utility motion was not a strong world/skin witness: "
                f"threshold={EYE_MOTION_WITNESS_THRESHOLD}, eye={eye_motion_delta}, "
                f"eyes={eye_world_deltas}, skin={skin_motion_deltas}"
            )
        import maya.api.OpenMayaAnim as oma

        oma.MAnimMessage.flushAnimKeyframeEditedCallbacks()

        warning_acknowledgement = {
            "invoked": False,
            "approved": False,
            "fatalRejected": False,
            "warnings": [],
        }

        def approve_warnings(validation_report: Any) -> bool:
            warning_acknowledgement["invoked"] = True
            warning_acknowledgement["warnings"] = [
                {
                    "path": str(issue.path),
                    "severity": str(issue.severity),
                    "reason": str(issue.reason),
                    "details": dict(getattr(issue, "details", {}) or {}),
                }
                for issue in getattr(validation_report, "issues", ()) or ()
            ]
            if bool(getattr(validation_report, "is_blocking", False)):
                warning_acknowledgement["fatalRejected"] = True
                return False
            warning_acknowledgement["approved"] = True
            return True

        progress = []
        published = ExportWorkflowService(
            vmd_action=create_maya_bake_timeline_vmd_action()
        ).execute(
            ExportWorkflowRequest(
                str(output_path),
                {
                    "export_format": "vmd",
                    "export_strategy": "bake_timeline",
                    "current_model_root": root,
                    "target_model": root,
                    "require_current_model": True,
                    "require_target": True,
                    "frame_range": (0.0, 10.0),
                    "frame_step": 1.0,
                },
            ),
            warning_callback=approve_warnings,
            progress_callback=progress.append,
        )
        curve_snapshot_after = {
            "times": [
                float(value)
                for value in (cmds.keyframe(curve, query=True, timeChange=True) or [])
            ],
            "values": [
                float(value)
                for value in (cmds.keyframe(curve, query=True, valueChange=True) or [])
            ],
        }
        source_keys_unchanged = curve_snapshot_before == curve_snapshot_after
        if not source_keys_unchanged:
            raise RuntimeError(
                f"EyeCtrl source keys changed during export: before={curve_snapshot_before}, "
                f"after={curve_snapshot_after}"
            )
        if not published.succeeded or not output_path.is_file():
            raise RuntimeError(
                "EyeCtrl one-shot export failed: "
                f"state={published.state!r}, error={published.error!r}, "
                f"warningAcknowledgement={warning_acknowledgement}"
            )
        published_vmd = VmdData().parse_file(str(output_path))
        vmd_witness = _eye_ctrl_vmd_witness(published_vmd, EYE_CTRL_FRAMES)
        if not vmd_witness["pass"]:
            raise RuntimeError(f"EyeCtrl VMD primary-name motion witness failed: {vmd_witness}")
        if not warning_acknowledgement["invoked"] or not warning_acknowledgement["approved"]:
            raise RuntimeError(
                "EyeCtrl dependency_baked warning was not explicitly approved: "
                f"{warning_acknowledgement}"
            )
        dependency_warnings = _dependency_warning_evidence(published.report.issues)
        if not dependency_warnings:
            raise RuntimeError(
                "EyeCtrl one-shot export did not report dependency_baked evidence: "
                f"{warning_acknowledgement['warnings']}"
            )
        expected_phases = [
            "collect",
            "encode",
            "flush",
            "output_verify",
            "cleanup",
            "warning_decision",
            "replace",
        ]
        if list(published.completed_phases) != expected_phases:
            raise RuntimeError(
                "EyeCtrl one-shot phase sequence mismatch: "
                f"{published.completed_phases!r}"
            )
        if str(published.state) != "Succeeded" or not published.succeeded:
            raise RuntimeError(
                "EyeCtrl one-shot terminal state was not Succeeded: "
                f"state={published.state!r}, succeeded={published.succeeded!r}"
            )
        action_result = published.action_result
        action_path = str(getattr(action_result, "exported_path", ""))
        if (
            action_result is None
            or not bool(getattr(action_result, "succeeded", False))
            or getattr(action_result, "error", None) is not None
            or Path(action_path).resolve() != output_path.resolve()
        ):
            raise RuntimeError(
                "EyeCtrl action_result publication facts are invalid: "
                f"{action_result!r}"
            )
        output_bytes = output_path.read_bytes()
        if not output_bytes:
            raise RuntimeError("EyeCtrl one-shot output verification produced an empty file")
        output_sha256 = hashlib.sha256(output_bytes).hexdigest()
        if not output_sha256 or output_sha256 != hashlib.sha256(output_path.read_bytes()).hexdigest():
            raise RuntimeError("EyeCtrl one-shot output hash verification failed")
    finally:
        cleanup_errors = []
        if utility and cmds.objExists(utility):
            try:
                output_plug = f"{utility}.output1D"
                destination_plug = f"{eye_ctrl}.rotateX"
                if cmds.isConnected(output_plug, destination_plug):
                    cmds.disconnectAttr(output_plug, destination_plug)
            except Exception as exc:
                cleanup_errors.append(f"utility disconnect: {type(exc).__name__}: {exc}")
        for node in (curve, utility):
            if not node:
                continue
            try:
                if cmds.objExists(node):
                    cmds.delete(node)
            except Exception as exc:
                cleanup_errors.append(f"utility delete {node}: {type(exc).__name__}: {exc}")
        try:
            cmds.playbackOptions(
                minTime=original_playback["minTime"],
                maxTime=original_playback["maxTime"],
                animationStartTime=original_playback["animationStartTime"],
                animationEndTime=original_playback["animationEndTime"],
            )
        except Exception as exc:
            cleanup_errors.append(f"playback restore: {type(exc).__name__}: {exc}")
        try:
            cmds.currentTime(original_current_time, edit=True)
        except Exception as exc:
            cleanup_errors.append(f"currentTime restore: {type(exc).__name__}: {exc}")
        try:
            restored = restore_mmd_control_rig_attached(root, cmds_module=cmds)
        except Exception as exc:
            cleanup_errors.append(f"rig restore: {type(exc).__name__}: {exc}")
            restored = {"state": "", "owner": ""}
    if cleanup_errors:
        raise RuntimeError("EyeCtrl oracle cleanup failed: " + "; ".join(cleanup_errors))
    if str(restored.get("state")) != "ATTACHED" or str(restored.get("owner")) != "MMD_OWNED":
        raise RuntimeError(f"EyeCtrl oracle cleanup state mismatch: {restored!r}")
    utility_removed = not bool(utility and cmds.objExists(utility))
    restored_current_time = float(cmds.currentTime(query=True))
    restored_playback = {
        name: float(cmds.playbackOptions(query=True, **{query: True}))
        for name, query in (
            ("minTime", "minTime"),
            ("maxTime", "maxTime"),
            ("animationStartTime", "animationStartTime"),
            ("animationEndTime", "animationEndTime"),
        )
    }
    if not utility_removed:
        raise RuntimeError("EyeCtrl utility node survived oracle cleanup")
    if restored_current_time != original_current_time or restored_playback != original_playback:
        raise RuntimeError(
            "EyeCtrl host state was not restored: "
            f"time={restored_current_time!r}, playback={restored_playback!r}"
        )
    fresh_import_parity = _fresh_eye_import_parity(
        str(model_path), output_path, source_pose, cmds
    )
    if not fresh_import_parity["pass"]:
        raise RuntimeError(f"EyeCtrl fresh PMX+VMD parity failed: {fresh_import_parity}")

    return {
        "status": "pass",
        "asset": str(model_path),
        "eyeCtrl": {
            "joint": eye_ctrl,
            "englishName": EYE_CTRL_ENGLISH_NAME,
            "primaryName": EYE_CTRL_PRIMARY_NAME,
            "boneIndex": EYE_CTRL_BONE_INDEX,
            "leftEye": left_eye,
            "rightEye": right_eye,
        },
        "mayaTimeUnit": {
            "before": original_time_unit,
            "export": "30fps",
            "after": original_time_unit,
        },
        "hostStateRestored": {
            "currentTime": restored_current_time,
            "playback": restored_playback,
            "utilityRemoved": utility_removed,
        },
        "editLifecycle": {
            "entered": {"state": str(edit.get("state")), "owner": str(edit.get("owner"))},
            "restored": {
                "state": str(restored.get("state")),
                "owner": str(restored.get("owner")),
            },
        },
        "utilityClosure": {
            "utilityNode": utility,
            "utilityNodeType": "plusMinusAverage",
            "utilityRemoved": utility_removed,
            "animCurve": curve,
            "sourceKeysBefore": curve_snapshot_before,
            "sourceKeysAfter": curve_snapshot_after,
            "sourceKeysUnchanged": source_keys_unchanged,
            "outputPreexisting": output_preexisting,
        },
        "dependencyBaked": dependency_warnings,
        "published": {
            "state": str(published.state),
            "succeeded": bool(published.succeeded),
            "output": str(output_path),
            "outputSha256": output_sha256,
            "completedPhases": list(published.completed_phases),
            "phaseTimings": dict(published.phase_timings),
            "progress": list(progress),
            "warningAcknowledgement": warning_acknowledgement,
            "actionResult": {
                "exportedPath": action_path,
                "succeeded": bool(action_result.succeeded),
                "statusMessage": str(getattr(action_result, "status_message", "")),
                "error": None if getattr(action_result, "error", None) is None else str(action_result.error),
            },
        },
        "vmd": vmd_witness,
        "freshImportParity": fresh_import_parity,
        "source": {
            "frames": list(EYE_CTRL_FRAMES),
            "world": source_pose["world"],
            "skin": source_pose["skin"],
            "eyeCtrlMotionDelta": eye_motion_delta,
            "eyeWorldDeltas": eye_world_deltas,
            "skinMotionDeltas": skin_motion_deltas,
        },
    }


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
        eye_bones = [
            bone for bone in spec.bones if str(bone.mmd_name) == EYE_CTRL_PRIMARY_NAME
        ]
        eye_roles = [
            role.role
            for role in spec.roles
            if role.binding is not None
            and str(role.binding.mmd_name) == EYE_CTRL_PRIMARY_NAME
        ]
        if len(eye_bones) != 1 or eye_roles:
            raise RuntimeError(
                "EyeCtrl analyzer unexpectedly exposed a dedicated control role: "
                f"bones={len(eye_bones)}, roles={eye_roles}"
            )
        report["graphEvidence"]["eyeCtrlControlGate"] = {
            "bone": eye_bones[0].to_dict(),
            "dedicatedControlRoles": eye_roles,
            "dedicatedControlAbsent": True,
        }
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
            dedicated_controls = [
                role
                for role, control in rig.controls.items()
                if str(role) in {"both_eyes", "eye_ctrl", "eyes"}
                or EYE_CTRL_ENGLISH_NAME.lower() in str(role).lower()
                or EYE_CTRL_PRIMARY_NAME in str(control)
            ]
            if dedicated_controls:
                raise RuntimeError(
                    "EyeCtrl rig unexpectedly exposed a dedicated control: "
                    f"{dedicated_controls}"
                )
            report["graphEvidence"]["eyeCtrlControlGate"].update(
                {
                    "dedicatedControls": dedicated_controls,
                    "dedicatedControlAbsent": True,
                }
            )
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
            report["eyeCtrlOracle"] = _run_eye_ctrl_oracle(
                root, str(model_path), cmds, report_file.parent
            )
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
    model = Path(args.model).expanduser().resolve()
    if not model.is_file():
        logger.error("model not found: %s", model)
        return 2
    try:
        command = ("import sys\nfrom pathlib import Path\n" f"project_root=Path(r'{_PROJECT_ROOT.as_posix()}')\n" "sys.path.insert(0,str(project_root)) if str(project_root) not in sys.path else None\n" "from tests.viewport.e2e_mmd_control_rig_bone_morph import run_probe\n" f"run_probe(r'{log_path.as_posix()}',r'{model.as_posix()}',r'{report_path.as_posix()}')\n")
        report = run_maya_e2e(
            project_root=_PROJECT_ROOT,
            version=args.maya,
            out_dir=out_dir,
            port=args.port,
            timeout=args.timeout,
            log_path=log_path,
            report_path=report_path,
            command=command,
            marker=COMPLETION_MARKER,
            send_label="<issue-97-bone-morph>",
            stale_paths=[report_path, log_path],
            wait_report_timeout=0,
            verify_status=False,
            report_error=f"report missing: {report_path}",
            terminate_process=False,
            quit_delay=2.0,
        )
        logger.info("status=%s report=%s", report.get("status"), report_path)
        return 0 if report.get("status") == "pass" else (1 if report.get("status") == "blocked" else 2)
    except (FileNotFoundError, TimeoutError, RuntimeError, ValueError) as exc:
        logger.error("probe blocked: %s", exc)
        return 2


if __name__ == "__main__":
    sys.exit(main())
