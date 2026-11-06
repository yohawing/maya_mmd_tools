# Real-model skirt penetration reproduction

This replaces the synthetic two-sphere contact experiment. It exercises the
production PMX/VMD import, Maya rig, live physics, and evaluated skin mesh in an
isolated Maya 2024 GUI, through the existing commandPort/background launcher.
It does not operate a user's existing Maya session.

## Reproduce

```powershell
uvx nox -s skirt_penetration
# Regression check: fails if sampled intersections reappear.
uvx nox -s skirt_penetration -- --out-dir build/reports/skirt-regression --assert-clear
```

The default manifest is `tests/data/physics/skirt_leg_real.fixture.json`. It pins
the existing local `sour-miku-rabbithole-physics` pair: Sour Miku Black PMX and
Rabbit Hole VMD. Assets are not redistributed. If stored elsewhere, copy the
manifest outside the checkout, change only its paths, and pass `--config`.
Both asset hashes must match. No download or substitute model is attempted.
Each invocation requires an empty output directory. Use a new `--out-dir` for
reruns; existing evidence is never removed or mixed into a new run.

The repository's `mmt_test_model.pmx` has only static bodies and no joints;
`yw_test_model.pmx` has dynamic hair but no skirt physics. Their shared short
test motion spans frames 0–5. They cannot reproduce this skirt case unchanged.

## Fixed conditions and evidence

- Maya 2024, 30 fps, DG evaluation (`off`), import scale 1, rig mode, live physics.
- Fresh import and world; enable physics at frame 0; evaluate **every frame**
  from 0 to 150. Sampling starts at 110, plus the frame-0 control.
- No collider enlargement, mask edits, cloth replacement, or extra warm-up.
- Record scene world settings and the runtime tick configuration separately;
  fail if `fixedTimestep` or `maxSubsteps` differs between them.
- Native body readback is recorded separately from mesh vertices. The Bullet
  wrapper returns the completed solver's current COM pose, not the interpolated
  graphics MotionState (which previously lagged by one fixed substep).

`report.json` stores actual settings, asset/binary/driver hashes, weighted region
sizes, per-frame intersection coordinates and counts, solver status, native
body readback, and warnings/errors. Capture frames additionally retain the
kinematic input signature, runtime bone matrices, and Maya joint world matrices.
Body and joint descriptors are captured from the imported Maya scene.

`frame-*.png` shows the original material appearance. `diagnostic-*.png` colors
skirt faces blue and leg faces orange on a **frozen copy**; original materials
are unchanged. `frozen-*.ma` is that evaluated diagnostic mesh and camera, with
no live physics dependency. `frame-*.ma` and `reproduction.ma` retain the live
scene: reopening them is **not** guaranteed to restore solver history. Re-run
the command from a fresh import to reproduce the trajectory.

`mesh-*.npz` holds the evaluated world-space skirt/leg triangles for independent
inspection. All generated third-party model data stays in ignored `build/`.

## What the measurement means

Regions come from actual Maya skin weights, not assumed PMX vertex indices:
each triangle's vertices must have more than 50% total weight to skirt-named or
leg/knee-named influences respectively. Strict edge/triangle crossings are
tested in both directions. Coplanar overlap, tangency, edge/end-point contact,
and regions excluded by that weight threshold are not counted. Coordinates are
deduplicated at five decimal places. Counts are **not penetration depth**, and
zero is not proof of globally collision-free clothing.

On 2026-11-06, the first fresh GUI scan and a second fresh GUI detailed run both
measured 36 crossings at frame 120 and 222 at frame 140; the detailed run found
245 at frame 139. Frame 0 was clear. This establishes a real-model Maya mesh
intersection reproduction, not its root cause or MMD parity.

A subsequent isolated-process diagnostic bypassed
`MmdRuntimeInstance.evaluate_current_pose_before_physics` after import, leaving
the source files, binaries, assets and Maya leg inputs unchanged. The 13 affected
sampled frames became clear, including frame 139 (245 crossings to 0). Static
thigh collider orientation errors of about 30 degrees dropped to float-scale
error. This implicates pre-physics reevaluation of already evaluated Maya world
poses (including append-driven leg D bones). The bypass is **not included** in
this runner and is not a production fix: it disables all pre-physics evaluation,
so compatibility with other input modes still requires a targeted correction.
Local evidence is `build/reports/skirt-20261106/ab-summary.json`.

The production correction removes that duplicate pre-physics evaluation from
the live Maya solver, and converts evaluated input matrices back to base local
channels without applying append transforms twice in mmd-anim. The regression
also covers reset, forward stepping, same-frame pose edits, and append donors
on either side of recipients in model order. The diagnostic bypass above is
historical evidence; the runner uses the production implementation unchanged.

With world tick settings honored at 1/60 second and 10 maximum substeps,
the old MotionState readback left four crossings at frame 131. Switching
readback to the current solver pose removes the interpolation delay; this is
validated independently against free-fall integration as well as this fixture.
The final bundled runtime was checked in a fresh Maya 2024 process: all 42
samples (frame 0 and every frame from 110 through 150) had zero crossings.
Local evidence is `build/reports/physics-fixes/verification.json`; this does not
extend the clearance claim beyond the measured mesh regions and frames.

`report.status=pass` means the diagnostic execution completed. Its separate
`reproduction` field states whether intersections were observed. `--assert-clear`
turns observed intersections into a failing regression command. Missing assets,
hash mismatch, failed import/solver/readback, and non-finite meshes also fail.
The regression assertion covers only the recorded frames (0 and the requested
sample interval), not the entire motion. Use `--sample-start 0 --stride 1` to
measure every frame through `--end`.

Before changing the solver, distinguish collider coverage and PMX collision
masks from input-pose mismatch, native contact failure, and delayed writeback.
Do not claim Bullet itself is broken solely from a mesh intersection.
