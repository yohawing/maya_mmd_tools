"""Opt-in, passthrough Viewport 2.0 render override.

The default R1 override is intentionally a lifecycle proof only: it delegates
the ordinary Maya scene, HUD, and present operations without shader routing,
scene filtering, or user preference changes.  The separate R2 resource probe
is development-only and opt-in; it only reports conservative caster draw and
target readback evidence, never receiver composition or self-shadow parity.
The optional native-shadow binding probe follows Maya's active-light resource
path into a plugin-owned diagnostic quad; it also never replaces imported
materials or claims self-shadow parity.
The opt-in native-shadow receiver pass is a separate diagnostic composition
path.  It re-renders only MMD receiver components with a plugin-owned effect
after Maya's ordinary scene, but remains Oracle-gated and never claims full
self-shadow parity by itself.
Registration is owned by this module so repeated plugin loads/unloads cannot
deregister an override owned by another plugin.
"""

from __future__ import annotations

import ctypes
import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

import maya.api.OpenMayaRender as omr

from ..core.constants import (
    ATTR_MMD_DRAW_FLAGS,
    ATTR_MMD_MODEL_NAME,
    ATTR_MMD_MODEL_NAME_EN,
    SCENE_ROOT_SUFFIX,
)


RENDER_OVERRIDE_NAME = "mmdToolsPassthroughRenderOverride"
RENDER_OVERRIDE_UI_NAME = "MMD Tools Passthrough"
SCENE_OPERATION_NAME = "mmdToolsPassthroughScene"
PRESENT_OPERATION_NAME = "mmdToolsPassthroughPresent"
SHADOW_TARGET_OPERATION_NAME = "mmdToolsShadowTargetClear"
RECEIVER_PROBE_OPERATION_NAME = "mmdToolsShadowReceiverProbe"
SHADOW_COLOR_TARGET_NAME = "mmdToolsSelfShadowR32F"
SHADOW_DEPTH_TARGET_NAME = "mmdToolsSelfShadowD32"
RECEIVER_PROBE_TARGET_NAME = "mmdToolsSelfShadowReceiverProbeR32F"
SHADOW_TARGET_SIZE = 2048
RECEIVER_PROBE_TARGET_SIZE = 4
LIGHT_SPACE_CAMERA_NAME = "mmdToolsR32FLightSpaceCamera"
NATIVE_SHADOW_REQUEST_ENV = "MMD_TOOLS_ENABLE_RENDER_OVERRIDE_NATIVE_SHADOW_REQUEST"
NATIVE_SHADOW_BINDING_PROBE_ENV = "MMD_TOOLS_ENABLE_RENDER_OVERRIDE_NATIVE_SHADOW_BINDING_PROBE"
NATIVE_SHADOW_BINDING_PROBE_OPERATION_NAME = "mmdToolsNativeShadowBindingProbe"
NATIVE_SHADOW_BINDING_PROBE_TARGET_NAME = "mmdToolsNativeShadowBindingProbeR32F"
NATIVE_SHADOW_BINDING_PROBE_TARGET_SIZE = 4
NATIVE_SHADOW_BINDING_PROBE_TECHNIQUE = "MmdToolsNativeShadowBindingProbe"
NATIVE_SHADOW_BINDING_PROBE_SHADER_PATH = (
    Path(__file__).resolve().parents[1] / "shaders" / "MMDNativeShadowBindingProbe.fx"
)
NATIVE_SHADOW_BINDING_PROBE_MAP_PARAMETER = "MayaShadowMap"
NATIVE_SHADOW_BINDING_PROBE_VIEWPROJ_PARAMETER = "MayaShadowViewProj"
NATIVE_SHADOW_BINDING_PROBE_ENABLED_PARAMETER = "MayaShadowEnabled"
NATIVE_SHADOW_RECEIVER_ENV = "MMD_TOOLS_ENABLE_RENDER_OVERRIDE_NATIVE_SHADOW_RECEIVER"
NATIVE_SHADOW_RECEIVER_OPERATION_NAME = "mmdToolsNativeShadowReceiver"
NATIVE_SHADOW_RECEIVER_SHADER_PATH = (
    Path(__file__).resolve().parents[1] / "shaders" / "MMDNativeShadowReceiver.fx"
)
NATIVE_SHADOW_RECEIVER_TECHNIQUE = "MMDNativeShadowReceiver"
NATIVE_SHADOW_RECEIVER_MAP_PARAMETER = "Light0ShadowMap"
NATIVE_SHADOW_RECEIVER_VIEWPROJ_PARAMETER = "Light0Matrix"
NATIVE_SHADOW_RECEIVER_ENABLED_PARAMETER = "UseShadows"
NATIVE_SHADOW_RECEIVER_STRENGTH_PARAMETER = "ShadowStrength"
NATIVE_SHADOW_RECEIVER_BIAS_PARAMETER = "ShadowBias"
SELF_SHADOW_MAP_DRAW_FLAG = 0x04
SELF_SHADOW_RECEIVER_DRAW_FLAG = 0x08
SHADOW_CLEAR_VALUE = 1.0
SHADOW_DEPTH_CLEAR_EPSILON = 1e-6
R32F_BINDING_PROBE_PARAMETER = "MmdToolsR32FTarget"
R32F_BINDING_PROBE_TECHNIQUE = "MmdToolsR32FTargetBindingProbe"
R32F_BINDING_PROBE_SHADER_PATH = (
    Path(__file__).resolve().parents[1]
    / "shaders"
    / "MMDTargetBindingProbe.fx"
)
R32F_CASTER_PASS_PARAMETER = "WorldViewProjection"
R32F_CASTER_PASS_TECHNIQUE = "MmdToolsR32FCaster"
R32F_CASTER_PASS_SHADER_PATH = (
    Path(__file__).resolve().parents[1] / "shaders" / "MMDTargetCaster.fx"
)
R32F_RECEIVER_PROBE_PARAMETER = R32F_BINDING_PROBE_PARAMETER
R32F_RECEIVER_PROBE_TECHNIQUE = "MmdToolsR32FReceiverProbe"
R32F_RECEIVER_PROBE_SHADER_PATH = (
    Path(__file__).resolve().parents[1] / "shaders" / "MMDTargetReceiverProbe.fx"
)


def _enabled_environment_flag(name: str) -> bool:
    """Return whether an explicit development-only render flag is enabled."""
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class ShadowCasterSelection:
    """MMD mesh components eligible for the later shadow-depth operation.

    The selection is deliberately data-only in this slice.  The diagnostic
    operation uses it only to witness ordinary VP2 caster draw/readback; it
    does not assert MMD shadow-camera, shader, or receiver semantics.
    """

    components: Tuple[str, ...]
    roots: Tuple[str, ...]
    flagged_materials: Tuple[str, ...]
    skipped_materials: Tuple[str, ...]


def _discover_self_shadow_components(
    draw_flag: int, cmds_module=None
) -> ShadowCasterSelection:
    """Return MMD mesh components whose material contains ``draw_flag``.

    The helper is shared by the cast and receive routes.  Missing, unreadable,
    or malformed attributes fail closed so an arbitrary Maya material is never
    silently routed into either opt-in operation.
    """
    if cmds_module is None:
        import maya.cmds as cmds_module

    roots: List[str] = []
    for node in cmds_module.ls(type="transform", long=True) or []:
        leaf_name = node.rsplit("|", 1)[-1]
        if not leaf_name.endswith(SCENE_ROOT_SUFFIX):
            continue
        if any(
            cmds_module.attributeQuery(attribute, node=node, exists=True)
            for attribute in (ATTR_MMD_MODEL_NAME, ATTR_MMD_MODEL_NAME_EN)
        ):
            roots.append(node)

    components: List[str] = []
    flagged_materials: List[str] = []
    skipped_materials: List[str] = []
    seen_components = set()
    seen_material_bindings = set()
    seen_flagged_materials = set()
    seen_skipped_materials = set()
    for root in roots:
        for shape in cmds_module.listRelatives(
            root, allDescendents=True, type="mesh", fullPath=True
        ) or []:
            for shading_group in cmds_module.listConnections(shape, type="shadingEngine") or []:
                materials = cmds_module.listConnections(
                    f"{shading_group}.surfaceShader", source=True, destination=False
                ) or []
                for material in materials:
                    material_binding = (material, shading_group)
                    if material_binding in seen_material_bindings:
                        continue
                    seen_material_bindings.add(material_binding)
                    if not cmds_module.attributeQuery(
                        ATTR_MMD_DRAW_FLAGS, node=material, exists=True
                    ):
                        if material not in seen_skipped_materials:
                            skipped_materials.append(material)
                            seen_skipped_materials.add(material)
                        continue
                    try:
                        draw_flags = int(
                            cmds_module.getAttr(f"{material}.{ATTR_MMD_DRAW_FLAGS}")
                        )
                    except (TypeError, ValueError, RuntimeError):
                        if material not in seen_skipped_materials:
                            skipped_materials.append(material)
                            seen_skipped_materials.add(material)
                        continue
                    if not draw_flags & draw_flag:
                        continue
                    if material not in seen_flagged_materials:
                        flagged_materials.append(material)
                        seen_flagged_materials.add(material)
                    for member in cmds_module.sets(shading_group, query=True) or []:
                        resolved_members = cmds_module.ls(member, long=True) or []
                        resolved_member = resolved_members[0] if resolved_members else member
                        owners = (shape,) + tuple(
                            cmds_module.listRelatives(shape, parent=True, fullPath=True) or []
                        )
                        owner_names = {
                            owner_name
                            for owner in owners
                            for owner_name in (owner, owner.rsplit("|", 1)[-1])
                        }
                        belongs_to_owner = any(
                            candidate == owner_name
                            or candidate.startswith(f"{owner_name}.")
                            for candidate in (member, resolved_member)
                            for owner_name in owner_names
                        )
                        if belongs_to_owner and resolved_member not in seen_components:
                            components.append(resolved_member)
                            seen_components.add(resolved_member)

    return ShadowCasterSelection(
        components=tuple(components),
        roots=tuple(roots),
        flagged_materials=tuple(flagged_materials),
        skipped_materials=tuple(skipped_materials),
    )


def discover_self_shadow_caster_components(cmds_module=None) -> ShadowCasterSelection:
    """Return only components whose PMX material has the cast-shadow bit.

    MMD's ``mmd_draw_flags`` bit ``0x04`` controls drawing to the self-shadow
    map.  The result is scoped to imported MMD model roots and is used only by
    the diagnostic caster operation.
    """
    return _discover_self_shadow_components(SELF_SHADOW_MAP_DRAW_FLAG, cmds_module)


def discover_self_shadow_receiver_components(cmds_module=None) -> ShadowCasterSelection:
    """Return only components whose PMX material receives self-shadowing.

    MMD's ``mmd_draw_flags`` bit ``0x08`` controls the receiver side.  The
    return shape intentionally matches ``ShadowCasterSelection`` so the same
    fail-closed selection plumbing can be used without widening the public
    data contract.
    """
    return _discover_self_shadow_components(
        SELF_SHADOW_RECEIVER_DRAW_FLAG, cmds_module
    )


class LightSpaceCasterCamera:
    """Own an opt-in orthographic camera aligned to a Maya directional light.

    The camera is deliberately temporary and plugin-owned.  It is used only
    by the diagnostic caster operation to remove the current model-panel
    camera from the shadow-map experiment; it does not replace the user's
    panel camera, create a persistent scene asset, or claim MMD self-shadow
    parity.  MMD model-root bounds are used when available and a finite
    origin-centred fallback keeps an empty scene deterministic.
    """

    def __init__(self, cmds_module=None, om_module=None, render_module=None):
        self._cmds = cmds_module
        self._om = om_module
        self._render_module = render_module or omr
        self._transform = None
        self._shape = None
        self._camera_override = None
        self._report = self._empty_report()

    @staticmethod
    def _empty_report() -> dict:
        """Return a stable, JSON-safe light-space camera report."""
        return {
            "enabled": True,
            "status": "not-run",
            "reason": "not-configured",
            "source": "directional-light",
            "directionalLight": None,
            "cameraTransform": None,
            "cameraShape": None,
            "cameraPath": None,
            "roots": [],
            "boundsSource": None,
            "bounds": None,
            "center": None,
            "forward": None,
            "rotation": None,
            "distance": None,
            "orthographicWidth": None,
            "nearClip": None,
            "farClip": None,
            "createAttemptCount": 0,
            "createSucceeded": False,
            "releaseAttemptCount": 0,
            "releaseSucceeded": False,
            "lastError": None,
        }

    def _modules(self):
        """Resolve Maya command/API modules lazily for unit-test isolation."""
        if self._cmds is None:
            import maya.cmds as cmds

            self._cmds = cmds
        if self._om is None:
            import maya.api.OpenMaya as om

            self._om = om
        return self._cmds, self._om

    @staticmethod
    def _as_finite_vector(values, *, fallback):
        """Return a finite 3-vector or the supplied fallback tuple."""
        try:
            vector = tuple(float(value) for value in values)
        except (TypeError, ValueError):
            return tuple(fallback)
        if len(vector) != 3 or not all(math.isfinite(value) for value in vector):
            return tuple(fallback)
        return vector

    @classmethod
    def _bounds(cls, cmds):
        """Return finite MMD-root bounds for the temporary camera."""
        roots = []
        boxes = []
        for node in cmds.ls(type="transform", long=True) or []:
            leaf_name = node.rsplit("|", 1)[-1]
            if not leaf_name.endswith(SCENE_ROOT_SUFFIX):
                continue
            try:
                has_model_attribute = any(
                    cmds.attributeQuery(attribute, node=node, exists=True)
                    for attribute in (ATTR_MMD_MODEL_NAME, ATTR_MMD_MODEL_NAME_EN)
                )
            except Exception:
                has_model_attribute = False
            if not has_model_attribute:
                continue
            try:
                box = tuple(float(value) for value in cmds.exactWorldBoundingBox(
                    node, ignoreInvisible=True
                ))
            except (TypeError, ValueError, RuntimeError):
                continue
            if len(box) != 6 or not all(math.isfinite(value) for value in box):
                continue
            minimum = box[:3]
            maximum = box[3:]
            if any(low > high for low, high in zip(minimum, maximum)):
                continue
            roots.append(node)
            boxes.append(box)

        if boxes:
            minimum = tuple(min(box[index] for box in boxes) for index in range(3))
            maximum = tuple(max(box[index + 3] for box in boxes) for index in range(3))
            return (
                tuple(roots),
                "mmd-root-bounds",
                {"min": list(minimum), "max": list(maximum)},
            )

        return (
            tuple(roots),
            "fallback-origin",
            {"min": [-5.0, -5.0, -5.0], "max": [5.0, 5.0, 5.0]},
        )

    @staticmethod
    def _camera_path_name(camera_path) -> str:
        """Return a stable path string from an MDagPath-like object."""
        full_path_name = getattr(camera_path, "fullPathName", None)
        if callable(full_path_name):
            return str(full_path_name())
        return str(camera_path)

    def configure(self) -> dict:
        """Create and configure the temporary light-space camera."""
        previous_release = self.release()
        if self.has_owned_camera():
            self._report.update(
                status="unsupported",
                reason="previous-camera-release-failed",
                createSucceeded=False,
                lastError=previous_release.get("lastError"),
            )
            return self.report()

        report = self._report
        report.update(
            status="not-run",
            reason="not-configured",
            createSucceeded=False,
            releaseSucceeded=False,
            lastError=None,
        )
        report["createAttemptCount"] += 1

        try:
            cmds, om = self._modules()
            directional_shapes = cmds.ls(type="directionalLight", long=True) or []
            light_transform = None
            for candidate in directional_shapes:
                parents = cmds.listRelatives(candidate, parent=True, fullPath=True) or []
                if parents:
                    light_transform = parents[0]
                    break
            if light_transform is None:
                report.update(
                    status="unsupported",
                    reason="no-directional-light",
                    lastError="No directionalLight shape with a parent transform was found",
                )
                return self.report()

            roots, bounds_source, bounds = self._bounds(cmds)
            minimum = tuple(bounds["min"])
            maximum = tuple(bounds["max"])
            center = tuple((low + high) * 0.5 for low, high in zip(minimum, maximum))
            span = max(maximum[index] - minimum[index] for index in range(3))
            if not math.isfinite(span) or span <= 0.0:
                span = 10.0
            distance = max(span * 4.0, 10.0)
            orthographic_width = max(span * 2.4, 1.0)
            near_clip = 0.01
            far_clip = max(distance + span * 4.0, near_clip + 1.0)

            world_matrix = tuple(
                float(value)
                for value in cmds.xform(
                    light_transform, query=True, worldSpace=True, matrix=True
                )
            )
            if len(world_matrix) != 16 or not all(
                math.isfinite(value) for value in world_matrix
            ):
                raise RuntimeError("directional light world matrix is not finite")
            forward = self._as_finite_vector(
                (-world_matrix[8], -world_matrix[9], -world_matrix[10]),
                fallback=(0.0, 0.0, -1.0),
            )
            forward_length = math.sqrt(sum(value * value for value in forward))
            if not math.isfinite(forward_length) or forward_length <= 1e-8:
                raise RuntimeError("directional light has a zero-length forward axis")
            forward = tuple(value / forward_length for value in forward)
            rotation = self._as_finite_vector(
                cmds.xform(light_transform, query=True, worldSpace=True, rotation=True),
                fallback=(0.0, 0.0, 0.0),
            )
            position = tuple(
                center[index] - forward[index] * distance for index in range(3)
            )

            if cmds.ls(LIGHT_SPACE_CAMERA_NAME, long=True):
                raise RuntimeError(
                    f"plugin-owned camera name is already in use: {LIGHT_SPACE_CAMERA_NAME}"
                )
            camera = cmds.camera(
                name=LIGHT_SPACE_CAMERA_NAME,
                orthographic=True,
                orthographicWidth=orthographic_width,
                nearClipPlane=near_clip,
                farClipPlane=far_clip,
            )
            if not isinstance(camera, (tuple, list)) or len(camera) < 2:
                raise RuntimeError(f"Maya camera command returned an invalid value: {camera!r}")
            self._transform, self._shape = str(camera[0]), str(camera[1])
            cmds.xform(
                self._transform, worldSpace=True, translation=position, rotation=rotation
            )
            try:
                cmds.setAttr(f"{self._transform}.visibility", False)
            except Exception:
                # Visibility is cosmetic; a camera path remains usable when
                # Maya rejects this optional attribute in a test double.
                pass

            selection = om.MSelectionList()
            selection.add(self._shape)
            camera_path = selection.getDagPath(0)
            camera_override_type = getattr(self._render_module, "MCameraOverride", None)
            if camera_override_type is None:
                raise RuntimeError("Maya MCameraOverride API is unavailable")
            camera_override = camera_override_type()
            camera_override.mCameraPath = camera_path
            camera_override.mUseNearClippingPlane = True
            camera_override.mNearClippingPlane = near_clip
            camera_override.mUseFarClippingPlane = True
            camera_override.mFarClippingPlane = far_clip
            self._camera_override = camera_override
            report.update(
                status="configured",
                reason="light-space-camera-configured",
                directionalLight=str(light_transform),
                cameraTransform=self._transform,
                cameraShape=self._shape,
                cameraPath=self._camera_path_name(camera_path),
                roots=list(roots),
                boundsSource=bounds_source,
                bounds=bounds,
                center=list(center),
                forward=list(forward),
                rotation=list(rotation),
                distance=distance,
                orthographicWidth=orthographic_width,
                nearClip=near_clip,
                farClip=far_clip,
                createSucceeded=True,
            )
        except Exception as exc:
            self._delete_owned_camera()
            report.update(
                status="unsupported",
                reason="camera-configuration-failed",
                createSucceeded=False,
                lastError=str(exc),
            )
        return self.report()

    def _delete_owned_camera(self) -> None:
        """Delete only the camera transform created by this instance."""
        if self._transform is None:
            self._shape = None
            self._camera_override = None
            return
        try:
            cmds, _ = self._modules()
            if cmds.objExists(self._transform):
                cmds.delete(self._transform)
        except Exception:
            return
        self._transform = None
        self._shape = None
        self._camera_override = None

    def release(self) -> dict:
        """Delete the temporary camera while retaining it on failure."""
        if self._transform is None:
            return self.report()
        self._report["releaseAttemptCount"] += 1
        try:
            cmds, _ = self._modules()
            if cmds.objExists(self._transform):
                cmds.delete(self._transform)
        except Exception as exc:
            self._report.update(
                status="unsupported",
                reason="camera-release-failed",
                releaseSucceeded=False,
                lastError=str(exc),
            )
            return self.report()
        self._transform = None
        self._shape = None
        self._camera_override = None
        self._report.update(
            status="released",
            reason="camera-released",
            releaseSucceeded=True,
            lastError=None,
        )
        return self.report()

    def camera_override(self):
        """Return the configured MCameraOverride, or ``None`` when unavailable."""
        return self._camera_override

    def has_owned_camera(self) -> bool:
        """Return whether cleanup must retain the temporary camera for retry."""
        return self._transform is not None

    def report(self) -> dict:
        """Return JSON-safe camera configuration/lifecycle diagnostics."""
        return dict(self._report)


