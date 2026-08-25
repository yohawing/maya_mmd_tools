"""
MMDファイル（PMX、PMD、VMD）を解析し、Mayaシーンにインポートするためのメインモジュール。
"""

from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from mmd_tools.core import settings, settings_keys
from mmd_tools.core import maya_viewport_utils
from mmd_tools.core.exceptions import MMDImportException
from mmd_tools.core.import_strategy import resolve_model_import_strategy
from mmd_tools.core.mmd_parser import parse_mmd_file
from mmd_tools.core.mmd_control_rig_builder import build_mmd_control_rig
from mmd_tools.core.mmd_control_rig_motion import enter_mmd_control_rig_edit
from mmd_tools.core.vmd_data import VmdData
from mmd_tools.converters import vmd_profile
from mmd_tools.converters.vmd_motion_kind import detect_vmd_motion_kind
from mmd_tools.io import pmx_importer, vmd_importer
from mmd_tools.io.cpp_fast_importer import fast_import
from mmd_tools.core.logger import get_logger

logger = get_logger("mmd_tools.io.mmd_importer")

_OPTION_TO_SETTINGS_KEY = settings_keys.MODEL_OPTION_TO_SETTINGS_KEY

# A VP2-owned import is an explicit UI route.  Falling through to the Python
# importer after that route fails creates a valid-looking ordinary Maya mesh,
# which is materially different from what the caller requested.  Keep the
# code stable so presenters and tests can surface an actionable failure.
_NATIVE_VP2_IMPORT_FAILURE_CODE = "NATIVE_VP2_OWNERSHIP_UNAVAILABLE"


def _native_route_profile(options: Dict[str, Any]) -> Dict[str, Any]:
    """Return the optional structured native-import diagnostics bucket."""
    profile = options.get("profile")
    if not isinstance(profile, dict):
        profile = {}
        options["profile"] = profile
    return profile.setdefault("native_import", {})


def _raise_native_vp2_failure(options: Dict[str, Any], reason: str, error: Optional[Exception] = None) -> None:
    """Fail closed when an explicitly requested VP2 route cannot be used."""
    diagnostics = _native_route_profile(options)
    diagnostics.update(
        {
            "requested": True,
            "route": "cpp_fast_load_vp2",
            "status": "failed",
            "fallback": "blocked",
            "code": _NATIVE_VP2_IMPORT_FAILURE_CODE,
            "reason": str(reason),
        }
    )
    message = (
        "C++ Fast Load with VP2 ownership was requested, but mmdRenderShape "
        f"could not be created ({reason}). Python mesh fallback is blocked. "
        "Check the loaded mmd_tools_cpp plugin and reload it before importing again."
    )
    if error is None:
        raise MMDImportException(message)
    raise MMDImportException(message) from error


def _resolve_vmd_content_route(parsed_data: Any, options: Dict[str, Any]) -> None:
    """Select the model or scene-animation VMD route from parsed frame content.

    Camera/light-only VMD files do not need a model. Bone, morph, or IK display
    keys make the file model-owned (including mixed model/camera motions), so
    those files must target the model currently selected in the Manager.
    """
    motion_kind = detect_vmd_motion_kind(parsed_data)
    scene_animation_only = motion_kind in {"camera", "light"}
    options["scene_animation_only"] = scene_animation_only
    if scene_animation_only:
        options.pop("target_model", None)
        return
    if not options.get("target_model"):
        raise MMDImportException(
            "VMD model motion requires a current model. Select a model in the Manager first."
        )


def _schedule_uv_editor_refresh() -> None:
    """Refresh all open UV Editors once after a successful model import."""
    try:
        from maya import cmds, utils

        def _refresh_open_uv_editors() -> None:
            try:
                editors = cmds.getPanel(type="polyTexturePlacementPanel") or []
                for editor in editors:
                    try:
                        cmds.textureWindow(editor, edit=True, forceRebake=True)
                        cmds.textureWindow(editor, edit=True, refresh=True)
                    except Exception:
                        logger.debug("Failed to refresh UV Editor %s", editor, exc_info=True)
            except Exception:
                logger.debug("Failed to enumerate open UV Editors", exc_info=True)

        utils.executeDeferred(_refresh_open_uv_editors)
    except Exception:
        logger.debug("Failed to schedule UV Editor refresh", exc_info=True)


