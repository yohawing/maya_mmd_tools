"""Check native target provenance and Python aliases after per-mesh filtering."""

from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[2]


def main(*, standalone: bool = True) -> dict:
    """Run the same import/reload checks in mayapy or an isolated GUI host."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plugin", type=Path, default=os.environ.get("MMD_TOOLS_CPP_PLUGIN"))
    args = parser.parse_args()
    if args.plugin is None:
        parser.error("--plugin or MMD_TOOLS_CPP_PLUGIN is required")
    sys.path.insert(0, str(ROOT))
    os.environ["PATH"] = str(args.plugin.parent) + os.pathsep + os.environ.get("PATH", "")
    import maya.standalone
    import maya.cmds as cmds

    if standalone:
        maya.standalone.initialize(name="python")
    try:
        cmds.loadPlugin(str(args.plugin.resolve()), quiet=True)
        from mmd_tools.core.mmd_parser import parse_pmx_file
        from mmd_tools.io.cpp_fast_importer import _apply_fast_morph_metadata

        pmx = parse_pmx_file(str(ROOT / "tests/data/test_morph_model.pmx"), use_native_pmx_parse=False)
        original = next(morph for morph in pmx.morphs if int(morph.morph_type) == 1 and morph.offsets)
        empty = copy.deepcopy(original)
        empty.name = "filtered_empty"
        empty.offsets = []
        valid = copy.deepcopy(original)
        valid.name = "surviving target"
        pmx.morphs = [empty, valid, copy.deepcopy(empty), copy.deepcopy(valid)]
        pmx.morphs[-1].name = "surviving_target"
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Path(temporary) / "filtered.pmx"
            pmx.write_file(str(fixture))
            reports = []
            for split in (False, True):
                cmds.file(new=True, force=True)
                cmds.mmdFastLoad(f=str(fixture), n="filtered", s=1.0, sp=split, mo=True)
                blends = cmds.ls(type="blendShape") or []
                assert blends, "fixture created no vertex targets"
                for blend in blends:
                    indices = list(cmds.getAttr(blend + ".mmd_source_morph_indices"))
                    assert indices == [1, 3], indices
                    geometry = cmds.blendShape(blend, query=True, geometry=True)[0]
                    _apply_fast_morph_metadata(str(fixture), geometry, cmds)
                    mapping = json.loads(cmds.getAttr(blend + ".mmd_blendshape_morph_names_json"))
                    assert mapping == {
                        "0": {"index": 1, "name": "surviving target"},
                        "1": {"index": 3, "name": "surviving_target"},
                    }, mapping
                    assert cmds.aliasAttr(blend + ".weight[0]", query=True) == "surviving_target"
                    assert cmds.aliasAttr(blend + ".weight[1]", query=True) == "surviving_target_1"
                scene = str(Path(temporary) / "filtered.ma")
                cmds.file(rename=scene)
                cmds.file(save=True, type="mayaAscii", force=True)
                cmds.file(scene, open=True, force=True)
                for blend in cmds.ls(type="blendShape"):
                    assert list(cmds.getAttr(blend + ".mmd_source_morph_indices")) == [1, 3]
                    assert json.loads(cmds.getAttr(blend + ".mmd_blendshape_morph_names_json"))["1"]["index"] == 3
                reports.append({"split": split, "blendShapes": len(blends), "indices": [1, 3], "reopen": "pass"})
            result = {"status": "pass", "cases": reports}
            print(json.dumps(result))
            return result
    finally:
        if standalone:
            maya.standalone.uninitialize()


if __name__ == "__main__":
    main()
