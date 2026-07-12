"""mmd-anim FFI の buffer-based API を使って PMX を高速パースするモジュール。

Python の struct.unpack ベースのパーサー (PmxData.parse_file) を置き換え、
Rust ネイティブパーサーで 5-10x の高速化を実現する。

DLL が利用不可能な場合や解析に失敗した場合は None を返す。PMX import では
呼び出し元が native 必須モードとして扱い、Python parser fallback は移行用途の明示 opt-out 時だけ許可する。
"""

from __future__ import annotations

import ctypes
import json
import tempfile
from ctypes import POINTER, c_float, c_size_t, c_uint8, c_uint32, c_void_p
from pathlib import Path
from typing import Any, Dict, List, Optional

from mmd_tools.core.logger import get_logger
from mmd_tools.core.native.mmd_anim_runtime_types import MmdRuntimeFfiByteBuffer as _ByteBuffer
from mmd_tools.core.pmx_data import PmxData
from mmd_tools.core.pmx_data.bone import PmxBone, PmxBoneFlag
from mmd_tools.core.pmx_data.display_frame import PmxDisplayFrame
from mmd_tools.core.pmx_data.face import PmxFace
from mmd_tools.core.pmx_data.header import PmxEncoding, PmxHeader, is_pmx_21_or_later
from mmd_tools.core.pmx_data.ik_link import PmxIKLink
from mmd_tools.core.pmx_data.joint import PmxJoint
from mmd_tools.core.pmx_data.material import (
    PmxDrawFlag,
    PmxMaterial,
    PmxSharedToonFlag,
    PmxSphereMode,
)
from mmd_tools.core.pmx_data.morph import PmxMorph, PmxMorphType
from mmd_tools.core.pmx_data.rigid_body import PmxRigidBody
from mmd_tools.core.pmx_data.vertex import PmxVertex

logger = get_logger(__name__)



# _ByteBuffer is shared with mmd_anim_runtime to avoid dual ctypes Structure
# definitions conflicting on argtypes.


_SKINNING_MODE_MAP = {
    "bdef1": 0,
    "bdef2": 1,
    "bdef4": 2,
    "sdef": 3,
    "qdef": 4,
}

_SPHERE_MODE_MAP = {
    "none": PmxSphereMode.DISABLED,
    "disabled": PmxSphereMode.DISABLED,
    "multiply": PmxSphereMode.MULTIPLY,
    "additive": PmxSphereMode.ADDITIVE,
    "sub_texture": PmxSphereMode.SUB_TEXTURE,
    "subTexture": PmxSphereMode.SUB_TEXTURE,
}

_MORPH_TYPE_MAP = {
    "group": PmxMorphType.GroupMorph,
    "vertex": PmxMorphType.VertexMorph,
    "bone": PmxMorphType.BoneMorph,
    "uv": PmxMorphType.UVMorph,
    "additionalUv1": PmxMorphType.AdditionalUVMorph1,
    "additionalUv2": PmxMorphType.AdditionalUVMorph2,
    "additionalUv3": PmxMorphType.AdditionalUVMorph3,
    "additionalUv4": PmxMorphType.AdditionalUVMorph4,
    "material": PmxMorphType.MaterialMorph,
    "flip": PmxMorphType.FlipMorph,
    "impulse": PmxMorphType.ImpulseMorph,
}

_MATERIAL_MORPH_OPERATION_MAP = {
    "multiply": 0,
    "add": 1,
}


def _get_any(data: dict, *keys: str, default=None):
    """Return the first present value from *data* for compatible JSON field aliases."""
    for key in keys:
        if key in data:
            return data[key]
    return default


def is_native_parser_available() -> bool:
    """buffer-based PMX パーサーが DLL で利用可能か返す。"""
    from .mmd_anim_runtime import get_mmd_runtime_library

    lib = get_mmd_runtime_library()
    if lib is None:
        return False
    return hasattr(lib, "mmd_runtime_parse_pmx_non_geometry_json")


def parse_pmx_native(file_path: str) -> Optional[PmxData]:
    """PMX ファイルをネイティブ DLL でパースし PmxData を返す。

    DLL 未対応またはパース失敗時は None を返す。
    """
    from .mmd_anim_runtime import get_mmd_runtime_library

    lib = get_mmd_runtime_library()
    if lib is None:
        return None
    if not hasattr(lib, "mmd_runtime_parse_pmx_non_geometry_json"):
        return None

    try:
        pmx_bytes = Path(file_path).read_bytes()
    except Exception as exc:
        logger.debug("Failed to read PMX file for native parse: %s", exc)
        return None

    try:
        pmx = _parse_pmx_bytes(lib, pmx_bytes)
        if pmx is not None and is_pmx_21_or_later(pmx.header.version):
            pmx.soft_body_loader = lambda pmx_bytes=pmx_bytes: _load_soft_bodies_from_legacy(
                pmx_bytes
            )
        return pmx
    except Exception as exc:
        logger.debug("Native PMX parse failed, will fallback: %s", exc)
        return None


