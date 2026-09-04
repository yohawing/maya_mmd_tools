"""PMX material morph metadata から runtime DG グラフを構築する。"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import os
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from maya import cmds

from mmd_tools.core.constants import ATTR_MMD_MATERIAL_INDEX
from mmd_tools.core.logger import get_logger
from mmd_tools.converters.morph_runtime_common import (
    connect_if_needed as _connect_if_needed,
    get_morph_order,
    is_connected as _is_connected,
    parse_morph_offsets_json,
    same_source as _same_source,
)
from mmd_tools.converters.morph_scene_metadata import iter_morph_network_metadata
from mmd_tools.converters.material_shader_parameters import hardware_morph_routes


logger = get_logger(__name__)

EVAL_NODE_TYPE = "mmdMaterialMorphEval"
_REQUIRED_EVAL_ATTRS = (
    "contribution",
    "baseDiffuse",
    "outputDiffuse",
    "outputDiffuseAlpha",
    "baseSpecular",
    "outputSpecular",
    "baseSpecularCoefficient",
    "outputSpecularCoefficient",
    "baseAmbient",
    "outputAmbient",
    "baseEdgeColor",
    "outputEdgeColor",
    "baseEdgeSize",
    "outputEdgeSize",
    "outputTextureMultiply",
    "outputTextureAdd",
    "outputSphereTextureMultiply",
    "outputSphereTextureAdd",
    "outputToonTextureMultiply",
    "outputToonTextureAdd",
)

# Backend identifiers returned by resolve_shader_color_route.
BACKEND_STANDARD = "standard"
BACKEND_DX11 = "dx11"
BACKEND_GLSL = "glsl"
BACKEND_UNKNOWN = "unknown"

# Effective VP2 draw-API ids from detect_effective_vp2_draw_api().
VP2_API_DIRECTX11 = "directx11"
VP2_API_OPENGL = "opengl"
VP2_API_OPENGL_CORE = "openglcore"
VP2_API_UNKNOWN = "unknown"

# Direct colour plugs for Maya standard materials (stable contracts).
_STANDARD_ATTR_BY_TYPE = {
    "standardSurface": "baseColor",
    "lambert": "color",
    "phong": "color",
    "blinn": "color",
}

# Shared RGB contract for dx11Shader (.fx) and GLSLShader (.ogsfx).
# Both expose DiffuseColorRGB (float3/vec3); alpha is a separate plug and must
# never be overwritten by the material-morph evaluator (double3 output).
_DX11_DIFFUSE_CANDIDATES: Tuple[str, ...] = ("DiffuseColorRGB",)
_GLSL_DIFFUSE_CANDIDATES: Tuple[str, ...] = ("DiffuseColorRGB",)
_RGB_TRIPLE_TYPES = frozenset({"double3", "float3"})
_GL_VP2_APIS = frozenset({VP2_API_OPENGL, VP2_API_OPENGL_CORE})


@dataclass(frozen=True)
class ShaderColorRoute:
    """Resolved material-morph colour target for one shader backend.

    Attributes:
        backend: Backend id (``standard`` / ``dx11`` / ``glsl`` / ``unknown``).
        attr_name: Destination attribute short name when routing is safe.
        skip_reason: Deterministic skip code when routing must fail closed.
    """

    backend: str
    attr_name: Optional[str] = None
    skip_reason: Optional[str] = None

    @property
    def is_usable(self) -> bool:
        """Return True when the route has a verified connectable attribute."""
        return bool(self.attr_name) and not self.skip_reason


def detect_effective_vp2_draw_api() -> str:
    """Return the effective VP2 draw API using operational Maya diagnostics.

    Prefers ``cmds.ogs(deviceInformation=True)`` and falls back to the
    ``vp2RenderingEngine`` optionVar.  Does **not** use ``cmds.about(api=True)``,
    which is not a reliable VP2 device probe in this project.

    Returns:
        One of :data:`VP2_API_DIRECTX11`, :data:`VP2_API_OPENGL`,
        :data:`VP2_API_OPENGL_CORE`, or :data:`VP2_API_UNKNOWN`.
    """
    device_text = ""
    try:
        raw = cmds.ogs(deviceInformation=True)
        if isinstance(raw, (list, tuple)):
            device_text = " ".join(str(part) for part in raw)
        elif raw is not None:
            device_text = str(raw)
    except Exception:
        device_text = ""

    device_api = _classify_vp2_draw_api_text(device_text)
    if device_api in {VP2_API_DIRECTX11, VP2_API_OPENGL_CORE}:
        return device_api

    override_api = _classify_vp2_draw_api_text(
        os.environ.get("MAYA_VP2_DEVICE_OVERRIDE", "")
    )

    option_text = ""
    try:
        option_text = str(cmds.optionVar(query="vp2RenderingEngine") or "")
    except Exception:
        option_text = ""

    option_api = _classify_vp2_draw_api_text(option_text)
    if device_api == VP2_API_OPENGL:
        # Maya's GLCore device diagnostic often reports only "OpenGL V.4.6".
        # Refine generic GL with explicit process state, then a core optionVar;
        # never let a stale non-core option override the live device family.
        if override_api == VP2_API_OPENGL_CORE or option_api == VP2_API_OPENGL_CORE:
            return VP2_API_OPENGL_CORE
        return VP2_API_OPENGL
    if override_api != VP2_API_UNKNOWN:
        return override_api
    return option_api


def _classify_vp2_draw_api_text(text: str) -> str:
    """Map free-form Maya VP2 diagnostic text to a stable API id."""
    if not text:
        return VP2_API_UNKNOWN

    lowered = text.lower()
    # DirectX first: "API : DirectX V.11" / "Direct3D11" / optionVar "DirectX11".
    if any(
        token in lowered
        for token in ("directx", "direct3d11", "dx11", "d3d11")
    ):
        return VP2_API_DIRECTX11

    # GL Core before plain OpenGL (optionVar "OpenGLCoreProfile", VirtualDeviceGLCore).
    if any(
        token in lowered
        for token in ("openglcore", "glcore", "core profile", "coreprofile")
    ):
        return VP2_API_OPENGL_CORE

    if any(token in lowered for token in ("opengl", "open gl", "virtualdevicegl")):
        # Avoid matching bare "gl" inside unrelated tokens when OpenGL is absent.
        return VP2_API_OPENGL

    return VP2_API_UNKNOWN


def resolve_mmd_shader_backend(configured_backend: str, vp2_api: str) -> str:
    """Resolve the MMD hardware shader backend from the effective VP2 API.

    ``standard`` remains an explicit opt-out from hardware MMD shaders.  For
    hardware rendering, the live VP2 device is authoritative: DirectX 11 must
    use ``dx11Shader`` and either OpenGL family must use ``GLSLShader``.  An
    unknown device fails closed to ``standard`` so an effect is never handed to
    the wrong compiler.
    """
    configured = str(configured_backend or "auto").strip().lower()
    if configured == BACKEND_STANDARD:
        return BACKEND_STANDARD
    if vp2_api == VP2_API_DIRECTX11:
        return BACKEND_DX11
    if vp2_api in _GL_VP2_APIS:
        return BACKEND_GLSL
    return BACKEND_STANDARD


def build_material_morph_graph(root_group: str) -> Dict[str, Any]:
    """Build the complete PMX material morph runtime under *root_group*.

    Args:
        root_group: Imported MMD model root group.
    Returns:
        Summary dict.
    """
    result: Dict[str, Any] = {
        "success": True,
        "evaluator_nodes": [],
        "created": 0,
        "reused": 0,
        "contributions": 0,
        "native_alpha": None,
        "skipped": [],
    }
    if not root_group or not cmds.objExists(root_group):
        result["success"] = False
        result["skipped"].append("root_group_missing")
        return result

    shaders_by_index = _collect_shaders_by_material_index(root_group)
    if not shaders_by_index:
        result["skipped"].append("no_indexed_shaders")
        return result

    contributions_by_shader = _collect_contributions_by_shader(
        _iter_material_morph_nodes(root_group),
        shaders_by_index,
        result["skipped"],
    )
    existing_by_shader = {
        shader: node
        for shader, node in _collect_existing_evaluators().items()
        if shader in set(shaders_by_index.values())
    }
    for shader in sorted(set(existing_by_shader) - set(contributions_by_shader)):
        _remove_evaluator(shader, existing_by_shader[shader])
    if not contributions_by_shader:
        result["skipped"].append("no_material_morph_contributions")

    # Detect VP2 API once per graph build; standard materials ignore it.
    vp2_api = detect_effective_vp2_draw_api()

    evaluator_nodes_by_shader = {}
    for shader, contributions in contributions_by_shader.items():
        node = existing_by_shader.get(shader)
        if node and _is_valid_evaluator(node):
            result["reused"] += 1
        else:
            node = _create_evaluator(shader)
            if not node:
                result["success"] = False
                result["skipped"].append(f"create_failed:{shader}")
                continue
            result["created"] += 1

        _mark_evaluator(node, shader)
        _refresh_contributions(node, contributions)
        route = resolve_shader_color_route(shader, vp2_api=vp2_api)
        if route.is_usable:
            route = _complete_hardware_route(shader, route)
        if not route.is_usable:
            skip = route.skip_reason or f"color_route_unavailable:{shader}"
            result["skipped"].append(skip)
            logger.debug(
                "Skipping material morph colour reroute for %s (%s backend, vp2=%s): %s",
                shader,
                route.backend,
                vp2_api,
                skip,
            )
        else:
            if not _reroute_complete_shader(shader, node, route):
                result["success"] = False
                result["skipped"].append(f"complete_route_failed:{shader}")
        result["evaluator_nodes"].append(node)
        evaluator_nodes_by_shader[shader] = node
        result["contributions"] += len(contributions)

    native_shapes = _collect_native_render_shapes(root_group)
    if native_shapes:
        native_results = []
        for render_shape in native_shapes:
            native_results.append(
                bind_native_material_alpha(
                    root_group,
                    render_shape,
                    shaders_by_index=shaders_by_index,
                    evaluators_by_shader=evaluator_nodes_by_shader,
                )
            )
        result["native_alpha"] = native_results[0] if len(native_results) == 1 else native_results
        for native_result in native_results:
            result["success"] = result["success"] and native_result["success"]
            result["skipped"].extend(native_result.get("skipped", []))

    return result


def bind_native_material_alpha(
    root_group: str,
    render_shape: str,
    *,
    shaders_by_index: Optional[Dict[int, str]] = None,
    evaluators_by_shader: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Bind evaluator alpha outputs or restore authored alpha on a native VP2 shape.

    The native VP2 path keeps standardSurface nodes as authored fallback
    materials.  Its per-material alpha is therefore driven through the
    existing ``mmdMaterialMorphEval`` result instead of routing the standard
    shader's colour plug.  ``materialAlpha`` is indexed by the original PMX
    material index, so material-split queue order cannot change the binding.

    ``baseDiffuseA`` is refreshed from the authored material metadata on every
    graph rebuild.  This deliberately avoids reading the native shape's
    evaluated alpha, which would make a rebuild at a non-zero morph weight
    capture the evaluated result as the new base.
    """
    result: Dict[str, Any] = {
        "success": True,
        "bindings": [],
        "skipped": [],
    }
    if not root_group or not cmds.objExists(root_group):
        result["success"] = False
        result["skipped"].append("root_group_missing")
        return result
    if not render_shape or not cmds.objExists(render_shape):
        result["success"] = False
        result["skipped"].append("render_shape_missing")
        return result
    try:
        has_material_alpha = cmds.attributeQuery(
            "materialAlpha", node=render_shape, exists=True
        )
    except Exception:
        has_material_alpha = False
    if not has_material_alpha:
        result["success"] = False
        result["skipped"].append("material_alpha_unavailable")
        return result

    if shaders_by_index is None:
        shaders_by_index = _collect_shaders_by_material_index(root_group)
    if evaluators_by_shader is None:
        evaluators_by_shader = _collect_existing_evaluators()
    for material_index, shader in sorted(shaders_by_index.items()):
        evaluator = evaluators_by_shader.get(shader)
        if evaluator and not _is_valid_evaluator(evaluator):
            result["success"] = False
            result["skipped"].append(f"invalid_evaluator:{material_index}:{evaluator}")
            continue

        try:
            base_alpha = _read_shader_base_alpha(
                shader,
                prefer_authored_metadata=True,
            )
            destination = f"{render_shape}.materialAlpha[{int(material_index)}]"
            if evaluator:
                cmds.setAttr(f"{evaluator}.baseDiffuseA", float(base_alpha))
                _connect_if_needed(
                    f"{evaluator}.outputDiffuseAlpha",
                    destination,
                    force=True,
                )
            else:
                cmds.setAttr(destination, float(base_alpha))
        except Exception:
            result["success"] = False
            failure = "bind_failed" if evaluator else "reset_failed"
            result["skipped"].append(f"{failure}:{material_index}:{shader}:{evaluator}")
            logger.warning(
                "Failed to %s native material alpha for %s (material %s)",
                "bind" if evaluator else "restore",
                shader,
                material_index,
                exc_info=True,
            )
            continue
        result["bindings"].append(
            {
                "material_index": int(material_index),
                "shader": shader,
                "evaluator": evaluator,
                "destination": destination,
                "base_alpha": float(base_alpha),
            }
        )
    if not result["bindings"] and shaders_by_index:
        result["success"] = False
    return result


