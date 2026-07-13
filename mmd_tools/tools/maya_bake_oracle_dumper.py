"""
Maya bake後のシーン状態をGoldenOracle互換JSONLとして出力するCLI。

GoldenOracleのmotion-numeric manifestからPMX/VMD/framesを読み取り、
MayaでPMXをインポートしてVMDをベイクした後、各フレームの最終的な
joint worldMatrixとblendShape weightをJSONLに保存する。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from mmd_tools.core.constants import ATTR_MMD_BONE_INDEX, ATTR_MMD_BONE_NAME
from mmd_tools.core.mmd_parser import parse_pmx_file
from mmd_tools.core.vmd_data import VmdData


def _load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _resolve_manifest_path(manifest_path: Path, raw_path: str) -> Path:
    path = Path(raw_path)
    if path.is_absolute():
        return path
    return (manifest_path.parent / path).resolve()


def _find_case(manifest: Dict[str, Any], case_name: str) -> Dict[str, Any]:
    for case in manifest.get("cases", []):
        if case.get("name") == case_name:
            return case
    raise ValueError(f"case not found in manifest: {case_name}")


def _offset_tag(sample_frame_offset: float) -> str:
    if not sample_frame_offset:
        return ""
    if float(sample_frame_offset).is_integer():
        value = str(int(sample_frame_offset))
    else:
        value = str(sample_frame_offset).replace(".", "p")
    return f".offset{value}"


def _default_output_path(
    manifest_path: Path,
    case: Dict[str, Any],
    sample_frame_offset: float,
) -> Path:
    oracle = case.get("oracle", {})
    oracle_path = oracle.get("path")
    filename = f"maya-bake{_offset_tag(sample_frame_offset)}.oracle.jsonl"
    if oracle_path:
        resolved_oracle = _resolve_manifest_path(manifest_path, oracle_path)
        return resolved_oracle.with_name(filename)

    out_dir = manifest_path.parent.parent / "runs" / "motion-numeric"
    return out_dir / case["name"] / filename


def _get_attr_if_exists(cmds_module: Any, node: str, attr: str) -> Any:
    if not cmds_module.attributeQuery(attr, node=node, exists=True):
        return None
    return cmds_module.getAttr(f"{node}.{attr}")


def _convert_maya_world_matrix_to_mmd(maya_matrix: List[float]) -> List[float]:
    """Convert Maya worldMatrix (xform -ws -m, row-major) to MMD oracle column-major.

    Maya returns row-major (p*M convention); the oracle stores column-major
    (matching glam::Mat4::to_cols_array). The Z-axis sign flip S=diag(1,1,-1)
    converts Maya→MMD coordinate system: rotation gets S*R*S, translation
    gets S*T.
    """
    if len(maya_matrix) != 16:
        return [float(v) for v in maya_matrix]
    signs = (1.0, 1.0, -1.0)
    mmd = [0.0] * 16
    for row in range(3):
        for col in range(3):
            mmd[col * 4 + row] = float(maya_matrix[row * 4 + col]) * signs[row] * signs[col]
    for i in range(3):
        mmd[12 + i] = float(maya_matrix[12 + i]) * signs[i]
    mmd[15] = 1.0
    return mmd


def _collect_bones(cmds_module: Any) -> List[Dict[str, Any]]:
    bones = []
    for fallback_index, joint in enumerate(cmds_module.ls(type="joint") or []):
        raw_index = _get_attr_if_exists(cmds_module, joint, ATTR_MMD_BONE_INDEX)
        raw_name = _get_attr_if_exists(cmds_module, joint, ATTR_MMD_BONE_NAME)
        index = int(raw_index) if raw_index is not None else fallback_index
        name = raw_name or joint
        matrix = cmds_module.xform(joint, query=True, worldSpace=True, matrix=True) or []
        world_matrix = _convert_maya_world_matrix_to_mmd([float(value) for value in matrix])
        bones.append(
            {
                "index": index,
                "name": name,
                "worldMatrix": world_matrix,
            }
        )

    return sorted(bones, key=lambda bone: bone["index"])


def _iter_blend_shape_weights(cmds_module: Any, blend_shape: str) -> Iterable[Dict[str, Any]]:
    weight_count = cmds_module.blendShape(blend_shape, query=True, weightCount=True) or 0
    for index in range(int(weight_count)):
        alias = cmds_module.aliasAttr(f"{blend_shape}.weight[{index}]", query=True)
        name = alias or f"{blend_shape}.weight[{index}]"
        weight = cmds_module.getAttr(f"{blend_shape}.weight[{index}]")
        yield {
            "index": index,
            "name": name,
            "weight": float(weight or 0.0),
        }


def _collect_morphs(cmds_module: Any) -> List[Dict[str, Any]]:
    """Legacy collector: returns morphs using blendShape-local weight indices.

    Kept for unit test compatibility. Production use _collect_morphs_in_pmx_order.
    """
    morphs = []
    for blend_shape in cmds_module.ls(type="blendShape") or []:
        morphs.extend(_iter_blend_shape_weights(cmds_module, blend_shape))
    return morphs


def _build_maya_morph_weight_map(cmds_module: Any) -> Dict[str, float]:
    """Scan all blendShapes and return {alias_name: current_weight}.

    Aliases are set by morph import using sanitize_text on PMX morph names (for vertex morphs).
    Non-vertex morphs have no BS target and will default to 0.0 in PMX-order output.
    Tries both .weight[] and .w[] forms for alias query robustness.
    """
    weight_map: Dict[str, float] = {}
    for blend_shape in cmds_module.ls(type="blendShape") or []:
        weight_count = int(cmds_module.blendShape(blend_shape, query=True, weightCount=True) or 0)
        for i in range(weight_count):
            alias = cmds_module.aliasAttr(f"{blend_shape}.weight[{i}]", query=True)
            if alias is None:
                alias = cmds_module.aliasAttr(f"{blend_shape}.w[{i}]", query=True)
            name = alias or f"{blend_shape}.weight[{i}]"
            try:
                weight = cmds_module.getAttr(f"{blend_shape}.weight[{i}]")
            except Exception:
                weight = 0.0
            if name:
                weight_map[name] = float(weight or 0.0)
    return weight_map


def _collect_morphs_in_pmx_order(cmds_module: Any, pmx_morph_names: List[str]) -> List[Dict[str, Any]]:
    """Return morph records in PMX morphs[] order (index + original name from PMX).

    This keeps the generated oracle stable by PMX morph index. Weights are looked up by
    alias (sanitized name) from the Maya blendShape(s) created during PMX import.
    Non-mapped morphs (e.g. bone/group morphs not represented in Maya BS) get 0.0.
    """
    alias_weight_map = _build_maya_morph_weight_map(cmds_module)
    # sanitize under maya (matches what morph_converter used for aliasAttr);
    # in plain-python unit tests (no maya) falls back to identity so FakeCmds aliases match pmx names directly.
    try:
        from mmd_tools.core.maya_name_utils import sanitize_text
    except Exception:
        def sanitize_text(name: str) -> str:
            return name or ""

    morphs: List[Dict[str, Any]] = []
    for index, name in enumerate(pmx_morph_names):
        if not name:
            name = f"morph_{index}"
        key = sanitize_text(name)
        weight = alias_weight_map.get(key)
        if weight is None:
            weight = alias_weight_map.get(name, 0.0)
        morphs.append({"index": index, "name": name, "weight": float(weight)})
    return morphs


def _make_record(
    *,
    frame: int,
    evaluated_frame: float,
    pmx_path: Path,
    vmd_path: Path,
    bones: List[Dict[str, Any]],
    morphs: List[Dict[str, Any]],
) -> Dict[str, Any]:
    return {
        "schemaVersion": 1,
        "source": {
            "mmdVersion": "maya_mmd_tools",
            "dumperVersion": "1.0.0",
            "backend": "maya_mmd_tools.maya-bake",
            "model": str(pmx_path),
            "motion": str(vmd_path),
            "evaluatedFrame": evaluated_frame,
        },
        "frame": frame,
        "models": [
            {
                "index": 0,
                "name": str(pmx_path),
                "filename": str(pmx_path),
                "visible": True,
                "bones": bones,
                "morphs": morphs,
            }
        ],
    }


def _load_maya_cmds() -> Any:
    try:
        from maya import cmds
    except Exception as exc:
        raise RuntimeError("maya_bake_oracle_dumper must be run under mayapy") from exc

    if hasattr(cmds, "file"):
        return cmds

    try:
        import maya.standalone

        maya.standalone.initialize(name="python")
    except Exception:
        if not hasattr(cmds, "file"):
            raise

    from maya import cmds as initialized_cmds

    if not hasattr(initialized_cmds, "file"):
        raise RuntimeError("maya.cmds is unavailable after Maya standalone initialization")
    return initialized_cmds


def dump_maya_bake_oracle(
    *,
    manifest_path: Path,
    case_name: str,
    output_path: Optional[Path] = None,
    sample_frame_offset: float = 0.0,
) -> Path:
    cmds = _load_maya_cmds()
    from mmd_tools.io.pmx_importer import import_pmx_file
    from mmd_tools.io.vmd_importer import import_vmd_file

    manifest = _load_json(manifest_path)
    case = _find_case(manifest, case_name)
    assets = case.get("assets", {})
    pmx_path = _resolve_manifest_path(manifest_path, assets["model"])
    vmd_path = _resolve_manifest_path(manifest_path, assets["motion"])
    frames = [int(frame) for frame in case.get("frames", [])]
    if not frames:
        raise ValueError(f"case has no frames: {case_name}")
    if not pmx_path.exists():
        raise FileNotFoundError(f"PMX not found: {pmx_path}")
    if not vmd_path.exists():
        raise FileNotFoundError(f"VMD not found: {vmd_path}")

    output_path = output_path or _default_output_path(manifest_path, case, sample_frame_offset)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    cmds.file(new=True, force=True)

    pmx_data = parse_pmx_file(str(pmx_path))
    pmx_morph_names = [getattr(m, "name", "") for m in getattr(pmx_data, "morphs", [])]
    target_model = import_pmx_file(
        pmx_data,
        str(pmx_path),
        options={
            "setup_rig": False,
            "setup_bone_orientation": False,
        },
    )
    if not target_model:
        raise RuntimeError(f"failed to import PMX into Maya: {pmx_path}")

    vmd_data = VmdData()
    vmd_data.parse_file(str(vmd_path))
    if not import_vmd_file(vmd_data, str(vmd_path), options={"target_model": target_model, "pmx_path": str(pmx_path), "bake_mode": True}):
        raise RuntimeError(f"failed to import VMD into Maya: {vmd_path}")

    with output_path.open("w", encoding="utf-8", newline="\n") as f:
        for frame in frames:
            evaluated_frame = float(frame) + sample_frame_offset
            cmds.currentTime(evaluated_frame, edit=True)
            record = _make_record(
                frame=frame,
                evaluated_frame=evaluated_frame,
                pmx_path=pmx_path,
                vmd_path=vmd_path,
                bones=_collect_bones(cmds),
                morphs=_collect_morphs_in_pmx_order(cmds, pmx_morph_names),
            )
            f.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
            f.write("\n")

    return output_path


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Dump Maya-baked PMX/VMD results as oracle JSONL."
    )
    parser.add_argument("--manifest", required=True, help="GoldenOracle motion-numeric manifest path")
    parser.add_argument("--case", required=True, help="Manifest case name")
    parser.add_argument("--output", help="Output JSONL path. Defaults to maya-bake.oracle.jsonl next to oracle.")
    parser.add_argument(
        "--sample-frame-offset",
        type=float,
        default=0.0,
        help="Evaluate Maya at manifest frame plus this offset, while keeping output frame labels unchanged.",
    )
    args = parser.parse_args(argv)

    manifest_path = Path(args.manifest).resolve()
    output_path = Path(args.output).resolve() if args.output else None

    try:
        actual_path = dump_maya_bake_oracle(
            manifest_path=manifest_path,
            case_name=args.case,
            output_path=output_path,
            sample_frame_offset=args.sample_frame_offset,
        )
        print(f"maya bake oracle written: {actual_path}")
        return 0
    except Exception as exc:
        print(f"maya bake oracle dump failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
