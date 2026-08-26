"""Nox orchestration for the compact Authoring cross-Maya matrix."""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Callable

from tools.gates import authoring_cross_maya_gate as gate


HEADLESS_QT_REQUIREMENT = "PySide6==6.11.0"


def _output_directory(root: Path, value: str) -> Path:
    output = (root / value).resolve()
    try:
        output.relative_to(root.resolve())
    except ValueError as exc:
        raise gate.CrossMayaGateError("--out-dir must remain inside the repository") from exc
    output.mkdir(parents=True, exist_ok=True)
    return output


def _remove_artifacts(paths) -> None:
    for path in paths:
        try:
            Path(path).unlink()
        except FileNotFoundError:
            pass


def _run_logged_checked(
    session,
    run_logged: Callable[..., Any],
    command: list[str],
    *,
    log_path: Path,
    root: Path,
    env=None,
    verbose: bool = False,
) -> tuple[int, Path]:
    started_ns = time.time_ns()
    returncode, resolved_log, _warnings = run_logged(
        command,
        log_path=log_path,
        cwd=root,
        env=env,
        verbose=verbose,
    )
    if returncode != 0:
        session.error("Authoring cross-Maya command failed: {} (log: {})".format(command[0], resolved_log))
    return started_ns, resolved_log


def run_authoring_cross_maya_matrix(
    session,
    *,
    posargs: list[str],
    root: Path,
    python_executable: str,
    configure,
    build,
    mayapy,
    mayapy_env,
    mayapy_script,
    run_logged,
) -> Path:
    """Run one selected matrix with exactly one headless and one GUI lane/version."""
    request = gate.parse_request(posargs)
    output = _output_directory(root, request["out_dir"])
    aggregate_path = output / "authoring-cross-maya-report.json"
    _remove_artifacts((aggregate_path,))
    matrix = gate.load_matrix(root)
    plan = gate.build_plan(
        matrix,
        request["profile"],
        request["domain"],
        request["change_kind"],
    )
    source = gate.source_identity(root)
    if plan["require_clean_worktree"] and source["dirty_paths"]:
        session.error(
            "release_candidate requires a clean worktree; dirty paths: {}".format(
                ", ".join(source["dirty_paths"])
            )
        )

    report = gate.new_report(plan, source)
    gui_manifest_path = output / "gui-batch-manifest.json"
    _remove_artifacts((gui_manifest_path,))
    gate.write_json(gui_manifest_path, gate.gui_batch_manifest(plan))

    headless_log = output / "headless-ui-surface-matrix.log"
    headless_junit = output / "headless-ui-surface-matrix.xml"
    _remove_artifacts((headless_log, headless_junit))
    headless_started, resolved_headless_log = _run_logged_checked(
        session,
        run_logged,
        [
            "uvx",
            "--with",
            "pytest",
            "--with",
            HEADLESS_QT_REQUIREMENT,
            "--",
            "python",
            "-m",
            "pytest",
            "tests/unit/test_authoring_ui_surface_matrix.py",
            "-q",
            "--junitxml={}".format(headless_junit),
        ],
        log_path=headless_log,
        root=root,
        verbose=request["verbose"],
    )
    headless_result = gate.validate_headless_junit(headless_junit, root, matrix)
    report["headless"] = {
        "case_id": matrix["surface_trace"]["version_independent_owner"],
        "test_count": headless_result["test_count"],
        "surface_test_count": headless_result["surface_test_count"],
        "test_identities": headless_result["test_identities"],
        "log": gate.artifact_identity(resolved_headless_log, headless_started, "headless_log"),
        "report": gate.artifact_identity(headless_junit, headless_started, "headless_junit"),
    }

    requires_native = any(
        case.get("runner") == "mayapy" and case.get("requires_cpp_plugin") is True
        for case in plan["cases"]
    )
    selected_mayapy = [case for case in plan["cases"] if case.get("runner") == "mayapy"]
    for version in plan["versions"]:
        plugin_path = (root / "plug-ins" / version / "Debug" / "mmd_tools_cpp.mll").resolve()
        plugin = None
        native_build = None
        if requires_native:
            build_log = output / "native-build-{}.log".format(version)
            build_report = output / "native-build-{}.json".format(version)
            _remove_artifacts((build_log, build_report))
            build_started = time.time_ns()
            configure(session, version, "Debug")
            build(session, version, "Debug", clean_first=True)
            plugin = dict(gate.plugin_identity(plugin_path, version))
            plugin["artifact"] = gate.artifact_identity(
                plugin_path, build_started, "native_plugin_binary"
            )
            build_finished = time.time_ns()
            build_log.write_text(
                "Maya {} Debug native configure/build PASS\nplugin={}\nsha256={}\n".format(
                    version, plugin["path"], plugin["sha256"]
                ),
                encoding="utf-8",
            )
            gate.write_json(
                build_report,
                {
                    "schema_version": 1,
                    "maya_version": version,
                    "config": "Debug",
                    "clean_first": True,
                    "started_ns": build_started,
                    "finished_ns": build_finished,
                    "plugin": plugin,
                },
            )
            native_build = {
                "log": gate.artifact_identity(build_log, build_started, "native_build_log"),
                "report": gate.artifact_identity(
                    build_report, build_started, "native_build_report"
                ),
            }

        gui_log = output / "gui-{}.log".format(version)
        gui_timing = output / "gui-{}.timing.json".format(version)
        gui_host_log = output / "gui-{}-host.log".format(version)
        _remove_artifacts((gui_log, gui_timing, gui_host_log))
        gui_env = os.environ.copy()
        gui_env.update(
            {
                "MAYA_VERSION": version,
                "MMD_TOOLS_CPP_CONFIG": "Debug",
            }
        )
        if plugin is not None:
            gui_env["MMD_TOOLS_CPP_PLUGIN"] = plugin["path"]
        else:
            gui_env.pop("MMD_TOOLS_CPP_PLUGIN", None)
        gui_started, resolved_gui_host_log = _run_logged_checked(
            session,
            run_logged,
            [
                python_executable,
                "tests/run_gui_tests.py",
                "--maya_version",
                version,
                "--batch_manifest",
                str(gui_manifest_path),
                "--log_path",
                str(gui_log),
                "--timing_report",
                str(gui_timing),
                "--port",
                "79{}".format(version[-2:]),
                "--vp2_device_override",
                "VirtualDeviceDx11",
            ],
            log_path=gui_host_log,
            root=root,
            env=gui_env,
            verbose=request["verbose"],
        )
        timing_payload = gate.load_json(gui_timing)
        gate.validate_gui_timing_report(timing_payload, version, plan["cases"])
        version_evidence = {
            "maya_version": version,
            "native_plugin": plugin,
            "native_build": native_build,
            "gui": {
                "host_log": gate.artifact_identity(
                    resolved_gui_host_log, gui_started, "gui_host_log"
                ),
                "log": gate.artifact_identity(gui_log, gui_started, "gui_log"),
                "timing_report": gate.artifact_identity(
                    gui_timing, gui_started, "gui_timing_report"
                ),
                "case_ids": [
                    case["id"] for case in plan["cases"] if case.get("runner") == "gui_batch"
                ],
            },
            "mayapy": [],
        }

        mayapy_path = mayapy(version) if selected_mayapy else None
        native_env = None
        if selected_mayapy:
            if not mayapy_path.exists():
                session.error("mayapy not found: {}".format(mayapy_path))
            native_env = mayapy_env(
                mayapy_path,
                MAYA_VERSION=version,
                MAYA_SKIP_USERSETUP_PY="1",
                MMD_TOOLS_CPP_CONFIG="Debug",
            )
            if plugin is not None:
                native_env["MMD_TOOLS_CPP_PLUGIN"] = plugin["path"]
        for case in selected_mayapy:
            case_log = output / "{}-{}.log".format(case["id"].replace(".", "-"), version)
            _remove_artifacts((case_log,))
            script = mayapy_script(mayapy_path, case["script"])
            case_started, resolved_case_log = _run_logged_checked(
                session,
                run_logged,
                [str(mayapy_path), script],
                log_path=case_log,
                root=root,
                env=native_env,
                verbose=request["verbose"],
            )
            version_evidence["mayapy"].append(
                {
                    "case_id": case["id"],
                    "script": case["script"],
                    "requires_cpp_plugin": case["requires_cpp_plugin"],
                    "log": gate.artifact_identity(
                        resolved_case_log, case_started, "mayapy_log"
                    ),
                }
            )
        report["versions"].append(version_evidence)

    if [entry["maya_version"] for entry in report["versions"]] != list(plan["versions"]):
        raise gate.CrossMayaGateError("version execution order does not match the selected plan")
    ending_source = gate.source_identity(root)
    if ending_source != source:
        _remove_artifacts((aggregate_path,))
        raise gate.CrossMayaGateError("Git source identity changed during matrix execution")
    report["source_verified_at_end"] = ending_source
    report["status"] = "pass"
    report["finished_ns"] = time.time_ns()
    gate.write_json(aggregate_path, report)
    session.log("Authoring cross-Maya matrix PASS: {}".format(aggregate_path))
    return aggregate_path
