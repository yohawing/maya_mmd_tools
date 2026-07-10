"""Runtime bake source resolution helpers for VMD conversion."""

import os
from pathlib import Path
from typing import Callable, List, Optional, Tuple

import maya.cmds as cmds

from ..core.import_strategy import resolve_vmd_runtime_bake_strategy
from ..core.namespace_utils import NamespaceUtils


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
    if live_rig_target:
        converter.logger.info("Bake mode requested; live MMD rig outputs will be disabled for runtime bake")
    strategy = resolve_vmd_runtime_bake_strategy(
        vmd_bytes=vmd_bytes,
        pmx_bytes=pmx_bytes,
        pmx_path=pmx_path,
        has_runtime=has_runtime,
        runtime_available=runtime_available,
        bake_mode=bake_mode,
    )
    converter.logger.info("VMD import strategy: runtime_bake=%s (%s)", strategy.use_runtime_bake, strategy.reason)
    return strategy.use_runtime_bake


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
                converter.logger.debug(f"Restored VMD bytes for runtime bake from VMD source_file: {vmd_source}")
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
            if NamespaceUtils.get_namespace_from_node(node) != target_namespace:
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
        converter.logger.debug(f"Restored PMX source from scene mmd_source_file: {candidates[0]}")
        return candidates[0]
    if len(candidates) > 1:
        converter.logger.warning(
            "runtime bake 用 PMX source が複数見つかったため自動復元をスキップします: "
            + ", ".join(candidates)
        )
    return None


def resolve_runtime_pmx_bytes_and_morph_names(
    pmx_bytes: bytes,
    pmx_path: str,
    logger,
    parse_pmx: Callable,
) -> Tuple[Optional[bytes], List[str]]:
    """Resolve PMX bytes and best-effort PMX morph names for runtime bake."""
    resolved_pmx_bytes = pmx_bytes
    if not resolved_pmx_bytes and pmx_path and os.path.exists(pmx_path):
        try:
            with open(pmx_path, "rb") as file:
                resolved_pmx_bytes = file.read()
        except Exception as exc:
            logger.error(f"Failed to read PMX file: {pmx_path} - {exc}")
            return None, []

    pmx_morph_names = []
    if pmx_path and os.path.exists(pmx_path):
        try:
            pmx_data = parse_pmx(pmx_path)
            if pmx_data is not None:
                pmx_morph_names = [morph.name for morph in pmx_data.morphs]
        except Exception as exc:
            logger.warning(f"Failed to get PMX morph names for runtime morph bake: {exc}")

    return resolved_pmx_bytes, pmx_morph_names
