"""Compare Maya setKeyframe animLayer graphs with API-keyed animLayer graphs.

Run with mayapy through the ``anim_layer_graph_compare`` Nox session. The
initial harness covers joint translate/rotate channels and writes graph dumps as
diagnostics while gating on evaluated plug values.
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = ROOT / "build" / "reports" / "anim_layer_graph_compare.json"
CASES = ("joint_translate", "joint_rotate")


class _Logger:
    def debug(self, *_args, **_kwargs) -> None:
        return


def _initialize_maya() -> bool:
    import maya.standalone

    try:
        maya.standalone.initialize(name="python")
        return True
    except RuntimeError:
        return False


def _repo_imports() -> None:
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))


def _create_joint(node_name: str, base_values: dict[str, float]) -> str:
    import maya.cmds as cmds

    cmds.select(clear=True)
    node = cmds.joint(name=node_name)
    for attr, value in base_values.items():
        cmds.setAttr(f"{node}.{attr}", float(value))
    return node


def _create_layer(node: str, attrs: list[str], route: str) -> str:
    import maya.cmds as cmds

    layer = cmds.animLayer(f"{route}_compare_layer", override=False)
    for attr in attrs:
        cmds.animLayer(layer, edit=True, attribute=f"{node}.{attr}")
    return layer


def _setkeyframe_path(
    *,
    node: str,
    attrs: list[str],
    samples: dict[str, list[tuple[float, float]]],
    base_values: dict[str, float],
    route: str,
) -> str:
    import maya.cmds as cmds

    layer = _create_layer(node, attrs, route)
    for attr in attrs:
        base = float(base_values.get(attr, 0.0))
        for frame, delta in samples[attr]:
            cmds.setKeyframe(
                node,
                attribute=attr,
                time=float(frame),
                value=base + float(delta),
                animLayer=layer,
                inTangentType="linear",
                outTangentType="linear",
            )
    return layer


def _api_path(
    *,
    node: str,
    attrs: list[str],
    samples: dict[str, list[tuple[float, float]]],
    route: str,
) -> str:
    from mmd_tools.converters.vmd_context import VmdKeyingContext
    from mmd_tools.converters.vmd_scene_keying import batch_key_scalar_channels

    layer = _create_layer(node, attrs, route)
    context = VmdKeyingContext(logger=_Logger(), anim_layer=layer, use_animation_layers=True)
    if not batch_key_scalar_channels(context, node, samples, animation_layer=layer):
        raise RuntimeError(f"API keying produced no keys for {node}")
    return layer


def _case_spec(case_name: str) -> dict[str, Any]:
    if case_name == "joint_translate":
        return {
            "attrs": ["translateX", "translateY", "translateZ"],
            "base": {"translateX": 3.0, "translateY": -2.0, "translateZ": 1.5},
            "samples": {
                "translateX": [(0.0, 0.0), (5.0, 4.0), (10.0, -1.0)],
                "translateY": [(0.0, 2.0), (5.0, -3.0), (10.0, 1.0)],
                "translateZ": [(0.0, -1.5), (5.0, 0.5), (10.0, 2.5)],
            },
            "frames": [0.0, 2.5, 5.0, 7.5, 10.0],
        }
    if case_name == "joint_rotate":
        return {
            "attrs": ["rotateX", "rotateY", "rotateZ"],
            "base": {"rotateX": 10.0, "rotateY": -5.0, "rotateZ": 20.0},
            "samples": {
                "rotateX": [(0.0, 0.0), (5.0, 15.0), (10.0, -5.0)],
                "rotateY": [(0.0, 5.0), (5.0, -10.0), (10.0, 0.0)],
                "rotateZ": [(0.0, -20.0), (5.0, 10.0), (10.0, 30.0)],
            },
            "frames": [0.0, 2.5, 5.0, 7.5, 10.0],
        }
    raise ValueError(f"unknown case: {case_name}")


def _run_route(case_name: str, route: str, spec: dict[str, Any]) -> dict[str, Any]:
    import maya.cmds as cmds

    from mmd_tools.tools.anim_layer_dg_dump import dump_plug_graph, eval_plugs_at_frames, normalize_graph

    cmds.file(new=True, force=True)
    cmds.currentUnit(time="ntsc")
    attrs = list(spec["attrs"])
    node = _create_joint(f"{case_name}_{route}_joint", spec["base"])
    if route == "setkeyframe":
        layer = _setkeyframe_path(node=node, attrs=attrs, samples=spec["samples"], base_values=spec["base"], route=route)
    elif route == "api":
        layer = _api_path(node=node, attrs=attrs, samples=spec["samples"], route=route)
    else:
        raise ValueError(f"unknown route: {route}")

    plugs = [f"{node}.{attr}" for attr in attrs]
    plug_eval = eval_plugs_at_frames(plugs, spec["frames"])
    plug_graph = {attr: dump_plug_graph(f"{node}.{attr}") for attr in attrs}
    return {
        "node": node,
        "layer": layer,
        "eval": {attr: plug_eval[f"{node}.{attr}"] for attr in attrs},
        "graph": plug_graph,
        "normalizedGraph": normalize_graph(plug_graph),
    }


def run(cases: list[str], *, tolerance: float) -> dict[str, Any]:
    from mmd_tools.tools.anim_layer_dg_dump import diff_evaluations

    results: list[dict[str, Any]] = []
    for case_name in cases:
        spec = _case_spec(case_name)
        setkeyframe = _run_route(case_name, "setkeyframe", spec)
        api = _run_route(case_name, "api", spec)
        eval_diff = diff_evaluations(setkeyframe["eval"], api["eval"], tolerance=tolerance)
        graph_match = setkeyframe["normalizedGraph"] == api["normalizedGraph"]
        results.append(
            {
                "name": case_name,
                "evalMatch": eval_diff["matches"],
                "graphMatch": graph_match,
                "evalDiff": eval_diff,
                "setkeyframe": setkeyframe,
                "api": api,
            }
        )

    return {
        "status": "passed" if all(item["evalMatch"] for item in results) else "failed",
        "cases": results,
        "tolerance": tolerance,
    }


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare animLayer DG graphs for setKeyframe and API keying.")
    parser.add_argument("--case", action="append", choices=CASES, help="Case to run. May be repeated.")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="Output JSON report path.")
    parser.add_argument("--tolerance", type=float, default=1.0e-5, help="Evaluation comparison tolerance.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(list(argv or sys.argv[1:]))
    _repo_imports()
    initialized = _initialize_maya()
    try:
        payload = run(args.case or list(CASES), tolerance=args.tolerance)
    except Exception as exc:
        payload = {
            "status": "failed",
            "error": str(exc),
            "traceback": traceback.format_exc(),
        }
    finally:
        if initialized:
            import maya.standalone

            maya.standalone.uninitialize()

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"status": payload["status"], "out": str(out_path)}, ensure_ascii=False, sort_keys=True))
    return 0 if payload["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
