"""Opt-in, passthrough Viewport 2.0 render override.

The default R1 override is intentionally a lifecycle proof only: it delegates
the ordinary Maya scene, HUD, and present operations without shader routing,
scene filtering, or user preference changes.  The separate R2 resource probe
is development-only and opt-in; it only reports conservative caster draw and
target readback evidence, never receiver composition or self-shadow parity.
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
SELF_SHADOW_MAP_DRAW_FLAG = 0x04
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


def discover_self_shadow_caster_components(cmds_module=None) -> ShadowCasterSelection:
    """Return only components whose PMX material has the cast-shadow bit.

    MMD's ``mmd_draw_flags`` bit ``0x04`` controls drawing to the self-shadow
    map.  Missing, unreadable, or malformed attributes fail closed so an
    arbitrary Maya material is never silently added as a caster.  The result
    is scoped to imported MMD model roots, keeping unrelated Maya scene meshes
    out of the future shadow pass.
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
                    if not draw_flags & SELF_SHADOW_MAP_DRAW_FLAG:
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
            "outputTransform": "one-minus-sampled-value",
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
        operations = (self._scene_operation, PassthroughHUDRender(), PassthroughPresentTarget())
        if self._receiver_probe is not None:
            operations = (self._receiver_probe, *operations)
        if self._shadow_target_operation is not None:
            operations = (self._shadow_target_operation, *operations)
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
                if self._r32f_binding_probe is not None:
                    # Bind only the plugin-owned R32F target.  This setup probe is
                    # intentionally disconnected from imported shaders and draw
                    # operations, so it cannot alter ordinary viewport output.
                    self._r32f_binding_probe.bind(targets[0] if targets else None)
            except Exception:
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
                raise
        self._scene_operation.configure_panel_background(destination)
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
        return report

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
    "ShadowCasterSelection",
    "ShadowTargetClearRender",
    "ShadowTargetResources",
    "ReceiverProbeResources",
    "discover_self_shadow_caster_components",
    "initializePlugin",
    "is_registered",
    "registered_override",
    "uninitializePlugin",
]
