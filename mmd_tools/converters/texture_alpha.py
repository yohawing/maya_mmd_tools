"""Per-material transparency classification by sampling texture alpha inside the
material's *used* UV region.

MMD models routinely share one atlas texture across many materials and save it as
32-bit RGBA even when opaque, so neither the file extension nor a whole-texture
alpha scan can tell whether a given material is actually transparent (a mostly
transparent atlas may only expose an opaque sub-region to a material, and vice
versa). This module rasterizes each material's UV triangles and samples the
texture alpha only at the texels the material covers, then classifies it.

Ported from the three-mmd-loader reference implementation
(``src/three/textures.ts`` :: ``evaluateMmdTextureAlphaGeometry`` /
``evaluateAlphaStats``). Pure Python so it works under every supported Maya
(Maya 2024's mayapy has no numpy).
"""

import math
import os

# Classification results (mapped to mesh_converter technique modes by the caller).
#   reference "opaque"    -> opaque
#   reference "alphaTest" -> cutout (hard-edged, depth-write ON + clip)
#   reference "alphaBlend"-> blend  (soft translucent, depth-write OFF)
MODE_OPAQUE = "opaque"
MODE_CUTOUT = "cutout"
MODE_BLEND = "blend"

# Classification thresholds (spec §8.2 (C)).
# NOTE: the opaque cutoff is 255, NOT three.js/babylon's 195. Real MMD (and the
# faithful saba) has no opaque alpha cutoff -- it always multiplies diffuse alpha
# and alpha-blends. The old 195 absorbed the 195-254 soft band and painted soft
# hair tips (~213/255) fully opaque, diverging from the golden. Raising it to 255
# forces "any sub-255 soft mask -> alphaBlend". (alphaBlendThreshold=100 and
# partial-ratio=0.25 match the reference loaders.)
TEXTURE_OPAQUE_ALPHA_THRESHOLD = 255
TEXTURE_ALPHA_BLEND_THRESHOLD = 100
PARTIAL_ALPHA_RATIO_THRESHOLD = 0.25

# UV triangles are rasterized into a fixed grid (clamped to texture size) to
# sample the used texels (spec §8.2 (B)).
_SCAN_RESOLUTION = 512
# Stride for the whole-texture material fallback (spec §8.2 (B) step 5).
_WHOLE_SCAN_STRIDE = 4
# These limits are part of the cross-runtime classifier contract.  The C++
# implementation mirrors them so malformed UVs cannot turn classification into
# an unbounded raster job.
_MAX_TILE_SPAN = 64
_MAX_UV_MAGNITUDE = 1_000_000.0
_MAX_RASTER_SAMPLES = _SCAN_RESOLUTION * _SCAN_RESOLUTION

# Texture v axis: MMD/PMX uv origin is top-left; MImage rows are bottom-up, so the
# v coordinate is flipped when indexing the alpha plane. Validated against YYB
# Miku (body/q3 used regions -> opaque, hair -> cutout).
_FLIP_V = True


class _AlphaStats:
    __slots__ = ("min_alpha", "max_alpha", "middle_total", "middle_count", "sample_count")

    def __init__(self):
        self.min_alpha = 255
        self.max_alpha = 0
        self.middle_total = 0
        self.middle_count = 0
        self.sample_count = 0


def _record(stats, alpha):
    if stats.sample_count >= _MAX_RASTER_SAMPLES:
        return
    stats.sample_count += 1
    if alpha < stats.min_alpha:
        stats.min_alpha = alpha
    if alpha > stats.max_alpha:
        stats.max_alpha = alpha
    if 0 < alpha < 255:
        stats.middle_total += alpha
        stats.middle_count += 1


def _evaluate(stats, opaque_threshold=TEXTURE_OPAQUE_ALPHA_THRESHOLD) -> str:
    if stats.sample_count == 0 or stats.min_alpha >= opaque_threshold:
        return MODE_OPAQUE
    if stats.middle_count / stats.sample_count >= PARTIAL_ALPHA_RATIO_THRESHOLD:
        return MODE_BLEND
    average_middle = stats.middle_total / stats.middle_count if stats.middle_count else 0
    return MODE_CUTOUT if average_middle + TEXTURE_ALPHA_BLEND_THRESHOLD < stats.max_alpha else MODE_BLEND


