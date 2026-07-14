"""Shared PMX/PMD model import pipeline helpers."""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, Optional, Tuple

from maya import cmds

from .. import settings
from ..converters.light_converter import create_mmd_light_controller, wire_mmd_shaders_to_mmd_light
from ..converters.mesh_converter import sync_dx11_generated_uniforms
from ..core import maya_attribute_utils, maya_viewport_utils, settings_keys as setting_keys
from ..core.constants import DEFAULT_IMPORT_PHYSICS, SCENE_ROOT_SUFFIX
from ..core.namespace_utils import NamespaceUtils
from ..core.visibility_state import sync_visibility_connections
from ..adapters.maya_cmds_adapter import MayaCmdsAdapter
from .import_scale import apply_import_scale


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
        """Build physics DAG nodes when the import option is enabled."""
        if not bool(self.options.get("import_physics", DEFAULT_IMPORT_PHYSICS)):
            self.logger.debug("Skipping physics scene build (import_physics disabled)")
            if self.profile is not None:
                self.profile["physics_converter"] = {
                    "skipped": True,
                    "reason": "import_physics_disabled",
                }
            return [], []

        rigid_bodies = getattr(parser, "rigid_bodies", None) or []
        if not rigid_bodies:
            self.logger.debug("Skipping physics scene build (no physics data found)")
            if self.profile is not None:
                self.profile["physics_converter"] = {
                    "skipped": True,
                    "reason": "no_physics_data",
                }
            return [], []

        from ..converters.physics_scene_builder import (
            build_physics_live_graph,
            build_physics_scene,
        )

        t0 = time.time()
        rb_transforms, jt_transforms = build_physics_scene(
            rigid_bodies=rigid_bodies,
            joints=getattr(parser, "joints", None) or [],
            bones=getattr(parser, "bones", None) or [],
            maya_joints=maya_joints,
            root_group=root_group,
            logger=self.logger,
        )
        elapsed = time.time() - t0
        self.logger.info(
            "Physics scene built: %d rigid bodies, %d joints (%.3fs)",
            len([t for t in rb_transforms if t]),
            len([t for t in jt_transforms if t]),
            elapsed,
        )
        # The live graph can evaluate immediately while its connections are
        # being created, so persist the source payload before creating the
        # solver node.  Otherwise the solver may cache a "no physics data"
        # initialization result during import.
        self._store_source_pmx_payload(root_group)
        live_graph = build_physics_live_graph(
            rigid_bodies=rigid_bodies,
            bones=getattr(parser, "bones", None) or [],
            maya_joints=maya_joints,
            root_group=root_group,
            logger=self.logger,
        )
        if live_graph.get("solver"):
            self.logger.info(
                "Physics live graph built: solver=%s, bone drivers=%d",
                live_graph["solver"],
                len(live_graph.get("drivers") or []),
            )
        else:
            self.logger.warning(
                "Physics DAG was imported, but live playback graph is unavailable: %s",
                live_graph.get("reason", "unknown"),
            )
        if self.profile is not None:
            self.profile["physics_converter"] = {
                "rigid_bodies": len(rb_transforms),
                "joints": len(jt_transforms),
                "solver": live_graph.get("solver"),
                "bone_drivers": len(live_graph.get("drivers") or []),
                "elapsed_seconds": elapsed,
            }
        return rb_transforms, jt_transforms

    def _store_source_pmx_payload(self, root_group: str) -> None:
        """Store raw PMX bytes on the model root for solver use."""
        import base64
        from pathlib import Path
        from ..core.constants import ATTR_MMD_SOURCE_PMX_PAYLOAD
        try:
            pmx_bytes = Path(self.filepath).read_bytes()
            encoded = base64.b64encode(pmx_bytes).decode("ascii")
            if not cmds.attributeQuery(ATTR_MMD_SOURCE_PMX_PAYLOAD, node=root_group, exists=True):
                cmds.addAttr(root_group, longName=ATTR_MMD_SOURCE_PMX_PAYLOAD, dataType="string", hidden=True)
            cmds.setAttr(f"{root_group}.{ATTR_MMD_SOURCE_PMX_PAYLOAD}", encoded, type="string")
            self.logger.debug("Stored PMX payload (%d bytes) on %s", len(pmx_bytes), root_group)
        except Exception as exc:
            self.logger.warning("Failed to store PMX payload: %s", exc)

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