def _load_soft_bodies_from_legacy(pmx_bytes: bytes) -> list:
    """Load PMX 2.1 soft bodies from captured source bytes at export time."""
    # mmd-anim native metadata does not expose PMX 2.1 soft bodies yet. Preserve
    # them through this hook until Rust metadata support can replace it.
    temp_path = None
    try:
        from mmd_tools.core.pmx_data.legacy_parser import parse_pmx_file_legacy

        with tempfile.NamedTemporaryFile(suffix=".pmx", delete=False) as temp_file:
            temp_file.write(pmx_bytes)
            temp_path = Path(temp_file.name)
        legacy_pmx = parse_pmx_file_legacy(str(temp_path))
        return list(getattr(legacy_pmx, "soft_bodies", []) or [])
    except Exception as exc:
        logger.warning("Failed to load PMX soft bodies from captured bytes: %s", exc)
        raise
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def _parse_pmx_bytes(lib: Any, pmx_bytes: bytes) -> Optional[PmxData]:
    """PMX バイト列をネイティブ API でパースし PmxData を構築する。"""
    _ensure_signatures(lib)

    buf_in = (c_uint8 * len(pmx_bytes)).from_buffer_copy(pmx_bytes)
    n = len(pmx_bytes)

    free_fn = lib.mmd_runtime_byte_buffer_free
    buffers: list[_ByteBuffer] = []

    try:
        def call(name: str) -> _ByteBuffer:
            return _call_buffer(lib, name, buf_in, n, buffers)

        json_buf = call("non_geometry_json")
        pos_buf = call("positions_buffer")
        norm_buf = call("normals_buffer")
        uv_buf = call("uvs_buffer")
        idx_buf = call("indices_buffer")
        skin_idx_buf = call("skin_indices_buffer")
        skin_wt_buf = call("skin_weights_buffer")
        mat_grp_buf = call("material_groups_buffer")
        edge_buf = call("edge_scale_buffer")
        sdef_c_buf = call("sdef_c_buffer")
        sdef_r0_buf = call("sdef_r0_buffer")
        sdef_r1_buf = call("sdef_r1_buffer")
        modes_buf = call("skinning_modes_json")

        metadata = _read_json(json_buf)
        if metadata is None:
            return None

        vert_count = pos_buf.len // 12  # float32 * 3
        idx_count = idx_buf.len // 4  # uint32

        pos = _as_float_array(pos_buf, vert_count * 3)
        norm = _as_float_array(norm_buf, vert_count * 3)
        uv = _as_float_array(uv_buf, vert_count * 2)
        idx = _as_uint32_array(idx_buf, idx_count)
        skin_i = _as_uint32_array(skin_idx_buf, vert_count * 4)
        skin_w = _as_float_array(skin_wt_buf, vert_count * 4)
        edge = _as_float_array(edge_buf, vert_count)
        sdef_c = _as_float_array(sdef_c_buf, vert_count * 3)
        sdef_r0 = _as_float_array(sdef_r0_buf, vert_count * 3)
        sdef_r1 = _as_float_array(sdef_r1_buf, vert_count * 3)
        additional_uvs = _read_additional_uv_arrays(lib, buf_in, n, vert_count, buffers)

        modes_data = _read_json(modes_buf)
        skinning_modes = (
            modes_data.get("skinningModes", []) if modes_data else []
        )

        mat_grp_count = mat_grp_buf.len // 12
        mat_grps = _as_uint32_array(mat_grp_buf, mat_grp_count * 3)

        pmx = PmxData()

        meta = metadata.get("metadata", {})
        _build_header(pmx.header, meta)

        pmx.vertices = _build_vertices(
            vert_count, pos, norm, uv, edge,
            skin_i, skin_w, skinning_modes,
            sdef_c, sdef_r0, sdef_r1, additional_uvs,
        )

        pmx.faces = _build_faces(idx, idx_count)

        tex_map: Dict[str, int] = {}
        tex_list: List[str] = []
        mat_json = metadata.get("materials", [])
        pmx.materials = _build_materials(
            mat_json, mat_grps, mat_grp_count, tex_map, tex_list,
        )
        pmx.textures = tex_list

        bones_json = metadata.get("skeleton", {}).get("bones", [])
        pmx.bones = _build_bones(bones_json)

        morphs_json = metadata.get("morphs", [])
        pmx.morphs = _build_morphs(morphs_json)

        frames_json = metadata.get("displayFrames", [])
        pmx.display_frames = _build_display_frames(frames_json)

        bodies_json = metadata.get("rigidBodies", [])
        pmx.rigid_bodies = _build_rigid_bodies(bodies_json)

        joints_json = metadata.get("joints", [])
        pmx.joints = _build_joints(joints_json)

        return pmx

    finally:
        for buf in buffers:
            try:
                free_fn(buf)
            except Exception:
                pass


