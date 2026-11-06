"""Generate a redistributable leg/skirt contact experiment and measure native contact.

This is a diagnostic fixture, not acceptance of real-model cloth or Maya playback.
Run through ``uvx nox -s skirt_leg_fixture``. Outputs are confined to build/ by default.
"""

# ruff: noqa: E402 -- standalone driver adds the checkout before product imports.

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mmd_tools.core.pmx_data import PmxData
from mmd_tools.core.pmx_data.bone import PmxBone
from mmd_tools.core.pmx_data.face import PmxFace
from mmd_tools.core.pmx_data.joint import PmxJoint
from mmd_tools.core.pmx_data.material import PmxMaterial
from mmd_tools.core.pmx_data.rigid_body import PmxRigidBody
from mmd_tools.core.pmx_data.vertex import PmxVertex
from mmd_tools.core.vmd_data import VmdData
from mmd_tools.core.vmd_data.bone_frame import VmdBoneFrame


def make_fixture(collisions=True):
    """One animated leg capsule and a two-link spherical skirt proxy, in MMD units."""
    pmx = PmxData()
    h = pmx.header
    h.version = 2.0
    h.model_name = h.model_name_english = "skirt_leg_contact"
    for name in ("vertex", "texture", "material", "bone", "morph", "rigid_body"):
        setattr(h, name + "_index_size", 2)
    # The leg starts clear of the hanging skirt, moves into it, holds, and returns.
    positions = [(0, 6, 0), (0, 3, -2), (0, 5, 0), (0, 3.5, 0)]
    for i, (name, pos) in enumerate(zip(("waist", "leg", "skirt_top", "skirt_hem"), positions)):
        bone = PmxBone()
        bone.name = bone.name_english = name
        bone.position = pos
        bone.parent_bone_index = -1 if i == 0 else (2 if i == 3 else 0)
        bone.bone_flag = 0x1E
        bone.connect_position_offset = (0, -0.5, 0)
        pmx.bones.append(bone)
        body = PmxRigidBody(2, 0)
        body.name = body.name_english = name
        body.related_bone_index = i
        body.position = pos
        body.shape_type = 2 if i == 1 else 0
        body.size = (0.65, 3, 0) if i == 1 else ((0.1, 0, 0) if i == 0 else (0.3, 0, 0))
        body.physics_mode = 0 if i < 2 else 1
        body.mass = 0 if i < 2 else 0.2
        body.velocity_attenuation = body.rotation_attenuation = 0.2
        body.friction = 0.5
        body.group = 0 if i == 1 else 1
        # PMX mask bits are allowed collision groups. Only leg/skirt pairs collide.
        body.collision_mask = (2 if i == 1 else (1 if i >= 2 else 0)) if collisions else 0
        pmx.rigid_bodies.append(body)
    for a, b, y in ((0, 2, 6), (2, 3, 4.25)):
        joint = PmxJoint(2, h.encoding)
        joint.name = joint.name_english = "hinge_" + str(b)
        joint.rigid_body_a_index, joint.rigid_body_b_index = a, b
        joint.position = (0, y, 0)
        joint.rotation_limit_min = (-1.4, -0.2, -0.2)
        joint.rotation_limit_max = (1.4, 0.2, 0.2)
        pmx.joints.append(joint)
    # Colored, double-sided panels make the two skirt segments visible in a host.
    # They deliberately are NOT claimed to be a capsule/sphere surface oracle.
    for bone_index, half_width, top, bottom, z, color in (
        (1, 0.65, 4.5, 1.5, -2, (0.9, 0.25, 0.15, 1)),
        (2, 0.5, 6, 4.25, 0, (0.2, 0.5, 0.9, 1)),
        (3, 0.5, 4.25, 2.75, 0, (0.2, 0.8, 0.6, 1)),
    ):
        start = len(pmx.vertices)
        for p in ((-half_width, top, z), (half_width, top, z),
                  (half_width, bottom, z), (-half_width, bottom, z)):
            vertex = PmxVertex()
            vertex.position, vertex.normal = p, (0, 0, -1)
            vertex.bone_indices = [bone_index]
            pmx.vertices.append(vertex)
        for tri in ((0, 1, 2), (0, 2, 3)):
            face = PmxFace()
            face.indices = tuple(start + v for v in tri)
            pmx.faces.append(face)
        material = PmxMaterial(2, h.encoding)
        material.name = material.name_english = pmx.bones[bone_index].name
        material.diffuse, material.face_count = color, 6
        pmx.materials.append(material)
    motion = VmdData()
    motion.header.model_name = h.model_name
    # Explicit per-frame keys remove interpolation ambiguity at the sampled frames.
    for frame in range(151):
        z = 0 if frame <= 30 else (min(frame - 30, 30) / 30 * 2)
        if frame > 90:
            z = max(0, 2 - (frame - 90) / 30 * 2)
        key = VmdBoneFrame()
        key.bone_name, key.frame_number = "leg", frame
        key.position, key.rotation = (0, 0, z), (0, 0, 0, 1)
        motion.bone_frames.append(key)
    return pmx, motion


