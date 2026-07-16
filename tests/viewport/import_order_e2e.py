"""Manifest-driven mayapy E2E checks for model/motion import ordering.

This harness verifies import-order invariants around mixed PMX/VMD scenes:

- background-like PMX and character PMX can be imported in either order;
- character model motion is imported only after its target model exists;
- camera motion clear replaces existing camera keys without clearing already
  imported character motion keys.
"""

from __future__ import annotations

import argparse
import itertools
import json
import os
import struct
import sys
import time
import traceback
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]


def _emit(payload: dict[str, Any], log_path: str | None = None) -> None:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    print(text)
    if log_path:
        with open(log_path, "a", encoding="utf-8", errors="replace") as handle:
            handle.write(text + "\n")


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


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _resolve_path(manifest_dir: Path, value: str | None) -> Path | None:
    if not value:
        return None
    path = Path(value)
    if path.is_absolute():
        return path
    return (manifest_dir / path).resolve()


def _load_cases(manifest_path: Path, case_name: str | None, limit: int) -> tuple[Path, list[dict[str, Any]]]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    defaults = manifest.get("defaults") or {}
    cases: list[dict[str, Any]] = []
    for case in manifest.get("cases") or []:
        if case_name and case.get("name") != case_name:
            continue
        cases.append(_deep_merge(defaults, case))
        if limit > 0 and len(cases) >= limit:
            break
    if not cases:
        raise ValueError(f"No manifest cases selected: {manifest_path}")
    return manifest_path.parent, cases


def _profile_fallback_counts() -> dict[str, int]:
    profile_path = os.environ.get("MMD_TOOLS_VMD_PROFILE_JSONL")
    if not profile_path:
        raise AssertionError("MMD_TOOLS_VMD_PROFILE_JSONL is required for --require-zero-fallback")
    path = Path(profile_path)
    if not path.exists():
        raise AssertionError(f"VMD profile JSONL was not written: {path}")

    totals = {"fallback_setKeyframe": 0, "fallback_base_values_build": 0}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        counts = payload.get("counts") or {}
        for key in totals:
            totals[key] += int(counts.get(key) or 0)
    return totals


def _default_cases(background_model: Path, character_model: Path, character_motion: Path) -> tuple[Path, list[dict[str, Any]]]:
    """Build local-asset cases from CLI paths without writing a committed manifest."""
    return Path.cwd(), [
        {
            "name": "background_character_permutations",
            "assets": [
                {
                    "id": "background",
                    "type": "pmx",
                    "role": "background",
                    "path": str(background_model),
                    "useNamespace": False,
                },
                {
                    "id": "character",
                    "type": "pmx",
                    "role": "character",
                    "path": str(character_model),
                    "useNamespace": False,
                },
            ],
            "orders": [
                ["background", "character"],
                ["character", "background"],
            ],
        },
        {
            "name": "character_motion_camera_clear_permutations",
            "assets": [
                {
                    "id": "character",
                    "type": "pmx",
                    "role": "character",
                    "path": str(character_model),
                    "useNamespace": True,
                },
                {
                    "id": "character_motion",
                    "type": "vmd",
                    "kind": "model",
                    "path": str(character_motion),
                    "target": "character",
                },
                {
                    "id": "camera_wide",
                    "type": "vmd",
                    "kind": "camera",
                    "generated": {
                        "type": "camera-vmd",
                        "filename": "camera_wide.vmd",
                        "frames": [0, 10],
                    },
                    "expect": {
                        "cameraKeys": [0, 10],
                    },
                },
                {
                    "id": "camera_short_clear",
                    "type": "vmd",
                    "kind": "camera",
                    "requires": ["camera_wide"],
                    "clearExistingMotion": True,
                    "generated": {
                        "type": "camera-vmd",
                        "filename": "camera_short.vmd",
                        "frames": [0],
                    },
                    "expect": {
                        "cameraKeys": [0],
                    },
                },
                {
                    "id": "light_wide",
                    "type": "vmd",
                    "kind": "light",
                    "generated": {
                        "type": "light-vmd",
                        "filename": "light_wide.vmd",
                        "frames": [0, 10],
                    },
                    "expect": {
                        "lightKeys": [0, 10],
                    },
                },
                {
                    "id": "light_short_clear",
                    "type": "vmd",
                    "kind": "light",
                    "requires": ["light_wide"],
                    "clearExistingMotion": True,
                    "generated": {
                        "type": "light-vmd",
                        "filename": "light_short.vmd",
                        "frames": [0],
                    },
                    "expect": {
                        "lightKeys": [0],
                    },
                },
            ],
            "constraints": [
                ["character", "before", "character_motion"],
                ["camera_wide", "before", "camera_short_clear"],
                ["light_wide", "before", "light_short_clear"],
            ],
            "orders": [
                [
                    "character",
                    "character_motion",
                    "camera_wide",
                    "camera_short_clear",
                    "light_wide",
                    "light_short_clear",
                ],
                [
                    "character",
                    "character_motion",
                    "light_wide",
                    "light_short_clear",
                    "camera_wide",
                    "camera_short_clear",
                ],
                [
                    "camera_wide",
                    "camera_short_clear",
                    "light_wide",
                    "light_short_clear",
                    "character",
                    "character_motion",
                ],
            ],
            "expect": {
                "characterMotionKeys": True,
                "cameraKeysAfterClear": [0],
                "lightKeysAfterClear": [0],
            },
        },
    ]