# ------------------------------------------------------------------
# Buffer helpers
# ------------------------------------------------------------------

_SIGS_SET = False


def _ensure_signatures(lib: Any) -> None:
    global _SIGS_SET
    if _SIGS_SET:
        return
    _SIGS_SET = True

    buf_names = [
        "positions_buffer", "normals_buffer", "uvs_buffer", "indices_buffer",
        "skin_indices_buffer", "skin_weights_buffer", "material_groups_buffer",
        "edge_scale_buffer", "non_geometry_json", "skinning_modes_json",
        "sdef_c_buffer", "sdef_r0_buffer", "sdef_r1_buffer",
        "qdef_enabled_buffer",
    ]
    for name in buf_names:
        fn = getattr(lib, f"mmd_runtime_parse_pmx_{name}", None)
        if fn is not None:
            fn.restype = _ByteBuffer
            fn.argtypes = [POINTER(c_uint8), c_size_t]

    count_fn = getattr(lib, "mmd_runtime_parse_pmx_additional_uv_count", None)
    if count_fn is not None:
        count_fn.restype = c_size_t
        count_fn.argtypes = [POINTER(c_uint8), c_size_t]

    additional_uvs_fn = getattr(lib, "mmd_runtime_parse_pmx_additional_uvs_buffer", None)
    if additional_uvs_fn is not None:
        additional_uvs_fn.restype = _ByteBuffer
        additional_uvs_fn.argtypes = [POINTER(c_uint8), c_size_t, c_size_t]

    free_fn = getattr(lib, "mmd_runtime_byte_buffer_free", None)
    if free_fn is not None:
        free_fn.restype = None
        free_fn.argtypes = [_ByteBuffer]


def _call_buffer(
    lib: Any, name: str, buf_in: Any, n: int, tracker: list,
) -> _ByteBuffer:
    fn = getattr(lib, f"mmd_runtime_parse_pmx_{name}")
    result = fn(buf_in, n)
    tracker.append(result)
    return result


def _read_additional_uv_arrays(lib: Any, buf_in: Any, n: int, vertex_count: int, tracker: list):
    count_fn = getattr(lib, "mmd_runtime_parse_pmx_additional_uv_count", None)
    buffer_fn = getattr(lib, "mmd_runtime_parse_pmx_additional_uvs_buffer", None)
    if count_fn is None or buffer_fn is None:
        return []

    count = int(count_fn(buf_in, n))
    if count <= 0:
        return []

    arrays = []
    for uv_index in range(count):
        buf = buffer_fn(buf_in, n, uv_index)
        tracker.append(buf)
        arrays.append(_as_float_array(buf, vertex_count * 4))
    return arrays


def _read_json(buf: _ByteBuffer) -> Optional[dict]:
    if not buf.data or buf.len == 0:
        return None
    addr = ctypes.cast(buf.data, c_void_p).value
    if not addr:
        return None
    raw = (c_uint8 * buf.len).from_address(addr)
    return json.loads(bytes(raw).decode("utf-8", errors="replace"))


def _as_float_array(buf: _ByteBuffer, count: int):
    if not buf.data or buf.len == 0 or count == 0:
        return None
    addr = ctypes.cast(buf.data, c_void_p).value
    if not addr:
        return None
    return (c_float * count).from_address(addr)


def _as_uint32_array(buf: _ByteBuffer, count: int):
    if not buf.data or buf.len == 0 or count == 0:
        return None
    addr = ctypes.cast(buf.data, c_void_p).value
    if not addr:
        return None
    return (c_uint32 * count).from_address(addr)


# ------------------------------------------------------------------
# PmxData builders
# ------------------------------------------------------------------

