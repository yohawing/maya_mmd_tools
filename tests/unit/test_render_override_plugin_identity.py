"""The GUI harness must use the requested plugin binary."""

import pytest

from tools.render_override.common import require_requested_plugin


def test_require_requested_plugin_rejects_different_canonical_binary(tmp_path):
    requested = tmp_path / "requested" / "mmd_tools_cpp.mll"
    loaded = tmp_path / "autoloaded" / "mmd_tools_cpp.mll"

    class Commands:
        @staticmethod
        def pluginInfo(plugin, **kwargs):
            if kwargs == {"query": True, "loaded": True}:
                return plugin == "mmd_tools_cpp"
            if kwargs == {"query": True, "path": True}:
                assert plugin == "mmd_tools_cpp"
                return str(loaded)
            raise AssertionError((plugin, kwargs))

    with pytest.raises(RuntimeError, match="differs from requested --plugin"):
        require_requested_plugin(Commands(), str(requested), lambda _message: None)


def test_require_requested_plugin_records_matching_canonical_binary(tmp_path):
    requested = tmp_path / "requested" / "mmd_tools_cpp.mll"
    log_messages = []

    class Commands:
        @staticmethod
        def pluginInfo(plugin, **kwargs):
            if kwargs == {"query": True, "loaded": True}:
                return plugin in {str(requested.resolve()), "mmd_tools_cpp"}
            if kwargs == {"query": True, "path": True}:
                assert plugin == "mmd_tools_cpp"
                return str(requested)
            raise AssertionError((plugin, kwargs))

    assert require_requested_plugin(
        Commands(), str(requested), log_messages.append
    ) == requested.resolve()
    assert any("loaded canonical plugin" in message for message in log_messages)
