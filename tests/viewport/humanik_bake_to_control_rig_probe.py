"""Maya GUI/commandPort probe for a HumanIK Control Rig round trip.

The normal path imports the smallest checked-in PMX/VMD pair with the MMD
``setup_rig=True`` importer option, imports motion onto SOURCE,
characterizes SOURCE/TARGET, creates the target Control Rig through the
frontend transaction, starts TARGET preview, and invokes the public
``HumanIkFrontendSession.bake_to_control_rig`` route.  It verifies native bake,
Control Rig edit propagation, exact importer foot-IK writer isolation, and
writer/topology restoration after ``bake_from_control_rig`` teardown.
``--no-setup-rig`` keeps a short direct-joint route available for release/nightly
coverage.  Control Rig creation requires an interactive Maya GUI; a
batch/licensing/GUI obstacle is recorded verbatim as ``blocked`` in the JSON
report rather than being reported as a fabricated pass.

Host-side usage::

    python tests/viewport/humanik_bake_to_control_rig_probe.py --maya 2024

Report JSON defaults to ``build/reports/humanik_bake_to_control_rig_probe.json``.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from tests.common import maya_commandport

COMMAND_PORT = 7727
COMPLETION_MARKER = "//-- HUMANIK_BAKE_TO_CONTROL_RIG_PROBE_DONE --//"
TEST_TIMEOUT = 900
LOG_POLL_INTERVAL = 1.0

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


# These are the only importer-created CCDIK nodes that the HumanIK ownership
# policy allows to be isolated.  Keep this list explicit: a broad ``mmdCcdIk``
# mute would hide a genuine writer conflict or DG cycle in a release gate.
EXPECTED_FOOT_CCDIK_NODES = frozenset(
    {
        "left_leg_ik_mmdCcdIk",
        "left_toe_ik_mmdCcdIk",
        "right_leg_ik_mmdCcdIk",
        "right_toe_ik_mmdCcdIk",
    }
)


def _foot_ccdik_key(node: str) -> Optional[str]:
    """Return the canonical importer foot-IK name for a Maya node path."""
    leaf = str(node).rsplit("|", 1)[-1].rsplit(":", 1)[-1]
    return next(
        (expected for expected in EXPECTED_FOOT_CCDIK_NODES if expected.lower() == leaf.lower()),
        None,
    )


def _is_gui_obstacle(error: str) -> bool:
    """Classify known commandPort/HumanIK interactive-runtime blockers."""
    text = str(error).lower()
    return any(
        marker in text
        for marker in (
            "about -batch",
            "batch mode",
            "requires an interactive",
            "humanik character controls ui",
            "license",
            "licensing",
            "could not open commandport",
            "maya executable not found",
        )
    )


def _write_report(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


# ===================================================================
# Maya-side: called inside the live GUI through commandPort
# ===================================================================
def run_probe(
    log_path: str,
    pmx_path: str,
    vmd_path: str,
    report_path: str,
    end: int = 10,
    setup_rig: bool = True,
) -> None:
    import traceback

    import maya.cmds as cmds
    import maya.mel as mel

    from mmd_tools.core.humanik_frontend import (
        FULL_ASSIGNMENT_PROFILE,
        HumanIkFrontendSession,
    )

    report: Dict[str, Any] = {
        "status": "error",
        "mayaVersion": None,
        "pmxPath": pmx_path,
        "vmdPath": vmd_path,
        "setupRig": bool(setup_rig),
        "frameRange": {"start": 0, "end": int(end)},
        "checks": {},
        "errors": [],
    }
    session: Optional[HumanIkFrontendSession] = None

    def _log(message: str) -> None:
        with open(log_path, "a", encoding="utf-8") as stream:
            stream.write(str(message) + "\n")
        try:
            print(message)
        except Exception:
            pass

    def _load_plugin() -> None:
        plugin_path = _PROJECT_ROOT / "mmd_tools" / "plugin_main.py"
        if not cmds.pluginInfo(plugin_path.stem, query=True, loaded=True):
            cmds.loadPlugin(str(plugin_path), quiet=True)

    def _import_model(path: Path, *, setup_rig: bool = True) -> str:
        from mmd_tools.io.mmd_importer import import_mmd_file

        root = import_mmd_file(
            str(path),
            options={
                "use_namespace": True,
                "setup_rig": bool(setup_rig),
                "import_physics": False,
                "create_mmd_shaders": False,
                "use_native_pmx_parse": False,
                "require_native_pmx_parse": False,
            },
        )
        if not root:
            raise RuntimeError(f"PMX import failed: {path}")
        return str(root)

    def _import_motion(path: Path, model: Path, target_root: str) -> None:
        from mmd_tools.io.mmd_importer import import_mmd_file

        if not import_mmd_file(
            str(path),
            options={
                "target_model": target_root,
                "pmx_path": str(model),
                "bake_mode": False,
                "clear_existing_motion": True,
                "use_native_pmx_parse": False,
                "require_native_pmx_parse": False,
            },
        ):
            raise RuntimeError(f"VMD import failed: {path}")

    def _sample_joints(joints):
        cmds.currentTime(0, edit=True)
        cmds.refresh(force=True)
        samples = {}
        for joint in joints:
            for channel in ("translateX", "translateY", "translateZ", "rotateX", "rotateY", "rotateZ"):
                value = cmds.getAttr(f"{joint}.{channel}")
                while isinstance(value, (tuple, list)) and len(value) == 1:
                    value = value[0]
                try:
                    samples[(str(joint), channel)] = float(value)
                except (TypeError, ValueError):
                    continue
        return samples

    def _world_position(node: str):
        """Return one DAG node's evaluated world-space translation."""
        value = cmds.xform(node, query=True, worldSpace=True, translation=True)
        return tuple(float(component) for component in value)

    def _edge_connected(source: str, destination: str) -> bool:
        """Return whether one exact source/destination DG edge is connected."""
        try:
            return bool(cmds.isConnected(str(source), str(destination)))
        except Exception:
            incoming = cmds.listConnections(
                str(destination), source=True, destination=False, plugs=True
            ) or []
            return str(source) in {str(value) for value in incoming}

    def _foot_ownership_rows(
        ownership: Dict[str, Any], model_root: str
    ) -> Dict[str, Dict[str, Any]]:
        """Index the four reviewed importer foot CCDIK rows by canonical name."""
        # ``inspect_target_ownership`` has already scoped this report to the
        # characterized target joints.  Do not compare its destination plugs
        # with assignment strings again: Maya may return one side as a full DAG
        # path and the other as a short name.
        raw_rows = ownership.get("constraintRows")
        if raw_rows is None:
            nested_ownership = ownership.get("ownership")
            raw_rows = (
                nested_ownership.get("rows", ())
                if isinstance(nested_ownership, dict)
                else ownership.get("rows", ())
            )
        rows: Dict[str, Dict[str, Any]] = {}
        root_leaf = str(model_root).rsplit("|", 1)[-1]
        namespace = root_leaf.split(":", 1)[0] if ":" in root_leaf else ""
        for row in raw_rows:
            if row.get("nodeType") != "mmdCcdIk":
                continue
            node = str(row.get("node", ""))
            node_leaf = node.rsplit("|", 1)[-1]
            if namespace and not node_leaf.startswith(f"{namespace}:"):
                continue
            key = _foot_ccdik_key(node)
            if key is not None:
                rows[key] = dict(row)
        return rows

    def _foot_transaction_edges(edges) -> List[Dict[str, str]]:
        """Keep only transaction edges owned by the reviewed foot CCDIK set."""
        return [
            dict(edge)
            for edge in edges or ()
            if _foot_ccdik_key(
                str(edge.get("node") or str(edge.get("source", "")).rsplit(".", 1)[0])
            )
            is not None
        ]

    def _writer_edges_for_nodes(rows: Dict[str, Dict[str, Any]]) -> List[Dict[str, str]]:
        """Capture exact source/destination edges for reviewed foot CCDIK rows."""
        edges: List[Dict[str, str]] = []
        for row in rows.values():
            node = str(row.get("node", ""))
            for destination in sorted(str(value) for value in row.get("writes", ())):
                for source in cmds.listConnections(
                    destination, source=True, destination=False, plugs=True
                ) or []:
                    source = str(source)
                    if source.rsplit(".", 1)[0] == node:
                        edges.append(
                            {
                                "node": node,
                                "source": source,
                                "destination": destination,
                            }
                        )
        return sorted(edges, key=lambda item: (item["node"], item["destination"], item["source"]))

    def _foot_ik_isolation(
        ownership: Dict[str, Any],
        transaction,
        *,
        model_root: str,
        recorded_edges: Optional[List[Dict[str, str]]] = None,
    ) -> Dict[str, Any]:
        """Describe the reviewed foot set and exact edge state at one lifecycle point."""
        rows = _foot_ownership_rows(ownership, model_root)
        expected = sorted(EXPECTED_FOOT_CCDIK_NODES)
        isolated_nodes = sorted(str(node) for node in (transaction.isolated_feedback_nodes if transaction else ()))
        edges = _foot_transaction_edges(
            recorded_edges
            if recorded_edges is not None
            else (transaction.disconnected if transaction else ())
        )
        edge_states = [
            {
                **dict(edge),
                "connected": _edge_connected(edge["source"], edge["destination"]),
            }
            for edge in edges
        ]
        row_names = sorted(rows)
        row_feedback = all(
            rows.get(name, {}).get("classification") in {"mute_for_hik", "feedback_blocker"}
            and bool(rows.get(name, {}).get("writes"))
            for name in expected
        )
        node_set_matches = set(row_names) == set(expected)
        disconnected_nodes = {
            _foot_ccdik_key(str(edge.get("node") or edge.get("source", "")))
            for edge in edges
            if _foot_ccdik_key(str(edge.get("node") or edge.get("source", ""))) is not None
        }
        isolated_nodes = sorted(
            {
                node for node in isolated_nodes if _foot_ccdik_key(node) is not None
            }
            | {
                str(edge.get("node") or str(edge.get("source", "")).rsplit(".", 1)[0])
                for edge in edges
                if edge.get("node") or edge.get("source")
            }
        )
        isolated_set_matches = {
            _foot_ccdik_key(node) or str(node).rsplit("|", 1)[-1].rsplit(":", 1)[-1]
            for node in isolated_nodes
        } == set(expected) and disconnected_nodes.issubset(set(expected))
        return {
            "expectedNodes": expected,
            "ownershipRows": rows,
            "ownershipNodeSetMatches": node_set_matches,
            "allRowsReviewedFeedback": row_feedback,
            "isolatedFeedbackNodes": isolated_nodes,
            "isolatedNodeSetMatches": isolated_set_matches,
            "recordedEdges": edges,
            "edgeStates": edge_states,
            "recordedEdgesCount": len(edges),
            "allRecordedEdgesDisconnected": bool(edges) and not any(
                state["connected"] for state in edge_states
            ),
            "allRecordedEdgesRestored": bool(edges) and all(
                state["connected"] for state in edge_states
            ),
        }

    def _distance(left, right) -> float:
        """Return the Euclidean distance between two world-space points."""
        return sum((float(a) - float(b)) ** 2 for a, b in zip(left, right)) ** 0.5

    def _move_control_attribute(effector: str, attribute: str, amount: float) -> Dict[str, Any]:
        """Key an editable Control Rig channel at zero and describe the edit.

        A relative ``keyframe`` edit is a no-op when a baked control has no
        key at exactly frame zero.  Writing an explicit key models an actual
        animator edit and removes that false-negative path.  The caller wraps
        this in an undo chunk, so no test edit survives the drive check.
        """
        attribute_path = f"{effector}.{attribute}"
        curves = cmds.listConnections(
            attribute_path,
            source=True,
            destination=False,
            type="animCurve",
        ) or []
        before = float(cmds.getAttr(attribute_path))
        if curves:
            if len(curves) != 1:
                raise RuntimeError(
                    "Control Rig foot channel has ambiguous animation curves: "
                    f"{attribute_path} -> {[str(curve) for curve in curves]}"
                )
            cmds.keyframe(
                curves[0],
                edit=True,
                time=(0, 0),
                absolute=True,
                valueChange=before + float(amount),
            )
            return {
                "route": "animCurveKey",
                "before": before,
                "after": float(cmds.getAttr(attribute_path)),
                "curves": [str(curve) for curve in curves],
            }
        if not cmds.getAttr(attribute_path, settable=True):
            raise RuntimeError(f"Control Rig foot channel is not editable: {attribute_path}")
        cmds.setAttr(attribute_path, before + float(amount))
        return {"route": "attribute", "before": before, "after": float(cmds.getAttr(attribute_path))}

    def _verify_foot_effector_drive(assignments, character: str):
        """Require each ankle IK control to move its characterized ankle.

        Characterization readback alone proves only that HIK slots are
        populated. Maya names the ankle-driving IK controller
        ``LeftAnkleEffector`` / ``RightAnkleEffector`` even though their
        characterized slots are ``LeftFoot`` / ``RightFoot``; the similarly
        named Foot Effectors drive the toe-base portion of the HIK rig.
        This direct control-to-skeleton check catches a broken Control Rig leg
        chain even when a generic hips effector still works.
        """
        joints_by_slot = {str(item.hik_bone): str(item.joint) for item in assignments}
        ik_nodes = sorted(
            str(node)
            for node in (mel.eval(f'hikGetRigIkNodes("{character}")') or [])
            if node and cmds.objExists(str(node))
        )
        checks = []
        for slot, effector_name in (
            ("LeftFoot", "LeftAnkleEffector"),
            ("RightFoot", "RightAnkleEffector"),
        ):
            # Bake preserves the caller's current frame, while this probe
            # deliberately offsets the keys authored at frame zero.  Evaluate
            # that same frame before measuring the HIK output.
            cmds.currentTime(0, edit=True)
            cmds.refresh(force=True)
            joint = joints_by_slot.get(slot)
            candidates = [
                node for node in ik_nodes
                if effector_name.lower() in node.lower()
            ]
            if not joint or not candidates:
                checks.append(
                    {
                        "slot": slot,
                        "joint": joint,
                        "effectors": candidates,
                        "moved": False,
                        "error": "missing characterized joint or Ankle Effector",
                    }
                )
                continue
            effector = candidates[0]
            before = _world_position(joint)
            edit = None
            error = None
            cmds.undoInfo(openChunk=True, chunkName=f"HumanIKFootDrive:{slot}")
            try:
                edit = _move_control_attribute(effector, "translateX", 1.0)
                cmds.refresh(force=True)
                after = _world_position(joint)
                movement = _distance(before, after)
            except Exception as exc:  # noqa: BLE001 - retain Maya-side diagnosis
                after = None
                movement = 0.0
                error = str(exc)
            finally:
                cmds.undoInfo(closeChunk=True)
                try:
                    cmds.undo()
                    cmds.refresh(force=True)
                except Exception as restore_exc:  # noqa: BLE001 - include recovery failure
                    error = f"{error or ''}; restore={restore_exc}".strip("; ")
            checks.append(
                {
                    "slot": slot,
                    "joint": joint,
                    "effector": effector,
                    "edit": edit,
                    "before": before,
                    "after": after,
                    "movement": movement,
                    "moved": error is None and movement > 1.0e-4,
                    "error": error,
                }
            )
        return checks

    def _edit_control_rig(joints, character):
        ik_nodes = [
            str(node)
            for node in (mel.eval(f'hikGetRigIkNodes("{character}")') or [])
            if node and cmds.objExists(str(node))
        ]
        candidates = [
            ("hikIkNode", node)
            for node in ik_nodes
        ] + [
            ("effectorTransform", str(node))
            for node in (cmds.ls(type="transform", long=True) or [])
            if "effector" in str(node).lower()
            and re.search(r"(LeftHand|RightHand|LeftFoot|RightFoot)", str(node), re.IGNORECASE)
        ]
        if not candidates:
            raise RuntimeError("No editable HIK Control Rig IK node or effector transform was found")
        candidates.sort(key=lambda item: (item[0] != "hikIkNode", item[1]))
        diagnostics = []
        for candidate_kind, candidate in candidates:
            effector = candidate
            for edit_kind, edit_values, undo_values in (
                ("translation", (1.0, 0.0, 0.0), (-1.0, 0.0, 0.0)),
                ("rotation", (20.0, 0.0, 0.0), (-20.0, 0.0, 0.0)),
            ):
                before = _sample_joints(joints)
                attribute = "translateX" if edit_kind == "translation" else "rotateX"
                attribute_path = f"{effector}.{attribute}"
                driver_curves = cmds.listConnections(
                    attribute_path,
                    source=True,
                    destination=False,
                    type="animCurve",
                ) or []
                edit_mode = "keyframe" if driver_curves else "attribute"
                try:
                    if driver_curves:
                        # Bake-To writes keys on Control Rig IK nodes.  Editing
                        # the connected plug directly is a no-op in Maya, so
                        # move the current-frame key on its animCurve instead.
                        for curve in driver_curves:
                            cmds.keyframe(
                                curve,
                                time=(0, 0),
                                relative=True,
                                valueChange=edit_values[0],
                            )
                        current_attribute = None
                    else:
                        current_attribute = float(cmds.getAttr(attribute_path))
                        cmds.setAttr(attribute_path, current_attribute + edit_values[0])
                except Exception as exc:
                    diagnostics.append(
                        {
                            "effector": effector,
                            "candidateKind": candidate_kind,
                            "editKind": edit_kind,
                            "editMode": edit_mode,
                            "driverCurves": [str(curve) for curve in driver_curves],
                            "error": str(exc),
                        }
                    )
                    continue
                cmds.refresh(force=True)
                after = _sample_joints(joints)
                changed = []
                for (joint, channel), before_value in before.items():
                    after_value = after.get((joint, channel))
                    if after_value is not None and abs(after_value - before_value) > 1.0e-4:
                        changed.append(
                            {
                                "joint": joint,
                                "channel": channel,
                                "before": before_value,
                                "after": after_value,
                                "delta": after_value - before_value,
                                "editKind": edit_kind,
                                "candidateKind": candidate_kind,
                            }
                        )
                if changed:
                    return effector, changed[0]
                diagnostics.append(
                    {
                        "effector": effector,
                        "candidateKind": candidate_kind,
                        "editKind": edit_kind,
                        "editMode": edit_mode,
                        "driverCurves": [str(curve) for curve in driver_curves],
                        "settable": {
                            channel: {
                                "lock": bool(cmds.getAttr(f"{effector}.{channel}", lock=True)),
                                "settable": bool(cmds.getAttr(f"{effector}.{channel}", settable=True)),
                            }
                            for channel in ("translateX", "rotateX")
                        },
                    }
                )
                if driver_curves:
                    for curve in driver_curves:
                        cmds.keyframe(
                            curve,
                            time=(0, 0),
                            relative=True,
                            valueChange=undo_values[0],
                        )
                else:
                    cmds.setAttr(attribute_path, current_attribute + undo_values[0])
                cmds.refresh(force=True)
        raise RuntimeError(
            "Control Rig edit did not change a target joint: "
            + ", ".join(item[1] for item in candidates)
            + f"; diagnostics={diagnostics}"
        )

    def _restore() -> None:
        if session is None:
            return
        try:
            report["restoreMmdRigReturned"] = bool(session.restore_mmd_rig())
            report["stateAfterRestore"] = session.describe_frontend_state(report.get("targetRoot"))
            target_character = report.get("targetCharacter")
            report["controlRigClearedAfterRestore"] = not bool(
                target_character
                and mel.eval(f'hikHasControlRig("{target_character}")')
            )
            report["previewClearedAfterRestore"] = session.active_preview is None
            report["transactionCountAfterRestore"] = len(
                [item for item in session._control_rig_transactions.values() if item.active]
            )
        except Exception as exc:  # noqa: BLE001 - report exact restore obstacle
            report["restoreError"] = str(exc)
            report["errors"].append("restore: " + traceback.format_exc())

    try:
        report["mayaVersion"] = cmds.about(version=True)
        pmx = Path(pmx_path).resolve()
        vmd = Path(vmd_path).resolve()
        if not pmx.is_file() or not vmd.is_file():
            raise FileNotFoundError(f"Fixtures not found: pmx={pmx} vmd={vmd}")
        cmds.file(new=True, force=True)
        _load_plugin()

        # The normal regression path deliberately keeps the importer-created MMD
        # CCDIK graph.  The Control Rig transaction must isolate only the four
        # reviewed foot nodes while it owns TARGET; ``setup_rig=False`` remains
        # available for short direct-joint release/nightly coverage.
        source_root = _import_model(pmx, setup_rig=setup_rig)
        target_root = _import_model(pmx, setup_rig=setup_rig)
        report.update({"sourceRoot": source_root, "targetRoot": target_root})

        session = HumanIkFrontendSession(cmds_module=cmds, mel_module=mel)
        target_binding = session.setup_and_characterize(
            target_root,
            profile=FULL_ASSIGNMENT_PROFILE,
            include_fingers=True,
        )
        ownership_before_control_rig = session.inspect_target_ownership(target_root)
        foot_before = _foot_ownership_rows(ownership_before_control_rig, target_root)
        report.update(
            {
                "targetCharacter": target_binding.character,
                "targetAssignmentCount": len(target_binding.assignments),
                "targetFootOwnershipBeforeControlRig": foot_before,
            }
        )

        if setup_rig and set(foot_before) != set(EXPECTED_FOOT_CCDIK_NODES):
            raise RuntimeError(
                "Expected exactly four importer foot mmdCcdIk ownership rows in setup_rig=True mode: "
                f"found={sorted(foot_before)}"
            )
        if setup_rig and not all(
            row.get("classification") in {"mute_for_hik", "feedback_blocker"}
            and row.get("writes")
            for row in foot_before.values()
        ):
            raise RuntimeError(
                "Importer foot mmdCcdIk rows were not all reviewed feedback blockers: "
                f"{foot_before}"
            )

        session.create_control_rig(target_root)
        target_character = target_binding.character
        rig_before_preview = bool(mel.eval(f'hikHasControlRig("{target_character}")'))
        transaction = session._control_rig_transactions.get(target_root)
        foot_transaction = _foot_ik_isolation(
            ownership_before_control_rig,
            transaction,
            model_root=target_root,
        )
        report["footIkIsolationAfterControlRig"] = foot_transaction
        report["checks"].update(
            {
                "footIkNodesReviewedAndIsolated": (
                    not setup_rig
                    or (
                        foot_transaction["ownershipNodeSetMatches"]
                        and foot_transaction["allRowsReviewedFeedback"]
                        and foot_transaction["isolatedNodeSetMatches"]
                        and foot_transaction["recordedEdgesCount"] > 0
                    )
                ),
                "footIkWriterEdgesDisconnectedBeforePreview": (
                    not setup_rig
                    or foot_transaction["allRecordedEdgesDisconnected"]
                ),
            }
        )

        # Match the GUI regression order: the selected TARGET already owns an
        # active Control Rig when an uncharacterized SOURCE is connected.  Its
        # temporarily disconnected foot writers must not pollute the SOURCE's
        # canonical-stance ownership report.
        source_binding = session.setup_and_characterize(
            source_root,
            profile=FULL_ASSIGNMENT_PROFILE,
            include_fingers=True,
        )
        session.enter_source_mode(source_root)
        _import_motion(vmd, pmx, source_root)
        source_anim_curve_count = len(cmds.ls(type="animCurve") or [])
        report.update(
            {
                "sourceCharacter": source_binding.character,
                "sourceAssignmentCount": len(source_binding.assignments),
                "sourceAnimCurveCountAfterVmd": source_anim_curve_count,
            }
        )
        report["checks"].update(
            {
                "targetControlRigActiveBeforeSourceCharacterize": rig_before_preview,
                "sourceCharacterizedAfterTargetControlRig": bool(
                    source_binding.character and source_binding.assignments
                ),
            }
        )
        session.enter_target_mode(target_root)
        state_before = session.describe_frontend_state(target_root)
        transaction = session._control_rig_transactions.get(target_root)
        foot_preview = _foot_ik_isolation(
            ownership_before_control_rig,
            transaction,
            model_root=target_root,
            recorded_edges=(transaction.disconnected if transaction else []),
        )
        report["footIkIsolationDuringPreview"] = foot_preview
        report["stateBeforeBake"] = state_before
        report["checks"].update(
            {
                "sourceMotionImported": source_anim_curve_count > 0,
                "targetControlRigBeforePreview": rig_before_preview,
                "previewActiveBeforeBake": session.active_preview is not None,
                "transactionActiveBeforeBake": bool(transaction and transaction.active),
                "footIkWriterEdgesDisconnectedDuringPreview": (
                    not setup_rig or foot_preview["allRecordedEdgesDisconnected"]
                ),
            }
        )

        bake_to_result = session.bake_to_control_rig(0, int(end))
        transaction_after = session._control_rig_transactions.get(target_root)
        foot_after_bake_to = _foot_ik_isolation(
            ownership_before_control_rig,
            transaction_after,
            model_root=target_root,
            recorded_edges=(transaction_after.disconnected if transaction_after else []),
        )
        report["footIkIsolationAfterBakeTo"] = foot_after_bake_to
        recorded_foot_edges = _foot_transaction_edges(
            transaction_after.disconnected if transaction_after else ()
        )
        input_type = int(mel.eval(f'hikGetInputType("{target_character}")'))
        state_after_bake_to = session.describe_frontend_state(target_root)
        report.update(
            {
                "bakeToResult": bake_to_result.to_dict(),
                "stateAfterBakeTo": state_after_bake_to,
                "targetInputTypeAfterBake": input_type,
            }
        )
        report["checks"].update(
            {
                "nativeControlRigAfterBake": bool(
                    mel.eval(f'hikHasControlRig("{target_character}")')
                ),
                "transactionActiveAfterBake": bool(transaction_after and transaction_after.active),
                "previewActiveAfterBake": session.active_preview is not None,
                "controlRigCountAfterBake": len(state_after_bake_to.get("controlRigs") or []),
                # hikBakeToControlRigPost switches the character to the live
                # Control Rig input (Maya 2024 reports the native enum as 1;
                # direct SOURCE input is 3).
                "controlRigInputAfterBake": input_type == 1,
                "footIkWriterEdgesDisconnectedAfterBakeTo": (
                    not setup_rig or foot_after_bake_to["allRecordedEdgesDisconnected"]
                ),
            }
        )
        foot_drive = _verify_foot_effector_drive(target_binding.assignments, target_character)
        report["ankleEffectorDrive"] = foot_drive
        report["checks"]["bothAnkleEffectorsDriveCharacterizedAnkles"] = all(
            bool(item.get("moved")) for item in foot_drive
        )
        if not all(report["checks"].values()):
            raise RuntimeError(f"Control Rig bake acceptance failed: {report['checks']}")

        edited_effector, changed_channel = _edit_control_rig(
            [assignment.joint for assignment in target_binding.assignments],
            target_character,
        )
        report["controlRigEdit"] = {
            "effector": edited_effector,
            "changedChannel": changed_channel,
        }
        bake_from_result = session.bake_from_control_rig(0, int(end))
        state_after_bake_from = session.describe_frontend_state(target_root)
        ownership_after_bake_from = session.inspect_target_ownership(target_root)
        foot_after_bake_from = _foot_ik_isolation(
            ownership_after_bake_from,
            None,
            model_root=target_root,
            recorded_edges=recorded_foot_edges,
        )
        restored_foot_edges = _writer_edges_for_nodes(
            _foot_ownership_rows(ownership_after_bake_from, target_root)
        )
        recorded_edge_pairs = {
            (str(edge["source"]), str(edge["destination"]))
            for edge in recorded_foot_edges
        }
        restored_edge_pairs = {
            (str(edge["source"]), str(edge["destination"]))
            for edge in restored_foot_edges
        }
        report["footIkRestorationAfterBakeFrom"] = {
            **foot_after_bake_from,
            "restoredEdges": restored_foot_edges,
            "recordedTopologyRestored": recorded_edge_pairs == restored_edge_pairs,
        }
        keyed_channel = f"{changed_channel['joint']}.{changed_channel['channel']}"
        keyed_curves = cmds.listConnections(
            keyed_channel,
            source=True,
            destination=False,
            type="animCurve",
        ) or []
        report.update(
            {
                "bakeFromResult": bake_from_result.to_dict(),
                "stateAfterBakeFrom": state_after_bake_from,
                "targetInputTypeAfterBakeFrom": int(
                    mel.eval(f'hikGetInputType("{target_character}")')
                ),
            }
        )
        report["checks"].update(
            {
                "editedJointChannelChanged": abs(changed_channel["delta"]) > 1.0e-4,
                "bakeFromKeyCountPositive": bake_from_result.key_count > 0,
                "bakeFromResidualWithinTolerance": bake_from_result.max_error <= 1.0e-5,
                "targetEditedChannelKeyed": bool(keyed_curves),
                "previewClearedAfterBakeFrom": session.active_preview is None,
                "controlRigClearedAfterBakeFrom": not bool(
                    mel.eval(f'hikHasControlRig("{target_character}")')
                ),
                "noActiveTransactionsAfterBakeFrom": not any(
                    item.active for item in session._control_rig_transactions.values()
                ),
                "footIkWriterEdgesRestoredAfterBakeFrom": (
                    not setup_rig
                    or foot_after_bake_from["allRecordedEdgesRestored"]
                ),
                "footIkTopologyRestoredAfterBakeFrom": (
                    not setup_rig
                    or (
                        foot_after_bake_from["ownershipNodeSetMatches"]
                        and foot_after_bake_from["allRowsReviewedFeedback"]
                        and report["footIkRestorationAfterBakeFrom"]["recordedTopologyRestored"]
                    )
                ),
            }
        )
        report["targetEditedChannelAnimCurves"] = sorted(str(curve) for curve in keyed_curves)
        if not all(report["checks"].values()):
            raise RuntimeError(f"Bake From Control Rig acceptance failed: {report['checks']}")
        report["status"] = "pass"
    except Exception as exc:  # noqa: BLE001 - completion/report must always be emitted
        text = str(exc)
        report["error"] = text
        report["errors"].append(traceback.format_exc())
        report["status"] = "blocked" if _is_gui_obstacle(text) else "error"
    finally:
        _restore()
        if report["status"] == "pass":
            # Bake-From intentionally tears down the target preview and
            # Control Rig as part of the successful authoring operation.  In
            # that completed state restore_mmd_rig() correctly returns False
            # because there is nothing left to restore; the post-bake state is
            # the acceptance evidence instead.
            restore_ok = all(
                (
                    report.get("controlRigClearedAfterRestore"),
                    report.get("previewClearedAfterRestore"),
                    report.get("transactionCountAfterRestore") == 0,
                )
            )
            if not restore_ok:
                report["status"] = "error"
                report["errors"].append("Restore acceptance failed")
        _write_report(Path(report_path), report)
        _log("RESULT_JSON: " + json.dumps({"status": report["status"], "error": report.get("error")}))
        _log(COMPLETION_MARKER)