class NativeShadowRequest:
    """Own Maya's native light-shadow request without touching imported shaders.

    ``MRenderer.setLightRequiresShadows`` is the renderer-owned way for a
    plug-in to keep a light's native shadow map alive.  This opt-in probe only
    acquires and releases that request for directional lights; it does not
    bind a resource to an imported ``dx11Shader`` or claim MMD self-shadow
    composition/parity.
    """

    def __init__(self, cmds_module=None, om_module=None, render_module=None):
        self._cmds = cmds_module
        self._om = om_module
        self._render_module = render_module or omr
        self._requested_lights = []
        self._report = self._empty_report()

    @staticmethod
    def _empty_report() -> dict:
        """Return the stable, JSON-safe native shadow request report."""
        return {
            "enabled": True,
            "status": "not-run",
            "reason": "not-requested",
            "source": "maya-renderer-light-request",
            "lights": [],
            "lightCount": 0,
            "requestAttemptCount": 0,
            "requestSucceeded": False,
            "releaseAttemptCount": 0,
            "releaseSucceeded": False,
            "lastError": None,
            "context": {
                "status": "not-run",
                "stage": None,
                "lightingMode": None,
                "lightFilter": None,
                "lightCount": 0,
                "lights": [],
                "lastError": None,
            },
        }

    def _modules(self):
        """Resolve Maya modules lazily so lifecycle tests stay host-independent."""
        if self._cmds is None:
            import maya.cmds as cmds

            self._cmds = cmds
        if self._om is None:
            import maya.api.OpenMaya as om

            self._om = om
        return self._cmds, self._om

    @staticmethod
    def _light_name(shape: object) -> str:
        """Return a stable light-shape name for JSON diagnostics."""
        return str(shape)

    def _mobject_for_shape(self, shape, om_module):
        """Resolve a directional-light shape to an MObject."""
        selection = om_module.MSelectionList()
        selection.add(shape)
        return selection.getDependNode(0)

    def _object_candidates(self, shape, cmds_module, om_module):
        """Yield the shape and parent transform candidates accepted by Maya."""
        yield "shape", shape, self._mobject_for_shape(shape, om_module)
        parents = cmds_module.listRelatives(shape, parent=True, fullPath=True) or []
        for parent in parents[:1]:
            yield "transform", parent, self._mobject_for_shape(parent, om_module)

    def _context_object_candidates(self, context):
        """Yield active directional-light objects from the current draw context."""
        context_report = self._report["context"]
        context_report.update(
            status="running",
            stage="need-evaluate-all-lights",
            lightingMode=None,
            lightFilter=None,
            lightCount=0,
            lights=[],
            lastError=None,
        )
        draw_context_type = getattr(self._render_module, "MDrawContext", None)
        light_filter = getattr(draw_context_type, "kFilteredIgnoreLightLimit", None)
        context_report["lightFilter"] = light_filter
        renderer = getattr(self._render_module, "MRenderer", None)
        need_evaluate_all_lights = getattr(renderer, "needEvaluateAllLights", None)
        try:
            if callable(need_evaluate_all_lights):
                # Maya's native shadow prepass explicitly requests light
                # evaluation before querying the active-light list. Without
                # this call the Python draw context can report no lights (or
                # raise while the renderer is still updating its light cache).
                need_evaluate_all_lights()
            lighting_mode_method = getattr(context, "getLightingMode", None)
            if callable(lighting_mode_method):
                lighting_mode = lighting_mode_method()
                context_report["lightingMode"] = str(lighting_mode)
                allowed_modes = {
                    getattr(draw_context_type, "kSelectedLights", None),
                    getattr(draw_context_type, "kSceneLights", None),
                }
                allowed_modes.discard(None)
                if allowed_modes and lighting_mode not in allowed_modes:
                    context_report.update(
                        status="skipped",
                        stage="lighting-mode",
                        reason="lighting-mode-has-no-scene-lights",
                    )
                    return
            context_report["stage"] = "number-of-active-lights"
            if light_filter is None:
                count = int(context.numberOfActiveLights())
            else:
                count = int(context.numberOfActiveLights(light_filter))
            context_report["lightCount"] = count
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc!r}"
            context_report.update(status="unsupported", lastError=error)
            raise
        context_report["status"] = "ready"
        for index in range(max(count, 0)):
            context_report["stage"] = f"light-information-{index}"
            if light_filter is None:
                info = context.getLightParameterInformation(index)
            else:
                info = context.getLightParameterInformation(index, light_filter)
            if info is None:
                continue
            light_type = str(info.lightType()).lower()
            context_light = {
                "index": index,
                "lightType": light_type,
                "parameters": {},
            }
            parameter_list = getattr(info, "parameterList", None)
            parameter_semantic = getattr(info, "parameterSemantic", None)
            get_parameter = getattr(info, "getParameter", None)
            if callable(parameter_list) and callable(parameter_semantic) and callable(get_parameter):
                light_info_type = getattr(
                    self._render_module, "MLightParameterInformation", None
                )
                interesting_semantics = {
                    getattr(light_info_type, name, None)
                    for name in (
                        "kGlobalShadowOn",
                        "kShadowOn",
                        "kLightEnabled",
                        "kEmitsDiffuse",
                        "kEmitsSpecular",
                    )
                }
                interesting_semantics.discard(None)
                for parameter_name in parameter_list():
                    semantic = parameter_semantic(parameter_name)
                    if semantic not in interesting_semantics:
                        continue
                    try:
                        context_light["parameters"][str(parameter_name)] = str(
                            get_parameter(parameter_name)
                        )
                    except Exception as exc:
                        context_light["parameters"][str(parameter_name)] = (
                            f"{type(exc).__name__}: {exc!r}"
                        )
            context_report["lights"].append(context_light)
            if "directional" not in light_type:
                continue
            path = info.lightPath()
            full_path_name = getattr(path, "fullPathName", None)
            node = getattr(path, "node", None)
            if not callable(full_path_name) or not callable(node):
                continue
            light_name = full_path_name()
            yield "draw-context", light_name, node()
            transform = getattr(path, "transform", None)
            if callable(transform):
                transform_path = transform()
                transform_full_path_name = getattr(transform_path, "fullPathName", None)
                transform_node = getattr(transform_path, "node", None)
                if callable(transform_full_path_name) and callable(transform_node):
                    yield "draw-context-transform", light_name, transform_node()
        context_report["stage"] = "complete"

    def request(self, context=None) -> dict:
        """Request native shadow maps for every directional light in the scene."""
        if self.has_owned_requests():
            return self.report()
        previous_release = self.release()
        if self.has_owned_requests():
            self._report.update(
                status="unsupported",
                reason="previous-shadow-request-release-failed",
                requestSucceeded=False,
                lastError=previous_release.get("lastError"),
            )
            return self.report()

        report = self._report
        report.update(
            status="not-run",
            reason="no-directional-light",
            lights=[],
            lightCount=0,
            requestSucceeded=False,
            releaseSucceeded=False,
            lastError=None,
        )
        report["requestAttemptCount"] += 1

        try:
            cmds, om = self._modules()
            if context is not None:
                context_candidates = tuple(self._context_object_candidates(context))
                grouped_candidates = {}
                for object_type, object_name, candidate in context_candidates:
                    grouped_candidates.setdefault(object_name, []).append(
                        (object_type, object_name, candidate)
                    )
                candidate_groups = list(grouped_candidates.items())
            else:
                candidate_groups = [
                    (
                        shape,
                        tuple(self._object_candidates(shape, cmds, om)),
                    )
                    for shape in (cmds.ls(type="directionalLight", long=True) or [])
                ]
            if not candidate_groups:
                return self.report()
            renderer = getattr(self._render_module, "MRenderer", None)
            request_method = getattr(renderer, "setLightRequiresShadows", None)
            if not callable(request_method):
                report.update(
                    status="unsupported",
                    reason="native-shadow-request-api-unavailable",
                    lastError="MRenderer.setLightRequiresShadows is unavailable",
                )
                return self.report()

            lights = []
            requested = []
            for shape, candidates in candidate_groups:
                light_report = {
                    "shape": self._light_name(shape),
                    "objectType": None,
                    "requestObject": None,
                    "requestSucceeded": False,
                    "releaseSucceeded": False,
                    "lastError": None,
                }
                light_object = None
                for object_type, object_name, candidate in candidates:
                    try:
                        result = request_method(candidate, True)
                        if result is False:
                            raise RuntimeError(
                                "MRenderer.setLightRequiresShadows returned False"
                            )
                    except Exception as exc:
                        light_report["lastError"] = f"{type(exc).__name__}: {exc!r}"
                        continue
                    light_object = candidate
                    light_report["objectType"] = object_type
                    light_report["requestObject"] = self._light_name(object_name)
                    break
                if light_object is None:
                    lights.append(light_report)
                    continue
                light_report["requestSucceeded"] = True
                lights.append(light_report)
                requested.append((shape, light_object, light_report))

            report["lights"] = lights
            report["lightCount"] = len(lights)
            if len(requested) != len(candidate_groups):
                for _shape, light_object, light_report in requested:
                    try:
                        result = request_method(light_object, False)
                        if result is False:
                            raise RuntimeError(
                                "MRenderer.setLightRequiresShadows release returned False"
                            )
                        light_report["requestSucceeded"] = False
                        light_report["releaseSucceeded"] = True
                    except Exception as exc:
                        light_report["lastError"] = f"{type(exc).__name__}: {exc!r}"
                        self._requested_lights.append((_shape, light_object, light_report))
                report.update(
                    status="unsupported",
                    reason="native-shadow-request-failed",
                    requestSucceeded=False,
                    lastError="one or more directional-light requests failed",
                )
                return self.report()

            self._requested_lights = requested
            report.update(
                status="requested",
                reason="native-shadow-requested",
                requestSucceeded=True,
            )
        except Exception as exc:
            report.update(
                status="unsupported",
                reason="native-shadow-request-failed",
                requestSucceeded=False,
                lastError=f"{type(exc).__name__}: {exc!r}",
            )
        return self.report()

    def release(self) -> dict:
        """Release every native shadow request, retaining failed ownership for retry."""
        if not self._requested_lights:
            return self.report()
        self._report["releaseAttemptCount"] += 1
        renderer = getattr(self._render_module, "MRenderer", None)
        release_method = getattr(renderer, "setLightRequiresShadows", None)
        if not callable(release_method):
            self._report.update(
                status="unsupported",
                reason="native-shadow-release-api-unavailable",
                releaseSucceeded=False,
                lastError="MRenderer.setLightRequiresShadows is unavailable",
            )
            return self.report()

        remaining = []
        for shape, light_object, light_report in self._requested_lights:
            try:
                result = release_method(light_object, False)
                if result is False:
                    raise RuntimeError(
                        "MRenderer.setLightRequiresShadows release returned False"
                    )
            except Exception as exc:
                light_report["lastError"] = f"{type(exc).__name__}: {exc!r}"
                light_report["releaseSucceeded"] = False
                remaining.append((shape, light_object, light_report))
                continue
            light_report["releaseSucceeded"] = True

        self._requested_lights = remaining
        if remaining:
            self._report.update(
                status="unsupported",
                reason="native-shadow-release-failed",
                releaseSucceeded=False,
                lastError="one or more directional-light requests remained owned",
            )
        else:
            self._report.update(
                status="released",
                reason="native-shadow-request-released",
                releaseSucceeded=True,
                lastError=None,
            )
        return self.report()

    def has_owned_requests(self) -> bool:
        """Return whether a failed release still owns native light requests."""
        return bool(self._requested_lights)

    def report(self) -> dict:
        """Return a JSON-safe copy of native request lifecycle diagnostics."""
        report = dict(self._report)
        report["lights"] = [dict(light) for light in self._report["lights"]]
        return report


_MUserRenderOperationBase = getattr(omr, "MUserRenderOperation", omr.MSceneRender)


class NativeShadowRequestRender(_MUserRenderOperationBase):
    """Queue Maya native light shadows from a real VP2 draw context."""

    def __init__(self, request: NativeShadowRequest):
        super().__init__("mmdToolsNativeShadowRequest")
        self._request = request
        self._execute_count = 0

    def requiresLightData(self):
        """Ask VP2 to populate active-light information for ``execute``."""
        return True

    def execute(self, context):
        """Request directional-light shadow maps before the scene operation."""
        self._execute_count += 1
        self._request.request(context)

    @property
    def execute_count(self) -> int:
        """Return the number of native shadow prepass callbacks observed."""
        return self._execute_count


