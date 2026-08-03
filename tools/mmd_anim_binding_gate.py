"""Run the explicit mmd-anim Python binding export-integration gate.

The gate feeds a PMX fixture and an optional VMD fixture into the experimental
binding, evaluates one frame, and writes a compact machine-readable evidence
artifact. CLI file validation remains a separate release gate.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Dict, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mmd_tools.validation.mmd_anim_binding_verifier import verify_mmd_anim_binding_asset  # noqa: E402


DEFAULT_MODEL = ROOT / "tests" / "data" / "mmt_test_model.pmx"
DEFAULT_MOTION = ROOT / "tests" / "data" / "mmt_test_model_test_motion.vmd"
DEFAULT_BINDING_ROOT = ROOT / "external" / "mmd-anim" / "bindings" / "python"
DEFAULT_OUTPUT = ROOT / "build" / "reports" / "export_validation" / "mmd_anim_binding_gate.json"


def _repo_path(value: str) -> Path:
    """Resolve an absolute or repository-relative path."""
    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (ROOT / path).resolve()


def _write_report(path: Path, payload: Dict[str, Any]) -> None:
    """Write deterministic JSON evidence and create its parent directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def run_gate(
    *,
    model: Path = DEFAULT_MODEL,
    motion: Optional[Path] = DEFAULT_MOTION,
    binding_root: Path = DEFAULT_BINDING_ROOT,
    runtime_library: Optional[Path] = None,
    frame: float = 0.0,
    output: Path = DEFAULT_OUTPUT,
) -> int:
    """Run the binding verifier and persist its result."""
    report = verify_mmd_anim_binding_asset(
        str(model),
        motion_path=str(motion) if motion is not None else None,
        binding_root=str(binding_root),
        runtime_library=str(runtime_library) if runtime_library is not None else None,
        frame=frame,
    )
    payload = {
        "schema_version": 1,
        "status": "pass" if report.valid else "fail",
        "model": str(model),
        "motion": str(motion) if motion is not None else None,
        "binding_root": str(binding_root),
        "runtime_library": str(runtime_library) if runtime_library is not None else None,
        "frame": frame,
        "report": report.to_dict(),
    }
    _write_report(output, payload)
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0 if report.valid else 1


def main(argv=None) -> int:
    """Parse command-line options and run the gate."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=str(DEFAULT_MODEL))
    parser.add_argument("--motion", default=str(DEFAULT_MOTION))
    parser.add_argument("--binding-root", default=str(DEFAULT_BINDING_ROOT))
    parser.add_argument("--runtime-library", default=None)
    parser.add_argument("--frame", type=float, default=0.0)
    parser.add_argument("--out", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args(argv)
    return run_gate(
        model=_repo_path(args.model),
        motion=_repo_path(args.motion) if args.motion else None,
        binding_root=_repo_path(args.binding_root),
        runtime_library=_repo_path(args.runtime_library) if args.runtime_library else None,
        frame=args.frame,
        output=_repo_path(args.out),
    )


if __name__ == "__main__":
    raise SystemExit(main())