def _post_model_import_control_rig(root: Optional[Any], options: Dict[str, Any]) -> Optional[Any]:
    """Build and bind the opt-in MMD Control Rig after model import.

    C++ fast loading and the Python PMX/PMD importer both return the model
    root from :func:`import_mmd_file`.  Keeping this follow-up in one helper
    prevents the two routes from drifting and, importantly, keeps a rig build
    failure partial: the imported model remains usable and the action layer
    can surface the structured profile warning.
    """
    if not root:
        return root

    _schedule_uv_editor_refresh()
    if not options.get("create_mmd_control_rig", False):
        return root

    profile = options.get("profile")
    if not isinstance(profile, dict):
        profile = {}
        options["profile"] = profile
    rig_profile = profile.setdefault("mmd_control_rig", {})
    rig_profile["requested"] = True
    model_root = str(root)
    rig_profile["model_root"] = model_root

    try:
        result = build_mmd_control_rig(model_root)
    except Exception as exc:
        warning = {
            "source": "mmd_importer",
            "code": "control_rig_create_failed",
            "message": str(exc),
            "model_root": model_root,
            "exception_type": type(exc).__name__,
            "severity": "warning",
            "fallback": "model_imported_without_control_rig",
        }
        rig_profile["succeeded"] = False
        rig_profile["error"] = str(exc)
        # ImportModelAction consumes this top-level list when classifying a
        # successful model import with non-fatal follow-up failures.
        profile.setdefault("warnings", []).append(warning)
        logger.warning(
            "MMD Control Rig build failed after model import (%s): %s",
            root,
            exc,
            exc_info=True,
        )
        return root

    created = bool(result.created)
    rig_profile["created"] = created
    rig_profile["reused"] = not created
    rig_profile["control_group"] = result.control_group
    rig_profile["selection_set"] = result.selection_set
    rig_profile["control_count"] = len(result.controls)

    try:
        metadata = enter_mmd_control_rig_edit(model_root)
    except Exception as exc:
        warning = {
            "source": "mmd_importer",
            "code": "control_rig_bind_failed",
            "message": str(exc),
            "model_root": model_root,
            "exception_type": type(exc).__name__,
            "severity": "warning",
            "fallback": "model_imported_with_attached_control_rig",
        }
        rig_profile["succeeded"] = False
        rig_profile["bound"] = False
        rig_profile["state"] = result.state
        rig_profile["owner"] = result.owner
        rig_profile["error"] = str(exc)
        profile.setdefault("warnings", []).append(warning)
        logger.warning(
            "MMD Control Rig bind failed after model import (%s): %s",
            root,
            exc,
            exc_info=True,
        )
        return root

    rig_profile["succeeded"] = True
    rig_profile["bound"] = True
    rig_profile["state"] = metadata["state"]
    rig_profile["owner"] = metadata.get("owner", result.owner)
    logger.info("MMD Control Rig created and bound for imported model: %s", root)
    return root


@contextmanager
def _scoped_settings_override(options: Dict[str, Any]):
    """Temporarily apply option values to global settings for the duration of a model import.

    Downstream converters that read settings directly will see the values from
    the options dict. Original values are restored unconditionally in a finally block.
    Only option keys present in both *options* and *_OPTION_TO_SETTINGS_KEY* are applied.
    """
    saved = {}
    for opt_key, settings_key in _OPTION_TO_SETTINGS_KEY.items():
        if opt_key in options:
            saved[settings_key] = settings.get(settings_key)
            settings.set(settings_key, options[opt_key])
    try:
        yield
    finally:
        for settings_key, original_value in saved.items():
            settings.set(settings_key, original_value)


