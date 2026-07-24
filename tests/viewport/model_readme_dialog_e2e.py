"""Maya GUI E2E gate for the PMX/PMD model-readme dialog.

The host launches a real Maya GUI through the shared commandPort helper.  The
Maya-side probe imports the redistributable YW fixture, opens the real modal
through both production presentation entry points, captures its selectable
plain text, and closes it with a Qt timer so the gate remains unattended.
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tests.common import maya_commandport


COMPLETION_MARKER = "//-- MODEL README E2E FINISHED --//"
DEFAULT_PORT = 7731
TIMEOUT = 240


def run_probe(log_path: str, report_path: str, model_path: str) -> None:
    """Run inside Maya GUI and write a machine-readable gate report."""
    import maya.cmds as cmds

    def log(message: object) -> None:
        with open(log_path, "a", encoding="utf-8") as handle:
            handle.write(str(message) + "\n")

    report = {
        "status": "fail",
        "mayaVersion": str(cmds.about(version=True)),
        "batch": bool(cmds.about(batch=True)),
        "checks": {},
    }
    try:
        from mmd_tools.io.mmd_importer import import_mmd_file
        from mmd_tools.services.scene_model_service import SceneModelService
        from mmd_tools.ui.drag_drop_importer import import_dropped_files
        from mmd_tools.ui.model_readme_dialog import (
            ModelReadme,
            ModelReadmeDialogAdapter,
            read_model_readme,
        )
        from mmd_tools.ui.presenters.import_export_presenter import ImportExportPresenter
        from mmd_tools.ui.qt_compat import QApplication, QDialog, QTextEdit, QTimer

        if report["batch"]:
            raise RuntimeError("probe must run in Maya GUI, not batch mode")

        class CapturingAdapter(ModelReadmeDialogAdapter):
            def __init__(self, **kwargs):
                super().__init__(**kwargs)
                self.captures = []

            def _capture_and_close(self) -> None:
                dialogs = [
                    widget
                    for widget in QApplication.topLevelWidgets()
                    if isinstance(widget, QDialog) and widget.windowTitle() == "MMD Model Readme"
                ]
                if len(dialogs) != 1:
                    self.captures.append({"error": f"expected one dialog, got {len(dialogs)}"})
                    for dialog in dialogs:
                        dialog.reject()
                    return
                dialog = dialogs[0]
                text_edits = dialog.findChildren(QTextEdit)
                self.captures.append(
                    {
                        "title": dialog.windowTitle(),
                        "text": text_edits[0].toPlainText() if len(text_edits) == 1 else "",
                        "textEditCount": len(text_edits),
                        "readOnly": bool(text_edits[0].isReadOnly()) if len(text_edits) == 1 else False,
                    }
                )
                dialog.accept()

            def show(self, readme=None, *, model_path="", parent=None):
                QTimer.singleShot(50, self._capture_and_close)
                return super().show(readme, model_path=model_path, parent=parent)

        class ProbeSettings:
            @staticmethod
            def is_development_mode() -> bool:
                return False

            @staticmethod
            def build_pmx_import_options(custom_namespace=None):
                del custom_namespace
                return {
                    "use_namespace": False,
                    "setup_rig": False,
                    "import_physics": False,
                    "create_mmd_shaders": False,
                    "use_native_pmx_parse": False,
                    "require_native_pmx_parse": False,
                }

        model = Path(model_path)
        if not model.is_file():
            raise FileNotFoundError(model)

        # Import/Export presenter path: production import metadata -> shared reader -> real modal.
        cmds.file(new=True, force=True)
        root = import_mmd_file(str(model), options=ProbeSettings.build_pmx_import_options())
        if not root:
            raise RuntimeError("production import returned no model root")
        scene_service = SceneModelService()
        readme = read_model_readme(scene_service, root)
        if readme is None or not readme.has_content:
            raise RuntimeError("fixture import did not preserve a model readme")

        presenter_adapter = CapturingAdapter(development_mode_getter=lambda: False)
        presenter = ImportExportPresenter.__new__(ImportExportPresenter)
        presenter.app_state = type("ProbeState", (), {"scene_model_service": scene_service})()
        presenter.model_readme_adapter = presenter_adapter
        presenter.view = None
        presenter._maybe_show_model_readme(root, str(model))
        expected_text = readme.to_plain_text()
        presenter_ok = (
            len(presenter_adapter.captures) == 1
            and presenter_adapter.captures[0].get("text") == expected_text
            and presenter_adapter.captures[0].get("readOnly") is True
        )
        report["checks"]["importExportPresenter"] = {
            "passed": presenter_ok,
            "capture": presenter_adapter.captures,
        }

        # Drag-and-drop production helper path performs its own import and modal call.
        cmds.file(new=True, force=True)
        drop_adapter = CapturingAdapter(development_mode_getter=lambda: False)
        drop_ok = import_dropped_files(
            [str(model)],
            settings_service=ProbeSettings(),
            scene_model_service=SceneModelService(),
            model_readme_adapter=drop_adapter,
        )
        drop_passed = (
            drop_ok is True
            and len(drop_adapter.captures) == 1
            and drop_adapter.captures[0].get("text") == expected_text
            and drop_adapter.captures[0].get("readOnly") is True
        )
        report["checks"]["dragAndDrop"] = {
            "passed": drop_passed,
            "capture": drop_adapter.captures,
        }

        # Policy gates must suppress the modal without scheduling or finding a dialog.
        sample = ModelReadme(japanese="日本語", english="English")
        policy_results = {
            "developmentMode": ModelReadmeDialogAdapter(
                development_mode_getter=lambda: True,
                batch_getter=lambda: False,
            ).show(sample),
            "batch": ModelReadmeDialogAdapter(
                development_mode_getter=lambda: False,
                batch_getter=lambda: True,
            ).show(sample),
            "explicitSkip": ModelReadmeDialogAdapter(
                development_mode_getter=lambda: False,
                batch_getter=lambda: False,
                enabled=False,
            ).show(sample),
            "empty": ModelReadmeDialogAdapter(
                development_mode_getter=lambda: False,
                batch_getter=lambda: False,
            ).show(ModelReadme()),
        }
        policy_passed = all(value is False for value in policy_results.values())
        report["checks"]["policy"] = {
            "passed": policy_passed,
            "results": policy_results,
        }

        report["status"] = (
            "pass"
            if presenter_ok and drop_passed and policy_passed
            else "fail"
        )
    except Exception as exc:
        report["error"] = str(exc)
        report["traceback"] = traceback.format_exc()
        log(report["traceback"])
    finally:
        Path(report_path).write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        log(json.dumps(report, ensure_ascii=False))
        log(COMPLETION_MARKER)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--maya", choices=("2024", "2026"), default="2026")
    parser.add_argument("--model", default="tests/data/yw_test_model.pmx")
    parser.add_argument("--out", default="build/reports/model_readme_dialog_e2e.json")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--launch-mode",
        choices=("direct", "powershell", "explorer"),
        default="explorer",
    )
    args = parser.parse_args()

    model = Path(args.model)
    if not model.is_absolute():
        model = (PROJECT_ROOT / model).resolve()
    report = Path(args.out)
    if not report.is_absolute():
        report = (PROJECT_ROOT / report).resolve()
    report.parent.mkdir(parents=True, exist_ok=True)
    run_dir = report.parent / f"model_readme_e2e_maya{args.maya}"
    run_dir.mkdir(parents=True, exist_ok=True)
    log_path = run_dir / "probe.log"
    maya_commandport.remove_stale_logs([report, log_path])
    if maya_commandport.is_port_open(args.port):
        raise RuntimeError(f"commandPort :{args.port} is already open")

    process = maya_commandport.launch_maya(
        version=args.maya,
        project_root=PROJECT_ROOT,
        output_dir=run_dir,
        port=args.port,
        launch_mode=args.launch_mode,
        env_overrides={"MAYA_VERSION": args.maya},
    )
    try:
        maya_commandport.wait_for_port(args.port, timeout=120, process=process)
        command = (
            "import sys\n"
            "from pathlib import Path\n"
            f"root = Path(r'{PROJECT_ROOT.as_posix()}')\n"
            "if str(root) not in sys.path:\n"
            "    sys.path.insert(0, str(root))\n"
            "from tests.viewport.model_readme_dialog_e2e import run_probe\n"
            f"run_probe(r'{log_path.as_posix()}', r'{report.as_posix()}', r'{model.as_posix()}')\n"
        )
        maya_commandport.send_python(args.port, command, label="<model-readme-e2e>")
        if not maya_commandport.tail_until_marker(log_path, COMPLETION_MARKER, TIMEOUT):
            raise TimeoutError(f"model readme probe did not finish within {TIMEOUT}s")
        result = json.loads(report.read_text(encoding="utf-8"))
        return 0 if result.get("status") == "pass" else 1
    finally:
        maya_commandport.quit_maya(args.port)
        try:
            maya_commandport.wait_for_port_close(args.port, timeout=30)
        except TimeoutError:
            pass
        if process is not None:
            try:
                process.wait(timeout=30)
            except Exception:
                process.kill()
        maya_commandport.close_process_logs(process)


if __name__ == "__main__":
    raise SystemExit(main())
