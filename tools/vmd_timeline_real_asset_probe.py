"""Run isolated real-asset VMD context/timeline sampling probes.

The controller launches one fresh mayapy process for every strategy/prefix
pair.  Only the ASCII config path and numeric/ASCII selectors cross argv;
PMX/VMD paths are decoded from the UTF-8 JSON config inside each worker.
This is a sampling probe, not an export runner: it imports through production
Actions, reuses production route discovery and sample-plan construction, and
never writes a VMD or reuses raw VMD transforms.
"""

from __future__ import annotations

import argparse
from collections import Counter, deque
import hashlib
import json
import math
import os
from pathlib import Path
import struct
import subprocess
import sys
import time
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = 1
PREFIX_FRAMES = (120, 300, 600)
STRATEGIES = ("context", "timeline_probe")
DEFAULT_FULL_FRAME_COUNT = 6786
PACKED_HEADER_SIZE = 6
PHYSICS_NODE_TYPES = frozenset(
    {
        "mmdPhysicsSolver",
        "mmdPhysicsBoneDriver",
        "mmdPhysicsWorldShape",
        "mmdRigidBodyShape",
        "mmdPhysicsJointShape",
    }
)


class ProbeConfigurationError(ValueError):
    """Raised before Maya starts when the local probe config is invalid."""


