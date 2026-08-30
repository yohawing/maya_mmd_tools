"""Profile PMX material-split UV welding in a real Maya standalone process."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import statistics
import time


ROOT = Path(__file__).resolve().parents[2]
PYTHON_PLUGIN = ROOT / "mmd_tools" / "plugin_main.py"


def _plugin_path() -> Path:
    """Resolve the C++ plugin selected by the Nox environment."""
    explicit = os.environ.get("MMD_TOOLS_CPP_PLUGIN")
    if explicit:
        path = Path(explicit)
        if path.exists():
            return path
        raise FileNotFoundError(path)
    version = os.environ.get("MAYA_VERSION", "2024")
    config = os.environ.get("MMD_TOOLS_CPP_CONFIG", "Debug")
    path = ROOT / "plug-ins" / version / config / "mmd_tools_cpp.mll"
    if not path.exists():
        raise FileNotFoundError(path)
    return path


def _read_config(path: Path) -> dict:
    """Read the UTF-8 config so non-ASCII PMX paths never cross argv."""
    config = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise ValueError("UV weld profile config must be a JSON object")
    pmx = Path(str(config.get("pmx", "")))
    if not pmx.is_file():
        raise FileNotFoundError(pmx)
    mode = str(config.get("mode", "batch"))
    if mode not in {"baseline", "batch"}:
        raise ValueError(f"Unsupported UV weld profile mode: {mode!r}")
    runs = int(config.get("runs", 3))
    warmup = int(config.get("warmup", 1))
    if runs < 1 or warmup < 0:
        raise ValueError("UV weld profile runs must be positive and warmup non-negative")
    compare = str(config.get("compare", "")).strip()
    separate = config.get("separate_meshes_by_material", True)
    if not isinstance(separate, bool):
        raise ValueError("separate_meshes_by_material must be boolean")
    compare_path = Path(compare) if compare else None
    if compare_path is not None and not compare_path.is_file():
        raise FileNotFoundError(compare_path)
    return {
        "pmx": pmx,
        "mode": mode,
        "runs": runs,
        "warmup": warmup,
        "separate_meshes_by_material": separate,
        "compare": compare_path,
    }


def _int_attribute(cmds, mesh: str, attribute: str) -> list[int]:
    """Read a typed longArray attribute in a stable, JSON-free form."""
    if not cmds.attributeQuery(attribute, node=mesh, exists=True):
        return []
    value = cmds.getAttr(f"{mesh}.{attribute}")
    if value is None:
        return []
    return [int(item) for item in value]


def _digest_ints(values: list[int]) -> str:
    """Hash mapping payloads without putting giant arrays in the profile JSON."""
    payload = json.dumps(values, separators=(",", ":")).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


def _mesh_oracle(cmds, mesh: str) -> dict:
    """Capture the production split mesh counts and provenance fingerprints."""
    source_indices = _int_attribute(cmds, mesh, "mmd_source_vertex_indices")
    source_to_local = _int_attribute(cmds, mesh, "mmd_source_to_local_indices")
    return {
        "name": mesh.rsplit("|", 1)[-1],
        "face_count": int(cmds.polyEvaluate(mesh, face=True)),
        "vertex_count": int(cmds.polyEvaluate(mesh, vertex=True)),
        "source_indices_count": len(source_indices),
        "source_indices_sha256": _digest_ints(source_indices),
        "source_to_local_count": len(source_to_local),
        "source_to_local_sha256": _digest_ints(source_to_local),
    }


def _run_once(
    cmds,
    parse_pmx_file,
    MeshConverter,
    settings,
    setting_keys,
    pmx,
    mode,
    separate_meshes_by_material,
    run_index,
):
    """Import one material-split scene and return its machine-readable profile."""
    cmds.file(new=True, force=True)
    settings.set(setting_keys.IMPORT_MODEL_CREATE_MMD_SHADERS, False)
    settings.set(
        setting_keys.IMPORT_MODEL_SEPARATE_MESHES_BY_MATERIAL,
        separate_meshes_by_material,
    )
    parsed = parse_pmx_file(str(pmx), use_native_pmx_parse=False)
    converter = MeshConverter(str(pmx))
    if mode == "baseline":
        converter._cpp_uv_weld_batch_command_available = lambda: False
    root = cmds.group(empty=True, name=f"uv_weld_profile_root_{run_index}")
    started = time.perf_counter()
    try:
        _mesh_group, meshes = converter.convert_pmx_mesh(parsed, root)
        conversion_seconds = time.perf_counter() - started
        profile = copy.deepcopy(converter.profile)
        profile["mesh_conversion_sec"] = round(conversion_seconds, 6)
        profile["mesh_count"] = len(meshes) if isinstance(meshes, list) else 1
        profile["face_count_scene"] = sum(
            int(cmds.polyEvaluate(mesh, face=True)) for mesh in (meshes if isinstance(meshes, list) else [meshes])
        )
        profile["vertex_count_scene"] = sum(
            int(cmds.polyEvaluate(mesh, vertex=True)) for mesh in (meshes if isinstance(meshes, list) else [meshes])
        )
        profile["mesh_oracle"] = [
            _mesh_oracle(cmds, mesh)
            for mesh in (meshes if isinstance(meshes, list) else [meshes])
        ]
        return profile
    finally:
        if cmds.objExists(root):
            cmds.delete(root)


def main() -> int:
    """Run warmups and measured imports, writing one UTF-8 JSON report."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    config = _read_config(Path(args.config))
    output = Path(args.out)

    import maya.cmds as cmds
    import maya.standalone

    plugin_path = _plugin_path()
    os.environ["PATH"] = str(plugin_path.parent) + os.pathsep + os.environ.get("PATH", "")
    dll_handle = os.add_dll_directory(str(plugin_path.parent)) if hasattr(os, "add_dll_directory") else None
    maya.standalone.initialize(name="python")
    try:
        cmds.loadPlugin(str(plugin_path), quiet=True)
        capabilities = cmds.mmdWeldUvSeamVertices(queryCapabilities=True)
        for capability in ("sourceToLocalV1", "morphEquivalentV1", "batchV1", "profileV1"):
            if capability not in capabilities:
                raise RuntimeError(f"mmdWeldUvSeamVertices lacks {capability}")
        cmds.loadPlugin(str(PYTHON_PLUGIN), quiet=True)
        from mmd_tools.core.mmd_parser import parse_pmx_file
        from mmd_tools.core.settings import settings
        from mmd_tools.converters import MeshConverter
        from mmd_tools.core import settings_keys as setting_keys

        for warmup_index in range(config["warmup"]):
            _run_once(
                cmds,
                parse_pmx_file,
                MeshConverter,
                settings,
                setting_keys,
                config["pmx"],
                config["mode"],
                config["separate_meshes_by_material"],
                f"warmup_{warmup_index}",
            )
        runs = [
            _run_once(
                cmds,
                parse_pmx_file,
                MeshConverter,
                settings,
                setting_keys,
                config["pmx"],
                config["mode"],
                config["separate_meshes_by_material"],
                index,
            )
            for index in range(config["runs"])
        ]
        oracle = None
        if config["compare"] is not None:
            baseline = json.loads(config["compare"].read_text(encoding="utf-8"))
            if baseline.get("status") != "pass" or baseline.get("mode") != "baseline":
                raise ValueError("--compare must point to a passing baseline UV weld profile")
            if baseline.get("pmx") != str(config["pmx"]):
                raise ValueError("baseline and batch profiles use different PMX paths")
            if baseline.get("separate_meshes_by_material", True) != config["separate_meshes_by_material"]:
                raise ValueError("baseline and batch profiles use different mesh split modes")
            if len(baseline.get("runs", [])) != len(runs):
                raise ValueError("baseline and batch profiles use different run counts")
            comparisons = []
            for run_index, (baseline_run, batch_run) in enumerate(zip(baseline["runs"], runs)):
                baseline_meshes = {
                    item["name"]: item for item in baseline_run.get("mesh_oracle", [])
                }
                batch_meshes = {
                    item["name"]: item for item in batch_run.get("mesh_oracle", [])
                }
                if set(baseline_meshes) != set(batch_meshes):
                    raise ValueError(
                        f"run {run_index}: baseline/batch mesh sets differ: "
                        f"{sorted(baseline_meshes)} != {sorted(batch_meshes)}"
                    )
                mismatches = []
                for name in sorted(baseline_meshes):
                    expected = baseline_meshes[name]
                    actual = batch_meshes[name]
                    if expected != actual:
                        mismatches.append({"mesh": name, "baseline": expected, "batch": actual})
                if baseline_run.get("face_count_scene") != batch_run.get("face_count_scene"):
                    mismatches.append(
                        {
                            "total_face_count": {
                                "baseline": baseline_run.get("face_count_scene"),
                                "batch": batch_run.get("face_count_scene"),
                            }
                        }
                    )
                if baseline_run.get("native_uv_weld_meshes") != batch_run.get("native_uv_weld_meshes"):
                    baseline_counts = [
                        (item.get("mesh", "").rsplit("|", 1)[-1], item.get("oldVertexCount"), item.get("newVertexCount"), item.get("status"))
                        for item in baseline_run.get("native_uv_weld_meshes", [])
                    ]
                    batch_counts = [
                        (item.get("mesh", "").rsplit("|", 1)[-1], item.get("oldVertexCount"), item.get("newVertexCount"), item.get("status"))
                        for item in batch_run.get("native_uv_weld_meshes", [])
                    ]
                    if baseline_counts != batch_counts:
                        mismatches.append(
                            {"weld_counts": {"baseline": baseline_counts, "batch": batch_counts}}
                        )
                comparisons.append({"run": run_index, "status": "pass" if not mismatches else "fail"})
                if mismatches:
                    raise ValueError(f"run {run_index}: baseline/batch oracle mismatch: {mismatches}")
            oracle = {"status": "pass", "baseline": str(config["compare"]), "runs": comparisons}
        native_seconds = [float(run["native_uv_weld_sec"]) for run in runs]
        report = {
            "status": "pass",
            "mode": config["mode"],
            "pmx": str(config["pmx"]),
            "separate_meshes_by_material": config["separate_meshes_by_material"],
            "warmup_runs": config["warmup"],
            "runs": runs,
            **({"oracle": oracle} if oracle is not None else {}),
            "median": {
                "mesh_conversion_sec": statistics.median(float(run["mesh_conversion_sec"]) for run in runs),
                "native_uv_weld_sec": statistics.median(native_seconds),
                "native_uv_weld_command_calls": statistics.median(
                    int(run["native_uv_weld_command_calls"]) for run in runs
                ),
                "native_uv_weld_pmx_read_count": statistics.median(
                    int(run["native_uv_weld_pmx_read_count"]) for run in runs
                ),
                "native_uv_weld_geometry_parse_count": statistics.median(
                    int(run["native_uv_weld_geometry_parse_count"]) for run in runs
                ),
                "native_uv_weld_non_geometry_parse_count": statistics.median(
                    int(run["native_uv_weld_non_geometry_parse_count"]) for run in runs
                ),
            },
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return 0
    except Exception as exc:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        raise
    finally:
        maya.standalone.uninitialize()
        if dll_handle is not None:
            dll_handle.close()


if __name__ == "__main__":
    raise SystemExit(main())
