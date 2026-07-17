"""``mmd_tools.view.shader_override`` の Maya 非依存部分を検証するユニットテスト。

shader_override は Maya Viewport 2.0 プラグイン (MPxNode + MPxShaderOverride) であり、
描画・属性計算・プラグイン登録のほとんどは live Maya / VP2.0 を必要とする (= mayapy
GUI が要るためここでは **ブロッカー**)。

しかし以下は Maya の挙動を必要とせず純Python で検証できる:
- モジュールが (Maya API スタブ下で) import できること
- 公開定数 (SHADER_NODE_NAME / SHADER_FX_FILE) が安定していること
- MMDShaderOverride.__init__ がマテリアル既定値を設定し、shader_path を
  実在する .fx ファイルへ解決すること (パッケージ整合性)
- supportedDrawAPIs / handlesConsolidatedGeometry の純粋な戻り値
- initializePlugin / uninitializePlugin の登録順・rollback・二重解除防止

そのために ``maya.api.OpenMaya`` / ``OpenMayaRender`` を *継承可能な実クラスを持つ*
スタブとして sys.modules に登録する (MagicMock は基底クラスにできないため)。本物の
maya がある mayapy 環境ではスタブを入れず、import 自体が成立しない (VP2.0 GUI 必須)
箇所には触れない。

ブロッカー (本テストで検証しない / mayapy+VP2.0 必須):
- MMDShaderNode.compute / initialize (MDataBlock, MFnNumericAttribute 等)
- MMDShaderOverride.initialize / draw / updateDG / terminate
"""

import os
import sys
import unittest
from types import ModuleType
from unittest.mock import MagicMock, patch


# ----------------------------------------------------------------------
# Maya API スタブ (継承可能な実クラスを提供)
# ----------------------------------------------------------------------

def _usable_mpxnode_present() -> bool:
    """継承可能な (本物の) maya.api.OpenMaya.MPxNode が既にあるかを判定する。

    本物の maya (mayapy) では MPxNode は ``type`` (継承可能なクラス)。
    他テストが入れた MagicMock スタブでは MPxNode が MagicMock になり継承できない。

    shader_override は ``class MMDShaderNode(om.MPxNode)`` で継承するため、
    「MPxNode が継承可能な type かどうか」が唯一信頼できる判定基準になる。
    他テストの private stub クラスを isinstance で個別判定する方法は脆いため採らない。
    """
    om = sys.modules.get("maya.api.OpenMaya")
    if om is None:
        return False
    mpxnode = getattr(om, "MPxNode", None)
    # MagicMock は type のサブクラスではない。本物 / 我々のスタブ実クラスのみ type。
    return isinstance(mpxnode, type)


def _install_maya_api_stub_for_shader():
    """shader_override の import に必要な maya.api.* スタブを登録する。

    他テストが先に MagicMock ベースの maya.api.* を入れている場合でも、shader_override
    は MPxNode 等を *継承* するため、継承可能な実クラスを持つスタブで上書きする必要が
    ある。maya.cmds 等は他テストの設定を壊さないよう温存する。

    継承可能な MPxNode (本物の maya もしくは我々が既に入れたスタブ) が居る場合は
    False を返し何もしない。
    """
    if _usable_mpxnode_present():
        return False

    maya = sys.modules.get("maya")
    if not isinstance(maya, ModuleType):
        maya = ModuleType("maya")
        sys.modules["maya"] = maya
    # 既存の api を温存しつつ OpenMaya/OpenMayaRender を継承可能スタブで上書きする
    api = getattr(maya, "api", None)
    if not isinstance(api, ModuleType):
        api = ModuleType("maya.api")

    om = _StubModule("maya.api.OpenMaya")
    om.MPxNode = _StubMPxNode
    om.MTypeId = lambda *a, **k: ("MTypeId", a)
    om.MColor = _StubMColor
    om.MGlobal = _StubMGlobal
    om.MFnNumericAttribute = MagicMock(name="MFnNumericAttribute")
    om.MFnEnumAttribute = MagicMock(name="MFnEnumAttribute")
    om.MFnNumericData = MagicMock(name="MFnNumericData")
    om.MFnDependencyNode = MagicMock(name="MFnDependencyNode")
    om.MFnPlugin = MagicMock(name="MFnPlugin")

    omr = _StubModule("maya.api.OpenMayaRender")
    omr.MPxShaderOverride = _StubMPxShaderOverride
    omr.MRenderer = _StubMRenderer
    omr.MPassContext = _StubMPassContext
    omr.MDrawRegistry = MagicMock(name="MDrawRegistry")
    omr.MRasterizerState = MagicMock(name="MRasterizerState")
    omr.MRasterizerStateDesc = MagicMock(name="MRasterizerStateDesc")
    omr.MStateManager = MagicMock(name="MStateManager")

    api.OpenMaya = om
    api.OpenMayaRender = omr
    maya.api = api
    sys.modules["maya.api"] = api
    sys.modules["maya.api.OpenMaya"] = om
    sys.modules["maya.api.OpenMayaRender"] = omr
    return True


