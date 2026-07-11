"""PMX material morph metadata から runtime DG グラフを構築する。"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import hashlib
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


logger = get_logger(__name__)

EVAL_NODE_TYPE = "mmdMaterialMorphEval"
_REQUIRED_EVAL_ATTRS = (
    "contribution",
    "baseDiffuse",
    "outputDiffuse",
    "outputTextureMultiply",
    "outputTextureAdd",
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

    api = _classify_vp2_draw_api_text(device_text)
    if api != VP2_API_UNKNOWN:
        return api

    option_text = ""
    try:
        option_text = str(cmds.optionVar(query="vp2RenderingEngine") or "")
    except Exception:
        option_text = ""

    return _classify_vp2_draw_api_text(option_text)


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


def build_material_morph_graph(root_group: str, *, connect_shader: bool = False) -> Dict[str, Any]:
    """Optionally build PMX material morph evaluator connections under *root_group*.

    Args:
        root_group: Imported MMD model root group.
        connect_shader: Explicit opt-in for the incomplete diffuse-RGB-only
            runtime. Normal imports leave this false until every PMX material
            channel can be reproduced safely.

    Returns:
        Summary dict.
    """
    result: Dict[str, Any] = {
        "success": True,
        "evaluator_nodes": [],
        "created": 0,
        "reused": 0,
        "contributions": 0,
        "skipped": [],
    }
    if not root_group or not cmds.objExists(root_group):
        result["success"] = False
        result["skipped"].append("root_group_missing")
        return result

    if not connect_shader:
        result["skipped"].append("material_morph_shader_routing_disabled")
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
    _append_group_weight_sources(
        contributions_by_shader,
        _iter_group_morph_nodes(root_group),
        result["skipped"],
    )
    if not contributions_by_shader:
        result["skipped"].append("no_material_morph_contributions")
        return result

    # Detect VP2 API once per graph build; standard materials ignore it.
    vp2_api = detect_effective_vp2_draw_api()

    existing_by_shader = _collect_existing_evaluators()
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
            _reroute_shader_color(shader, node, route)
        result["evaluator_nodes"].append(node)
        result["contributions"] += len(contributions)

    return result


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


def _iter_group_morph_nodes(root_group: str) -> Iterable[str]:
    for metadata in iter_morph_network_metadata(
        root_group=root_group,
        morph_types={"group"},
        required_attrs=("mmd_group_morph_offsets_json",),
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
                        # Sharing this dict would make group sources accumulate
                        # once per shader during the later global-index pass.
                        contributions_by_shader[shader].append(dict(contribution))
                else:
                    skipped.append(f"missing_shader:{morph_node}:{contribution['material_index']}")
                continue
            contributions_by_shader[shader].append(contribution)

    for contributions in contributions_by_shader.values():
        contributions.sort(key=lambda c: (c["morph_order"], c["morph_node"]))
    return dict(contributions_by_shader)


def _append_group_weight_sources(
    contributions_by_shader: Dict[str, List[Dict[str, Any]]],
    group_morph_nodes: Iterable[str],
    skipped: List[str],
) -> None:
    """Add one-level group morph weights to referenced material contributions.

    PMX group offsets use global morph indices.  Material contribution records
    retain their direct weight and receive zero or more additional
    ``group.weight * morph_rate`` sources.  References to groups (including
    self/nested/cyclic networks) are deliberately ignored so this builder can
    never introduce a dependency cycle.
    """
    contributions_by_index: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    for contributions in contributions_by_shader.values():
        for contribution in contributions:
            morph_index = contribution.get("morph_index")
            if morph_index is not None:
                contributions_by_index[int(morph_index)].append(contribution)

    group_nodes = list(group_morph_nodes)
    group_indices = set()
    for node in group_nodes:
        index = _get_explicit_morph_index(node)
        if index is not None:
            group_indices.add(index)
    for group_node in group_nodes:
        group_index = _get_explicit_morph_index(group_node)
        if group_index is None:
            skipped.append(f"missing_group_morph_index:{group_node}")
            continue
        offsets = parse_morph_offsets_json(group_node, "mmd_group_morph_offsets_json")
        if offsets is None:
            skipped.append(f"invalid_group_offsets:{group_node}")
            continue
        for offset in offsets:
            try:
                target_index = int(offset["morph_index"])
                rate = float(offset.get("morph_rate", 0.0))
            except (KeyError, TypeError, ValueError):
                skipped.append(f"invalid_group_offset:{group_node}")
                continue
            if target_index in group_indices:
                skipped.append(f"nested_group_reference_unsupported:{group_node}:{target_index}")
                logger.warning(
                    "Ignoring nested/cyclic group morph reference %s -> morph index %s",
                    group_node,
                    target_index,
                )
                continue
            targets = contributions_by_index.get(target_index)
            if not targets:
                continue
            source = (group_index, group_node, rate)
            for contribution in targets:
                contribution.setdefault("group_weight_sources", []).append(source)

    for contributions in contributions_by_shader.values():
        for contribution in contributions:
            contribution.get("group_weight_sources", []).sort(
                key=lambda source: (source[0], source[1], source[2])
            )


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


def _get_explicit_morph_index(morph_node: str) -> Optional[int]:
    if not cmds.attributeQuery("mmd_morph_index", node=morph_node, exists=True):
        return None
    try:
        return int(cmds.getAttr(f"{morph_node}.mmd_morph_index"))
    except Exception:
        return None


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
            "morph_index": _get_explicit_morph_index(morph_node),
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
        try:
            shader = cmds.getAttr(f"{node}.mmd_target_shader") or ""
        except Exception:
            continue
        if shader and cmds.objExists(shader):
            evaluators[shader] = node
    return evaluators


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
        _connect_contribution_weight(node, slot, contribution, f"{prefix}.weight")


def _connect_contribution_weight(
    evaluator_node: str,
    slot: int,
    contribution: Dict[str, Any],
    destination: str,
) -> None:
    """Connect direct + one-level group weights additively and deterministically."""
    group_sources = contribution.get("group_weight_sources") or []
    direct_source = f"{contribution['morph_node']}.weight"
    if not group_sources:
        _connect_if_needed(direct_source, destination, force=True)
        return

    token = _collision_safe_node_token(evaluator_node)
    sum_node = _get_or_create_owned_helper(
        evaluator_node,
        "plusMinusAverage",
        f"contribution{slot}:sum",
        f"{token}_contribution{slot}_effectiveWeight",
    )
    cmds.setAttr(f"{sum_node}.operation", 1)
    for index in cmds.getAttr(f"{sum_node}.input1D", multiIndices=True) or []:
        try:
            cmds.removeMultiInstance(f"{sum_node}.input1D[{index}]", b=True)
        except Exception:
            pass
    _connect_if_needed(direct_source, f"{sum_node}.input1D[0]", force=True)
    for source_slot, (_, group_node, rate) in enumerate(group_sources, start=1):
        multiplier = _get_or_create_owned_helper(
            evaluator_node,
            "multiplyDivide",
            f"contribution{slot}:group{source_slot}",
            f"{token}_contribution{slot}_groupWeight{source_slot}",
        )
        cmds.setAttr(f"{multiplier}.operation", 1)
        cmds.setAttr(f"{multiplier}.input2X", float(rate))
        _connect_if_needed(f"{group_node}.weight", f"{multiplier}.input1X", force=True)
        _connect_if_needed(f"{multiplier}.outputX", f"{sum_node}.input1D[{source_slot}]", force=True)
    _connect_if_needed(f"{sum_node}.output1D", destination, force=True)


def _collision_safe_node_token(node: str) -> str:
    long_names = cmds.ls(node, long=True) or [node]
    identity = str(long_names[0])
    digest = hashlib.sha1(identity.encode("utf-8")).hexdigest()[:10]
    token = node.split("|")[-1]
    for char in (":", ".", "[", "]"):
        token = token.replace(char, "_")
    return f"{token}_{digest}"


def _get_or_create_owned_helper(
    evaluator_node: str,
    node_type: str,
    helper_key: str,
    desired_name: str,
) -> str:
    """Reuse only a helper explicitly owned by *evaluator_node* and *helper_key*."""
    for candidate in cmds.listConnections(
        f"{evaluator_node}.message",
        source=False,
        destination=True,
    ) or []:
        try:
            if cmds.nodeType(candidate) != node_type:
                continue
            if not cmds.attributeQuery("mmd_weight_helper_key", node=candidate, exists=True):
                continue
            if cmds.getAttr(f"{candidate}.mmd_weight_helper_key") == helper_key:
                return candidate
        except Exception:
            continue

    helper = cmds.createNode(node_type, name=desired_name)
    cmds.addAttr(helper, longName="mmd_weight_owner", attributeType="message")
    cmds.addAttr(helper, longName="mmd_weight_helper_key", dataType="string")
    cmds.setAttr(f"{helper}.mmd_weight_helper_key", helper_key, type="string")
    _connect_if_needed(f"{evaluator_node}.message", f"{helper}.mmd_weight_owner", force=True)
    return helper


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


def _read_shader_base_alpha(shader: str) -> float:
    """Read an opacity-compatible alpha for evaluator initialization."""
    scalar_candidates = ("DiffuseColorA",)
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
