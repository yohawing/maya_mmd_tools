# Leg/skirt contact diagnostic

Run from the checkout:

```powershell
uvx nox -s skirt_leg_fixture
```

Requires the bundled native physics runtime and Maya 2024's Python. No Maya
scene is opened or modified. The generator owns its outputs under
`build/skirt-leg-fixture/`; rerunning replaces them.

- `contact_on.pmx`: original, redistributable synthetic model; animated leg
  capsule, waist anchor, two dynamic sphere proxies connected by spring joints.
- `contact_off.pmx`: identical geometry, motion and constraints; collision masks
  alone are zeroed as a negative control.
- `leg_sweep.vmd`: 30 fps, frames 0–150. Rest 0–30, approach 30–60,
  hold 60–90, return 90–120, rest 120–150. Start sequential playback at 0.
- `report.json`: asset/runtime hashes, every rigid-body pose and signed
  sphere/capsule separation, worst frame, penetration depth and affected frames.

Measurements use MMD units, gravity `(0, -9.8, 0)`, 1/120-second native
substeps, fresh world creation and native reset before frame 0. The 0.05-unit
count is a descriptive threshold, not a model-wide acceptance threshold.
A failed runtime/setup/readback or undetected negative control fails the command.
A measured penetration does not fail it: this is a characterization experiment.

The colored panels show bone motion; they are not the collision surfaces.
This fixture isolates native capsule/sphere response and constrained following.
It does **not** reproduce a complete skirt, mesh penetration, dynamic mode 2,
compound body/joint rotations, Maya pose injection, playback reset, or MMD parity.
Do not mark the reported real-model bug fixed based on this result.

Next reproduction needs the affected PMX/VMD versions, hashes, frame interval,
FPS, scale, physics settings and exact playback/reset sequence. Preserve the
original assets locally. Compare mesh penetration and actual simulated collider
poses separately, then reduce only the affected leg/skirt region while retaining
weights, masks, joint frames and motion. Add that case beside this control.
