"""Generate the deterministic Control Rig Bone Morph coverage fixture.

The PMX is derived from the repository's CC0 ``yw_test_model`` using the
legacy PMX writer.  A single explicit BoneMorph and its VMD weight keys are
added from semantic source JSON; no binary hand editing is permitted.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Mapping

if __package__ in (None, ""):
    _ROOT = Path(__file__).resolve().parents[2]
    if str(_ROOT) not in sys.path:
        sys.path.insert(0, str(_ROOT))

from mmd_tools.core.mmd_parser import parse_pmx_file
from mmd_tools.core.pmx_data.morph import PmxMorph, PmxMorphType
from mmd_tools.core.utils import choose_reference_index_size


SCRIPT = Path(__file__).resolve()
ROOT = SCRIPT.parents[2]
BASE_MODEL = SCRIPT.with_name("yw_test_model.pmx")
DEFAULT_SOURCE = SCRIPT.with_name("yw_test_model_control_rig_bone_morph.json")
DEFAULT_MODEL = SCRIPT.with_name("yw_test_model_control_rig_bone_morph.pmx")
DEFAULT_OUTPUT = SCRIPT.with_name("yw_test_model_control_rig_bone_morph.vmd")
_VMD_GENERATOR = SCRIPT.with_name("generate_yw_test_model_control_rig_vmd.py")


def _load_vmd_generator():
    spec = importlib.util.spec_from_file_location(
        "yw_test_model_control_rig_vmd_generator_for_bone_morph", _VMD_GENERATOR
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load VMD generator: {_VMD_GENERATOR}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _source_data(source_path: str | Path) -> Mapping[str, Any]:
    try:
        payload = json.loads(Path(source_path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"invalid fixture source JSON: {source_path}") from exc
    if not isinstance(payload, Mapping) or payload.get("schema") != 1:
        raise ValueError(f"unsupported fixture source schema: {source_path}")
    return payload


def _morph_spec(source: Mapping[str, Any]) -> Mapping[str, Any]:
    spec = source.get("bone_morph")
    if not isinstance(spec, Mapping):
        raise ValueError("bone_morph source must be an object")
    name = str(spec.get("name", ""))
    bone_name = str(spec.get("bone_name", ""))
    frames = spec.get("frames")
    if not name or not bone_name or not isinstance(frames, list) or not frames:
        raise ValueError("bone_morph requires name, bone_name, and frames")
    if [int(entry.get("frame", -1)) for entry in frames if isinstance(entry, Mapping)] != [0, 10, 20]:
        raise ValueError("bone_morph frames must be exactly [0, 10, 20]")
    return spec


def generate_model(
    output_path: str | Path = DEFAULT_MODEL,
    base_model_path: str | Path = BASE_MODEL,
    source_path: str | Path = DEFAULT_SOURCE,
) -> Path:
    """Generate the PMX with one explicit BoneMorph offset."""

    source = _source_data(source_path)
    spec = _morph_spec(source)
    pmx = parse_pmx_file(str(base_model_path), use_native_pmx_parse=False)
    if pmx.morphs:
        raise ValueError("base model already has morphs; fixture derivation must remain deterministic")
    bone_name = str(spec["bone_name"])
    bone_indices = [index for index, bone in enumerate(pmx.bones) if bone.name == bone_name]
    if len(bone_indices) != 1:
        raise ValueError(f"BoneMorph target must resolve exactly one PMX bone: {bone_name!r}")
    offsets = [
        {
            "bone_index": bone_indices[0],
            "translation": tuple(float(value) for value in spec["translation"]),
            "rotation": tuple(float(value) for value in spec.get("rotation", (0.0, 0.0, 0.0, 1.0))),
        }
    ]
    if len(offsets[0]["translation"]) != 3 or len(offsets[0]["rotation"]) != 4:
        raise ValueError("BoneMorph offset requires translation[3] and rotation[4]")
    morph = PmxMorph(
        vertex_index_size=pmx.header.vertex_index_size,
        material_index_size=pmx.header.material_index_size,
        bone_index_size=pmx.header.bone_index_size,
        morph_index_size=pmx.header.morph_index_size,
        rigid_body_index_size=pmx.header.rigid_body_index_size,
        encoding=pmx.header.encoding,
    )
    morph.name = str(spec["name"])
    morph.name_english = str(spec.get("name_english", morph.name))
    morph.panel = int(spec.get("panel", 4))
    morph.morph_type = PmxMorphType.BoneMorph
    morph.offsets = offsets
    pmx.morphs.append(morph)
    pmx.header.morph_index_size = choose_reference_index_size(len(pmx.morphs))
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    pmx.write_file(str(output))
    return output


def generate_motion(
    output_path: str | Path = DEFAULT_OUTPUT,
    model_path: str | Path = DEFAULT_MODEL,
    source_path: str | Path = DEFAULT_SOURCE,
) -> Path:
    """Generate VMD bone, morph, and IK-property tracks."""

    source = _source_data(source_path)
    spec = _morph_spec(source)
    generator = _load_vmd_generator()
    vmd_data, _roles = generator.build_vmd_data(
        model_path,
        source_path,
        require_no_morphs=False,
    )
    morph_frames = []
    for entry in spec["frames"]:
        if not isinstance(entry, Mapping):
            raise ValueError("invalid BoneMorph VMD frame")
        morph_frames.append(
            {
                "morph_name": str(spec["name"]),
                "frame_number": int(entry["frame"]),
                "value": float(entry["weight"]),
            }
        )
    vmd_data.morph_frames = _load_vmd_generator()._vmd_exporter_class()(native_exporter=None).to_vmd_data(
        {
            "model_name": vmd_data.header.model_name,
            "bone_frames": vmd_data.bone_frames,
            "ik_show_hide_frames": vmd_data.ik_show_hide_frames,
            "morph_frames": morph_frames,
        }
    ).morph_frames
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    vmd_data.write_file(str(output))
    return output


def generate_fixture(
    model_output: str | Path = DEFAULT_MODEL,
    vmd_output: str | Path = DEFAULT_OUTPUT,
    base_model_path: str | Path = BASE_MODEL,
    source_path: str | Path = DEFAULT_SOURCE,
) -> tuple[Path, Path]:
    """Generate both deterministic fixture binaries."""

    model = generate_model(model_output, base_model_path, source_path)
    motion = generate_motion(vmd_output, model, source_path)
    return model, motion


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-model", type=Path, default=BASE_MODEL)
    parser.add_argument("--model-output", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--vmd-output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    generate_fixture(args.model_output, args.vmd_output, args.base_model, args.source)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