class ShadowTargetResources:
    """Own fixed-size R32F/D32 VP2 targets for the R2 architecture probe.

    These resources are deliberately opt-in and do not implement MMD shadow
    projection or composition.  They establish target ownership and expose a
    conservative caster occupancy diagnostic from D32/R32F readback.
    """

    def __init__(self):
        self._descriptions = {
            "color": omr.MRenderTargetDescription(
                SHADOW_COLOR_TARGET_NAME,
                SHADOW_TARGET_SIZE,
                SHADOW_TARGET_SIZE,
                1,
                omr.MRenderer.kR32_FLOAT,
                0,
                False,
            ),
            "depth": omr.MRenderTargetDescription(
                SHADOW_DEPTH_TARGET_NAME,
                SHADOW_TARGET_SIZE,
                SHADOW_TARGET_SIZE,
                1,
                omr.MRenderer.kD32_FLOAT,
                0,
                False,
            ),
        }
        self._targets = {"color": None, "depth": None}
        self._acquire_count = 0
        self._release_count = 0
        self._last_error = None
        self._color_clear_sample = None
        self._readback_count = 0
        self._occupancy = self._empty_occupancy(
            status="not-run", reason="not-evaluated"
        )
        self._color_occupancy = self._empty_occupancy(
            status="not-run", reason="not-evaluated"
        )
        self._depth_occupancy = self._empty_occupancy(
            status="not-run", reason="not-evaluated"
        )
        self._occupancy_captured = False

    def acquire(self) -> tuple:
        """Acquire the R32F color and D32 depth targets for one viewport frame."""
        manager = omr.MRenderer.getRenderTargetManager()
        if manager is None:
            raise RuntimeError("Viewport 2.0 render target manager is unavailable")
        self._color_clear_sample = None
        self._occupancy = self._empty_occupancy(
            status="not-run", reason="not-rendered"
        )
        self._color_occupancy = self._empty_occupancy(
            status="not-run", reason="not-rendered"
        )
        self._depth_occupancy = self._empty_occupancy(
            status="not-run", reason="not-rendered"
        )
        self._occupancy_captured = False
        acquired = []
        try:
            for role, description in self._descriptions.items():
                target = self._targets[role]
                if target is None:
                    target = manager.acquireRenderTarget(description)
                    if target is None:
                        raise RuntimeError(f"failed to acquire {role} shadow target")
                    self._targets[role] = target
                else:
                    target.updateDescription(description)
                acquired.append(target)
        except Exception as exc:
            self._last_error = str(exc)
            self.release()
            raise
        self._acquire_count += len(acquired)
        return tuple(acquired)

    def release(self) -> None:
        """Release all owned targets and leave no VP2 resource retained."""
        manager = omr.MRenderer.getRenderTargetManager()
        for role, target in tuple(self._targets.items()):
            if target is None:
                continue
            if manager is None:
                self._last_error = "Viewport 2.0 render target manager disappeared before release"
                # The renderer has already invalidated its ownership boundary.
                # Retaining the Python wrapper would let a later setup attempt
                # call updateDescription on an invalid target, so fail closed.
                self._targets[role] = None
                continue
            try:
                manager.releaseRenderTarget(target)
                self._release_count += 1
            finally:
                self._targets[role] = None

    def capture_color_clear_sample(self) -> None:
        """Read one R32F texel after the probe clear for a numeric contract check."""
        self._color_clear_sample = self._read_color_clear_sample()
        self._readback_count += 1

    def capture_color_occupancy(self, caster_selection: Optional[dict]) -> dict:
        """Report a cheap first-sample R32F check without scanning the texture."""
        selection_status = (
            caster_selection.get("status")
            if isinstance(caster_selection, dict)
            else None
        )
        selected_count = (
            caster_selection.get("count", 0)
            if isinstance(caster_selection, dict)
            else 0
        )
        if not isinstance(selected_count, int) or selected_count < 0:
            return self._set_color_occupancy(
                status="unsupported",
                reason="invalid-caster-selection",
                selected_count=0,
            )
        if selection_status == "not-run" or caster_selection is None:
            return self._set_color_occupancy(
                status="not-run",
                reason="caster-selection-not-run",
                selected_count=selected_count,
            )
        if selection_status == "empty" and selected_count == 0:
            return self._set_color_occupancy(
                status="empty",
                reason="no-components",
                selected_count=0,
            )
        if selection_status != "ok" or selected_count < 1:
            return self._set_color_occupancy(
                status="unsupported",
                reason="caster-selection-invalid",
                selected_count=selected_count,
            )

        try:
            first_sample = self._read_color_clear_sample()
        except Exception as exc:
            return self._set_color_occupancy(
                status="unsupported",
                reason="color-readback-unavailable",
                selected_count=selected_count,
                error=str(exc),
            )
        self._color_clear_sample = first_sample
        self._readback_count += 1
        return self._set_color_occupancy(
            status="unsupported",
            reason="r32f-occupancy-scan-disabled",
            selected_count=selected_count,
            sampleCount=1,
            nonClearSampleCount=0,
            nonFiniteSampleCount=0 if math.isfinite(first_sample) else 1,
            firstSample=first_sample,
            minSample=first_sample if math.isfinite(first_sample) else None,
            maxSample=first_sample if math.isfinite(first_sample) else None,
        )

    def capture_depth_occupancy(self, caster_selection: Optional[dict]) -> dict:
        """Classify selected caster draw evidence from the D32 target.

        Depth is the conservative draw witness for this operation.  It proves
        that VP2 submitted selected geometry into the target pair, while the
        nested color report remains the only R32F-value claim.
        """
        selection_status = (
            caster_selection.get("status")
            if isinstance(caster_selection, dict)
            else None
        )
        selected_count = (
            caster_selection.get("count", 0)
            if isinstance(caster_selection, dict)
            else 0
        )
        if not isinstance(selected_count, int) or selected_count < 0:
            return self._set_depth_occupancy(
                status="unsupported", reason="invalid-caster-selection", selected_count=0
            )
        if selection_status == "not-run" or caster_selection is None:
            return self._set_depth_occupancy(
                status="not-run", reason="caster-selection-not-run", selected_count=selected_count
            )
        if selection_status == "empty" and selected_count == 0:
            return self._set_depth_occupancy(
                status="empty", reason="no-components", selected_count=0
            )
        if selection_status != "ok" or selected_count < 1:
            return self._set_depth_occupancy(
                status="unsupported", reason="caster-selection-invalid", selected_count=selected_count
            )

        try:
            samples = self._read_depth_samples()
        except Exception as exc:
            return self._set_depth_occupancy(
                status="unsupported",
                reason="depth-readback-unavailable",
                selected_count=selected_count,
                error=str(exc),
            )
        self._readback_count += 1
        if samples["nonFiniteSampleCount"]:
            return self._set_depth_occupancy(
                status="unsupported",
                reason="non-finite-depth-readback",
                selected_count=selected_count,
                **samples,
            )
        if samples["belowClearSampleCount"]:
            return self._set_depth_occupancy(
                status="occupied",
                reason="depth-below-clear",
                selected_count=selected_count,
                evidenceTarget=SHADOW_DEPTH_TARGET_NAME,
                **samples,
            )
        return self._set_depth_occupancy(
            status="unsupported",
            reason="all-clear-depth-after-caster-selection",
            selected_count=selected_count,
            evidenceTarget=SHADOW_DEPTH_TARGET_NAME,
            **samples,
        )

    def capture_target_occupancy(self, caster_selection: Optional[dict]) -> dict:
        """Capture D32 draw evidence plus independent R32F value evidence."""
        if self._occupancy_captured:
            return dict(self._occupancy)
        depth = self.capture_depth_occupancy(caster_selection)
        color = self.capture_color_occupancy(caster_selection)
        report = dict(depth)
        report["evidenceTarget"] = SHADOW_DEPTH_TARGET_NAME
        report["colorOccupancy"] = dict(color)
        report["depthOccupancy"] = dict(depth)
        self._occupancy = report
        self._occupancy_captured = True
        return dict(report)

    def _read_depth_samples(self) -> dict:
        """Read and summarize every D32 texel while the target is valid."""
        target = self._targets["depth"]
        if target is None:
            raise RuntimeError("R2 depth target is unavailable for occupancy readback")
        description = self._descriptions["depth"]
        width = int(description.width())
        height = int(description.height())
        if width < 1 or height < 1:
            raise RuntimeError("R2 depth target has invalid dimensions")
        raw_data, row_pitch, slice_pitch = target.rawData()
        if not raw_data:
            raise RuntimeError("R2 color target rawData returned no buffer")
        try:
            pointer = int(raw_data)
            pitch = int(row_pitch)
            total_pitch = int(slice_pitch)
            minimum_pitch = width * ctypes.sizeof(ctypes.c_float)
            if (
                pointer <= 0
                or pitch < minimum_pitch
                or total_pitch < pitch * height
            ):
                raise RuntimeError("R2 depth target rawData has invalid address or pitch")
            first_sample = ctypes.c_float.from_address(pointer).value
            sample_count = width * height
            non_clear_count = 0
            below_clear_count = 0
            non_finite_count = 0
            minimum = math.inf
            maximum = -math.inf
            for row in range(height):
                row_address = pointer + row * pitch
                for column in range(width):
                    value = ctypes.c_float.from_address(
                        row_address + column * ctypes.sizeof(ctypes.c_float)
                    ).value
                    if not math.isfinite(value):
                        non_finite_count += 1
                        continue
                    minimum = min(minimum, value)
                    maximum = max(maximum, value)
                    if not math.isclose(value, SHADOW_CLEAR_VALUE, abs_tol=1e-6):
                        non_clear_count += 1
                    if value < SHADOW_CLEAR_VALUE - SHADOW_DEPTH_CLEAR_EPSILON:
                        below_clear_count += 1
            return {
                "sampleCount": sample_count,
                "nonClearSampleCount": non_clear_count,
                "belowClearSampleCount": below_clear_count,
                "nonFiniteSampleCount": non_finite_count,
                "firstSample": first_sample,
                "minSample": None if minimum == math.inf else minimum,
                "maxSample": None if maximum == -math.inf else maximum,
            }
        finally:
            omr.MRenderTarget.freeRawData(raw_data)

    def _read_color_clear_sample(self) -> float:
        """Read only the first R32F texel for the legacy clear contract."""
        target = self._targets["color"]
        if target is None:
            raise RuntimeError("R2 color target is unavailable for clear readback")
        raw_data, _row_pitch, _slice_pitch = target.rawData()
        if not raw_data:
            raise RuntimeError("R2 color target rawData returned no buffer")
        try:
            pointer = int(raw_data)
            if pointer <= 0:
                raise RuntimeError("R2 color target rawData returned an invalid address")
            return ctypes.c_float.from_address(pointer).value
        finally:
            omr.MRenderTarget.freeRawData(raw_data)

    @staticmethod
    def _empty_occupancy(status: str, reason: str) -> dict:
        """Return the stable target-occupancy diagnostic shape."""
        return {
            "status": status,
            "reason": reason,
            "selectedCasterCount": 0,
            "sampleCount": 0,
            "nonClearSampleCount": 0,
            "nonFiniteSampleCount": 0,
            "clearValue": SHADOW_CLEAR_VALUE,
            "firstSample": None,
            "minSample": None,
            "maxSample": None,
        }

    def _set_color_occupancy(
        self, *, status: str, reason: str, selected_count: int, **values
    ) -> dict:
        """Store and return one JSON-safe occupancy result."""
        report = self._empty_occupancy(status=status, reason=reason)
        report["selectedCasterCount"] = selected_count
        report.update(values)
        self._color_occupancy = report
        self._occupancy = report
        return dict(report)

    def _set_depth_occupancy(
        self, *, status: str, reason: str, selected_count: int, **values
    ) -> dict:
        """Store and return one D32 depth occupancy result."""
        report = self._empty_occupancy(status=status, reason=reason)
        report["selectedCasterCount"] = selected_count
        report.update(values)
        self._depth_occupancy = report
        self._occupancy = report
        return dict(report)

    def report(self) -> dict:
        """Return deterministic resource-contract diagnostics for the R2 probe."""
        return {
            "enabled": True,
            "color": self._description_report("color"),
            "depth": self._description_report("depth"),
            "clearDepth": SHADOW_CLEAR_VALUE,
            "acquireCount": self._acquire_count,
            "releaseCount": self._release_count,
            "balanced": self._acquire_count == self._release_count,
            "colorClearSample": self._color_clear_sample,
            "readbackCount": self._readback_count,
            "occupancy": dict(self._occupancy),
            "colorOccupancy": dict(self._color_occupancy),
            "depthOccupancy": dict(self._depth_occupancy),
            "lastError": self._last_error,
        }

    def _description_report(self, role: str) -> dict:
        description = self._descriptions[role]
        return {
            "name": description.name(),
            "width": description.width(),
            "height": description.height(),
            "samples": description.multiSampleCount(),
            "rasterFormat": description.rasterFormat(),
        }


class ReceiverProbeResources:
    """Own the separate R32F output used by the receiver binding probe."""

    def __init__(self):
        self._description = omr.MRenderTargetDescription(
            RECEIVER_PROBE_TARGET_NAME,
            RECEIVER_PROBE_TARGET_SIZE,
            RECEIVER_PROBE_TARGET_SIZE,
            1,
            omr.MRenderer.kR32_FLOAT,
            0,
            False,
        )
        self._target = None
        self._acquire_count = 0
        self._release_count = 0
        self._readback_count = 0
        self._sample = None
        self._sample_details = {}
        self._last_error = None

    def acquire(self):
        """Acquire or refresh the output target for one receiver probe."""
        manager = omr.MRenderer.getRenderTargetManager()
        if manager is None:
            raise RuntimeError("Viewport 2.0 render target manager is unavailable")
        try:
            if self._target is None:
                self._target = manager.acquireRenderTarget(self._description)
                if self._target is None:
                    raise RuntimeError("failed to acquire receiver probe target")
            else:
                self._target.updateDescription(self._description)
        except Exception as exc:
            self._last_error = str(exc)
            self.release()
            raise
        self._sample = None
        self._sample_details = {}
        self._last_error = None
        self._acquire_count += 1
        return self._target

    @property
    def target(self):
        """Return the currently owned output target, if any."""
        return self._target

    def release(self) -> None:
        """Release the output target after the receiver shader is detached."""
        target = self._target
        if target is None:
            return
        manager = omr.MRenderer.getRenderTargetManager()
        if manager is None:
            self._last_error = "Viewport 2.0 render target manager disappeared before release"
            return
        try:
            manager.releaseRenderTarget(target)
        except Exception as exc:
            self._last_error = str(exc)
            return
        self._target = None
        self._release_count += 1

    def capture_sample(self) -> dict:
        """Scan the small output target while it is still owned."""
        if self._target is None:
            if self._sample is not None:
                return self.report()
            self._last_error = "receiver-target-unavailable"
            return self.report(status="unsupported", reason=self._last_error)
        raw_data = None
        try:
            raw_data, row_pitch, slice_pitch = self._target.rawData()
            if not raw_data:
                raise RuntimeError("receiver probe target rawData returned no buffer")
            pointer = int(raw_data)
            width = int(self._description.width())
            height = int(self._description.height())
            pitch = int(row_pitch)
            if pointer <= 0 or pitch < width * ctypes.sizeof(ctypes.c_float):
                raise RuntimeError("receiver probe target rawData has invalid address or pitch")
            values = [
                ctypes.c_float.from_address(
                    pointer + row * pitch + column * ctypes.sizeof(ctypes.c_float)
                ).value
                for row in range(height)
                for column in range(width)
            ]
            finite = [value for value in values if math.isfinite(value)]
            self._sample = float(values[0]) if values else None
            non_clear = sum(
                1
                for value in finite
                if not math.isclose(value, SHADOW_CLEAR_VALUE, abs_tol=1e-6)
            )
        except Exception as exc:
            self._last_error = str(exc)
            return self.report(status="unsupported", reason="receiver-readback-failed")
        finally:
            if raw_data:
                omr.MRenderTarget.freeRawData(raw_data)
        self._readback_count += 1
        self._last_error = None
        self._sample_details = {
            "changedFromClear": non_clear > 0,
            "sampleCount": len(values),
            "nonClearSampleCount": non_clear,
            "nonFiniteSampleCount": len(values) - len(finite),
            "minSample": min(finite) if finite else None,
            "maxSample": max(finite) if finite else None,
        }
        return self.report(
            status="sampled",
            reason="receiver-output-readback",
            **self._sample_details,
        )

    def report(self, **values) -> dict:
        """Return JSON-safe receiver target ownership and sample diagnostics."""
        report = {
            "enabled": True,
            "target": {
                "name": self._description.name(),
                "width": self._description.width(),
                "height": self._description.height(),
                "samples": self._description.multiSampleCount(),
                "rasterFormat": self._description.rasterFormat(),
            },
            "acquireCount": self._acquire_count,
            "releaseCount": self._release_count,
            "balanced": self._acquire_count == self._release_count,
            "readbackCount": self._readback_count,
            "sample": self._sample,
            "status": "not-run" if self._sample is None else "sampled",
            "reason": "not-rendered" if self._sample is None else "receiver-output-readback",
            "lastError": self._last_error,
        }
        report.update(self._sample_details)
        report.update(values)
        return report


class NativeShadowBindingProbeResources(ReceiverProbeResources):
    """Own the separate R32F target used by the native shadow binding probe."""

    def __init__(self):
        super().__init__()
        self._description = omr.MRenderTargetDescription(
            NATIVE_SHADOW_BINDING_PROBE_TARGET_NAME,
            NATIVE_SHADOW_BINDING_PROBE_TARGET_SIZE,
            NATIVE_SHADOW_BINDING_PROBE_TARGET_SIZE,
            1,
            omr.MRenderer.kR32_FLOAT,
            0,
            False,
        )

    def report(self, **values) -> dict:
        """Use native-shadow wording for the inherited readback contract."""
        report = super().report(**values)
        if report.get("reason") == "receiver-output-readback":
            report["reason"] = "native-shadow-binding-output-readback"
        return report


