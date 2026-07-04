"""Sample Bullet physics preview motion for a representative PMX model.

Run under mayapy. The harness imports a physics-heavy PMX fixture, samples
rigid-body and related-bone world transforms at a few frames, then writes a JSON
report that proves the Bullet preview graph is connected and moving without
pinning exact solver output as a golden value.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import traceback
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MODEL = ROOT / "tests" / "data" / "physics" / "test_hair_physics.pmx"
DEFAULT_OUT = ROOT / "build" / "reports" / "physics_preview_numeric.json"
DYNAMIC_MOTION_EPSILON = 1.0e-3
STATIC_TRANSLATE_EPSILON = 1.0e-2
REWIND_EPSILON = 1.0e-3


def _initialize_maya() -> bool:
    import maya.standalone

    try:
        maya.standalone.initialize(name="python")
        return True
    except RuntimeError:
        return False


def _repo_imports() -> None:
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))


def _emit(payload: dict[str, Any], out_path: Path) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text + "\n", encoding="utf-8")
    print(text)


def _distance(values_a: list[float], values_b: list[float]) -> float:
    return math.sqrt(sum((float(values_a[i]) - float(values_b[i])) ** 2 for i in range(len(values_a))))


def _long_path(cmds, node: str) -> str:
    paths = cmds.ls(node, long=True) or []
    return paths[0] if paths else node


def _descendant_transforms(cmds, root: str) -> list[str]:
    root_path = _long_path(cmds, root)
    descendants = cmds.listRelatives(root_path, allDescendents=True, type="transform", fullPath=True) or []
    return [root_path, *descendants]


def _safe_get_attr(cmds, attr_path: str, default=None):
    try:
        return cmds.getAttr(attr_path)
    except Exception:
        return default


def _rigid_body_refs(cmds, root: str) -> dict[int, dict[str, Any]]:
    refs: dict[int, dict[str, Any]] = {}
    for transform in _descendant_transforms(cmds, root):
        if not cmds.attributeQuery("mmd_rigid_body_index", node=transform, exists=True):
            continue
        shapes = cmds.listRelatives(transform, shapes=True, type="bulletRigidBodyShape", fullPath=True) or []
        if not shapes:
            continue
        index = int(cmds.getAttr(f"{transform}.mmd_rigid_body_index"))
        mode = int(_safe_get_attr(cmds, f"{transform}.mmd_physics_mode", -1))
        bone = int(_safe_get_attr(cmds, f"{transform}.mmd_related_bone_index", -1))
        refs[index] = {
            "node": transform,
            "shape": shapes[0],
            "name": _safe_get_attr(cmds, f"{transform}.mmd_rigid_body_name", transform.rsplit("|", 1)[-1]),
            "physicsMode": mode,
            "relatedBoneIndex": bone,
        }
    return refs


def _bone_refs(cmds, root: str) -> dict[int, str]:
    from mmd_tools.core.constants import ATTR_MMD_BONE_INDEX

    refs: dict[int, str] = {}
    for joint in cmds.listRelatives(_long_path(cmds, root), allDescendents=True, type="joint", fullPath=True) or []:
        if not cmds.attributeQuery(ATTR_MMD_BONE_INDEX, node=joint, exists=True):
            continue
        refs[int(cmds.getAttr(f"{joint}.{ATTR_MMD_BONE_INDEX}"))] = joint
    return refs


def _solver_summary(cmds) -> dict[str, Any]:
    shapes = cmds.ls(type="bulletSolverShape", long=True) or []
    if not shapes:
        return {"exists": False, "shape": "", "transform": ""}
    shape = shapes[0]
    parents = cmds.listRelatives(shape, parent=True, fullPath=True) or []
    return {
        "exists": True,
        "shape": shape,
        "transform": parents[0] if parents else "",
        "gravityY": _safe_get_attr(cmds, f"{shape}.gravityY"),
        "internalFixedFrameRate": _safe_get_attr(cmds, f"{shape}.internalFixedFrameRate"),
        "startTime": _safe_get_attr(cmds, f"{shape}.startTime"),
    }


def _has_pair_blend_drive(cmds, transform: str) -> bool:
    for attr in ("translateX", "translateY", "translateZ", "rotateX", "rotateY", "rotateZ"):
        if cmds.listConnections(f"{transform}.{attr}", source=True, destination=False, type="pairBlend") or []:
            return True
    return False


def _has_preview_orient_constraint(cmds, joint: str) -> bool:
    constraints = cmds.listConnections(joint, source=True, destination=False, type="orientConstraint") or []
    for constraint in constraints:
        try:
            if cmds.attributeQuery("mmd_physics_preview_constraint", node=constraint, exists=True) and cmds.getAttr(
                f"{constraint}.mmd_physics_preview_constraint"
            ):
                return True
        except Exception:
            continue
    return False


def _sample_node(cmds, node: str) -> dict[str, list[float]]:
    return {
        "worldTranslate": [float(value) for value in cmds.xform(node, query=True, worldSpace=True, translation=True)],
        "worldRotate": [float(value) for value in cmds.xform(node, query=True, worldSpace=True, rotation=True)],
    }


def _sample_frames(cmds, frames: list[int], probes: dict[str, str], *, dgdirty: bool) -> dict[str, dict[str, Any]]:
    samples: dict[str, dict[str, Any]] = {}
    for frame in frames:
        cmds.currentTime(frame, edit=True)
        if dgdirty:
            cmds.dgdirty(allPlugs=True)
            cmds.currentTime(frame, edit=True)
        samples[str(frame)] = {name: _sample_node(cmds, node) for name, node in probes.items()}
    return samples


def _motion_delta(samples: dict[str, dict[str, Any]], start_frame: int, end_frame: int, probe: str) -> dict[str, float]:
    start = samples[str(start_frame)][probe]
    end = samples[str(end_frame)][probe]
    return {
        "translate": _distance(start["worldTranslate"], end["worldTranslate"]),
        "rotate": _distance(start["worldRotate"], end["worldRotate"]),
    }


def _assertion(assertions: list[dict[str, Any]], name: str, passed: bool, details: dict[str, Any] | None = None) -> None:
    item = {"name": name, "pass": bool(passed)}
    if details is not None:
        item["details"] = details
    assertions.append(item)


def run(
    *,
    model: Path,
    out: Path,
    frames: list[int],
    min_dynamic_movers: int,
    min_tip_rotate_deg: float,
    max_displacement: float,
    dgdirty: bool = False,
) -> dict[str, Any]:
    _repo_imports()
    import maya.cmds as cmds

    from mmd_tools.converters import PhysicsConverter
    from mmd_tools.io.mmd_importer import import_mmd_file

    if not PhysicsConverter.is_bullet_available():
        payload = {
            "status": "skip",
            "reason": "Maya Bullet plugin is unavailable",
            "model": str(model),
            "bulletAvailable": False,
        }
        _emit(payload, out)
        return payload

    os.environ["MMD_TOOLS_SKIP_SHADER_OVERRIDE"] = "1"
    cmds.file(new=True, force=True)
    root = import_mmd_file(
        str(model),
        options={
            "import_physics": True,
            "create_physics_joints": True,
            "create_mmd_shaders": False,
            "use_namespace": False,
            "cpp_fast_load": False,
        },
    )
    if not root:
        raise RuntimeError(f"PMX import failed: {model}")
    root = _long_path(cmds, root)

    rigid_refs = _rigid_body_refs(cmds, root)
    bone_refs = _bone_refs(cmds, root)
    dynamic_refs = {index: ref for index, ref in rigid_refs.items() if ref["physicsMode"] != 0}
    static_refs = {index: ref for index, ref in rigid_refs.items() if ref["physicsMode"] == 0}

    probes: dict[str, str] = {}
    for index in (0, 4, 7, 8, 15):
        if index in rigid_refs:
            probes[f"rb_{index}"] = rigid_refs[index]["node"]
    for index in (10, 19):
        if index in bone_refs:
            probes[f"bone_{index}"] = bone_refs[index]

    start_frame = frames[0]
    compare_frame = frames[1]
    end_frame = frames[-1]
    cmds.playbackOptions(
        min=start_frame,
        max=end_frame,
        animationStartTime=start_frame,
        animationEndTime=end_frame,
    )
    samples = _sample_frames(cmds, frames, probes, dgdirty=dgdirty)

    per_dynamic_motion: dict[str, dict[str, float]] = {}
    dynamic_movers = 0
    dynamic_motion_max = 0.0
    dynamic_displacement_max = 0.0
    for index, ref in dynamic_refs.items():
        probe_name = f"dynamic_{index}"
        motion_samples = _sample_frames(cmds, [start_frame, compare_frame], {probe_name: ref["node"]}, dgdirty=dgdirty)
        delta = _motion_delta(motion_samples, start_frame, compare_frame, probe_name)
        per_dynamic_motion[str(index)] = delta
        magnitude = max(delta["translate"], delta["rotate"])
        dynamic_motion_max = max(dynamic_motion_max, magnitude)
        dynamic_displacement_max = max(dynamic_displacement_max, delta["translate"])
        if magnitude > DYNAMIC_MOTION_EPSILON:
            dynamic_movers += 1

    static_motion_max = 0.0
    for index in static_refs:
        probe_name = f"rb_{index}"
        if probe_name not in probes:
            continue
        delta = _motion_delta(samples, start_frame, compare_frame, probe_name)
        static_motion_max = max(static_motion_max, delta["translate"])

    tip_rotate_deltas = {}
    for probe_name in ("bone_10", "bone_19"):
        if probe_name not in probes:
            continue
        tip_rotate_deltas[probe_name] = {
            str(frame): _motion_delta(samples, start_frame, frame, probe_name)["rotate"]
            for frame in frames[1:]
        }
    tip_rotate_max = max(
        (value for frame_values in tip_rotate_deltas.values() for value in frame_values.values()),
        default=0.0,
    )

    cmds.currentTime(start_frame, edit=True)
    if dgdirty:
        cmds.dgdirty(allPlugs=True)
        cmds.currentTime(start_frame, edit=True)
    rewind_samples = {name: _sample_node(cmds, node) for name, node in probes.items()}
    rewind_error_max = 0.0
    for name, rewind in rewind_samples.items():
        original = samples[str(start_frame)][name]
        rewind_error_max = max(
            rewind_error_max,
            _distance(original["worldTranslate"], rewind["worldTranslate"]),
            _distance(original["worldRotate"], rewind["worldRotate"]),
        )

    pair_blend_count = sum(1 for ref in dynamic_refs.values() if _has_pair_blend_drive(cmds, ref["node"]))
    orient_constraint_count = sum(
        1
        for ref in dynamic_refs.values()
        if ref["relatedBoneIndex"] in bone_refs and _has_preview_orient_constraint(cmds, bone_refs[ref["relatedBoneIndex"]])
    )
    locator_count = len(cmds.ls(type="mmdRigidBodyLocator", long=True) or [])
    constraint_count = len(cmds.ls(type="bulletRigidBodyConstraintShape", long=True) or [])

    assertions: list[dict[str, Any]] = []
    _assertion(assertions, "rigid_body_count_is_16", len(rigid_refs) == 16, {"actual": len(rigid_refs)})
    _assertion(assertions, "dynamic_rigid_body_count_is_14", len(dynamic_refs) == 14, {"actual": len(dynamic_refs)})
    _assertion(assertions, "bullet_constraint_count_at_least_14", constraint_count >= 14, {"actual": constraint_count})
    _assertion(
        assertions,
        "dynamic_bodies_have_pair_blend",
        pair_blend_count >= len(dynamic_refs),
        {"actual": pair_blend_count, "expected": len(dynamic_refs)},
    )
    _assertion(
        assertions,
        "dynamic_bones_have_preview_orient_constraint",
        orient_constraint_count >= len(dynamic_refs),
        {"actual": orient_constraint_count, "expected": len(dynamic_refs)},
    )
    _assertion(
        assertions,
        "dynamic_bodies_moved_by_compare_frame",
        dynamic_movers >= min_dynamic_movers,
        {"actual": dynamic_movers, "minimum": min_dynamic_movers, "frame": compare_frame},
    )
    _assertion(
        assertions,
        "tip_bone_rotated",
        tip_rotate_max >= min_tip_rotate_deg,
        {"actual": tip_rotate_max, "minimum": min_tip_rotate_deg},
    )
    _assertion(
        assertions,
        "dynamic_displacement_not_exploded",
        dynamic_displacement_max < max_displacement,
        {"actual": dynamic_displacement_max, "maximum": max_displacement},
    )
    _assertion(
        assertions,
        "static_bodies_stay_near_initial_pose",
        static_motion_max < STATIC_TRANSLATE_EPSILON,
        {"actual": static_motion_max, "maximum": STATIC_TRANSLATE_EPSILON},
    )
    _assertion(
        assertions,
        "rewind_returns_to_initial_pose",
        rewind_error_max < REWIND_EPSILON,
        {"actual": rewind_error_max, "maximum": REWIND_EPSILON},
    )

    failed = [item for item in assertions if not item["pass"]]
    payload = {
        "status": "fail" if failed else "pass",
        "model": str(model),
        "bulletAvailable": True,
        "importSummary": {
            "root": root,
            "rigidBodyCount": len(rigid_refs),
            "dynamicRigidBodyCount": len(dynamic_refs),
            "staticRigidBodyCount": len(static_refs),
            "bulletConstraintCount": constraint_count,
            "locatorCount": locator_count,
            "pairBlendDrivenDynamicCount": pair_blend_count,
            "previewOrientConstraintCount": orient_constraint_count,
            "solver": _solver_summary(cmds),
        },
        "probeNodes": {
            name: {"node": node, "kind": "bone" if name.startswith("bone_") else "rigidBody"}
            for name, node in probes.items()
        },
        "frames": samples,
        "metrics": {
            "compareFrame": compare_frame,
            "dynamicMovers": dynamic_movers,
            "dynamicMotionMax": dynamic_motion_max,
            "dynamicDisplacementMax": dynamic_displacement_max,
            "staticMotionMax": static_motion_max,
            "tipBoneRotateDeltas": tip_rotate_deltas,
            "tipBoneRotateDeltaMax": tip_rotate_max,
            "rewindErrorMax": rewind_error_max,
            "perDynamicMotion": per_dynamic_motion,
        },
        "assertions": assertions,
    }
    if failed:
        payload["failures"] = failed
    _emit(payload, out)
    return payload


def _parse_frames(raw: str) -> list[int]:
    frames = [int(part.strip()) for part in raw.split(",") if part.strip()]
    if len(frames) < 2:
        raise argparse.ArgumentTypeError("--frames requires at least two frame numbers")
    if frames != sorted(set(frames)):
        raise argparse.ArgumentTypeError("--frames must be unique and ascending")
    return frames


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=str(DEFAULT_MODEL))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--frames", type=_parse_frames, default=_parse_frames("1,30,60"))
    parser.add_argument("--min-dynamic-movers", type=int, default=8)
    parser.add_argument("--min-tip-rotate-deg", type=float, default=0.5)
    parser.add_argument("--max-displacement", type=float, default=5.0)
    parser.add_argument("--dgdirty", action="store_true", help="Force DG dirtiness before each frame sample.")
    args = parser.parse_args()

    initialized = False
    try:
        initialized = _initialize_maya()
        payload = run(
            model=Path(args.model).resolve(),
            out=Path(args.out).resolve(),
            frames=args.frames,
            min_dynamic_movers=args.min_dynamic_movers,
            min_tip_rotate_deg=args.min_tip_rotate_deg,
            max_displacement=args.max_displacement,
            dgdirty=args.dgdirty,
        )
        return 0 if payload.get("status") in {"pass", "skip"} else 1
    except Exception:
        payload = {
            "status": "error",
            "exception": traceback.format_exc(),
            "model": str(Path(args.model).resolve()),
        }
        _emit(payload, Path(args.out).resolve())
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