def import_mmd_file(
    filepath: str,
    scale: Optional[float] = None,
    options: Optional[Dict[str, Any]] = None,
    progress_callback: Optional[Callable[[int], None]] = None,
) -> Optional[Any]:
    """
    MMDファイルを解析し、Mayaシーンにインポートします。
    ファイルタイプに応じて適切なインポーターを呼び出します。

    Args:
        filepath (str): インポートするMMDファイルのパス。
        scale (float): インポート時のスケール値。(互換性のために残している)
        options (dict): インポートオプション。scaleを含むことができる。
        progress_callback (Callable[[int], None]): フェーズ進捗通知コールバック。

    Returns:
        str: インポートされたモデルのルートノード名。

    Raises:
        MMDImportException: ファイルの解析またはインポートに失敗した場合。
    """

    # デフォルトオプション
    if options is None:
        options = {}

    def _emit_progress(value: int) -> None:
        if progress_callback is not None:
            try:
                progress_callback(value)
            except Exception:
                logger.debug("Progress callback failed", exc_info=True)

    _emit_progress(5)
    strategy = resolve_model_import_strategy(filepath, options)
    suffix = strategy.suffix
    # Precedence: explicit scale= kwarg > options["scale"] > mode-aware policy
    # (dev: persisted scale_factor, normal: DEFAULT_SCALE_FACTOR 1.0).
    # Explicit scale= remains an intentional public API override.
    if scale is not None:
        import_scale = scale
    elif "scale" in options:
        import_scale = options["scale"]
    else:
        from mmd_tools.services.settings_service import SettingsService

        import_scale = SettingsService().resolve_import_scale()

    # --- C++ fast import path (Development Mode only in the UI, PMX only) --
    logger.info("Model import strategy: cpp_fast_load=%s (%s)", strategy.use_cpp_fast_load, strategy.cpp_fast_load_reason)
    vp2_ownership_requested = bool(options.get("use_cpp_vp2_ownership", False))
    if suffix == ".pmx" and vp2_ownership_requested and not strategy.use_cpp_fast_load:
        # The UI can persist the two native checkboxes independently.  A stale
        # VP2=true with Fast Load=false must not silently become a Python mesh.
        _raise_native_vp2_failure(options, "C++ Fast Load is disabled")

    if strategy.use_cpp_fast_load:
        _emit_progress(10)
        mesh_only = options.get(
            "cpp_fast_load_mesh_only",
            settings.get(settings_keys.IMPORT_NATIVE_CPP_FAST_LOAD_MESH_ONLY, True),
        )
        if options.get("create_mmd_control_rig", False):
            # The control-rig analyzer needs the indexed joints emitted by
            # the fast skeleton/skin path.  A requested rig therefore takes
            # precedence over the mesh-only performance option.
            mesh_only = False
        base_name = options.get("custom_namespace") or Path(filepath).stem
        include_morphs = options.get(
            "import_morphs",
            settings.get(settings_keys.IMPORT_MORPH_IMPORT_MORPHS, True),
        )
        fast_kwargs = {
            "base_name": base_name,
            "scale": import_scale,
            "mesh_only": mesh_only,
            "include_morphs": include_morphs,
        }
        # Direct callers that explicitly request C++ Fast Load retain the
        # ordinary mesh path unless they also opt into VP2 ownership.  The UI
        # supplies this setting explicitly, so its default remains the native
        # RenderOverride route without changing the direct API contract.
        if options.get("use_cpp_vp2_ownership", False):
            fast_kwargs["vp2_ownership"] = True
        try:
            fast_root = fast_import(filepath, **fast_kwargs)
        except Exception as exc:
            if vp2_ownership_requested:
                _raise_native_vp2_failure(options, f"fast importer error: {exc}", exc)
            raise
        if fast_root is not None:
            _emit_progress(90)
            if vp2_ownership_requested:
                diagnostics = _native_route_profile(options)
                diagnostics.update(
                    {
                        "requested": True,
                        "route": "cpp_fast_load_vp2",
                        "status": "succeeded",
                        "fallback": "not_used",
                    }
                )
            if vp2_ownership_requested and settings.get(
                settings_keys.IMPORT_VIEW_SETUP_COLOR_MANAGEMENT, True
            ):
                # Native MMD output is authored sRGB and must retain gamma-space
                # alpha blending.  The Python dx11Shader path uses the separate
                # CM-on/de-gamma contract in ModelImportPipeline.setup_view().
                maya_viewport_utils.setup_mmd_native_color_management()
            logger.info("C++ fast import succeeded: %s", fast_root)
            return _post_model_import_control_rig(fast_root, options)
        if vp2_ownership_requested:
            _raise_native_vp2_failure(options, "fast importer returned no model root")
        logger.info("C++ fast import failed/excluded – falling back to Python parser")

    parse_completed = False
    try:
        # 汎用パーサーでファイルを解析
        with vmd_profile.scope("vmd_parse" if suffix == ".vmd" else "model_parse"):
            parsed_data = parse_mmd_file(
                filepath,
                use_native_pmx_parse=strategy.use_native_pmx_parse,
                require_native_pmx_parse=strategy.require_native_pmx_parse,
            )
        parse_completed = True
        if suffix == ".vmd":
            vmd_profile.set_extra("vmd_path", str(Path(filepath).resolve()))
            for attr in ("bone_frames", "morph_frames", "camera_frames", "light_frames"):
                vmd_profile.set_extra(attr, len(getattr(parsed_data, attr, []) or []))
        _emit_progress(12)

        # 手動reload後はクラスIDがずれて isinstance が失敗することがあるため、
        # ファイル拡張子でインポーターを選ぶ。
        if suffix == ".pmx":
            with _scoped_settings_override(options):
                model_root = pmx_importer.import_pmx_file(
                    parsed_data,
                    filepath,
                    import_scale,
                    options,
                    progress_callback=progress_callback,
                )
            return _post_model_import_control_rig(model_root, options)

        elif suffix == ".pmd":
            with _scoped_settings_override(options):
                model_root = pmx_importer.import_pmx_file(
                    parsed_data,
                    filepath,
                    import_scale,
                    options,
                    progress_callback=progress_callback,
                    is_pmd=True,
                )
            return _post_model_import_control_rig(model_root, options)

        elif suffix == ".vmd":
            _resolve_vmd_content_route(parsed_data, options)
            return vmd_importer.import_vmd_file(
                parsed_data,
                filepath,
                options,
                progress_callback=progress_callback,
            )

        else:
            raise MMDImportException(f"Unsupported data type returned from parser: {type(parsed_data)}")

    except Exception as e:
        if suffix == ".vmd" and options.get("bake_mode", False) and not parse_completed:
            logger.warning(
                "VMD parser failed in bake mode; attempting runtime bake from raw bytes: %s",
                e,
            )
            vmd_data = VmdData()
            vmd_data.source_file = str(Path(filepath).resolve())
            return vmd_importer.import_vmd_file(
                vmd_data,
                filepath,
                options,
                progress_callback=progress_callback,
            )
        if isinstance(e, MMDImportException):
            raise
        logger.error(f"Failed to import {filepath}: {e}", exc_info=True)
        raise MMDImportException(f"Failed to import {filepath}: {e}") from e