class R32FTargetBindingProbe:
    """Own a diagnostic-only shader instance bound to the R32F target.

    This deliberately never enters a render operation and never replaces an
    imported ``dx11Shader``.  Its only purpose is to prove whether VP2 accepts
    an ``MRenderTarget`` through ``MShaderInstance.setParameter`` while the
    plugin owns both objects.  A later receiver pass must be separately
    designed and validated; a successful binding is not self-shadow evidence.
    """

    def __init__(self):
        self._shader = None
        self._shader_manager = None
        self._report = self._empty_report()

    @staticmethod
    def _empty_report() -> dict:
        """Return the stable, explicitly non-rendering diagnostic shape."""
        return {
            "enabled": True,
            "status": "not-run",
            "reason": "not-setup",
            "targetName": SHADOW_COLOR_TARGET_NAME,
            "parameter": R32F_BINDING_PROBE_PARAMETER,
            "shaderPath": str(R32F_BINDING_PROBE_SHADER_PATH),
            "bindingAttemptCount": 0,
            "bindingSucceeded": False,
            "releaseAttemptCount": 0,
            "releaseSucceeded": False,
            "drawsReceiver": False,
            "lastError": None,
        }

    def bind(self, target) -> dict:
        """Create, bind, and retain a plugin-owned shader for one VP2 setup.

        A missing target, manager, asset, shader, or named parameter all fail
        closed and retain no shader instance.  The target is not sampled or
        rendered by this probe.
        """
        previous_release = self.release()
        report = self._report
        if self.has_owned_shader():
            report.update(
                status="unsupported",
                reason="previous-shader-release-failed",
                lastError=previous_release.get("lastError"),
                bindingSucceeded=False,
            )
            return self.report()
        report["status"] = "not-run"
        report["reason"] = "target-unavailable"
        report["lastError"] = None
        report["bindingSucceeded"] = False
        report["releaseSucceeded"] = False
        if target is None:
            return self.report()
        report["bindingAttemptCount"] += 1
        draw_api_getter = getattr(omr.MRenderer, "drawAPI", None)
        if callable(draw_api_getter):
            try:
                draw_api = draw_api_getter()
            except Exception as exc:
                report.update(
                    status="unsupported",
                    reason="draw-api-query-failed",
                    lastError=str(exc),
                )
                return self.report()
            if draw_api != getattr(omr.MRenderer, "kDirectX11", draw_api):
                report.update(
                    status="unsupported",
                    reason="directx11-only-shader-probe",
                    lastError=f"draw API {draw_api!r} is not DirectX11",
                )
                return self.report()
        if not R32F_BINDING_PROBE_SHADER_PATH.is_file():
            report.update(status="unsupported", reason="shader-asset-missing")
            return self.report()
        try:
            shader_manager = omr.MRenderer.getShaderManager()
        except Exception as exc:
            report.update(
                status="unsupported",
                reason="shader-manager-unavailable",
                lastError=str(exc),
            )
            return self.report()
        if shader_manager is None:
            report.update(status="unsupported", reason="shader-manager-unavailable")
            return self.report()
        shader = None
        try:
            shader = shader_manager.getEffectsFileShader(
                str(R32F_BINDING_PROBE_SHADER_PATH), R32F_BINDING_PROBE_TECHNIQUE
            )
            if shader is None:
                report.update(status="unsupported", reason="shader-create-failed")
                return self.report()
            parameter_list = tuple(shader.parameterList())
            if R32F_BINDING_PROBE_PARAMETER not in parameter_list:
                self._shader = shader
                self._shader_manager = shader_manager
                release_report = self.release()
                if release_report["releaseSucceeded"]:
                    report.update(
                        status="unsupported", reason="target-parameter-unavailable"
                    )
                return self.report()
            shader.setParameter(R32F_BINDING_PROBE_PARAMETER, target)
        except Exception as exc:
            if shader is not None:
                self._shader = shader
                self._shader_manager = shader_manager
                release_report = self.release()
                report.update(
                    status="unsupported",
                    reason=(
                        "target-binding-failed"
                        if release_report["releaseSucceeded"]
                        else "shader-release-failed"
                    ),
                    lastError=(
                        str(exc)
                        if release_report["releaseSucceeded"]
                        else f"{exc}; {release_report.get('lastError')}"
                    ),
                )
            else:
                report.update(
                    status="unsupported", reason="target-binding-failed", lastError=str(exc)
                )
            return self.report()
        self._shader = shader
        self._shader_manager = shader_manager
        report.update(status="bound", reason="target-set-parameter", bindingSucceeded=True)
        return self.report()

    def release(self) -> dict:
        """Release the owned shader before the target itself is released."""
        shader = self._shader
        shader_manager = self._shader_manager
        if shader is None:
            return self.report()
        self._report["releaseAttemptCount"] += 1
        try:
            if shader_manager is None:
                raise RuntimeError("shader manager disappeared before shader release")
            shader_manager.releaseShader(shader)
        except Exception as exc:
            self._report.update(
                status="unsupported", reason="shader-release-failed", lastError=str(exc)
            )
        else:
            self._shader = None
            self._shader_manager = None
            self._report.update(
                status="released", reason="shader-released", releaseSucceeded=True
            )
        return self.report()

    def has_owned_shader(self) -> bool:
        """Return whether cleanup must retain the target for a release retry."""
        return self._shader is not None

    def report(self) -> dict:
        """Return a JSON-safe binding lifecycle report without render claims."""
        return dict(self._report)


class R32FCasterShaderPass:
    """Own the opt-in HLSL shader used only by the R32F/D32 caster operation.

    The shader instance is created after the target pair is acquired and is
    released before those targets are detached or released.  A failed create
    or release never falls back to Maya's ordinary material shading: the
    operation reports an empty selection until the lifecycle can be retried.
    This pass only writes caster depth; receiver composition and self-shadow
    parity remain explicitly false in the diagnostic report.
    """

    def __init__(self):
        self._shader = None
        self._shader_manager = None
        self._report = self._empty_report()

    @staticmethod
    def _empty_report() -> dict:
        """Return stable lifecycle diagnostics for the caster shader."""
        return {
            "enabled": True,
            "status": "not-run",
            "reason": "not-setup",
            "targetNames": [SHADOW_COLOR_TARGET_NAME, SHADOW_DEPTH_TARGET_NAME],
            "shaderPath": str(R32F_CASTER_PASS_SHADER_PATH),
            "technique": R32F_CASTER_PASS_TECHNIQUE,
            "requiredParameter": R32F_CASTER_PASS_PARAMETER,
            "createAttemptCount": 0,
            "createSucceeded": False,
            "releaseAttemptCount": 0,
            "releaseSucceeded": False,
            "releaseBeforeTarget": False,
            "drawsReceiver": False,
            "claimsSelfShadow": False,
            "receiverComposition": False,
            "lastError": None,
        }

    def create(self, targets: Optional[Tuple]) -> dict:
        """Create and retain the caster shader for one acquired target pair."""
        previous_release = self.release()
        if self.has_owned_shader():
            self._report.update(
                status="unsupported",
                reason="previous-shader-release-failed",
                lastError=previous_release.get("lastError"),
                createSucceeded=False,
            )
            return self.report()

        report = self._report
        report.update(
            status="not-run",
            reason="target-pair-unavailable",
            createSucceeded=False,
            releaseSucceeded=False,
            releaseBeforeTarget=False,
            lastError=None,
        )
        if not isinstance(targets, (tuple, list)) or len(targets) != 2 or any(
            target is None for target in targets
        ):
            return self.report()

        report["createAttemptCount"] += 1
        draw_api_getter = getattr(omr.MRenderer, "drawAPI", None)
        if callable(draw_api_getter):
            try:
                draw_api = draw_api_getter()
            except Exception as exc:
                report.update(
                    status="unsupported", reason="draw-api-query-failed", lastError=str(exc)
                )
                return self.report()
            if draw_api != getattr(omr.MRenderer, "kDirectX11", draw_api):
                report.update(
                    status="unsupported",
                    reason="directx11-only-caster-pass",
                    lastError=f"draw API {draw_api!r} is not DirectX11",
                )
                return self.report()

        if not R32F_CASTER_PASS_SHADER_PATH.is_file():
            report.update(status="unsupported", reason="shader-asset-missing")
            return self.report()
        try:
            shader_manager = omr.MRenderer.getShaderManager()
        except Exception as exc:
            report.update(
                status="unsupported",
                reason="shader-manager-unavailable",
                lastError=str(exc),
            )
            return self.report()
        if shader_manager is None:
            report.update(status="unsupported", reason="shader-manager-unavailable")
            return self.report()

        shader = None
        try:
            shader = shader_manager.getEffectsFileShader(
                str(R32F_CASTER_PASS_SHADER_PATH), R32F_CASTER_PASS_TECHNIQUE
            )
            if shader is None:
                report.update(status="unsupported", reason="shader-create-failed")
                return self.report()
            # ``WorldViewProjection`` is a Maya effect semantic.  Maya's
            # shader manager binds it from the operation/camera context, but
            # it does not expose every semantic in ``parameterList()`` (the
            # live DX11 effect omits it).  Treat successful effect creation as
            # the authoritative availability check rather than rejecting a
            # valid semantic-only shader instance.
        except Exception as exc:
            if shader is not None:
                self._shader = shader
                self._shader_manager = shader_manager
                release_report = self.release()
                report.update(
                    status="unsupported",
                    reason=(
                        "shader-create-failed"
                        if release_report["releaseSucceeded"]
                        else "shader-release-failed"
                    ),
                    lastError=(
                        str(exc)
                        if release_report["releaseSucceeded"]
                        else f"{exc}; {release_report.get('lastError')}"
                    ),
                )
            else:
                report.update(
                    status="unsupported", reason="shader-create-failed", lastError=str(exc)
                )
            return self.report()

        self._shader = shader
        self._shader_manager = shader_manager
        report.update(status="created", reason="shader-created", createSucceeded=True)
        return self.report()

    def release(self) -> dict:
        """Release the shader while its target pair is still owned."""
        shader = self._shader
        shader_manager = self._shader_manager
        if shader is None:
            return self.report()
        self._report["releaseAttemptCount"] += 1
        try:
            if shader_manager is None:
                raise RuntimeError("shader manager disappeared before shader release")
            shader_manager.releaseShader(shader)
        except Exception as exc:
            self._report.update(
                status="unsupported",
                reason="shader-release-failed",
                releaseSucceeded=False,
                releaseBeforeTarget=False,
                lastError=str(exc),
            )
        else:
            self._shader = None
            self._shader_manager = None
            self._report.update(
                status="released",
                reason="shader-released-before-target",
                releaseSucceeded=True,
                releaseBeforeTarget=True,
                lastError=None,
            )
        return self.report()

    def shader_instance(self):
        """Return the retained instance, or ``None`` until create succeeds."""
        return self._shader

    def has_owned_shader(self) -> bool:
        """Return whether cleanup must defer target release for a retry."""
        return self._shader is not None

    def report(self) -> dict:
        """Return JSON-safe lifecycle diagnostics without receiver claims."""
        return dict(self._report)


_MQuadRenderBase = getattr(omr, "MQuadRender", omr.MSceneRender)


class R32FReceiverProbe(_MQuadRenderBase):
    """Sample the caster target into a separate output with ``MQuadRender``.

    This operation is deliberately diagnostic: it proves that a plugin-owned
    R32F target can be bound as a quad input and read back from a distinct
    output target.  It does not replace imported MMD materials or compose a
    self-shadow term into the viewport.
    """

    def __init__(self, resources: ReceiverProbeResources):
        super().__init__(RECEIVER_PROBE_OPERATION_NAME)
        self._resources = resources
        self._shader = None
        self._shader_manager = None
        self._report = {
            "enabled": True,
            "status": "not-run",
            "reason": "not-setup",
            "inputTargetName": SHADOW_COLOR_TARGET_NAME,
            "outputTargetName": RECEIVER_PROBE_TARGET_NAME,
            "shaderPath": str(R32F_RECEIVER_PROBE_SHADER_PATH),
            "technique": R32F_RECEIVER_PROBE_TECHNIQUE,
            "parameter": R32F_RECEIVER_PROBE_PARAMETER,
            "outputTransform": "one-minus-16x16-min-sampled-value",
            "createAttemptCount": 0,
            "createSucceeded": False,
            "bindAttemptCount": 0,
            "bindSucceeded": False,
            "postDrawCallbackCount": 0,
            "manualReadbackCount": 0,
            "releaseAttemptCount": 0,
            "releaseSucceeded": False,
            "releaseBeforeTarget": False,
            "drawsReceiver": True,
            "receiverComposition": False,
            "claimsSelfShadow": False,
            "lastError": None,
        }

    def targetOverrideList(self):
        """Route the quad output to a target distinct from the caster input."""
        target = self._resources.target
        return [target] if target is not None else None

    def clearOperation(self):
        """Clear the diagnostic output to the one-minus probe's far value."""
        clear = super().clearOperation()
        clear.setClearColor((SHADOW_CLEAR_VALUE,) * 4)
        clear.setClearGradient(False)
        clear.setMask(omr.MClearOperation.kClearColor)
        return clear

    def shader(self):
        """Return the plugin-owned receiver probe shader instance."""
        return self._shader

    def create(self, input_target) -> dict:
        """Create the quad shader and bind the caster target as its input."""
        previous_release = self.release_shader()
        if self.has_owned_shader():
            self._report.update(
                status="unsupported",
                reason="previous-shader-release-failed",
                lastError=previous_release.get("lastError"),
                createSucceeded=False,
            )
            return self.report()
        report = self._report
        report.update(
            status="not-run",
            reason="input-target-unavailable",
            createSucceeded=False,
            bindSucceeded=False,
            releaseSucceeded=False,
            releaseBeforeTarget=False,
            lastError=None,
        )
        if input_target is None or self._resources.target is None:
            return self.report()
        report["createAttemptCount"] += 1
        draw_api_getter = getattr(omr.MRenderer, "drawAPI", None)
        if callable(draw_api_getter):
            try:
                draw_api = draw_api_getter()
            except Exception as exc:
                report.update(status="unsupported", reason="draw-api-query-failed", lastError=str(exc))
                return self.report()
            if draw_api != getattr(omr.MRenderer, "kDirectX11", draw_api):
                report.update(
                    status="unsupported",
                    reason="directx11-only-receiver-probe",
                    lastError=f"draw API {draw_api!r} is not DirectX11",
                )
                return self.report()
        if not R32F_RECEIVER_PROBE_SHADER_PATH.is_file():
            report.update(status="unsupported", reason="shader-asset-missing")
            return self.report()
        try:
            shader_manager = omr.MRenderer.getShaderManager()
            if shader_manager is None:
                raise RuntimeError("shader manager unavailable")
            shader = shader_manager.getEffectsFileShader(
                str(R32F_RECEIVER_PROBE_SHADER_PATH),
                R32F_RECEIVER_PROBE_TECHNIQUE,
                None,
                True,
                None,
                self._post_draw,
            )
            if shader is None:
                report.update(status="unsupported", reason="shader-create-failed")
                return self.report()
            self._shader = shader
            self._shader_manager = shader_manager
            report["createSucceeded"] = True
            report["bindAttemptCount"] += 1
            shader.setParameter(R32F_RECEIVER_PROBE_PARAMETER, input_target)
            report.update(
                status="created",
                reason="shader-created-and-target-bound",
                bindSucceeded=True,
            )
        except Exception as exc:
            release_report = self.release_shader()
            report.update(
                status="unsupported",
                reason=(
                    "shader-release-failed"
                    if not release_report["releaseSucceeded"] and self.has_owned_shader()
                    else "receiver-target-bind-failed"
                ),
                lastError=(
                    str(exc)
                    if release_report["releaseSucceeded"] or not self.has_owned_shader()
                    else f"{exc}; {release_report.get('lastError')}"
                ),
            )
        return self.report()

    def _post_draw(self, _context, _render_item_list, _shader) -> None:
        """Capture one output sample after VP2 finishes the quad draw."""
        self._report["postDrawCallbackCount"] += 1
        try:
            self._resources.capture_sample()
        except Exception as exc:
            self._report.update(status="unsupported", reason="receiver-readback-failed", lastError=str(exc))

    def capture_output(self) -> dict:
        """Capture the output during cleanup when VP2 has not called postCb."""
        self._report["manualReadbackCount"] += 1
        return self._resources.capture_sample()

    def release_shader(self) -> dict:
        """Release the shader while both input/output targets remain owned."""
        shader = self._shader
        manager = self._shader_manager
        if shader is None:
            return self.report()
        self._report["releaseAttemptCount"] += 1
        try:
            if manager is None:
                raise RuntimeError("shader manager disappeared before receiver release")
            manager.releaseShader(shader)
        except Exception as exc:
            self._report.update(
                status="unsupported",
                reason="receiver-shader-release-failed",
                releaseSucceeded=False,
                releaseBeforeTarget=False,
                lastError=str(exc),
            )
        else:
            self._shader = None
            self._shader_manager = None
            self._report.update(
                status="released",
                reason="receiver-shader-released-before-target",
                releaseSucceeded=True,
                releaseBeforeTarget=True,
                lastError=None,
            )
        return self.report()

    def has_owned_shader(self) -> bool:
        """Return whether cleanup must defer target release for a retry."""
        return self._shader is not None

    def report(self) -> dict:
        """Return JSON-safe receiver shader diagnostics."""
        report = dict(self._report)
        report["output"] = self._resources.report()
        return report