def _collect_native_render_shapes(root_group: str) -> List[str]:
    """Find source-mesh-connected native VP2 shapes owned by *root_group*."""
    try:
        shapes = cmds.listRelatives(
            root_group,
            allDescendents=True,
            type="mmdRenderShape",
            fullPath=True,
        ) or []
    except Exception:
        return []
    if not isinstance(shapes, (list, tuple)):
        return []

    connected = []
    for shape in shapes:
        try:
            if cmds.listConnections(
                f"{shape}.inputMesh",
                source=True,
                destination=False,
            ):
                connected.append(str(shape))
        except Exception:
            continue
    return connected


# ---------------------------------------------------------------------------
# Discovery helpers
# ---------------------------------------------------------------------------

def _collect_shaders_by_material_index(root_group: str) -> Dict[int, str]:
    """Find shader nodes with mmd_material_index under root_group's meshes."""
    shapes = cmds.listRelatives(root_group, allDescendents=True, type="mesh", fullPath=True) or []
    if not shapes:
        return {}

    shading_groups = set()
    for shape in shapes:
        sgs = cmds.listConnections(shape, type="shadingEngine") or []
        shading_groups.update(sgs)

    shaders_by_index: Dict[int, str] = {}
    for sg in shading_groups:
        shader_list = cmds.ls(cmds.listConnections(sg) or [], materials=True) or []
        for shader in shader_list:
            if not cmds.attributeQuery(ATTR_MMD_MATERIAL_INDEX, node=shader, exists=True):
                continue
            try:
                mat_index = int(cmds.getAttr(f"{shader}.{ATTR_MMD_MATERIAL_INDEX}"))
            except Exception:
                continue
            shaders_by_index[mat_index] = shader
    return shaders_by_index


