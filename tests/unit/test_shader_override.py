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

そのために ``maya.api.OpenMaya`` / ``OpenMayaRender`` を *継承可能な実クラスを持つ*
スタブとして sys.modules に登録する (MagicMock は基底クラスにできないため)。本物の
maya がある mayapy 環境ではスタブを入れず、import 自体が成立しない (VP2.0 GUI 必須)
箇所には触れない。

ブロッカー (本テストで検証しない / mayapy+VP2.0 必須):
- MMDShaderNode.compute / initialize (MDataBlock, MFnNumericAttribute 等)
- MMDShaderOverride.initialize / draw / updateDG / terminate
- initializePlugin / uninitializePlugin (MFnPlugin, MDrawRegistry 登録)
"""

import os
import sys
import unittest
from types import ModuleType
from unittest.mock import MagicMock


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
    """継承可能な MPxNode 代替。kDependNode 定数のみ提供。"""

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
        self.assertEqual(shader_override.MMDShaderNode.classification, "shader/surface")


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
    def test_override_resolves_shader_path_to_existing_fx(self):
        override = shader_override.MMDShaderOverride(object())
        self.assertTrue(
            os.path.isfile(override.shader_path),
            f"MMDShaderOverride.shader_path does not exist: {override.shader_path}",
        )


class TestShaderOverrideInit(unittest.TestCase):
    """MMDShaderOverride.__init__ の純粋な既定値設定を検証する。"""

    def setUp(self):
        if not _shader_override_uses_test_stub():
            self.skipTest("MPxShaderOverride instance construction requires a VP2/Maya GUI object")
        self.override = shader_override.MMDShaderOverride(object())

    def test_initial_shader_is_none(self):
        self.assertIsNone(self.override.shader)

    def test_default_material_scalars(self):
        self.assertEqual(self.override.shininess, 1.0)
        self.assertEqual(self.override.edge_size, 0.01)
        self.assertEqual(self.override.sphere_mode, 0)

    def test_default_diffuse_color(self):
        c = self.override.diffuse_color
        self.assertEqual((c.r, c.g, c.b), (0.8, 0.8, 0.8))

    def test_default_edge_color_is_black(self):
        c = self.override.edge_color
        self.assertEqual((c.r, c.g, c.b), (0.0, 0.0, 0.0))


class TestShaderOverridePureMethods(unittest.TestCase):
    def setUp(self):
        if not _shader_override_uses_test_stub():
            self.skipTest("MPxShaderOverride instance construction requires a VP2/Maya GUI object")
        self.override = shader_override.MMDShaderOverride(object())

    def test_supported_draw_apis_ors_all_three(self):
        result = self.override.supportedDrawAPIs()
        self.assertEqual(result, 1 | 2 | 4)

    def test_handles_consolidated_geometry_true(self):
        self.assertTrue(self.override.handlesConsolidatedGeometry())

    def test_activate_key_returns_true(self):
        self.assertTrue(self.override.activateKey(object(), object()))

    def test_creator_returns_instance(self):
        inst = shader_override.MMDShaderOverride.creator(object())
        self.assertIsInstance(inst, shader_override.MMDShaderOverride)


if __name__ == "__main__":
    unittest.main()