def _arguments(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--strategy", choices=STRATEGIES)
    parser.add_argument("--prefix", type=int, choices=PREFIX_FRAMES)
    return parser.parse_args(argv)


def load_config(path: Path, *, require_assets: bool = True) -> dict[str, Any]:
    """Read and strictly normalize the UTF-8 real-asset probe config."""

    config_path = Path(path)
    if not str(config_path).isascii():
        raise ProbeConfigurationError("config path must be ASCII-safe for mayapy argv")
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ProbeConfigurationError(f"could not read UTF-8 config: {exc}") from exc
    if not isinstance(raw, Mapping):
        raise ProbeConfigurationError("config root must be an object")
    allowed = {
        "schema_version",
        "pmx_path",
        "vmd_path",
        "prefix_frames",
        "out_dir",
        "full_frame_count",
        "mayapy",
        "worker_timeout_sec",
    }
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise ProbeConfigurationError(f"unknown config fields: {', '.join(unknown)}")
    if raw.get("schema_version") != SCHEMA_VERSION:
        raise ProbeConfigurationError(f"schema_version must be {SCHEMA_VERSION}")
    try:
        prefixes = tuple(int(value) for value in raw["prefix_frames"])
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise ProbeConfigurationError("prefix_frames must contain 120, 300, and 600") from exc
    if prefixes != PREFIX_FRAMES:
        raise ProbeConfigurationError("prefix_frames must be exactly [120, 300, 600]")
    try:
        full_frame_count = int(raw.get("full_frame_count", DEFAULT_FULL_FRAME_COUNT))
    except (TypeError, ValueError, OverflowError) as exc:
        raise ProbeConfigurationError("full_frame_count must be an integer") from exc
    if full_frame_count <= max(PREFIX_FRAMES):
        raise ProbeConfigurationError("full_frame_count must exceed the largest prefix")
    try:
        pmx_path = Path(str(raw["pmx_path"])).expanduser()
        vmd_path = Path(str(raw["vmd_path"])).expanduser()
        out_dir = Path(str(raw["out_dir"])).expanduser()
    except KeyError as exc:
        raise ProbeConfigurationError(f"missing config field: {exc.args[0]}") from exc
    if pmx_path.suffix.casefold() not in {".pmx", ".pmd"}:
        raise ProbeConfigurationError("pmx_path must name a PMX or PMD file")
    if vmd_path.suffix.casefold() != ".vmd":
        raise ProbeConfigurationError("vmd_path must name a VMD file")
    if require_assets:
        for label, asset in (("pmx_path", pmx_path), ("vmd_path", vmd_path)):
            if not asset.is_file():
                raise ProbeConfigurationError(f"{label} does not exist: {asset}")
    mayapy = raw.get("mayapy")
    if mayapy is not None and not str(mayapy).strip():
        raise ProbeConfigurationError("mayapy must not be empty")
    try:
        worker_timeout_sec = float(raw.get("worker_timeout_sec", 900.0))
    except (TypeError, ValueError, OverflowError) as exc:
        raise ProbeConfigurationError("worker_timeout_sec must be numeric") from exc
    if not math.isfinite(worker_timeout_sec) or worker_timeout_sec <= 0.0:
        raise ProbeConfigurationError("worker_timeout_sec must be positive and finite")
    return {
        "schema_version": SCHEMA_VERSION,
        "pmx_path": pmx_path,
        "vmd_path": vmd_path,
        "prefix_frames": prefixes,
        "out_dir": out_dir,
        "full_frame_count": full_frame_count,
        "mayapy": str(mayapy) if mayapy is not None else None,
        "worker_timeout_sec": worker_timeout_sec,
    }


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _result_stem(strategy: str, prefix: int) -> str:
    return f"{strategy}-{int(prefix):04d}"


def _result_path(config: Mapping[str, Any], strategy: str, prefix: int) -> Path:
    return Path(config["out_dir"]) / f"{_result_stem(strategy, prefix)}.json"


def _packed_path(config: Mapping[str, Any], strategy: str, prefix: int) -> Path:
    return Path(config["out_dir"]) / f"{_result_stem(strategy, prefix)}.packed.bin"


def _payload(plan: Any, strategy: str) -> str:
    request: dict[str, Any] = {
        "version": 1,
        "frames": list(plan.frames),
        "channels": list(plan.request_channels),
    }
    if strategy == "timeline_probe":
        request["evaluation_mode"] = strategy
    return json.dumps(request, ensure_ascii=False, separators=(",", ":"))


def _packed_bytes(values: Sequence[float]) -> bytes:
    return struct.pack(f"<{len(values)}d", *(float(value) for value in values))


def _canonical_root(value: Any, cmds_module: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"ImportModelAction returned an invalid root: {value!r}")
    matches = cmds_module.ls(value, long=True) or []
    if isinstance(matches, (str, bytes)) or len(matches) != 1:
        raise RuntimeError(f"imported root is not unique: {value!r} -> {matches!r}")
    root = str(matches[0])
    if not root.startswith("|"):
        raise RuntimeError(f"imported root is not a canonical DAG path: {root!r}")
    return root


def _import_options() -> dict[str, Any]:
    return {
        "scale": 1.0,
        "import_physics": True,
        "setup_rig": True,
        "setup_bone_orientation": True,
        "create_mmd_control_rig": False,
        "create_mmd_shaders": False,
        "use_cpp_fast_load": False,
        "use_native_pmx_parse": False,
        "require_native_pmx_parse": False,
    }


def _require_action_success(result: Any, name: str, *, require_root: bool) -> Any:
    warnings = list(getattr(result, "warnings", ()) or ())
    outcome = str(getattr(result, "outcome", "") or "").casefold()
    if not getattr(result, "succeeded", False) or outcome not in {"", "success"} or warnings:
        raise RuntimeError(
            f"{name} failed or was partial: error={getattr(result, 'error', None)!r} "
            f"outcome={outcome!r} warnings={warnings!r}"
        )
    root = getattr(result, "root_node", None)
    if require_root and not root:
        raise RuntimeError(f"{name} returned no model root")
    return root


def _import_assets(config: Mapping[str, Any], cmds_module: Any) -> str:
    from mmd_tools.actions.import_model_action import ImportModelAction, ImportModelRequest
    from mmd_tools.actions.import_vmd_action import ImportVmdAction, ImportVmdRequest

    model_result = ImportModelAction().execute(
        ImportModelRequest(
            file_path=str(config["pmx_path"]),
            options={**_import_options(), "profile": {}},
            create_new_scene=True,
        )
    )
    root = _canonical_root(
        _require_action_success(model_result, "ImportModelAction", require_root=True),
        cmds_module,
    )
    motion_result = ImportVmdAction().execute(
        ImportVmdRequest(
            file_path=str(config["vmd_path"]),
            options={
                **_import_options(),
                "target_model": root,
                "pmx_path": str(config["pmx_path"]),
                "bake_mode": False,
            },
            create_new_scene=False,
        )
    )
    _require_action_success(motion_result, "ImportVmdAction", require_root=False)
    return root


def _upstream_physics_nodes(cmds_module: Any, plugs: Sequence[str]) -> list[dict[str, str]]:
    """Return a bounded unique inventory of physics nodes upstream of channels."""

    queue: deque[str] = deque()
    for plug in plugs:
        queue.extend(
            str(value).split(".", 1)[0]
            for value in (
                cmds_module.listConnections(
                    plug,
                    source=True,
                    destination=False,
                    plugs=True,
                )
                or []
            )
        )
    visited: set[str] = set()
    physics: dict[str, str] = {}
    while queue and len(visited) < 4096:
        node = str(queue.popleft())
        matches = cmds_module.ls(node, long=True) or [node]
        canonical = str(matches[0])
        if canonical in visited:
            continue
        visited.add(canonical)
        node_type = str(cmds_module.nodeType(canonical) or "")
        if node_type in PHYSICS_NODE_TYPES:
            physics[canonical] = node_type
        queue.extend(
            str(value)
            for value in (
                cmds_module.listConnections(
                    canonical,
                    source=True,
                    destination=False,
                )
                or []
            )
        )
    return [
        {"node": node, "node_type": node_type}
        for node, node_type in sorted(physics.items())
    ]


def _route_inventory(plan: Any, routes: Mapping[str, Mapping[str, Sequence[str]]], cmds_module: Any) -> dict[str, Any]:
    rows = []
    node_types: Counter[str] = Counter()
    hint_counts: Counter[str] = Counter()
    routed_count = 0
    for channel in plan.logical_channels:
        node = channel.plug.rsplit(".", 1)[0]
        node_type = str(cmds_module.nodeType(node) or "")
        route = routes.get(channel.joint) or {}
        routed = channel.attr in route
        routed_count += int(routed)
        node_types[node_type] += 1
        hint_counts[channel.hint] += 1
        rows.append(
            {
                "joint": channel.joint,
                "attr": channel.attr,
                "plug": channel.plug,
                "physical_index": channel.physical_index,
                "hint": channel.hint,
                "unit": channel.unit,
                "node_type": node_type,
                "routed": routed,
                "physical_node_is_physics": node_type in PHYSICS_NODE_TYPES,
            }
        )
    physics_nodes = _upstream_physics_nodes(
        cmds_module,
        [channel.plug for channel in plan.physical_channels],
    )
    return {
        "logical_channel_count": len(plan.logical_channels),
        "physical_channel_count": len(plan.physical_channels),
        "routed_logical_channel_count": routed_count,
        "hint_counts": dict(sorted(hint_counts.items())),
        "physical_node_type_counts": dict(sorted(node_types.items())),
        "physics_upstream_nodes": physics_nodes,
        "channels": rows,
    }


def _animated_joint_plan(root: str, prefix: int, cmds_module: Any) -> tuple[Any, Mapping[str, Any]]:
    from mmd_tools.adapters.native_vmd_batch_sampler import build_dense_bone_sample_plan
    from mmd_tools.converters.vmd_scene_collector import VmdSceneCollector, _routed_key_times

    collector = VmdSceneCollector()
    joints = collector._find_joints(root)
    routes = collector._scene_authored_input_routes(joints, root)
    animated = []
    for joint in joints:
        long_joint = str((cmds_module.ls(joint, long=True) or [joint])[0])
        route = routes.get(long_joint, {})
        if _routed_key_times(joint, route):
            animated.append(joint)
    if not animated:
        raise RuntimeError("Current Model has no animated bone routes")
    plan = build_dense_bone_sample_plan(
        animated,
        range(prefix),
        input_routes=routes,
        cmds_module=cmds_module,
    )
    return plan, routes


def _run_worker(config: Mapping[str, Any], strategy: str, prefix: int) -> dict[str, Any]:
    import maya.standalone
    from tests.common.maya_plugin_setup import load_mmd_tools_plugin

    try:
        maya.standalone.initialize(name="python")
    except RuntimeError:
        pass
    from maya import cmds
    from mmd_tools.adapters.native_vmd_batch_sampler import (
        NativeVmdBatchSampler,
        parse_packed_result,
    )

    out_dir = Path(config["out_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    load_mmd_tools_plugin(ROOT)
    import_started = time.perf_counter()
    root = _import_assets(config, cmds)
    import_wall_sec = time.perf_counter() - import_started
    plan, routes = _animated_joint_plan(root, prefix, cmds)
    inventory = _route_inventory(plan, routes, cmds)
    inventory_sha256 = hashlib.sha256(
        json.dumps(
            inventory,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    gateway = NativeVmdBatchSampler(cmds)
    if not gateway.available:
        raise RuntimeError(f"mmdVmdBatchSample unavailable: {gateway.last_diagnostics!r}")
    entry_time = float(cmds.currentTime(query=True))
    started = time.perf_counter()
    packed = [
        float(value)
        for value in cmds.mmdVmdBatchSample(payload=_payload(plan, strategy))
    ]
    wall_sec = time.perf_counter() - started
    restored_time = float(cmds.currentTime(query=True))
    if not math.isclose(entry_time, restored_time, rel_tol=0.0, abs_tol=1.0e-9):
        raise RuntimeError(
            f"currentTime was not restored: entry={entry_time} restored={restored_time}"
        )
    rows, strategy_counts = parse_packed_result(packed, plan)
    del rows
    packed_blob = _packed_bytes(packed)
    values_blob = _packed_bytes(packed[PACKED_HEADER_SIZE:])
    packed_path = _packed_path(config, strategy, prefix)
    packed_path.write_bytes(packed_blob)
    full_count = int(config["full_frame_count"])
    result = {
        "schema_version": SCHEMA_VERSION,
        "status": "pass",
        "strategy": strategy,
        "prefix_frames": prefix,
        "full_frame_count": full_count,
        "wall_sec": round(wall_sec, 6),
        "estimated_full_wall_sec": round(wall_sec * full_count / prefix, 6),
        "import_wall_sec": round(import_wall_sec, 6),
        "current_time": {
            "entry": entry_time,
            "restored": restored_time,
            "restored_exactly": entry_time == restored_time,
        },
        "packed": {
            "header": packed[:PACKED_HEADER_SIZE],
            "float_count": len(packed),
            "byte_count": len(packed_blob),
            "sha256": hashlib.sha256(packed_blob).hexdigest(),
            "values_sha256": hashlib.sha256(values_blob).hexdigest(),
            "artifact": str(packed_path),
        },
        "strategy_counts": strategy_counts,
        "route_inventory": inventory,
        "route_inventory_sha256": inventory_sha256,
        "assets": {
            "pmx_path": str(config["pmx_path"]),
            "pmx_sha256": _sha256_file(Path(config["pmx_path"])),
            "vmd_path": str(config["vmd_path"]),
            "vmd_sha256": _sha256_file(Path(config["vmd_path"])),
            "current_model_root": root,
        },
        "native": dict(gateway.last_diagnostics),
    }
    result_path = _result_path(config, strategy, prefix)
    result_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return result


def compare_pair(context: Mapping[str, Any], timeline: Mapping[str, Any]) -> dict[str, Any]:
    """Compare one isolated context/timeline pair using their binary artifacts."""

    context_path = Path(str(context["packed"]["artifact"]))
    timeline_path = Path(str(timeline["packed"]["artifact"]))
    context_blob = context_path.read_bytes()
    timeline_blob = timeline_path.read_bytes()
    header_bytes = PACKED_HEADER_SIZE * 8
    values_exact = context_blob[header_bytes:] == timeline_blob[header_bytes:]
    return {
        "prefix_frames": int(context["prefix_frames"]),
        "packed_sha256_equal": context["packed"]["sha256"] == timeline["packed"]["sha256"],
        "values_sha256_equal": context["packed"]["values_sha256"]
        == timeline["packed"]["values_sha256"],
        "packed_binary_exactly_equal": context_blob == timeline_blob,
        "packed_values_exactly_equal": values_exact,
        "strategy_header_equal": context["packed"]["header"] == timeline["packed"]["header"],
        "route_inventory_equal": context["route_inventory_sha256"]
        == timeline["route_inventory_sha256"],
        "context_wall_sec": float(context["wall_sec"]),
        "timeline_probe_wall_sec": float(timeline["wall_sec"]),
        "timeline_over_context_ratio": round(
            float(timeline["wall_sec"]) / float(context["wall_sec"]), 6
        )
        if float(context["wall_sec"]) > 0.0
        else None,
    }


def estimate_full_wall(results: Sequence[Mapping[str, Any]], full_frame_count: int) -> float:
    """Return the least-squares-through-origin prefix extrapolation."""

    denominator = sum(float(item["prefix_frames"]) ** 2 for item in results)
    if denominator <= 0.0:
        raise ValueError("prefix results are empty")
    slope = sum(
        float(item["prefix_frames"]) * float(item["wall_sec"])
        for item in results
    ) / denominator
    return round(slope * int(full_frame_count), 6)


def _run_controller(config_path: Path, config: Mapping[str, Any]) -> dict[str, Any]:
    out_dir = Path(config["out_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    mayapy = config.get("mayapy") or sys.executable
    results: dict[tuple[str, int], dict[str, Any]] = {}
    launches = []
    for prefix in config["prefix_frames"]:
        for strategy in STRATEGIES:
            stem = _result_stem(strategy, prefix)
            stdout_path = out_dir / f"{stem}.stdout.log"
            stderr_path = out_dir / f"{stem}.stderr.log"
            command = [
                str(mayapy),
                str(Path(__file__).resolve()),
                "--config",
                str(config_path),
                "--worker",
                "--strategy",
                strategy,
                "--prefix",
                str(prefix),
            ]
            environment = dict(os.environ)
            environment["PYTHONUTF8"] = "1"
            environment.setdefault("MMD_TOOLS_CPP_SKIP_NATIVE_CASTER", "1")
            try:
                completed = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    env=environment,
                    timeout=float(config["worker_timeout_sec"]),
                )
            except subprocess.TimeoutExpired as exc:
                stdout_path.write_text(str(exc.stdout or ""), encoding="utf-8")
                stderr_path.write_text(str(exc.stderr or ""), encoding="utf-8")
                raise RuntimeError(
                    f"probe worker timed out: strategy={strategy} prefix={prefix} "
                    f"timeout_sec={config['worker_timeout_sec']}"
                ) from exc
            stdout_path.write_text(completed.stdout, encoding="utf-8")
            stderr_path.write_text(completed.stderr, encoding="utf-8")
            launches.append(
                {
                    "strategy": strategy,
                    "prefix_frames": prefix,
                    "return_code": completed.returncode,
                    "stdout": str(stdout_path),
                    "stderr": str(stderr_path),
                }
            )
            if completed.returncode != 0:
                raise RuntimeError(
                    f"probe worker failed: strategy={strategy} prefix={prefix} "
                    f"return_code={completed.returncode}; stderr={stderr_path}"
                )
            result = json.loads(_result_path(config, strategy, prefix).read_text(encoding="utf-8"))
            results[(strategy, prefix)] = result
    pairs = [
        compare_pair(results[("context", prefix)], results[("timeline_probe", prefix)])
        for prefix in config["prefix_frames"]
    ]
    parity = all(
        item["packed_values_exactly_equal"]
        and item["packed_sha256_equal"]
        and item["values_sha256_equal"]
        and item["strategy_header_equal"]
        and item["route_inventory_equal"]
        for item in pairs
    )
    estimates = {
        strategy: estimate_full_wall(
            [results[(strategy, prefix)] for prefix in config["prefix_frames"]],
            int(config["full_frame_count"]),
        )
        for strategy in STRATEGIES
    }
    summary = {
        "schema_version": SCHEMA_VERSION,
        "status": "pass" if parity else "fail",
        "fresh_process_per_strategy_prefix": True,
        "prefix_frames": list(config["prefix_frames"]),
        "full_frame_count": int(config["full_frame_count"]),
        "estimated_full_wall_sec": estimates,
        "packed_values_parity": parity,
        "pairs": pairs,
        "launches": launches,
        "results": [
            results[(strategy, prefix)]
            for prefix in config["prefix_frames"]
            for strategy in STRATEGIES
        ],
    }
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    args = _arguments(argv)
    config = load_config(args.config)
    if args.worker:
        if args.strategy is None or args.prefix is None:
            raise ProbeConfigurationError("worker requires --strategy and --prefix")
        result = _run_worker(config, args.strategy, args.prefix)
    else:
        if args.strategy is not None or args.prefix is not None:
            raise ProbeConfigurationError("--strategy/--prefix require --worker")
        result = _run_controller(args.config, config)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("status") == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
