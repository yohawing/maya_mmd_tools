"""Read-only composition for Morph authoring snapshots."""

from __future__ import annotations

from typing import Any, Sequence

from mmd_tools.adapters.maya_morph_read_projection import MayaMorphReadProjectionAdapter
from mmd_tools.adapters.maya_scene_metadata_backend import MayaSceneMetadataBackend
from mmd_tools.core.constants import ATTR_MMD_MODEL_NAME, ATTR_MMD_MODEL_NAME_EN
from mmd_tools.core.morph_read_projection import MorphAuthoringReadSnapshot
from mmd_tools.core.morph_topology import MorphTopologyInspection


class MayaMorphAuthoringSnapshotProvider:
    """Build one combined snapshot from the Presenter's Maya adapter."""

    def __init__(self, cmds_adapter: Any) -> None:
        self._cmds = cmds_adapter
        self._backend = MayaSceneMetadataBackend(cmds_adapter)

    def read_morph_authoring_snapshot(self, root: str) -> MorphAuthoringReadSnapshot:
        """Return the backend's strict semantic/runtime snapshot unchanged."""

        if self._has_mmd_model_marker(root):
            snapshot = self._backend.read_morph_authoring_snapshot(root)
        else:
            snapshot = MorphAuthoringReadSnapshot(
                spec=None,
                projection=MayaMorphReadProjectionAdapter(
                    self._cmds
                ).read_runtime_only_projection(root),
                topology_inspection=MorphTopologyInspection({}, {}, ()),
            )
        if not isinstance(snapshot, MorphAuthoringReadSnapshot):
            raise TypeError("invalid morph authoring snapshot")
        return snapshot

    def _has_mmd_model_marker(self, root: str) -> bool:
        return any(
            self._cmds.attribute_exists(attr, root)
            for attr in (ATTR_MMD_MODEL_NAME, ATTR_MMD_MODEL_NAME_EN)
        )

    def begin_morph_preview(self, root: str, targets: Sequence[str]) -> Any:
        """Open one fixed-target runtime-only preview transaction."""

        return self._backend.begin_morph_preview(root, targets)

    def update_morph_preview(self, session: Any, value: float) -> int:
        """Update an already captured preview target set."""

        try:
            return self._backend.apply_morph_preview(
                session.root,
                session,
                (value,) * len(session.targets),
            )
        except Exception:
            self._backend.rollback_morph_preview(session.root, session)
            raise

    def commit_morph_preview(self, session: Any) -> int:
        """Commit one fixed-target preview transaction."""

        try:
            return self._backend.commit_morph_preview(session.root, session)
        except Exception:
            self._backend.rollback_morph_preview(session.root, session)
            raise

    def rollback_morph_preview(self, session: Any) -> None:
        """Rollback one fixed-target preview transaction."""

        self._backend.rollback_morph_preview(session.root, session)

    def set_morph_preview(
        self,
        root: str,
        targets: Sequence[str],
        value: float,
    ) -> int:
        """Apply one synchronous preview value without rediscovery."""

        return self._apply_preview_value(root, targets, value)

    def reset_morph_preview(self, root: str, targets: Sequence[str]) -> int:
        """Reset one synchronous preview target set without rediscovery."""

        return self._apply_preview_value(root, targets, 0.0)

    def _apply_preview_value(
        self,
        root: str,
        targets: Sequence[str],
        value: float,
    ) -> int:
        session = self._backend.begin_morph_preview(root, targets)
        try:
            result = self._backend.apply_morph_preview(
                root,
                session,
                (value,) * len(session.targets),
            )
            self._backend.commit_morph_preview(root, session)
            return result
        except Exception:
            self._backend.rollback_morph_preview(root, session)
            raise


__all__ = ["MayaMorphAuthoringSnapshotProvider"]