def _build_header(header: PmxHeader, meta: dict) -> None:
    header.magic = b"PMX "
    header.version = float(meta.get("version", 2.0))
    header.header_size = 8

    enc = meta.get("encoding", "utf-16-le")
    header.encoding = PmxEncoding.UTF8 if "utf-8" in enc.lower() else PmxEncoding.UTF16LE

    header.additional_uv = int(meta.get("additionalUvCount", 0))

    sizes = meta.get("indexSizes", {})
    header.vertex_index_size = sizes.get("vertex", 2)
    header.texture_index_size = sizes.get("texture", 2)
    header.material_index_size = sizes.get("material", 2)
    header.bone_index_size = sizes.get("bone", 2)
    header.morph_index_size = sizes.get("morph", 2)
    header.rigid_body_index_size = sizes.get("rigidBody", 2)

    header.model_name = meta.get("name", "")
    header.model_name_english = meta.get("englishName", "")
    header.comment = meta.get("comment", "")
    header.comment_english = meta.get("englishComment", "")


def _build_vertices(
    count: int,
    pos, norm, uv, edge,
    skin_i, skin_w, modes,
    sdef_c, sdef_r0, sdef_r1, additional_uv_arrays=None,
) -> List[PmxVertex]:
    additional_uv_arrays = additional_uv_arrays or []
    vertices = []
    for i in range(count):
        v = PmxVertex.__new__(PmxVertex)
        i3 = i * 3
        i2 = i * 2
        i4 = i * 4

        v.position = (pos[i3], pos[i3 + 1], pos[i3 + 2])
        v.normal = (norm[i3], norm[i3 + 1], norm[i3 + 2])
        v.uv = (uv[i2], uv[i2 + 1])
        v.additional_uvs = []
        for additional_uv in additional_uv_arrays:
            if additional_uv is None:
                continue
            uv4 = i * 4
            v.additional_uvs.append((
                additional_uv[uv4],
                additional_uv[uv4 + 1],
                additional_uv[uv4 + 2],
                additional_uv[uv4 + 3],
            ))
        v.edge_magnification = edge[i] if edge else 1.0

        mode_str = modes[i] if i < len(modes) else "bdef1"
        wt = _SKINNING_MODE_MAP.get(mode_str, 0)
        v.weight_transform_type = wt

        if wt == 0:  # BDEF1
            v.bone_indices = [int(skin_i[i4])]
            v.bone_weights = []
        elif wt == 1 or wt == 3:  # BDEF2 / SDEF
            v.bone_indices = [int(skin_i[i4]), int(skin_i[i4 + 1])]
            v.bone_weights = [float(skin_w[i4])]
        else:  # BDEF4, QDEF
            v.bone_indices = [
                int(skin_i[i4]), int(skin_i[i4 + 1]),
                int(skin_i[i4 + 2]), int(skin_i[i4 + 3]),
            ]
            v.bone_weights = [
                float(skin_w[i4]), float(skin_w[i4 + 1]),
                float(skin_w[i4 + 2]), float(skin_w[i4 + 3]),
            ]

        if wt == 3 and sdef_c is not None:  # SDEF
            v.sdef_c = (sdef_c[i3], sdef_c[i3 + 1], sdef_c[i3 + 2])
            v.sdef_r0 = (sdef_r0[i3], sdef_r0[i3 + 1], sdef_r0[i3 + 2])
            v.sdef_r1 = (sdef_r1[i3], sdef_r1[i3 + 1], sdef_r1[i3 + 2])
        else:
            v.sdef_c = (0.0, 0.0, 0.0)
            v.sdef_r0 = (0.0, 0.0, 0.0)
            v.sdef_r1 = (0.0, 0.0, 1.0)

        v.bone_index_size = 2
        v.additional_uv_count = len(v.additional_uvs)

        vertices.append(v)

    return vertices


def _build_faces(idx, idx_count: int) -> List[PmxFace]:
    faces = []
    for i in range(0, idx_count, 3):
        f = PmxFace.__new__(PmxFace)
        f.vertex_index_size = 4
        f.indices = (int(idx[i]), int(idx[i + 1]), int(idx[i + 2]))
        faces.append(f)
    return faces


def _tex_index(path: str, tex_map: Dict[str, int], tex_list: List[str]) -> int:
    if not path:
        return -1
    if path in tex_map:
        return tex_map[path]
    idx = len(tex_list)
    tex_list.append(path)
    tex_map[path] = idx
    return idx


def _build_draw_flag(flags: dict) -> int:
    val = 0
    if flags.get("doubleSided"):
        val |= PmxDrawFlag.DOUBLE_SIDED
    if flags.get("groundShadow"):
        val |= PmxDrawFlag.GROUND_SHADOW
    if flags.get("selfShadowMap"):
        val |= PmxDrawFlag.SELF_SHADOW_MAP
    if flags.get("selfShadow"):
        val |= PmxDrawFlag.SELF_SHADOW
    if flags.get("edge"):
        val |= PmxDrawFlag.EDGE_DRAWING
    if flags.get("vertexColor"):
        val |= PmxDrawFlag.VERTEX_COLOR
    if flags.get("pointDraw"):
        val |= PmxDrawFlag.POINT_DRAWING
    if flags.get("lineDraw"):
        val |= PmxDrawFlag.LINE_DRAWING
    return val