class NativeShadowBindingProbe(_MQuadRenderBase):
    """Bind Maya's native shadow resource to a plugin-owned diagnostic quad.

    The pre-draw callback follows Autodesk's ``py2ViewRenderOverride`` sample:
    it reads ``kShadowMap`` and ``kShadowViewProj`` from the active directional
    light and binds them with ``MShaderInstance.setParameter``.  The operation
    writes only a separate tiny R32F target; imported MMD shaders are never
    mutated and receiver/self-shadow parity remains explicitly unclaimed.
    """

    def __init__(self, resources: NativeShadowBindingProbeResources, render_module=None):
        super().__init__(NATIVE_SHADOW_BINDING_PROBE_OPERATION_NAME)
        self._resources = resources
        self._render_module = render_module or omr
        self._shader = None
        self._shader_manager = None
        self._report = self._empty_report()

    _native_shadow_map_parameter = NATIVE_SHADOW_BINDING_PROBE_MAP_PARAMETER
    _native_shadow_viewproj_parameter = NATIVE_SHADOW_BINDING_PROBE_VIEWPROJ_PARAMETER
    _native_shadow_enabled_parameter = NATIVE_SHADOW_BINDING_PROBE_ENABLED_PARAMETER

    @staticmethod
    def _empty_report() -> dict:
        """Return stable lifecycle and native-light binding diagnostics."""
        return {
            "enabled": True,
            "status": "not-run",
            "reason": "not-setup",
            "targetName": NATIVE_SHADOW_BINDING_PROBE_TARGET_NAME,
            "shaderPath": str(NATIVE_SHADOW_BINDING_PROBE_SHADER_PATH),
            "technique": NATIVE_SHADOW_BINDING_PROBE_TECHNIQUE,
            "mapParameter": NATIVE_SHADOW_BINDING_PROBE_MAP_PARAMETER,
            "viewProjParameter": NATIVE_SHADOW_BINDING_PROBE_VIEWPROJ_PARAMETER,
            "createAttemptCount": 0,
            "createSucceeded": False,
            "preDrawCallbackCount": 0,
            "contextExecuteCount": 0,
            "manualReadbackCount": 0,
            "bindingAttemptCount": 0,
            "bindingSucceeded": False,
            "resourceHandle": None,
            "releaseAttemptCount": 0,
            "releaseSucceeded": False,
            "releaseBeforeTarget": False,
            "drawsReceiver": False,
            "receiverComposition": False,
            "claimsSelfShadow": False,
            "context": {
                "status": "not-run",
                "stage": None,
                "lightingMode": None,
                "lightFilter": None,
                "lightCount": 0,
                "lights": [],
                "lastError": None,
            },
            "lastError": None,
        }

    def targetOverrideList(self):
        """Route the diagnostic quad to its separate R32F output target."""
        target = self._resources.target
        return [target] if target is not None else None

    def requiresLightData(self):
        """Ask VP2 to populate active-light information for the callback."""
        return True

    def clearOperation(self):
        """Clear the output to one so an unbound map is distinguishable."""
        clear = super().clearOperation()
        clear.setClearColor((SHADOW_CLEAR_VALUE,) * 4)
        clear.setClearGradient(False)
        clear.setMask(omr.MClearOperation.kClearColor)
        return clear

    def shader(self):
        """Return the plugin-owned native-shadow probe shader instance."""
        return self._shader

    @staticmethod
    def _semantic(render_module, name):
        info_type = getattr(render_module, "MLightParameterInformation", None)
        return getattr(info_type, name, None)

    def _bind_active_shadow(self, context, shader_instance):
        """Bind the first active directional native shadow map, if available."""
        if self._report["bindingSucceeded"]:
            return
        context_report = self._report["context"]
        context_report.update(
            status="running",
            stage="need-evaluate-all-lights",
            lightingMode=None,
            lightFilter=None,
            lightCount=0,
            lights=[],
            lastError=None,
        )
        if context is None or shader_instance is None:
            context_report.update(status="unsupported", reason="context-or-shader-unavailable")
            return

        draw_context_type = getattr(self._render_module, "MDrawContext", None)
        light_filter = getattr(draw_context_type, "kFilteredIgnoreLightLimit", None)
        context_report["lightFilter"] = light_filter
        renderer = getattr(self._render_module, "MRenderer", None)
        try:
            need_evaluate = getattr(renderer, "needEvaluateAllLights", None)
            if callable(need_evaluate):
                need_evaluate()
            lighting_mode_method = getattr(context, "getLightingMode", None)
            if callable(lighting_mode_method):
                context_report["lightingMode"] = str(lighting_mode_method())
            if light_filter is None:
                light_count = int(context.numberOfActiveLights())
            else:
                light_count = int(context.numberOfActiveLights(light_filter))
            context_report["lightCount"] = light_count
        except Exception as exc:
            context_report.update(status="unsupported", stage="light-list", lastError=str(exc))
            self._report.update(status="unsupported", reason="native-light-query-failed", lastError=str(exc))
            return

        shadow_map_semantic = self._semantic(self._render_module, "kShadowMap")
        shadow_view_proj_semantic = self._semantic(self._render_module, "kShadowViewProj")
        global_shadow_semantic = self._semantic(self._render_module, "kGlobalShadowOn")
        local_shadow_semantic = self._semantic(self._render_module, "kShadowOn")
        directional_semantic = self._semantic(self._render_module, "kWorldDirection")
        for index in range(max(light_count, 0)):
            context_report["stage"] = f"light-information-{index}"
            try:
                info = (
                    context.getLightParameterInformation(index)
                    if light_filter is None
                    else context.getLightParameterInformation(index, light_filter)
                )
            except Exception as exc:
                context_report["lights"].append({"index": index, "error": str(exc)})
                continue
            if info is None:
                context_report["lights"].append({"index": index, "error": "no-light-info"})
                continue
            light_report = {
                "index": index,
                "lightType": str(info.lightType()),
                "lightPath": None,
                "parameters": {},
                "shadowMap": False,
                "shadowViewProj": False,
                "globalShadowOn": None,
                "shadowOn": None,
            }
            try:
                parameter_list_method = getattr(shader_instance, "parameterList", None)
                if callable(parameter_list_method):
                    light_report["shaderParameters"] = [
                        str(parameter) for parameter in parameter_list_method()
                    ]
            except Exception as exc:
                light_report["shaderParameterListError"] = str(exc)
            try:
                path = info.lightPath()
                full_path_name = getattr(path, "fullPathName", None)
                if callable(full_path_name):
                    light_report["lightPath"] = full_path_name()
            except Exception as exc:
                light_report["lightPathError"] = str(exc)
            shadow_resource = None
            shadow_view_proj = None
            try:
                parameter_list = info.parameterList()
                for parameter_name in parameter_list:
                    semantic = info.parameterSemantic(parameter_name)
                    semantic_name = str(semantic)
                    try:
                        value = info.getParameter(parameter_name)
                        light_report["parameters"][str(parameter_name)] = semantic_name
                    except Exception as exc:
                        light_report["parameters"][str(parameter_name)] = f"error: {exc}"
                        continue
                    if semantic == shadow_map_semantic:
                        shadow_resource = value
                        light_report["shadowMap"] = value is not None
                    elif semantic == shadow_view_proj_semantic:
                        shadow_view_proj = value
                        light_report["shadowViewProj"] = value is not None
                    elif semantic == global_shadow_semantic:
                        light_report["globalShadowOn"] = bool(value[0]) if value else False
                    elif semantic == local_shadow_semantic:
                        light_report["shadowOn"] = bool(value[0]) if value else False
                    elif semantic == directional_semantic:
                        light_report["worldDirection"] = list(value)
            except Exception as exc:
                light_report["parameterError"] = str(exc)
            context_report["lights"].append(light_report)
            if "directional" not in light_report["lightType"].lower():
                continue
            if shadow_resource is None:
                continue
            self._report["bindingAttemptCount"] += 1
            resource_handle = None
            shader_set_results = {}
            try:
                resource_handle_method = getattr(shadow_resource, "resourceHandle", None)
                resource_handle = resource_handle_method() if callable(resource_handle_method) else None
                self._report["resourceHandle"] = int(resource_handle) if resource_handle is not None else None
                if resource_handle is not None and int(resource_handle) <= 0:
                    raise RuntimeError("native shadow resource handle is invalid")
                shader_set_results[self._native_shadow_map_parameter] = repr(
                    shader_instance.setParameter(
                    self._native_shadow_map_parameter, shadow_resource
                    )
                )
                if shadow_view_proj is not None:
                    shader_set_results[self._native_shadow_viewproj_parameter] = repr(
                        shader_instance.setParameter(
                            self._native_shadow_viewproj_parameter, shadow_view_proj
                        )
                    )
                shader_set_results[self._native_shadow_enabled_parameter] = repr(
                    shader_instance.setParameter(
                        self._native_shadow_enabled_parameter, True
                    )
                )
                light_report["shaderParameterSetResults"] = shader_set_results
                self._report.update(
                    status="bound",
                    reason="native-shadow-map-bound",
                    bindingSucceeded=True,
                    lastError=None,
                )
                context_report["status"] = "ready"
                context_report["stage"] = "complete"
            except Exception as exc:
                light_report["shaderParameterSetResults"] = dict(shader_set_results)
                light_report["shaderParameterSetError"] = f"{type(exc).__name__}: {exc!r}"
                if "nativeParameterErrors" in self._report:
                    self._report["nativeParameterErrors"].append(
                        {
                            "lightIndex": index,
                            "error": light_report["shaderParameterSetError"],
                        }
                    )
                self._report.update(
                    status="unsupported",
                    reason="native-shadow-map-bind-failed",
                    bindingSucceeded=False,
                    lastError=str(exc),
                )
            finally:
                try:
                    texture_manager = getattr(renderer, "getTextureManager", lambda: None)()
                    release_texture = getattr(texture_manager, "releaseTexture", None)
                    if callable(release_texture):
                        release_texture(shadow_resource)
                except Exception:
                    pass
            context_report["status"] = "ready"
            context_report["stage"] = "complete"
            return

        self._report.update(
            status="unsupported",
            reason="native-shadow-map-unavailable",
            bindingSucceeded=False,
        )
        context_report["stage"] = "complete"

    def _pre_draw(self, context, _render_item_list, shader_instance):
        """Bind active native light data immediately before the quad draw."""
        self._report["preDrawCallbackCount"] += 1
        self._bind_active_shadow(context, shader_instance)

    def create(self) -> dict:
        """Create the probe shader and retain it until target cleanup."""
        previous_release = self.release_shader()
        if self.has_owned_shader():
            self._report.update(
                status="unsupported",
                reason="previous-shader-release-failed",
                lastError=previous_release.get("lastError"),
                createSucceeded=False,
            )
            return self.report()
        report = self._report
        report.update(
            status="not-run",
            reason="target-unavailable",
            createSucceeded=False,
            bindingSucceeded=False,
            releaseSucceeded=False,
            releaseBeforeTarget=False,
            lastError=None,
        )
        if self._resources.target is None:
            return self.report()
        report["createAttemptCount"] += 1
        draw_api_getter = getattr(self._render_module.MRenderer, "drawAPI", None)
        if callable(draw_api_getter):
            try:
                draw_api = draw_api_getter()
            except Exception as exc:
                report.update(status="unsupported", reason="draw-api-query-failed", lastError=str(exc))
                return self.report()
            if draw_api != getattr(self._render_module.MRenderer, "kDirectX11", draw_api):
                report.update(
                    status="unsupported",
                    reason="directx11-only-native-shadow-binding-probe",
                    lastError=f"draw API {draw_api!r} is not DirectX11",
                )
                return self.report()
        if not NATIVE_SHADOW_BINDING_PROBE_SHADER_PATH.is_file():
            report.update(status="unsupported", reason="shader-asset-missing")
            return self.report()
        try:
            shader_manager = self._render_module.MRenderer.getShaderManager()
            if shader_manager is None:
                raise RuntimeError("shader manager unavailable")
            shader = shader_manager.getEffectsFileShader(
                str(NATIVE_SHADOW_BINDING_PROBE_SHADER_PATH),
                NATIVE_SHADOW_BINDING_PROBE_TECHNIQUE,
                None,
                True,
                self._pre_draw,
                None,
            )
            if shader is None:
                report.update(status="unsupported", reason="shader-create-failed")
                return self.report()
            self._shader = shader
            self._shader_manager = shader_manager
            report.update(status="created", reason="shader-created", createSucceeded=True)
        except Exception as exc:
            release_report = self.release_shader()
            report.update(
                status="unsupported",
                reason=(
                    "shader-release-failed"
                    if not release_report["releaseSucceeded"] and self.has_owned_shader()
                    else "shader-create-failed"
                ),
                lastError=str(exc),
            )
        return self.report()

    def capture_output(self) -> dict:
        """Read the diagnostic target when Maya omits the post-draw callback."""
        self._report["manualReadbackCount"] = self._report.get("manualReadbackCount", 0) + 1
        return self._resources.capture_sample()

    def release_shader(self) -> dict:
        """Release the shader while its output target remains owned."""
        shader = self._shader
        manager = self._shader_manager
        if shader is None:
            return self.report()
        self._report["releaseAttemptCount"] += 1
        try:
            if manager is None:
                raise RuntimeError("shader manager disappeared before native shadow probe release")
            manager.releaseShader(shader)
        except Exception as exc:
            self._report.update(
                status="unsupported",
                reason="shader-release-failed",
                releaseSucceeded=False,
                releaseBeforeTarget=False,
                lastError=str(exc),
            )
        else:
            self._shader = None
            self._shader_manager = None
            self._report.update(
                status="released",
                reason="shader-released-before-target",
                releaseSucceeded=True,
                releaseBeforeTarget=True,
                lastError=None,
            )
        return self.report()

    def has_owned_shader(self) -> bool:
        """Return whether cleanup must retain the shader for a release retry."""
        return self._shader is not None

    def report(self) -> dict:
        """Return lifecycle, context, and output diagnostics."""
        report = dict(self._report)
        report["context"] = dict(self._report["context"])
        report["context"]["lights"] = [dict(light) for light in self._report["context"]["lights"]]
        report["output"] = self._resources.report()
        return report


class NativeShadowBindingContextRender(_MUserRenderOperationBase):
    """Fetch native light parameters after the ordinary scene render."""

    def __init__(self, probe: NativeShadowBindingProbe):
        super().__init__(NATIVE_SHADOW_BINDING_PROBE_OPERATION_NAME + "Context")
        self._probe = probe
        self._execute_count = 0

    def requiresLightData(self):
        """Ask VP2 to populate the active light list for ``execute``."""
        return True

    def execute(self, context):
        """Bind the native shadow resource while the scene context is active."""
        self._execute_count += 1
        self._probe._report["contextExecuteCount"] += 1
        self._probe._bind_active_shadow(context, self._probe.shader())

    @property
    def execute_count(self) -> int:
        """Return the number of context callbacks observed by the probe."""
        return self._execute_count