class _StubModule(ModuleType):
    """未定義属性に対し MagicMock を返すモジュールプロキシ。

    継承可能な実クラス (MPxNode 等) は明示的に属性へ設定し、それ以外の
    任意シンボルアクセスは MagicMock で吸収する。これにより他テストが入れた
    MagicMock ベース stub の上書きとなっても挙動の後退を起こさない。
    """

    def __getattr__(self, name):
        # ModuleType の通常属性 (__path__ 等) は通常解決され、ここには来ない
        value = MagicMock(name=f"{self.__name__}.{name}")
        setattr(self, name, value)
        return value


class _StubMPxNode:
    """継承可能な MPxNode 代替。kDependNode 定数を提供。"""

    kDependNode = 0

    def __init__(self, *args, **kwargs):
        pass


class _StubMPxShaderOverride:
    """継承可能な MPxShaderOverride 代替。__init__(obj) を受ける。"""

    def __init__(self, *args, **kwargs):
        pass

    @staticmethod
    def drawGeometry(context, item):
        pass


class _StubMColor:
    """(r, g, b[, a]) タプルを受け取り r/g/b 属性を持つ MColor 代替。"""

    def __init__(self, rgb):
        self.r, self.g, self.b = rgb[0], rgb[1], rgb[2]


class _StubMGlobal:
    @staticmethod
    def displayInfo(*_a, **_k):
        pass

    @staticmethod
    def displayWarning(*_a, **_k):
        pass

    @staticmethod
    def displayError(*_a, **_k):
        pass


class _StubMRenderer:
    # supportedDrawAPIs() が OR する API フラグ (int で OR 可能にする)
    kDirectX11 = 1
    kOpenGL = 2
    kOpenGLCoreProfile = 4


class _StubMPassContext:
    kColorPassName = "colorPass"


_install_maya_api_stub_for_shader()

from mmd_tools.view import shader_override  # noqa: E402


def _shader_override_uses_test_stub() -> bool:
    return shader_override.MMDShaderOverride.__mro__[1] is _StubMPxShaderOverride


def _skip_without_shader_override_stub():
    return unittest.skipUnless(
        _shader_override_uses_test_stub(),
        "MPxShaderOverride instance construction requires a VP2/Maya GUI object",
    )


class TestShaderModuleConstants(unittest.TestCase):
    def test_node_name_constant(self):
        self.assertEqual(shader_override.SHADER_NODE_NAME, "MMDShader")

    def test_fx_file_constant(self):
        self.assertEqual(shader_override.SHADER_FX_FILE, "shaders/MMDShader.fx")

    def test_node_class_name_matches_constant(self):
        self.assertEqual(
            shader_override.MMDShaderNode.kNodeName,
            shader_override.SHADER_NODE_NAME,
        )

    def test_drawdb_classification_format(self):
        self.assertEqual(
            shader_override.MMDShaderNode.drawDbClassification,
            "drawdb/shader/surface/MMDShader",
        )

    def test_hypershade_classification(self):
        self.assertEqual(
            shader_override.MMDShaderNode.classification,
            "shader/surface:drawdb/shader/surface/MMDShader",
        )


