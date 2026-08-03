#!/usr/bin/env python
"""Run the Maya-side v0.7 export release probes.

The probe deliberately starts a fresh Maya scene for each import boundary.  It
exports a small PMX fixture, verifies PMD import plus the explicit public
export policy rejection, and exports a VMD motion.  The JSON output is
consumed by :mod:`tools.export_release_gate`.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import traceback
from typing import Any, Iterable, Mapping


ROOT = Path(__file__).resolve().parents[1]
BUILD_ROOT = (ROOT / "build").resolve()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.common.maya_plugin_setup import load_mmd_tools_plugin  # noqa: E402


DEFAULT_PMX = ROOT / "tests" / "data" / "for_unit_test" / "test_1bone_cube.pmx"
DEFAULT_VMD = ROOT / "tests" / "data" / "for_unit_test" / "test_1bone_cube_motion.vmd"
ORACLE_FRAMES = (0, 9, 19, 29, 39, 49)
FLOAT_TOLERANCE = 1.0e-4


def _require_build_path(value: str | Path, label: str) -> Path:
    """Resolve an output path and reject paths outside ``build/``."""
    path = Path(value)
    if not path.is_absolute():
        path = ROOT / path
    path = path.resolve()
    if path != BUILD_ROOT and BUILD_ROOT not in path.parents:
        raise ValueError(f"{label} must resolve under {BUILD_ROOT}: {path}")
    return path


def _json_default(value: Any) -> Any:
    """Convert small Maya/Python scalar values into JSON-safe values."""
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return list(value)
    return str(value)


def _round_values(values: Iterable[float], digits: int = 7) -> list[float]:
    """Round numeric oracle values to make the digest deterministic."""
    return [round(float(value), digits) for value in values]


def _digest_json(value: Any) -> str:
    """Return a stable digest for a JSON-safe oracle fragment."""
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _write_independent_pmd_fixture(path: Path) -> None:
    """Write the repository's independent supported PMD fixture for this run."""
    from tests.common.pmd_mock import PmdMock

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(PmdMock.create_minimal_pmd())


def _attribute_value(node: str, name: str) -> Any:
    """Read one optional Maya attribute without turning metadata into a blocker."""
    from maya import cmds

    if not cmds.attributeQuery(name, node=node, exists=True):
        return None
    try:
        return cmds.getAttr(f"{node}.{name}")
    except Exception:
        return None


def _find_mesh_transforms(root: str) -> list[str]:
    """Return stable mesh transforms below a model root."""
    from maya import cmds

    shapes = cmds.listRelatives(root, allDescendents=True, type="mesh", fullPath=True) or []
    transforms = []
    for shape in sorted(shapes):
        parents = cmds.listRelatives(shape, parent=True, fullPath=True) or []
        if parents:
            transform = str(parents[0])
            if transform not in transforms:
                transforms.append(transform)
    return transforms


