"""Context objects passed from VmdConverter into split VMD helper modules."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional, Tuple


@dataclass(frozen=True)
class VmdKeyingContext:
    """Minimal state needed by scene keying helpers."""

    logger: Any
    anim_layer: Optional[str]
    use_animation_layers: bool


@dataclass(frozen=True)
class VmdRuntimeRigContext:
    """Minimal state needed by runtime-rig scene helpers."""

    logger: Any
    bone_name_mapping: Mapping[str, str]
    bone_bind_poses: Mapping[str, Tuple[float, float, float]]
    runtime_joint_attrs: Callable[[], Tuple[str, str, str, str, str, str]]
