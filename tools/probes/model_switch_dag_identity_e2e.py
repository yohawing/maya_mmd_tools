"""Maya GUI E2E for canonical DAG identity during two-model switching.

The probe imports the same PMX twice under distinct namespaces, opens the real
MMD Tools main window, and alternates the Header model combo between the two
canonical roots.  Every switch refreshes the list and forces a model-info cache
miss so a stale or ambiguous descendant identity cannot look like a pass.

Run this file with a normal Python interpreter.  The host side launches an
isolated Maya GUI through the repository commandPort harness::

    python tools/probes/model_switch_dag_identity_e2e.py --maya 2024 --separate true
    python tools/probes/model_switch_dag_identity_e2e.py --maya 2026 --separate false
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MODEL = PROJECT_ROOT / "tests" / "data" / "mmt_test_model.pmx"
DEFAULT_OUT_DIR = PROJECT_ROOT / "build" / "reports" / "model_switch_dag_identity"
COMMAND_PORT = 7771
COMPLETION_MARKER = "//-- MODEL_SWITCH_DAG_IDENTITY_E2E_DONE --//"
SCHEMA_VERSION = 1
COUNT_KEYS = ("vertex_count", "material_count", "bone_count", "morph_count")


def _parse_bool(value: str) -> bool:
    normalized = str(value).strip().lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise argparse.ArgumentTypeError("expected true or false")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--maya", default="2024", help="Maya GUI version.")
    parser.add_argument("--model", default=str(DEFAULT_MODEL), help="PMX imported into both namespaces.")
    parser.add_argument(
        "--separate",
        type=_parse_bool,
        default=False,
        metavar="true|false",
        help="Set import.model.separate_meshes_by_material for both imports.",
    )
    parser.add_argument("--switches", type=int, default=6, help="Number of alternating A/B UI selections.")
    parser.add_argument("--namespace-a", default="__dag_identity_a__")
    parser.add_argument("--namespace-b", default="__dag_identity_b__")
    parser.add_argument("--port", type=int, default=COMMAND_PORT)
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    return parser.parse_args()


def _log(log_file: Path, message: str) -> None:
    encoded = str(message)
    print(encoded)
    log_file.parent.mkdir(parents=True, exist_ok=True)
    with log_file.open("a", encoding="utf-8") as handle:
        handle.write(encoded + "\n")


def _write_report(path: Path, report: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _count_summary(info: Mapping[str, Any], expected_root: str) -> Dict[str, int]:
    if str(info.get("root")) != expected_root:
        raise AssertionError(
            f"model info root mismatch: expected={expected_root!r} actual={info.get('root')!r}"
        )
    counts = {key: int(info.get(key, -1)) for key in COUNT_KEYS}
    if counts["vertex_count"] <= 0:
        raise AssertionError(f"model has no vertices: {expected_root!r}")
    if counts["material_count"] <= 0:
        raise AssertionError(f"model has no materials: {expected_root!r}")
    if counts["bone_count"] <= 0:
        raise AssertionError(f"model has no bones: {expected_root!r}")
    if counts["morph_count"] < 0:
        raise AssertionError(f"model morph count is invalid: {expected_root!r}")
    return counts


def _combo_roots(combo: Any) -> List[Optional[str]]:
    return [
        None if combo.itemData(index) is None else str(combo.itemData(index))
        for index in range(combo.count())
    ]


def _combo_index(combo: Any, root: str) -> int:
    matches = [index for index in range(combo.count()) if str(combo.itemData(index)) == root]
    if len(matches) != 1:
        raise AssertionError(f"Header combo root is not unique: root={root!r} matches={matches!r}")
    return matches[0]


def _long_shape_paths(cmds: Any, root: str) -> List[str]:
    shapes = cmds.listRelatives(root, allDescendents=True, type="mesh", fullPath=True) or []
    long_shapes = [str(shape) for shape in shapes]
    if not long_shapes or any(not shape.startswith("|") for shape in long_shapes):
        raise AssertionError(f"mesh descendants are not canonical long paths: {root!r} -> {long_shapes!r}")
    return sorted(long_shapes)


def _material_split_nodes(cmds: Any, root: str, expected: bool) -> List[str]:
    """Verify the requested import mode against authored scene metadata."""

    transforms = cmds.listRelatives(
        root,
        allDescendents=True,
        type="transform",
        fullPath=True,
    ) or []
    split_nodes = [
        str(node)
        for node in transforms
        if cmds.attributeQuery("mmd_material_split_mesh", node=node, exists=True)
        and bool(cmds.getAttr(f"{node}.mmd_material_split_mesh"))
    ]
    if expected and not split_nodes:
        raise AssertionError(f"material-split import authored no split mesh metadata: {root!r}")
    if not expected and split_nodes:
        raise AssertionError(
            f"unified import unexpectedly authored split mesh metadata: {root!r} -> {split_nodes!r}"
        )
    return sorted(split_nodes)


def _start_command_output_capture() -> Dict[str, Any]:
    messages: List[Dict[str, Any]] = []
    try:
        import maya.api.OpenMaya as om

        error_type = int(om.MCommandMessage.kError)

        def _callback(message, message_type, _client_data):
            messages.append({"type": int(message_type), "message": str(message)})

        callback_id = om.MCommandMessage.addCommandOutputCallback(_callback)
    except Exception as exc:
        return {
            "enabled": False,
            "callback": None,
            "errorType": None,
            "messages": messages,
            "error": str(exc),
        }
    return {
        "enabled": True,
        "callback": callback_id,
        "errorType": error_type,
        "messages": messages,
        "error": None,
    }


def _stop_command_output_capture(state: Mapping[str, Any]) -> Dict[str, Any]:
    remove_error = None
    callback_id = state.get("callback")
    if callback_id is not None:
        try:
            import maya.api.OpenMaya as om

            om.MMessage.removeCallback(callback_id)
        except Exception as exc:
            remove_error = str(exc)
    messages = list(state.get("messages") or [])
    error_type = state.get("errorType")
    errors = [
        str(row.get("message", ""))
        for row in messages
        if error_type is not None and int(row.get("type", -1)) == int(error_type)
    ]
    return {
        "enabled": bool(state.get("enabled")),
        "messageCount": len(messages),
        "errorCount": len(errors),
        "errors": errors[-200:],
        "registrationError": state.get("error"),
        "removeError": remove_error,
    }


def _assert_root_set(actual: Sequence[str], expected: Sequence[str], label: str) -> None:
    if len(actual) != len(expected) or set(actual) != set(expected):
        raise AssertionError(f"{label} mismatch: expected={list(expected)!r} actual={list(actual)!r}")


def run_probe(
    log_path: str,
    model_path: str,
    report_path: str,
    separate_meshes_by_material: bool,
    switches: int,
    namespace_a: str,
    namespace_b: str,
) -> None:
    """Run the Maya-side UI probe and always emit a machine-readable report."""

    from maya import cmds

    from tools.probes.model_selection_sync_benchmark import _full_root, _safe_process_events

    log_file = Path(log_path)
    report_file = Path(report_path)
    report: Dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "status": "error",
        "mayaVersion": str(cmds.about(version=True)),
        "model": str(Path(model_path).resolve()),
        "separateMeshesByMaterial": bool(separate_meshes_by_material),
        "namespaces": [str(namespace_a), str(namespace_b)],
        "models": {},
        "listRefresh": {},
        "switches": [],
        "mayaCommandOutput": {},
        "errors": [],
    }
    main_window = None
    capture: Optional[Dict[str, Any]] = None
    try:
        if bool(cmds.about(batch=True)):
            raise RuntimeError("model switch DAG identity E2E requires Maya GUI, not batch mayapy")
        model_file = Path(model_path)
        if not model_file.is_file():
            raise FileNotFoundError(model_file)
        if switches < 2:
            raise ValueError("switches must be >= 2")
        if not namespace_a or not namespace_b or namespace_a == namespace_b:
            raise ValueError("two distinct non-empty namespaces are required")
        cmds.file(new=True, force=True)
        from mmd_tools.io.mmd_importer import import_mmd_file

        imported: List[str] = []
        for namespace in (namespace_a, namespace_b):
            profile: Dict[str, Any] = {}
            root = import_mmd_file(
                str(model_file.resolve()),
                options={
                    "custom_namespace": namespace,
                    "use_namespace": True,
                    "setup_rig": False,
                    "setup_bone_orientation": True,
                    "import_physics": False,
                    "import_morphs": True,
                    "separate_meshes_by_material": bool(separate_meshes_by_material),
                    "profile": profile,
                },
            )
            imported.append(_full_root(cmds, str(root)))
        if len(set(imported)) != 2 or any(":" not in root for root in imported):
            raise AssertionError(f"expected two distinct namespaced roots, got {imported!r}")

        from mmd_tools.services.scene_model_service import SceneModelService

        service = SceneModelService(cmds_module=cmds)
        canonical = [service.canonical_node(root) for root in imported]
        if canonical != imported:
            raise AssertionError(f"canonical root identity mismatch: imported={imported!r} canonical={canonical!r}")
        discovered = [_full_root(cmds, str(root)) for root in service.list_mmd_models()]
        _assert_root_set(discovered, imported, "SceneModelService model list")

        shape_paths = {root: _long_shape_paths(cmds, root) for root in imported}
        split_nodes = {
            root: _material_split_nodes(cmds, root, bool(separate_meshes_by_material))
            for root in imported
        }
        shape_names = {
            root: sorted(shape.rsplit("|", 1)[-1].rsplit(":", 1)[-1] for shape in paths)
            for root, paths in shape_paths.items()
        }
        if shape_names[imported[0]] != shape_names[imported[1]]:
            raise AssertionError(
                "same PMX did not produce matching unqualified shape identities: "
                f"{shape_names!r}"
            )
        report["models"] = {
            "roots": list(imported),
            "canonicalRoots": list(canonical),
            "discoveredRoots": sorted(discovered),
            "distinctCanonicalLongIdentities": True,
            "meshLongPathsByRoot": shape_paths,
            "materialSplitNodesByRoot": split_nodes,
            "materialSplitModeVerified": True,
            "sharedUnqualifiedShapeNames": shape_names[imported[0]],
        }

        from mmd_tools.plugin_main import close_main_window, open_main_window

        main_window = open_main_window(dockable=False)
        if main_window is None:
            raise RuntimeError("MMD Tools main window did not open")
        _safe_process_events()

        app_state = main_window.app_state
        header = main_window.header_widget
        combo = header.model_combo
        app_service = app_state.scene_model_service
        initial_roots = [root for root in _combo_roots(combo) if root is not None]
        _assert_root_set(initial_roots, imported, "initial Header combo")

        # Import and window initialization may emit unrelated host/plugin
        # diagnostics.  This task's error oracle covers only the actual model
        # switching, refresh, selection-follow, and cache-readback window.
        capture = _start_command_output_capture()
        if not capture.get("enabled"):
            raise RuntimeError(
                "Maya command output capture could not be registered: "
                f"{capture.get('error') or 'unknown error'}"
            )

        info_calls: List[str] = []
        original_get_model_info = app_service.get_model_info

        def _tracked_get_model_info(root):
            info_calls.append(str(root))
            return original_get_model_info(root)

        app_service.get_model_info = _tracked_get_model_info
        baseline_counts: Optional[Dict[str, int]] = None
        for index in range(switches):
            target = imported[index % len(imported)]
            before_calls = len(info_calls)
            combo.setCurrentIndex(_combo_index(combo, target))
            cmds.select(target, replace=True)
            _safe_process_events()

            selected = [str(node) for node in (cmds.ls(selection=True, long=True) or [])]
            resolved = app_service.resolve_model_from_selection(app_state.available_models)
            resolved = _full_root(cmds, str(resolved)) if resolved else None
            current_combo_root = combo.itemData(combo.currentIndex())
            current_combo_root = None if current_combo_root is None else str(current_combo_root)
            current_state_root = str(app_state.current_model_root or "")
            if selected != [target]:
                raise AssertionError(f"Maya selection mismatch: expected={[target]!r} actual={selected!r}")
            if resolved != target:
                raise AssertionError(f"selection resolution mismatch: expected={target!r} actual={resolved!r}")
            if current_combo_root != target or current_state_root != target:
                raise AssertionError(
                    "UI switch target mismatch: "
                    f"expected={target!r} combo={current_combo_root!r} state={current_state_root!r}"
                )

            cached_info = app_state.get_model_info(target)
            if not isinstance(cached_info, Mapping):
                raise AssertionError(f"cached model info is missing for {target!r}: {cached_info!r}")
            cached_counts = _count_summary(cached_info, target)
            cache_before_clear = sorted(str(key) for key in app_state._model_info_cache)
            if target not in cache_before_clear:
                raise AssertionError(
                    f"ApplicationState did not cache the selected root: {target!r} -> {cache_before_clear!r}"
                )
            app_state.clear_cache()
            if app_state._model_info_cache:
                raise AssertionError("ApplicationState.clear_cache() left cached model info")
            calls_before_refetch = len(info_calls)
            refetched_info = app_state.get_model_info(target)
            if not isinstance(refetched_info, Mapping):
                raise AssertionError(f"refetched model info is missing for {target!r}: {refetched_info!r}")
            refetched_counts = _count_summary(refetched_info, target)
            if len(info_calls) != calls_before_refetch + 1 or info_calls[-1] != target:
                raise AssertionError(
                    "cache miss did not call SceneModelService.get_model_info exactly once: "
                    f"target={target!r} calls={info_calls[calls_before_refetch:]!r}"
                )
            if cached_counts != refetched_counts:
                raise AssertionError(
                    f"cached/refetched model counts differ for {target!r}: "
                    f"{cached_counts!r} != {refetched_counts!r}"
                )
            if baseline_counts is None:
                baseline_counts = dict(refetched_counts)
            elif refetched_counts != baseline_counts:
                raise AssertionError(
                    "same PMX produced unstable model counts across A/B switches: "
                    f"baseline={baseline_counts!r} actual={refetched_counts!r}"
                )

            generation_before = int(app_state.refresh_generation)
            header.refresh_btn.click()
            _safe_process_events()
            refreshed_roots = [root for root in _combo_roots(combo) if root is not None]
            _assert_root_set(refreshed_roots, imported, f"Header refresh {index}")
            refreshed_combo_root = combo.itemData(combo.currentIndex())
            refreshed_combo_root = None if refreshed_combo_root is None else str(refreshed_combo_root)
            if str(app_state.current_model_root or "") != target or refreshed_combo_root != target:
                raise AssertionError(
                    "Header refresh changed current model identity: "
                    f"expected={target!r} combo={refreshed_combo_root!r} "
                    f"state={app_state.current_model_root!r}"
                )
            if int(app_state.refresh_generation) != generation_before + 1:
                raise AssertionError(
                    "Header refresh generation did not advance exactly once: "
                    f"before={generation_before} after={app_state.refresh_generation}"
                )

            report["switches"].append(
                {
                    "index": index,
                    "targetRoot": target,
                    "selectedLongRoots": selected,
                    "resolvedRoot": resolved,
                    "comboRoot": current_combo_root,
                    "applicationStateRoot": current_state_root,
                    "cacheKeysBeforeClear": cache_before_clear,
                    "cacheRefetched": True,
                    "cachedCounts": cached_counts,
                    "refetchedCounts": refetched_counts,
                    "serviceCallsDuringSwitch": info_calls[before_calls:],
                    "refreshGenerationBefore": generation_before,
                    "refreshGenerationAfter": int(app_state.refresh_generation),
                    "refreshedComboRoots": refreshed_roots,
                    "refreshedCurrentRoot": str(app_state.current_model_root or ""),
                    "status": "pass",
                }
            )

        report["listRefresh"] = {
            "initialRoots": initial_roots,
            "finalRoots": [root for root in _combo_roots(combo) if root is not None],
            "refreshCount": switches,
            "finalGeneration": int(app_state.refresh_generation),
            "status": "pass",
        }
        report["models"]["stableCounts"] = baseline_counts
        report["models"]["sceneModelInfoCalls"] = list(info_calls)
        report["status"] = "pass"
        close_main_window()
        main_window = None
        _safe_process_events()
    except Exception as exc:
        report["status"] = "error"
        report["errors"].append(f"{type(exc).__name__}: {exc}")
        report["traceback"] = traceback.format_exc()
        _log(log_file, "ERROR: " + report["errors"][-1])
    finally:
        if main_window is not None:
            try:
                from mmd_tools.plugin_main import close_main_window

                close_main_window()
            except Exception as exc:
                report["errors"].append(f"Main window cleanup failed: {exc}")
                report["status"] = "error"
        command_output = (
            _stop_command_output_capture(capture)
            if capture is not None
            else {
                "enabled": False,
                "messageCount": 0,
                "errorCount": 0,
                "errors": [],
                "registrationError": "capture did not start",
                "removeError": None,
            }
        )
        report["mayaCommandOutput"] = command_output
        if not command_output.get("enabled") and report.get("status") == "pass":
            report["status"] = "error"
            report["errors"].append(
                "Maya command output capture was unavailable: "
                f"{command_output.get('registrationError') or 'unknown error'}"
            )
        if command_output.get("removeError"):
            report["status"] = "error"
            report["errors"].append(
                f"Maya command output callback removal failed: {command_output['removeError']}"
            )
        if command_output.get("errorCount"):
            report["status"] = "error"
            report["errors"].append(
                f"Maya command output contained {command_output['errorCount']} error message(s)"
            )
        _write_report(report_file, report)
        _log(log_file, "RESULT_JSON: " + json.dumps(report, ensure_ascii=False, sort_keys=True))
        _log(log_file, COMPLETION_MARKER)


def main() -> int:
    """Launch the isolated Maya GUI and return the fail-closed report status."""

    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    from tests.viewport.maya_e2e_harness import run_maya_e2e

    args = _parse_args()
    model = Path(args.model).resolve()
    if not model.is_file():
        raise FileNotFoundError(model)
    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    mode = "split" if args.separate else "unified"
    suffix = f"maya{args.maya}-{mode}"
    report_path = out_dir / f"model_switch_dag_identity_{suffix}.json"
    log_path = out_dir / f"model_switch_dag_identity_{suffix}.log"
    command = (
        "import sys\n"
        "from pathlib import Path\n"
        f"project_root = Path({str(PROJECT_ROOT.as_posix())!r})\n"
        "sys.path.insert(0, str(project_root)) if str(project_root) not in sys.path else None\n"
        "from tools.probes.model_switch_dag_identity_e2e import run_probe\n"
        f"run_probe({str(log_path.as_posix())!r}, {str(model.as_posix())!r}, "
        f"{str(report_path.as_posix())!r}, {bool(args.separate)!r}, {int(args.switches)}, "
        f"{str(args.namespace_a)!r}, {str(args.namespace_b)!r})\n"
    )
    report = run_maya_e2e(
        project_root=PROJECT_ROOT,
        version=args.maya,
        out_dir=out_dir,
        port=args.port,
        timeout=args.timeout,
        log_path=log_path,
        report_path=report_path,
        command=command,
        marker=COMPLETION_MARKER,
        send_label="<model-switch-dag-identity-e2e>",
        stale_paths=(report_path, log_path),
        terminate_process=True,
        quit_delay=3.0,
        port_error=f"commandPort :{args.port} is already open; choose another --port",
        report_error=f"model switch DAG identity report missing: {report_path}",
        env_overrides={"MAYA_SKIP_USERSETUP_PY": "1"},
    )
    return 0 if report.get("status") == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