def _capture_scene_oracle(root: str, frames: Iterable[int]) -> dict[str, Any]:
    """Capture mesh, pose, and model metadata from the current Maya scene."""
    from maya import cmds

    meshes = _find_mesh_transforms(root)
    mesh_oracle = []
    for transform in meshes:
        shapes = cmds.listRelatives(transform, shapes=True, fullPath=True, type="mesh") or []
        shape = str(shapes[0]) if shapes else transform
        raw_vertices = cmds.xform(f"{shape}.vtx[*]", query=True, worldSpace=True, translation=True) or []
        try:
            face_count = int(cmds.polyEvaluate(transform, face=True))
        except Exception:
            face_count = 0
        mesh_oracle.append(
            {
                "transform": transform,
                "vertex_count": len(raw_vertices) // 3,
                "face_count": face_count,
                "vertices": _round_values(raw_vertices),
                "vertex_digest": _digest_json(_round_values(raw_vertices)),
            }
        )

    joints = cmds.listRelatives(root, allDescendents=True, type="joint", fullPath=True) or []
    indexed_joints = []
    for joint in joints:
        index = _attribute_value(joint, "mmd_bone_index")
        if index is None:
            continue
        matrix = cmds.xform(joint, query=True, worldSpace=True, matrix=True) or []
        indexed_joints.append(
            {
                "index": int(index),
                "name": _attribute_value(joint, "mmd_bone_name") or str(joint),
                "translation": _round_values(
                    cmds.xform(joint, query=True, worldSpace=True, translation=True) or []
                ),
                "matrix": _round_values(matrix),
            }
        )
    indexed_joints.sort(key=lambda item: (item["index"], item["name"]))

    pose_by_frame = {}
    for frame in frames:
        cmds.currentTime(frame, edit=True)
        cmds.refresh(force=True)
        pose_by_frame[str(int(frame))] = [
            {
                "index": item["index"],
                "name": item["name"],
                "translation": _round_values(
                    cmds.xform(
                        next(
                            joint
                            for joint in joints
                            if (_attribute_value(joint, "mmd_bone_index") == item["index"])
                        ),
                        query=True,
                        worldSpace=True,
                        translation=True,
                    )
                    or []
                ),
            }
            for item in indexed_joints
        ]

    metadata = {
        name: _attribute_value(root, name)
        for name in (
            "mmd_file_type",
            "mmd_file_version",
            "mmd_model_name",
            "mmd_model_name_en",
            "mmd_comment",
            "mmd_comment_en",
            "mmd_display_frames_json",
        )
    }
    return {
        "mesh": mesh_oracle,
        "pose": {"joint_count": len(indexed_joints), "joints": indexed_joints, "frames": pose_by_frame},
        "metadata": metadata,
    }


def _compare_float_lists(expected: list[float], actual: list[float]) -> float:
    """Return the largest absolute difference between two flat vectors."""
    if len(expected) != len(actual):
        return float("inf")
    return max((abs(float(a) - float(b)) for a, b in zip(expected, actual)), default=0.0)


def _compare_scene_oracles(
    expected: Mapping[str, Any],
    actual: Mapping[str, Any],
    *,
    pose: bool,
    mesh: bool = True,
) -> list[str]:
    """Compare required mesh/pose/metadata oracle fields and return failures."""
    failures: list[str] = []
    if mesh:
        expected_mesh = list(expected.get("mesh", []))
        actual_mesh = list(actual.get("mesh", []))
        if len(expected_mesh) != len(actual_mesh):
            failures.append(f"mesh count differs: expected {len(expected_mesh)}, actual {len(actual_mesh)}")
        for index, (source, result) in enumerate(zip(expected_mesh, actual_mesh)):
            for field in ("vertex_count", "face_count"):
                if source.get(field) != result.get(field):
                    failures.append(
                        f"mesh[{index}].{field}: expected {source.get(field)}, actual {result.get(field)}"
                    )
            difference = _compare_float_lists(source.get("vertices", []), result.get("vertices", []))
            if difference > FLOAT_TOLERANCE:
                failures.append(f"mesh[{index}].vertices max error {difference:g}")

    expected_metadata = expected.get("metadata", {})
    actual_metadata = actual.get("metadata", {})
    for name in ("mmd_file_type", "mmd_model_name"):
        if expected_metadata.get(name) != actual_metadata.get(name):
            failures.append(
                f"metadata.{name}: expected {expected_metadata.get(name)!r}, actual {actual_metadata.get(name)!r}"
            )

    if pose:
        expected_pose = expected.get("pose", {})
        actual_pose = actual.get("pose", {})
        if expected_pose.get("joint_count") != actual_pose.get("joint_count"):
            failures.append(
                f"pose.joint_count: expected {expected_pose.get('joint_count')}, actual {actual_pose.get('joint_count')}"
            )
        expected_frames = expected_pose.get("frames", {})
        actual_frames = actual_pose.get("frames", {})
        for frame, expected_joints in expected_frames.items():
            actual_joints = actual_frames.get(frame)
            if actual_joints is None:
                failures.append(f"pose frame {frame} is missing")
                continue
            if len(expected_joints) != len(actual_joints):
                failures.append(f"pose frame {frame} joint count differs")
                continue
            for expected_joint, actual_joint in zip(expected_joints, actual_joints):
                if expected_joint["name"] != actual_joint["name"]:
                    failures.append(
                        f"pose frame {frame} bone name differs: {expected_joint['name']!r} vs {actual_joint['name']!r}"
                    )
                difference = _compare_float_lists(
                    expected_joint.get("translation", []), actual_joint.get("translation", [])
                )
                if difference > FLOAT_TOLERANCE:
                    failures.append(f"pose frame {frame} bone {expected_joint['name']} max error {difference:g}")
    return failures


