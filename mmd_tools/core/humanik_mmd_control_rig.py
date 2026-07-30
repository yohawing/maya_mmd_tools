"""HumanIK/MMD-native Control Rig ownership interop.

HumanIK's overlay rig and the MMD-native Control Rig have independent node
graphs, but they can still target the same imported joints.  This module keeps
their boundary explicit by treating the UUID-validated MMD metadata as the
authority for the native rig's motion owner.  HumanIK may temporarily lease a
native rig only while it is in the display/attached ``MMD_OWNED`` state; any
authoring, bake, conversion, or malformed metadata is rejected before a HIK
operation mutates the scene.

The read is deliberately lazy and Maya-independent so the HumanIK unit tests
can run without a Maya process.  A missing Maya runtime is reported as an
unavailable scene (allowed for host-neutral callers), while an error from an
injected/real scene command module fails closed as invalid metadata.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional

from mmd_tools.core.constants import ATTR_MMD_CONTROL_RIG_JSON
from mmd_tools.core.humanik_utils import maya_cmds


MMD_INTEROP_ATTACHED = "ATTACHED"
MMD_INTEROP_BAKED = "BAKED"
MMD_INTEROP_EDIT = "EDIT"
MMD_INTEROP_MMD_OWNED = "MMD_OWNED"
MMD_INTEROP_CONTROL_OWNED = "CONTROL_OWNED"
MMD_INTEROP_CONVERTING = "CONVERTING"


def _node_uuid(cmds, node: str) -> Optional[str]:
    """Return one concrete Maya UUID, or ``None`` when identity is unknown."""
    try:
        values = cmds.ls(str(node), uuid=True) or []
    except Exception:
        return None
    return str(values[0]) if len(values) == 1 and values[0] else None


@dataclass(frozen=True)
class HumanIkMmdControlRigInterop:
    """Read-only ownership lease for one model's native Control Rig."""

    model_root: str
    present: bool
    allowed: bool
    state: Optional[str] = None
    owner: Optional[str] = None
    reason: str = "no_native_control_rig"
    scene_available: bool = True
    metadata_error: Optional[str] = None
    model_root_uuid: Optional[str] = None

    @property
    def lease(self) -> str:
        """Return the stable lease label used by diagnostics and transactions."""
        if not self.present:
            return "none"
        if self.allowed:
            return "overlay_isolation"
        return "blocked"

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-safe ownership snapshot."""
        return {
            "modelRoot": self.model_root,
            "present": bool(self.present),
            "allowed": bool(self.allowed),
            "state": self.state,
            "owner": self.owner,
            "reason": self.reason,
            "lease": self.lease,
            "sceneAvailable": bool(self.scene_available),
            "metadataError": self.metadata_error,
            "modelRootUuid": self.model_root_uuid,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "HumanIkMmdControlRigInterop":
        """Validate a persisted interop snapshot without guessing ownership."""
        if not isinstance(payload, Mapping):
            raise ValueError("HumanIK MMD Control Rig interop must be an object")
        model_root = payload.get("modelRoot")
        if not isinstance(model_root, str) or not model_root:
            raise ValueError("HumanIK MMD Control Rig interop modelRoot is invalid")
        booleans = ("present", "allowed", "sceneAvailable")
        if any(not isinstance(payload.get(key), bool) for key in booleans):
            raise ValueError("HumanIK MMD Control Rig interop boolean field is invalid")
        state = payload.get("state")
        owner = payload.get("owner")
        reason = payload.get("reason", "")
        error = payload.get("metadataError")
        model_root_uuid = payload.get("modelRootUuid")
        scene_available = bool(payload["sceneAvailable"])
        if model_root_uuid is not None and (
            not isinstance(model_root_uuid, str) or not model_root_uuid
        ):
            raise ValueError("HumanIK MMD Control Rig interop modelRootUuid is invalid")
        if scene_available and model_root_uuid is None:
            raise ValueError(
                "HumanIK MMD Control Rig interop modelRootUuid is required"
            )
        if state is not None and not isinstance(state, str):
            raise ValueError("HumanIK MMD Control Rig interop state is invalid")
        if owner is not None and not isinstance(owner, str):
            raise ValueError("HumanIK MMD Control Rig interop owner is invalid")
        if not isinstance(reason, str) or not reason:
            raise ValueError("HumanIK MMD Control Rig interop reason is invalid")
        if error is not None and not isinstance(error, str):
            raise ValueError("HumanIK MMD Control Rig interop metadataError is invalid")
        result = cls(
            model_root=model_root,
            present=bool(payload["present"]),
            allowed=bool(payload["allowed"]),
            state=state,
            owner=owner,
            reason=reason,
            scene_available=scene_available,
            metadata_error=error,
            model_root_uuid=model_root_uuid,
        )
        # Persisted state is evidence only.  Do not allow an inconsistent
        # payload to silently become a lease on scene reopen.
        if not result.present and (result.state is not None or result.owner is not None):
            raise ValueError("HumanIK MMD Control Rig interop absent row has state/owner")
        if result.allowed and result.present and (
            result.state != MMD_INTEROP_ATTACHED
            or result.owner != MMD_INTEROP_MMD_OWNED
        ):
            raise ValueError("HumanIK MMD Control Rig interop allowed row is not ATTACHED/MMD_OWNED")
        return result


def inspect_humanik_mmd_control_rig_interop(
    model_root: str,
    *,
    cmds_module=None,
) -> HumanIkMmdControlRigInterop:
    """Inspect native Control Rig ownership without changing the scene.

    ``ATTACHED/MMD_OWNED`` is the only state with no active control-writer
    route.  It is therefore safe for HumanIK to isolate any incoming MMD
    writer during its own transaction and restore it afterwards.  ``BAKED``
    is intentionally blocked as it can retain authored controller animation
    even though its owner label has returned to ``MMD_OWNED``.
    """
    root = str(model_root or "").strip()
    if not root:
        raise ValueError("model_root is required")

    if cmds_module is None:
        try:
            cmds = maya_cmds()
        except Exception:
            return HumanIkMmdControlRigInterop(
                model_root=root,
                present=False,
                allowed=True,
                reason="scene_unavailable",
                scene_available=False,
            )
    else:
        cmds = cmds_module
    root_uuid = _node_uuid(cmds, root)

    # A model binding can be represented by a synthetic root in host-neutral
    # frontend tests, and a stale scene binding can point at a deleted root.
    # In both cases there cannot be a native MMD Control Rig to lease.  Maya's
    # ``attributeQuery`` raises for a missing node, so establish the node's
    # existence first and treat a concrete ``False`` as an absent rig rather
    # than misclassifying it as malformed metadata.  Lightweight command
    # doubles may omit ``objExists``; leave those to the metadata probe below.
    obj_exists = getattr(cmds, "objExists", None)
    if callable(obj_exists):
        try:
            exists = obj_exists(root)
        except Exception:
            exists = None
        if isinstance(exists, bool) and not exists:
            return HumanIkMmdControlRigInterop(
                model_root=root,
                present=False,
                allowed=True,
                reason="no_native_control_rig",
                scene_available=True,
                model_root_uuid=root_uuid,
            )

    # Lightweight command doubles (and a batch host before the development
    # rig module has loaded) may not expose ``attributeQuery``.  In that case
    # there is no authoritative metadata plug to inspect, so report the
    # native rig as absent.  Real Maya ``cmds`` always provides this query;
    # once the plug exists, all read/validation errors remain fail-closed.
    attribute_query = getattr(cmds, "attributeQuery", None)
    if not callable(attribute_query):
        return HumanIkMmdControlRigInterop(
            model_root=root,
            present=False,
            allowed=True,
            reason="no_native_control_rig",
            scene_available=True,
            model_root_uuid=root_uuid,
        )
    try:
        has_metadata_attr = attribute_query(
            ATTR_MMD_CONTROL_RIG_JSON, node=root, exists=True
        )
        # A bare MagicMock (the minimal Maya import stub used by several
        # headless unit modules) is not scene evidence.  Real ``cmds`` returns
        # a concrete bool for ``attributeQuery(..., exists=True)``.
        if not isinstance(has_metadata_attr, bool):
            return HumanIkMmdControlRigInterop(
                model_root=root,
                present=False,
                allowed=True,
                reason="scene_unavailable",
                scene_available=False,
                model_root_uuid=root_uuid,
            )
        if not has_metadata_attr:
            return HumanIkMmdControlRigInterop(
                model_root=root,
                present=False,
                allowed=True,
                reason="no_native_control_rig",
                scene_available=True,
                model_root_uuid=root_uuid,
            )
    except Exception as exc:
        return HumanIkMmdControlRigInterop(
            model_root=root,
            present=True,
            allowed=False,
            reason="metadata_invalid",
            scene_available=True,
            metadata_error=str(exc),
            model_root_uuid=root_uuid,
        )

    try:
        # Lazy import avoids a HumanIK -> MMD builder import at module load and
        # keeps this helper usable when the development rig module is absent.
        from mmd_tools.core.mmd_control_rig_builder import read_mmd_control_rig_metadata

        metadata = read_mmd_control_rig_metadata(root, cmds_module=cmds)
    except Exception as exc:  # malformed/stale metadata is fail-closed
        return HumanIkMmdControlRigInterop(
            model_root=root,
            present=True,
            allowed=False,
            reason="metadata_invalid",
            scene_available=True,
            metadata_error=str(exc),
            model_root_uuid=root_uuid,
        )

    if metadata is None:
        return HumanIkMmdControlRigInterop(
            model_root=root,
            present=False,
            allowed=True,
            reason="no_native_control_rig",
            scene_available=True,
            model_root_uuid=root_uuid,
        )
    if not isinstance(metadata, Mapping):
        return HumanIkMmdControlRigInterop(
            model_root=root,
            present=True,
            allowed=False,
            reason="metadata_invalid",
            scene_available=True,
            metadata_error="metadata is not an object",
            model_root_uuid=root_uuid,
        )
    if not root_uuid or metadata.get("modelRootUuid") != root_uuid:
        return HumanIkMmdControlRigInterop(
            model_root=root,
            present=True,
            allowed=False,
            reason="metadata_invalid",
            scene_available=True,
            metadata_error="modelRootUuid mismatch",
            model_root_uuid=root_uuid,
        )

    state = metadata.get("state")
    owner = metadata.get("owner")
    if state == MMD_INTEROP_ATTACHED and owner == MMD_INTEROP_MMD_OWNED:
        return HumanIkMmdControlRigInterop(
            model_root=root,
            present=True,
            allowed=True,
            state=state,
            owner=owner,
            reason="attached_mmd_owned",
            scene_available=True,
            model_root_uuid=root_uuid,
        )

    return HumanIkMmdControlRigInterop(
        model_root=root,
        present=True,
        allowed=False,
        state=str(state) if state is not None else None,
        owner=str(owner) if owner is not None else None,
        reason="native_control_rig_owned",
        scene_available=True,
        model_root_uuid=root_uuid,
    )


def require_humanik_mmd_control_rig_interop(
    model_root: str,
    *,
    cmds_module=None,
) -> HumanIkMmdControlRigInterop:
    """Return the interop lease or reject before any HIK scene mutation."""
    report = inspect_humanik_mmd_control_rig_interop(
        model_root,
        cmds_module=cmds_module,
    )
    if not report.allowed:
        details = []
        if report.state is not None:
            details.append(f"state={report.state}")
        if report.owner is not None:
            details.append(f"owner={report.owner}")
        if report.metadata_error:
            details.append(f"error={report.metadata_error}")
        suffix = f" ({', '.join(details)})" if details else ""
        raise RuntimeError(
            "HumanIK overlay is blocked by the MMD-native Control Rig ownership "
            f"contract for {report.model_root}: {report.reason}{suffix}"
        )
    return report


__all__ = [
    "HumanIkMmdControlRigInterop",
    "inspect_humanik_mmd_control_rig_interop",
    "require_humanik_mmd_control_rig_interop",
]