class NativeShadowReceiverRender(omr.MSceneRender):
    """Re-render MMD receiver components with Maya's native shadow resource.

    This operation is deliberately opt-in and diagnostic.  It leaves the
    ordinary scene operation untouched, selects only imported MMD components
    carrying the receiver bit, and overlays a plugin-owned MMD effect with
    Maya's ``kShadowMap``/``kShadowViewProj`` handoff.  It is useful for a
    real receiver-composition experiment, but ``claimsSelfShadow`` remains
    false until the fixture Oracle proves the complete material path.
    """

    _native_shadow_map_parameter = NATIVE_SHADOW_RECEIVER_MAP_PARAMETER
    _native_shadow_viewproj_parameter = NATIVE_SHADOW_RECEIVER_VIEWPROJ_PARAMETER
    _native_shadow_enabled_parameter = NATIVE_SHADOW_RECEIVER_ENABLED_PARAMETER
    _semantic = staticmethod(NativeShadowBindingProbe._semantic)

    _material_parameter_map = (
        ("DiffuseColorRGB", "DiffuseColorRGB"),
        ("DiffuseColorA", "DiffuseColorA"),
        ("SpecularColor", "SpecularColor"),
        ("Shininess", "Shininess"),
        ("AmbientColor", "AmbientColor"),
        ("Opacity", "Opacity"),
        ("SphereMode", "SphereMode"),
        ("MMDLightDirection", "FixedLightDirection"),
        ("MMDLightColor", "FixedLightColor"),
    )
    _texture_parameter_map = (
        ("MainTexture", "MainTexture", "HasMainTexture"),
        ("SphereTexture", "SphereTexture", "HasSphereTexture"),
        ("ToonTexture", "ToonTexture", "HasToonTexture"),
    )

    def __init__(self, selection_provider=None, render_module=None):
        super().__init__(NATIVE_SHADOW_RECEIVER_OPERATION_NAME)
        self._selection_provider = (
            selection_provider or discover_self_shadow_receiver_components
        )
        self._render_module = render_module or omr
        self._selection = None
        self._shader = None
        self._shader_manager = None
        self._source_material_cache = {}
        self._source_texture_cache = {}
        self._source_texture_manager = None
        self._report = self._empty_report()

    @staticmethod
    def _empty_report() -> dict:
        """Return the stable receiver-composition diagnostic shape."""
        return {
            "enabled": True,
            "status": "not-run",
            "reason": "not-setup",
            "operationName": NATIVE_SHADOW_RECEIVER_OPERATION_NAME,
            "shaderPath": str(NATIVE_SHADOW_RECEIVER_SHADER_PATH),
            "technique": NATIVE_SHADOW_RECEIVER_TECHNIQUE,
            "mapParameter": NATIVE_SHADOW_RECEIVER_MAP_PARAMETER,
            "viewProjParameter": NATIVE_SHADOW_RECEIVER_VIEWPROJ_PARAMETER,
            "selection": {
                "status": "not-run",
                "reason": "not-evaluated",
                "components": [],
                "count": 0,
            },
            "createAttemptCount": 0,
            "createSucceeded": False,
            "preDrawCallbackCount": 0,
            "sourceItemCount": 0,
            "materialBindingCount": 0,
            "materialParameterSetCount": 0,
            "sourceShaderParameters": [],
            "sourceShaderParameterErrors": [],
            "sourceItemDiagnostics": [],
            "sourceMaterialShaderNodes": [],
            "sourceMaterialLookupErrors": [],
            "sourceMaterialTextureNodes": [],
            "sourceMaterialTexturePaths": [],
            "textureBindingCount": 0,
            "textureAcquireErrors": [],
            "textureReleaseAttemptCount": 0,
            "textureReleaseSucceeded": True,
            "bindingAttemptCount": 0,
            "bindingSucceeded": False,
            "resourceHandle": None,
            "releaseAttemptCount": 0,
            "releaseSucceeded": False,
            "releaseBeforeTarget": False,
            "drawsReceiver": True,
            "receiverComposition": True,
            "claimsSelfShadow": False,
            "context": {
                "status": "not-run",
                "stage": None,
                "lightingMode": None,
                "lightFilter": None,
                "lightCount": 0,
                "lights": [],
                "lastError": None,
            },
            "nativeParameterErrors": [],
            "parameterErrors": [],
            "lastError": None,
        }

    def requiresLightData(self):
        """Ask VP2 to expose active-light shadow resources for the pass."""
        return True

    def renderFilterOverride(self):
        """Render only shaded geometry from the receiver selection."""
        return omr.MSceneRender.kRenderShadedItems

    def shadowEnableOverride(self):
        """Ensure Maya evaluates the light shadow resource for this pass."""
        return True

    def shaderOverride(self):
        """Return the plugin-owned receiver effect after successful creation."""
        return self._shader

    def targetOverrideList(self):
        """Keep the receiver overlay on Maya's active color/depth targets."""
        return None

    def clearOperation(self):
        """Preserve the ordinary scene color while drawing the receiver overlay."""
        clear = super().clearOperation()
        clear.setClearGradient(False)
        clear.setMask(getattr(omr.MClearOperation, "kClearNone", 0))
        return clear

    def objectSetOverride(self):
        """Select only receiver components and fail closed on discovery errors."""
        try:
            import maya.api.OpenMaya as om

            empty_selection = om.MSelectionList()
        except Exception:
            self._report["selection"] = self._selection_report(
                (), "error", "api-unavailable"
            )
            self._selection = []
            return self._selection

        try:
            discovered = self._selection_provider()
        except Exception:
            self._report["selection"] = self._selection_report(
                (), "error", "discovery-failed"
            )
            self._selection = empty_selection
            return empty_selection
        if not isinstance(discovered, ShadowCasterSelection):
            self._report["selection"] = self._selection_report(
                (), "error", "invalid-selection-provider-result"
            )
            self._selection = empty_selection
            return empty_selection
        components = tuple(discovered.components)
        if not components:
            self._report["selection"] = self._selection_report(
                (), "empty", "no-components"
            )
            self._selection = empty_selection
            return empty_selection
        if any(not isinstance(component, str) or not component for component in components):
            self._report["selection"] = self._selection_report(
                components, "error", "invalid-component"
            )
            self._selection = empty_selection
            return empty_selection
        try:
            selection = om.MSelectionList()
            for component in components:
                selection.add(component)
        except Exception:
            self._report["selection"] = self._selection_report(
                components, "error", "add-failed"
            )
            self._selection = empty_selection
            return empty_selection
        self._prime_source_material_cache(components)
        self._report["selection"] = self._selection_report(
            components, "ok", "components-added"
        )
        self._selection = selection
        return selection

    @staticmethod
    def _selection_report(components, status: str, reason: str) -> dict:
        """Return one JSON-safe receiver selection report."""
        values = tuple(components)
        return {
            "status": status,
            "reason": reason,
            "components": list(values),
            "count": len(values),
        }

    @staticmethod
    def _items(render_item_list):
        """Materialize an ``MRenderItemList`` without relying on iteration."""
        if render_item_list is None:
            return []
        try:
            return [render_item_list[index] for index in range(len(render_item_list))]
        except Exception:
            return []

    def _set_parameter(self, shader_instance, name: str, value) -> bool:
        """Set one receiver parameter and record unsupported values."""
        try:
            shader_instance.setParameter(name, value)
        except Exception as exc:
            self._report["parameterErrors"].append(
                {"parameter": name, "error": f"{type(exc).__name__}: {exc!r}"}
            )
            return False
        self._report["materialParameterSetCount"] += 1
        return True

    @staticmethod
    def _flatten_maya_value(value):
        """Normalize Maya's one-element compound return shape."""
        if isinstance(value, (list, tuple)) and len(value) == 1:
            nested = value[0]
            if isinstance(nested, (list, tuple)):
                return tuple(nested)
        return value

    @classmethod
    def _read_maya_value(cls, cmds, node: str, attribute: str):
        """Read a scalar or recover a generated compound from its children."""
        try:
            return cls._flatten_maya_value(cmds.getAttr(f"{node}.{attribute}"))
        except Exception:
            children = cmds.attributeQuery(attribute, node=node, listChildren=True) or []
            if not children:
                raise
            values = []
            for child in children:
                child_value = cls._flatten_maya_value(cmds.getAttr(f"{node}.{child}"))
                values.append(child_value)
            return tuple(values)

    @staticmethod
    def _component_token_parts(component_token):
        """Return a mesh shape and inclusive face indices from one Maya token."""
        shape, separator, face_text = str(component_token).partition(".f[")
        if not separator or not face_text.endswith("]"):
            return str(component_token), None
        indices = []
        for part in face_text[:-1].split(","):
            bounds = part.split(":", 1)
            try:
                start = int(bounds[0])
                end = int(bounds[-1])
            except (TypeError, ValueError):
                return shape, None
            step = 1 if end >= start else -1
            indices.extend(range(start, end + step, step))
        return shape, tuple(indices)

    def _prime_source_material_cache(self, components) -> None:
        """Resolve receiver assignments before VP2 enters draw callbacks."""
        try:
            import maya.cmds as cmds
        except Exception as exc:
            self._report["sourceMaterialLookupErrors"].append(
                {"stage": "prime-import", "error": f"{type(exc).__name__}: {exc!r}"}
            )
            return
        self._source_material_cache = {}
        for component_token in components:
            shape, indices = self._component_token_parts(component_token)
            shape_candidates = cmds.listRelatives(
                shape, shapes=True, fullPath=True, noIntermediate=True
            ) or []
            lookup_shape = shape_candidates[0] if shape_candidates else shape
            shading_engines = cmds.listConnections(lookup_shape, type="shadingEngine") or []
            selected_engine = None
            for shading_engine in shading_engines:
                try:
                    if cmds.sets(component_token, isMember=shading_engine):
                        selected_engine = shading_engine
                        break
                except Exception:
                    continue
            if selected_engine is None and shading_engines:
                # Maya can reject isMember for a component range during an
                # update callback; retain a deterministic connected candidate.
                selected_engine = shading_engines[0]
            if selected_engine is None:
                self._report["sourceMaterialLookupErrors"].append(
                    {"stage": "prime-shading-engine", "component": component_token}
                )
                continue
            shader_nodes = cmds.listConnections(
                f"{selected_engine}.surfaceShader",
                source=True,
                destination=False,
            ) or []
            shader_node = next(
                (
                    node
                    for node in shader_nodes
                    if cmds.nodeType(node) in {"dx11Shader", "GLSLShader"}
                ),
                None,
            )
            if shader_node is None:
                self._report["sourceMaterialLookupErrors"].append(
                    {
                        "stage": "prime-shader-node",
                        "component": component_token,
                        "shadingEngine": selected_engine,
                    }
                )
                continue
            if shader_node not in self._report["sourceMaterialShaderNodes"]:
                self._report["sourceMaterialShaderNodes"].append(shader_node)
            self._prime_source_texture_cache(shader_node)
            if indices:
                for index in indices:
                    self._source_material_cache[(lookup_shape, index)] = shader_node
            else:
                self._source_material_cache[(lookup_shape, None)] = shader_node

    def _prime_source_texture_cache(self, shader_node: str) -> None:
        """Acquire source MMD file textures before VP2 draw callbacks."""
        try:
            import maya.cmds as cmds

            renderer = getattr(self._render_module, "MRenderer", None)
            texture_manager_getter = getattr(renderer, "getTextureManager", None)
            texture_manager = texture_manager_getter() if callable(texture_manager_getter) else None
            if texture_manager is None:
                raise RuntimeError("texture manager unavailable")
            self._source_texture_manager = texture_manager
            for source_name, _target_name, _has_name in self._texture_parameter_map:
                cache_key = (shader_node, source_name)
                if cache_key in self._source_texture_cache:
                    continue
                incoming = cmds.listConnections(
                    f"{shader_node}.{source_name}",
                    source=True,
                    destination=False,
                ) or []
                file_node = next(
                    (node for node in incoming if cmds.nodeType(node) == "file"),
                    None,
                )
                if not file_node:
                    continue
                texture_path = cmds.getAttr(f"{file_node}.fileTextureName")
                if not texture_path:
                    continue
                texture_record = {
                    "shader": shader_node,
                    "parameter": source_name,
                    "fileNode": file_node,
                    "path": str(texture_path),
                }
                if file_node not in self._report["sourceMaterialTextureNodes"]:
                    self._report["sourceMaterialTextureNodes"].append(file_node)
                if texture_record not in self._report["sourceMaterialTexturePaths"]:
                    self._report["sourceMaterialTexturePaths"].append(texture_record)
                try:
                    texture = texture_manager.acquireTexture(str(texture_path))
                except Exception as exc:
                    self._report["textureAcquireErrors"].append(
                        {**texture_record, "error": f"{type(exc).__name__}: {exc!r}"}
                    )
                    continue
                if texture is None:
                    self._report["textureAcquireErrors"].append(
                        {**texture_record, "error": "acquireTexture returned None"}
                    )
                    continue
                self._source_texture_cache[cache_key] = texture
        except Exception as exc:
            self._report["textureAcquireErrors"].append(
                {"stage": "prime", "shader": shader_node, "error": f"{type(exc).__name__}: {exc!r}"}
            )

    def _cached_source_material_shader(self, item):
        """Return a shader node primed for the item's shape/face component."""
        try:
            source_path = item.sourceDagPath()
            full_path_name = getattr(source_path, "fullPathName", None)
            shape = full_path_name() if callable(full_path_name) else str(source_path)
            component = item.shadingComponent()
            if component is None or component.isNull():
                return self._source_material_cache.get((shape, None))
            from maya.api import OpenMaya as om

            component_fn = om.MFnSingleIndexedComponent(component)
            for index in component_fn.getElements():
                shader_node = self._source_material_cache.get((shape, int(index)))
                if shader_node:
                    return shader_node
        except Exception:
            return None
        return None

    def _source_material_shader(self, item):
        """Resolve the authored shader node for one receiver face component."""
        cached_shader = self._cached_source_material_shader(item)
        if cached_shader:
            return cached_shader
        try:
            import maya.api.OpenMaya as om
            import maya.cmds as cmds

            source_path = item.sourceDagPath()
            full_path_name = getattr(source_path, "fullPathName", None)
            shape = full_path_name() if callable(full_path_name) else str(source_path)
            component = item.shadingComponent()
            component_objects = []
            if component is not None and not component.isNull():
                component_fn = om.MFnSingleIndexedComponent(component)
                component_objects = [
                    f"{shape}.f[{int(index)}]"
                    for index in component_fn.getElements()
                ]
            if not component_objects:
                component_objects = [shape]

            shading_engines = []
            for component_object in component_objects:
                shading_engines.extend(cmds.listSets(object=component_object) or [])
                if shading_engines:
                    break
            if not shading_engines:
                candidates = cmds.listConnections(
                    shape,
                    type="shadingEngine",
                ) or []
                if component_objects:
                    for shading_engine in candidates:
                        try:
                            if cmds.sets(component_objects[0], isMember=shading_engine):
                                shading_engines.append(shading_engine)
                        except Exception:
                            continue
                if not shading_engines:
                    shading_engines.extend(candidates)
            for shading_engine in dict.fromkeys(shading_engines):
                shader_nodes = cmds.listConnections(
                    f"{shading_engine}.surfaceShader",
                    source=True,
                    destination=False,
                ) or []
                for shader_node in shader_nodes:
                    if cmds.nodeType(shader_node) not in {"dx11Shader", "GLSLShader"}:
                        continue
                    if shader_node not in self._report["sourceMaterialShaderNodes"]:
                        self._report["sourceMaterialShaderNodes"].append(shader_node)
                    return shader_node
            return None
        except Exception as exc:
            self._report["sourceMaterialLookupErrors"].append(
                {"stage": "resolve", "error": f"{type(exc).__name__}: {exc!r}"}
            )
            return None

    def _bind_maya_material_parameters(self, item, shader_instance) -> bool:
        """Copy authored scalar values through Maya's shadingEngine assignment."""
        shader_node = self._source_material_shader(item)
        if not shader_node:
            return False
        bound_any = False
        try:
            import maya.cmds as cmds
        except Exception as exc:
            self._report["sourceMaterialLookupErrors"].append(
                {"stage": "read", "shader": shader_node, "error": f"{type(exc).__name__}: {exc!r}"}
            )
            return False
        for source_name, target_name in self._material_parameter_map:
            try:
                if not cmds.attributeQuery(source_name, node=shader_node, exists=True):
                    continue
                value = self._read_maya_value(cmds, shader_node, source_name)
                if value is None:
                    continue
                if self._set_parameter(shader_instance, target_name, value):
                    bound_any = True
            except Exception as exc:
                self._report["sourceMaterialLookupErrors"].append(
                    {
                        "stage": "read",
                        "shader": shader_node,
                        "attribute": source_name,
                        "error": f"{type(exc).__name__}: {exc!r}",
                    }
                )
        return bound_any

    def _bind_maya_texture_parameters(self, shader_node, shader_instance) -> bool:
        """Bind acquired MMD textures and enable only successful resources."""
        bound_any = False
        for source_name, target_name, has_name in self._texture_parameter_map:
            texture = self._source_texture_cache.get((shader_node, source_name))
            if texture is None:
                self._set_parameter(shader_instance, has_name, False)
                continue
            if self._set_parameter(shader_instance, target_name, texture):
                self._set_parameter(shader_instance, has_name, True)
                self._report["textureBindingCount"] += 1
                bound_any = True
        return bound_any

    def _bind_material_parameters(self, item, shader_instance) -> None:
        """Copy safe scalar/color values from Maya's source render item."""
        if len(self._report["sourceItemDiagnostics"]) < 16:
            item_diagnostic = {}
            try:
                item_diagnostic["name"] = str(item.name())
            except Exception as exc:
                item_diagnostic["nameError"] = f"{type(exc).__name__}: {exc!r}"
            try:
                source_shader = item.getShader()
                item_diagnostic["sourceShaderPresent"] = source_shader is not None
                if source_shader is not None:
                    parameter_list = source_shader.parameterList()
                    item_diagnostic["sourceShaderParameters"] = [
                        str(parameter) for parameter in parameter_list or ()
                    ]
                    item_diagnostic["sourceShaderResource"] = str(
                        source_shader.resourceName("MainTexture")
                    )
            except Exception as exc:
                item_diagnostic["sourceShaderError"] = (
                    f"{type(exc).__name__}: {exc!r}"
                )
            for component_name in ("component", "shadingComponent"):
                try:
                    component = getattr(item, component_name)()
                    component_info = {"null": bool(component.isNull())}
                    if not component.isNull():
                        component_info["apiType"] = int(component.apiType())
                        try:
                            from maya.api import OpenMaya as om

                            component_fn = om.MFnSingleIndexedComponent(component)
                            component_info["elements"] = [
                                int(element) for element in component_fn.getElements()
                            ]
                        except Exception as exc:
                            component_info["elementsError"] = (
                                f"{type(exc).__name__}: {exc!r}"
                            )
                    item_diagnostic[component_name] = component_info
                except Exception as exc:
                    item_diagnostic[f"{component_name}Error"] = (
                        f"{type(exc).__name__}: {exc!r}"
                    )
            self._report["sourceItemDiagnostics"].append(item_diagnostic)
        try:
            available_parameters = item.availableShaderParameters()
        except Exception as exc:
            available_parameters = ()
            self._report["sourceShaderParameterErrors"].append(
                {"stage": "available", "error": f"{type(exc).__name__}: {exc!r}"}
            )
        for parameter in available_parameters or ():
            if not isinstance(parameter, str):
                continue
            if parameter not in self._report["sourceShaderParameters"]:
                self._report["sourceShaderParameters"].append(parameter)
        bound_any = False
        for source_name, target_name in self._material_parameter_map:
            try:
                value = item.getShaderParameters(source_name)
            except Exception:
                continue
            if value is None:
                continue
            if self._set_parameter(shader_instance, target_name, value):
                bound_any = True
        if self._bind_maya_material_parameters(item, shader_instance):
            bound_any = True
        shader_node = self._source_material_shader(item)
        if shader_node and self._bind_maya_texture_parameters(shader_node, shader_instance):
            bound_any = True
        # The receiver effect always uses the explicit fixed MMD light values
        # above; source texture resources are held until shader release.
        self._set_parameter(shader_instance, NATIVE_SHADOW_RECEIVER_STRENGTH_PARAMETER, 1.0)
        self._set_parameter(shader_instance, NATIVE_SHADOW_RECEIVER_BIAS_PARAMETER, 0.01)
        # A source shader may not expose MMD uniforms (for example the compact
        # PMX E2E importer uses lambert fallback materials).  The operation is
        # still a valid receiver draw with effect defaults; record that fallback
        # explicitly instead of treating it as a missing draw.
        if not bound_any:
            self._report["sourceMaterialFallbackCount"] = (
                self._report.get("sourceMaterialFallbackCount", 0) + 1
            )
        self._report["materialBindingCount"] += 1

    def _pre_draw(self, context, render_item_list, shader_instance):
        """Bind native shadow data and source MMD material parameters per draw."""
        self._report["preDrawCallbackCount"] += 1
        items = self._items(render_item_list)
        self._report["sourceItemCount"] += len(items)
        self._bind_active_shadow(context, shader_instance)
        for item in items:
            try:
                source_path = item.sourceDagPath()
                full_path_name = getattr(source_path, "fullPathName", None)
                if callable(full_path_name):
                    self._report.setdefault("sourceDagPaths", []).append(full_path_name())
            except Exception:
                pass
            self._bind_material_parameters(item, shader_instance)

    def _bind_active_shadow(self, context, shader_instance):
        """Reuse the proven native-light binding implementation with new names."""
        return NativeShadowBindingProbe._bind_active_shadow(self, context, shader_instance)

    def create(self) -> dict:
        """Create the plugin-owned overlay effect for the active DX11 device."""
        previous_release = self.release_shader()
        if self.has_owned_shader():
            self._report.update(
                status="unsupported",
                reason="previous-shader-release-failed",
                lastError=previous_release.get("lastError"),
                createSucceeded=False,
            )
            return self.report()
        self._report.update(
            status="not-run",
            reason="shader-unavailable",
            createSucceeded=False,
            bindingSucceeded=False,
            releaseSucceeded=False,
            releaseBeforeTarget=False,
            lastError=None,
            parameterErrors=[],
            context=self._empty_report()["context"],
        )
        self._source_material_cache = {}
        self._source_texture_cache = {}
        self._source_texture_manager = None
        for key in (
            "sourceMaterialShaderNodes",
            "sourceMaterialTextureNodes",
            "sourceMaterialTexturePaths",
            "textureAcquireErrors",
        ):
            self._report[key] = []
        self._report["textureBindingCount"] = 0
        self._report["textureReleaseAttemptCount"] = 0
        self._report["textureReleaseSucceeded"] = True
        self._report["createAttemptCount"] += 1
        draw_api_getter = getattr(self._render_module.MRenderer, "drawAPI", None)
        if callable(draw_api_getter):
            try:
                draw_api = draw_api_getter()
            except Exception as exc:
                self._report.update(
                    status="unsupported", reason="draw-api-query-failed", lastError=str(exc)
                )
                return self.report()
            if draw_api != getattr(self._render_module.MRenderer, "kDirectX11", draw_api):
                self._report.update(
                    status="unsupported",
                    reason="directx11-only-native-shadow-receiver",
                    lastError=f"draw API {draw_api!r} is not DirectX11",
                )
                return self.report()
        if not NATIVE_SHADOW_RECEIVER_SHADER_PATH.is_file():
            self._report.update(status="unsupported", reason="shader-asset-missing")
            return self.report()
        try:
            shader_manager = self._render_module.MRenderer.getShaderManager()
            if shader_manager is None:
                raise RuntimeError("shader manager unavailable")
            shader = shader_manager.getEffectsFileShader(
                str(NATIVE_SHADOW_RECEIVER_SHADER_PATH),
                NATIVE_SHADOW_RECEIVER_TECHNIQUE,
                None,
                True,
                self._pre_draw,
                None,
            )
            if shader is None:
                raise RuntimeError("receiver shader creation returned None")
            self._shader = shader
            self._shader_manager = shader_manager
            self._report.update(
                status="created", reason="shader-created", createSucceeded=True
            )
        except Exception as exc:
            release_report = self.release_shader()
            self._report.update(
                status="unsupported",
                reason=(
                    "shader-release-failed"
                    if not release_report["releaseSucceeded"] and self.has_owned_shader()
                    else "shader-create-failed"
                ),
                lastError=str(exc),
            )
        return self.report()

    def release_shader(self) -> dict:
        """Release the overlay shader while Maya still owns the active targets."""
        shader = self._shader
        manager = self._shader_manager
        if shader is None:
            return self.report()
        self._report["releaseAttemptCount"] += 1
        try:
            if manager is None:
                raise RuntimeError("shader manager disappeared before receiver release")
            manager.releaseShader(shader)
        except Exception as exc:
            self._report.update(
                status="unsupported",
                reason="receiver-shader-release-failed",
                releaseSucceeded=False,
                releaseBeforeTarget=False,
                lastError=str(exc),
            )
        else:
            self._shader = None
            self._shader_manager = None
            self._release_source_textures()
            self._report.update(
                status="released",
                reason="receiver-shader-released-before-target",
                releaseSucceeded=True,
                releaseBeforeTarget=True,
                lastError=None,
            )
        return self.report()

    def _release_source_textures(self) -> None:
        """Release textures acquired for the receiver after shader detachment."""
        manager = self._source_texture_manager
        textures = []
        seen = set()
        for texture in self._source_texture_cache.values():
            identity = id(texture)
            if identity not in seen:
                seen.add(identity)
                textures.append(texture)
        self._report["textureReleaseAttemptCount"] += len(textures)
        if manager is None:
            self._report["textureReleaseSucceeded"] = not textures
            self._source_texture_cache = {}
            return
        succeeded = True
        for texture in textures:
            try:
                manager.releaseTexture(texture)
            except Exception as exc:
                succeeded = False
                self._report["textureAcquireErrors"].append(
                    {"stage": "release", "error": f"{type(exc).__name__}: {exc!r}"}
                )
        self._report["textureReleaseSucceeded"] = succeeded
        self._source_texture_cache = {}
        self._source_texture_manager = None

    def has_owned_shader(self) -> bool:
        """Return whether cleanup must retain the receiver shader for retry."""
        return self._shader is not None

    def report(self) -> dict:
        """Return JSON-safe receiver composition diagnostics."""
        report = dict(self._report)
        report["selection"] = dict(self._report["selection"])
        report["context"] = dict(self._report["context"])
        report["context"]["lights"] = [
            dict(light) for light in self._report["context"].get("lights", [])
        ]
        report["parameterErrors"] = list(self._report["parameterErrors"])
        report["nativeParameterErrors"] = list(self._report["nativeParameterErrors"])
        if "sourceDagPaths" in self._report:
            report["sourceDagPaths"] = list(self._report["sourceDagPaths"])
        return report


