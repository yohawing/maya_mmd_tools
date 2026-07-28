"""Drag-and-drop import support for PMX/PMD/VMD/VPD files in Maya.

The module installs a Qt event filter on Maya's main window and routes dropped
MMD files through the importer entry points used by the UI.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, Iterable, List, Optional
from urllib.parse import unquote, urlparse

from maya import cmds
import maya.api.OpenMaya as om

from mmd_tools.actions.import_result import warnings_from_options
from mmd_tools.core.constants import ATTR_MMD_MODEL_NAME, ATTR_MMD_MODEL_NAME_EN
from mmd_tools.core.logger import get_logger
from mmd_tools.core.mmd_parser import parse_mmd_file
from mmd_tools.io.mmd_importer import import_mmd_file
from mmd_tools.io.vpd_importer import import_vpd_file
from mmd_tools.services.scene_model_service import SceneModelService
from mmd_tools.services.settings_service import SettingsService
from mmd_tools.ui.model_readme_dialog import ModelReadmeDialogAdapter, read_model_readme

logger = get_logger(__name__)

_MODEL_EXTENSIONS = {".pmx", ".pmd"}
_MOTION_EXTENSIONS = {".vmd"}
_POSE_EXTENSIONS = {".vpd"}
_SUPPORTED_EXTENSIONS = _MODEL_EXTENSIONS | _MOTION_EXTENSIONS | _POSE_EXTENSIONS
_DROP_FILTER = None


def _display_info(message: str) -> None:
    try:
        om.MGlobal.displayInfo(message)
    except Exception:
        logger.info(message)


def _display_warning(message: str) -> None:
    try:
        om.MGlobal.displayWarning(message)
    except Exception:
        logger.warning(message)


def _display_error(message: str) -> None:
    try:
        om.MGlobal.displayError(message)
    except Exception:
        logger.error(message)


def path_from_drop_url(value: str) -> str:
    """Convert a Qt/Maya drop URL or plain file path into a local path."""
    text = str(value or "").strip()
    if not text:
        return ""
    if text.startswith("file:"):
        parsed = urlparse(text)
        path = unquote(parsed.path or "")
        if parsed.netloc:
            path = f"//{parsed.netloc}{path}"
        if os.name == "nt" and path.startswith("/") and len(path) >= 3 and path[2] == ":":
            path = path[1:]
        return os.path.normpath(path)
    return os.path.normpath(unquote(text))


def supported_mmd_files(paths: Iterable[str]) -> List[str]:
    """Return existing PMX/PMD/VMD/VPD file paths in drop order."""
    result: List[str] = []
    seen = set()
    for raw_path in paths:
        file_path = path_from_drop_url(raw_path)
        if not file_path:
            continue
        suffix = Path(file_path).suffix.lower()
        if suffix not in _SUPPORTED_EXTENSIONS:
            continue
        norm = os.path.normcase(os.path.abspath(file_path))
        if norm in seen:
            continue
        if not os.path.isfile(file_path):
            continue
        seen.add(norm)
        result.append(file_path)
    return result


def _selected_model_root(scene_model_service: Optional[SceneModelService] = None) -> Optional[str]:
    service = scene_model_service or SceneModelService()
    for node in cmds.ls(selection=True, long=True) or []:
        root = service.get_parent_mmd_root(node) or node
        if (
            service.object_exists(root)
            and (
                service.attribute_exists(root, ATTR_MMD_MODEL_NAME)
                or service.attribute_exists(root, ATTR_MMD_MODEL_NAME_EN)
            )
        ):
            return root
    models = service.list_mmd_models()
    return models[0] if len(models) == 1 else None


def _missing_target_warning(scene_model_service: SceneModelService, asset_label: str) -> str:
    models = scene_model_service.list_mmd_models()
    if len(models) > 1:
        return (
            f"Maya MMD Tools: select one MMD model before dropping {asset_label} files; "
            f"{len(models)} models are loaded."
        )
    return f"Maya MMD Tools: load or drop a PMX/PMD model before dropping {asset_label} files."


def _partial_import_warning(asset_label: str, file_path: str, warnings: Iterable[object]) -> str:
    """Build a concise warning for a successful import with profile warnings."""
    warning_list = list(warnings or [])
    details = []
    for warning in warning_list:
        if isinstance(warning, dict):
            code = warning.get("code") or warning.get("reason") or warning.get("message")
            if code:
                details.append(str(code))
    suffix = ", ".join(details) if details else f"{len(warning_list)} warning(s)"
    return f"Maya MMD Tools: imported {asset_label} with warnings ({Path(file_path).name}: {suffix})."


def import_dropped_files(
    paths: Iterable[str],
    *,
    importer: Optional[Callable[..., object]] = None,
    pose_importer: Optional[Callable[..., object]] = None,
    parser: Optional[Callable[[str], object]] = None,
    settings_service: Optional[SettingsService] = None,
    scene_model_service: Optional[SceneModelService] = None,
    model_readme_adapter: Optional[ModelReadmeDialogAdapter] = None,
) -> bool:
    """Import dropped PMX/PMD/VMD files and apply dropped VPD pose files.

    Models are imported before motions.  If a model and VMD are dropped
    together, the VMD targets the last imported model and receives that PMX path
    for runtime bake.  If only VMD files are dropped, an existing selected MMD
    model root is used; without a loaded model the drop fails with a warning.
    VPD files are pose-apply operations: they target the selected/current MMD
    model or the model imported by the same drop, then key the current frame.
    """
    files = supported_mmd_files(paths)
    if not files:
        return False

    importer = importer or import_mmd_file
    pose_importer = pose_importer or import_vpd_file
    parser = parser or parse_mmd_file
    settings_service = settings_service or SettingsService()
    scene_model_service = scene_model_service or SceneModelService()
    model_readme_adapter = model_readme_adapter or ModelReadmeDialogAdapter(
        development_mode_getter=getattr(settings_service, "is_development_mode", lambda: False),
    )
    model_files = [p for p in files if Path(p).suffix.lower() in _MODEL_EXTENSIONS]
    motion_files = [p for p in files if Path(p).suffix.lower() in _MOTION_EXTENSIONS]
    pose_files = [p for p in files if Path(p).suffix.lower() in _POSE_EXTENSIONS]

    _display_info(f"Maya MMD Tools: importing dropped file(s): {len(files)}")
    last_model_root: Optional[str] = None
    last_model_path: Optional[str] = None
    imported_any = False

    for model_path in model_files:
        options = settings_service.build_pmx_import_options(custom_namespace=None)
        profile = {}
        options["profile"] = profile
        root = importer(model_path, options=options)
        if root:
            last_model_root = str(root)
            last_model_path = model_path
            imported_any = True
            try:
                cmds.select(root, replace=True)
            except Exception:
                pass
            warnings = warnings_from_options(options)
            if warnings:
                _display_warning(_partial_import_warning("model", model_path, warnings))
            else:
                _display_info(f"Maya MMD Tools: imported model {Path(model_path).name}")
            readme = read_model_readme(scene_model_service, root)
            if readme is not None:
                try:
                    model_readme_adapter.show(readme, model_path=model_path)
                except Exception as exc:
                    logger.error("Failed to show model readme for dropped model: %s", exc, exc_info=True)
        else:
            _display_warning(f"Maya MMD Tools: model import failed: {model_path}")

    for motion_path in motion_files:
        target_model = last_model_root or _selected_model_root(scene_model_service)
        if not target_model:
            _display_warning(_missing_target_warning(scene_model_service, "VMD motion"))
            continue
        options = settings_service.build_vmd_import_options(target_model)
        if last_model_path:
            options["pmx_path"] = last_model_path
        success = importer(motion_path, options=options)
        if success:
            imported_any = True
            warnings = warnings_from_options(options)
            if warnings:
                _display_warning(_partial_import_warning("motion", motion_path, warnings))
            else:
                _display_info(f"Maya MMD Tools: imported motion {Path(motion_path).name}")
        else:
            _display_warning(f"Maya MMD Tools: VMD import failed: {motion_path}")

    for pose_path in pose_files:
        target_model = last_model_root or _selected_model_root(scene_model_service)
        if not target_model:
            _display_warning(_missing_target_warning(scene_model_service, "VPD pose"))
            continue
        try:
            pose_data = parser(pose_path)
            success = bool(
                pose_importer(
                    pose_data,
                    pose_path,
                    {
                        "target_model": target_model,
                        "create_keyframe": True,
                    },
                )
            )
        except Exception as exc:
            logger.warning("VPD pose apply failed for dropped file '%s': %s", pose_path, exc, exc_info=True)
            success = False
        if success:
            imported_any = True
            _display_info(f"Maya MMD Tools: applied pose {Path(pose_path).name}")
        else:
            _display_warning(f"Maya MMD Tools: VPD pose apply failed: {pose_path}")

    return imported_any


class _MmdDropEventFilter:
    """Qt event filter installed on Maya's main window."""

    def __init__(self):
        from mmd_tools.ui.qt_compat import QObject

        class EventFilter(QObject):
            def eventFilter(inner_self, watched, event):  # noqa: N805
                try:
                    event_type = event.type()
                    qt_core = _qt_core()
                    if event_type in (qt_core.QEvent.DragEnter, qt_core.QEvent.DragMove):
                        if _event_supported_paths(event):
                            event.acceptProposedAction()
                            return True
                    if event_type == qt_core.QEvent.Drop:
                        paths = _event_supported_paths(event)
                        if paths:
                            event.acceptProposedAction()
                            cmds.evalDeferred(lambda: import_dropped_files(paths))
                            return True
                except Exception as exc:
                    logger.debug("MMD drop event filter ignored event: %s", exc, exc_info=True)
                return False

        self._filter = EventFilter()
        self._targets = []
        self._accept_drop_states = []

    def install(self) -> bool:
        window = _maya_main_window()
        if window is None:
            return False
        targets = self._drop_targets(window)
        if not targets:
            return False
        try:
            for target in targets:
                self._install_on_target(target)
            return True
        except Exception as exc:
            logger.warning("Failed to install MMD drag-and-drop importer: %s", exc)
            return False

    def uninstall(self) -> None:
        for target in self._targets:
            try:
                target.removeEventFilter(self._filter)
            except Exception:
                pass
        for target, previous in reversed(self._accept_drop_states):
            try:
                target.setAcceptDrops(previous)
            except Exception:
                pass
        self._targets = []
        self._accept_drop_states = []

    def _drop_targets(self, window):
        # Maya 2027 replaces parts of its Qt hierarchy when leaving the Home
        # screen. A global QApplication filter is stable on Maya 2024-2026 but
        # can re-enter transient wrapper creation on Maya 2027+, so keep the
        # conservative main-window-only path there.
        if _maya_version() >= 2027:
            return [window]
        try:
            from mmd_tools.ui.qt_compat import QApplication

            app = QApplication.instance()
        except Exception as exc:
            logger.debug("Failed to resolve QApplication for drop filtering: %s", exc, exc_info=True)
            app = None
        if app is None or app is window:
            return [window]
        return [app, window]

    def _install_on_target(self, target) -> None:
        if hasattr(target, "setAcceptDrops"):
            try:
                previous = bool(target.acceptDrops()) if hasattr(target, "acceptDrops") else False
                self._accept_drop_states.append((target, previous))
                target.setAcceptDrops(True)
            except Exception:
                pass
        target.installEventFilter(self._filter)
        self._targets.append(target)


