"""Fail-closed real-asset Control Rig bake parity matrix.

This module has two deliberately small entry points.  The host process selects
exactly five PMX/VMD pairs and launches one fresh ``mayapy`` child for each
pair and Maya 2024/2026.  The child owns all Maya state and writes one JSON
report.  A report is accepted only when the imported VMD, both bake directions,
curve/key identity, save/reopen, and VMD export/fresh import gates are green.

Examples::

    python -m tests.viewport.real_asset_bake_rig_parity --dry-run
    python -m tests.viewport.real_asset_bake_rig_parity \
        --manifest F:/MMD/parity-manifest.json --out build/reports/real-asset --resume
    mayapy -m tests.viewport.real_asset_bake_rig_parity --child \
        --maya 2026 --pair-config build/reports/pair-input.json \
        --out build/reports/real-asset_child.json

The harness is local-only: assets are never copied or modified and generated
reports are expected under ``build/``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import subprocess
import traceback
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ASSET_ROOT = Path("F:/MMD")
REQUIRED_VERSIONS = ("2024", "2026")
PAIR_COUNT = 5
MODULE_NAME = "tests.viewport.real_asset_bake_rig_parity"
REPORT_KIND = "mmd-control-rig-real-asset-bake-parity"
MATRIX_EPSILON = 5.0e-3
CHANGE_EPSILON = 1.0e-5
BONE_CATEGORIES = ("fk", "arm", "leg", "hand", "twist", "ik", "append")


def _resolve_path(value: str | os.PathLike[str], *, base: Path | None = None) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute() and base is not None:
        path = base / path
    return path.resolve()


def _pair_row(name: str, pmx: Path, vmd: Path) -> dict[str, str]:
    return {"name": str(name), "pmx": str(pmx.resolve()), "vmd": str(vmd.resolve())}


def _validate_pair_rows(rows: Iterable[Mapping[str, Any]], *, count: int = PAIR_COUNT) -> list[dict[str, str]]:
    """Validate an explicit pair list and enforce uniqueness/count fail-closed."""

    result: list[dict[str, str]] = []
    seen: set[tuple[Path, Path]] = set()
    names: set[str] = set()
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise ValueError(f"pair {index} is not an object")
        name = str(row.get("name") or f"pair-{index + 1}")
        pmx_value = row.get("pmx") or row.get("model")
        vmd_value = row.get("vmd") or row.get("motion")
        if not pmx_value or not vmd_value:
            raise ValueError(f"pair {name!r} must include pmx and vmd")
        pmx = _resolve_path(str(pmx_value))
        vmd = _resolve_path(str(vmd_value))
        if pmx.suffix.lower() != ".pmx" or vmd.suffix.lower() != ".vmd":
            raise ValueError(f"pair {name!r} has invalid PMX/VMD suffix")
        if not pmx.is_file() or not vmd.is_file():
            raise FileNotFoundError(f"pair {name!r} asset is missing: {pmx}, {vmd}")
        key = (pmx, vmd)
        if key in seen:
            raise ValueError(f"duplicate PMX/VMD pair: {pmx} + {vmd}")
        if name in names:
            raise ValueError(f"duplicate pair name: {name}")
        seen.add(key)
        names.add(name)
        result.append(_pair_row(name, pmx, vmd))
    if len(result) != count:
        raise ValueError(f"exactly {count} PMX/VMD pairs are required; got {len(result)}")
    return result


def load_pair_manifest(path: Path, *, count: int = PAIR_COUNT) -> list[dict[str, str]]:
    """Read a JSON list or ``{"pairs": [...]}`` manifest."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("pairs") if isinstance(payload, Mapping) else payload
    if not isinstance(rows, list):
        raise ValueError("manifest must be a JSON list or an object containing pairs")
    resolved: list[dict[str, Any]] = []
    for row in rows:
        if isinstance(row, Mapping):
            resolved.append(
                {
                    **row,
                    "pmx": str(_resolve_path(str(row.get("pmx") or row.get("model")), base=path.parent)),
                    "vmd": str(_resolve_path(str(row.get("vmd") or row.get("motion")), base=path.parent)),
                }
            )
        else:
            resolved.append(row)
    return _validate_pair_rows(resolved, count=count)


def _same_stem_candidates(pmx_files: Sequence[Path], vmd_files: Sequence[Path]) -> list[dict[str, str]]:
    """Find conservative same-stem pairs; never guess across unrelated folders."""

    by_stem: dict[str, list[Path]] = {}
    for vmd in vmd_files:
        by_stem.setdefault(vmd.stem.casefold(), []).append(vmd)
    pairs = []
    for pmx in sorted(pmx_files, key=lambda item: str(item).casefold()):
        candidates = sorted(by_stem.get(pmx.stem.casefold(), []), key=lambda item: str(item).casefold())
        if len(candidates) != 1:
            continue
        pairs.append(_pair_row(pmx.stem, pmx, candidates[0]))
    return pairs