class ShadowTargetClearRender(omr.MSceneRender):
    """Render selected MMD casters into the diagnostic target pair."""

    def __init__(
        self,
        resources: ShadowTargetResources,
        name: str = SHADOW_TARGET_OPERATION_NAME,
        selection_provider=None,
        caster_shader_pass: Optional[R32FCasterShaderPass] = None,
        camera_provider: Optional[LightSpaceCasterCamera] = None,
    ):
        super().__init__(name)
        self._resources = resources
        self._targets = None
        self._selection_provider = (
            selection_provider or discover_self_shadow_caster_components
        )
        self._caster_shader_pass = caster_shader_pass
        self._camera_provider = camera_provider
        self._selection_report = self._empty_selection_report(
            status="not-run", reason="not-evaluated"
        )
        self._selection = None
        self._manual_readback_requested = False
        self._post_render_captured = False

    def set_targets(self, targets: Optional[Tuple]) -> None:
        """Attach or detach the R2 probe render targets for this operation."""
        self._targets = targets
        if targets:
            self._selection = None
            self._post_render_captured = False
            self._selection_report = self._empty_selection_report(
                status="not-run", reason="not-evaluated"
            )

    def targetOverrideList(self):
        """Route this operation to the offscreen target pair when enabled."""
        return list(self._targets) if self._targets else None

    def shaderOverride(self):
        """Use only the opt-in plugin-owned caster shader when ready."""
        if self._caster_shader_pass is None or not self._targets:
            return None
        return self._caster_shader_pass.shader_instance()

    def cameraOverride(self):
        """Use the optional plugin-owned light-space camera for caster draws."""
        if self._camera_provider is None:
            return None
        return self._camera_provider.camera_override()

    def renderFilterOverride(self):
        """Restrict this operation to shaded geometry from the caster set."""
        return omr.MSceneRender.kRenderShadedItems

    @staticmethod
    def _is_color_pass(context) -> bool:
        """Return whether a Maya draw context represents the color pass."""
        try:
            pass_context = context.getPassContext()
            semantics = pass_context.passSemantics()
            return omr.MPassContext.kColorPassSemantic in semantics
        except Exception:
            return False

    def clearOperation(self):
        """Clear the color/depth targets to the documented far-depth contract."""
        clear = super().clearOperation()
        clear.setClearColor((SHADOW_CLEAR_VALUE,) * 4)
        clear.setClearDepth(SHADOW_CLEAR_VALUE)
        clear.setClearGradient(False)
        clear.setMask(omr.MClearOperation.kClearAll)
        return clear

    def objectSetOverride(self):
        """Return only discovered MMD caster components, failing closed.

        This is a routing diagnostic for the target probe.  A retained list is
        returned after all components have been added so VP2 cannot observe a
        partially populated selection during its render callback.
        """
        try:
            import maya.api.OpenMaya as om
        except Exception:
            self._selection_report = self._empty_selection_report(
                status="error", reason="api-unavailable"
            )
            return []

        if self._caster_shader_pass is not None and not self._caster_shader_pass.shader_instance():
            self._selection_report = self._empty_selection_report(
                status="error", reason="caster-shader-unavailable"
            )
            self._selection = om.MSelectionList()
            return self._selection

        try:
            empty_selection = om.MSelectionList()
        except Exception:
            self._selection_report = self._empty_selection_report(
                status="error", reason="api-unavailable"
            )
            return []

        try:
            discovered = self._selection_provider()
        except Exception:
            self._selection_report = self._empty_selection_report(
                status="error", reason="discovery-failed"
            )
            self._selection = empty_selection
            return empty_selection
        if not isinstance(discovered, ShadowCasterSelection):
            self._selection_report = self._empty_selection_report(
                status="error", reason="invalid-selection-provider-result"
            )
            self._selection = empty_selection
            return empty_selection
        components = tuple(discovered.components)

        if not components:
            self._selection_report = self._selection_report_for(
                components=(), status="empty", reason="no-components"
            )
            self._selection = empty_selection
            return empty_selection

        if any(not isinstance(component, str) or not component for component in components):
            self._selection_report = self._selection_report_for(
                components=components,
                status="error",
                reason="invalid-component",
            )
            self._selection = empty_selection
            return empty_selection

        try:
            selection = om.MSelectionList()
            for component in components:
                selection.add(component)
        except Exception:
            self._selection_report = self._selection_report_for(
                components=components,
                status="error",
                reason="add-failed",
            )
            self._selection = empty_selection
            return empty_selection

        self._selection_report = self._selection_report_for(
            components=components, status="ok", reason="components-added"
        )
        self._selection = selection
        return selection

    @staticmethod
    def _empty_selection_report(status: str, reason: str) -> dict:
        """Return the stable zero-component diagnostic shape."""
        return {
            "status": status,
            "reason": reason,
            "components": [],
            "count": 0,
        }

    @staticmethod
    def _selection_report_for(
        components: Tuple[str, ...], status: str, reason: str
    ) -> dict:
        """Return a JSON-safe diagnostic for one provider evaluation."""
        return {
            "status": status,
            "reason": reason,
            "components": list(components),
            "count": len(components),
        }

    def selection_report(self) -> dict:
        """Return the latest caster-routing diagnostic for target probes."""
        return dict(self._selection_report)

    def release_shader(self) -> dict:
        """Release the optional caster shader before detaching target handles."""
        if self._caster_shader_pass is None:
            return {
                "status": "not-run",
                "reason": "caster-pass-disabled",
                "releaseSucceeded": True,
                "releaseBeforeTarget": True,
            }
        return self._caster_shader_pass.release()

    def caster_shader_report(self) -> Optional[dict]:
        """Return optional caster shader lifecycle diagnostics."""
        if self._caster_shader_pass is None:
            return None
        return self._caster_shader_pass.report()

    def camera_report(self) -> Optional[dict]:
        """Return optional light-space camera lifecycle diagnostics."""
        if self._camera_provider is None:
            return None
        return self._camera_provider.report()

    def postSceneRender(self, context) -> None:
        """Capture occupancy once after the color pass while targets are valid."""
        if context is None:
            if not self._manual_readback_requested:
                return
        elif not self._is_color_pass(context):
            return
        if self._post_render_captured:
            return
        self._resources.capture_target_occupancy(self.selection_report())
        self._post_render_captured = True

    def manual_target_occupancy(self) -> dict:
        """Run the explicit test/E2E readback path without a draw context."""
        self._manual_readback_requested = True
        try:
            self.postSceneRender(None)
            report = self._resources.report()
            return report.get("occupancy", {})
        finally:
            self._manual_readback_requested = False


class PassthroughSceneRender(omr.MSceneRender):
    """Use Maya's ordinary scene render and preserve the active view background."""

    def __init__(self, name: str = SCENE_OPERATION_NAME):
        super().__init__(name)
        self._panel_camera_override = None

    def configure_panel_background(self, panel_name: str) -> None:
        """Mirror a model panel's VP2 background in this scene operation.

        An override owns its scene clear operation.  Unlike the stock VP2
        path, the default ``MSceneRender`` clear is black, so copy the active
        panel's configured flat or gradient background before rendering.
        """
        import maya.api.OpenMayaUI as omui

        view = omui.M3dView.getM3dViewFromModelPanel(panel_name)
        try:
            camera_override = omr.MCameraOverride()
            camera_override.mCameraPath = view.getCamera()
            self._panel_camera_override = camera_override
        except Exception:
            # Background mirroring remains useful on API variants without a
            # camera-path override; Maya will use the panel camera normally.
            self._panel_camera_override = None
        clear = self.clearOperation()
        if view.isBackgroundGradient():
            clear.setClearColor(view.backgroundColorTop())
            clear.setClearColor2(view.backgroundColorBottom())
            clear.setClearGradient(True)
        else:
            clear.setClearColor(view.backgroundColor())
            clear.setClearGradient(False)

    def cameraOverride(self):
        """Restore the original model-panel camera after diagnostic passes."""
        return self._panel_camera_override


class PassthroughHUDRender(omr.MHUDRender):
    """Use Maya's ordinary HUD operation with no custom drawables."""

    def __init__(self):
        # Maya's MHUDRender constructor supplies the standard HUD operation
        # name.  Deliberately do not add custom UI drawables or target routing.
        super().__init__()


class PassthroughPresentTarget(omr.MPresentTarget):
    """Present Maya's ordinary output target without replacing it."""

    def __init__(self, name: str = PRESENT_OPERATION_NAME):
        super().__init__(name)


