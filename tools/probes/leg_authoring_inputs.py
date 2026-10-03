"""Measure existing pre-IK thigh inputs, with and without a bone morph.

This is a design experiment on a disposable TestModel variant, not an editing
tool or a claim that ordinary joint manipulators can author these inputs.
"""

import argparse
import json
import math
import os
from pathlib import Path
import sys
import traceback

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
MARKER = "LEG AUTHORING INPUTS FINISHED"


def probe(output, plugin):
    from maya import cmds
    from mmd_tools.core.pmx_data import PmxData
    from mmd_tools.core.pmx_data.morph import PmxMorph, PmxMorphType
    from mmd_tools.converters.bone_morph_runtime import _destination_upstream_of_append, ACCUM_NODE_TYPE
    from mmd_tools.io.mmd_importer import import_mmd_file

    out = Path(output)
    report = {"status": "fail", "cases": [], "scope": "controlled TestModel design experiment",
              "statusScope": "measurement collection only; not product editing acceptance"}
    try:
        cmds.loadPlugin(plugin, quiet=True)
        cmds.loadPlugin(str(ROOT / "plug-ins/mmd_tools_plugin.py"), quiet=True)
        fixture = PmxData().parse_file(str(ROOT / "tests/data/yw_test_model.pmx"))
        roles = {role: next(i for i, bone in enumerate(fixture.bones) if bone.name in names)
                 for role, names in {"thigh": ("左足",), "knee": ("左ひざ",),
                                     "ankle": ("左足首",), "controller": ("左足ＩＫ", "左足IK")}.items()}
        header = fixture.header
        morph = PmxMorph(header.vertex_index_size, header.material_index_size, header.bone_index_size,
                         header.morph_index_size, header.rigid_body_index_size, header.encoding)
        morph.name = morph.name_english = "LegProbe"
        morph.morph_type = PmxMorphType.BoneMorph
        morph.panel = 4
        morph.offsets = [{"bone_index": roles["thigh"], "translation": (0, 0, 0),
                          "rotation": (0, math.sin(math.radians(10) / 2), 0, math.cos(math.radians(10) / 2))}]
        fixture.morphs = [morph]
        fixture_path = out / "leg-inputs.pmx"
        fixture.write_file(str(fixture_path))
        for use_morph in (False, True):
            cmds.file(new=True, force=True)
            root = import_mmd_file(str(fixture_path), options={
                "import_physics": False, "import_morphs": use_morph, "setup_rig": True,
                "create_mmd_control_rig": False,
            })
            assert root
            indexed = {int(cmds.getAttr(j + ".mmd_bone_index")): j
                       for j in cmds.listRelatives(root, ad=True, type="joint", fullPath=True) or []
                       if cmds.attributeQuery("mmd_bone_index", node=j, exists=True)}
            joints = {role: indexed[index] for role, index in roles.items()}
            # Bend the leg first; a straight knee has no stable bend direction.
            cmds.move(0, .5, .5, joints["controller"], relative=True, objectSpace=True)
            destination = _destination_upstream_of_append(joints["thigh"], "rotate")
            incoming = cmds.listConnections(destination, source=True, destination=False) or []
            accumulators = [node for node in incoming if cmds.nodeType(node) == ACCUM_NODE_TYPE]
            editable = accumulators[0] + ".baseRotate" if accumulators else destination
            assert not cmds.listConnections(editable, source=True, destination=False), editable
            original = cmds.getAttr(editable)[0]
            weights = (0, 1) if use_morph else (0,)
            weight_plug = None
            morph_nodes = [n for n in cmds.ls(type="network")
                           if cmds.attributeQuery("mmd_morph_name", node=n, exists=True)
                           and cmds.getAttr(n + ".mmd_morph_name") == "LegProbe"]
            assert len(morph_nodes) == int(use_morph), morph_nodes
            if use_morph:
                morph_node, = morph_nodes
                weight_plug = morph_node + ".weight"
                drivers = cmds.listConnections(weight_plug, source=True, destination=False, plugs=True) or []
                if drivers:
                    weight_plug, = drivers
                assert cmds.getAttr(weight_plug, settable=True), weight_plug
            for weight in weights:
                if weight_plug:
                    cmds.setAttr(weight_plug, weight)
                cmds.setAttr(editable, *original, type="double3")

                def positions():
                    return {role: cmds.xform(joint, query=True, worldSpace=True, translation=True)
                            for role, joint in joints.items()}

                baseline = positions()
                samples = []
                for axis in range(3):
                    for angle in (-15, 15):
                        values = list(original)
                        values[axis] += angle
                        cmds.setAttr(editable, *values, type="double3")
                        posed = positions()
                        assert all(math.isfinite(v) for xyz in posed.values() for v in xyz)
                        samples.append({"axis": "XYZ"[axis], "angle": angle, "positions": posed,
                            "kneeDisplacement": math.dist(posed["knee"], baseline["knee"]),
                            "ankleDisplacement": math.dist(posed["ankle"], baseline["ankle"])})
                cmds.setAttr(editable, *original, type="double3")
                report["cases"].append({"morphImported": bool(morph_nodes), "morphNodes": morph_nodes, "weight": weight,
                    "editable": editable, "thighRotateSettable": cmds.getAttr(joints["thigh"] + ".rotate", settable=True),
                    "jointOrient": cmds.getAttr(joints["thigh"] + ".jointOrient"),
                    "offsetParentMatrix": cmds.getAttr(joints["thigh"] + ".offsetParentMatrix"),
                    "baseline": baseline, "samples": samples,
                    "restoreMaxDistance": max(math.dist(xyz, baseline[role]) for role, xyz in positions().items()),
                    "cycles": cmds.cycleCheck(all=True, list=True) or []})
            cmds.file(rename=str(out / ("with-morph.ma" if use_morph else "without-morph.ma")))
            cmds.file(save=True, type="mayaAscii", force=True)
        report["collectionChecks"] = {
            "expectedCases": len(report["cases"]) == 3 and all(len(row["samples"]) == 6 for row in report["cases"]),
            "finiteMeasurements": all(math.isfinite(row["restoreMaxDistance"])
                                      and all(math.isfinite(sample[key]) for sample in row["samples"]
                                              for key in ("kneeDisplacement", "ankleDisplacement"))
                                      for row in report["cases"]),
            "noCycles": all(not row["cycles"] for row in report["cases"]),
        }
        report["status"] = "pass" if all(report["collectionChecks"].values()) else "fail"
        report["limitations"] = ["ordinary joint manipulation not implemented", "one synthetic morph",
                                 "VMD, append variants and export not tested", "OPM comparison not implemented"]
    except Exception:
        report["error"] = traceback.format_exc()
    finally:
        (out / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        (out / "probe.log").write_text(MARKER, encoding="utf-8")


def main():
    from tests.viewport.maya_e2e_harness import run_maya_e2e

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--maya", default="2024", choices=("2024", "2026"))
    parser.add_argument("--port", type=int, default=7806)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    out = args.out_dir.resolve()
    if not out.is_relative_to(ROOT / "build"):
        parser.error("output must be inside build")
    plugin = ROOT / f"plug-ins/{args.maya}/Release/mmd_tools_cpp.mll"
    result = run_maya_e2e(
        project_root=ROOT, version=args.maya, out_dir=out, port=args.port, timeout=600,
        log_path=out / "probe.log", report_path=out / "report.json", marker=MARKER,
        command=("from maya import cmds\nfrom tools.probes.leg_authoring_inputs import probe\n"
                 f"cmds.evalDeferred(lambda: probe({str(out)!r}, {str(plugin)!r}), lowestPriority=True)"),
        send_label="leg-authoring-inputs", stale_paths=(out / "probe.log", out / "report.json"),
        env_overrides={"MAYA_SKIP_USERSETUP_PY": "1", "MAYA_VP2_DEVICE_OVERRIDE": "VirtualDeviceDx11",
                       "MMD_TOOLS_CPP_PLUGIN": str(plugin),
                       "PATH": str(plugin.parent) + os.pathsep + os.environ.get("PATH", "")},
    )
    print(json.dumps({"status": result["status"], "error": result.get("error")}))
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