def _qt_core():
    from mmd_tools.ui.qt_compat import QtCore

    return QtCore


def _maya_version() -> int:
    """Return the numeric Maya version, or zero when the host query is unavailable."""
    try:
        value = str(cmds.about(version=True)).strip()
        return int(value.split(".", 1)[0])
    except Exception:
        return 0


def _maya_main_window():
    try:
        import maya.OpenMayaUI as omui
        from mmd_tools.ui.qt_compat import wrapInstance

        ptr = omui.MQtUtil.mainWindow()
        if ptr is None:
            return None
        from mmd_tools.ui.qt_compat import QMainWindow

        return wrapInstance(int(ptr), QMainWindow)
    except Exception as exc:
        logger.debug("Failed to resolve Maya main window: %s", exc, exc_info=True)
        return None


def _event_supported_paths(event) -> List[str]:
    mime = event.mimeData()
    if not mime or not mime.hasUrls():
        return []
    paths = []
    for url in mime.urls():
        try:
            if hasattr(url, "toLocalFile"):
                paths.append(url.toLocalFile())
            else:
                paths.append(str(url))
        except Exception:
            paths.append(str(url))
    return supported_mmd_files(paths)


def install_drag_drop_importer() -> bool:
    """Install the MMD file drag-and-drop importer."""
    global _DROP_FILTER
    if _DROP_FILTER is not None:
        return True
    event_filter = _MmdDropEventFilter()
    if event_filter.install():
        _DROP_FILTER = event_filter
        _display_info("Maya MMD Tools: drag-and-drop import enabled")
        return True
    return False


def uninstall_drag_drop_importer() -> None:
    """Remove the MMD file drag-and-drop importer if installed."""
    global _DROP_FILTER
    if _DROP_FILTER is not None:
        _DROP_FILTER.uninstall()
        _DROP_FILTER = None