def _iter_material_morph_nodes(root_group: str) -> Iterable[str]:
    for metadata in iter_morph_network_metadata(
        root_group=root_group,
        morph_types={"material"},
        required_attrs=("mmd_material_morph_offsets_json",),
    ):
        yield metadata.node


def _collect_contributions_by_shader(
    morph_nodes: Iterable[str],
    shaders_by_index: Dict[int, str],
    skipped: List[str],
) -> Dict[str, List[Dict[str, Any]]]:
    contributions_by_shader: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for morph_node in morph_nodes:
        offsets = _parse_offsets_json(morph_node)
        if offsets is None:
            skipped.append(f"invalid_offsets:{morph_node}")
            continue
        morph_order = _get_morph_order(morph_node)
        for offset in offsets:
            contribution = _offset_to_contribution(morph_node, morph_order, offset)
            if contribution is None:
                skipped.append(f"invalid_offset:{morph_node}")
                continue
            shader = shaders_by_index.get(contribution["material_index"])
            if shader is None:
                # material_index == -1 means "all materials"
                if contribution["material_index"] == -1:
                    for shader in shaders_by_index.values():
                        # Each evaluator owns and annotates its contribution.
                        # Do not share mutable contribution dictionaries across shaders.
                        contributions_by_shader[shader].append(dict(contribution))
                else:
                    skipped.append(f"missing_shader:{morph_node}:{contribution['material_index']}")
                continue
            contributions_by_shader[shader].append(contribution)

    for contributions in contributions_by_shader.values():
        contributions.sort(key=lambda c: (c["morph_order"], c["morph_node"]))
    return dict(contributions_by_shader)


def _parse_offsets_json(morph_node: str) -> Optional[List[Dict[str, Any]]]:
    return parse_morph_offsets_json(morph_node, "mmd_material_morph_offsets_json")


def _is_neutral_diffuse_rgb_offset(offset: Dict[str, Any]) -> bool:
    """Compatibility probe for callers inspecting diffuse RGB neutrality.

    Neutral RGB offsets are still retained now because alpha and the other PMX
    channels may carry a non-neutral contribution.
    """
    if not isinstance(offset, dict):
        return False
    try:
        diffuse = offset.get("diffuse", [0.0, 0.0, 0.0, 0.0])
        if len(diffuse) < 3:
            return False
        neutral = 1.0 if int(offset.get("operation_type", 1)) == 0 else 0.0
        return all(abs(float(value) - neutral) <= 1.0e-7 for value in diffuse[:3])
    except (TypeError, ValueError):
        return False


def _get_morph_order(morph_node: str) -> int:
    return get_morph_order(morph_node)