def sphere_capsule_gap(sphere, capsule, radius=0.3, capsule_radius=0.65, height=3.0):
    """Exact signed separation of a sphere and a Y-axis capsule in world space."""
    p, _ = sphere
    center, (x, y, z, w) = capsule
    axis = (2 * (x * y - z * w), 1 - 2 * (x * x + z * z), 2 * (y * z + x * w))
    delta = [p[i] - center[i] for i in range(3)]
    t = max(-height / 2, min(height / 2, sum(delta[i] * axis[i] for i in range(3))))
    return math.sqrt(sum((delta[i] - t * axis[i]) ** 2 for i in range(3))) - radius - capsule_radius


def measure(pmx_path, vmd_path):
    """Sample fresh native simulation; no cache, Maya, or MMD oracle is implied."""
    from mmd_tools.core.mmd_parser import parse_pmx_file
    from mmd_tools.core.native.mmd_anim_runtime_handles import (
        MmdRuntimeClip, MmdRuntimeInstance, MmdRuntimeModel, MmdRuntimePhysicsWorld,
    )
    from mmd_tools.core.native.mmd_anim_runtime_types import MMD_RUNTIME_PHYSICS_MODE_LIVE
    from mmd_tools.core.physics_descriptor import build_descriptors_from_pmx

    pmx = parse_pmx_file(str(pmx_path))
    motion = VmdData().parse_file(str(vmd_path))
    desc = build_descriptors_from_pmx(pmx.rigid_bodies, pmx.joints, pmx.bones)
    if not desc.is_valid:
        raise RuntimeError(str(desc.validation_errors))
    handles = []
    try:
        def keep(value):
            if value is None:
                raise RuntimeError("Native physics handle creation failed")
            handles.append(value)
            return value
        model = keep(MmdRuntimeModel.from_pmx_bytes(pmx_path.read_bytes()))
        instance = keep(MmdRuntimeInstance.for_model(model))
        clip = keep(MmdRuntimeClip.from_vmd_bytes_for_model(model, vmd_path.read_bytes()))
        world = keep(MmdRuntimePhysicsWorld.from_descriptors(desc.rigid_bodies, desc.joints))
        if not (instance.set_physics_mode(MMD_RUNTIME_PHYSICS_MODE_LIVE)
                and instance.set_physics_tick_config(1 / 120, 8)
                and instance.evaluate_clip_frame_before_physics(clip, 0)
                and world.set_gravity((0, -9.8, 0))):
            raise RuntimeError("Native physics setup failed")
        if world.reset(instance) is None:
            raise RuntimeError("Native reset failed")
        samples = []
        for frame in range(151):
            if not instance.evaluate_clip_frame_before_physics(clip, frame):
                raise RuntimeError("Motion evaluation failed")
            report = world.step_runtime(instance, 1 / 30) if frame else None
            if frame and report is None:
                raise RuntimeError("Physics step failed")
            states = world.copy_rigidbody_states()
            if states is None or len(states) != 4:
                raise RuntimeError("Rigid-body readback failed")
            expected_leg = tuple(pmx.bones[1].position[i] +
                                 motion.bone_frames[frame].position[i]
                                 for i in range(3))
            if max(abs(states[1][0][i] - expected_leg[i]) for i in range(3)) > 1e-5:
                raise RuntimeError("Animated leg did not follow the fixture motion")
            gaps = [sphere_capsule_gap(states[i], states[1]) for i in (2, 3)]
            if not all(math.isfinite(v) for state in states for part in state for v in part):
                raise RuntimeError("Non-finite rigid-body state")
            samples.append({"frame": frame, "gaps": gaps, "states": states,
                            "substeps": int(report.tick.substeps) if report else 0})
        worst = min(samples, key=lambda s: min(s["gaps"]))
        return {"max_penetration": max(0, -min(worst["gaps"])), "worst_frame": worst["frame"],
                "frames_over_0_05": sum(min(s["gaps"]) < -0.05 for s in samples), "samples": samples}
    finally:
        for handle in reversed(handles):
            handle.free()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=ROOT / "build/skirt-leg-fixture")
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    results = {}
    for enabled in (True, False):
        name = "contact_on" if enabled else "contact_off"
        pmx, motion = make_fixture(enabled)
        pmx_path, vmd_path = args.out_dir / (name + ".pmx"), args.out_dir / "leg_sweep.vmd"
        pmx.write_file(str(pmx_path))
        motion.write_file(str(vmd_path))
        results[name] = measure(pmx_path, vmd_path)
    from mmd_tools.core.native.mmd_anim_runtime import get_runtime_library_path
    runtime = Path(get_runtime_library_path())
    report = {"status": "measured_not_real_model_acceptance", "fps": 30, "gravity": [0, -9.8, 0],
              "fixed_substep_seconds": 1 / 120, "runtime": str(runtime),
              "runtime_sha256": hashlib.sha256(runtime.read_bytes()).hexdigest(),
              "assets": {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                         for p in (args.out_dir / "contact_on.pmx", args.out_dir / "contact_off.pmx",
                                   args.out_dir / "leg_sweep.vmd")},
              "results": results,
              "limitations": ["Synthetic sphere skirt proxies, not a real skirt mesh",
                              "Native descriptor route only; Maya and MMD playback not verified",
                              "No full skirt, mode 2, rotation sweep, or reset replay acceptance"]}
    (args.out_dir / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({name: {k: v for k, v in result.items() if k != "samples"}
                      for name, result in results.items()}, indent=2))
    if results["contact_off"]["max_penetration"] < 0.5:
        raise RuntimeError("Negative control did not reproduce penetration")


if __name__ == "__main__":
    main()
