"""Maya 2024 mayapy probe for HUMANIK-EXTERNAL-SOURCE-1 (ES-2).

Verifies that ``HumanIkFrontendSession.enter_external_source_mode`` can drive
a HumanIK TARGET preview from a *non-MMD* HIK character -- a synthetic
skeleton built and characterized entirely inside this probe, standing in for
a mocap performer characterized outside mmd_tools -- exactly the way
``enter_source_mode`` drives it from an MMD binding.

No HumanIK Control Rig / Character Controls UI is involved, so this probe
runs under plain ``mayapy`` (no Maya GUI, no ``commandPort``), unlike the
Control-Rig-touching probes in this directory.

Steps:

1. Import the checked-in MMD PMX+VMD fixture and characterize it as TARGET.
2. Build a synthetic ~15-joint external skeleton with ``cmds.joint`` and
   characterize+lock it as its own HIK character (never touching the MMD
   fixture's characterization machinery).
3. Key a few frames of translate/rotate animation on the external skeleton.
4. ``enter_external_source_mode`` + ``enter_target_mode`` to connect it as
   SOURCE, and check ``hikGetInputType``/``hikGetRetargetCharacterInput``.
5. Drive the external Hips and confirm the TARGET Hips follows
   (``verify_root_locomotion``).
6. ``bake_to_mmd_rig`` and check a nonzero key count.
7. ``restore_mmd_rig`` and confirm the external skeleton's local
   transform channels are exactly what they were before any of this ran --
   this session must never mutate a character it does not own.

Usage (host side, spawns mayapy)::

    python tests/viewport/humanik_external_source_probe.py --maya 2024

Report JSON: ``build/reports/humanik_external_source_probe.json``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from pathlib import Path
from typing import Any, Dict, List, Tuple

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


# HIK body-slot indices used to characterize the synthetic external skeleton
# (see mmd_tools/config/humanik_mapping.py::MMD_TO_HIK_BONE_INDEX for the
# canonical table this subset is drawn from).
CHANNELS: Tuple[str, ...] = (
    "translateX",
    "translateY",
    "translateZ",
    "rotateX",
    "rotateY",
    "rotateZ",
)

EXTERNAL_HIK_INDEX: Dict[str, int] = {
    "Hips": 1,
    "LeftUpLeg": 2,
    "LeftLeg": 3,
    "LeftFoot": 4,
    "RightUpLeg": 5,
    "RightLeg": 6,
    "RightFoot": 7,
    "Spine": 8,
    "LeftArm": 9,
    "LeftForeArm": 10,
    "LeftHand": 11,
    "RightArm": 12,
    "RightForeArm": 13,
    "RightHand": 14,
    "Head": 15,
}


def _running_under_maya() -> bool:
    try:
        import maya.cmds  # noqa: F401
    except ImportError:
        return False
    return True


# ===================================================================
# mayapy side: everything below runs inside a live mayapy process
# ===================================================================
def _parse_probe_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Probe HUMANIK-EXTERNAL-SOURCE-1 ES-2 under mayapy.")
    parser.add_argument("--pmx", default="tests/data/mmt_test_model.pmx")
    parser.add_argument("--vmd", default="tests/data/mmt_test_model_test_motion.vmd")
    parser.add_argument("--out", default="build/reports/humanik_external_source_probe.json")
    return parser.parse_args()


def _load_mmd_plugin(cmds) -> None:
    plugin_path = _PROJECT_ROOT / "mmd_tools" / "plugin_main.py"
    if not cmds.pluginInfo(plugin_path.stem, query=True, loaded=True):
        cmds.loadPlugin(str(plugin_path), quiet=True)


def _import_model(path: Path) -> str:
    from mmd_tools.io.mmd_importer import import_mmd_file

    root = import_mmd_file(
        str(path),
        options={
            "use_namespace": True,
            "setup_rig": False,
            "import_physics": False,
            "create_mmd_shaders": False,
            "use_native_pmx_parse": False,
            "require_native_pmx_parse": False,
        },
    )
    if not root:
        raise RuntimeError(f"MMD model import failed: {path}")
    return str(root)


def _import_motion(path: Path, target_model: str, pmx: Path) -> None:
    from mmd_tools.io.mmd_importer import import_mmd_file

    if not import_mmd_file(
        str(path),
        options={
            "target_model": target_model,
            "pmx_path": str(pmx),
            "bake_mode": True,
            "clear_existing_motion": True,
            "use_native_pmx_parse": False,
            "require_native_pmx_parse": False,
        },
    ):
        raise RuntimeError(f"VMD motion import failed: {path}")


def _find_assignment(result, hik_bone: str):
    for assignment in result.assignments:
        if assignment.hik_bone == hik_bone:
            return assignment
    raise RuntimeError(f"Required HIK assignment is missing: {hik_bone}")


_ANIM_CURVE_TYPES = ("animCurveTL", "animCurveTA", "animCurveTU", "animCurveTT")


def _clear_target_animation(cmds, joints: List[str]) -> int:
    """Remove any animCurves the VMD import baked onto ``joints``.

    ``bake_to_mmd_rig`` refuses to overwrite a channel that already has an
    unreviewed incoming writer (HUMANIK-RETARGET-S4's writer-conflict guard)
    -- a plain VMD-baked animCurve directly on a joint's transform channel is
    not a constraint node ``classify_humanik_constraints`` recognizes, so it
    is never muted the way a physics/IK writer would be during
    ``enter_target_mode``. This mirrors what a real retarget workflow would
    do anyway: discard the target's own prior VMD animation before driving it
    from a live external SOURCE. Returns the number of animCurve nodes
    removed, purely for the report.
    """
    curves = set()
    for joint in joints:
        for channel in CHANNELS:
            plug = f"{joint}.{channel}"
            for source in cmds.listConnections(plug, source=True, destination=False, plugs=True) or []:
                node = source.split(".")[0]
                try:
                    node_type = cmds.nodeType(node)
                except Exception:
                    continue
                if node_type in _ANIM_CURVE_TYPES:
                    curves.add(node)
    if curves:
        cmds.delete(list(curves))
    return len(curves)


def _build_external_skeleton(cmds) -> Dict[str, str]:
    """Build a minimal ~15-joint Maya-native skeleton, standing in for mocap.

    Deliberately not an MMD import: no ``mmd_bone_index``/``mmd_bone_name``
    attributes, so ``resolve_scene_humanik_assignments`` and
    ``find_humanik_character_for_model`` never resolve it to an MMD model --
    this is the scene fact ``list_scene_hik_characters`` must report as
    ``isMmd=False``.
    """
    joints: Dict[str, str] = {}

    def _make(name: str, parent: str, position: Tuple[float, float, float]) -> str:
        cmds.select(clear=True)
        if parent:
            cmds.select(parent)
        joint = cmds.joint(name=name, position=position)
        joints[name] = joint
        return joint

    hips = _make("ExtSrc_Hips", "", (0.0, 100.0, 0.0))
    spine = _make("ExtSrc_Spine", hips, (0.0, 120.0, 0.0))
    head = _make("ExtSrc_Head", spine, (0.0, 150.0, 0.0))
    left_arm = _make("ExtSrc_LeftArm", spine, (20.0, 130.0, 0.0))
    left_forearm = _make("ExtSrc_LeftForeArm", left_arm, (40.0, 130.0, 0.0))
    left_hand = _make("ExtSrc_LeftHand", left_forearm, (60.0, 130.0, 0.0))
    right_arm = _make("ExtSrc_RightArm", spine, (-20.0, 130.0, 0.0))
    right_forearm = _make("ExtSrc_RightForeArm", right_arm, (-40.0, 130.0, 0.0))
    right_hand = _make("ExtSrc_RightHand", right_forearm, (-60.0, 130.0, 0.0))
    left_upleg = _make("ExtSrc_LeftUpLeg", hips, (10.0, 90.0, 0.0))
    left_leg = _make("ExtSrc_LeftLeg", left_upleg, (10.0, 50.0, 0.0))
    left_foot = _make("ExtSrc_LeftFoot", left_leg, (10.0, 10.0, 0.0))
    right_upleg = _make("ExtSrc_RightUpLeg", hips, (-10.0, 90.0, 0.0))
    right_leg = _make("ExtSrc_RightLeg", right_upleg, (-10.0, 50.0, 0.0))
    right_foot = _make("ExtSrc_RightFoot", right_leg, (-10.0, 10.0, 0.0))

    return {
        "Hips": hips,
        "Spine": spine,
        "Head": head,
        "LeftArm": left_arm,
        "LeftForeArm": left_forearm,
        "LeftHand": left_hand,
        "RightArm": right_arm,
        "RightForeArm": right_forearm,
        "RightHand": right_hand,
        "LeftUpLeg": left_upleg,
        "LeftLeg": left_leg,
        "LeftFoot": left_foot,
        "RightUpLeg": right_upleg,
        "RightLeg": right_leg,
        "RightFoot": right_foot,
    }


def _key_external_animation(cmds, hips_joint: str, arm_joint: str) -> None:
    """Key a few frames of translate/rotate on the synthetic skeleton.

    Deliberately keys ``translateY`` (a bob), not ``translateX``: step 5's
    locomotion check drives ``translateX`` with a raw ``cmds.setAttr`` on the
    same joint (``verify_root_locomotion``), which only has an observable
    effect on an otherwise-unkeyed channel -- an animCurve-driven channel's
    value is recomputed from the curve on the very next evaluation and would
    silently discard the manual push.
    """
    for time, value in ((0, 0.0), (5, 2.0), (10, 4.0)):
        cmds.setKeyframe(hips_joint, attribute="translateY", time=time, value=value)
    for time, value in ((0, 0.0), (5, 30.0), (10, 0.0)):
        cmds.setKeyframe(arm_joint, attribute="rotateZ", time=time, value=value)


def _snapshot_local_values(cmds, joints: List[str]) -> Dict[str, Dict[str, List[float]]]:
    snapshot: Dict[str, Dict[str, List[float]]] = {}
    for joint in sorted(joints):
        translate = cmds.getAttr(f"{joint}.translate")[0]
        rotate = cmds.getAttr(f"{joint}.rotate")[0]
        snapshot[joint] = {
            "translate": [float(v) for v in translate],
            "rotate": [float(v) for v in rotate],
        }
    return snapshot


def _values_equal(
    before: Dict[str, Dict[str, List[float]]],
    after: Dict[str, Dict[str, List[float]]],
    tolerance: float = 1.0e-6,
) -> bool:
    if set(before) != set(after):
        return False
    for joint, channels in before.items():
        other = after[joint]
        for channel, values in channels.items():
            other_values = other.get(channel)
            if other_values is None or len(other_values) != len(values):
                return False
            for left, right in zip(values, other_values):
                if abs(float(left) - float(right)) > tolerance:
                    return False
    return True


def run_probe() -> int:
    args = _parse_probe_args()
    out = Path(args.out)
    if not out.is_absolute():
        out = _PROJECT_ROOT / out
    out.parent.mkdir(parents=True, exist_ok=True)
    payload: Dict[str, Any] = {"status": "fail", "stage": "start"}

    import maya.cmds as cmds
    import maya.mel as mel
    import maya.standalone

    maya.standalone.initialize(name="python")
    try:
        payload["mayaVersion"] = cmds.about(version=True)
        _load_mmd_plugin(cmds)

        pmx = Path(args.pmx)
        if not pmx.is_absolute():
            pmx = _PROJECT_ROOT / pmx
        vmd = Path(args.vmd)
        if not vmd.is_absolute():
            vmd = _PROJECT_ROOT / vmd
        pmx = pmx.resolve()
        vmd = vmd.resolve()
        if not pmx.is_file() or not vmd.is_file():
            raise FileNotFoundError(f"ES-2 fixtures not found: pmx={pmx} vmd={vmd}")

        from mmd_tools.core.humanik_builder import (
            create_humanik_definition,
            lock_humanik_definition,
        )
        from mmd_tools.core.humanik_frontend import HumanIkFrontendSession
        from mmd_tools.core.humanik_resolver import HumanIkBoneAssignment, HumanIkResolveResult
        from mmd_tools.core.humanik_retarget import list_scene_hik_characters, verify_root_locomotion

        # ---- Step 1: MMD fixture as TARGET ----
        # Characterize is done *before* the VMD import: ``setup_and_characterize``
        # runs an automatic canonical-T-pose/characterize/restore transaction
        # (HumanIkStanceTransaction) that snapshots and writes back every
        # mapped joint's rotate channel -- restoring a *driven* (animCurve
        # -incoming) rotate channel to sub-1e-12 precision is not guaranteed,
        # so every other HumanIK probe/smoke that needs both VMD animation
        # and this automatic stance path (as opposed to the lower-level
        # ``create_humanik_definition_from_scene``) characterizes the model
        # while it is still in its unanimated bind pose and imports VMD
        # afterward. Characterization itself does not care whether animation
        # already exists or is added later.
        payload["stage"] = "import_target"
        target_root = _import_model(pmx)
        payload["targetRoot"] = target_root

        session = HumanIkFrontendSession()
        payload["stage"] = "characterize_target"
        target_binding = session.setup_and_characterize(target_root)
        payload["targetCharacter"] = target_binding.character

        payload["stage"] = "import_target_motion"
        _import_motion(vmd, target_root, pmx)
        cleared_count = _clear_target_animation(
            cmds, [assignment.joint for assignment in target_binding.assignments]
        )
        payload["targetMotionAnimCurvesClearedBeforeRetarget"] = cleared_count

        # ---- Step 2: synthetic external skeleton, characterized standalone ----
        payload["stage"] = "build_external_skeleton"
        external_joints = _build_external_skeleton(cmds)
        payload["externalJoints"] = dict(external_joints)

        assignments = tuple(
            HumanIkBoneAssignment(
                joint=joint,
                mmd_bone=hik_bone,
                hik_bone=hik_bone,
                hik_index=EXTERNAL_HIK_INDEX[hik_bone],
                source="probe",
            )
            for hik_bone, joint in external_joints.items()
        )
        external_result = HumanIkResolveResult(
            assignments=assignments,
            missing_mmd_bones=(),
            unindexed_mmd_bones=(),
            duplicate_assignments=(),
        )
        payload["stage"] = "characterize_external"
        external_character = create_humanik_definition(
            external_result,
            name_hint="ExtMocap",
            create_control_rig=False,
            update_ui=False,
        )
        payload["externalCharacter"] = external_character
        external_locked = lock_humanik_definition(external_character)
        payload["externalLocked"] = bool(external_locked)

        # ---- Step 3: key a few frames on the external skeleton ----
        payload["stage"] = "key_external_animation"
        hips_joint = external_joints["Hips"]
        arm_joint = external_joints["LeftArm"]
        _key_external_animation(cmds, hips_joint, arm_joint)
        cmds.currentTime(0)
        pre_source_local_values = _snapshot_local_values(cmds, list(external_joints.values()))
        payload["preSourceLocalValues"] = pre_source_local_values

        scene_characters = {row["character"]: row for row in list_scene_hik_characters()}
        payload["sceneCharacters"] = scene_characters
        payload["externalIsMmd"] = bool(
            scene_characters.get(external_character, {}).get("isMmd")
        )

        # ---- Step 4: connect external source -> target ----
        payload["stage"] = "enter_external_source_mode"
        enter_result = session.enter_external_source_mode(external_character)
        payload["enterExternalSourceResult"] = enter_result

        payload["stage"] = "enter_target_mode"
        preview = session.enter_target_mode(target_root)
        payload["previewActive"] = bool(preview.active)

        input_type = int(mel.eval(f'hikGetInputType("{target_binding.character}")'))
        retarget_input = str(
            mel.eval(f'hikGetRetargetCharacterInput("{target_binding.character}")') or ""
        )
        payload["inputType"] = input_type
        payload["retargetCharacterInput"] = retarget_input
        payload["retargetConnected"] = bool(
            input_type == 3 and retarget_input == external_character
        )

        # ---- Step 5: locomotion ----
        # ``humanik_retarget_smoke.py`` (the S0 reference this reuses) always
        # pins evaluationManager to "off" before probing locomotion -- HIK's
        # own solve does not reliably re-run from a bare dgdirty/refresh under
        # every evaluation mode, and mayapy standalone's default mode is not
        # guaranteed to be "off".
        payload["stage"] = "locomotion"
        target_hips = _find_assignment(target_binding.result, "Hips").joint
        original_eval_mode = cmds.evaluationManager(query=True, mode=True) or ["off"]
        try:
            cmds.evaluationManager(mode="off")
            locomotion = verify_root_locomotion(
                hips_joint,
                [],
                translation=(1.0, 0.0, 0.0),
                observed_root_joint=target_hips,
            )
        finally:
            cmds.evaluationManager(mode=original_eval_mode[0])
        payload["locomotion"] = locomotion

        # ---- Step 6: bake ----
        payload["stage"] = "bake"
        bake_result = session.bake_to_mmd_rig(0, 10)
        payload["bakeKeyCount"] = int(bake_result.key_count)
        payload["bakeMaxError"] = float(bake_result.max_error)

        # ---- Step 7: restore, external skeleton must be untouched ----
        payload["stage"] = "restore"
        restored = session.restore_mmd_rig()
        payload["restoreMmdRigReturned"] = bool(restored)
        payload["externalSourceClearedAfterRestore"] = session._external_source_character is None
        cmds.currentTime(0)
        post_restore_local_values = _snapshot_local_values(cmds, list(external_joints.values()))
        payload["postRestoreLocalValues"] = post_restore_local_values
        payload["externalSkeletonUnaffected"] = _values_equal(
            pre_source_local_values, post_restore_local_values
        )

        stop_reasons: List[str] = []
        if not external_locked:
            stop_reasons.append("external_lock_failed")
        if payload["externalIsMmd"]:
            stop_reasons.append("external_character_misclassified_as_mmd")
        if not payload["retargetConnected"]:
            stop_reasons.append("retarget_not_connected")
        if not locomotion.get("passed"):
            stop_reasons.append("locomotion_failed")
        if payload["bakeKeyCount"] <= 0:
            stop_reasons.append("no_bake_keys")
        if not payload["restoreMmdRigReturned"]:
            stop_reasons.append("restore_reported_no_work")
        if not payload["externalSourceClearedAfterRestore"]:
            stop_reasons.append("external_source_selection_not_cleared")
        if not payload["externalSkeletonUnaffected"]:
            stop_reasons.append("external_skeleton_mutated")

        payload["stopReasons"] = stop_reasons
        payload["stage"] = "complete"
        payload["status"] = "pass" if not stop_reasons else "fail"
        out.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(
            "RESULT_JSON: "
            + json.dumps({"status": payload["status"], "stopReasons": stop_reasons})
        )
        return 0 if payload["status"] == "pass" else 1
    except Exception as exc:  # noqa: BLE001
        payload["error"] = str(exc)
        payload["traceback"] = traceback.format_exc()
        payload["status"] = "error"
        out.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print("RESULT_JSON: " + json.dumps({"status": "error", "error": str(exc)}))
        return 1
    finally:
        maya.standalone.uninitialize()


# ===================================================================
# Host side: plain python, spawns mayapy against this same script
# ===================================================================
def _host_main() -> int:
    import subprocess

    from tests.common.maya_location import (
        mayapy,
        pythonpath_for_maya_process,
        resolve_path_for_maya_process,
    )

    parser = argparse.ArgumentParser(description="HUMANIK-EXTERNAL-SOURCE-1 ES-2 probe")
    parser.add_argument("--maya", default="2024")
    parser.add_argument("--pmx", default="tests/data/mmt_test_model.pmx")
    parser.add_argument("--vmd", default="tests/data/mmt_test_model_test_motion.vmd")
    parser.add_argument("--out", default="build/reports/humanik_external_source_probe.json")
    args = parser.parse_args()

    mayapy_path = mayapy(args.maya)
    if not mayapy_path.exists():
        raise FileNotFoundError(f"mayapy not found: {mayapy_path}")

    root = _PROJECT_ROOT
    script = resolve_path_for_maya_process(mayapy_path, root, Path(__file__))
    pmx = resolve_path_for_maya_process(mayapy_path, root, args.pmx)
    vmd = resolve_path_for_maya_process(mayapy_path, root, args.vmd)
    out = resolve_path_for_maya_process(mayapy_path, root, args.out)

    env = dict(os.environ)
    env["PYTHONPATH"] = pythonpath_for_maya_process(
        mayapy_path, root, env.get("PYTHONPATH"), preserve_existing=True
    )
    env["MAYA_SKIP_USERSETUP_PY"] = "1"

    result = subprocess.run(
        [str(mayapy_path), script, "--pmx", pmx, "--vmd", vmd, "--out", out],
        env=env,
    )
    return result.returncode


def main() -> int:
    if _running_under_maya():
        return run_probe()
    return _host_main()


if __name__ == "__main__":
    raise SystemExit(main())