def _import_options() -> dict[str, Any]:
    """Return deterministic, shader-light Maya import options for the probe."""
    return {
        "scale": 1.0,
        "setup_rig": False,
        "setup_bone_orientation": False,
        "create_mmd_control_rig": False,
        "create_mmd_shaders": False,
        "use_cpp_fast_load": False,
    }


def _fresh_import(path: Path, *, target_model: str | None = None, pmx_path: Path | None = None) -> str:
    """Create a new scene and import one model or VMD fixture."""
    from maya import cmds
    from mmd_tools.io.mmd_importer import import_mmd_file

    cmds.file(new=True, force=True)
    options = _import_options()
    if target_model is not None:
        options["target_model"] = target_model
    if pmx_path is not None:
        options["pmx_path"] = str(pmx_path)
    root = import_mmd_file(str(path), options=options)
    if not root:
        raise RuntimeError(f"Maya import returned no root: {path}")
    return str(root)


def _run_model_case(
    export_format: str,
    source_model: Path,
    out_dir: Path,
) -> dict[str, Any]:
    """Export one model format, fresh-import it, and compare scene oracles."""
    from mmd_tools.core.pmx_data import PmxData
    from mmd_tools.services.export_workflow_service import (
        ExportWorkflowRequest,
        ExportWorkflowService,
    )

    output = out_dir / f"model.{export_format}"
    report_dir = out_dir / "report"
    source_root = _fresh_import(source_model)
    source_oracle = _capture_scene_oracle(source_root, (0,))
    request = ExportWorkflowRequest(
        str(output),
        {
            "export_format": export_format,
            "require_target": True,
            "target_model": source_root,
            "target_identity": source_root,
            "validation_report_dir": str(report_dir),
            "validation_report_evidence": {
                "gate": "V070-EXPORT-RELEASE-GATE-1",
                "fixture": source_model.name,
                "fresh_import": True,
                "oracles": ["mesh", "pose", "metadata"],
            },
        },
    )
    workflow = ExportWorkflowService()
    if export_format == "pmd":
        validation = workflow.validate(request)
        policy_codes = [issue.code for issue in validation.report.issues]
        if validation.state != "Blocked" or policy_codes != ["PMD_EXPORT_POLICY_REJECT"]:
            raise AssertionError(
                f"PMD policy probe expected one blocking rejection, got "
                f"state={validation.state!r}, issues={policy_codes!r}"
            )
        report_dir.mkdir(parents=True, exist_ok=True)
        evidence = request.options["validation_report_evidence"]
        validation.report.write_canonical_json(
            report_dir / "report.json",
            target_identity=source_root,
            provenance="ExportWorkflowService",
            evidence=evidence,
        )
        validation.report.write_markdown(
            report_dir / "report.md",
            target_identity=source_root,
            provenance="ExportWorkflowService",
            evidence=evidence,
        )
        if output.exists():
            raise AssertionError(f"PMD policy rejection created an output: {output}")
        return {
            "status": "policy-reject",
            "format": export_format,
            "source": str(source_model),
            "output": None,
            "report_json": str(report_dir / "report.json"),
            "report_md": str(report_dir / "report.md"),
            "policy_code": "PMD_EXPORT_POLICY_REJECT",
            "import_oracles": {
                "mesh": source_oracle["mesh"],
                "pose": source_oracle["pose"],
                "metadata": source_oracle["metadata"],
            },
            "collection": {
                "collector": "ExportWorkflowService validation -> PMD policy",
                "target_model": source_root,
                "source_fresh_import": True,
                "export_writer_called": False,
            },
        }

    result = workflow.execute(request)
    if not result.succeeded:
        raise RuntimeError(f"{export_format} export failed: {result.error or result.report}")
    parsed = PmxData().parse_file(str(output))
    result_root = _fresh_import(output)
    result_oracle = _capture_scene_oracle(result_root, (0,))
    failures = _compare_scene_oracles(source_oracle, result_oracle, pose=True)
    if failures:
        raise AssertionError("; ".join(failures))
    return {
        "status": "pass",
        "format": export_format,
        "source": str(source_model),
        "output": str(output),
        "report_json": str(report_dir / "report.json"),
        "report_md": str(report_dir / "report.md"),
        "parsed_counts": {
            "vertices": len(parsed.vertices),
            "faces": len(parsed.faces),
            "materials": len(parsed.materials),
            "bones": len(parsed.bones),
        },
        "oracles": {
            "mesh": result_oracle["mesh"],
            "pose": result_oracle["pose"],
            "metadata": result_oracle["metadata"],
        },
        "collection": {
            "collector": "ExportWorkflowService -> ExportSceneCollector.collect",
            "target_model": source_root,
            "source_fresh_import": True,
        },
    }


