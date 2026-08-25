"""Contracts for the headless typed-parts VMD oracle."""

from array import array
from pathlib import Path
from tempfile import TemporaryDirectory

from mmd_tools.core.vmd_data import VmdData
from tests.common.vmd_parts_export_oracle import export_vmd_from_parts_oracle


def test_oracle_serializes_typed_bone_and_morph_parts() -> None:
    interpolation = bytes(range(64))
    payload = export_vmd_from_parts_oracle(
        {
            "modelName": "モデル",
            "modelNameBytes": list("モデル".encode("cp932")),
            "boneNames": [
                {"name": "センター", "nameBytes": list("センター".encode("cp932"))}
            ],
            "morphNames": [
                {"name": "笑い", "nameBytes": list("笑い".encode("cp932"))}
            ],
        },
        array("I", [0]),
        array("I", [4]),
        array("f", [1.0, 2.0, 3.0]),
        array("f", [0.0, 0.0, 0.0, 1.0]),
        array("B", interpolation),
        array("I", [0]),
        array("I", [8]),
        array("f", [0.25]),
    )

    with TemporaryDirectory(prefix="mmd-vmd-oracle-test-") as directory:
        output = Path(directory) / "oracle.vmd"
        output.write_bytes(payload)
        parsed = VmdData().parse_file(str(output))

    assert parsed.header.model_name == "モデル"
    assert parsed.bone_frames[0].bone_name == "センター"
    assert parsed.bone_frames[0].frame_number == 4
    assert parsed.bone_frames[0].interpolation == interpolation
    assert parsed.morph_frames[0].morph_name == "笑い"
    assert parsed.morph_frames[0].frame_number == 8