@_skip_without_shader_override_stub()
class TestShaderRegistrationLifecycle(unittest.TestCase):
    def setUp(self):
        shader_override._node_registered = False
        shader_override._override_registered = False
        self.plugin_fn = MagicMock(name="plugin_fn")
        self.plugin_patch = patch.object(
            shader_override.om,
            "MFnPlugin",
            return_value=self.plugin_fn,
        )
        self.plugin_patch.start()
        shader_override.omr.MDrawRegistry.reset_mock()
        shader_override.omr.MDrawRegistry.registerShaderOverrideCreator.side_effect = None
        shader_override.omr.MDrawRegistry.deregisterShaderOverrideCreator.side_effect = None

    def tearDown(self):
        self.plugin_patch.stop()
        shader_override._node_registered = False
        shader_override._override_registered = False

    def test_registration_uses_linked_drawdb_classification(self):
        calls = MagicMock()
        calls.attach_mock(self.plugin_fn.registerNode, "register_node")
        calls.attach_mock(
            shader_override.omr.MDrawRegistry.registerShaderOverrideCreator,
            "register_override",
        )

        shader_override.initializePlugin(object())

        self.assertEqual(
            [call[0] for call in calls.method_calls],
            ["register_node", "register_override"],
        )
        self.plugin_fn.registerNode.assert_called_once_with(
            shader_override.SHADER_NODE_NAME,
            shader_override.MMDShaderNode.kNodeId,
            shader_override.MMDShaderNode.creator,
            shader_override.MMDShaderNode.initialize,
            shader_override.om.MPxNode.kDependNode,
            shader_override.MMDShaderNode.classification,
        )
        shader_override.omr.MDrawRegistry.registerShaderOverrideCreator.assert_called_once_with(
            shader_override.MMDShaderNode.drawDbClassification,
            shader_override.SHADER_OVERRIDE_REGISTRANT_ID,
            shader_override.MMDShaderOverride.creator,
        )
        self.assertTrue(shader_override._node_registered)
        self.assertTrue(shader_override._override_registered)

    def test_override_registration_failure_rolls_back_node_once(self):
        shader_override.omr.MDrawRegistry.registerShaderOverrideCreator.side_effect = RuntimeError(
            "kInvalidParameter"
        )

        with self.assertRaisesRegex(RuntimeError, "kInvalidParameter"):
            shader_override.initializePlugin(object())

        self.plugin_fn.deregisterNode.assert_called_once_with(shader_override.MMDShaderNode.kNodeId)
        self.assertFalse(shader_override._node_registered)
        self.assertFalse(shader_override._override_registered)

        shader_override.uninitializePlugin(object())
        self.plugin_fn.deregisterNode.assert_called_once()
        shader_override.omr.MDrawRegistry.deregisterShaderOverrideCreator.assert_not_called()

    def test_uninitialize_deregisters_registered_parts_in_reverse_order_once(self):
        shader_override.initializePlugin(object())
        calls = MagicMock()
        calls.attach_mock(
            shader_override.omr.MDrawRegistry.deregisterShaderOverrideCreator,
            "deregister_override",
        )
        calls.attach_mock(self.plugin_fn.deregisterNode, "deregister_node")

        shader_override.uninitializePlugin(object())
        shader_override.uninitializePlugin(object())

        self.assertEqual(
            [call[0] for call in calls.method_calls],
            ["deregister_override", "deregister_node"],
        )
        self.assertFalse(shader_override._node_registered)
        self.assertFalse(shader_override._override_registered)


class TestShaderFxPackaging(unittest.TestCase):
    """SHADER_FX_FILE が実在し、__init__ がそこへ解決することを検証する。"""

    def test_fx_file_exists_in_package(self):
        view_dir = os.path.dirname(shader_override.__file__)
        fx_path = os.path.normpath(
            os.path.join(view_dir, "..", shader_override.SHADER_FX_FILE)
        )
        self.assertTrue(
            os.path.isfile(fx_path),
            f"Shader .fx file missing (packaging regression): {fx_path}",
        )


@_skip_without_shader_override_stub()
class TestShaderOverrideLifecycleDebugLogging(unittest.TestCase):
    """MMDShaderOverride の冗長ライフサイクル診断が logger.debug 経由であること。

    Script Editor を汚染していた displayInfo 呼び出しが、通常パスの 7 箇所で
    logger.debug に置き換わったことをスタブ下で検証する (実 VP2 は不要)。
    """

    def test_lifecycle_diagnostics_use_logger_debug_not_display_info(self):
        fake_shader = MagicMock(name="fake_effects_shader")
        fake_shader_mgr = MagicMock(name="fake_shader_manager")
        fake_shader_mgr.getEffectsFileShader.return_value = fake_shader

        with patch.object(
            shader_override.logger, "debug", autospec=True
        ) as mock_debug, patch.object(
            shader_override.om.MGlobal, "displayInfo", autospec=True
        ) as mock_display_info, patch.object(
            shader_override.omr.MRenderer,
            "getShaderManager",
            return_value=fake_shader_mgr,
            create=True,
        ):
            override = shader_override.MMDShaderOverride(None)
            override.initialize(None, None)
            # null / falsy object: log then early-return without reading plugs
            override.updateDG(None)
            override.activateKey(None, None)
            # empty renderables: logs count only (draw body is a no-op)
            override.draw(None, [])

        self.assertEqual(mock_display_info.call_count, 0)

        # call[0] is args tuple (Py3.7-safe; _Call.args is 3.8+)
        debug_messages = [call[0][0] for call in mock_debug.call_args_list]
        self.assertEqual(
            debug_messages,
            [
                "MMDShaderOverride: Loading shader from %s",
                "MMDShaderOverride: initialize() called",
                "MMDShaderOverride: Loading shader from %s",
                "MMDShaderOverride: Shader loaded successfully",
                "MMDShaderOverride: updateDG() called",
                "MMDShaderOverride: activateKey() called",
                "MMDShaderOverride: draw() called with %s renderables",
            ],
        )
        # draw() passes the renderable count as the %-format argument
        draw_call = mock_debug.call_args_list[6]
        self.assertEqual(draw_call[0][1], 0)


if __name__ == "__main__":
    unittest.main()