def _run_vmd_case(source_pmx: Path, source_vmd: Path, out_dir: Path) -> dict[str, Any]:
    """Roundtrip a VMD through a Maya scene and compare fresh-import poses."""
    from mmd_tools.core.vmd_data import VmdData
    from mmd_tools.services.export_workflow_service import (
        ExportWorkflowRequest,
        ExportWorkflowService,
    )

    source_root = _fresh_import(source_pmx)
    source_root = _import_vmd_into_current_scene(source_root, source_pmx, source_vmd)
    source_oracle = _capture_scene_oracle(source_root, ORACLE_FRAMES)
    output = out_dir / "motion.vmd"
    report_dir = out_dir / "report"
    result = ExportWorkflowService().execute(
        ExportWorkflowRequest(
            str(output),
            {
                "vmd_mode": "C",
                "export_format": "vmd",
                "require_target": True,
                "target_model": source_root,
                "start_frame": min(ORACLE_FRAMES),
                "end_frame": max(ORACLE_FRAMES),
                "model_name": VmdData().parse_file(str(source_vmd)).header.model_name,
                "target_identity": source_root,
                "validation_report_dir": str(report_dir),
                "validation_report_evidence": {
                    "gate": "V070-EXPORT-RELEASE-GATE-1",
                    "fixture": source_vmd.name,
                    "fresh_import": True,
                    "oracles": ["pose", "metadata"],
                },
            },
        )
    )
    if not result.succeeded:
        raise RuntimeError(f"vmd export failed: {result.error or result.report}")
    parsed = VmdData().parse_file(str(output))
    if not parsed.bone_frames:
        raise AssertionError("VMD output contains no bone frames")
    fresh_root = _fresh_import(source_pmx)
    fresh_root = _import_vmd_into_current_scene(fresh_root, source_pmx, output)
    result_oracle = _capture_scene_oracle(fresh_root, ORACLE_FRAMES)
    failures = _compare_scene_oracles(source_oracle, result_oracle, pose=True, mesh=False)
    if failures:
        raise AssertionError("; ".join(failures))
    return {
        "status": "pass",
        "format": "vmd",
        "source": str(source_vmd),
        "output": str(output),
        "report_json": str(report_dir / "report.json"),
        "report_md": str(report_dir / "report.md"),
        "parsed_counts": {
            "bone_frames": len(parsed.bone_frames),
            "morph_frames": len(parsed.morph_frames),
            "camera_frames": len(parsed.camera_frames),
            "light_frames": len(parsed.light_frames),
        },
        "oracles": {
            "mesh": result_oracle["mesh"],
            "pose": result_oracle["pose"],
            "metadata": result_oracle["metadata"],
        },
        "collection": {
            "collector": "ExportWorkflowService -> VmdSceneCollector.collect",
            "target_model": source_root,
            "source_fresh_import": True,
            "result_fresh_import": True,
        },
    }


