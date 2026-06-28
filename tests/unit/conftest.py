"""Auto-skip Maya-dependent tests when running outside real Maya (mayapy).

When running `pytest tests/unit/` outside Maya, test modules that import
`maya.cmds` would fail to collect. We install the maya stub early so they
can be collected, then mark them as skipped since the stub doesn't provide
real return values.
"""

from unittest.mock import MagicMock

import pytest

from tests.common.maya_stub import install_maya_stub, _is_real_maya_present

_real_maya = _is_real_maya_present()
if not _real_maya:
    install_maya_stub(profile="headless")


def _module_uses_real_maya(module):
    """Return True if a test module expects real Maya (imports cmds as MagicMock)."""
    cmds = getattr(module, "cmds", None)
    if isinstance(cmds, MagicMock):
        return True
    for attr in vars(module).values():
        if isinstance(attr, type):
            for base in attr.__mro__:
                if base.__name__ == "MayaTestBase":
                    return True
    return False


def pytest_collection_modifyitems(config, items):
    if _real_maya:
        return
    skip_maya = pytest.mark.skip(reason="requires real Maya (not stub)")
    seen_modules = {}
    for item in items:
        mod = getattr(item, "module", None)
        if mod is None:
            continue
        mod_name = mod.__name__
        if mod_name not in seen_modules:
            seen_modules[mod_name] = _module_uses_real_maya(mod)
        if seen_modules[mod_name]:
            item.add_marker(skip_maya)
