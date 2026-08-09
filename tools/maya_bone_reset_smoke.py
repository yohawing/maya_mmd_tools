"""Durable Maya standalone smoke for the Bone Tab reset/reconcile workflow.

The probe intentionally exercises the production authoring composition rather
than an injected fake: it creates the checked-in basic template, adds one
unregistered descendant joint, captures an animated current-frame rest pose,
and verifies strict metadata read-back and one-step Maya undo.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _close(values: Any, expected: tuple[float, float, float], tolerance: float = 1.0e-6) -> bool:
    """Return whether a Maya vector contains the expected finite values."""
    if not isinstance(values, (tuple, list)) or len(values) != 3:
        return False
    return all(abs(float(value) - target) <= tolerance for value, target in zip(values, expected))


def run(output: Path | None = None) -> dict[str, Any]:
    """Run the smoke in an initialized Maya standalone interpreter."""
    import maya.standalone

    maya.standalone.initialize(name="python")
    try:
        from maya import cmds

        from mmd_tools.adapters.maya_authoring_factory import build_maya_authoring_composition

        cmds.file(new=True, force=True)
        composition = build_maya_authoring_composition()
        created = composition.model_initializer.create(
            "pmx20-basic-v1",
            "Bone Reset Smoke",
            "Bone Reset Smoke",
        )
        root = created.root
        original = composition.metadata_adapter.read_spec(root)
        if len(original.bones) != 1 or original.bones[0].binding_identity is None:
            raise AssertionError("basic template did not create one bound root bone")
        root_joint = original.bones[0].binding_identity

        child = cmds.createNode("joint", name="boneResetSmokeChild", parent=root_joint)
        child = (cmds.ls(child, long=True) or [child])[0]
        cmds.currentTime(12, edit=True)
        # A real animCurve/keyframe warning must not disable reset.  Set the
        # child after keying the root so both captured world positions are
        # deterministic at the current frame.
        cmds.setKeyframe(root_joint, attribute="translateX", time=(12, 12), value=0.25)
        cmds.xform(child, worldSpace=True, translation=(1.0, 2.0, 3.0))

        plan = composition.coordinator.plan_bone_reset(root)
        if not plan.is_valid:
            raise AssertionError(f"reset preflight blocked unexpectedly: {plan.blockers}")
        if not any("animation" in warning.lower() for warning in plan.warnings):
            raise AssertionError(f"animation warning missing from plan: {plan.warnings}")
        if not any("zero direct children" in warning for warning in plan.warnings):
            raise AssertionError(f"new leaf derivation warning missing from plan: {plan.warnings}")
        if child not in plan.added_bindings:
            raise AssertionError(f"child was not discovered as an addition: {plan.added_bindings}")

        composition.coordinator.reset_bones(root, plan)
        observed = composition.metadata_adapter.read_spec(root)
        indices = [bone.index for bone in observed.bones]
        if indices != [0, 1]:
            raise AssertionError(f"bone indices are not contiguous: {indices}")
        child_bone = next(
            (bone for bone in observed.bones if bone.binding_identity == child),
            None,
        )
        if child_bone is None:
            raise AssertionError("added child metadata is missing after reset")
        if not _close(child_bone.rest_position, (1.0, 2.0, -3.0)):
            raise AssertionError(f"child rest position did not apply Z conversion: {child_bone.rest_position}")
        if child_bone.connect_bone_index is not None or not _close(child_bone.tail_offset, (0.0, -1.0, 0.0)):
            raise AssertionError(f"new leaf tail derivation mismatch: {child_bone}")
        root_bone = observed.bones[0]
        if root_bone.tail_offset != original.bones[0].tail_offset or root_bone.connect_bone_index != original.bones[0].connect_bone_index:
            raise AssertionError("existing root tail semantics changed during reset")
        if not _close(root_bone.rest_position, (0.25, 0.0, 0.0)):
            raise AssertionError(f"animated current-frame root rest mismatch: {root_bone.rest_position}")

        # The reset coordinator owns one undo chunk, so a single undo restores
        # the preflight fingerprint and leaves the newly-created child unbound.
        cmds.undo()
        restored = composition.metadata_adapter.read_spec(root)
        if restored.fingerprint() != original.fingerprint():
            raise AssertionError("undo did not restore the original metadata fingerprint")
        if any(bone.binding_identity == child for bone in restored.bones):
            raise AssertionError("undo left the added child registered")

        report = {
            "mayaVersion": cmds.about(version=True),
            "root": root,
            "child": child,
            "planValid": plan.is_valid,
            "warnings": list(plan.warnings),
            "addedBindings": list(plan.added_bindings),
            "indicesAfterReset": indices,
            "childRest": list(child_bone.rest_position),
            "rootRest": list(root_bone.rest_position),
            "undoRestoredFingerprint": True,
            "childUnregisteredAfterUndo": True,
        }
        if output is not None:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        return report
    finally:
        maya.standalone.uninitialize()


def main() -> int:
    """Parse a build-local report path and execute the smoke."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()
    run(args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