def _build_materials(
    mat_json: list,
    mat_grps,
    mat_grp_count: int,
    tex_map: Dict[str, int],
    tex_list: List[str],
) -> List[PmxMaterial]:
    materials = []
    for i, mj in enumerate(mat_json):
        m = PmxMaterial.__new__(PmxMaterial)
        m.texture_index_size = 2
        m.encoding = PmxEncoding.UTF16LE
        m.material_index = i

        m.name = mj.get("name", "")
        m.name_english = mj.get("englishName", "")

        d = mj.get("diffuse", [1.0, 1.0, 1.0, 1.0])
        m.diffuse = tuple(d[:4]) if len(d) >= 4 else (1.0, 1.0, 1.0, 1.0)
        s = mj.get("specular", [0.5, 0.5, 0.5])
        m.specular = tuple(s[:3]) if len(s) >= 3 else (0.5, 0.5, 0.5)
        m.specular_coefficient = float(mj.get("specularPower", 5.0))
        a = mj.get("ambient", [0.3, 0.3, 0.3])
        m.ambient = tuple(a[:3]) if len(a) >= 3 else (0.3, 0.3, 0.3)

        flags = mj.get("flags", {})
        m.draw_flag = _build_draw_flag(flags)

        ec = mj.get("edgeColor", [0.0, 0.0, 0.0, 1.0])
        m.edge_color = tuple(ec[:4]) if len(ec) >= 4 else (0.0, 0.0, 0.0, 1.0)
        m.edge_size = float(mj.get("edgeSize", 1.0))

        m.texture_index = _tex_index(mj.get("texturePath", ""), tex_map, tex_list)
        m.sphere_texture_index = _tex_index(
            mj.get("sphereTexturePath", ""), tex_map, tex_list,
        )
        m.sphere_mode = _SPHERE_MODE_MAP.get(
            mj.get("sphereMode", "none"), PmxSphereMode.DISABLED,
        )

        shared_toon = mj.get("sharedToonIndex", -1)
        if shared_toon is not None and shared_toon >= 0:
            m.shared_toon_flag = PmxSharedToonFlag.SHARED
            m.toon_texture_index = int(shared_toon)
        else:
            toon_path = mj.get("toonTexturePath", "")
            if toon_path:
                m.shared_toon_flag = PmxSharedToonFlag.NOT_SHARED
                m.toon_texture_index = _tex_index(toon_path, tex_map, tex_list)
            else:
                m.shared_toon_flag = PmxSharedToonFlag.NOT_SHARED
                m.toon_texture_index = -1

        m.memo = mj.get("memo", "")

        if mat_grps is not None and i < mat_grp_count:
            m.face_count = int(mat_grps[i * 3 + 1])
        else:
            m.face_count = int(mj.get("faceCount", 0)) * 3

        materials.append(m)

    return materials


def _build_bone_flag(flags: dict) -> int:
    val = 0
    if flags.get("indexedTail"):
        val |= PmxBoneFlag.CONNECT_BONE
    if flags.get("rotatable"):
        val |= PmxBoneFlag.ROTATABLE
    if flags.get("translatable"):
        val |= PmxBoneFlag.MOVABLE
    if flags.get("visible"):
        val |= PmxBoneFlag.DISPLAY
    if flags.get("enabled"):
        val |= PmxBoneFlag.OPERATABLE
    if flags.get("ik"):
        val |= PmxBoneFlag.IK
    if flags.get("appendLocal"):
        val |= PmxBoneFlag.LOCAL
    if flags.get("appendRotate"):
        val |= PmxBoneFlag.GRANT_PARENT_ROTATE
    if flags.get("appendTranslate"):
        val |= PmxBoneFlag.GRANT_PARENT_MOVE
    if flags.get("fixedAxis"):
        val |= PmxBoneFlag.AXIS_FIXED
    if flags.get("localAxis"):
        val |= PmxBoneFlag.LOCAL_AXIS
    if flags.get("transformAfterPhysics"):
        val |= PmxBoneFlag.DEFORM_AFTER_PHYSICS
    if flags.get("externalParentTransform"):
        val |= PmxBoneFlag.EXTERNAL_PARENT_DEFORM
    return val


