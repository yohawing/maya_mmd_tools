"""Runtime bake source resolution helpers for VMD conversion."""

import os
from pathlib import Path
from typing import Optional, Tuple

import maya.cmds as cmds


def should_use_mmd_runtime_bake(
    converter,
    vmd_bytes: bytes,
    pmx_bytes: bytes,
    pmx_path: str,
    has_runtime: bool,
    runtime_available,
    live_rig_target: bool = False,
    bake_mode: bool = False,
) -> bool:
    """Return True for Bake mode final-pose import, False for live Rig mode."""
    if not bake_mode:
        return False
    if live_rig_target:
        converter.logger.info("Bake mode requested; live MMD rig outputs will be disabled for runtime bake")
    if not (has_runtime and runtime_available()):
        return False

    has_vmd = bool(vmd_bytes)
    if bool(pmx_bytes):
        has_pmx = True
    else:
        has_pmx = bool(pmx_path) and Path(pmx_path).suffix.lower() == ".pmx" and os.path.exists(pmx_path)
    return bool(has_vmd and has_pmx)


def resolve_runtime_bake_sources(
    converter,
    vmd_data,
    vmd_bytes: bytes,
    pmx_bytes: bytes,
    pmx_path: str,
    target_namespace: str = None,
) -> Tuple[bytes, bytes, str]:
    """Restore missing runtime bake inputs from VMD data and scene metadata."""
    resolved_vmd_bytes = vmd_bytes
    if not resolved_vmd_bytes:
        vmd_source = getattr(vmd_data, "source_file", None)
        if vmd_source and os.path.exists(vmd_source):
            try:
                with open(vmd_source, "rb") as file:
                    resolved_vmd_bytes = file.read()
                converter.logger.info(f"Restored VMD bytes for runtime bake from VMD source_file: {vmd_source}")
            except Exception as exc:
                converter.logger.debug(f"Failed to read VMD source_file: {vmd_source}: {exc}")

    resolved_pmx_path = pmx_path
    if not pmx_bytes and not resolved_pmx_path:
        resolved_pmx_path = resolve_pmx_path_from_scene(converter, target_namespace)

    return resolved_vmd_bytes, pmx_bytes, resolved_pmx_path


def resolve_pmx_path_from_scene(converter, target_namespace: str = None) -> Optional[str]:
    """Find a PMX source path stored on an MMD model root in the scene."""
    candidates = []
    for attr in cmds.ls("*.mmd_source_file", objectsOnly=False) or []:
        node = attr.rsplit(".", 1)[0]
        if target_namespace:
            node_namespace = node.rsplit(":", 1)[0] if ":" in node else ""
            if node_namespace != target_namespace:
                continue
        try:
            stored = cmds.getAttr(attr)
        except Exception:
            continue
        if not stored:
            continue
        if Path(str(stored)).suffix.lower() != ".pmx":
            continue
        if os.path.exists(stored):
            candidates.append(str(stored))

    if len(candidates) == 1:
        converter.logger.info(f"Restored PMX source from scene mmd_source_file: {candidates[0]}")
        return candidates[0]
    if len(candidates) > 1:
        converter.logger.warning(
            "runtime bake 用 PMX source が複数見つかったため自動復元をスキップします: "
            + ", ".join(candidates)
        )
    return None
