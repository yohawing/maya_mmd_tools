"""Compare the local Tda V4X structural reference with the repo fixture.

The source PMX remains user-owned and is never copied or modified.  This
probe records only selected bone metadata needed to audit the normalized
product template contract.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mmd_tools.core.mmd_parser import parse_pmx_file  # noqa: E402


DEFAULT_FIXTURE = ROOT / "tests" / "unit" / "fixtures" / "tda_v4x_semistandard_structure_v1.json"


def _parent_name(bones: list[Any], parent_index: int) -> str | None:
    """Return the parent name for a PMX bone index."""
    if parent_index < 0:
        return None
    return bones[parent_index].name


def run_probe(reference: Path, fixture_path: Path) -> dict[str, Any]:
    """Validate selected Tda bone records against the checked-in fixture."""
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    pmx = parse_pmx_file(str(reference), use_native_pmx_parse=False)
    expected_count = fixture["source_asset"]["expected_bone_count"]
    if len(pmx.bones) != expected_count:
        raise RuntimeError(f"expected {expected_count} source bones, got {len(pmx.bones)}")

    verified = []
    for role in fixture["selected_roles"]:
        index = role["source_index"]
        bone = pmx.bones[index]
        actual_parent = _parent_name(pmx.bones, bone.parent_bone_index)
        if bone.name != role["name"]:
            raise RuntimeError(f"source bone {index} name mismatch: {bone.name!r} != {role['name']!r}")
        if actual_parent != role["parent"]:
            raise RuntimeError(
                f"source bone {bone.name!r} parent mismatch: {actual_parent!r} != {role['parent']!r}"
            )
        if int(bone.bone_flag) != role["source_flags"]:
            raise RuntimeError(
                f"source bone {bone.name!r} flags mismatch: {int(bone.bone_flag)} != {role['source_flags']}"
            )
        if any(
            abs(float(actual) - float(expected)) > 0.0015
            for actual, expected in zip(bone.position, role["source_position"])
        ):
            raise RuntimeError(
                f"source bone {bone.name!r} position mismatch: {bone.position!r} != {role['source_position']!r}"
            )
        verified.append({"index": index, "name": bone.name, "parent": actual_parent})

    return {
        "status": "pass",
        "reference": str(reference),
        "fixture": str(fixture_path),
        "source_bone_count": len(pmx.bones),
        "verified_roles": verified,
    }


def main() -> int:
    """Run the structural reference probe."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, required=True, help="local Tda V4X PMX path")
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--out", type=Path, help="optional UTF-8 JSON report path")
    args = parser.parse_args()

    report = run_probe(args.reference.resolve(), args.fixture.resolve())
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
