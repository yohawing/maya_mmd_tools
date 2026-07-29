"""Generate the deterministic CC0 Control Rig VMD fixture.

The source JSON contains semantic track values.  Bone names are resolved from
the PMX structure (IK flags and grant-parent metadata) so an accidental name
or index drift fails closed instead of silently producing a different fixture.
Run from the repository root with ``python tests/data/generate_yw_test_model_control_rig_vmd.py``.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import unicodedata
from pathlib import Path
from typing import Any, Mapping

if __package__ in (None, ""):
    _ROOT = Path(__file__).resolve().parents[2]
    if str(_ROOT) not in sys.path:
        sys.path.insert(0, str(_ROOT))

from mmd_tools.core.mmd_parser import parse_pmx_file
from mmd_tools.core.pmx_data.bone import PmxBoneFlag
from mmd_tools.core.vmd_data import VmdData


SCRIPT = Path(__file__).resolve()
_ROOT = SCRIPT.parents[2]
DEFAULT_SOURCE = SCRIPT.with_name("yw_test_model_control_rig_vmd.json")
DEFAULT_MODEL = SCRIPT.with_name("yw_test_model.pmx")
DEFAULT_OUTPUT = SCRIPT.with_name("yw_test_model_control_rig_vmd.vmd")
_NAME_WIDTHS = {"bone": 15, "ik": 20, "model": 20}


def _vmd_exporter_class():
    """Load ``VmdExporter`` without importing Maya-only ``mmd_tools.io`` exports."""

    try:
        from mmd_tools.io.vmd_exporter import VmdExporter

        return VmdExporter
    except ModuleNotFoundError as exc:
        if exc.name != "maya":
            raise
    # Use a private module name under ``mmd_tools`` so relative imports resolve
    # normally without installing a fake ``mmd_tools.io`` package in the
    # process.  The temporary module entry is removed before returning.
    module_name = "mmd_tools._fixture_vmd_exporter"
    module_path = _ROOT / "mmd_tools" / "io" / "vmd_exporter.py"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"unable to load VmdExporter from {module_path}")
    module = importlib.util.module_from_spec(spec)
    previous = sys.modules.get(module_name)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
        return module.VmdExporter
    finally:
        if previous is None:
            sys.modules.pop(module_name, None)
        else:
            sys.modules[module_name] = previous


def _shift_jis_name(value: str, width: int, label: str) -> str:
    """Validate strict Shift-JIS roundtrip and fixed-width capacity."""

    name = str(value)
    try:
        encoded = name.encode("shift_jis", errors="strict")
        decoded = encoded.decode("shift_jis", errors="strict")
    except UnicodeError as exc:
        raise ValueError(f"{label} is not representable in Shift-JIS: {name!r}") from exc
    if decoded != name or len(encoded) >= width:
        raise ValueError(
            f"{label} fails Shift-JIS roundtrip or fixed-width capacity: "
            f"{name!r} ({len(encoded)} bytes, width {width})"
        )
    return name


def _side_matches(name: str, side: str) -> bool:
    marker = "左" if side == "left" else "右"
    return marker in name


def _normal_name(name: str) -> str:
    return unicodedata.normalize("NFKC", name)


def resolve_structural_roles(model_path: str | Path) -> dict[str, dict[str, Any]]:
    """Resolve IK controllers and grant sources from PMX bone metadata."""

    pmx = parse_pmx_file(str(model_path))
    bones = list(pmx.bones)
    ik_bones = [
        (index, bone)
        for index, bone in enumerate(bones)
        if bone.get_flag(PmxBoneFlag.IK)
    ]
    result: dict[str, dict[str, Any]] = {}
    for side in ("left", "right"):
        candidates = [
            (index, bone)
            for index, bone in ik_bones
            if _side_matches(bone.name, side)
        ]
        foot = [
            (index, bone)
            for index, bone in candidates
            if "足" in _normal_name(bone.name) and "つま先" not in _normal_name(bone.name)
        ]
        toe = [
            (index, bone)
            for index, bone in candidates
            if "つま先" in _normal_name(bone.name)
        ]
        if len(foot) != 1 or len(toe) != 1:
            raise ValueError(
                f"expected one {side} foot and toe IK controller, got "
                f"foot={len(foot)} toe={len(toe)}"
            )
        foot_index, foot_bone = foot[0]
        toe_index, toe_bone = toe[0]

        # A side's foot D bone is a structural grant target.  Its parent is
        # the authored grant source and is therefore safe to key directly.
        grant_targets = [
            (index, bone)
            for index, bone in enumerate(bones)
            if _side_matches(bone.name, side)
            and _normal_name(bone.name).endswith("足D")
            and bone.grant_parent_bone_index >= 0
        ]
        if len(grant_targets) != 1:
            raise ValueError(f"expected one {side} foot grant target, got {len(grant_targets)}")
        grant_target_index, grant_target = grant_targets[0]
        source_index = int(grant_target.grant_parent_bone_index)
        if source_index >= len(bones):
            raise ValueError(f"grant source index out of range for {grant_target.name}")
        source = bones[source_index]
        if not _side_matches(source.name, side) or "足" not in _normal_name(source.name):
            raise ValueError(f"grant source is not side-specific foot bone: {source.name!r}")

        result[f"{side}_foot_ik"] = {
            "index": foot_index,
            "name": _shift_jis_name(foot_bone.name, _NAME_WIDTHS["ik"], f"{side} foot IK"),
            "kind": "foot_ik",
            "side": side,
        }
        result[f"{side}_toe_ik"] = {
            "index": toe_index,
            "name": _shift_jis_name(toe_bone.name, _NAME_WIDTHS["ik"], f"{side} toe IK"),
            "kind": "toe_ik",
            "side": side,
        }
        result[f"{side}_grant_source"] = {
            "index": source_index,
            "name": _shift_jis_name(source.name, _NAME_WIDTHS["bone"], f"{side} grant source"),
            "kind": "grant_source",
            "side": side,
            "target_index": grant_target_index,
            "target_name": _shift_jis_name(
                grant_target.name, _NAME_WIDTHS["bone"], f"{side} grant target"
            ),
            "rate": float(grant_target.grant_rate),
        }
    return result


def _source_data(source_path: str | Path) -> Mapping[str, Any]:
    try:
        payload = json.loads(Path(source_path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"invalid fixture source JSON: {source_path}") from exc
    if not isinstance(payload, Mapping) or payload.get("schema") != 1:
        raise ValueError(f"unsupported fixture source schema: {source_path}")
    return payload


def build_vmd_data(
    model_path: str | Path,
    source_path: str | Path = DEFAULT_SOURCE,
    *,
    require_no_morphs: bool = True,
) -> tuple[VmdData, dict[str, dict[str, Any]]]:
    """Build VMD data and resolved role metadata without writing a file."""

    source = _source_data(source_path)
    roles = resolve_structural_roles(model_path)
    model = parse_pmx_file(str(model_path))
    if require_no_morphs and model.morphs:
        raise ValueError("yw_test_model fixture unexpectedly contains morphs; do not fabricate Bone Morph coverage")
    model_name = str(source.get("model_name", model.header.model_name))
    _shift_jis_name(model_name, _NAME_WIDTHS["model"], "VMD model name")
    if model_name != model.header.model_name:
        raise ValueError(f"source model_name does not match PMX header: {model_name!r}")

    selection = source.get("selection")
    if not isinstance(selection, Mapping):
        raise ValueError("fixture source selection must be an object")
    for role, declaration in selection.items():
        if role not in roles or not isinstance(declaration, Mapping):
            raise ValueError(f"fixture source selection does not match PMX structure: {role!r}")
        for key in ("side", "kind"):
            if declaration.get(key) != roles[role].get(key):
                raise ValueError(f"fixture source role mismatch for {role}: {key}")
        if role.endswith("grant_source"):
            target_suffix = str(declaration.get("target_suffix", ""))
            target_name = str(roles[role].get("target_name", ""))
            if not target_suffix or not _normal_name(target_name).endswith(_normal_name(target_suffix)):
                raise ValueError(f"fixture source grant target mismatch for {role}")

    motion = source.get("bone_motion")
    if not isinstance(motion, Mapping) or motion.get("frames") != [0, 10, 20]:
        raise ValueError("bone_motion must author exactly frames [0, 10, 20]")
    tracks = motion.get("tracks")
    if not isinstance(tracks, Mapping) or set(tracks) != set(roles):
        raise ValueError("bone_motion tracks must cover every resolved role exactly once")
    bone_frames = []
    for role in sorted(roles):
        entries = tracks[role]
        if not isinstance(entries, list) or [entry.get("frame") for entry in entries] != [0, 10, 20]:
            raise ValueError(f"bone track {role} must contain ordered frames [0, 10, 20]")
        for entry in entries:
            if not isinstance(entry, Mapping):
                raise ValueError(f"invalid bone entry for {role}")
            bone_frames.append(
                {
                    "bone_name": roles[role]["name"],
                    "frame_number": int(entry["frame"]),
                    "position": tuple(float(value) for value in entry["position"]),
                    "rotation": tuple(float(value) for value in entry["rotation"]),
                }
            )

    ik_enable = source.get("ik_enable")
    if not isinstance(ik_enable, list) or [entry.get("frame") for entry in ik_enable] != [0, 6, 12, 20]:
        raise ValueError("ik_enable must author exactly frames [0, 6, 12, 20]")
    ik_frames = []
    for entry in ik_enable:
        if not isinstance(entry, Mapping):
            raise ValueError("invalid IK enable entry")
        states = []
        for side in ("left", "right"):
            enabled = bool(entry[side])
            for suffix in ("foot_ik", "toe_ik"):
                states.append((roles[f"{side}_{suffix}"]["name"], int(enabled)))
        ik_frames.append({"frame_number": int(entry["frame"]), "visible": 1, "ik_states": states})

    # Import lazily because ``mmd_tools.io`` exposes Maya-dependent importers
    # from its package initializer; role and source validation stay usable in
    # a plain Python process as well as mayapy.
    VmdExporter = _vmd_exporter_class()
    exporter = VmdExporter(native_exporter=None)
    return exporter.to_vmd_data(
        {"model_name": model_name, "bone_frames": bone_frames, "ik_show_hide_frames": ik_frames}
    ), roles


def generate_fixture(
    output_path: str | Path = DEFAULT_OUTPUT,
    model_path: str | Path = DEFAULT_MODEL,
    source_path: str | Path = DEFAULT_SOURCE,
) -> Path:
    """Write the deterministic VMD fixture and return its path."""

    output = Path(output_path)
    vmd_data, _roles = build_vmd_data(model_path, source_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    VmdExporter = _vmd_exporter_class()
    VmdExporter(native_exporter=None).export_vmd_animation(str(output), vmd_data)
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    generate_fixture(args.output, args.model, args.source)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