def _round_half_away_from_zero(value):
    # C++ std::llround rounds half away from zero; Python round() uses bankers'
    # rounding, which differs at texel boundaries (notably for wrapped UVs).
    magnitude = math.floor(abs(value) + 0.5)
    return -magnitude if value < 0.0 else magnitude


def _sample_alpha(alpha, width, height, u, v):
    x = _round_half_away_from_zero(u * width) % width
    y = _round_half_away_from_zero(v * height) % height
    if x < 0:
        x += width
    if y < 0:
        y += height
    return alpha[y * width + x]


def _record_triangle_tile(stats, alpha, width, height, ax, ay, bx, by, cx, cy, resolution):
    if stats.sample_count >= _MAX_RASTER_SAMPLES:
        return
    rax = ax * resolution
    ray = ay * resolution
    rbx = bx * resolution
    rby = by * resolution
    rcx = cx * resolution
    rcy = cy * resolution
    min_x = max(0, math.floor(min(rax, rbx, rcx)))
    max_x = min(resolution - 1, math.ceil(max(rax, rbx, rcx)))
    min_y = max(0, math.floor(min(ray, rby, rcy)))
    max_y = min(resolution - 1, math.ceil(max(ray, rby, rcy)))
    if min_x > max_x or min_y > max_y:
        return
    denominator = (rby - rcy) * (rax - rcx) + (rcx - rbx) * (ray - rcy)
    if abs(denominator) < 1e-9:
        return
    inv = 1.0 / denominator
    for y in range(min_y, max_y + 1):
        py = y + 0.5
        for x in range(min_x, max_x + 1):
            px = x + 0.5
            wa = ((rby - rcy) * (px - rcx) + (rcx - rbx) * (py - rcy)) * inv
            wb = ((rcy - ray) * (px - rcx) + (rax - rcx) * (py - rcy)) * inv
            wc = 1.0 - wa - wb
            if wa >= 0 and wb >= 0 and wc >= 0:
                _record(stats, _sample_alpha(alpha, width, height, px / resolution, py / resolution))


def _record_triangle(stats, alpha, width, height, ax, ay, bx, by, cx, cy, resolution):
    # Tiling: replicate triangles whose UVs fall outside [0, 1] so wrapped texels
    # are sampled (matches the reference's shift loop).
    before = stats.sample_count
    min_u = min(ax, bx, cx)
    max_u = max(ax, bx, cx)
    min_v = min(ay, by, cy)
    max_v = max(ay, by, cy)
    first_u = math.ceil(-max_u)
    last_u = math.floor(1 - min_u)
    first_v = math.ceil(-max_v)
    last_v = math.floor(1 - min_v)
    # Match the native bounded-runtime contract: very large wrap spans skip
    # tiled rasterization and use the vertex fallback below.
    if last_u - first_u <= _MAX_TILE_SPAN and last_v - first_v <= _MAX_TILE_SPAN:
        for shift_u in range(first_u, last_u + 1):
            for shift_v in range(first_v, last_v + 1):
                _record_triangle_tile(
                    stats,
                    alpha,
                    width,
                    height,
                    ax + shift_u,
                    ay + shift_v,
                    bx + shift_u,
                    by + shift_v,
                    cx + shift_u,
                    cy + shift_v,
                    resolution,
                )
                if stats.sample_count >= _MAX_RASTER_SAMPLES:
                    return
    # Triangle fallback (spec §8.2 (B) step 4): a degenerate / sub-texel triangle
    # may cover no grid cell; sample its three vertices so it still contributes.
    if stats.sample_count == before:
        _record(stats, _sample_alpha(alpha, width, height, ax, ay))
        _record(stats, _sample_alpha(alpha, width, height, bx, by))
        _record(stats, _sample_alpha(alpha, width, height, cx, cy))


def load_texture_alpha(path):
    """Read a texture's alpha plane via Maya's MImage.

    Returns ``(alpha, width, height)`` where ``alpha`` is a row-major ``bytes`` of
    length ``width * height``, or ``None`` if the file cannot be read.
    """
    try:
        import ctypes

        import maya.api.OpenMaya as om

        image = om.MImage()
        image.readFromFile(path)
        width, height = image.getSize()
        if width <= 0 or height <= 0:
            return None
        count = width * height * 4
        buffer = (ctypes.c_ubyte * count).from_address(image.pixels())
        alpha = bytes(buffer[3:count:4])
        return alpha, width, height
    except Exception:
        return None


