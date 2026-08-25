"""Adapters for external Maya APIs."""

from .maya_cmds_adapter import MayaCmdsAdapter
from .native_authoring_command import NativeAuthoringCommandGateway

__all__ = [
    "MayaCmdsAdapter",
    "MayaVmdExportBackend",
    "NativeAuthoringCommandGateway",
]


def __getattr__(name):
    """Avoid importing the VMD host boundary while generic adapters load."""

    if name != "MayaVmdExportBackend":
        raise AttributeError(name)
    from .maya_vmd_prepare_backend import MayaVmdExportBackend

    globals()[name] = MayaVmdExportBackend
    return MayaVmdExportBackend