def _node_mmd_bone_name(cmds, node: str) -> str:
    from mmd_tools.core.constants import ATTR_MMD_BONE_NAME

    try:
        if cmds.attributeQuery(ATTR_MMD_BONE_NAME, node=node, exists=True):
            return cmds.getAttr(f"{node}.{ATTR_MMD_BONE_NAME}") or ""
    except Exception:
        pass
    return ""


def _find_center_joint(cmds, root: str) -> str:
    joints = cmds.listRelatives(root, allDescendents=True, type="joint", fullPath=True) or []
    candidates: list[str] = []
    for joint in joints:
        leaf = joint.rsplit("|", 1)[-1].rsplit(":", 1)[-1].lower()
        mmd_name = _node_mmd_bone_name(cmds, joint)
        if leaf.startswith("center") or mmd_name in {"center", "センター"}:
            candidates.append(joint)
    if not candidates:
        raise AssertionError(f"No center-like joint found under {root}")
    candidates.sort(key=lambda item: item.count("|"))
    return candidates[0]


def _key_times(cmds, node: str, attr: str) -> list[float]:
    values = cmds.keyframe(node, attribute=attr, query=True, timeChange=True)
    return [float(value) for value in (values or [])]


def _key_times_for_attrs(cmds, node: str, attrs: tuple[str, ...]) -> list[float]:
    times: set[float] = set()
    if not node or not cmds.objExists(node):
        return []
    for attr in attrs:
        attr_name = attr.split("[", 1)[0]
        if not cmds.attributeQuery(attr_name, node=node, exists=True):
            continue
        times.update(_key_times(cmds, node, attr))
    return sorted(times)


def _camera_key_times(cmds, camera: str) -> list[float]:
    from mmd_tools.converters.vmd_camera_animation import ATTR_MMD_CAMERA_ROOT_NODE, ATTR_MMD_CAMERA_TARGET_NODE

    nodes = [camera]
    if cmds.attributeQuery(ATTR_MMD_CAMERA_TARGET_NODE, node=camera, exists=True):
        nodes.extend(cmds.listConnections(f"{camera}.{ATTR_MMD_CAMERA_TARGET_NODE}", source=True) or [])
    if cmds.attributeQuery(ATTR_MMD_CAMERA_ROOT_NODE, node=camera, exists=True):
        nodes.extend(cmds.listConnections(f"{camera}.{ATTR_MMD_CAMERA_ROOT_NODE}", source=True) or [])

    times: set[float] = set()
    transform_attrs = ("translateX", "translateY", "translateZ", "rotateX", "rotateY", "rotateZ")
    for node in nodes:
        times.update(_key_times_for_attrs(cmds, node, transform_attrs))
    for shape in cmds.listRelatives(camera, shapes=True, type="camera") or []:
        times.update(_key_times_for_attrs(cmds, shape, ("focalLength", "orthographicWidth", "orthographic")))
    return sorted(times)


def _light_key_times(cmds, light: str) -> list[float]:
    times = set(_key_times_for_attrs(cmds, light, ("rotateX", "rotateY", "rotateZ")))
    times.update(_key_times_for_attrs(cmds, light, ("mmd_light_colorR", "mmd_light_colorG", "mmd_light_colorB")))
    for shape in cmds.listRelatives(light, shapes=True, type="directionalLight") or []:
        times.update(_key_times_for_attrs(cmds, shape, ("colorR", "colorG", "colorB")))
    return sorted(times)