def _scan_whole_texture(stats, alpha, stride=_WHOLE_SCAN_STRIDE):
    """Material fallback (spec §8.2 (B) step 5): scan the whole alpha plane.

    Loses atlas-padding resistance, so this is only used when the UV scan yielded
    no samples at all.
    """
    for index in range(0, len(alpha), stride):
        _record(stats, alpha[index])
        if stats.sample_count >= _MAX_RASTER_SAMPLES:
            return


def classify_uv_triangles(
    alpha, width, height, uv_triangles, resolution=_SCAN_RESOLUTION,
    opaque_threshold=TEXTURE_OPAQUE_ALPHA_THRESHOLD,
) -> str:
    """Classify transparency from alpha sampled within the given UV triangles.

    ``uv_triangles`` is an iterable of ``(ax, ay, bx, by, cx, cy)`` tuples.

    The native classifier shares this behavioral contract: malformed,
    non-finite, or over-magnitude UV triangles are ignored; wrapped tiling is
    limited to a 64-tile span and then falls back to the three vertices;
    no-coverage falls back to every fourth texel; and raster work is capped at
    ``512 * 512`` samples.  Invalid dimensions or alpha buffers classify as
    opaque (fail closed).
    """
    try:
        requested_resolution = int(resolution)
    except (TypeError, ValueError, OverflowError):
        return MODE_OPAQUE
    if (
        alpha is None
        or width <= 0
        or height <= 0
        or len(alpha) != width * height
        or requested_resolution <= 0
    ):
        return MODE_OPAQUE
    scan_resolution = min(requested_resolution, max(width, height), _SCAN_RESOLUTION)
    stats = _AlphaStats()
    try:
        triangles = iter(uv_triangles)
    except TypeError:
        triangles = iter(())
    for triangle in triangles:
        try:
            ax, ay, bx, by, cx, cy = (float(value) for value in triangle)
        except (TypeError, ValueError):
            continue
        if _FLIP_V:
            ay = 1.0 - ay
            by = 1.0 - by
            cy = 1.0 - cy
        if not all(
            math.isfinite(value)
            and abs(value) <= _MAX_UV_MAGNITUDE
            for value in (ax, ay, bx, by, cx, cy)
        ):
            continue
        _record_triangle(stats, alpha, width, height, ax, ay, bx, by, cx, cy, scan_resolution)
        if stats.sample_count >= _MAX_RASTER_SAMPLES:
            break
    if stats.sample_count == 0:
        _scan_whole_texture(stats, alpha)
    return _evaluate(stats, opaque_threshold=opaque_threshold)


_ALPHA_CAPABLE_EXTENSIONS = {".png", ".tga", ".tif", ".tiff", ".dds"}


def classify_material(
    resolved_texture_path, uv_triangles, alpha_cache=None, resolution=_SCAN_RESOLUTION,
    opaque_threshold=TEXTURE_OPAQUE_ALPHA_THRESHOLD,
):
    """Classify one material from its resolved texture path and used UV triangles.

    Returns one of ``MODE_OPAQUE`` / ``MODE_CUTOUT`` / ``MODE_BLEND``. Materials
    without an alpha-capable texture (or unreadable ones) resolve to opaque.
    ``alpha_cache`` (optional dict) memoizes decoded textures across materials.
    """
    if not resolved_texture_path:
        return MODE_OPAQUE
    if os.path.splitext(resolved_texture_path)[1].lower() not in _ALPHA_CAPABLE_EXTENSIONS:
        return MODE_OPAQUE
    if not os.path.isfile(resolved_texture_path):
        return MODE_OPAQUE

    loaded = None
    if alpha_cache is not None and resolved_texture_path in alpha_cache:
        loaded = alpha_cache[resolved_texture_path]
    else:
        loaded = load_texture_alpha(resolved_texture_path)
        if alpha_cache is not None:
            alpha_cache[resolved_texture_path] = loaded
    if not loaded:
        return MODE_OPAQUE

    alpha, width, height = loaded
    if alpha is None or width <= 0 or height <= 0 or len(alpha) != width * height:
        return MODE_OPAQUE
    # Fast path: a fully opaque texture (every texel >= threshold) is opaque
    # regardless of UV coverage.
    if min(alpha) >= opaque_threshold:
        return MODE_OPAQUE
    return classify_uv_triangles(
        alpha, width, height, uv_triangles, resolution=resolution, opaque_threshold=opaque_threshold
    )