def _build_bones(bones_json: list) -> List[PmxBone]:
    bones = []
    for bj in bones_json:
        b = PmxBone.__new__(PmxBone)
        b.bone_index_size = 2
        b.encoding = PmxEncoding.UTF16LE

        b.name = bj.get("name", "")
        b.name_english = bj.get("englishName", "")

        p = bj.get("position") or [0.0, 0.0, 0.0]
        b.position = tuple(p[:3])
        b.parent_bone_index = int(bj.get("parentIndex", -1))
        b.transform_layer = int(bj.get("layer", 0))

        flags = bj.get("flags", {})
        b.bone_flag = _build_bone_flag(flags)

        tail_idx = bj.get("tailIndex", -1)
        if tail_idx is not None and tail_idx >= 0 and flags.get("indexedTail"):
            b.connect_bone_index = int(tail_idx)
            b.connect_position_offset = (0.0, 0.0, 0.0)
        else:
            b.connect_bone_index = -1
            tp = bj.get("tailPosition") or [0.0, 0.0, 0.0]
            b.connect_position_offset = tuple(tp[:3])

        append = bj.get("appendTransform")
        if append:
            b.grant_parent_bone_index = int(append.get("parentIndex", -1))
            b.grant_rate = float(append.get("ratio", 0.0))
        else:
            b.grant_parent_bone_index = -1
            b.grant_rate = 0.0

        fa = bj.get("fixedAxis")
        if fa:
            b.axis_direction = tuple(fa[:3])
        else:
            b.axis_direction = (0.0, 0.0, 0.0)

        la = bj.get("localAxis")
        if la:
            x = la.get("x") or [1.0, 0.0, 0.0]
            z = la.get("z") or [0.0, 0.0, 1.0]
            b.x_axis_direction = tuple(x[:3])
            b.z_axis_direction = tuple(z[:3])
        else:
            b.x_axis_direction = (1.0, 0.0, 0.0)
            b.z_axis_direction = (0.0, 0.0, 1.0)

        b.key_value = int(bj.get("externalParentKey", 0))

        ik = bj.get("ik")
        if ik:
            b.ik_target_bone_index = int(ik.get("targetBoneIndex", -1))
            b.ik_loop_count = int(ik.get("loopCount", 0))
            b.ik_limit_angle = float(ik.get("limitAngle", 0.0))
            b.ik_links = []
            for lk in ik.get("links", []):
                link = PmxIKLink.__new__(PmxIKLink)
                link.ik_bone_index = int(lk.get("boneIndex", 0))
                link.angle_limit = 1 if lk.get("hasAngleLimit") else 0
                lmin = lk.get("limitMin") or [0.0, 0.0, 0.0]
                lmax = lk.get("limitMax") or [0.0, 0.0, 0.0]
                link.limit_min = tuple(lmin[:3])
                link.limit_max = tuple(lmax[:3])
                b.ik_links.append(link)
        else:
            b.ik_target_bone_index = -1
            b.ik_loop_count = 0
            b.ik_limit_angle = 0.0
            b.ik_links = []

        bones.append(b)

    return bones


