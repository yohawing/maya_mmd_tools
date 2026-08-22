"""Auto-skip Maya-dependent tests when running outside real Maya (mayapy).

When running ``pytest tests/unit/`` outside Maya, importing modules that touch
``maya.cmds`` would otherwise fail at collection time. We install the Maya
stub early so mixed modules can be collected, then skip only the test classes
that explicitly inherit the live-scene base.
"""

from unittest.mock import MagicMock

import pytest

from tests.common.maya_stub import install_maya_stub, _is_real_maya_present

_real_maya = _is_real_maya_present()
if not _real_maya:
    install_maya_stub(profile="headless")

# All headless Qt tests construct production widgets in-process.  Redirect
# QSettings before test modules are imported so their production class aliases
# cannot bind the Windows native backend first.
from tests.common.qsettings_isolation import activate_qsettings_isolation  # noqa: E402

activate_qsettings_isolation()


def _uses_real_maya_class(cls):
    """Return True when a collected test class expects a live Maya scene."""
    if cls is None:
        return False
    for base in cls.__mro__:
        if base.__name__ == "MayaTestBase":
            return True
    if cls.__name__.endswith("Maya"):
        return True
    return False


def _uses_real_maya_function(item):
    """Return True for module-level tests that directly use the cmds stub."""
    if getattr(item, "cls", None) is not None:
        return False
    module = getattr(item, "module", None)
    if module is None:
        return False
    cmds = getattr(module, "cmds", None)
    return isinstance(cmds, MagicMock)


def _item_uses_real_maya(item):
    """Return True if a collected pytest item should require mayapy."""
    if _uses_real_maya_class(getattr(item, "cls", None)):
        return True
    return _uses_real_maya_function(item)


def pytest_collection_modifyitems(config, items):
    if _real_maya:
        return
    skip_maya = pytest.mark.skip(reason="requires real Maya (not stub)")
    for item in items:
        if _item_uses_real_maya(item):
            item.add_marker(skip_maya)


@pytest.fixture(autouse=True)
def _install_headless_vmd_parts_oracle(monkeypatch):
    """Keep headless tests independent of a platform-native runtime DLL."""

    if _real_maya:
        yield
        return
    from mmd_tools.actions import vmd_sibling_stage
    from tests.common.vmd_parts_export_oracle import export_vmd_from_parts_oracle

    monkeypatch.setattr(
        vmd_sibling_stage,
        "export_vmd_from_parts",
        export_vmd_from_parts_oracle,
    )
    yield
