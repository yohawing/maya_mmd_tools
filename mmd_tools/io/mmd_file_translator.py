"""Maya File > Import translator for PMX/PMD/VMD files.

Maya exposes file translators only through the Python API 1.0 plug-in layer,
so this module keeps the API 1.0 registration isolated while routing actual
imports through the same importer contract used by drag-and-drop import.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from maya import cmds
import maya.OpenMaya as om1
import maya.OpenMayaMPx as mpx

from mmd_tools.core.logger import get_logger
from mmd_tools.io.drag_drop_importer import _selected_model_root, import_dropped_files

logger = get_logger(__name__)

TRANSLATOR_NAME = "Maya MMD Tools"
_SUPPORTED_EXTENSIONS = {".pmx", ".pmd", ".vmd"}


def _file_object_path(file_object) -> str:
    """Return a usable local path from Maya's MFileObject."""
    for attr_name in ("resolvedFullName", "expandedFullName", "fullName"):
        try:
            value = getattr(file_object, attr_name)()
        except Exception:
            continue
        if value:
            return str(value)
    return ""


def _is_supported_path(path: str) -> bool:
    return Path(str(path or "")).suffix.lower() in _SUPPORTED_EXTENSIONS


def _display_warning(message: str) -> None:
    try:
        om1.MGlobal.displayWarning(message)
    except Exception:
        logger.warning(message)


def _deferred_import(path: str) -> None:
    if not import_dropped_files([path]):
        _display_warning(f"Maya MMD Tools: File > Import failed for {path}")


def _schedule_import(path: str) -> None:
    try:
        cmds.scriptJob(runOnce=True, idleEvent=lambda import_path=path: _deferred_import(import_path))
    except Exception:
        cmds.evalDeferred(lambda import_path=path: _deferred_import(import_path), lowestPriority=True)


class MmdFileTranslator(mpx.MPxFileTranslator):
    """Import PMX/PMD/VMD from Maya's standard File > Import dialog."""

    def __init__(self):
        mpx.MPxFileTranslator.__init__(self)

    def haveReadMethod(self):
        return True

    def haveWriteMethod(self):
        return False

    def haveReferenceMethod(self):
        return False

    def canBeOpened(self):
        return False

    def defaultExtension(self):
        return "pmx"

    def filter(self):
        return "*.pmx;*.pmd;*.vmd"

    def identifyFile(self, file_object, buffer, size):
        path = _file_object_path(file_object)
        if _is_supported_path(path):
            return mpx.MPxFileTranslator.kIsMyFileType
        return mpx.MPxFileTranslator.kNotMyFileType

    def reader(self, file_object, option_string, access_mode):
        path = _file_object_path(file_object)
        if not _is_supported_path(path):
            raise RuntimeError(f"Unsupported MMD file type: {path}")
        if Path(path).suffix.lower() == ".vmd" and not _selected_model_root():
            _display_warning("Maya MMD Tools: load or import a PMX/PMD model before importing VMD motion files.")
            raise RuntimeError(f"Failed to import MMD file: {path}")
        _schedule_import(path)


def translator_creator():
    """Create the Maya API 1.0 translator pointer."""
    return mpx.asMPxPtr(MmdFileTranslator())


def register_file_translator(mobject) -> None:
    """Register the MMD file translator on the host plug-in."""
    plugin = mpx.MFnPlugin(mobject)
    plugin.registerFileTranslator(TRANSLATOR_NAME, None, translator_creator)


def deregister_file_translator(mobject) -> None:
    """Deregister the MMD file translator from the host plug-in."""
    plugin = mpx.MFnPlugin(mobject)
    plugin.deregisterFileTranslator(TRANSLATOR_NAME)


def supported_file_filters() -> Iterable[str]:
    """Return extensions accepted by the translator."""
    return tuple(sorted(_SUPPORTED_EXTENSIONS))