_NON_BODY_TOKENS = (
    "camera",
    "light",
    "lip",
    "face",
    "morph",
    "表情",
    "カメラ",
    "照明",
    "リップ",
    "口パク",
)
_PROP_TOKENS = (
    "stage",
    "ステージ",
    "camera",
    "weapon",
    "sword",
    "gun",
    "box",
    "chair",
    "背景",
    "小物",
    "武器",
    "弓",
    "注射",
    "泡泡枪",
    "药丸",
    "子弹",
    "剑",
    "枪",
    "花",
    "球",
    "猫",
    "章鱼",
    "寄居蟹",
    "结晶虫",
    "笔记",
)


def _default_character_pairs(asset_root: Path, *, count: int) -> list[dict[str, str]]:
    """Select character PMX and body-motion VMD files when names do not match.

    F:/MMD stores models and motion packs in separate trees, so exact-stem
    matching is insufficient.  Restrict models to ``pmx/`` (never background
    trees), reject obvious props, reject camera/lip/face-only motions, then
    choose a deterministic hash-ordered one-to-one sample.  A manifest remains
    the authority for intentional model/motion compatibility choices.
    """

    pmx_root = asset_root / "pmx"
    vmd_root = asset_root / "vmd"
    pmx_files = [
        path
        for path in pmx_root.rglob("*.pmx")
        if path.is_file() and not any(token in path.stem.casefold() for token in _PROP_TOKENS)
    ]
    vmd_files = [
        path
        for path in vmd_root.rglob("*.vmd")
        if path.is_file() and not any(token.casefold() in str(path.relative_to(vmd_root)).casefold() for token in _NON_BODY_TOKENS)
    ]
    if len(pmx_files) < count or len(vmd_files) < count:
        raise ValueError(
            f"character/body discovery needs {count} candidates; found {len(pmx_files)} PMX and {len(vmd_files)} VMD"
        )
    pmx_files.sort(key=lambda path: hashlib.sha256(str(path).encode("utf-8")).hexdigest())
    vmd_files.sort(key=lambda path: hashlib.sha256(str(path).encode("utf-8")).hexdigest())
    return [
        _pair_row(f"character-{index + 1}-{pmx.stem}", pmx, vmd)
        for index, (pmx, vmd) in enumerate(zip(pmx_files[:count], vmd_files[:count]))
    ]


def discover_asset_pairs(asset_root: Path, *, count: int = PAIR_COUNT) -> list[dict[str, str]]:
    """Discover deterministic non-duplicate exact-stem pairs from ``F:/MMD``.

    Motion bundles commonly contain camera/lip/face clips that must not be
    guessed as model animation.  Therefore only unique exact stem matches are
    selected; callers can use a manifest for intentional cross-name pairing.
    """

    root = asset_root.resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"asset root does not exist: {root}")
    pmx_files = [path for path in root.rglob("*.pmx") if path.is_file()]
    vmd_files = [path for path in root.rglob("*.vmd") if path.is_file()]
    candidates = _same_stem_candidates(pmx_files, vmd_files)
    # Stable hash ordering gives a repeatable random-looking sample while
    # avoiding process-dependent set/directory ordering.
    candidates.sort(
        key=lambda row: hashlib.sha256(f"{row['pmx']}\0{row['vmd']}".encode("utf-8")).hexdigest()
    )
    if len(candidates) < count:
        # Separate model/motion trees are the normal F:/MMD layout.  The
        # fallback remains conservative (character PMX + body VMD filters).
        return _validate_pair_rows(_default_character_pairs(root, count=count), count=count)
    # A model bundle can legitimately contain the same stem in two folders.
    # Keep report filenames collision-free while retaining readable labels.
    selected = candidates[:count]
    counts: dict[str, int] = {}
    for row in selected:
        counts[row["name"]] = counts.get(row["name"], 0) + 1
    for row in selected:
        if counts[row["name"]] > 1:
            digest = hashlib.sha256(f"{row['pmx']}\0{row['vmd']}".encode("utf-8")).hexdigest()[:8]
            row["name"] = f"{row['name']}-{digest}"
    return _validate_pair_rows(selected, count=count)