# ===================================================================
# Host-side launcher/commandPort driver
# ===================================================================
def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "HumanIK bake-to-Control-Rig GUI regression probe "
            "(default: full importer MMD rig round trip)"
        )
    )
    parser.add_argument("--maya", default="2024")
    parser.add_argument("--pmx", default="tests/data/mmt_test_model.pmx")
    parser.add_argument("--vmd", default="tests/data/mmt_test_model_test_motion.vmd")
    parser.add_argument("--end", type=int, default=10)
    setup_group = parser.add_mutually_exclusive_group()
    setup_group.add_argument(
        "--setup-rig",
        dest="setup_rig",
        action="store_true",
        help="Use the full importer MMD rig path (default).",
    )
    setup_group.add_argument(
        "--no-setup-rig",
        dest="setup_rig",
        action="store_false",
        help=(
            "Use direct-joint importer mode for short release/nightly coverage "
            "(for example, --end 1)."
        ),
    )
    parser.set_defaults(setup_rig=True)
    parser.add_argument("--port", type=int, default=COMMAND_PORT)
    parser.add_argument(
        "--out",
        default="build/reports/humanik_bake_to_control_rig_probe.json",
    )
    args = parser.parse_args()

    pmx_path = (_PROJECT_ROOT / args.pmx).resolve()
    vmd_path = (_PROJECT_ROOT / args.vmd).resolve()
    output = (_PROJECT_ROOT / args.out).resolve()
    log_dir = _PROJECT_ROOT / "build" / "e2e"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "humanik_bake_to_control_rig_probe.log"
    if log_path.exists():
        log_path.unlink()

    base_report: Dict[str, Any] = {
        "status": "error",
        "mayaVersion": None,
        "pmxPath": str(pmx_path),
        "vmdPath": str(vmd_path),
        "setupRig": bool(args.setup_rig),
        "frameRange": {"start": 0, "end": args.end},
        "errors": [],
    }
    proc = None
    maya_port_ready = False
    try:
        if not pmx_path.is_file() or not vmd_path.is_file():
            obstacle = f"Fixtures not found: pmx={pmx_path} vmd={vmd_path}"
            base_report.update({"status": "blocked", "obstacle": obstacle})
            _write_report(output, base_report)
            logger.error(obstacle)
            return 2
        proc = maya_commandport.launch_maya(
            version=args.maya,
            project_root=_PROJECT_ROOT,
            output_dir=log_dir,
            port=args.port,
            launch_mode="explorer" if sys.platform == "win32" else "direct",
        )
        maya_commandport.wait_for_port(args.port, timeout=120, process=proc)
        maya_port_ready = True
        command = (
            "import importlib, sys; "
            f"sys.path.insert(0, {json.dumps(str(_PROJECT_ROOT))}); "
            "import tests.viewport.humanik_bake_to_control_rig_probe as _probe; "
            "importlib.reload(_probe); "
            f"_probe.run_probe({json.dumps(str(log_path))}, {json.dumps(str(pmx_path))}, "
            f"{json.dumps(str(vmd_path))}, {json.dumps(str(output))}, {int(args.end)}, "
            f"{bool(args.setup_rig)})"
        )
        maya_commandport.send_python(args.port, command, label="<humanik-bake-to-control-rig-probe>")

        start = time.time()
        done = False
        while time.time() - start < TEST_TIMEOUT:
            if log_path.exists():
                for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
                    if COMPLETION_MARKER in line:
                        done = True
            if done:
                break
            time.sleep(LOG_POLL_INTERVAL)
        if not done:
            raise TimeoutError(f"Probe did not finish within {TEST_TIMEOUT}s")
        if output.exists():
            payload = json.loads(output.read_text(encoding="utf-8"))
            logger.info("probe status=%s report=%s", payload.get("status"), output)
            return 0 if payload.get("status") == "pass" else (2 if payload.get("status") == "blocked" else 1)
        return 1
    except Exception as exc:  # exact launcher/GUI obstacle goes into report
        obstacle = str(exc)
        base_report.update(
            {
                "status": "blocked" if _is_gui_obstacle(obstacle) else "error",
                "obstacle": obstacle,
                "errors": [obstacle],
            }
        )
        _write_report(output, base_report)
        logger.error("probe did not run: %s", obstacle)
        return 2 if base_report["status"] == "blocked" else 1
    finally:
        if maya_port_ready:
            try:
                # The imported/edited probe scene can keep Maya's normal quit
                # request alive despite ``force=True``.  Clear only this
                # dedicated probe process before asking it to exit.
                maya_commandport.send_python(
                    args.port,
                    "import maya.cmds as cmds\n"
                    "cmds.file(new=True, force=True)\n"
                    "cmds.quit(force=True)\n",
                    label="<humanik-bake-to-control-rig-probe-cleanup>",
                )
            except Exception:
                pass
        if proc is not None:
            maya_commandport.close_process_logs(proc)


if __name__ == "__main__":
    raise SystemExit(main())
