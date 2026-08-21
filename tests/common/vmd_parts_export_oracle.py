"""Pure-Python VMD parts oracle used only by headless unit tests."""

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Mapping

def export_vmd_from_parts_oracle(
    metadata: Mapping[str, Any],
    bone_name_indices: Any,
    bone_frames: Any,
    bone_translations_xyz: Any,
    bone_rotations_xyzw: Any,
    bone_interpolations: Any,
    morph_name_indices: Any,
    morph_frames: Any,
    morph_weights: Any,
) -> bytes:
    """Serialize typed parts without requiring the native runtime DLL."""

    # Import lazily so the CI importability probe does not enter the Maya-aware
    # ``mmd_tools.io`` package before pytest installs its headless Maya stub.
    from mmd_tools.io.vmd_exporter import VmdExporter

    bone_names = tuple(metadata.get("boneNames", ()))
    morph_names = tuple(metadata.get("morphNames", ()))
    payload = {
        "model_name": _name(metadata, "modelName", "modelNameBytes"),
        "bone_frames": [
            {
                "bone_name": _indexed_name(bone_names, bone_name_indices[index]),
                "frame": bone_frames[index],
                "position": bone_translations_xyz[index * 3 : index * 3 + 3],
                "rotation": bone_rotations_xyzw[index * 4 : index * 4 + 4],
                "interpolation": bytes(
                    bone_interpolations[index * 64 : index * 64 + 64]
                ),
            }
            for index in range(len(bone_frames))
        ],
        "morph_frames": [
            {
                "morph_name": _indexed_name(morph_names, morph_name_indices[index]),
                "frame": morph_frames[index],
                "value": morph_weights[index],
            }
            for index in range(len(morph_frames))
        ],
        "camera_frames": [
            {
                "frame": row.get("frame", 0),
                "distance": row.get("distance", 0.0),
                "position": row.get("position", (0.0, 0.0, 0.0)),
                "rotation": row.get("rotation", (0.0, 0.0, 0.0)),
                "interpolation": bytes(row.get("interpolation", ())),
                "viewing_angle": row.get("fov", 0),
                "perspective": 0 if row.get("perspective", True) else 1,
            }
            for row in metadata.get("cameraFrames", ())
        ],
        "light_frames": [
            {
                "frame": row.get("frame", 0),
                "color": row.get("color", (0.0, 0.0, 0.0)),
                "position": row.get("direction", (0.0, 0.0, 0.0)),
            }
            for row in metadata.get("lightFrames", ())
        ],
        "shadow_frames": list(metadata.get("selfShadowFrames", ())),
        "ik_show_hide_frames": [
            {
                "frame": row.get("frame", 0),
                "visible": row.get("visible", True),
                "ik_states": [
                    (
                        _name(state, "boneName", "boneNameBytes"),
                        state.get("enabled", False),
                    )
                    for state in row.get("ikStates", ())
                ],
            }
            for row in metadata.get("propertyFrames", ())
        ],
    }
    with TemporaryDirectory(prefix="mmd-vmd-parts-oracle-") as directory:
        output = Path(directory) / "oracle.vmd"
        VmdExporter().export_vmd_animation(str(output), payload)
        result = output.read_bytes()
        # The legacy Python writer omits the optional IK count when empty,
        # while prepared-stage validation requires every section boundary.
        if not payload["ik_show_hide_frames"]:
            result += b"\x00\x00\x00\x00"
        return result


def _indexed_name(table: tuple[Any, ...], index: Any) -> str:
    return _name(table[int(index)], "name", "nameBytes")


def _name(row: Mapping[str, Any], text_key: str, bytes_key: str) -> str:
    raw = bytes(row.get(bytes_key, ()))
    if raw:
        return raw.decode("cp932")
    return str(row.get(text_key, ""))
