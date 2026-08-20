# User-path roundtrip smoke report (Maya 2024)

Date: 2026-08-19

## Scope

The gate exercises the production Action, Presenter/authoring, and workflow
boundaries rather than the legacy parser/writer shortcut:

`Import -> deterministic edit -> Prepare/Validate -> Export -> Parse -> fresh Import -> semantic oracle`

Mode C uses normal ascending Maya Timeline evaluation. The alternate
`MDGContext` implementation is not a correctness oracle or fallback. A
prepared, immutable, verified VMD artifact is published atomically so the
expensive Timeline bake is visible as Prepare work and the final Export stays
bounded.

## Fixed matrix result

| Case | Result | Export | Evidence |
| --- | --- | ---: | --- |
| YYB Hatsune Miku 10th model | BLOCKED | 20.84 s | Model edit, validation, PMX write, and parse passed. Fresh import is partial because relative texture sidecars were not staged beside the output PMX. The original `KeyError: 415` did not recur. |
| rabbit model | BLOCKED | 34.69 s | Model edit, validation, PMX write, and parse passed. Fresh import has the same missing-relative-texture packaging blocker. |
| dense motion + rabbit | PASS | cold 1.31 s; warm 1.29/1.32/1.43 s | 339,593 bone frames and one IK section preserved; fresh import semantic oracle passed. |
| sparse Aikotoba IV + YYB | PASS | 3.31 s | 2,979,054 prepared/exported bone frames, 678,600 morph frames, and one IK section preserved. Bone `+1 degree` and morph `+0.05` edits survived fresh import. |
| sparse facial + Mualani | PASS | 0.69 s | 597 bone frames and 37,611 morph frames preserved; edited morph and fresh import semantic oracle passed. |

All measured Export phases meet the 60 second target. The large sparse YYB
case spent 400.52 seconds in Prepare and 742.34 seconds in the smoke-only
fresh-import oracle; those costs are deliberately not reported as final Export
latency. Its peak smoke-process RSS was 36.97 GiB.

## Product bugs closed during the smoke

- Current Model scoped Mode C preparation is mandatory and fail-closed.
- Mode C sampling uses the Maya Timeline only; unavailable native sampling no
  longer falls back to alternate-context evaluation.
- Prepared VMD data is validated, encoded, verified, and staged once, then
  atomically published with SHA-256 and size checks.
- Dynamic physics bone animation is routed to owned pre-physics inputs.
- Bone morph, append/IK, and physics redirected XYZ inputs use one persistent
  Transform authoring proxy, preserving quaternion interpolation and avoiding
  compound-parent/child double inputs.
- Prepared-to-written VMD values are compared strictly.
- The Mode C world-pose oracle uses the existing `1e-2` CCD reconstruction
  contract; edit sentinels remain independently strict.

## Remaining blocker

PMX stores portable relative texture paths and intentionally does not serialize
machine-local absolute paths. Exporting a PMX into an arbitrary directory does
not currently copy its referenced texture tree, so clean fresh import cannot be
claimed for the two real model cases. This must be resolved as an explicit,
safe texture-packaging feature (collision, overwrite, symlink escape, and
rollback policy included), or the user must stage the same relative sidecars.
The smoke must not suppress the partial import or manufacture a pass.

## Local artifacts

- `build/reports/local_asset_roundtrip/sparse-yyb-accepted-20260819/sparse-yyb-accepted/`
- `build/reports/local_asset_roundtrip/dense-rabbit-20260819/dense-rabbit/`
- `build/reports/local_asset_roundtrip/sparse-facial-20260819/sparse-facial/`
- `build/reports/local_asset_roundtrip/model-yyb-accepted-20260819/model-yyb-accepted/`
- `build/reports/local_asset_roundtrip/model-rabbit-20260819/model-rabbit/`

These real-asset outputs remain ignored and are not copied into Git.