def _import_model(path: Path, *, use_namespace: bool) -> str:
    from mmd_tools.io.mmd_importer import import_mmd_file

    root = import_mmd_file(
        str(path),
        options={
            "use_namespace": use_namespace,
            "import_physics": False,
            "use_native_pmx_parse": False,
            "require_native_pmx_parse": False,
        },
    )
    if not root:
        raise AssertionError(f"Model import failed: {path}")
    return str(root)


def _import_vmd(
    path: Path,
    *,
    target_model: str | None = None,
    camera_motion: bool = False,
    clear_existing_motion: bool = False,
) -> None:
    from mmd_tools.io.mmd_importer import import_mmd_file

    options: dict[str, Any] = {
        "clear_existing_motion": clear_existing_motion,
        "use_native_pmx_parse": False,
        "require_native_pmx_parse": False,
    }
    if camera_motion:
        options["scene_animation_only"] = True
    elif target_model:
        options["target_model"] = target_model
    else:
        raise AssertionError(f"Model VMD requires an explicit target: {path}")
    if not import_mmd_file(str(path), options=options):
        raise AssertionError(f"VMD import failed: {path}")


def _write_camera_vmd(path: Path, frame_numbers: list[int]) -> None:
    data = bytearray()
    data.extend(b"Vocaloid Motion Data 0002\x00\x00\x00\x00\x00")
    model_name = "Camera".encode("shift-jis")
    data.extend(model_name + b"\x00" * (20 - len(model_name)))
    data.extend(struct.pack("<I", 0))  # bones
    data.extend(struct.pack("<I", 0))  # morphs
    data.extend(struct.pack("<I", len(frame_numbers)))
    for index, frame in enumerate(frame_numbers):
        data.extend(struct.pack("<I", frame))
        data.extend(struct.pack("<f", 30.0 + index))
        data.extend(struct.pack("<fff", float(index), 10.0, 0.0))
        data.extend(struct.pack("<fff", 0.0, 0.0, 0.0))
        data.extend(b"\x14" * 24)
        data.extend(struct.pack("<I", 30))
        data.extend(struct.pack("<B", 0))
    data.extend(struct.pack("<I", 0))  # lights
    data.extend(struct.pack("<I", 0))  # shadows
    data.extend(struct.pack("<I", 0))  # IK display
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(data))


def _write_light_vmd(path: Path, frame_numbers: list[int]) -> None:
    data = bytearray()
    data.extend(b"Vocaloid Motion Data 0002\x00\x00\x00\x00\x00")
    model_name = "Light".encode("shift-jis")
    data.extend(model_name + b"\x00" * (20 - len(model_name)))
    data.extend(struct.pack("<I", 0))  # bones
    data.extend(struct.pack("<I", 0))  # morphs
    data.extend(struct.pack("<I", 0))  # cameras
    data.extend(struct.pack("<I", len(frame_numbers)))
    for index, frame in enumerate(frame_numbers):
        data.extend(struct.pack("<I", frame))
        data.extend(struct.pack("<fff", 1.0, max(0.1, 0.8 - index * 0.1), max(0.1, 0.6 - index * 0.1)))
        data.extend(struct.pack("<fff", 0.5 + index, -1.0, 1.0))
    data.extend(struct.pack("<I", 0))  # shadows
    data.extend(struct.pack("<I", 0))  # IK display
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(data))


def _camera_transform(cmds) -> str:
    from mmd_tools.core.constants import ATTR_MMD_CAMERA

    cameras = cmds.ls(f"*.{ATTR_MMD_CAMERA}", objectsOnly=True) or []
    if not cameras:
        raise AssertionError("No MMD camera transform found")
    return cameras[0]


def _light_transform(cmds) -> str:
    from mmd_tools.core.constants import ATTR_MMD_LIGHT

    lights = cmds.ls(f"*.{ATTR_MMD_LIGHT}", objectsOnly=True) or []
    if not lights:
        raise AssertionError("No MMD light transform found")
    return lights[0]


def _assets_by_id(case: dict[str, Any]) -> dict[str, dict[str, Any]]:
    assets = case.get("assets") or []
    by_id: dict[str, dict[str, Any]] = {}
    for asset in assets:
        asset_id = asset.get("id")
        if not asset_id:
            raise ValueError(f"Manifest asset has no id: {asset}")
        if asset_id in by_id:
            raise ValueError(f"Duplicate manifest asset id: {asset_id}")
        by_id[asset_id] = asset
    return by_id