def _build_morphs(morphs_json: list) -> List[PmxMorph]:
    morphs = []
    for mj in morphs_json:
        m = PmxMorph.__new__(PmxMorph)
        m.vertex_index_size = 2
        m.material_index_size = 2
        m.bone_index_size = 2
        m.morph_index_size = 2
        m.rigid_body_index_size = 2
        m.type_formats = {}
        m.encoding = PmxEncoding.UTF16LE

        m.name = mj.get("name", "")
        m.name_english = mj.get("englishName", "")
        m.panel = int(mj.get("panel", 4))
        morph_type_name = mj.get("type", "vertex")
        if morph_type_name == "additionalUv":
            add_uv_offsets = mj.get("additionalUvOffsets", [])
            uv_index = int(add_uv_offsets[0].get("uvIndex", 0)) if add_uv_offsets else 0
            m.morph_type = PmxMorphType.AdditionalUVMorph1 + max(0, min(uv_index, 3))
        else:
            m.morph_type = _MORPH_TYPE_MAP.get(
                morph_type_name, PmxMorphType.VertexMorph,
            )

        offsets = []
        if m.morph_type == PmxMorphType.VertexMorph:
            for vo in mj.get("vertexOffsets", []):
                p = vo.get("position", [0.0, 0.0, 0.0])
                offsets.append({
                    "vertex_index": int(vo.get("vertexIndex", 0)),
                    "position_offset": tuple(p[:3]),
                })
        elif m.morph_type == PmxMorphType.BoneMorph:
            for bo in mj.get("boneOffsets", []):
                t = bo.get("translation", [0.0, 0.0, 0.0])
                r = bo.get("rotation", [0.0, 0.0, 0.0, 1.0])
                offsets.append({
                    "bone_index": int(bo.get("boneIndex", 0)),
                    "translation": tuple(t[:3]),
                    "rotation": tuple(r[:4]),
                })
        elif m.morph_type == PmxMorphType.GroupMorph:
            for go in mj.get("groupOffsets", []):
                offsets.append({
                    "morph_index": int(go.get("morphIndex", 0)),
                    "morph_rate": float(_get_any(go, "weight", "morphRate", default=0.0)),
                })
        elif m.morph_type == PmxMorphType.UVMorph:
            for uo in mj.get("uvOffsets", []):
                uv_off = _get_any(uo, "uv", "uvOffset", default=[0.0, 0.0, 0.0, 0.0])
                offsets.append({
                    "vertex_index": int(uo.get("vertexIndex", 0)),
                    "uv_offset": tuple(uv_off[:4]),
                })
        elif PmxMorphType.AdditionalUVMorph1 <= m.morph_type <= PmxMorphType.AdditionalUVMorph4:
            for uo in mj.get("additionalUvOffsets", []):
                uv_off = _get_any(uo, "uv", "uvOffset", default=[0.0, 0.0, 0.0, 0.0])
                offsets.append({
                    "vertex_index": int(uo.get("vertexIndex", 0)),
                    "uv_offset": tuple(uv_off[:4]),
                })
        elif m.morph_type == PmxMorphType.MaterialMorph:
            for mo in mj.get("materialOffsets", []):
                d = mo.get("diffuse", [0.0, 0.0, 0.0, 0.0])
                s = mo.get("specular", [0.0, 0.0, 0.0])
                a = mo.get("ambient", [0.0, 0.0, 0.0])
                ec = mo.get("edgeColor", [0.0, 0.0, 0.0, 0.0])
                tex_coeff = _get_any(mo, "textureFactor", "textureCoefficient", default=[0.0, 0.0, 0.0, 0.0])
                sph_coeff = _get_any(
                    mo,
                    "sphereTextureFactor",
                    "sphereCoefficient",
                    default=[0.0, 0.0, 0.0, 0.0],
                )
                toon_coeff = _get_any(
                    mo,
                    "toonTextureFactor",
                    "toonCoefficient",
                    default=[0.0, 0.0, 0.0, 0.0],
                )
                operation_type = _get_any(mo, "operationType", default=None)
                if operation_type is None:
                    operation_type = _MATERIAL_MORPH_OPERATION_MAP.get(mo.get("operation", "multiply"), 0)
                offsets.append({
                    "material_index": int(mo.get("materialIndex", 0)),
                    "operation_type": int(operation_type),
                    "diffuse": tuple(d[:4]),
                    "specular": tuple(s[:3]),
                    "specular_coefficient": float(mo.get("specularPower", 0.0)),
                    "ambient": tuple(a[:3]),
                    "edge_color": tuple(ec[:4]),
                    "edge_size": float(mo.get("edgeSize", 0.0)),
                    "texture_factor": tuple(tex_coeff[:4]),
                    "sphere_texture_factor": tuple(sph_coeff[:4]),
                    "toon_texture_factor": tuple(toon_coeff[:4]),
                })
        elif m.morph_type == PmxMorphType.FlipMorph:
            for fo in mj.get("flipOffsets", []):
                offsets.append({
                    "morph_index": int(fo.get("morphIndex", 0)),
                    "flip_rate": float(_get_any(fo, "weight", "morphRate", default=0.0)),
                })
        elif m.morph_type == PmxMorphType.ImpulseMorph:
            for io_data in mj.get("impulseOffsets", []):
                v = io_data.get("velocity", [0.0, 0.0, 0.0])
                torque = _get_any(io_data, "torque", "rotationVelocity", default=[0.0, 0.0, 0.0])
                offsets.append({
                    "rigid_body_index": int(io_data.get("rigidBodyIndex", 0)),
                    "is_local": int(_get_any(io_data, "local", "isLocal", default=0)),
                    "impulse": tuple(v[:3]),
                    "torque": tuple(torque[:3]),
                })

        m.offset_count = len(offsets)
        m.offsets = offsets
        morphs.append(m)

    return morphs


def _build_display_frames(frames_json: list) -> List[PmxDisplayFrame]:
    frames = []
    for fj in frames_json:
        df = PmxDisplayFrame.__new__(PmxDisplayFrame)
        df.bone_index_size = 2
        df.morph_index_size = 2
        df.encoding_flag = 1
        df.encoding = "utf-8"

        df.name = fj.get("name", "")
        df.name_english = fj.get("englishName", "")
        df.special_flag = 1 if fj.get("special") else 0

        df.elements = []
        for elem in fj.get("frames", []):
            elem_type = elem.get("type", "bone")
            if elem_type == "bone":
                df.elements.append({"type": 0, "index": int(elem.get("index", 0))})
            elif elem_type == "morph":
                df.elements.append({"type": 1, "index": int(elem.get("index", 0))})

        frames.append(df)

    return frames


