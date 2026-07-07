"""VMD animation export boundary.

This module converts already-collected animation data into ``VmdData`` and
writes a VMD file. Maya scene keyframe collection is intentionally kept outside
this class so it can be tested separately from the binary writer.
"""

import os
from typing import Any, Iterable, Mapping, Optional

from mmd_tools.core.exceptions import MMDExportException
from mmd_tools.core.vmd_data import VmdData
from mmd_tools.core.vmd_data.bone_frame import DEFAULT_BONE_INTERPOLATION, VmdBoneFrame
from mmd_tools.core.vmd_data.camera_frame import DEFAULT_CAMERA_INTERPOLATION, VmdCameraFrame
from mmd_tools.core.vmd_data.light_frame import VmdLightFrame
from mmd_tools.core.vmd_data.morph_frame import VmdMorphFrame
from mmd_tools.core.vmd_data.shadow_frame import VmdShadowFrame
from mmd_tools.core.native import export_vmd_animation_json


class VmdExporter:
    """Maya側で収集済みのアニメーションデータをVMDファイルへ書き出すクラス。"""

    def __init__(self, native_exporter=export_vmd_animation_json):
        self._native_exporter = native_exporter

    def export_vmd_animation(self, file_path: str, maya_data: Any) -> VmdData:
        """収集済みアニメーションデータをVMDファイルにエクスポートする。

        Args:
            file_path: エクスポート先のVMDファイルパス。
            maya_data: ``VmdData``、または ``model_name`` と各 frame list を含む辞書。

        Returns:
            書き出しに使用した ``VmdData``。
        """
        try:
            vmd_data = self.to_vmd_data(maya_data)
            native_bytes = self._try_native_export(vmd_data)
            if native_bytes is not None:
                parent_dir = os.path.dirname(file_path)
                if parent_dir:
                    os.makedirs(parent_dir, exist_ok=True)
                with open(file_path, "wb") as handle:
                    handle.write(native_bytes)
            else:
                vmd_data.write_file(file_path)
            return vmd_data
        except (ValueError, TypeError) as e:
            raise MMDExportException(f"Failed to export VMD file {file_path}: {e}") from e

    def to_vmd_data(self, maya_data: Any) -> VmdData:
        """収集済みデータを ``VmdData`` に正規化する。"""
        if isinstance(maya_data, VmdData):
            return maya_data
        if not isinstance(maya_data, Mapping):
            raise TypeError("maya_data must be VmdData or a mapping")

        vmd_data = VmdData()
        vmd_data.header.model_name = str(maya_data.get("model_name", ""))
        vmd_data.bone_frames = [
            self._coerce_bone_frame(frame) for frame in self._get_frames(maya_data, "bone_frames")
        ]
        vmd_data.morph_frames = [
            self._coerce_morph_frame(frame) for frame in self._get_frames(maya_data, "morph_frames")
        ]
        vmd_data.camera_frames = [
            self._coerce_camera_frame(frame) for frame in self._get_frames(maya_data, "camera_frames")
        ]
        vmd_data.light_frames = [
            self._coerce_light_frame(frame) for frame in self._get_frames(maya_data, "light_frames")
        ]
        vmd_data.shadow_frames = [
            self._coerce_shadow_frame(frame) for frame in self._get_frames(maya_data, "shadow_frames")
        ]
        vmd_data.ik_show_hide_frames = list(self._get_frames(maya_data, "ik_show_hide_frames"))
        return vmd_data

    def to_native_json_payload(self, vmd_data: VmdData) -> dict:
        """``VmdData`` を mmd-anim の VmdParsedAnimation JSON shape に変換する。"""
        bone_frames = [
            {
                "boneName": frame.bone_name,
                "frame": int(frame.frame_number),
                "translation": _float_list(frame.position, 3, "bone translation"),
                "rotation": _float_list(frame.rotation, 4, "bone rotation"),
                "interpolation": list(_interpolation_bytes(frame.interpolation, 64, DEFAULT_BONE_INTERPOLATION)),
            }
            for frame in vmd_data.bone_frames
        ]
        morph_frames = [
            {
                "morphName": frame.morph_name,
                "frame": int(frame.frame_number),
                "weight": float(frame.value),
            }
            for frame in vmd_data.morph_frames
        ]
        camera_frames = [
            {
                "frame": int(frame.frame_number),
                "distance": float(frame.distance),
                "position": _float_list(frame.position, 3, "camera position"),
                "rotation": _float_list(frame.rotation, 3, "camera rotation"),
                "interpolation": list(_interpolation_bytes(frame.interpolation, 24, DEFAULT_CAMERA_INTERPOLATION)),
                "fov": int(frame.viewing_angle),
                "perspective": bool(frame.perspective == 0),
            }
            for frame in vmd_data.camera_frames
        ]
        light_frames = [
            {
                "frame": int(frame.frame_number),
                "color": _float_list(frame.color, 3, "light color"),
                "direction": _float_list(frame.position, 3, "light direction"),
            }
            for frame in vmd_data.light_frames
        ]
        self_shadow_frames = [
            {
                "frame": int(frame.frame_number),
                "mode": int(frame.mode),
                "distance": float(frame.distance),
            }
            for frame in vmd_data.shadow_frames
        ]
        property_frames = [
            {
                "frame": int(frame.frame_number),
                "visible": bool(frame.visible),
                "ikStates": [
                    {"boneName": str(name), "enabled": bool(enabled)}
                    for name, enabled in frame.ik_states
                ],
            }
            for frame in vmd_data.ik_show_hide_frames
        ]
        max_frame = max(
            [0]
            + [frame["frame"] for frame in bone_frames]
            + [frame["frame"] for frame in morph_frames]
            + [frame["frame"] for frame in camera_frames]
            + [frame["frame"] for frame in light_frames]
            + [frame["frame"] for frame in self_shadow_frames]
            + [frame["frame"] for frame in property_frames]
        )
        return {
            "kind": "vmd",
            "metadata": {
                "format": "vmd",
                "modelName": vmd_data.header.model_name,
                "counts": {
                    "bones": len(bone_frames),
                    "morphs": len(morph_frames),
                    "cameras": len(camera_frames),
                    "lights": len(light_frames),
                    "selfShadows": len(self_shadow_frames),
                    "properties": len(property_frames),
                },
                "maxFrame": max_frame,
            },
            "boneFrames": bone_frames,
            "morphFrames": morph_frames,
            "cameraFrames": camera_frames,
            "lightFrames": light_frames,
            "selfShadowFrames": self_shadow_frames,
            "propertyFrames": property_frames,
        }

    def _try_native_export(self, vmd_data: VmdData) -> Optional[bytes]:
        if self._native_exporter is None:
            return None
        return self._native_exporter(self.to_native_json_payload(vmd_data))

    @staticmethod
    def _get_frames(data: Mapping[str, Any], key: str) -> Iterable[Any]:
        value = data.get(key, ())
        if value is None:
            return ()
        return value

    @staticmethod
    def _coerce_bone_frame(frame_data: Any) -> VmdBoneFrame:
        if isinstance(frame_data, VmdBoneFrame):
            return frame_data
        data = _require_mapping(frame_data, "bone frame")
        frame = VmdBoneFrame()
        frame.bone_name = str(data.get("bone_name", data.get("name", "")))
        frame.frame_number = int(data.get("frame_number", data.get("frame", 0)))
        frame.position = _float_tuple(data.get("position", (0.0, 0.0, 0.0)), 3, "bone position")
        frame.rotation = _float_tuple(data.get("rotation", (0.0, 0.0, 0.0, 1.0)), 4, "bone rotation")
        frame.interpolation = _bytes_value(data.get("interpolation", DEFAULT_BONE_INTERPOLATION), 64)
        return frame

    @staticmethod
    def _coerce_morph_frame(frame_data: Any) -> VmdMorphFrame:
        if isinstance(frame_data, VmdMorphFrame):
            return frame_data
        data = _require_mapping(frame_data, "morph frame")
        frame = VmdMorphFrame()
        frame.morph_name = str(data.get("morph_name", data.get("name", "")))
        frame.frame_number = int(data.get("frame_number", data.get("frame", 0)))
        frame.value = float(data.get("value", data.get("weight", 0.0)))
        return frame

    @staticmethod
    def _coerce_camera_frame(frame_data: Any) -> VmdCameraFrame:
        if isinstance(frame_data, VmdCameraFrame):
            return frame_data
        data = _require_mapping(frame_data, "camera frame")
        frame = VmdCameraFrame()
        frame.frame_number = int(data.get("frame_number", data.get("frame", 0)))
        frame.distance = float(data.get("distance", 0.0))
        frame.position = _float_tuple(data.get("position", (0.0, 0.0, 0.0)), 3, "camera position")
        frame.rotation = _float_tuple(data.get("rotation", (0.0, 0.0, 0.0)), 3, "camera rotation")
        frame.interpolation = _bytes_value(
            data.get("interpolation", DEFAULT_CAMERA_INTERPOLATION), 24
        )
        frame.viewing_angle = int(data.get("viewing_angle", data.get("view_angle", 0)))
        frame.perspective = int(data.get("perspective", 0))
        return frame

    @staticmethod
    def _coerce_light_frame(frame_data: Any) -> VmdLightFrame:
        if isinstance(frame_data, VmdLightFrame):
            return frame_data
        data = _require_mapping(frame_data, "light frame")
        frame = VmdLightFrame()
        frame.frame_number = int(data.get("frame_number", data.get("frame", 0)))
        frame.color = _float_tuple(data.get("color", (0.0, 0.0, 0.0)), 3, "light color")
        frame.position = _float_tuple(data.get("position", (0.0, 0.0, 0.0)), 3, "light position")
        return frame

    @staticmethod
    def _coerce_shadow_frame(frame_data: Any) -> VmdShadowFrame:
        if isinstance(frame_data, VmdShadowFrame):
            return frame_data
        data = _require_mapping(frame_data, "shadow frame")
        frame = VmdShadowFrame()
        frame.frame_number = int(data.get("frame_number", data.get("frame", 0)))
        frame.mode = int(data.get("mode", 0))
        frame.distance = float(data.get("distance", 0.0))
        return frame


def _require_mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{label} must be a mapping or matching VMD frame")
    return value


def _float_tuple(value: Any, length: int, label: str) -> tuple:
    try:
        result = tuple(float(item) for item in value)
    except TypeError as exc:
        raise TypeError(f"{label} must be an iterable of {length} numbers") from exc
    if len(result) != length:
        raise ValueError(f"{label} must contain {length} numbers")
    return result


def _float_list(value: Any, length: int, label: str) -> list:
    return list(_float_tuple(value, length, label))


def _bytes_value(value: Any, expected_length: int) -> bytes:
    if value is None:
        return b""
    result = bytes(value)
    if len(result) != expected_length:
        raise ValueError(f"interpolation must be {expected_length} bytes")
    return result


def _interpolation_bytes(value: Any, expected_length: int, fallback: bytes) -> bytes:
    result = bytes(value or b"")
    if len(result) == expected_length:
        return result
    return fallback