def _constraint_pairs(case: dict[str, Any], assets: dict[str, dict[str, Any]]) -> set[tuple[str, str]]:
    pairs: set[tuple[str, str]] = set()
    for asset_id, asset in assets.items():
        for requirement in asset.get("requires") or []:
            pairs.add((str(requirement), asset_id))
        target = asset.get("target")
        if asset.get("type") == "vmd" and target:
            pairs.add((str(target), asset_id))
    for constraint in case.get("constraints") or []:
        if len(constraint) != 3 or constraint[1] != "before":
            raise ValueError(f"Unsupported order constraint: {constraint}")
        pairs.add((str(constraint[0]), str(constraint[2])))
    unknown = {item for pair in pairs for item in pair if item not in assets}
    if unknown:
        raise ValueError(f"Order constraints reference unknown assets: {sorted(unknown)}")
    return pairs


def _validate_order(order: list[str], assets: dict[str, dict[str, Any]], pairs: set[tuple[str, str]]) -> None:
    if sorted(order) != sorted(assets):
        raise ValueError(f"Order must contain every asset exactly once: {order}")
    positions = {asset_id: index for index, asset_id in enumerate(order)}
    violations = [(before, after) for before, after in pairs if positions[before] > positions[after]]
    if violations:
        raise ValueError(f"Order violates constraints: order={order}, violations={violations}")


def _orders_for_case(case: dict[str, Any], assets: dict[str, dict[str, Any]]) -> list[list[str]]:
    pairs = _constraint_pairs(case, assets)
    if case.get("orders"):
        orders = [[str(asset_id) for asset_id in order] for order in case["orders"]]
        for order in orders:
            _validate_order(order, assets, pairs)
        return orders

    policy = case.get("orderPolicy") or {}
    if policy.get("mode") != "permutations":
        order = list(assets)
        _validate_order(order, assets, pairs)
        return [order]

    max_orders = int(policy.get("maxOrders") or 0)
    orders: list[list[str]] = []
    for candidate in itertools.permutations(assets):
        order = list(candidate)
        try:
            _validate_order(order, assets, pairs)
        except ValueError:
            continue
        orders.append(order)
        if max_orders > 0 and len(orders) >= max_orders:
            break
    if not orders:
        raise ValueError(f"No valid order permutations for case: {case.get('name')}")
    return orders


def _asset_path(manifest_dir: Path, out_dir: Path, case_name: str, asset: dict[str, Any]) -> Path:
    path = _resolve_path(manifest_dir, asset.get("path"))
    if path:
        return path

    generated = asset.get("generated") or {}
    if generated.get("type") == "camera-vmd":
        filename = generated.get("filename") or f"{case_name}-{asset['id']}.vmd"
        path = out_dir / case_name / filename
        _write_camera_vmd(path, [int(frame) for frame in generated.get("frames", [])])
        return path
    if generated.get("type") == "light-vmd":
        filename = generated.get("filename") or f"{case_name}-{asset['id']}.vmd"
        path = out_dir / case_name / filename
        _write_light_vmd(path, [int(frame) for frame in generated.get("frames", [])])
        return path

    raise ValueError(f"Asset has neither path nor supported generated block: {asset}")


def _character_state(cmds, roots: dict[str, str], centers: dict[str, str]) -> tuple[str, list[float]] | None:
    for asset_id, root in roots.items():
        if asset_id in centers:
            return centers[asset_id], _key_times(cmds, centers[asset_id], "translateX")
        if cmds.objExists(root):
            try:
                center = _find_center_joint(cmds, root)
            except AssertionError:
                continue
            centers[asset_id] = center
            return center, _key_times(cmds, center, "translateX")
    return None


