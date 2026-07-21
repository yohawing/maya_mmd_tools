"""Small shared helpers reused across ``mmd_tools.core.humanik_*`` modules.

This module holds the genuinely duplicated, semantics-identical pieces that
were previously copy-pasted into every ``humanik_*`` module: lazy ``maya``
module accessors, MEL string quoting, and the incoming-source-plug lookup
used by both the transaction journal and the stance transaction.

Every ``humanik_*`` module keeps its own per-function ``cmds_module=None`` /
``mel_module=None`` test-injection parameters; only the fallback
implementation used when a caller omits them lives here now.
"""

from __future__ import annotations

from typing import List


def maya_cmds():
    """Return the ``maya.cmds`` module, imported lazily.

    Kept as a function (rather than a module-level import) so this module
    stays importable outside a Maya process; callers pass an injected
    ``cmds_module`` in tests instead of calling this directly.
    """
    from maya import cmds

    return cmds


def maya_mel():
    """Return the ``maya.mel`` module, imported lazily.

    See :func:`maya_cmds` for why this stays a lazy accessor.
    """
    from maya import mel

    return mel


def mel_string(value: str) -> str:
    """Quote a Python string as a MEL string literal.

    Backslashes and double quotes are escaped so the result is safe to embed
    directly in an ``mel.eval(...)`` command string.
    """
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def incoming_sources(cmds, destination: str) -> List[str]:
    """Return the sorted incoming source plugs connected to ``destination``.

    Args:
        cmds: Maya ``cmds``-compatible module.
        destination: Destination plug (``node.attr``) to query.

    Returns:
        Sorted, stringified source plugs, or an empty list when nothing is
        connected.
    """
    return sorted(
        str(source)
        for source in (cmds.listConnections(destination, source=True, destination=False, plugs=True) or [])
    )