class PassthroughRenderOverride(omr.MRenderOverride):
    """A minimal scene -> HUD -> present operation queue.

    The iterator methods mirror Maya's Python API 2.0 override contract.  The
    queue is stable for the lifetime of the registered instance, while
    ``cleanup`` only resets iterator state so Maya can safely reuse it.
    """

    operation_roles = ("scene", "hud", "present")

    def __init__(self, name: str = RENDER_OVERRIDE_NAME, selection_provider=None):
        super().__init__(name)
        self._native_shadow_request = (
            NativeShadowRequest()
            if _enabled_environment_flag(NATIVE_SHADOW_REQUEST_ENV)
            else None
        )
        self._native_shadow_request_operation = (
            NativeShadowRequestRender(self._native_shadow_request)
            if self._native_shadow_request is not None
            else None
        )
        self._native_shadow_binding_probe_resources = (
            NativeShadowBindingProbeResources()
            if _enabled_environment_flag(NATIVE_SHADOW_BINDING_PROBE_ENV)
            else None
        )
        self._native_shadow_binding_probe = (
            NativeShadowBindingProbe(self._native_shadow_binding_probe_resources)
            if self._native_shadow_binding_probe_resources is not None
            else None
        )
        self._native_shadow_binding_context_operation = (
            NativeShadowBindingContextRender(self._native_shadow_binding_probe)
            if self._native_shadow_binding_probe is not None
            else None
        )
        self._native_shadow_receiver = (
            NativeShadowReceiverRender(selection_provider=selection_provider)
            if _enabled_environment_flag(NATIVE_SHADOW_RECEIVER_ENV)
            else None
        )
        self._scene_operation = PassthroughSceneRender()
        self._target_probe = (
            ShadowTargetResources()
            if _enabled_environment_flag("MMD_TOOLS_ENABLE_RENDER_OVERRIDE_TARGET_PROBE")
            else None
        )
        self._r32f_caster_shader_pass = (
            R32FCasterShaderPass()
            if self._target_probe is not None
            and _enabled_environment_flag(
                "MMD_TOOLS_ENABLE_RENDER_OVERRIDE_R32F_CASTER_PASS"
            )
            else None
        )
        self._light_space_camera = (
            LightSpaceCasterCamera()
            if self._r32f_caster_shader_pass is not None
            and _enabled_environment_flag(
                "MMD_TOOLS_ENABLE_RENDER_OVERRIDE_R32F_LIGHT_SPACE"
            )
            else None
        )
        self._shadow_target_operation = (
            ShadowTargetClearRender(
                self._target_probe,
                selection_provider=selection_provider,
                caster_shader_pass=self._r32f_caster_shader_pass,
                camera_provider=self._light_space_camera,
            )
            if self._target_probe is not None
            else None
        )
        self._r32f_binding_probe = (
            R32FTargetBindingProbe()
            if self._target_probe is not None
            and _enabled_environment_flag("MMD_TOOLS_ENABLE_RENDER_OVERRIDE_R32F_BINDING_PROBE")
            else None
        )
        receiver_enabled = (
            self._target_probe is not None
            and self._r32f_caster_shader_pass is not None
            and _enabled_environment_flag("MMD_TOOLS_ENABLE_RENDER_OVERRIDE_R32F_RECEIVER_PROBE")
        )
        self._receiver_probe_resources = ReceiverProbeResources() if receiver_enabled else None
        self._receiver_probe = (
            R32FReceiverProbe(self._receiver_probe_resources)
            if self._receiver_probe_resources is not None
            else None
        )
        operations = [self._scene_operation]
        if self._native_shadow_binding_context_operation is not None:
            # Run after the ordinary scene so Maya has had a chance to build
            # its native light shadow resource for this frame.
            operations.append(self._native_shadow_binding_context_operation)
        if self._native_shadow_receiver is not None:
            operations.append(self._native_shadow_receiver)
        if self._native_shadow_binding_probe is not None:
            operations.append(self._native_shadow_binding_probe)
        operations.extend([PassthroughHUDRender(), PassthroughPresentTarget()])
        operations = tuple(operations)
        if self._receiver_probe is not None:
            operations = (self._receiver_probe, *operations)
        if self._shadow_target_operation is not None:
            operations = (self._shadow_target_operation, *operations)
        if self._native_shadow_request_operation is not None:
            operations = (self._native_shadow_request_operation, *operations)
        self._operations = operations
        self._current_operation = -1
        self._cleanup_count = 0

    def supportedDrawAPIs(self):
        """Support the APIs exposed by Maya 2024's VP2 renderer."""
        return (
            omr.MRenderer.kOpenGL
            | omr.MRenderer.kDirectX11
            | omr.MRenderer.kOpenGLCoreProfile
        )

    def startOperationIterator(self):
        """Reset iteration to the first ordinary scene operation."""
        self._current_operation = 0
        return bool(self._operations)

    def renderOperation(self):
        """Return the current operation, or ``None`` at end of iteration."""
        if 0 <= self._current_operation < len(self._operations):
            return self._operations[self._current_operation]
        return None

    def nextRenderOperation(self):
        """Advance to the next operation and report whether one remains."""
        if self._current_operation < len(self._operations):
            self._current_operation += 1
        return self._current_operation < len(self._operations)

    def cleanup(self):
        """Reset iterator state while retaining reusable operation objects."""
        if self._receiver_probe is not None and self._receiver_probe_resources is not None:
            self._receiver_probe.capture_output()
            self._receiver_probe.release_shader()
            if self._receiver_probe.has_owned_shader():
                self._current_operation = -1
                self._cleanup_count += 1
                raise RuntimeError("R32F receiver shader release failed; target release deferred")
            self._receiver_probe_resources.release()
            if self._receiver_probe_resources.target is not None:
                self._current_operation = -1
                self._cleanup_count += 1
                raise RuntimeError("R32F receiver target release failed; target release deferred")
        if (
            self._native_shadow_binding_probe is not None
            and self._native_shadow_binding_probe_resources is not None
        ):
            self._native_shadow_binding_probe.capture_output()
            self._native_shadow_binding_probe.release_shader()
            if self._native_shadow_binding_probe.has_owned_shader():
                self._current_operation = -1
                self._cleanup_count += 1
                raise RuntimeError(
                    "native shadow binding probe shader release failed; target release deferred"
                )
            self._native_shadow_binding_probe_resources.release()
            if self._native_shadow_binding_probe_resources.target is not None:
                self._current_operation = -1
                self._cleanup_count += 1
                raise RuntimeError(
                    "native shadow binding probe target release failed; target release deferred"
                )
        if self._native_shadow_receiver is not None:
            self._native_shadow_receiver.release_shader()
            if self._native_shadow_receiver.has_owned_shader():
                self._current_operation = -1
                self._cleanup_count += 1
                raise RuntimeError("native shadow receiver shader release failed")
        if self._shadow_target_operation is not None:
            self._shadow_target_operation.release_shader()
            if self._r32f_caster_shader_pass is not None and self._r32f_caster_shader_pass.has_owned_shader():
                # Keep the target pair attached while shader release is
                # rejected; a later cleanup can retry in the same ownership
                # order instead of releasing a referenced target.
                self._current_operation = -1
                self._cleanup_count += 1
                raise RuntimeError(
                    "R32F caster shader release failed; target release deferred"
                )
        if self._r32f_binding_probe is not None:
            self._r32f_binding_probe.release()
            if self._r32f_binding_probe.has_owned_shader():
                # Do not release a target still referenced by a shader whose
                # manager rejected release.  A later cleanup can retry safely.
                self._current_operation = -1
                self._cleanup_count += 1
                raise RuntimeError(
                    "R32F binding probe shader release failed; target release deferred"
                )
        if self._shadow_target_operation is not None:
            self._shadow_target_operation.set_targets(None)
        if self._target_probe is not None:
            self._target_probe.release()
        if self._light_space_camera is not None:
            self._light_space_camera.release()
            if self._light_space_camera.has_owned_camera():
                self._current_operation = -1
                self._cleanup_count += 1
                raise RuntimeError("light-space camera release failed; camera retained")
        if self._native_shadow_request is not None:
            self._native_shadow_request.release()
            if self._native_shadow_request.has_owned_requests():
                self._current_operation = -1
                self._cleanup_count += 1
                raise RuntimeError("native shadow request release failed; request retained")
        self._current_operation = -1
        self._cleanup_count += 1

    def setup(self, destination: str) -> None:
        """Prepare the scene operation for the viewport being rendered."""
        if self._target_probe is not None and self._shadow_target_operation is not None:
            targets = None
            try:
                targets = self._target_probe.acquire()
                self._shadow_target_operation.set_targets(targets)
                if self._light_space_camera is not None:
                    camera_report = self._light_space_camera.configure()
                    if camera_report.get("status") != "configured":
                        raise RuntimeError(
                            "light-space caster camera is unavailable: "
                            f"{camera_report!r}"
                        )
                if self._r32f_caster_shader_pass is not None:
                    # The caster shader is created only after both plugin-owned
                    # targets exist, and it is released before either target.
                    self._r32f_caster_shader_pass.create(targets)
                if self._receiver_probe is not None and self._receiver_probe_resources is not None:
                    self._receiver_probe_resources.acquire()
                    self._receiver_probe.create(targets[0] if targets else None)
                if (
                    self._native_shadow_binding_probe is not None
                    and self._native_shadow_binding_probe_resources is not None
                ):
                    self._native_shadow_binding_probe_resources.acquire()
                    self._native_shadow_binding_probe.create()
                if self._native_shadow_receiver is not None:
                    self._native_shadow_receiver.create()
                if self._r32f_binding_probe is not None:
                    # Bind only the plugin-owned R32F target.  This setup probe is
                    # intentionally disconnected from imported shaders and draw
                    # operations, so it cannot alter ordinary viewport output.
                    self._r32f_binding_probe.bind(targets[0] if targets else None)
            except Exception:
                if (
                    self._native_shadow_binding_probe is not None
                    and self._native_shadow_binding_probe_resources is not None
                ):
                    self._native_shadow_binding_probe.release_shader()
                    self._native_shadow_binding_probe_resources.release()
                if self._native_shadow_receiver is not None:
                    self._native_shadow_receiver.release_shader()
                if self._receiver_probe is not None and self._receiver_probe_resources is not None:
                    self._receiver_probe.release_shader()
                    self._receiver_probe_resources.release()
                if self._shadow_target_operation is not None:
                    self._shadow_target_operation.release_shader()
                    self._shadow_target_operation.set_targets(None)
                if self._r32f_binding_probe is not None:
                    self._r32f_binding_probe.release()
                if self._target_probe is not None:
                    self._target_probe.release()
                if self._light_space_camera is not None:
                    self._light_space_camera.release()
                if self._native_shadow_request is not None:
                    self._native_shadow_request.release()
                raise
        if (
            self._native_shadow_binding_probe is not None
            and self._native_shadow_binding_probe_resources is not None
            and self._target_probe is None
        ):
            try:
                self._native_shadow_binding_probe_resources.acquire()
                self._native_shadow_binding_probe.create()
                if self._native_shadow_receiver is not None:
                    self._native_shadow_receiver.create()
            except Exception:
                self._native_shadow_binding_probe.release_shader()
                self._native_shadow_binding_probe_resources.release()
                if self._native_shadow_receiver is not None:
                    self._native_shadow_receiver.release_shader()
                if self._native_shadow_request is not None:
                    self._native_shadow_request.release()
                raise
        if self._native_shadow_receiver is not None and not self._native_shadow_receiver.has_owned_shader():
            self._native_shadow_receiver.create()
        try:
            self._scene_operation.configure_panel_background(destination)
        except Exception:
            if self._native_shadow_request is not None:
                self._native_shadow_request.release()
            raise
        self._current_operation = -1

    def target_probe_report(self) -> Optional[dict]:
        """Return R2 resource and occupancy diagnostics, or ``None`` when off."""
        if self._target_probe is None:
            return None
        report = self._target_probe.report()
        if self._shadow_target_operation is not None:
            report["casterSelection"] = self._shadow_target_operation.selection_report()
            caster_report = self._shadow_target_operation.caster_shader_report()
            if caster_report is not None:
                report["r32fCasterPass"] = caster_report
            camera_report = self._shadow_target_operation.camera_report()
            if camera_report is not None:
                report["lightSpaceCamera"] = camera_report
        if self._receiver_probe is not None:
            report["r32fReceiverProbe"] = self._receiver_probe.report()
        if self._r32f_binding_probe is not None:
            report["r32fBindingProbe"] = self._r32f_binding_probe.report()
        if self._native_shadow_request is not None:
            report["nativeShadowRequest"] = self._native_shadow_request.report()
        if self._native_shadow_binding_probe is not None:
            report["nativeShadowBindingProbe"] = self._native_shadow_binding_probe.report()
        if self._native_shadow_receiver is not None:
            report["nativeShadowReceiver"] = self._native_shadow_receiver.report()
        return report

    def native_shadow_request_report(self) -> Optional[dict]:
        """Return native shadow-request diagnostics when the opt-in probe is enabled."""
        if self._native_shadow_request is None:
            return None
        return self._native_shadow_request.report()

    def native_shadow_binding_probe_report(self) -> Optional[dict]:
        """Return native shadow-resource binding diagnostics when enabled."""
        if self._native_shadow_binding_probe is None:
            return None
        return self._native_shadow_binding_probe.report()

    def native_shadow_receiver_report(self) -> Optional[dict]:
        """Return native receiver-composition diagnostics when enabled."""
        if self._native_shadow_receiver is None:
            return None
        return self._native_shadow_receiver.report()

    @property
    def cleanup_count(self) -> int:
        """Return how often Maya (or a test) invoked ``cleanup``."""
        return self._cleanup_count

    @property
    def operations(self):
        """Expose the immutable operation tuple for lifecycle diagnostics."""
        return self._operations

    @property
    def operation_names(self):
        """Return Maya operation names in queue order."""
        return tuple(operation.name() for operation in self._operations)

    def uiName(self):
        """Return the label Maya may show for this opt-in override."""
        return RENDER_OVERRIDE_UI_NAME


_registered_override = None


def registered_override():
    """Return this module's registered override, if any."""
    return _registered_override


def is_registered() -> bool:
    """Return whether this module currently owns a registered override."""
    return _registered_override is not None


def initializePlugin(_mobject=None):
    """Register the passthrough override once and return the owned instance.

    ``MRenderer.registerOverride`` does not select an active override.  The
    caller therefore retains Maya's current renderer selection unchanged.
    """
    global _registered_override
    if _registered_override is not None:
        return _registered_override

    override = PassthroughRenderOverride()
    try:
        omr.MRenderer.registerOverride(override)
    except Exception as exc:
        # Maya may have partially accepted an object before raising.  A best
        # effort deregistration keeps a failed load from leaking our instance.
        try:
            omr.MRenderer.deregisterOverride(override)
        except Exception:
            pass
        raise RuntimeError(
            f"Failed to register MMD Tools render override {RENDER_OVERRIDE_NAME}: {exc}"
        ) from exc

    _registered_override = override
    return override


def uninitializePlugin(_mobject=None):
    """Release owned resources, then deregister this module's override."""
    global _registered_override
    override = _registered_override
    if override is None:
        return

    try:
        override.cleanup()
    except Exception as exc:
        raise RuntimeError(
            f"Failed to clean up MMD Tools render override {RENDER_OVERRIDE_NAME}: "
            f"{exc}"
        ) from exc
    try:
        omr.MRenderer.deregisterOverride(override)
    except Exception as exc:
        raise RuntimeError(
            f"Failed to deregister MMD Tools render override {RENDER_OVERRIDE_NAME}: {exc}"
        ) from exc
    _registered_override = None


__all__ = [
    "PRESENT_OPERATION_NAME",
    "PassthroughHUDRender",
    "PassthroughPresentTarget",
    "R32FCasterShaderPass",
    "R32FReceiverProbe",
    "R32FTargetBindingProbe",
    "LightSpaceCasterCamera",
    "NativeShadowRequest",
    "NativeShadowRequestRender",
    "NativeShadowBindingProbe",
    "NativeShadowBindingProbeResources",
    "NativeShadowBindingContextRender",
    "NativeShadowReceiverRender",
    "NATIVE_SHADOW_REQUEST_ENV",
    "NATIVE_SHADOW_BINDING_PROBE_ENV",
    "NATIVE_SHADOW_RECEIVER_ENV",
    "PassthroughRenderOverride",
    "PassthroughSceneRender",
    "RENDER_OVERRIDE_NAME",
    "RENDER_OVERRIDE_UI_NAME",
    "R32F_CASTER_PASS_PARAMETER",
    "R32F_CASTER_PASS_SHADER_PATH",
    "R32F_CASTER_PASS_TECHNIQUE",
    "R32F_RECEIVER_PROBE_PARAMETER",
    "R32F_RECEIVER_PROBE_SHADER_PATH",
    "R32F_RECEIVER_PROBE_TECHNIQUE",
    "RECEIVER_PROBE_OPERATION_NAME",
    "RECEIVER_PROBE_TARGET_NAME",
    "RECEIVER_PROBE_TARGET_SIZE",
    "LIGHT_SPACE_CAMERA_NAME",
    "SCENE_OPERATION_NAME",
    "SHADOW_COLOR_TARGET_NAME",
    "SHADOW_DEPTH_TARGET_NAME",
    "SHADOW_TARGET_OPERATION_NAME",
    "SHADOW_TARGET_SIZE",
    "SELF_SHADOW_MAP_DRAW_FLAG",
    "SELF_SHADOW_RECEIVER_DRAW_FLAG",
    "ShadowCasterSelection",
    "ShadowTargetClearRender",
    "ShadowTargetResources",
    "ReceiverProbeResources",
    "discover_self_shadow_caster_components",
    "discover_self_shadow_receiver_components",
    "initializePlugin",
    "is_registered",
    "registered_override",
    "uninitializePlugin",
]