def _run_order(
    manifest_dir: Path,
    case: dict[str, Any],
    order: list[str],
    out_dir: Path,
) -> dict[str, Any]:
    import maya.cmds as cmds

    cmds.file(new=True, force=True)
    assets = _assets_by_id(case)
    roots: dict[str, str] = {}
    centers: dict[str, str] = {}
    camera_keys_before_clear: list[float] = []
    camera_keys_after_clear: list[float] = []
    light_keys_before_clear: list[float] = []
    light_keys_after_clear: list[float] = []
    preserved_snapshots: list[dict[str, Any]] = []
    asset_timings: list[dict[str, Any]] = []

    for asset_id in order:
        asset = assets[asset_id]
        asset_type = asset.get("type")
        asset_start = time.perf_counter()
        if asset_type == "pmx":
            path = _asset_path(manifest_dir, out_dir, case["name"], asset)
            root = _import_model(path, use_namespace=bool(asset.get("useNamespace", True)))
            roots[asset_id] = root
            if asset.get("role") == "character":
                centers[asset_id] = _find_center_joint(cmds, root)
            asset_timings.append(
                {
                    "asset": asset_id,
                    "type": asset_type,
                    "kind": asset.get("kind"),
                    "elapsed": round(time.perf_counter() - asset_start, 4),
                }
            )
            continue

        if asset_type == "vmd":
            path = _asset_path(manifest_dir, out_dir, case["name"], asset)
            target_id = asset.get("target")
            target_root = roots.get(target_id) if target_id else None
            if target_id and not target_root:
                raise AssertionError(f"VMD target was not imported before motion: {asset_id} -> {target_id}")

            clear_existing = bool(asset.get("clearExistingMotion", asset.get("clear_existing_motion", False)))
            before_character = _character_state(cmds, roots, centers) if clear_existing else None
            _import_vmd(
                path,
                target_model=target_root,
                camera_motion=asset.get("kind") in {"camera", "light"},
                clear_existing_motion=clear_existing,
            )

            if asset.get("kind") == "camera":
                camera = _camera_transform(cmds)
                camera_keys = _camera_key_times(cmds, camera)
                if clear_existing:
                    camera_keys_after_clear = camera_keys
                else:
                    camera_keys_before_clear = camera_keys
                expected = asset.get("expect", {}).get("cameraKeys")
                if expected is not None and camera_keys != [float(frame) for frame in expected]:
                    raise AssertionError(f"Camera keys mismatch for {asset_id}: expected={expected}, actual={camera_keys}")

            if asset.get("kind") == "light":
                light = _light_transform(cmds)
                light_keys = _light_key_times(cmds, light)
                if clear_existing:
                    light_keys_after_clear = light_keys
                else:
                    light_keys_before_clear = light_keys
                expected = asset.get("expect", {}).get("lightKeys")
                if expected is not None and light_keys != [float(frame) for frame in expected]:
                    raise AssertionError(f"Light keys mismatch for {asset_id}: expected={expected}, actual={light_keys}")

            if before_character:
                center, before_keys = before_character
                after_keys = _key_times(cmds, center, "translateX")
                preserved_snapshots.append(
                    {
                        "asset": asset_id,
                        "center": center,
                        "before": before_keys,
                        "after": after_keys,
                    }
                )
                if before_keys and after_keys != before_keys:
                    raise AssertionError(
                        "Clear import changed character motion keys: "
                        f"asset={asset_id}, before={before_keys}, after={after_keys}"
                    )
            asset_timings.append(
                {
                    "asset": asset_id,
                    "type": asset_type,
                    "kind": asset.get("kind"),
                    "clear_existing_motion": clear_existing,
                    "elapsed": round(time.perf_counter() - asset_start, 4),
                }
            )
            continue

        raise ValueError(f"Unsupported asset type: {asset_type}")

    expect = case.get("expect") or {}
    for asset_id, asset in assets.items():
        role = asset.get("role")
        if role in {"background", "character"} and not cmds.objExists(roots.get(asset_id, "")):
            raise AssertionError(f"Expected imported root for {role}: {asset_id}")
        if role == "character":
            center = centers.get(asset_id) or _find_center_joint(cmds, roots[asset_id])
            centers[asset_id] = center
            if expect.get("characterMotionKeys"):
                center_keys = _key_times(cmds, center, "translateX")
                if not center_keys:
                    raise AssertionError(f"Character motion keys were not created: {asset_id}")

    if expect.get("cameraKeysAfterClear") is not None:
        expected_camera_keys = [float(frame) for frame in expect["cameraKeysAfterClear"]]
        if camera_keys_after_clear != expected_camera_keys:
            raise AssertionError(
                f"Camera clear keys mismatch: expected={expected_camera_keys}, actual={camera_keys_after_clear}"
            )
    if expect.get("lightKeysAfterClear") is not None:
        expected_light_keys = [float(frame) for frame in expect["lightKeysAfterClear"]]
        if light_keys_after_clear != expected_light_keys:
            raise AssertionError(
                f"Light clear keys mismatch: expected={expected_light_keys}, actual={light_keys_after_clear}"
            )

    return {
        "case": case["name"],
        "status": "pass",
        "order": order,
        "roots": roots,
        "centers": centers,
        "camera_keys_before_clear": camera_keys_before_clear,
        "camera_keys_after_clear": camera_keys_after_clear,
        "light_keys_before_clear": light_keys_before_clear,
        "light_keys_after_clear": light_keys_after_clear,
        "preserved_snapshots": preserved_snapshots,
        "asset_timings": asset_timings,
        "joint_count": len(cmds.ls(type="joint") or []),
    }