def _read_json(path: Path) -> Mapping[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError(f"report is not a JSON object: {path}")
    return payload


def validate_child_report(
    payload: Mapping[str, Any],
    *,
    pair: Mapping[str, str],
    version: str,
) -> list[str]:
    """Return actionable validation errors; an empty list is the only green result."""

    errors: list[str] = []
    if payload.get("kind") != REPORT_KIND:
        errors.append(f"kind mismatch: {payload.get('kind')!r}")
    if payload.get("status") != "pass":
        errors.append(f"child status is not pass: {payload.get('status')!r}")
    if str(payload.get("mayaVersion", "")).split(".", 1)[0] != str(version):
        errors.append(f"Maya version mismatch: expected {version}, got {payload.get('mayaVersion')!r}")
    for key in ("pmx", "vmd"):
        if _resolve_path(str(payload.get(key, ""))) != _resolve_path(str(pair[key])):
            errors.append(f"{key} path mismatch")
    frames = payload.get("frames")
    if not isinstance(frames, list) or 0 not in [int(item) for item in frames if str(item).lstrip("-").isdigit()] or not any(int(item) > 0 for item in frames if str(item).lstrip("-").isdigit()):
        errors.append("frames must include 0 and a non-zero representative frame")
    required_gates = (
        "preImportedVmd",
        "setupBoundary",
        "controlRigBake",
        "bakeBack",
        "curveIdentity",
        "persistence",
        "exportFreshImport",
    )
    for key in required_gates:
        value = payload.get(key)
        if not isinstance(value, Mapping) or value.get("pass") is not True:
            errors.append(f"required gate {key}.pass is not true")
    coverage = payload.get("boneCoverage")
    if not isinstance(coverage, Mapping) or int(coverage.get("compared", 0)) <= 0 or coverage.get("pass") is not True:
        errors.append("boneCoverage must have compared > 0 and pass=true")
    if payload.get("errors"):
        errors.append("child report contains errors")
    return errors


def _flatten(value: Any) -> list[float]:
    if value is None or isinstance(value, (str, bytes)):
        return []
    if isinstance(value, Iterable):
        result: list[float] = []
        for item in value:
            result.extend(_flatten(item))
        return result
    try:
        return [float(value)]
    except (TypeError, ValueError):
        return []


def _matrix_error(left: Iterable[float], right: Iterable[float]) -> float:
    a, b = list(left), list(right)
    if len(a) != len(b):
        return float("inf")
    return max((abs(float(x) - float(y)) for x, y in zip(a, b)), default=0.0)


def _capture_worlds(cmds: Any, root: str, frames: Sequence[int]) -> dict[str, dict[str, list[float]]]:
    indexed: dict[str, str] = {}
    for joint in cmds.listRelatives(root, allDescendents=True, type="joint", fullPath=True) or []:
        try:
            if cmds.attributeQuery("mmd_bone_index", node=joint, exists=True):
                indexed[str(int(cmds.getAttr(f"{joint}.mmd_bone_index")))] = str(joint)
        except (TypeError, ValueError, RuntimeError):
            continue
    captured: dict[str, dict[str, list[float]]] = {}
    for frame in frames:
        cmds.currentTime(int(frame), edit=True)
        cmds.refresh(force=True)
        captured[str(frame)] = {
            index: _flatten(cmds.xform(joint, query=True, worldSpace=True, matrix=True))
            for index, joint in sorted(indexed.items())
            if cmds.objExists(joint)
        }
    return captured


def _world_delta(left: Mapping[str, Mapping[str, Sequence[float]]], right: Mapping[str, Mapping[str, Sequence[float]]]) -> float:
    return max(
        (
            _matrix_error(matrix, right.get(frame, {}).get(index, []))
            for frame, rows in left.items()
            for index, matrix in rows.items()
        ),
        default=float("inf"),
    )


def _world_delta_rows(
    left: Mapping[str, Mapping[str, Sequence[float]]],
    right: Mapping[str, Mapping[str, Sequence[float]]],
) -> list[dict[str, Any]]:
    """Return sorted per-bone setup deltas for a fail-closed diagnostic."""

    rows = [
        {
            "frame": int(frame),
            "boneIndex": str(index),
            "error": _matrix_error(matrix, right.get(frame, {}).get(index, [])),
        }
        for frame, values in left.items()
        for index, matrix in values.items()
    ]
    return sorted(rows, key=lambda row: float(row["error"]), reverse=True)


def _curve_inventory(cmds: Any) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for node in cmds.ls(type="animCurve", long=True) or []:
        try:
            uuid = str((cmds.ls(node, uuid=True) or [""])[0])
            times = [float(value) for value in _flatten(cmds.keyframe(node, query=True, timeChange=True) or [])]
            values = [float(value) for value in _flatten(cmds.keyframe(node, query=True, valueChange=True) or [])]
            destinations = [str(value) for value in (cmds.listConnections(node, source=False, destination=True, plugs=True) or [])]
            try:
                interpolation = str(cmds.rotationInterpolation(node, query=True) or "none")
            except RuntimeError:
                interpolation = "none"
        except (RuntimeError, TypeError, ValueError):
            continue
        if uuid:
            result[uuid] = {
                "node": str(node),
                "times": times,
                "values": values,
                "destinations": destinations,
                "rotationInterpolation": interpolation,
            }
    return result


def _bone_coverage(cmds: Any, root: str, worlds: Mapping[str, Mapping[str, Sequence[float]]], frames: Sequence[int]) -> dict[str, Any]:
    """Classify categories present in one asset without requiring all of them."""

    names: list[str] = []
    for joint in cmds.listRelatives(root, allDescendents=True, type="joint", fullPath=True) or []:
        try:
            if cmds.attributeQuery("mmd_bone_index", node=joint, exists=True):
                names.append(str(cmds.getAttr(f"{joint}.mmd_bone_name") or ""))
        except (RuntimeError, TypeError):
            continue
    lowered = [name.casefold() for name in names]
    categories = {
        "fk": bool(names),
        "arm": any(any(token in name for token in ("arm", "腕", "ひじ", "肘", "shoulder", "肩", "wrist", "手首")) for name in lowered),
        "leg": any(any(token in name for token in ("leg", "足", "脚", "ひざ", "膝", "knee", "ankle", "足首")) for name in lowered),
        "hand": any(any(token in name for token in ("hand", "手", "指", "finger", "thumb")) for name in lowered),
        "twist": any(any(token in name for token in ("twist", "捩")) for name in lowered),
        "ik": bool(cmds.ls(type="mmdCcdIk", long=True) or []),
        "append": bool(cmds.ls(type="mmdAppend", long=True) or []),
    }
    compared = sum(len(rows) for rows in worlds.values())
    return {
        "compared": compared,
        "frames": list(frames),
        "categories": categories,
        "complete": all(categories.values()),
        "pass": compared > 0,
    }


def _aggregate_bone_coverage(payloads: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Require the five-pair matrix, rather than every model, to cover all roles."""

    categories = {
        category: any(
            bool((payload.get("boneCoverage", {}).get("categories") or {}).get(category))
            for payload in payloads
        )
        for category in BONE_CATEGORIES
    }
    compared = sum(
        int(payload.get("boneCoverage", {}).get("compared", 0) or 0)
        for payload in payloads
    )
    return {
        "childCount": len(payloads),
        "compared": compared,
        "categories": categories,
        "pass": len(payloads) == PAIR_COUNT * len(REQUIRED_VERSIONS)
        and compared > 0
        and all(categories.values()),
    }


def _first_probe_frame(vmd: Any) -> int:
    values = sorted(
        {
            int(float(frame.frame_number))
            for frame in getattr(vmd, "bone_frames", []) or []
            if float(frame.frame_number).is_integer() and int(frame.frame_number) > 0
        }
    )
    if not values:
        raise RuntimeError("VMD has no non-zero integer bone frame")
    return values[0]


def _load_plugins(cmds: Any, maya_major: str) -> dict[str, str]:
    cpp_override = os.environ.get(f"MMD_TOOLS_CPP_PLUGIN_{maya_major}") or os.environ.get("MMD_TOOLS_CPP_PLUGIN")
    cpp = Path(cpp_override).expanduser().resolve() if cpp_override else ROOT / "plug-ins" / maya_major / "Debug" / "mmd_tools_cpp.mll"
    if not cpp.is_file():
        raise RuntimeError(f"required native plugin is missing: {cpp}")
    if str(cpp.parent) not in os.environ.get("PATH", "").split(os.pathsep):
        os.environ["PATH"] = str(cpp.parent) + os.pathsep + os.environ.get("PATH", "")
    if not cmds.pluginInfo(str(cpp), query=True, loaded=True):
        cmds.loadPlugin(str(cpp), quiet=True)
    py_plugin = ROOT / "mmd_tools" / "plugin_main.py"
    if not cmds.pluginInfo(py_plugin.stem, query=True, loaded=True):
        cmds.loadPlugin(str(py_plugin), quiet=True)
    return {"native": str(cpp), "python": str(py_plugin)}


def _run_child(args: argparse.Namespace) -> int:
    """Run one pair in a fresh mayapy standalone process."""

    output = Path(args.out).resolve()
    report: dict[str, Any] = {
        "kind": REPORT_KIND,
        "status": "fail",
        "mayaVersion": None,
        "pmx": str(Path(args.pmx).resolve()),
        "vmd": str(Path(args.vmd).resolve()),
        "pairName": str(args.pair_name),
        "frames": [],
        "errors": [],
    }
    try:
        import maya.cmds as cmds
        from mmd_tools.core.mmd_control_rig_builder import (
            CONTROL_RIG_ATTACHED,
            CONTROL_RIG_BAKED,
            CONTROL_RIG_EDIT,
            build_mmd_control_rig,
            read_mmd_control_rig_metadata,
            resolve_mmd_control_rig_binding_joint,
        )
        from mmd_tools.core.mmd_control_rig_motion import bake_mmd_control_rig, enter_mmd_control_rig_edit
        from mmd_tools.core.vmd_data import VmdData
        from mmd_tools.converters.vmd_scene_collector import VmdSceneCollector
        from mmd_tools.io.mmd_importer import import_mmd_file
        from mmd_tools.io.vmd_exporter import VmdExporter

        report["mayaVersion"] = str(cmds.about(version=True))
        major = report["mayaVersion"].split(".", 1)[0]
        report["plugins"] = _load_plugins(cmds, major)
        source_vmd = VmdData().parse_file(str(Path(args.vmd).resolve()))
        nonzero = _first_probe_frame(source_vmd)
        frames = [0, nonzero]
        report["frames"] = frames
        if len(source_vmd.bone_frames) <= 0:
            raise RuntimeError("VMD contains no bone frames")

        cmds.file(new=True, force=True)
        root = import_mmd_file(
            str(Path(args.pmx).resolve()),
            options={
                "setup_rig": True,
                "setup_bone_orientation": True,
                "import_physics": False,
                "create_mmd_shaders": False,
            },
        )
        if not root:
            raise RuntimeError("PMX import returned no model root")
        root = str(root)
        if not import_mmd_file(
            str(Path(args.vmd).resolve()),
            options={"target_model": root, "pmx_path": str(Path(args.pmx).resolve()), "bake_mode": False},
        ):
            raise RuntimeError("pre-imported VMD route returned no result")
        baseline = _capture_worlds(cmds, root, frames)
        report["preImportedVmd"] = {"importStatus": "pass", "boneFrames": len(source_vmd.bone_frames), "pass": True}

        setup_rows: list[dict[str, Any]] = []
        # Execute the setup transition in two independent fresh scenes.  This
        # keeps the frame-0 and non-zero setup boundaries real operations rather
        # than merely sampling a frame-0 transition at two times.
        for setup_frame in (0, nonzero):
            if setup_frame != 0:
                cmds.file(new=True, force=True)
                root = import_mmd_file(
                    str(Path(args.pmx).resolve()),
                    options={
                        "setup_rig": True,
                        "setup_bone_orientation": True,
                        "import_physics": False,
                        "create_mmd_shaders": False,
                    },
                )
                if not root or not import_mmd_file(
                    str(Path(args.vmd).resolve()),
                    options={"target_model": str(root), "pmx_path": str(Path(args.pmx).resolve()), "bake_mode": False},
                ):
                    raise RuntimeError(f"re-import failed for setup frame {setup_frame}")
                root = str(root)
                baseline = _capture_worlds(cmds, root, frames)
            cmds.currentTime(setup_frame, edit=True)
            rig = build_mmd_control_rig(root)
            if rig.state != CONTROL_RIG_ATTACHED:
                raise RuntimeError(f"Control Rig setup did not produce ATTACHED at {setup_frame}: {rig.state}")
            after_build = _capture_worlds(cmds, root, frames)
            build_error = _world_delta(baseline, after_build)
            metadata_before = read_mmd_control_rig_metadata(root)
            edited = enter_mmd_control_rig_edit(root)
            if edited.get("state") != CONTROL_RIG_EDIT:
                raise RuntimeError(f"Control Rig setup did not produce EDIT at {setup_frame}: {edited.get('state')}")
            after_setup = _capture_worlds(cmds, root, frames)
            setup_error = _world_delta(baseline, after_setup)
            edit_error = _world_delta(after_build, after_setup)
            setup_rows_detail = _world_delta_rows(baseline, after_setup)
            build_rows_detail = _world_delta_rows(baseline, after_build)
            edit_rows_detail = _world_delta_rows(after_build, after_setup)
            setup_rows.append(
                {
                    "enteredAtFrame": setup_frame,
                    "verifiedFrames": frames,
                    "maxWorldMatrixError": setup_error,
                    "metadataPresent": metadata_before is not None,
                    "buildBoundary": {
                        "maxWorldMatrixError": build_error,
                        "largestBoneDeltas": build_rows_detail[:20],
                    },
                    "editBoundary": {
                        "maxWorldMatrixError": edit_error,
                        "largestBoneDeltas": edit_rows_detail[:20],
                    },
                    "largestBoneDeltas": setup_rows_detail[:20],
                    "pass": math.isfinite(setup_error) and setup_error <= MATRIX_EPSILON,
                }
            )
            report["setupBoundary"] = {
                "transitions": setup_rows,
                "pass": all(row["pass"] for row in setup_rows),
            }
            if not setup_rows[-1]["pass"]:
                raise RuntimeError(f"setup boundary changed existing motion at {setup_frame}: {setup_error}")
        report["setupBoundary"] = {
            "transitions": setup_rows,
            "pass": all(row["pass"] for row in setup_rows),
        }

        metadata = read_mmd_control_rig_metadata(root) or {}
        controls = dict(rig.controls)
        changed = None
        # Prefer direct FK/arm channels before solver-owned IK channels.  This
        # proves authored Control keys without weakening IK evidence below.
        for role in ("center", "upper_body", "left_arm", "right_arm", "left_foot_ik"):
            control = controls.get(role)
            binding = (metadata.get("bindings") or {}).get(role)
            if not control or not isinstance(binding, Mapping):
                continue
            try:
                joint = resolve_mmd_control_rig_binding_joint(cmds, binding)
            except Exception:
                continue
            for attr in ("rotateX", "translateX", "rotateY"):
                try:
                    if cmds.getAttr(f"{control}.{attr}", lock=True):
                        continue
                    cmds.currentTime(nonzero, edit=True)
                    before_matrix = _flatten(cmds.xform(joint, query=True, worldSpace=True, matrix=True))
                    source = cmds.listConnections(f"{control}.{attr}", source=True, destination=False, plugs=True) or []
                    current = float(cmds.getAttr(f"{control}.{attr}"))
                    target_value = current + (0.1 if attr.startswith("rotate") else 0.25)
                    if source and str(source[0]).split(".", 1)[0].startswith("animCurve"):
                        source_node = str(source[0]).split(".", 1)[0]
                        cmds.setKeyframe(source_node, time=nonzero, value=target_value)
                        key_node = source_node
                    else:
                        cmds.setKeyframe(control, attribute=attr, time=nonzero, value=target_value)
                        keyed_source = cmds.listConnections(
                            f"{control}.{attr}", source=True, destination=False, plugs=True
                        ) or []
                        key_node = (
                            str(keyed_source[0]).split(".", 1)[0]
                            if keyed_source
                            else str(control)
                        )
                    cmds.dgdirty(allPlugs=True)
                    cmds.refresh(force=True)
                    after_matrix = _flatten(cmds.xform(joint, query=True, worldSpace=True, matrix=True))
                    delta = _matrix_error(before_matrix, after_matrix)
                    if delta > CHANGE_EPSILON:
                        changed = {
                            "role": role,
                            "control": str(control),
                            "joint": str(joint),
                            "attribute": attr,
                            "frame": nonzero,
                            "keyNode": key_node,
                            "worldMatrixDelta": delta,
                            "pass": True,
                        }
                        break
                except (RuntimeError, TypeError, ValueError):
                    continue
            if changed:
                break
        if changed is None:
            raise RuntimeError("no Control Rig authored key produced a target-joint change")

        before_bake = _capture_worlds(cmds, root, frames)
        curve_before = _curve_inventory(cmds)
        baked = bake_mmd_control_rig(root)
        if baked.get("state") != CONTROL_RIG_BAKED:
            raise RuntimeError(f"bake back did not produce BAKED: {baked.get('state')}")
        after_bake = _capture_worlds(cmds, root, frames)
        propagation_error = _world_delta(before_bake, after_bake)
        changed_after = _matrix_error(
            before_bake.get(str(nonzero), {}).get(next(iter(before_bake.get(str(nonzero), {})), ""), []),
            after_bake.get(str(nonzero), {}).get(next(iter(after_bake.get(str(nonzero), {})), ""), []),
        )
        curve_after = _curve_inventory(cmds)
        common_uuids = sorted(set(curve_before) & set(curve_after))
        relevant_before = {
            uuid: row
            for uuid, row in curve_before.items()
            if row.get("destinations") and row.get("times")
        }
        preserved_times = [
            uuid
            for uuid, row in relevant_before.items()
            if uuid in curve_after
            and row.get("times") == curve_after[uuid].get("times")
        ]
        changed_key_curve = str(changed.get("keyNode")) if changed else ""
        changed_key_uuid = next(
            (uuid for uuid, row in curve_after.items() if row.get("node") == changed_key_curve),
            None,
        )
        changed_key_time_pass = bool(
            changed_key_uuid
            and any(abs(float(time) - nonzero) <= 1.0e-6 for time in curve_after[changed_key_uuid].get("times", []))
        )
        source_interpolation_count = sum(
            len(getattr(frame, "interpolation", b"")) == 64
            for frame in source_vmd.bone_frames
        )
        report["controlRigBake"] = {
            "setupFrames": frames,
            "changedControlKey": changed,
            "pass": True,
        }
        report["bakeBack"] = {
            "state": baked.get("state"),
            "maxWorldMatrixError": propagation_error,
            "changedFrameWorldMatrixDelta": changed_after,
            "pass": math.isfinite(propagation_error) and propagation_error <= MATRIX_EPSILON,
        }
        quaternion_observed = any(
            str(row.get("rotationInterpolation")) == "quaternionSlerp"
            for row in curve_after.values()
        )
        report["curveIdentity"] = {
            "beforeCount": len(curve_before),
            "afterCount": len(curve_after),
            "commonUuidCount": len(common_uuids),
            "commonUuids": common_uuids[:20],
            "relevantBeforeCount": len(relevant_before),
            "preservedKeyTimeCount": len(preserved_times),
            "preservedKeyTimes": preserved_times[:20],
            "changedKeyCurve": changed_key_curve,
            "changedKeyTimePass": changed_key_time_pass,
            "quaternionInterpolation": quaternion_observed,
            "sourceVmdInterpolationFrameCount": source_interpolation_count,
            "vmdTimeCurveKeys": source_interpolation_count > 0,
            "pass": bool(relevant_before)
            and bool(curve_after)
            and bool(common_uuids)
            and len(preserved_times) == len(relevant_before)
            and changed_key_time_pass
            and quaternion_observed
            and source_interpolation_count == len(source_vmd.bone_frames),
        }
        if not report["bakeBack"]["pass"] or not report["curveIdentity"]["pass"]:
            raise RuntimeError("bake-back propagation or curve identity gate failed")

        scene_path = output.with_suffix(".ma")
        scene_path.parent.mkdir(parents=True, exist_ok=True)
        cmds.file(rename=str(scene_path))
        cmds.file(save=True, force=True, type="mayaAscii")
        saved_world = _capture_worlds(cmds, root, frames)
        cmds.file(str(scene_path), open=True, force=True)
        reopened = next(iter(cmds.ls("*.mmd_control_rig_json", objectsOnly=True, long=True) or []), root)
        reopened_meta = read_mmd_control_rig_metadata(reopened)
        reopened_world = _capture_worlds(cmds, reopened, frames)
        persistence_error = _world_delta(saved_world, reopened_world)
        report["persistence"] = {
            "scene": str(scene_path),
            "state": reopened_meta.get("state") if reopened_meta else None,
            "maxWorldMatrixError": persistence_error,
            "pass": bool(reopened_meta and reopened_meta.get("state") == CONTROL_RIG_BAKED and math.isfinite(persistence_error) and persistence_error <= MATRIX_EPSILON),
        }
        if not report["persistence"]["pass"]:
            raise RuntimeError("save/reopen did not preserve BAKED state and transforms")

        collected = VmdSceneCollector().collect({"target_model": reopened})
        exported_path = output.with_suffix(".vmd")
        VmdExporter().export_vmd_animation(str(exported_path), collected)
        exported = VmdData().parse_file(str(exported_path))
        if not exported.bone_frames:
            raise RuntimeError("VMD export produced no bone frames")
        cmds.file(new=True, force=True)
        fresh_root = import_mmd_file(str(Path(args.pmx).resolve()), options={"setup_rig": True, "setup_bone_orientation": True, "import_physics": False, "create_mmd_shaders": False})
        if not fresh_root or not import_mmd_file(str(exported_path), options={"target_model": str(fresh_root), "pmx_path": str(Path(args.pmx).resolve()), "bake_mode": False}):
            raise RuntimeError("fresh PMX/VMD import failed")
        fresh_world = _capture_worlds(cmds, str(fresh_root), frames)
        fresh_error = _world_delta(saved_world, fresh_world)
        report["exportFreshImport"] = {
            "exportedVmd": str(exported_path),
            "exportedBoneFrames": len(exported.bone_frames),
            "interpolationFrameCount": sum(
                len(getattr(frame, "interpolation", b"")) == 64 for frame in exported.bone_frames
            ),
            "interpolationBytes64": all(
                len(getattr(frame, "interpolation", b"")) == 64 for frame in exported.bone_frames
            ),
            "maxWorldMatrixError": fresh_error,
            "pass": math.isfinite(fresh_error)
            and fresh_error <= MATRIX_EPSILON
            and bool(exported.bone_frames)
            and all(len(getattr(frame, "interpolation", b"")) == 64 for frame in exported.bone_frames),
        }
        if not report["exportFreshImport"]["pass"]:
            raise RuntimeError("VMD export/fresh import parity failed")
        report["boneCoverage"] = _bone_coverage(cmds, str(fresh_root), fresh_world, frames)
        if not report["boneCoverage"]["pass"]:
            raise RuntimeError(f"indexed bone coverage is missing: {report['boneCoverage']}")
        if not report["curveIdentity"]["pass"]:
            raise RuntimeError("quaternion interpolation or VMD time-curve evidence is missing")
        report["status"] = "pass"
    except Exception as exc:
        report["errors"].append(str(exc))
        report["traceback"] = traceback.format_exc()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "report": str(output)}, ensure_ascii=False))
    return 0 if report["status"] == "pass" else 1


def _mayapy(version: str) -> Path:
    return Path(os.environ.get(f"MAYAPY_{version}", f"C:/Program Files/Autodesk/Maya{version}/bin/mayapy.exe"))


def _require_ascii_path(path: Path, *, label: str) -> Path:
    """Return an absolute child-I/O path, rejecting argv-unsafe Unicode paths."""

    resolved = path.resolve()
    if not str(resolved).isascii():
        raise ValueError(f"{label} must be ASCII-safe for mayapy argv: {resolved}")
    return resolved


def _write_child_pair_config(path: Path, pair: Mapping[str, str]) -> Path:
    """Write one exact Unicode pair payload for a mayapy child to read as UTF-8."""

    config = _require_ascii_path(path, label="child pair config")
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(
        json.dumps({"name": pair["name"], "pmx": pair["pmx"], "vmd": pair["vmd"]}, ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )
    return config


def _load_child_pair_config(path: str | os.PathLike[str]) -> dict[str, str]:
    """Load a host-written pair config without routing Unicode through mayapy argv."""

    config = Path(path)
    if not config.is_file():
        raise FileNotFoundError(f"child pair config is missing: {config}")
    payload = _read_json(config)
    try:
        pair = {key: str(payload[key]) for key in ("name", "pmx", "vmd")}
    except KeyError as exc:
        raise ValueError(f"child pair config is missing {exc.args[0]!r}: {config}") from exc
    if not all(pair.values()):
        raise ValueError(f"child pair config contains an empty value: {config}")
    return pair


def _run_host(args: argparse.Namespace) -> int:
    output = Path(args.out).resolve()
    aggregate: dict[str, Any] = {"kind": REPORT_KIND, "status": "fail", "versions": [], "pairs": [], "children": [], "errors": []}
    try:
        versions = tuple(item.strip() for item in str(args.versions).split(",") if item.strip())
        if versions != REQUIRED_VERSIONS:
            raise ValueError(f"required Maya versions are exactly {REQUIRED_VERSIONS}; got {versions}")
        manifest = _resolve_path(args.manifest) if args.manifest else None
        pairs = load_pair_manifest(manifest) if manifest else discover_asset_pairs(_resolve_path(args.asset_root))
        aggregate["versions"] = list(versions)
        aggregate["pairs"] = pairs
        out_dir = output if output.suffix == "" else output.parent / output.stem
        out_dir.mkdir(parents=True, exist_ok=True)
        child_payloads = []
        for pair_index, pair in enumerate(pairs):
            for version in versions:
                # Pair labels and asset paths may be Unicode.  Keep all child
                # argv values ASCII-only and let the child reconstruct them
                # from this UTF-8 JSON payload instead.
                digest = hashlib.sha256(
                    json.dumps(pair, ensure_ascii=False, sort_keys=True).encode("utf-8")
                ).hexdigest()[:12]
                artifact_stem = f"pair_{pair_index:02d}_{digest}_maya{version}"
                child = _require_ascii_path(out_dir / f"{artifact_stem}.json", label="child report")
                pair_config = _write_child_pair_config(out_dir / f"{artifact_stem}.input.json", pair)
                child.parent.mkdir(parents=True, exist_ok=True)
                if bool(getattr(args, "resume", False)) and child.is_file():
                    payload = _read_json(child)
                    errors = validate_child_report(payload, pair=pair, version=version)
                    if not errors:
                        aggregate["children"].append(
                            {
                                "pair": pair["name"],
                                "maya": version,
                                "report": str(child),
                                "returncode": 0,
                                "status": payload.get("status"),
                                "errors": [],
                                "reused": True,
                            }
                        )
                        child_payloads.append(payload)
                        continue
                try:
                    child.unlink()
                except FileNotFoundError:
                    pass
                except OSError as exc:
                    raise RuntimeError(f"stale child report cleanup failed: {child}: {exc}") from exc
                mayapy = _mayapy(version)
                if not mayapy.is_file():
                    raise FileNotFoundError(f"mayapy not found for Maya {version}: {mayapy}")
                env = dict(os.environ)
                env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
                command = [str(mayapy), "-m", MODULE_NAME, "--child", "--maya", version, "--pair-config", str(pair_config), "--out", str(child)]
                completed = subprocess.run(command, cwd=str(ROOT), env=env, check=False)
                if not child.is_file():
                    raise RuntimeError(f"missing child report after mayapy exit {completed.returncode}: {child}")
                payload = _read_json(child)
                errors = validate_child_report(payload, pair=pair, version=version)
                aggregate["children"].append({"pair": pair["name"], "maya": version, "report": str(child), "returncode": completed.returncode, "status": payload.get("status"), "errors": errors})
                if errors:
                    raise RuntimeError(f"child gate failed for {pair['name']} Maya {version}: {'; '.join(errors)}")
                child_payloads.append(payload)
        aggregate["boneCoverage"] = _aggregate_bone_coverage(child_payloads)
        if not aggregate["boneCoverage"]["pass"]:
            raise RuntimeError(
                f"five-pair bone category coverage is incomplete: {aggregate['boneCoverage']}"
            )
        aggregate["status"] = "pass"
    except Exception as exc:
        aggregate["errors"].append(str(exc))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(aggregate, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": aggregate["status"], "report": str(output)}, ensure_ascii=False))
    return 0 if aggregate["status"] == "pass" else 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--child", action="store_true", help="run inside a single mayapy child")
    parser.add_argument("--maya", default="2026")
    parser.add_argument("--versions", default=",".join(REQUIRED_VERSIONS))
    parser.add_argument("--asset-root", default=str(DEFAULT_ASSET_ROOT))
    parser.add_argument("--manifest")
    parser.add_argument("--out", default=str(ROOT / "build" / "reports" / "real_asset_bake_rig_parity.json"))
    parser.add_argument("--pair-config", help="ASCII-safe UTF-8 JSON pair payload for --child")
    parser.add_argument(
        "--resume",
        action="store_true",
        help="reuse only existing child reports that still pass strict validation",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if args.child:
        if not args.pair_config:
            parser.error("--child requires --pair-config")
        try:
            pair = _load_child_pair_config(args.pair_config)
            _require_ascii_path(Path(args.out), label="child report")
        except (OSError, ValueError) as exc:
            parser.error(str(exc))
        args.pair_name = pair["name"]
        args.pmx = pair["pmx"]
        args.vmd = pair["vmd"]
        import maya.standalone

        maya.standalone.initialize(name="python")
        try:
            return _run_child(args)
        finally:
            maya.standalone.uninitialize()
    if args.dry_run:
        versions = tuple(item.strip() for item in str(args.versions).split(",") if item.strip())
        pairs = load_pair_manifest(_resolve_path(args.manifest)) if args.manifest else discover_asset_pairs(_resolve_path(args.asset_root))
        if versions != REQUIRED_VERSIONS:
            raise SystemExit(f"required Maya versions are exactly {REQUIRED_VERSIONS}; got {versions}")
        print(
            json.dumps(
                {"status": "ready", "versions": versions, "pairs": pairs},
                ensure_ascii=True,
                indent=2,
            )
        )
        return 0
    return _run_host(args)


if __name__ == "__main__":
    raise SystemExit(main())
