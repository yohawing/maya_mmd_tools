"""Shared PMX/PMD model import pipeline helpers."""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, Optional, Tuple

from maya import cmds

from .. import settings
from ..converters import PhysicsConverter
from ..converters.light_converter import create_mmd_light_controller, wire_mmd_shaders_to_mmd_light
from ..converters.mesh_converter import sync_dx11_generated_uniforms
from ..core import maya_attribute_utils, maya_viewport_utils, settings_keys as setting_keys
from ..core.constants import SCENE_ROOT_SUFFIX
from ..core.namespace_utils import NamespaceUtils
from ..core.utils import create_bone_joint_mapping
from ..core.visibility_state import sync_visibility_connections
from ..adapters.maya_cmds_adapter import MayaCmdsAdapter
from .import_scale import apply_import_scale


def _is_development_mode() -> bool:
    """Return the persisted Development Mode state."""
    return bool(settings.get(setting_keys.UI_GENERAL_DEVELOPMENT_MODE, False))


class ModelImportPipeline:
    """Common orchestration support shared by PMX and PMD importers."""

    def __init__(
        self,
        *,
        logger: Any,
        filepath: str,
        scale: float,
        options: Optional[Dict[str, Any]],
        progress_callback: Optional[Callable[[int], None]] = None,
    ) -> None:
        self.logger = logger
        self.filepath = filepath
        self.scale = scale
        self.options = options or {}
        self.progress_callback = progress_callback
        self.profile = self.options.get("profile") if isinstance(self.options.get("profile"), dict) else None
        self.phase_timings: Dict[str, float] = {}

    def record_phase(self, name: str, start: float) -> None:
        """Record elapsed seconds for a named import phase when profiling."""
        if self.profile is not None:
            self.phase_timings[name] = round(time.perf_counter() - start, 6)

    def emit_progress(self, value: int) -> None:
        """Notify the caller about import progress without making callbacks fatal."""
        if self.progress_callback is None:
            return
        try:
            self.progress_callback(value)
        except Exception:
            self.logger.debug("Progress callback failed", exc_info=True)

    def resolve_namespace(self, model_name: str, *, custom_namespace: Optional[str] = None) -> Optional[str]:
        """Return the namespace requested by import options, creating a unique name."""
        if not self.options.get("use_namespace", False):
            return None

        if custom_namespace:
            namespace = NamespaceUtils.ensure_unique_namespace(custom_namespace)
            self.logger.debug("Using custom namespace: %s", namespace)
            return namespace

        base_ns = NamespaceUtils.generate_namespace(model_name)
        namespace = NamespaceUtils.ensure_unique_namespace(base_ns)
        self.logger.debug("Using namespace: %s", namespace)
        return namespace

    def create_root_group(self, model_name: str, attributes: Dict[str, Any]) -> str:
        """Create the model root transform and attach MMD metadata."""
        root_group = cmds.group(empty=True, name=f"{model_name}{SCENE_ROOT_SUFFIX}")
        self.logger.debug("Created root group: %s", root_group)
        maya_attribute_utils.set_custom_attributes(root_group, attributes)
        return root_group

    def connect_morph_nodes_to_root(self, root_group: str, morph_result: Dict[str, Any]) -> None:
        """Connect PMX network morph metadata nodes back to the model root."""
        for morph_node in (
            morph_result.get("bone_morph_nodes", [])
            + morph_result.get("material_morph_nodes", [])
        ):
            if not cmds.attributeQuery("mmd_model_root", node=morph_node, exists=True):
                cmds.addAttr(morph_node, longName="mmd_model_root", attributeType="message")
            cmds.connectAttr(f"{root_group}.message", f"{morph_node}.mmd_model_root", force=True)

    def connect_texture_nodes_to_root(self, root_group: str, texture_nodes) -> None:
        """Attach instance ownership to imported texture nodes."""
        for texture_node in texture_nodes or []:
            if not cmds.attributeQuery("mmd_model_root", node=texture_node, exists=True):
                cmds.addAttr(texture_node, longName="mmd_model_root", attributeType="message")
            cmds.connectAttr(f"{root_group}.message", f"{texture_node}.mmd_model_root", force=True)

    def convert_physics(
        self,
        *,
        file_kind: str,
        parser: Any,
        maya_joints: Any,
        root_group: str,
    ) -> Tuple[list, list]:
        """Run the development-only Maya Bullet preview conversion."""
        preview_requested = bool(self.options.get("enable_maya_bullet_preview", False))
        development_mode = _is_development_mode()
        if not (preview_requested and development_mode):
            self.logger.debug("Skipping Maya Bullet preview conversion")
            if self.profile is not None:
                self.profile["physics_converter"] = {
                    "skipped": True,
                    "reason": "maya_bullet_preview_disabled",
                }
            return [], []

        import_physics = self.options.get(
            "import_physics",
            settings.get(setting_keys.IMPORT_PHYSICS_IMPORT_PHYSICS, True),
        )
        if not import_physics:
            return [], []

        self.logger.debug("Converting physics...")
        if not getattr(parser, "rigid_bodies", None):
            self.logger.debug("No physics data found")
            return [], []

        physics_converter = PhysicsConverter(self._physics_converter_settings())
        bone_joint_mapping = create_bone_joint_mapping(parser.bones, maya_joints, file_kind)
        phase_start = time.perf_counter()
        if file_kind == "pmx":
            ncloth_nodes, constraint_nodes = physics_converter.convert_pmx_physics(
                parser, bone_joint_mapping, root_group
            )
        else:
            ncloth_nodes, constraint_nodes = physics_converter.convert_pmd_physics(
                parser, bone_joint_mapping, root_group
            )
        self.record_phase("physics_conversion_sec", phase_start)
        if self.profile is not None:
            self.profile["physics_converter"] = {
                "created_bullet_rigid_bodies": len(physics_converter.created_bullet_rigid_bodies),
                "created_bullet_constraints": len(physics_converter.created_bullet_constraints),
                "bullet_visual_locator_failure_count": len(
                    physics_converter.bullet_visual_locator_failures
                ),
                "bullet_visual_locator_failures": list(
                    physics_converter.bullet_visual_locator_failures
                ),
            }
        self.emit_progress(86)
        self.logger.debug(
            "Physics conversion complete: nCloth=%d, Constraints=%d",
            len(ncloth_nodes),
            len(constraint_nodes),
        )
        return ncloth_nodes, constraint_nodes

    def _physics_converter_settings(self) -> dict:
        """Collect physics converter settings from import options and defaults."""
        keys = (
            "create_physics_joints",
            "simulation_quality",
            "solver_iterations",
            "substeps",
            "start_frame",
            "time_scale",
            "gravity",
            "bullet_fixed_frame_rate",
            "split_impulse",
        )
        result = {}
        for key in keys:
            setting_key = f"import.physics.{key}"
            result[key] = self.options.get(key, settings.get(setting_key, None))
        result["scale"] = self.scale
        return {key: value for key, value in result.items() if value is not None}

    def create_light_controller(self) -> Optional[str]:
        """Create the shared MMD light controller when enabled."""
        if not settings.get(setting_keys.IMPORT_LIGHT_CREATE_CONTROLLER, True):
            return None
        try:
            return create_mmd_light_controller()
        except Exception:
            self.logger.debug("Failed to create MMD light controller", exc_info=True)
            return None

    def apply_scale_and_select(self, root_group: str, *, apply_scale: bool = True) -> None:
        """Finalize root visibility, apply import scale, and select the model."""
        sync_visibility_connections(MayaCmdsAdapter(cmds), root_group)
        if apply_scale:
            apply_import_scale(root_group, self.scale, self.logger)
        self.emit_progress(92)
        cmds.select(root_group)

    def sync_dx11_uniforms(self, mesh_converter: Any, *, refresh_if_dx11: bool = False) -> int:
        """Materialize hardware uniforms, then sync DX11 compatibility attrs."""
        has_hardware_shader = bool(
            mesh_converter.has_dx11_shaders
            or getattr(mesh_converter, "has_glsl_shaders", False)
        )
        if refresh_if_dx11 and has_hardware_shader:
            try:
                phase_start = time.perf_counter()
                cmds.refresh(force=True)
                self.record_phase("refresh_sec", phase_start)
            except Exception:
                self.logger.debug("Failed to refresh viewport before hardware uniform sync", exc_info=True)

        phase_start = time.perf_counter()
        synced_dx11 = sync_dx11_generated_uniforms(mesh_converter.created_shaders)
        self.record_phase("dx11_uniform_sync_sec", phase_start)
        if synced_dx11:
            self.logger.debug("dx11Shader generated uniforms synchronized: %d", synced_dx11)
        return synced_dx11

    def wire_light_controller(self, mesh_converter: Any, light_ctrl: Optional[str]) -> None:
        """Wire the MMD light controller to generated hardware shaders."""
        if not light_ctrl:
            return
        try:
            wire_mmd_shaders_to_mmd_light(mesh_converter.created_shaders, light_ctrl)
        except Exception:
            self.logger.debug("Failed to wire MMD light", exc_info=True)

    def setup_view(self) -> None:
        """Apply MMD-friendly view settings requested by import options."""
        if settings.get(setting_keys.IMPORT_VIEW_SETUP_COLOR_MANAGEMENT, True):
            maya_viewport_utils.setup_mmd_color_management()
        if settings.get(setting_keys.IMPORT_VIEW_SETUP_TRANSPARENCY, True):
            maya_viewport_utils.setup_mmd_transparency()
        self.emit_progress(96)

    def cleanup_namespace(self, namespace: Optional[str]) -> None:
        """Remove a namespace after an import failure."""
        if namespace:
            self.logger.debug("Cleaning up namespace: %s", namespace)
            NamespaceUtils.cleanup_namespace(namespace, force=True)