def run_manifest(
    manifest_path: Path | None,
    out_dir: Path,
    *,
    background_model: Path,
    character_model: Path,
    character_motion: Path,
    case_name: str | None = None,
    limit: int = 0,
    order_limit: int = 0,
    log_path: str | None = None,
    require_zero_fallback: bool = False,
) -> dict[str, Any]:
    _repo_imports()
    import maya.cmds as cmds

    from mmd_tools.core import settings

    settings.set("import.model.create_mmd_shaders", False)
    settings.set("import.light.create_controller", True)
    out_dir.mkdir(parents=True, exist_ok=True)

    if manifest_path:
        manifest_dir, cases = _load_cases(manifest_path, case_name, limit)
        manifest_label = str(manifest_path)
    else:
        manifest_dir, cases = _default_cases(background_model, character_model, character_motion)
        if case_name:
            cases = [case for case in cases if case["name"] == case_name]
        if limit > 0:
            cases = cases[:limit]
        if not cases:
            raise ValueError("No generated default import-order cases selected")
        manifest_label = "<generated-from-cli-paths>"
    results: list[dict[str, Any]] = []
    for case in cases:
        assets = _assets_by_id(case)
        orders = _orders_for_case(case, assets)
        if order_limit > 0:
            orders = orders[:order_limit]
        for index, order in enumerate(orders):
            result = _run_order(manifest_dir, case, order, out_dir)
            result["order_index"] = index
            _emit(result, log_path)
            results.append(result)

    fallback_counts = None
    if require_zero_fallback:
        fallback_counts = _profile_fallback_counts()
        nonzero = {key: value for key, value in fallback_counts.items() if value}
        if nonzero:
            raise AssertionError(f"Expected zero fallback keying activity, got {nonzero}")

    summary = {
        "status": "pass",
        "manifest": manifest_label,
        "case_count": len(cases),
        "order_count": len(results),
        "results": results,
        "fallback_counts": fallback_counts,
    }
    _emit({"summary": summary}, log_path)
    cmds.file(new=True, force=True)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest")
    parser.add_argument("--background-model", default=str(ROOT / "tests/data/for_unit_test/test_1bone_cube.pmx"))
    parser.add_argument("--character-model", default=str(ROOT / "tests/data/mmt_test_model.pmx"))
    parser.add_argument("--character-motion", default=str(ROOT / "tests/data/mmt_test_model_test_motion.vmd"))
    parser.add_argument("--case", dest="case_name", default=None)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--order-limit", type=int, default=0)
    parser.add_argument("--out-dir", default=str(ROOT / "build/import-order-e2e"))
    parser.add_argument("--log")
    parser.add_argument("--require-zero-fallback", action="store_true")
    args = parser.parse_args()

    initialized = False
    try:
        initialized = _initialize_maya()
        _repo_imports()
        import maya.cmds as cmds

        plugin_path = str(ROOT / "mmd_tools" / "plugin_main.py")
        if not cmds.pluginInfo(plugin_path, query=True, loaded=True):
            cmds.loadPlugin(plugin_path, quiet=True)
        result = run_manifest(
            Path(args.manifest).resolve() if args.manifest else None,
            Path(args.out_dir).resolve(),
            background_model=Path(args.background_model).resolve(),
            character_model=Path(args.character_model).resolve(),
            character_motion=Path(args.character_motion).resolve(),
            case_name=args.case_name,
            limit=args.limit,
            order_limit=args.order_limit,
            log_path=args.log,
            require_zero_fallback=args.require_zero_fallback,
        )
        return 0 if result["status"] == "pass" else 1
    except Exception:
        _emit({"status": "error", "traceback": traceback.format_exc()}, args.log)
        return 1
    finally:
        if initialized:
            try:
                import maya.standalone

                maya.standalone.uninitialize()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