def _build_rigid_bodies(bodies_json: list) -> List[PmxRigidBody]:
    bodies = []
    for rj in bodies_json:
        rb = PmxRigidBody.__new__(PmxRigidBody)
        rb.bone_index_size = 2
        rb.encoding_flag = 1
        rb.encoding = "utf-8"

        rb.name = rj.get("name", "")
        rb.name_english = rj.get("englishName", "")
        rb.related_bone_index = int(_get_any(rj, "boneIndex", "relatedBoneIndex", default=-1))
        rb.group = int(rj.get("group", 0))
        rb.collision_mask = int(_get_any(rj, "mask", "collisionMask", default=0))

        shape_map = {"sphere": 0, "box": 1, "capsule": 2}
        rb.shape_type = shape_map.get(_get_any(rj, "shape", "shapeType", default="sphere"), 0)

        sz = rj.get("size", [0.0, 0.0, 0.0])
        rb.size = tuple(sz[:3])
        p = rj.get("position", [0.0, 0.0, 0.0])
        rb.position = tuple(p[:3])
        r = rj.get("rotation", [0.0, 0.0, 0.0])
        rb.rotation = tuple(r[:3])

        rb.mass = float(rj.get("mass", 0.0))
        rb.velocity_attenuation = float(_get_any(rj, "linearDamping", "velocityAttenuation", default=0.0))
        rb.rotation_attenuation = float(_get_any(rj, "angularDamping", "rotationAttenuation", default=0.0))
        rb.elasticity = float(rj.get("restitution", 0.0))
        rb.friction = float(rj.get("friction", 0.0))

        mode_map = {
            "boneFollow": 0,
            "static": 0,
            "physics": 1,
            "dynamic": 1,
            "physicsAlignment": 2,
            "dynamicBone": 2,
        }
        rb.physics_mode = mode_map.get(_get_any(rj, "mode", "physicsMode", default="boneFollow"), 0)

        bodies.append(rb)

    return bodies


def _build_joints(joints_json: list) -> List[PmxJoint]:
    joints = []
    for jj in joints_json:
        j = PmxJoint.__new__(PmxJoint)
        j.rigid_body_index_size = 2
        j.encoding = PmxEncoding.UTF16LE

        j.name = jj.get("name", "")
        j.name_english = jj.get("englishName", "")

        type_map = {
            "spring6DOF": 0,
            "generic6dofSpring": 0,
            "6dof": 1,
            "generic6dof": 1,
            "p2p": 2,
            "point2point": 2,
            "coneTwist": 3,
            "slider": 4,
            "hinge": 5,
        }
        j.joint_type = type_map.get(_get_any(jj, "type", "jointType", default="spring6DOF"), 0)

        j.rigid_body_a_index = int(_get_any(jj, "rigidBodyIndexA", "rigidBodyAIndex", default=-1))
        j.rigid_body_b_index = int(_get_any(jj, "rigidBodyIndexB", "rigidBodyBIndex", default=-1))

        p = jj.get("position", [0.0, 0.0, 0.0])
        j.position = tuple(p[:3])
        r = jj.get("rotation", [0.0, 0.0, 0.0])
        j.rotation = tuple(r[:3])

        tmin = _get_any(jj, "translationLowerLimit", "translationLimitMin", default=[0.0, 0.0, 0.0])
        tmax = _get_any(jj, "translationUpperLimit", "translationLimitMax", default=[0.0, 0.0, 0.0])
        j.translation_limit_min = tuple(tmin[:3])
        j.translation_limit_max = tuple(tmax[:3])

        rmin = _get_any(jj, "rotationLowerLimit", "rotationLimitMin", default=[0.0, 0.0, 0.0])
        rmax = _get_any(jj, "rotationUpperLimit", "rotationLimitMax", default=[0.0, 0.0, 0.0])
        j.rotation_limit_min = tuple(rmin[:3])
        j.rotation_limit_max = tuple(rmax[:3])

        st = _get_any(jj, "springTranslationFactor", "springTranslation", default=[0.0, 0.0, 0.0])
        sr = _get_any(jj, "springRotationFactor", "springRotation", default=[0.0, 0.0, 0.0])
        j.spring_translation = tuple(st[:3])
        j.spring_rotation = tuple(sr[:3])

        joints.append(j)

    return joints
