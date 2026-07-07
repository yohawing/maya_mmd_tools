"""Native VMD camera and light sampling helpers for the mmd-anim runtime FFI."""

from __future__ import annotations

from ctypes import CDLL, c_float, c_size_t, c_uint8
from typing import Any, Callable, Dict, List, Optional

from mmd_tools.core.logger import get_logger
from mmd_tools.core.native import mmd_anim_runtime_loader as _runtime_loader

logger = get_logger(__name__)


def _resolve_library(get_library: Optional[Callable[[], Optional[CDLL]]] = None) -> Optional[CDLL]:
    return (get_library or _runtime_loader.get_mmd_runtime_library)()


def sample_vmd_camera_frames(
    vmd_bytes: bytes,
    start_frame: float,
    frame_step: float,
    frame_count: int,
    get_library: Optional[Callable[[], Optional[CDLL]]] = None,
) -> Optional[List[Dict[str, Any]]]:
    """Sample VMD camera state through mmd-anim's camera interpolation logic."""
    lib = _resolve_library(get_library)
    if lib is None or not vmd_bytes or frame_count <= 0:
        return None
    create_track = getattr(lib, "mmd_runtime_vmd_camera_track_create_from_vmd_bytes", None)
    sample_track = getattr(lib, "mmd_runtime_vmd_camera_track_sample", None)
    free_track = getattr(lib, "mmd_runtime_vmd_camera_track_free", None)
    if create_track is None or sample_track is None or free_track is None:
        return None

    track = None
    try:
        buf = (c_uint8 * len(vmd_bytes)).from_buffer_copy(vmd_bytes)
        track = create_track(buf, len(vmd_bytes))
        if not track:
            return None
        out = (c_float * 9)()
        samples: List[Dict[str, Any]] = []
        for index in range(int(frame_count)):
            frame = float(start_frame) + float(frame_step) * index
            if not sample_track(track, c_float(frame), out, c_size_t(9)):
                continue
            samples.append(
                {
                    "frame": frame,
                    "distance": float(out[0]),
                    "position": (float(out[1]), float(out[2]), float(out[3])),
                    "rotation": (float(out[4]), float(out[5]), float(out[6])),
                    "fov": float(out[7]),
                    "perspective": bool(out[8] != 0.0),
                }
            )
        return samples or None
    except Exception as e:
        logger.error(f"sample_vmd_camera_frames failed: {e}", exc_info=True)
        return None
    finally:
        if track:
            free_track(track)


def sample_vmd_light_frames(
    vmd_bytes: bytes,
    start_frame: float,
    frame_step: float,
    frame_count: int,
    get_library: Optional[Callable[[], Optional[CDLL]]] = None,
) -> Optional[List[Dict[str, Any]]]:
    """Sample VMD light state through mmd-anim's light interpolation logic."""
    lib = _resolve_library(get_library)
    if lib is None or not vmd_bytes or frame_count <= 0:
        return None
    create_track = getattr(lib, "mmd_runtime_vmd_light_track_create_from_vmd_bytes", None)
    sample_track = getattr(lib, "mmd_runtime_vmd_light_track_sample", None)
    free_track = getattr(lib, "mmd_runtime_vmd_light_track_free", None)
    if create_track is None or sample_track is None or free_track is None:
        return None

    track = None
    try:
        buf = (c_uint8 * len(vmd_bytes)).from_buffer_copy(vmd_bytes)
        track = create_track(buf, len(vmd_bytes))
        if not track:
            return None
        out = (c_float * 6)()
        samples: List[Dict[str, Any]] = []
        for index in range(int(frame_count)):
            frame = float(start_frame) + float(frame_step) * index
            if not sample_track(track, c_float(frame), out, c_size_t(6)):
                continue
            samples.append(
                {
                    "frame": frame,
                    "color": (float(out[0]), float(out[1]), float(out[2])),
                    "position": (float(out[3]), float(out[4]), float(out[5])),
                }
            )
        return samples or None
    except Exception as e:
        logger.error(f"sample_vmd_light_frames failed: {e}", exc_info=True)
        return None
    finally:
        if track:
            free_track(track)