def _offset_to_contribution(
    morph_node: str,
    morph_order: int,
    offset: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    if not isinstance(offset, dict) or "material_index" not in offset:
        return None
    try:
        operation_type = int(offset.get("operation_type", 1))
        neutral = 1.0 if operation_type == 0 else 0.0

        def vector(key: str, size: int) -> Tuple[float, ...]:
            value = offset.get(key, [neutral] * size)
            if len(value) != size:
                raise ValueError(f"{key} must contain {size} values")
            return tuple(float(component) for component in value)

        return {
            "morph_node": morph_node,
            "morph_order": int(morph_order),
            "material_index": int(offset["material_index"]),
            "operation_type": operation_type,
            "diffuse": vector("diffuse", 4),
            "specular": vector("specular", 3),
            "specular_coefficient": (float(offset.get("specular_coefficient", neutral)),),
            "ambient": vector("ambient", 3),
            "edge_color": vector("edge_color", 4),
            "edge_size": (float(offset.get("edge_size", neutral)),),
            "texture_factor": vector("texture_factor", 4),
            "sphere_texture_factor": vector("sphere_texture_factor", 4),
            "toon_texture_factor": vector("toon_texture_factor", 4),
        }
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Evaluator node management
# ---------------------------------------------------------------------------

def _collect_existing_evaluators() -> Dict[str, str]:
    evaluators: Dict[str, str] = {}
    for node in cmds.ls(type=EVAL_NODE_TYPE) or []:
        if not _is_valid_evaluator(node):
            continue
        if not cmds.attributeQuery("mmd_target_shader", node=node, exists=True):
            continue
        if not cmds.attributeQuery("mmd_material_morph_eval", node=node, exists=True):
            continue
        try:
            if not cmds.getAttr(f"{node}.mmd_material_morph_eval"):
                continue
            shader = cmds.getAttr(f"{node}.mmd_target_shader") or ""
        except Exception:
            continue
        if shader and cmds.objExists(shader):
            evaluators[shader] = node
    return evaluators


def _remove_evaluator(shader: str, node: str) -> None:
    """Restore base inputs and delete one owned material-morph evaluator."""
    shader_type = cmds.nodeType(shader)
    bindings: list[tuple[str, Optional[str], str, int]] = []
    if shader_type in {"dx11Shader", "GLSLShader"}:
        bindings = [
            (route.uniform, route.evaluator_base, route.evaluator_output, route.size)
            for route in hardware_morph_routes(shader_type)
        ]
        try:
            bindings = _expand_route_bindings(shader, node, bindings)
        except Exception:
            logger.warning("Could not expand evaluator cleanup routes for %s", shader, exc_info=True)
            raise
    else:
        route = resolve_shader_color_route(shader)
        if route.attr_name:
            bindings = [(route.attr_name, "baseDiffuse", "outputDiffuse", 3)]

    for _shader_name, base_name, output_name, _size in bindings:
        output = f"{node}.{output_name}"
        destinations = cmds.listConnections(output, s=False, d=True, p=True) or []
        for destination in destinations:
            cmds.disconnectAttr(output, destination)
            if base_name is None:
                continue
            base = f"{node}.{base_name}"
            sources = cmds.listConnections(base, s=True, d=False, p=True) or []
            if sources:
                for source in sources:
                    cmds.disconnectAttr(source, base)
                    _connect_if_needed(source, destination, force=True)
            else:
                value = cmds.getAttr(base)
                destination_type = cmds.getAttr(destination, type=True)
                while (
                    isinstance(value, (list, tuple))
                    and len(value) == 1
                    and isinstance(value[0], (list, tuple))
                ):
                    value = value[0]
                if destination_type in {"double3", "float3"} and isinstance(value, (list, tuple)):
                    value = value[:3]
                _set_plug_value(destination, value, destination_type)

    cmds.delete(node)


def _create_evaluator(shader: str) -> Optional[str]:
    node_name = f"{shader.split('|')[-1]}_materialMorphEval"
    try:
        node = cmds.createNode(EVAL_NODE_TYPE, name=node_name)
    except Exception as exc:
        logger.warning("Failed to create %s for %s: %s", EVAL_NODE_TYPE, shader, exc)
        return None
    if _is_valid_evaluator(node):
        return node
    logger.warning(
        "Created %s for %s, but required attributes are unavailable; skipping material morph runtime",
        EVAL_NODE_TYPE,
        shader,
    )
    try:
        if cmds.objExists(node):
            cmds.delete(node)
    except Exception:
        logger.debug("Failed to delete invalid %s node %s", EVAL_NODE_TYPE, node, exc_info=True)
    return None


def _is_valid_evaluator(node: str) -> bool:
    """Return whether *node* is a usable mmdMaterialMorphEval instance."""
    if not node or not cmds.objExists(node):
        return False
    try:
        if cmds.nodeType(node) != EVAL_NODE_TYPE:
            return False
        return all(cmds.attributeQuery(attr, node=node, exists=True) for attr in _REQUIRED_EVAL_ATTRS)
    except Exception:
        return False


def _mark_evaluator(node: str, shader: str) -> None:
    if not cmds.attributeQuery("mmd_material_morph_eval", node=node, exists=True):
        cmds.addAttr(node, longName="mmd_material_morph_eval", attributeType="bool")
    cmds.setAttr(f"{node}.mmd_material_morph_eval", True)
    if not cmds.attributeQuery("mmd_target_shader", node=node, exists=True):
        cmds.addAttr(node, longName="mmd_target_shader", dataType="string")
    cmds.setAttr(f"{node}.mmd_target_shader", shader, type="string")
    if not cmds.attributeQuery("mmd_complete_route_ready", node=node, exists=True):
        cmds.addAttr(node, longName="mmd_complete_route_ready", attributeType="bool")
        cmds.setAttr(f"{node}.mmd_complete_route_ready", False)


def _refresh_contributions(node: str, contributions: List[Dict[str, Any]]) -> None:
    for index in cmds.getAttr(f"{node}.contribution", multiIndices=True) or []:
        try:
            cmds.removeMultiInstance(f"{node}.contribution[{index}]", b=True)
        except Exception:
            pass

    for slot, contribution in enumerate(contributions):
        prefix = f"{node}.contribution[{slot}]"
        cmds.setAttr(f"{prefix}.morphOrder", int(contribution["morph_order"]))
        cmds.setAttr(f"{prefix}.operationType", int(contribution["operation_type"]))
        attr_values = {
            "diffuseOffset": contribution["diffuse"],
            "specularOffset": contribution["specular"],
            "specularCoefficientOffset": contribution["specular_coefficient"],
            "ambientOffset": contribution["ambient"],
            "edgeColorOffset": contribution["edge_color"],
            "edgeSizeOffset": contribution["edge_size"],
            "textureOffset": contribution["texture_factor"],
            "sphereTextureOffset": contribution["sphere_texture_factor"],
            "toonTextureOffset": contribution["toon_texture_factor"],
        }
        for attr_name, values in attr_values.items():
            plug = f"{prefix}.{attr_name}"
            if len(values) == 1:
                cmds.setAttr(plug, values[0])
            elif len(values) == 3:
                cmds.setAttr(plug, *values, type="double3")
            else:
                for axis, value in zip("RGBA", values):
                    cmds.setAttr(f"{plug}{axis}", value)
        _connect_if_needed(f"{contribution['morph_node']}.weight", f"{prefix}.weight", force=True)


def resolve_shader_color_route(
    shader: str,
    *,
    vp2_api: Optional[str] = None,
) -> ShaderColorRoute:
    """Resolve a backend- and VP2-API-aware colour plug for material morph routing.

    Standard Maya materials are API-independent.  ``dx11Shader`` may route only
    when the effective VP2 draw API is DirectX11 **and** a writable
    ``DiffuseColorRGB`` plug is demonstrated.  ``GLSLShader`` may route only on
    OpenGL / OpenGL Core with a validated ``DiffuseColorRGB`` plug.  Unknown API
    state fails closed because a writable plug alone does not prove that a saved
    hardware-shader connection is portable to the next Maya session.  The
    evaluator never reconnects a vec4 ``DiffuseColor`` plug (would corrupt alpha).

    Args:
        shader: Material / shader node name.
        vp2_api: Optional pre-detected VP2 API id.  When omitted, calls
            :func:`detect_effective_vp2_draw_api` once for hardware backends.

    Returns:
        A :class:`ShaderColorRoute`.  Unusable routes leave the shader untouched
        and expose a deterministic ``skip_reason``.
    """
    if not shader or not cmds.objExists(shader):
        return ShaderColorRoute(
            backend=BACKEND_UNKNOWN,
            skip_reason=f"shader_missing:{shader or ''}",
        )

    try:
        node_type = cmds.nodeType(shader) or ""
    except Exception:
        return ShaderColorRoute(
            backend=BACKEND_UNKNOWN,
            skip_reason=f"shader_type_unavailable:{shader}",
        )

    if node_type == "dx11Shader":
        effective_api = vp2_api if vp2_api is not None else detect_effective_vp2_draw_api()
        if effective_api != VP2_API_DIRECTX11:
            return ShaderColorRoute(
                backend=BACKEND_DX11,
                skip_reason=f"dx11_vp2_not_directx11:{shader}",
            )
        return _resolve_candidate_route(
            shader,
            backend=BACKEND_DX11,
            candidates=_DX11_DIFFUSE_CANDIDATES,
            skip_reason=f"dx11_diffuse_unroutable:{shader}",
        )

    if node_type == "GLSLShader":
        effective_api = vp2_api if vp2_api is not None else detect_effective_vp2_draw_api()
        if effective_api not in _GL_VP2_APIS:
            return ShaderColorRoute(
                backend=BACKEND_GLSL,
                skip_reason=f"glsl_vp2_not_opengl:{shader}",
            )
        return _resolve_candidate_route(
            shader,
            backend=BACKEND_GLSL,
            candidates=_GLSL_DIFFUSE_CANDIDATES,
            skip_reason=f"glsl_diffuse_unroutable:{shader}",
        )

    # Standard / unknown Maya materials: API-independent colour contracts.
    attr_name = _STANDARD_ATTR_BY_TYPE.get(node_type, "color")
    if _is_rgb_plug_contract_safe(shader, attr_name):
        return ShaderColorRoute(backend=BACKEND_STANDARD, attr_name=attr_name)

    # Unknown Maya material types still try the conventional ``color`` plug.
    if attr_name != "color" and _is_rgb_plug_contract_safe(shader, "color"):
        return ShaderColorRoute(backend=BACKEND_STANDARD, attr_name="color")

    return ShaderColorRoute(
        backend=BACKEND_STANDARD if node_type in _STANDARD_ATTR_BY_TYPE else BACKEND_UNKNOWN,
        skip_reason=f"standard_diffuse_unroutable:{shader}",
    )


def _complete_hardware_route(shader: str, route: ShaderColorRoute) -> ShaderColorRoute:
    """Require the entire PMX material plug set before exposing a route."""
    if not route.is_usable:
        return route
    if route.backend not in {BACKEND_DX11, BACKEND_GLSL}:
        return ShaderColorRoute(
            backend=route.backend,
            skip_reason=f"complete_material_backend_unsupported:{shader}",
        )
    shader_type = "dx11Shader" if route.backend == BACKEND_DX11 else "GLSLShader"
    required = tuple(
        (item.uniform, item.size) for item in hardware_morph_routes(shader_type)
    ) + (("Opacity", 1),)
    missing = [name for name, size in required if not _is_writable_plug(shader, name, size)]
    if missing:
        return ShaderColorRoute(
            backend=route.backend,
            skip_reason=f"{route.backend}_material_plugs_incomplete:{shader}:{','.join(missing)}",
        )
    return route


def _is_writable_plug(shader: str, attr_name: str, size: int) -> bool:
    try:
        if not cmds.attributeQuery(attr_name, node=shader, exists=True):
            return False
        if not cmds.attributeQuery(attr_name, node=shader, writable=True):
            return False
        if cmds.getAttr(f"{shader}.{attr_name}", lock=True):
            return False
        attr_type = cmds.getAttr(f"{shader}.{attr_name}", type=True)
    except Exception:
        return False
    if size == 1:
        return attr_type in {"double", "float", "long", "short"}
    return len(_scalar_leaf_attrs(shader, attr_name)) == size


def _scalar_leaf_attrs(
    node: str,
    attr_name: str,
    *,
    max_depth: int = 4,
    require_writable: bool = True,
) -> List[str]:
    """Flatten nested Maya compounds to writable scalar leaves in declared order."""
    result: List[str] = []
    visited = set()

    def visit(current: str, depth: int) -> None:
        if current in visited or depth > max_depth:
            return
        visited.add(current)
        try:
            children = cmds.attributeQuery(current, node=node, listChildren=True) or []
        except Exception:
            children = []
        if children:
            for child in children:
                visit(child, depth + 1)
            return
        plug = f"{node}.{current}"
        try:
            attr_type = cmds.getAttr(plug, type=True)
            writable = cmds.attributeQuery(current, node=node, writable=True)
            locked = cmds.getAttr(plug, lock=True)
        except Exception:
            return
        if (writable or not require_writable) and not locked and attr_type in {
            "double", "float", "long", "short"
        }:
            result.append(current)

    visit(attr_name, 0)
    return result


def _expand_route_bindings(
    shader: str,
    node: str,
    bindings: Sequence[Tuple[str, Optional[str], str, int]],
) -> List[Tuple[str, Optional[str], str, int]]:
    """Expand every vector route to scalar leaves so nested GLSL compounds connect."""
    expanded: List[Tuple[str, Optional[str], str, int]] = []
    for shader_name, base_name, output_name, size in bindings:
        if size == 1:
            expanded.append((shader_name, base_name, output_name, 1))
            continue
        shader_leaves = _scalar_leaf_attrs(shader, shader_name)
        output_leaves = _scalar_leaf_attrs(node, output_name, require_writable=False)
        base_leaves = _scalar_leaf_attrs(node, base_name) if base_name else []
        if len(shader_leaves) != size or len(output_leaves) != size or (
            base_name and len(base_leaves) < size
        ):
            raise RuntimeError(
                f"route leaf arity mismatch: {shader}.{shader_name} "
                f"shader={shader_leaves} base={base_leaves} output={output_leaves}"
            )
        for index in range(size):
            expanded.append((
                shader_leaves[index],
                base_leaves[index] if base_name else None,
                output_leaves[index],
                1,
            ))
    return expanded


def _reroute_complete_shader(shader: str, node: str, route: ShaderColorRoute) -> bool:
    """Atomically preflight, initialize, and connect every evaluator output."""
    complete = _complete_hardware_route(shader, route)
    if not complete.is_usable:
        return False
    shader_type = "dx11Shader" if route.backend == BACKEND_DX11 else "GLSLShader"
    route_contract = hardware_morph_routes(shader_type)
    bindings = [
        (item.uniform, item.evaluator_base, item.evaluator_output, item.size)
        for item in route_contract
        if not item.uniform.startswith("EdgeColor")
    ]
    if route.backend == BACKEND_DX11:
        edge_rgb = next(item for item in route_contract if item.uniform == "EdgeColorRGB")
        edge_alpha = next(item for item in route_contract if item.uniform == "EdgeColorA")
        edge_rgb_children = cmds.attributeQuery(
            edge_rgb.uniform, node=shader, listChildren=True
        ) or []
        if len(edge_rgb_children) != 3:
            return False
        bindings.extend(
            (
                edge_rgb_children[index],
                f"{edge_rgb.evaluator_base}{axis}",
                f"{edge_rgb.evaluator_output}{axis}",
                1,
            )
            for index, axis in enumerate("RGB")
        )
        bindings.append((
            edge_alpha.uniform,
            edge_alpha.evaluator_base,
            edge_alpha.evaluator_output,
            edge_alpha.size,
        ))
    else:
        edge_color = next(item for item in route_contract if item.uniform == "EdgeColor")
        bindings.append((
            edge_color.uniform,
            edge_color.evaluator_base,
            edge_color.evaluator_output,
            edge_color.size,
        ))
    try:
        bindings = _expand_route_bindings(shader, node, bindings)
    except Exception:
        logger.warning("Material morph route leaf expansion failed for %s", shader, exc_info=True)
        return False
    if _complete_route_already_owned(shader, node, bindings):
        return True
    touched = []
    for shader_name, base_name, output_name, size in bindings:
        destination = f"{shader}.{shader_name}"
        touched.extend(_expanded_plugs(destination, size))
        if base_name is not None:
            touched.extend(_expanded_plugs(f"{node}.{base_name}", size))
    touched.append(f"{shader}.Opacity")
    touched.extend((
        f"{node}.mmd_complete_route_ready",
        f"{node}.mmd_target_shader",
    ))
    snapshots = {plug: _snapshot_plug(plug) for plug in dict.fromkeys(touched)}
    try:
        cmds.setAttr(f"{node}.mmd_complete_route_ready", False)
        for source in _exact_incoming_sources(f"{shader}.Opacity"):
            cmds.disconnectAttr(source, f"{shader}.Opacity")
        cmds.setAttr(f"{shader}.Opacity", 1.0)
        for shader_name, base_name, output_name, size in bindings:
            destination = f"{shader}.{shader_name}"
            output = f"{node}.{output_name}"
            if base_name is not None:
                _initialize_and_reroute_base(destination, f"{node}.{base_name}", output, size)
            _connect_if_needed(output, destination, force=True)
        cmds.setAttr(f"{node}.mmd_complete_route_ready", True)
    except Exception:
        logger.warning("Material morph complete route failed for %s", shader, exc_info=True)
        _restore_plug_snapshots(snapshots)
        return False
    return True


def _complete_route_already_owned(
    shader: str,
    node: str,
    bindings: Sequence[Tuple[str, Optional[str], str, int]],
) -> bool:
    """Return whether this evaluator already owns every final hardware plug."""
    try:
        if not cmds.getAttr(f"{node}.mmd_complete_route_ready"):
            return False
        if abs(float(cmds.getAttr(f"{shader}.Opacity")) - 1.0) > 1.0e-7:
            return False
        if _exact_incoming_sources(f"{shader}.Opacity"):
            return False
    except Exception:
        return False
    return all(
        _is_connected(f"{node}.{output_name}", f"{shader}.{shader_name}")
        for shader_name, _base_name, output_name, _size in bindings
    )


def _expanded_plugs(parent: str, size: int) -> List[str]:
    """Return parent plus actual compound children in Maya's declared order."""
    result = [parent]
    if size <= 1:
        return result
    node, attr = parent.split(".", 1)
    try:
        children = cmds.attributeQuery(attr, node=node, listChildren=True) or []
        result.extend(f"{node}.{child}" for child in children[:size])
    except Exception:
        pass
    return result


def _snapshot_plug(plug: str) -> Dict[str, Any]:
    """Capture value/type and incoming connections for transaction rollback."""
    snapshot: Dict[str, Any] = {"sources": _exact_incoming_sources(plug)}
    try:
        snapshot["type"] = cmds.getAttr(plug, type=True)
        snapshot["value"] = cmds.getAttr(plug)
        node, attr = plug.split(".", 1)
        snapshot["has_children"] = bool(
            cmds.attributeQuery(attr, node=node, listChildren=True) or []
        )
    except Exception:
        snapshot["value"] = None
    return snapshot


def _restore_plug_snapshots(snapshots: Dict[str, Dict[str, Any]]) -> None:
    """Best-effort exact restoration after a failed complete-route transaction."""
    # Disconnect every current route first; child values cannot be restored while
    # their parent compound is still driven.
    for plug in snapshots:
        for source in _exact_incoming_sources(plug):
            try:
                cmds.disconnectAttr(source, plug)
            except Exception:
                logger.debug("Failed to disconnect rollback plug %s", plug, exc_info=True)
    # Restore leaf/scalar values. Compound parent values are represented by their
    # exact child snapshots and intentionally skipped.
    for plug in reversed(list(snapshots)):
        original = snapshots[plug]
        value = original.get("value")
        if value is not None and not original["sources"] and not original.get("has_children"):
            try:
                _set_plug_value(plug, value, original.get("type"))
            except Exception:
                logger.debug("Failed to restore rollback value %s", plug, exc_info=True)
    # Finally restore the original topology after all stored values are back.
    for plug, original in snapshots.items():
        for source in original["sources"]:
            try:
                cmds.connectAttr(source, plug, force=True)
            except Exception:
                logger.error("Failed to restore rollback connection %s -> %s", source, plug, exc_info=True)


def _set_plug_value(plug: str, value: Any, attr_type: Optional[str]) -> None:
    """Restore scalar/string/vector values including Maya's nested vec4 shape."""
    while (
        isinstance(value, (list, tuple))
        and len(value) == 1
        and isinstance(value[0], (list, tuple))
    ):
        value = value[0]
    if attr_type == "string":
        cmds.setAttr(plug, value, type="string")
    elif isinstance(value, (list, tuple)) and attr_type in {
        "double2", "double3", "double4", "float2", "float3", "float4"
    }:
        cmds.setAttr(plug, *value, type=attr_type)
    elif not isinstance(value, (list, tuple)):
        cmds.setAttr(plug, value)


def _initialize_and_reroute_base(destination: str, base: str, output: str, size: int) -> None:
    """Transfer authored/current final values and incoming drivers to evaluator base."""
    value = cmds.getAttr(destination)
    if size > 1 and isinstance(value, (list, tuple)) and value and isinstance(value[0], (list, tuple)):
        value = value[0]
    values = (float(value),) if size == 1 else tuple(float(v) for v in value[:size])
    destination_node, destination_attr = destination.split(".", 1)
    base_node, base_attr = base.split(".", 1)
    output_node, output_attr = output.split(".", 1)
    destination_children = []
    base_children = []
    output_children = []
    if size > 1:
        destination_children = cmds.attributeQuery(
            destination_attr, node=destination_node, listChildren=True
        ) or []
        base_children = cmds.attributeQuery(base_attr, node=base_node, listChildren=True) or []
        output_children = cmds.attributeQuery(output_attr, node=output_node, listChildren=True) or []
        if len(destination_children) < size or len(base_children) < size or len(output_children) < size:
            raise RuntimeError(f"compound child arity mismatch: {destination} -> {base}")
    if size == 1:
        cmds.setAttr(base, values[0])
    else:
        for child, component in zip(base_children, values):
            cmds.setAttr(f"{base_node}.{child}", component)
    sources = _exact_incoming_sources(destination)
    for source in sources:
        if _same_source(source, output):
            continue
        cmds.disconnectAttr(source, destination)
        _connect_if_needed(source, base, force=True)
    for destination_child_name, base_child_name, output_child_name in zip(
        destination_children, base_children, output_children
    ):
        destination_child = f"{destination_node}.{destination_child_name}"
        base_child = f"{base_node}.{base_child_name}"
        output_child = f"{output_node}.{output_child_name}"
        for source in _exact_incoming_sources(destination_child):
            if _same_source(source, output_child):
                continue
            cmds.disconnectAttr(source, destination_child)
            _connect_if_needed(source, base_child, force=True)


def _exact_incoming_sources(plug: str) -> List[str]:
    """Return only the source connected to this exact plug, excluding child aggregation."""
    try:
        if not cmds.connectionInfo(plug, isExactDestination=True):
            return []
        source = cmds.connectionInfo(plug, sourceFromDestination=True)
        return [source] if source else []
    except Exception:
        return list(cmds.listConnections(plug, s=True, d=False, p=True) or [])


def _resolve_candidate_route(
    shader: str,
    *,
    backend: str,
    candidates: Sequence[str],
    skip_reason: str,
) -> ShaderColorRoute:
    """Pick the first candidate that passes the RGB plug contract check."""
    for attr_name in candidates:
        if _is_rgb_plug_contract_safe(shader, attr_name):
            return ShaderColorRoute(backend=backend, attr_name=attr_name)
    return ShaderColorRoute(backend=backend, skip_reason=skip_reason)


def _is_rgb_plug_contract_safe(shader: str, attr_name: str) -> bool:
    """Return True when *attr_name* is a demonstrated writable RGB morph target.

    Existence alone is not enough: generated dx11 uniforms may be locked or
    internally driven.  The contract requires Maya command probes for
    existence, writability, lock state, and an RGB-compatible type layout.
    """
    if not shader or not attr_name:
        return False
    try:
        if not cmds.attributeQuery(attr_name, node=shader, exists=True):
            return False
    except Exception:
        return False

    plug = f"{shader}.{attr_name}"
    try:
        if cmds.getAttr(plug, lock=True):
            return False
    except Exception:
        return False

    try:
        if not cmds.attributeQuery(attr_name, node=shader, writable=True):
            return False
    except Exception:
        return False

    # RGB triple plug (dx11 DiffuseColorRGB, lambert.color, etc.).
    try:
        attr_type = cmds.getAttr(plug, type=True)
    except Exception:
        attr_type = None
    if attr_type in _RGB_TRIPLE_TYPES:
        return True

    # Colour compound with R/G/B children (standard materials, some OGSFX vec4).
    if _has_per_channel_children(shader, attr_name):
        for axis in ("R", "G", "B"):
            child = f"{attr_name}{axis}"
            child_plug = f"{shader}.{child}"
            try:
                if cmds.getAttr(child_plug, lock=True):
                    return False
                if not cmds.attributeQuery(child, node=shader, writable=True):
                    return False
            except Exception:
                return False
        return True

    return False


def _get_diffuse_attr_name(shader: str) -> Optional[str]:
    """Return the verified diffuse attribute name, or None when unroutable.

    Kept as a thin compatibility wrapper around :func:`resolve_shader_color_route`.
    """
    route = resolve_shader_color_route(shader)
    return route.attr_name if route.is_usable else None


def _has_per_channel_children(shader: str, attr_name: str) -> bool:
    """Return True if ``shader.attrNameR`` (per-channel sub-attr) exists."""
    try:
        return all(
            cmds.attributeQuery(f"{attr_name}{axis}", node=shader, exists=True)
            for axis in ("R", "G", "B")
        )
    except Exception:
        return False


def _reroute_shader_color(shader: str, node: str, route: Optional[ShaderColorRoute] = None) -> bool:
    """Intercept shader diffuse colour through the evaluator node.

    Args:
        shader: Target material/shader node.
        node: ``mmdMaterialMorphEval`` evaluator node.
        route: Pre-resolved colour route.  When omitted, resolves on demand.

    Returns:
        True when the colour plug was connected (or already connected).
        False when the route is unusable; the shader connection is left untouched.
    """
    if route is None:
        route = resolve_shader_color_route(shader)
    if not route.is_usable or not route.attr_name:
        return False

    diffuse_name = route.attr_name
    output_attr = f"{node}.outputDiffuse"
    base_attr = f"{node}.baseDiffuse"
    shader_attr = f"{shader}.{diffuse_name}"

    if _reroute_standard_surface_texture_gain(shader, node, shader_attr):
        return True

    if _is_connected(output_attr, shader_attr):
        return True

    # Copy current colour and alpha to the RGBA baseDiffuse compound.  Maya does
    # not guarantee that setAttr(type="double3") is accepted by a four-child
    # compound, so write every child explicitly.
    try:
        color = cmds.getAttr(shader_attr)[0]
        rgba = (*color[:3], _read_shader_base_alpha(shader))
        for axis, value in zip("RGBA", rgba):
            cmds.setAttr(f"{base_attr}{axis}", float(value))
    except Exception:
        logger.debug("Failed to read %s", shader_attr, exc_info=True)

    # Reroute any existing connections to baseDiffuse
    for source in cmds.listConnections(shader_attr, s=True, d=False, p=True) or []:
        if _same_source(source, output_attr):
            continue
        try:
            cmds.disconnectAttr(source, shader_attr)
        except Exception:
            pass
        _connect_if_needed(source, base_attr, force=True)

    # Per-channel rerouting (lambert.colorR/G/B, standardSurface.baseColorR/G/B).
    # dx11 DiffuseColorRGB is typically a flat double3 without R/G/B children.
    if _has_per_channel_children(shader, diffuse_name):
        for axis in ("R", "G", "B"):
            shader_axis = f"{shader}.{diffuse_name}{axis}"
            base_axis = f"{base_attr}{axis[-1].lower()}"
            output_axis = f"{output_attr}{axis[-1].lower()}"
            for source in cmds.listConnections(shader_axis, s=True, d=False, p=True) or []:
                if _same_source(source, output_axis):
                    continue
                try:
                    cmds.disconnectAttr(source, shader_axis)
                except Exception:
                    pass
                _connect_if_needed(source, base_axis, force=True)

    _connect_if_needed(output_attr, shader_attr, force=True)
    return True


def _reroute_standard_surface_texture_gain(shader: str, node: str, shader_attr: str) -> bool:
    """Drive a directly connected file texture's colorGain for material morphs.

    Keeping ``file.outColor -> standardSurface.baseColor`` intact preserves
    Viewport 2.0's built-in fallback when texture display is disabled. The
    evaluator therefore owns the PMX diffuse multiplier on ``file.colorGain``
    instead of intercepting the shader's baseColor connection.
    """
    try:
        if cmds.nodeType(shader) != "standardSurface":
            return False
        sources = cmds.listConnections(shader_attr, s=True, d=False, p=True) or []
    except Exception:
        return False

    file_node = None
    for source in sources:
        source_node = source.split(".", 1)[0]
        try:
            if cmds.nodeType(source_node) == "file" and source.split(".", 1)[1] == "outColor":
                file_node = source_node
                break
        except (IndexError, TypeError):
            continue
        except Exception:
            continue
    if not file_node:
        return False

    output_attr = f"{node}.outputDiffuse"
    base_attr = f"{node}.baseDiffuse"
    gain_attr = f"{file_node}.colorGain"
    if _is_connected(output_attr, gain_attr):
        return True

    try:
        gain = cmds.getAttr(gain_attr)[0]
        for axis, value in zip("RGB", gain[:3]):
            cmds.setAttr(f"{base_attr}{axis}", float(value))
        cmds.setAttr(f"{base_attr}A", _read_shader_base_alpha(shader))
    except Exception:
        logger.debug("Failed to read %s", gain_attr, exc_info=True)

    for source in cmds.listConnections(gain_attr, s=True, d=False, p=True) or []:
        if _same_source(source, output_attr):
            continue
        try:
            cmds.disconnectAttr(source, gain_attr)
        except Exception:
            pass
        _connect_if_needed(source, base_attr, force=True)

    _connect_if_needed(output_attr, gain_attr, force=True)
    return True


def _read_shader_base_alpha(
    shader: str,
    *,
    prefer_authored_metadata: bool = False,
) -> float:
    """Read an opacity-compatible alpha for evaluator initialization."""
    scalar_candidates = (
        ("mmd_diffuse_alpha", "DiffuseColorA")
        if prefer_authored_metadata
        else ("DiffuseColorA",)
    )
    for attr_name in scalar_candidates:
        try:
            if cmds.attributeQuery(attr_name, node=shader, exists=True):
                return float(cmds.getAttr(f"{shader}.{attr_name}"))
        except Exception:
            pass

    try:
        if cmds.attributeQuery("opacity", node=shader, exists=True):
            opacity = cmds.getAttr(f"{shader}.opacity")[0]
            return sum(float(value) for value in opacity[:3]) / 3.0
    except Exception:
        pass

    try:
        if cmds.attributeQuery("transparency", node=shader, exists=True):
            transparency = cmds.getAttr(f"{shader}.transparency")[0]
            average = sum(float(value) for value in transparency[:3]) / 3.0
            return 1.0 - average
    except Exception:
        pass

    return 1.0
