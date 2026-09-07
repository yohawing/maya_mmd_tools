"""Capture and compare the semantic Maya scene contract of an MMD import.

This module deliberately has no Maya import at module load time.  The GUI
runner supplies ``maya.cmds`` and ``maya.api.OpenMaya`` to :func:`capture_scene`;
the comparator is consequently usable from ordinary Python unit tests as well.

The contract is keyed by root-relative transform paths and PMX semantic
indices.  Shape leaf names and namespaces are retained as diagnostic data but
are not semantic identity.  Native implementation nodes and proxy nodes have
their own contracts, so filtering them out of the public DAG cannot hide a
missing proxy or a changed proxy connection.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping, Sequence
from typing import Any

SCHEMA_VERSION = 1

TOLERANCES = {
    "position": 1.0e-6,
    "normal": 1.0e-5,
    "uv": 1.0e-6,
    "weight": 1.0e-6,
    "matrix": 1.0e-6,
    "attribute": 1.0e-6,
}

ALLOWED_DIFFERENCES = (
    {
        "id": "namespace-prefix",
        "scope": "root-relative paths and connection endpoints",
        "reason": "Import routes may use distinct Maya namespaces.",
    },
    {
        "id": "auto-shape-name",
        "scope": "mesh shape leaf name",
        "reason": "Maya may generate different shape leaf names; the parent transform and shape index remain semantic.",
    },
    {
        "id": "mmdRenderShape-implementation",
        "scope": "implementationNodes and implementationLinks",
        "reason": "The native VP2 route may add an mmdRenderShape and its visibility ownership connection.",
    },
    {
        "id": "proxy-node-name",
        "scope": "proxy name when the proxy semantic key is stable",
        "reason": "Proxy names are implementation-generated; proxy count and links remain required.",
    },
    {
        "id": "vmd-authoring-uuid",
        "scope": "mmd_vmd_authoring_target_uuid, destinations_json owner_uuid, and known unit_conversion.uuid values",
        "reason": "Maya generates different UUIDs per import; capture resolves only these authority references to semantic endpoints and retains the raw-to-semantic mapping in normalizationLedger.",
    },
)

EXCLUSIONS = (
    {
        "id": "implementation-nodes",
        "scope": "mmdRenderShape rows are absent from semanticDag",
        "reason": "They are compared by the separate implementation contract.",
    },
    {
        "id": "runtime-only-morph-behavior",
        "scope": "none",
        "reason": "Current evaluated geometry is required; metadata alone never proves morph behavior.",
    },
)

_PATH_KEYS = {"path", "key", "transformPath", "sourcePath", "root", "src", "dst", "endpoint"}
_PHYSICS_TYPES = {
    "mmdRigidBodyShape",
    "mmdPhysicsJointShape",
    "mmdPhysicsWorldShape",
    "mmdPhysicsSolver",
    "mmdPhysicsBoneDriver",
}
_MORPH_TYPES = {
    "blendShape",
    "mmdMorphController",
    "mmdBoneMorphAccum",
    "mmdMaterialMorphEval",
}
_MATERIAL_NODE_TYPES = {
    "anisotropic",
    "blinn",
    "dx11Shader",
    "lambert",
    "phong",
    "phongE",
    "standardSurface",
}
_MORPH_KINDS = {
    "bone",
    "vertex",
    "group",
    "material",
    "uv",
    "additional_uv1",
    "additional_uv2",
    "additional_uv3",
    "additional_uv4",
}
_PROXY_MARKERS = {"mmd_vmd_authoring_proxy"}
_VMD_PROXY_MARKER = "mmd_vmd_authoring_proxy"
_VMD_TARGET_PATH = "mmd_vmd_authoring_target_path"
_VMD_TARGET_UUID = "mmd_vmd_authoring_target_uuid"
_VMD_DESTINATIONS = "mmd_vmd_authoring_destinations_json"
_SEMANTIC_PREFIXES = {
    "bone",
    "material",
    "morph",
    "physics",
    "joint",
    "proxy",
    "rigidbody",
    "physicsjoint",
    "physicsworld",
    "physicssolver",
    "physicsdriver",
}
_SEMANTIC_ID_RE = re.compile(
    r"^(?:" + "|".join(sorted(_SEMANTIC_PREFIXES)) + r"):-?\d+(?:@[^.]+)?(?:\..*)?$"
)
_PHYSICS_SEMANTIC_PREFIX = {
    "mmdRigidBodyShape": "rigidbody",
    "mmdPhysicsJointShape": "physicsjoint",
    "mmdPhysicsWorldShape": "physicsworld",
    "mmdPhysicsSolver": "physicssolver",
    "mmdPhysicsBoneDriver": "physicsdriver",
}
_MATERIAL_ATTRS = {
    "color",
    "diffuseColor",
    "transparency",
    "specularColor",
    "ambientColor",
    "incandescence",
    "eccentricity",
    "specularRollOff",
    "reflectivity",
    "roughness",
    "baseColor",
    "base",
    "baseWeight",
    "diffuse",
    "specular",
    "specularRoughness",
    "metalness",
    "opacity",
    "transmission",
    "subsurface",
    "coat",
    "emissionColor",
    "emissionWeight",
    "normalCamera",
    "outColor",
    "outTransparency",
    "DiffuseColor",
    "DiffuseColorRGB",
    "DiffuseColorA",
    "SpecularColor",
    "AmbientColor",
    "ToonCoordinateOffset",
    "EdgeColor",
    "EdgeColorRGB",
    "EdgeColorA",
    "Shininess",
    "EdgeSize",
    "SphereMode",
    "Opacity",
    "HasMainTexture",
    "HasSphereTexture",
    "HasToonTexture",
    "MainTextureMultiply",
    "MainTextureAdd",
    "SphereTextureMultiply",
    "SphereTextureAdd",
    "ToonTextureMultiply",
    "ToonTextureAdd",
    "glowIntensity",
    "mmd_material",
    "mmd_material_index",
    "mmd_material_name",
    "mmd_material_name_en",
    "mmd_draw_flags",
    "mmd_edge_color",
    "mmd_edge_size",
    "mmd_edge_alpha",
    "mmd_texture_path",
    "mmd_texture_index",
    "mmd_sphere_path",
    "mmd_sphere_mode",
    "mmd_toon_path",
    "mmd_toon_texture_index",
    "mmd_texture_unresolved",
    "mmd_texture_cache_path",
    "mmd_material_morph_offsets_json",
}
_MORPH_ATTRS = {
    "mmd_morph_name",
    "mmd_morph_name_en",
    "mmd_morph_type",
    "mmd_morph_index",
    "mmd_morph_panel",
    "mmd_bone_morph_offsets_json",
    "mmd_vertex_morph_offsets_json",
    "mmd_uv_morph_offsets_json",
    "mmd_material_morph_offsets_json",
    "mmd_morph_weight",
}
_SKIN_ATTRS = {
    "skinningMethod",
    "normalizeWeights",
    "maintainMaxInfluences",
    "maxInfluences",
    "bindMethod",
}
_PHYSICS_ATTRS = {
    "pmxIndex",
    "nameJp",
    "nameEn",
    "enable",
    "shapeType",
    "shapeSizeX",
    "shapeSizeY",
    "shapeSizeZ",
    "positionX",
    "positionY",
    "positionZ",
    "rotationX",
    "rotationY",
    "rotationZ",
    "physicsMode",
    "mass",
    "linearDamping",
    "angularDamping",
    "friction",
    "restitution",
    "collisionGroup",
    "collisionMask",
    "relatedBoneIndex",
    "rigidBodyAIndex",
    "rigidBodyBIndex",
    "solverIndex",
}


def _strip_namespace(value: str) -> str:
    # Semantic keys use a colon as their own delimiter (``bone:12`` and
    # ``material:3``); treating that delimiter as a Maya namespace would make
    # unrelated keys collide.
    if _SEMANTIC_ID_RE.match(value):
        return value
    return "|".join(part.rsplit(":", 1)[-1] for part in value.split("|"))


def _json_value(value: Any) -> Any:
    """Convert common Maya API values to plain JSON-compatible containers."""

    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, Sequence):
        return [_json_value(item) for item in value]
    # MPoint, MVector, MColor and similar API values expose named components.
    component_names = ("x", "y", "z", "w")
    if all(hasattr(value, name) for name in component_names[:3]):
        result = [float(getattr(value, name)) for name in component_names[:3]]
        if hasattr(value, "w"):
            result.append(float(value.w))
        return result
    try:
        return [_json_value(item) for item in value]
    except (TypeError, ValueError):
        return repr(value)


def _call(obj: Any, name: str, *args: Any, default: Any = None, **kwargs: Any) -> Any:
    function = getattr(obj, name, None)
    if function is None:
        raise RuntimeError(f"required Maya API is unavailable: {name}")
    value = function(*args, **kwargs)
    return default if value is None else value


def _required_call(obj: Any, name: str, *args: Any, **kwargs: Any) -> Any:
    function = getattr(obj, name, None)
    if function is None:
        raise RuntimeError(f"required Maya API is unavailable: {name}")
    try:
        value = function(*args, **kwargs)
    except Exception as exc:
        raise RuntimeError(f"required Maya API failed: {name}: {exc}") from exc
    if value is None:
        raise RuntimeError(f"required Maya API returned no value: {name}")
    return value


def _required_get_attr(cmds: Any, plug: str) -> Any:
    function = getattr(cmds, "getAttr", None)
    if function is None:
        raise RuntimeError("required Maya API is unavailable: getAttr")
    try:
        return function(plug)
    except Exception as exc:
        raise RuntimeError(f"required Maya attribute read failed: {plug}: {exc}") from exc


def _required_list_attrs(cmds: Any, node: str) -> list[Any]:
    function = getattr(cmds, "listAttr", None)
    if function is None:
        raise RuntimeError("required Maya API is unavailable: listAttr")
    try:
        return list(function(node, userDefined=True) or [])
    except Exception as exc:
        raise RuntimeError(f"required Maya attribute enumeration failed: {node}: {exc}") from exc


def _long_name(cmds: Any, node: str) -> str:
    values = _required_call(cmds, "ls", node, long=True) or []
    if not values:
        raise RuntimeError(f"Maya node does not exist: {node}")
    return str(values[0])


def _node_type(cmds: Any, node: str) -> str:
    return str(_required_call(cmds, "nodeType", node))


def _relative_path(root: str, node: str) -> str:
    root = _strip_namespace(root)
    node = _strip_namespace(node)
    if node == root:
        return "<root>"
    prefix = root.rstrip("|") + "|"
    if node.startswith(prefix):
        return node[len(prefix) :]
    return node.lstrip("|")


def _leaf(path: str) -> str:
    return path.rsplit("|", 1)[-1]


def _attr_exists(cmds: Any, node: str, attr: str) -> bool:
    answer = _call(cmds, "attributeQuery", attr, node=node, exists=True, default=None)
    if answer is not None:
        return bool(answer)
    return _call(cmds, "getAttr", f"{node}.{attr}", default=_MISSING) is not _MISSING


class _Missing:
    pass


_MISSING = _Missing()


def _read_attr(cmds: Any, node: str, attr: str) -> Any:
    if not _attr_exists(cmds, node, attr):
        return _MISSING
    # Maya message attributes intentionally have no value.  Their semantic
    # evidence is the directed connection captured by ``_connections``.
    # Calling getAttr without this type check raises on an unconnected message
    # plug and would make otherwise valid roots fail closed.
    attr_type = _required_get_attr_type(cmds, f"{node}.{attr}")
    if attr_type == "message":
        return _MISSING
    plug = f"{node}.{attr}"
    try:
        value = _required_get_attr(cmds, plug)
    except RuntimeError:
        # DX11 shader vector parameters can expose a compound parent plug that
        # Maya refuses to return as one value.  Read its typed children while
        # retaining the original failure for unsupported leaf attributes.
        children = list(_call(cmds, "attributeQuery", attr, node=node, listChildren=True, default=[]) or [])
        if not children:
            raise
        compound: dict[str, Any] = {}
        for child in children:
            child_value = _read_attr(cmds, node, str(child))
            if child_value is not _MISSING:
                compound[str(child)] = child_value
        return compound
    return _json_value(value)


def _required_get_attr_type(cmds: Any, plug: str) -> str:
    function = getattr(cmds, "getAttr", None)
    if function is None:
        raise RuntimeError("required Maya API is unavailable: getAttr")
    try:
        value = function(plug, type=True)
    except Exception as exc:
        raise RuntimeError(f"required Maya attribute type read failed: {plug}: {exc}") from exc
    if value is None:
        raise RuntimeError(f"required Maya attribute type read returned no value: {plug}")
    return str(value)


def _user_attrs(cmds: Any, node: str) -> list[str]:
    values = _required_list_attrs(cmds, node)
    return sorted({str(value) for value in values if str(value).startswith("mmd_")})


def _attrs(cmds: Any, node: str, names: Sequence[str] = ()) -> dict[str, Any]:
    attributes = set(str(name) for name in names)
    attributes.update(_user_attrs(cmds, node))
    result: dict[str, Any] = {}
    for attr in sorted(attributes):
        value = _read_attr(cmds, node, attr)
        if value is not _MISSING:
            result[attr] = value
    return result


def _resolve_uuid_endpoint(cmds: Any, root: str, raw_uuid: Any, field: str, expected_node: str | None = None) -> tuple[str, str]:
    """Resolve one authoring UUID and return its semantic message endpoint."""

    raw = str(raw_uuid or "")
    if not raw:
        raise RuntimeError(f"{field} is empty")
    matches = _call(cmds, "ls", raw, long=True, default=[]) or []
    if len(matches) != 1:
        raise RuntimeError(f"{field} UUID must resolve to exactly one Maya node: {raw!r} -> {matches!r}")
    resolved = _long_name(cmds, str(matches[0]))
    if expected_node is not None:
        expected = _long_name(cmds, str(expected_node))
        if resolved != expected:
            raise RuntimeError(
                f"{field} UUID resolves to a different node: {raw!r} -> {resolved!r}, expected {expected!r}"
            )
    return _endpoint(cmds, root, f"{resolved}.message"), resolved


def _normalize_vmd_authoring_attrs(
    cmds: Any,
    root: str,
    node: str,
    attrs: Mapping[str, Any],
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Normalize only the UUID references owned by redirected VMD proxies."""

    normalized = dict(attrs)
    if attrs.get(_VMD_PROXY_MARKER) is not True:
        return normalized, []
    target_path = attrs.get(_VMD_TARGET_PATH)
    raw_target_uuid = attrs.get(_VMD_TARGET_UUID)
    if not isinstance(target_path, str) or not target_path:
        raise RuntimeError(f"{node}.{_VMD_TARGET_PATH} is required for UUID normalization")
    target_endpoint, _target_node = _resolve_uuid_endpoint(
        cmds,
        root,
        raw_target_uuid,
        f"{node}.{_VMD_TARGET_UUID}",
        expected_node=target_path,
    )
    ledger = [
        {
            "node": _relative_path(root, _long_name(cmds, node)),
            "field": _VMD_TARGET_UUID,
            "raw": str(raw_target_uuid),
            "semantic": target_endpoint,
        }
    ]
    normalized[_VMD_TARGET_UUID] = target_endpoint

    raw_destinations = attrs.get(_VMD_DESTINATIONS)
    if not isinstance(raw_destinations, str) or not raw_destinations:
        raise RuntimeError(f"{node}.{_VMD_DESTINATIONS} is required for UUID normalization")
    try:
        destinations = json.loads(raw_destinations)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"{node}.{_VMD_DESTINATIONS} is not valid JSON") from exc
    if not isinstance(destinations, dict) or not destinations:
        raise RuntimeError(f"{node}.{_VMD_DESTINATIONS} must contain destination records")
    normalized_destinations = {}
    for channel, record in destinations.items():
        if not isinstance(record, Mapping):
            raise RuntimeError(f"{node}.{_VMD_DESTINATIONS}.{channel} must be an object")
        plug = str(record.get("plug") or "")
        owner_uuid = record.get("owner_uuid")
        owner_name, separator, owner_attr = plug.partition(".")
        if not separator or not owner_name or not owner_attr:
            raise RuntimeError(f"{node}.{_VMD_DESTINATIONS}.{channel}.plug is not a node plug: {plug!r}")
        expected_owner = _long_name(cmds, owner_name)
        owner_endpoint, _owner_node = _resolve_uuid_endpoint(
            cmds,
            root,
            owner_uuid,
            f"{node}.{_VMD_DESTINATIONS}.{channel}.owner_uuid",
            expected_node=expected_owner,
        )
        normalized_record = dict(record)
        normalized_record["owner_uuid"] = owner_endpoint
        unit_conversion = record.get("unit_conversion")
        if isinstance(unit_conversion, Mapping) and "uuid" in unit_conversion:
            wrapper_endpoint, _wrapper_node = _resolve_uuid_endpoint(
                cmds,
                root,
                unit_conversion.get("uuid"),
                f"{node}.{_VMD_DESTINATIONS}.{channel}.unit_conversion.uuid",
            )
            normalized_conversion = dict(unit_conversion)
            normalized_conversion["uuid"] = wrapper_endpoint
            normalized_record["unit_conversion"] = normalized_conversion
            ledger.append(
                {
                    "node": _relative_path(root, _long_name(cmds, node)),
                    "field": f"{_VMD_DESTINATIONS}.{channel}.unit_conversion.uuid",
                    "raw": str(unit_conversion.get("uuid")),
                    "semantic": wrapper_endpoint,
                }
            )
        normalized_destinations[str(channel)] = normalized_record
        ledger.append(
            {
                "node": _relative_path(root, _long_name(cmds, node)),
                "field": f"{_VMD_DESTINATIONS}.{channel}.owner_uuid",
                "raw": str(owner_uuid),
                "semantic": owner_endpoint,
            }
        )
    normalized[_VMD_DESTINATIONS] = json.dumps(
        normalized_destinations,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return normalized, ledger


def _semantic_id(cmds: Any, node: str, node_type: str = "", root: str = "") -> str | None:
    if node_type in _PHYSICS_SEMANTIC_PREFIX:
        physics_prefix = _PHYSICS_SEMANTIC_PREFIX[node_type]
        candidates = (
            ("pmxIndex", physics_prefix),
            ("jointIndex", physics_prefix),
            ("mmd_bone_index", physics_prefix),
        )
    else:
        candidates = []
        if node_type == "joint":
            candidates.append(("mmd_bone_index", "bone"))
        lowered_type = node_type.lower()
        if node_type in _MATERIAL_NODE_TYPES or lowered_type.endswith(("shader", "surface")):
            candidates.append(("mmd_material_index", "material"))
        morph_type = _read_attr(cmds, node, "mmd_morph_type")
        if node_type in _MORPH_TYPES or morph_type in _MORPH_KINDS:
            candidates.append(("mmd_morph_index", "morph"))
        if node_type == "joint":
            candidates.append(("jointIndex", "joint"))
    for attr, prefix in candidates:
        value = _read_attr(cmds, node, attr)
        if value is not _MISSING and isinstance(value, (int, float)) and not isinstance(value, bool):
            semantic = f"{prefix}:{int(value)}"
            if prefix == "material" and root:
                owners = []
                values = _call(
                    cmds,
                    "listConnections",
                    node,
                    plugs=True,
                    connections=True,
                    source=True,
                    destination=True,
                    default=[],
                ) or []
                for index in range(0, len(values) - 1, 2):
                    for plug in (str(values[index]), str(values[index + 1])):
                        match = re.search(r"\.materialMembers\[(\d+)\]$", plug)
                        if not match:
                            continue
                        owner_node = plug.split(".", 1)[0]
                        owner = _relative_path(root, _long_name(cmds, owner_node))
                        owners.append(f"{owner}@{match.group(1)}")
                role = sorted(set(owners))[0] if owners else f"shader:{_strip_namespace(_long_name(cmds, node))}"
                semantic = f"{semantic}@{role}"
            return semantic
    return None


def _mesh_role_index(cmds: Any, parent: str, target: str) -> int:
    """Return a stable mesh role index, excluding implementation shape types."""

    shapes = _call(cmds, "listRelatives", parent, shapes=True, fullPath=True, default=[]) or []
    mesh_shapes = [
        _long_name(cmds, str(shape))
        for shape in shapes
        if _node_type(cmds, str(shape)) == "mesh"
    ]
    active = [
        shape
        for shape in mesh_shapes
        if not bool(_read_attr(cmds, shape, "intermediateObject"))
    ]
    ordered = active + [shape for shape in mesh_shapes if shape not in active]
    long_target = _long_name(cmds, target)
    try:
        return ordered.index(long_target)
    except ValueError:
        return 0


def _endpoint(cmds: Any, root: str, plug: Any) -> str:
    text = str(plug)
    node = text.split(".", 1)[0]
    attr = text[len(node) :]
    long_node = _long_name(cmds, node)
    semantic = _semantic_id(cmds, long_node, _node_type(cmds, long_node), root)
    if semantic:
        return f"{semantic}{attr}"
    if _node_type(cmds, long_node) == "mesh":
        parents = _call(cmds, "listRelatives", long_node, parent=True, fullPath=True, default=[]) or []
        if parents:
            parent = _long_name(cmds, str(parents[0]))
            shape_index = _mesh_role_index(cmds, parent, long_node)
            return f"{_relative_path(root, parent)}#mesh[{shape_index}]{attr}"
    if long_node == node and not node.startswith("|"):
        # A dependency node may not be in the DAG.  Namespace normalization is
        # still useful, while the node type/semantic index disambiguates it.
        path = _strip_namespace(node)
    else:
        path = _relative_path(root, long_node)
    return f"{path}{attr}"


def _connections(cmds: Any, root: str, node: str) -> list[dict[str, str]]:
    values = _call(
        cmds,
        "listConnections",
        node,
        plugs=True,
        connections=True,
        source=True,
        destination=True,
        default=[],
    ) or []
    result: list[dict[str, str]] = []
    for index in range(0, len(values) - 1, 2):
        first_raw, second_raw = str(values[index]), str(values[index + 1])
        first, second = _endpoint(cmds, root, first_raw), _endpoint(cmds, root, second_raw)
        source, destination = first, second
        first_destinations = _call(cmds, "connectionInfo", first_raw, destinationFromSource=True, default=[]) or []
        second_destinations = _call(cmds, "connectionInfo", second_raw, destinationFromSource=True, default=[]) or []
        if isinstance(first_destinations, str):
            first_destinations = [first_destinations]
        if isinstance(second_destinations, str):
            second_destinations = [second_destinations]
        first_source = _call(cmds, "connectionInfo", first_raw, sourceFromDestination=True, default=None)
        second_source = _call(cmds, "connectionInfo", second_raw, sourceFromDestination=True, default=None)
        if second_raw in [str(value) for value in first_destinations] or (second_source and _endpoint(cmds, root, second_source) == first):
            source, destination = first, second
        elif first_raw in [str(value) for value in second_destinations] or (first_source and _endpoint(cmds, root, first_source) == second):
            source, destination = second, first
        result.append({"src": source, "dst": destination})
    return sorted(result, key=lambda item: (item["src"], item["dst"]))


def _textures(cmds: Any, root: str, shader: str) -> list[dict[str, Any]]:
    """Read texture identity and path, keeping it beside material attributes."""

    result = []
    for node in _call(cmds, "listConnections", shader, source=True, destination=False, default=[]) or []:
        node = str(node)
        if not _attr_exists(cmds, node, "fileTextureName") and _node_type(cmds, node) not in {"file", "aiImage", "imageTexture"}:
            continue
        path = _strip_namespace(_long_name(cmds, node))
        result.append(
            {
                "key": path,
                "type": _node_type(cmds, node),
                "attrs": _attrs(cmds, node, ("fileTextureName", "colorSpace", "alphaIsLuminance", "uvTilingMode")),
                "connections": _connections(cmds, root, node),
            }
        )
    return sorted(result, key=lambda item: str(item["key"]))


def _mesh_fn(om: Any, mesh: str) -> Any:
    selection = _required_call(om, "MSelectionList")
    selection.add(mesh)
    return om.MFnMesh(selection.getDagPath(0))


def _vector(value: Any, width: int = 3) -> list[float]:
    raw = _json_value(value)
    if not isinstance(raw, list):
        return []
    return [float(item) for item in raw[:width]]


def _mesh_geometry(cmds: Any, om: Any, root: str, mesh: str, transform_node: str, transform_path: str, shape_index: int) -> dict[str, Any]:
    fn = _mesh_fn(om, mesh)
    points: list[Any] = []
    face_corners: list[dict[str, Any]] = []
    uv_sets: list[dict[str, Any]] = []
    face_normals: list[list[float]] = []
    materials: list[dict[str, Any]] = []
    face_material_indices: list[int] = []
    if fn is not None:
        points = [_vector(value) for value in _required_call(fn, "getPoints")]
        polygon_count = int(getattr(fn, "numPolygons", 0) or 0)
        for face_id in range(polygon_count):
            vertices = [int(value) for value in _required_call(fn, "getPolygonVertices", face_id)]
            corners = []
            for local_index, vertex_id in enumerate(vertices):
                mspace = getattr(getattr(om, "MSpace", None), "kObject", None)
                if mspace is None:
                    raise RuntimeError("required Maya API is unavailable: MSpace.kObject")
                normal = _required_call(fn, "getFaceVertexNormal", face_id, vertex_id, mspace)
                normal_value = _vector(normal)
                corners.append({"vertex": vertex_id, "local": local_index})
                face_normals.append(normal_value)
            face_corners.append({"vertices": vertices, "corners": corners})
        set_names = _required_call(fn, "getUVSetNames")
        for set_name in set_names:
            name = str(set_name)
            uv_values = _required_call(fn, "getUVs", name)
            u_values = list(uv_values[0]) if len(uv_values) > 0 else []
            v_values = list(uv_values[1]) if len(uv_values) > 1 else []
            assignment = _required_call(fn, "getAssignedUVs", name)
            uv_sets.append(
                {
                    "name": name,
                    "values": [[float(u), float(v)] for u, v in zip(u_values, v_values)],
                    "faceCounts": [int(value) for value in list(assignment[0])],
                    "faceUVIds": [int(value) for value in list(assignment[1])],
                }
            )
        connected = _required_call(fn, "getConnectedShaders", 0)
        shader_objects = list(connected[0]) if len(connected) > 0 else []
        face_material_indices = [int(value) for value in list(connected[1])] if len(connected) > 1 else []
        for slot, shading_engine_object in enumerate(shader_objects):
            dep_fn = getattr(om, "MFnDependencyNode", None)
            if dep_fn is None:
                raise RuntimeError("required Maya API is unavailable: MFnDependencyNode")
            try:
                shading_engine_name = dep_fn(shading_engine_object).name()
            except Exception as exc:
                raise RuntimeError("failed to resolve shadingEngine returned by getConnectedShaders") from exc
            shading_engine_name = _long_name(cmds, str(shading_engine_name))
            shader_names = _call(cmds, "listConnections", f"{shading_engine_name}.surfaceShader", source=True, destination=False, default=[]) or []
            if not shader_names:
                raise RuntimeError(f"shadingEngine has no surfaceShader: {shading_engine_name}")
            shader_name = _long_name(cmds, str(shader_names[0]))
            materials.append(
                {
                    "slot": slot,
                    "shadingEngine": _strip_namespace(shading_engine_name),
                    "key": _semantic_id(cmds, shader_name, _node_type(cmds, shader_name), root) or _strip_namespace(shader_name),
                    "path": _strip_namespace(shader_name),
                    "attrs": _attrs(cmds, shader_name, _MATERIAL_ATTRS),
                    "textures": _textures(cmds, root, shader_name),
                    "connections": _connections(cmds, root, shader_name),
                }
            )
    vertex_count = len(points)
    if fn is not None:
        vertex_count = int(getattr(fn, "numVertices", vertex_count) or vertex_count)
    if not face_corners:
        vertex_count = int(_required_call(cmds, "polyEvaluate", mesh, vertex=True) or vertex_count)
    additional = {
        "transform": _attrs(cmds, transform_node, ("mmd_additional_uvs_json", "mmd_pmx_additional_uv_count")),
        "shape": _attrs(cmds, mesh, ("mmd_additional_uvs_json", "mmd_pmx_additional_uv_count")),
    }
    intermediate = _read_attr(cmds, mesh, "intermediateObject")
    return {
        "key": f"{transform_path}#mesh[{shape_index}]",
        "transformPath": transform_path,
        "shapeIndex": shape_index,
        "shapeName": _leaf(_strip_namespace(mesh)),
        "intermediate": bool(intermediate) if intermediate is not _MISSING else False,
        "vertices": vertex_count,
        "points": points,
        "faceCorners": face_corners,
        "faceVertexNormals": face_normals,
        "uvSets": uv_sets,
        "additionalUvMetadata": additional,
        "faceMaterialIndices": face_material_indices,
        "materials": sorted(materials, key=lambda item: (str(item["key"]), int(item["slot"]))),
    }


def _history(cmds: Any, mesh: str) -> list[str]:
    if getattr(cmds, "listHistory", None) is None:
        raise RuntimeError("required Maya API is unavailable: listHistory")
    # Maya returns None for a valid mesh with no construction history.  An
    # exception still propagates so a failed history query cannot look empty.
    return [str(value) for value in (cmds.listHistory(mesh, pruneDagObjects=True) or [])]


def _skin_weights(cmds: Any, om: Any, cluster: str, mesh: str, influences: list[str], vertex_count: int) -> list[dict[str, Any]]:
    # OpenMayaAnim is required: a skinPercent fallback can silently reorder or
    # omit influences and therefore cannot prove parity.
    weights: list[float] = []
    from maya.api import OpenMayaAnim as oma  # type: ignore

    selection = _required_call(om, "MSelectionList")
    selection.add(cluster)
    skin = oma.MFnSkinCluster(selection.getDependNode(0))
    mesh_selection = _required_call(om, "MSelectionList")
    mesh_selection.add(mesh)
    dag = mesh_selection.getDagPath(0)
    component_fn = _required_call(om, "MFnSingleIndexedComponent")
    component = component_fn.create(om.MFn.kMeshVertComponent)
    component_fn.addElements(list(range(vertex_count)))
    raw, influence_count = skin.getWeights(dag, component)
    if int(influence_count) != len(influences):
        raise RuntimeError(f"skin influence count changed while reading {cluster}: {influence_count} != {len(influences)}")
    weights = [float(value) for value in raw]
    expected = vertex_count * len(influences)
    if len(weights) != expected:
        raise RuntimeError(f"skin weight array is incomplete for {cluster}: {len(weights)} != {expected}")
    influence_count = len(influences)
    result = []
    for vertex_id in range(vertex_count):
        start = vertex_id * influence_count
        row = []
        for influence_id, influence in enumerate(influences):
            value = weights[start + influence_id]
            if value == 0.0:
                continue
            bone_key = _semantic_id(cmds, influence, _node_type(cmds, influence)) or _strip_namespace(influence)
            row.append({"bone": bone_key, "value": value})
        result.append({"vertex": vertex_id, "weights": row})
    return result


def _skin_signature(cmds: Any, om: Any, root: str, mesh: str, cluster: str, vertex_count: int) -> dict[str, Any]:
    from maya.api import OpenMayaAnim as oma  # type: ignore

    selection = _required_call(om, "MSelectionList")
    selection.add(cluster)
    skin = oma.MFnSkinCluster(selection.getDependNode(0))
    influence_paths = list(skin.influenceObjects())
    if not influence_paths:
        raise RuntimeError(f"skinCluster has no influences: {cluster}")
    influences = [path.fullPathName() for path in influence_paths]
    influence_rows = []
    for index, influence in enumerate(influences):
        logical_index = int(skin.indexForInfluenceObject(influence_paths[index]))
        influence_rows.append(
            {
                "index": index,
                "key": _semantic_id(cmds, influence, _node_type(cmds, influence)) or _strip_namespace(influence),
                "path": _relative_path(root, _long_name(cmds, influence)),
                "logicalIndex": logical_index,
                "bindPreMatrix": _json_value(_required_call(cmds, "getAttr", f"{cluster}.bindPreMatrix[{logical_index}]")),
            }
        )
    return {
        "key": _strip_namespace(cluster),
        "influences": influence_rows,
        "weights": _skin_weights(cmds, om, cluster, mesh, influences, vertex_count),
        "weightsFormat": "sparse-nonzero-v1",
        "weightVertexCount": vertex_count,
        "weightInfluenceCount": len(influences),
        "absentInfluenceMeansZero": True,
        "attrs": _attrs(cmds, cluster, _SKIN_ATTRS),
        "connections": _connections(cmds, root, cluster),
    }


def _morph_signature(cmds: Any, root: str, node: str, evaluated_meshes: list[dict[str, Any]]) -> dict[str, Any]:
    node_type = _node_type(cmds, node)
    aliases = _call(cmds, "aliasAttr", node, query=True, default=[]) or []
    alias_rows = []
    for index in range(0, len(aliases) - 1, 2):
        alias_rows.append({"alias": str(aliases[index]), "plug": str(aliases[index + 1]), "value": _json_value(_call(cmds, "getAttr", f"{node}.{aliases[index + 1]}", default=None))})
    output_paths = []
    for value in _call(cmds, "listConnections", node, source=False, destination=True, type="mesh", default=[]) or []:
        output_paths.append(_strip_namespace(_long_name(cmds, str(value))))
    outputs = []
    for output_path in output_paths:
        output_transform = output_path.rsplit("|", 1)[0]
        matched = next(
            (
                mesh["key"]
                for mesh in evaluated_meshes
                if output_transform.endswith("|" + str(mesh.get("transformPath", "")).lstrip("|"))
            ),
            output_path,
        )
        outputs.append(matched)
    return {
        "key": _semantic_id(cmds, node, node_type, root) or _strip_namespace(node),
        "path": _strip_namespace(_long_name(cmds, node)),
        "type": node_type,
        "attrs": _attrs(cmds, node, _MORPH_ATTRS),
        "aliases": alias_rows,
        "outputs": sorted(outputs),
        "drivers": _connections(cmds, root, node),
        # This is the current evaluated state.  It makes metadata-only
        # equality insufficient, while keeping the full trajectory a feature
        # test concern rather than pretending one pose proves all morphs.
        "evaluated": [
            {"mesh": mesh["key"], "vertexCount": len(mesh.get("points", []))}
            for mesh in evaluated_meshes
            if mesh.get("key") in outputs or mesh.get("transformPath") in outputs
        ],
    }


def _physics_signature(cmds: Any, root: str, node: str) -> dict[str, Any]:
    node_type = _node_type(cmds, node)
    attrs = _attrs(cmds, node, _PHYSICS_ATTRS)
    return {
        "key": _semantic_id(cmds, node, node_type, root) or _strip_namespace(_long_name(cmds, node)),
        "path": _strip_namespace(_long_name(cmds, node)),
        "type": node_type,
        "attrs": attrs,
        "connections": _connections(cmds, root, node),
    }


def _is_morph_node(cmds: Any, node: str) -> bool:
    node_type = _node_type(cmds, node)
    if node_type in _MORPH_TYPES:
        return True
    morph_type = _read_attr(cmds, node, "mmd_morph_type")
    return isinstance(morph_type, str) and morph_type in _MORPH_KINDS


def _is_proxy_node(cmds: Any, node: str) -> bool:
    return any(_attr_exists(cmds, node, marker) for marker in _PROXY_MARKERS)


def _registry_owned_nodes(cmds: Any, seeds: Sequence[str], depth: int = 3) -> set[str]:
    """Follow only MMD-owned DG edges; do not enumerate the whole scene graph."""

    owned: set[str] = set()
    frontier = {str(seed) for seed in seeds}
    for _ in range(depth):
        next_frontier: set[str] = set()
        for node in frontier:
            for connected in _call(cmds, "listConnections", node, source=True, destination=True, default=[]) or []:
                connected = str(connected)
                node_type = _node_type(cmds, connected)
                if (
                    node_type.startswith("mmd")
                    or node_type in _MORPH_TYPES
                    or node_type == "skinCluster"
                    or _user_attrs(cmds, connected)
                ):
                    if connected not in owned:
                        owned.add(connected)
                        next_frontier.add(connected)
        frontier = next_frontier
        if not frontier:
            break
    return owned


def capture_scene(cmds: Any, om: Any, root: str) -> dict[str, Any]:
    """Capture a JSON-serializable semantic snapshot rooted at *root*.

    The caller owns scene setup and evaluation state.  The returned
    ``evaluation`` section therefore describes the current Maya evaluation
    only; it is deliberately not a claim of all animation or physics frames.
    """

    canonical_root = _long_name(cmds, str(root))
    descendants = [str(value) for value in (_call(cmds, "listRelatives", canonical_root, allDescendents=True, fullPath=True, default=[]) or [])]
    dag_nodes = [canonical_root, *descendants]
    rows: list[dict[str, Any]] = []
    meshes: list[dict[str, Any]] = []
    histories: set[str] = set()
    attrs_cache: dict[str, dict[str, Any]] = {}
    normalization_ledger: list[dict[str, str]] = []

    def capture_attrs(node: str) -> dict[str, Any]:
        long_node = _long_name(cmds, node)
        if long_node not in attrs_cache:
            attrs = _attrs(cmds, long_node)
            attrs, entries = _normalize_vmd_authoring_attrs(cmds, canonical_root, long_node, attrs)
            attrs_cache[long_node] = attrs
            normalization_ledger.extend(entries)
        return attrs_cache[long_node]

    for node in dag_nodes:
        node_type = _node_type(cmds, node)
        path = _relative_path(canonical_root, _long_name(cmds, node))
        row = {
            "key": path,
            "path": path,
            "sourcePath": _long_name(cmds, node),
            "type": node_type,
            "name": _leaf(path),
            "semanticId": _semantic_id(cmds, node, node_type, canonical_root),
            "attrs": capture_attrs(node),
            "connections": _connections(cmds, canonical_root, node),
        }
        visibility = _read_attr(cmds, node, "visibility")
        if visibility is not _MISSING:
            row["visibility"] = bool(visibility)
        rows.append(row)
        if node_type == "mesh":
            parents = _call(cmds, "listRelatives", node, parent=True, fullPath=True, default=[]) or []
            transform = _relative_path(canonical_root, _long_name(cmds, str(parents[0]))) if parents else path
            shape_index = _mesh_role_index(cmds, str(parents[0]), node) if parents else 0
            mesh = _mesh_geometry(cmds, om, canonical_root, node, str(parents[0]) if parents else node, transform, shape_index)
            mesh["skin"] = []
            history = _history(cmds, node)
            histories.update(history)
            for history_node in history:
                if _node_type(cmds, history_node) == "skinCluster":
                    mesh["skin"].append(_skin_signature(cmds, om, canonical_root, node, history_node, mesh["vertices"]))
            meshes.append(mesh)
    # Connected non-DAG nodes (materials, blendShapes, physics solvers) are
    # included through history or explicit root connections, but never by
    # enumerating unrelated nodes in the user's scene.
    for node in _call(cmds, "listConnections", canonical_root, source=True, destination=True, default=[]) or []:
        histories.add(str(node))
    registry_nodes = _registry_owned_nodes(cmds, [canonical_root, *dag_nodes, *sorted(histories)])
    histories.update(registry_nodes)
    implementation_nodes = [row for row in rows if row["type"] == "mmdRenderShape"]
    proxy_nodes = [
        row
        for row in rows
        if _is_proxy_node(cmds, row["sourcePath"])
    ]
    for node in sorted(histories):
        if not _is_proxy_node(cmds, node):
            continue
        long_node = _long_name(cmds, node)
        if any(row["sourcePath"] == long_node for row in proxy_nodes):
            continue
        path = _strip_namespace(long_node)
        proxy_nodes.append(
            {
                "key": _semantic_id(cmds, node, node_type, canonical_root) or path,
                "path": path,
                "sourcePath": long_node,
                "type": node_type,
                "name": _leaf(path),
                "attrs": capture_attrs(node),
                "connections": _connections(cmds, canonical_root, node),
            }
        )
    candidate_nodes = histories | set(dag_nodes)
    morph_nodes = [node for node in candidate_nodes if _is_morph_node(cmds, node)]
    physics_nodes = [node for node in candidate_nodes if _node_type(cmds, node) in _PHYSICS_TYPES]
    evaluated = [
        {"key": mesh["key"], "transformPath": mesh["transformPath"], "points": mesh.get("points", [])}
        for mesh in meshes
    ]
    morphs = sorted((_morph_signature(cmds, canonical_root, node, evaluated) for node in morph_nodes), key=lambda item: str(item["key"]))
    physics = sorted((_physics_signature(cmds, canonical_root, node) for node in physics_nodes), key=lambda item: str(item["key"]))
    registry_rows = sorted(
        (
            {
                "key": _semantic_id(cmds, node, _node_type(cmds, node), canonical_root) or _strip_namespace(_long_name(cmds, node)),
                "path": _strip_namespace(_long_name(cmds, node)),
                "type": _node_type(cmds, node),
                "attrs": capture_attrs(node),
                "connections": _connections(cmds, canonical_root, node),
            }
            for node in registry_nodes
            if node not in dag_nodes
        ),
        key=lambda item: str(item["key"]),
    )
    bones = sorted(
        (
            {
                "key": row["semanticId"] or row["path"],
                "path": row["path"],
                "name": row["name"],
                "attrs": row["attrs"],
                "connections": row["connections"],
            }
            for row in rows
            if row["type"] == "joint"
        ),
        key=lambda item: str(item["key"]),
    )
    semantic_rows = [
        row
        for row in rows
        if row["type"] not in {"mmdRenderShape", "mesh"} and not _is_proxy_node(cmds, row["sourcePath"])
    ]
    implementation_links = [
        link
        for row in implementation_nodes
        for link in row["connections"]
    ]
    proxy_links = [
        link
        for row in proxy_nodes
        for link in row["connections"]
    ]
    implementation_paths = {str(row["path"]) for row in implementation_nodes}
    root_attrs = _attrs(cmds, canonical_root)
    normalization_ledger.sort(key=lambda item: (item["node"], item["field"], item["raw"], item["semantic"]))
    return {
        "schemaVersion": SCHEMA_VERSION,
        "captureErrors": [],
        "root": {"path": "<root>", "sourcePath": canonical_root, "name": _leaf(_strip_namespace(canonical_root)), "attrs": root_attrs},
        "dag": sorted(rows, key=lambda item: str(item["path"])),
        "semanticDag": sorted(semantic_rows, key=lambda item: str(item["path"])),
        "implementationNodes": sorted(implementation_nodes, key=lambda item: str(item["path"])),
        "implementationLinks": sorted(implementation_links, key=lambda item: (item["src"], item["dst"])),
        "meshes": sorted(meshes, key=lambda item: str(item["key"])),
        "morphs": morphs,
        "bones": bones,
        "physics": physics,
        "registryNodes": registry_rows,
        "proxies": {"count": len(proxy_nodes), "nodes": sorted(proxy_nodes, key=lambda item: str(item["key"])), "links": sorted(proxy_links, key=lambda item: (item["src"], item["dst"]))},
        "connections": sorted(
            (
                link
                for row in semantic_rows
                for link in row["connections"]
                if not _touches_nodes(link, implementation_paths)
            ),
            key=lambda item: (item["src"], item["dst"]),
        ),
        "evaluation": {"meshes": evaluated},
        "normalizationLedger": {"vmdAuthoringUuid": normalization_ledger},
        "unsupported": ["morph trajectories beyond the captured current evaluation", "physics trajectories across playback frames"],
        "rootMmdAttrs": root_attrs,
        "contract": {"schemaVersion": SCHEMA_VERSION, "tolerances": dict(TOLERANCES), "allowedDifferences": list(ALLOWED_DIFFERENCES), "exclusions": list(EXCLUSIONS)},
    }


def _path_context(path: str) -> bool:
    leaf = path.rsplit(".", 1)[-1]
    return leaf in _PATH_KEYS or leaf.endswith("Path") or leaf in {"a", "b"}


def _canonical_compare(value: Any, path: str = "") -> Any:
    if isinstance(value, str) and _path_context(path):
        return _strip_namespace(value)
    if isinstance(value, Mapping):
        return {str(key): _canonical_compare(item, f"{path}.{key}" if path else str(key)) for key, item in value.items()}
    if isinstance(value, list):
        return [_canonical_compare(item, f"{path}[]") for item in value]
    return value


def _nonfinite(value: Any, path: str = "") -> list[str]:
    if isinstance(value, float) and not math.isfinite(value):
        return [path or "<root>"]
    if isinstance(value, Mapping):
        result = []
        for key, item in value.items():
            result.extend(_nonfinite(item, f"{path}.{key}" if path else str(key)))
        return result
    if isinstance(value, (list, tuple)):
        result = []
        for index, item in enumerate(value):
            result.extend(_nonfinite(item, f"{path}[{index}]"))
        return result
    return []


def _tolerance(path: str) -> float:
    lowered = path.lower()
    for name in ("normal", "weight", "uv", "matrix", "position"):
        if name in lowered:
            return TOLERANCES[name]
    return TOLERANCES["attribute"]


def _equal(left: Any, right: Any, path: str = "") -> bool:
    left = _canonical_compare(left, path)
    right = _canonical_compare(right, path)
    if isinstance(left, (int, float)) and isinstance(right, (int, float)) and not isinstance(left, bool) and not isinstance(right, bool):
        return math.isfinite(float(left)) and math.isfinite(float(right)) and abs(float(left) - float(right)) <= _tolerance(path)
    if type(left) is not type(right):
        return False
    if isinstance(left, Mapping):
        return set(left) == set(right) and all(_equal(left[key], right[key], f"{path}.{key}" if path else str(key)) for key in left)
    if isinstance(left, (list, tuple)):
        return len(left) == len(right) and all(_equal(a, b, f"{path}[{index}]") for index, (a, b) in enumerate(zip(left, right)))
    return left == right


def _rows(rows: Any, key_name: str = "key") -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for row in rows or []:
        if not isinstance(row, Mapping):
            continue
        key = row.get(key_name)
        if key is None:
            key = row.get("path")
        if key is not None:
            result[str(_canonical_compare(str(key), key_name))] = row
    return result


def _mesh_rows(snapshot: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    result = {}
    for row in snapshot.get("meshes", []) or []:
        if not isinstance(row, Mapping):
            continue
        key = row.get("key") or row.get("transformPath") or row.get("path")
        if key is None:
            continue
        result[str(_strip_namespace(str(key)))] = row
    return result


def _snapshot_errors(snapshot: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if snapshot.get("captureErrors"):
        errors.extend(str(error) for error in snapshot["captureErrors"])
    meshes = snapshot.get("meshes")
    if not isinstance(meshes, list) or not meshes:
        errors.append("meshes must be a non-empty list")
        return errors
    keys: set[str] = set()
    required = {"key", "vertices", "points", "faceCorners", "faceVertexNormals", "uvSets", "additionalUvMetadata", "faceMaterialIndices", "materials", "skin"}
    for index, mesh in enumerate(meshes):
        if not isinstance(mesh, Mapping):
            errors.append(f"meshes[{index}] is not an object")
            continue
        missing = sorted(required - set(mesh))
        if missing:
            errors.append(f"meshes[{index}] missing required fields: {', '.join(missing)}")
        key = str(mesh.get("key", ""))
        if not key:
            errors.append(f"meshes[{index}] has no semantic key")
        elif key in keys:
            errors.append(f"duplicate mesh semantic key: {key}")
        keys.add(key)
        if isinstance(mesh.get("vertices"), int) and mesh["vertices"] > 0 and not mesh.get("points"):
            errors.append(f"meshes[{index}] has vertices but no points")
        if isinstance(mesh.get("vertices"), int) and mesh["vertices"] <= 0:
            errors.append(f"meshes[{index}] has no vertices")
        corner_count = sum(len(face.get("corners", [])) for face in mesh.get("faceCorners", []) if isinstance(face, Mapping))
        if corner_count != len(mesh.get("faceVertexNormals", [])):
            errors.append(f"meshes[{index}] face-vertex normal count is incomplete")
        if len(mesh.get("faceMaterialIndices", [])) != len(mesh.get("faceCorners", [])):
            errors.append(f"meshes[{index}] face-material assignment count is incomplete")
        for uv_index, uv_set in enumerate(mesh.get("uvSets", [])):
            if len(uv_set.get("faceCounts", [])) != len(mesh.get("faceCorners", [])):
                errors.append(f"meshes[{index}].uvSets[{uv_index}] face assignment count is incomplete")
            if sum(uv_set.get("faceCounts", [])) != len(uv_set.get("faceUVIds", [])):
                errors.append(f"meshes[{index}].uvSets[{uv_index}] UV assignment array is incomplete")
            uv_ids = uv_set.get("faceUVIds", [])
            uv_values = uv_set.get("values", [])
            if uv_ids and (not uv_values or max(uv_ids) >= len(uv_values)):
                errors.append(f"meshes[{index}].uvSets[{uv_index}] references a missing UV value")
        skin_rows = mesh.get("skin", [])
        if not isinstance(skin_rows, list):
            errors.append(f"meshes[{index}].skin must be a list")
        else:
            for skin_index, skin in enumerate(skin_rows):
                if not isinstance(skin, Mapping):
                    errors.append(f"meshes[{index}].skin[{skin_index}] is not an object")
                    continue
                if skin.get("weightsFormat") != "sparse-nonzero-v1":
                    errors.append(f"meshes[{index}].skin[{skin_index}] has unsupported weights format")
                if skin.get("absentInfluenceMeansZero") is not True:
                    errors.append(f"meshes[{index}].skin[{skin_index}] does not declare absent weights as zero")
                influences = skin.get("influences", [])
                weight_rows = skin.get("weights", [])
                if skin.get("weightVertexCount") != mesh.get("vertices"):
                    errors.append(f"meshes[{index}].skin[{skin_index}] vertex count is incomplete")
                if not isinstance(influences, list) or skin.get("weightInfluenceCount") != len(influences):
                    errors.append(f"meshes[{index}].skin[{skin_index}] influence count is incomplete")
                if not isinstance(weight_rows, list) or len(weight_rows) != mesh.get("vertices"):
                    errors.append(f"meshes[{index}].skin[{skin_index}] weight rows are incomplete")
                    continue
                influence_keys = {str(row.get("key")) for row in influences if isinstance(row, Mapping)}
                seen_vertices: set[int] = set()
                for row in weight_rows:
                    if not isinstance(row, Mapping) or not isinstance(row.get("vertex"), int):
                        errors.append(f"meshes[{index}].skin[{skin_index}] has an invalid vertex row")
                        continue
                    vertex_id = int(row["vertex"])
                    if vertex_id in seen_vertices or vertex_id < 0 or vertex_id >= int(mesh.get("vertices", 0)):
                        errors.append(f"meshes[{index}].skin[{skin_index}] has duplicate or invalid vertex rows")
                    seen_vertices.add(vertex_id)
                    for weight in row.get("weights", []) if isinstance(row.get("weights", []), list) else []:
                        if not isinstance(weight, Mapping) or str(weight.get("bone")) not in influence_keys:
                            errors.append(f"meshes[{index}].skin[{skin_index}] references an unknown influence")
                        elif weight.get("value") == 0.0:
                            errors.append(f"meshes[{index}].skin[{skin_index}] stores an explicit zero weight")
    for section in ("semanticDag", "bones", "morphs", "physics", "registryNodes"):
        rows = snapshot.get(section, [])
        if not isinstance(rows, list):
            errors.append(f"{section} must be a list")
            continue
        seen: set[str] = set()
        for index, row in enumerate(rows):
            if not isinstance(row, Mapping):
                errors.append(f"{section}[{index}] is not an object")
                continue
            key = row.get("key") or row.get("path")
            if key is None:
                errors.append(f"{section}[{index}] has no semantic key")
                continue
            normalized = str(_canonical_compare(str(key), "key"))
            if normalized in seen:
                errors.append(f"duplicate {section} semantic key: {normalized}")
            seen.add(normalized)
    implementation_nodes = snapshot.get("implementationNodes", [])
    if not isinstance(implementation_nodes, list):
        errors.append("implementationNodes must be a list")
    else:
        for index, row in enumerate(implementation_nodes):
            if not isinstance(row, Mapping):
                errors.append(f"implementationNodes[{index}] is not an object")
            elif row.get("type") != "mmdRenderShape":
                errors.append(f"implementationNodes[{index}] is not an mmdRenderShape")
    proxies = snapshot.get("proxies", {})
    if isinstance(proxies, Mapping):
        proxy_nodes = proxies.get("nodes", [])
        if not isinstance(proxy_nodes, list):
            errors.append("proxies.nodes must be a list")
        else:
            seen_proxy_keys: set[str] = set()
            for index, row in enumerate(proxy_nodes):
                if not isinstance(row, Mapping):
                    errors.append(f"proxies.nodes[{index}] is not an object")
                    continue
                key = row.get("key") or row.get("path")
                if key is None:
                    errors.append(f"proxies.nodes[{index}] has no semantic key")
                    continue
                normalized = str(_canonical_compare(str(key), "key"))
                if normalized in seen_proxy_keys:
                    errors.append(f"duplicate proxies semantic key: {normalized}")
                seen_proxy_keys.add(normalized)
    return errors


def _summary(value: Any, limit: int = 8) -> Any:
    """Keep reports reviewable without duplicating full mesh arrays."""

    if isinstance(value, Mapping):
        return {str(key): _summary(item, limit) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        if len(value) <= limit:
            return [_summary(item, limit) for item in value]
        return {"length": len(value), "head": [_summary(item, limit) for item in value[:limit]], "truncated": True}
    return value


def _touches_nodes(link: Mapping[str, Any], paths: set[str]) -> bool:
    for endpoint in (str(link.get("src", "")), str(link.get("dst", ""))):
        node_path = endpoint.split(".", 1)[0]
        if node_path in paths or any(node_path.startswith(path + "|") for path in paths):
            return True
    return False


def _without_implementation_links(value: Any, paths: set[str]) -> Any:
    """Apply the declared native-node allowance to duplicated nested links too."""
    if isinstance(value, Mapping):
        return {key: _without_implementation_links(item, paths) for key, item in value.items()}
    if isinstance(value, list):
        return [
            _without_implementation_links(item, paths)
            for item in value
            if not (isinstance(item, Mapping) and "src" in item and "dst" in item
                    and _touches_nodes(item, paths))
        ]
    return value


def _check(checks: list[dict[str, Any]], name: str, passed: bool, **details: Any) -> None:
    checks.append({"name": name, "pass": bool(passed), **{key: _summary(value) for key, value in details.items()}})


def compare_scenes(left: Mapping[str, Any], right: Mapping[str, Any]) -> dict[str, Any]:
    """Compare two :func:`capture_scene` snapshots and return an audit report."""

    checks: list[dict[str, Any]] = []
    left = left if isinstance(left, Mapping) else {}
    right = right if isinstance(right, Mapping) else {}
    left_nonfinite = _nonfinite(left)
    right_nonfinite = _nonfinite(right)
    _check(checks, "finite-values", not left_nonfinite and not right_nonfinite, left=left_nonfinite[:20], right=right_nonfinite[:20])
    left_structure = _snapshot_errors(left)
    right_structure = _snapshot_errors(right)
    _check(checks, "snapshot-structure", not left_structure and not right_structure, left=left_structure, right=right_structure)
    left_empty = not left or not left.get("meshes")
    right_empty = not right or not right.get("meshes")
    _check(checks, "non-empty-scene", not left_empty and not right_empty, leftEmpty=left_empty, rightEmpty=right_empty)
    _check(checks, "schema-version", left.get("schemaVersion", SCHEMA_VERSION) == right.get("schemaVersion", SCHEMA_VERSION), left=left.get("schemaVersion"), right=right.get("schemaVersion"))
    if left_empty or right_empty or left_nonfinite or right_nonfinite or left_structure or right_structure:
        return {
            "status": "fail",
            "checks": checks,
            "allowedDifferences": list(ALLOWED_DIFFERENCES),
            "exclusions": list(EXCLUSIONS),
            "tolerances": dict(TOLERANCES),
        }

    # Native render connections also occur in material/morph/registry rows.
    # Keep their full original ledger, but apply the same allowance everywhere
    # they are duplicated inside the semantic contract.
    left, right = dict(left), dict(right)
    for snapshot in (left, right):
        paths = {str(row.get("path", row.get("key", ""))) for row in snapshot.get("implementationNodes", [])}
        for section in ("semanticDag", "meshes", "morphs", "bones", "physics", "registryNodes", "proxies", "connections"):
            if section in snapshot:
                snapshot[section] = _without_implementation_links(snapshot[section], paths)

    _check(checks, "root-contract", _equal(left.get("root", {}), right.get("root", {}), "root"), left=left.get("root", {}), right=right.get("root", {}))

    left_meshes = _mesh_rows(left)
    right_meshes = _mesh_rows(right)
    _check(checks, "mesh-keys", set(left_meshes) == set(right_meshes), left=sorted(left_meshes), right=sorted(right_meshes))
    shape_name_differences = []
    for key in sorted(set(left_meshes) & set(right_meshes)):
        lmesh, rmesh = left_meshes[key], right_meshes[key]
        for field in ("vertices", "intermediate", "faceCorners", "faceVertexNormals", "uvSets", "additionalUvMetadata", "faceMaterialIndices", "materials", "skin"):
            _check(checks, f"mesh.{field}[{key}]", _equal(lmesh.get(field), rmesh.get(field), f"meshes.{field}"), left=lmesh.get(field), right=rmesh.get(field))
        if lmesh.get("shapeName") != rmesh.get("shapeName"):
            shape_name_differences.append(key)
        _check(checks, f"mesh.points[{key}]", _equal(lmesh.get("points"), rmesh.get("points"), "mesh.position"), left=lmesh.get("points"), right=rmesh.get("points"))
    _check(checks, "auto-shape-name-allowed", True, allowed=True, differences=shape_name_differences, ledgerId="auto-shape-name")

    for section, label in (("semanticDag", "dag"), ("rootMmdAttrs", "root-metadata"), ("bones", "bones"), ("morphs", "morph-metadata"), ("physics", "physics"), ("registryNodes", "registry-nodes"), ("connections", "connections")):
        left_value = _canonical_compare(left.get(section, []), section)
        right_value = _canonical_compare(right.get(section, []), section)
        _check(checks, label, _equal(left_value, right_value, section), left=left_value, right=right_value)

    left_proxies = left.get("proxies", {}) if isinstance(left.get("proxies", {}), Mapping) else {}
    right_proxies = right.get("proxies", {}) if isinstance(right.get("proxies", {}), Mapping) else {}
    _check(checks, "proxy-count", left_proxies.get("count", len(left_proxies.get("nodes", []) or [])) == right_proxies.get("count", len(right_proxies.get("nodes", []) or [])), left=left_proxies.get("count"), right=right_proxies.get("count"))
    _check(checks, "proxy-links", _equal(left_proxies.get("links", []), right_proxies.get("links", []), "proxy.links"), left=left_proxies.get("links", []), right=right_proxies.get("links", []))
    left_proxy_rows, right_proxy_rows = _rows(left_proxies.get("nodes", [])), _rows(right_proxies.get("nodes", []))
    proxy_name_differences = [key for key in sorted(set(left_proxy_rows) & set(right_proxy_rows)) if left_proxy_rows[key].get("name") != right_proxy_rows[key].get("name")]
    _check(checks, "proxy-node-contract", _equal([{key: value for key, value in row.items() if key not in {"name", "sourcePath"}} for row in left_proxy_rows.values()], [{key: value for key, value in row.items() if key not in {"name", "sourcePath"}} for row in right_proxy_rows.values()], "proxies.nodes"), left=left_proxy_rows, right=right_proxy_rows)
    _check(checks, "proxy-node-name-allowed", True, allowed=True, differences=proxy_name_differences, ledgerId="proxy-node-name")

    implementation_left = left.get("implementationNodes", []) or []
    implementation_right = right.get("implementationNodes", []) or []
    implementation_links_left = left.get("implementationLinks", []) or []
    implementation_links_right = right.get("implementationLinks", []) or []
    implementation_different = not _equal(implementation_left, implementation_right, "implementationNodes")
    _check(
        checks,
        "mmdRenderShape-implementation-allowed",
        True,
        allowed=True,
        differences=implementation_different or not _equal(implementation_links_left, implementation_links_right, "implementationLinks"),
        ledgerId="mmdRenderShape-implementation",
        leftNodes=implementation_left,
        rightNodes=implementation_right,
        leftLinks=implementation_links_left,
        rightLinks=implementation_links_right,
    )

    morphs = left.get("morphs", []) or right.get("morphs", [])
    if morphs:
        left_eval = left.get("evaluation")
        right_eval = right.get("evaluation")
        _check(checks, "morph-evaluated-state-required", bool(left_eval and right_eval), left=bool(left_eval), right=bool(right_eval))
        if left_eval and right_eval:
            _check(checks, "morph-evaluated-state", _equal(left_eval, right_eval, "morph.evaluation"), left=left_eval, right=right_eval)

    status = "pass" if all(check["pass"] for check in checks) else "fail"
    return {"status": status, "checks": checks, "allowedDifferences": list(ALLOWED_DIFFERENCES), "exclusions": list(EXCLUSIONS), "tolerances": dict(TOLERANCES)}


__all__ = ["capture_scene", "compare_scenes", "SCHEMA_VERSION", "TOLERANCES", "ALLOWED_DIFFERENCES", "EXCLUSIONS"]