def _import_vmd_into_current_scene(root: str, pmx_path: Path, vmd_path: Path) -> str:
    """Apply a model-owned VMD to the current model and require success."""
    from mmd_tools.io.mmd_importer import import_mmd_file

    result = import_mmd_file(
        str(vmd_path),
        options={
            **_import_options(),
            "target_model": root,
            "pmx_path": str(pmx_path),
            "bake_mode": False,
        },
    )
    if not result:
        raise RuntimeError(f"Maya VMD import returned no result: {vmd_path}")
    return root


def run_probe(pmx_path: Path, vmd_path: Path, out_dir: Path) -> dict[str, Any]:
    """Run all model and motion cases in one initialized Maya process."""
    from mmd_tools.core.pmx_data import PmxData
    from maya import standalone

    os.environ.setdefault("MMD_TOOLS_SKIP_SHADER_OVERRIDE", "1")
    try:
        standalone.initialize(name="python")
    except RuntimeError:
        pass
    load_mmd_tools_plugin(ROOT)
    out_dir.mkdir(parents=True, exist_ok=True)
    PmxData().parse_file(str(pmx_path))
    pmd_path = out_dir / "fixtures" / "independent_minimal.pmd"
    _write_independent_pmd_fixture(pmd_path)
    cases = []
    for export_format, source_model in (
        ("pmx", pmx_path),
        ("pmd", pmd_path),
    ):
        case_dir = out_dir / export_format
        try:
            case = _run_model_case(export_format, source_model, case_dir)
            case["conversion_warnings"] = []
        except Exception as exc:
            case = {
                "status": "fail",
                "format": export_format,
                "source": str(source_model),
                "error": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc(limit=12),
            }
        cases.append(case)
    try:
        cases.append(_run_vmd_case(pmx_path, vmd_path, out_dir / "vmd"))
    except Exception as exc:
        cases.append(
            {
                "status": "fail",
                "format": "vmd",
                "source": str(vmd_path),
                "error": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc(limit=12),
            }
        )
    accepted_case_statuses = {"pass", "policy-reject"}
    report = {
        "schema_version": 1,
        "gate": "V070-EXPORT-RELEASE-GATE-1",
        "maya_version": _maya_version(),
        "status": "pass" if all(case["status"] in accepted_case_statuses for case in cases) else "fail",
        "fixture": {"pmx": str(pmx_path), "pmd": str(pmd_path), "vmd": str(vmd_path)},
        "cases": cases,
    }
    (out_dir / "maya-probe.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=_json_default) + "\n",
        encoding="utf-8",
    )
    return report


def _maya_version() -> str:
    """Return the active Maya version without importing it at module load."""
    from maya import cmds

    return str(cmds.about(version=True))


def main(argv: list[str] | None = None) -> int:
    """Parse arguments, run the Maya probe, and return a process status."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pmx", default=str(DEFAULT_PMX))
    parser.add_argument("--vmd", default=str(DEFAULT_VMD))
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args(argv)
    try:
        report = run_probe(
            Path(args.pmx).resolve(),
            Path(args.vmd).resolve(),
            _require_build_path(args.out_dir, "--out-dir"),
        )
    except Exception as exc:
        print(f"Maya export release probe failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        traceback.print_exc()
        return 1
    print(json.dumps({"status": report["status"], "maya_version": report["maya_version"]}, ensure_ascii=False))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
