#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
物理コンバータの値クランプ処理テスト（pure-python / Maya 非依存）。

PMX 剛体の減衰パラメータ（rotation_attenuation 等）は Maya Bullet の
許容範囲（多くは [0,1]）を超えることがあり、そのまま setAttr すると
RuntimeError で剛体作成全体が失敗していた。``_clamp_to_range`` /
``_set_attr_clamped`` が値を属性の有効範囲へ丸めることを検証する。

NOTE: physics_converter は ``import maya.cmds`` をトップレベルで行うため、
CI（Maya 非依存）では sys.modules に stub を仕込んでからインポートする。
"""

from __future__ import annotations

import sys
import types
import unittest
from unittest.mock import MagicMock


class _StubModule(types.ModuleType):
    """欠落属性に対して MagicMock を返すモジュールプロキシ。"""

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return MagicMock()


def _seed_maya_modules():
    """maya 系モジュールが無い環境向けに最小 stub を sys.modules へ仕込む。"""
    try:
        import maya.cmds  # noqa: F401

        return
    except ImportError:
        pass

    maya_mod = types.ModuleType("maya")
    maya_mod.__path__ = ["maya"]
    sys.modules["maya"] = maya_mod

    maya_cmds = types.ModuleType("maya.cmds")
    sys.modules["maya.cmds"] = maya_cmds

    maya_api = types.ModuleType("maya.api")
    maya_api.__path__ = ["maya/api"]
    sys.modules["maya.api"] = maya_api

    for sub in ("OpenMaya", "OpenMayaAnim", "OpenMayaRender"):
        full_name = f"maya.api.{sub}"
        sys.modules[full_name] = _StubModule(full_name)

    for sub_name in ("maya.mel", "maya.standalone", "maya.OpenMayaUI"):
        sys.modules.setdefault(sub_name, _StubModule(sub_name))


_seed_maya_modules()

from mmd_tools.converters import physics_converter  # noqa: E402
from mmd_tools.converters.physics_converter import (  # noqa: E402
    _clamp_to_range,
    _mmd_collision_group_to_bullet_filter_group,
    _mmd_collision_mask_to_bullet_filter_mask,
)


class TestClampToRange(unittest.TestCase):
    """純粋なクランプ関数のテスト。"""

    def test_value_within_range_unchanged(self):
        self.assertEqual(_clamp_to_range(0.5, 0.0, 1.0), 0.5)

    def test_value_above_max_clamped(self):
        # rotation_attenuation が 1.0 を超えるケース（実モデルで発生）
        self.assertEqual(_clamp_to_range(1.5, 0.0, 1.0), 1.0)

    def test_value_below_min_clamped(self):
        self.assertEqual(_clamp_to_range(-0.3, 0.0, 1.0), 0.0)

    def test_no_min(self):
        self.assertEqual(_clamp_to_range(-5.0, None, 1.0), -5.0)
        self.assertEqual(_clamp_to_range(5.0, None, 1.0), 1.0)

    def test_no_max(self):
        self.assertEqual(_clamp_to_range(5.0, 0.0, None), 5.0)
        self.assertEqual(_clamp_to_range(-5.0, 0.0, None), 0.0)

    def test_no_bounds(self):
        self.assertEqual(_clamp_to_range(123.456, None, None), 123.456)


class TestCollisionFilterConversion(unittest.TestCase):
    """MMD collision group/mask から Maya Bullet filter 値への変換を検証。"""

    def test_group_index_converts_to_bullet_bit(self):
        self.assertEqual(_mmd_collision_group_to_bullet_filter_group(0), 0x0001)
        self.assertEqual(_mmd_collision_group_to_bullet_filter_group(1), 0x0002)
        self.assertEqual(_mmd_collision_group_to_bullet_filter_group(15), 0x8000)

    def test_group_index_is_clamped(self):
        self.assertEqual(_mmd_collision_group_to_bullet_filter_group(-1), 0x0001)
        self.assertEqual(_mmd_collision_group_to_bullet_filter_group(99), 0x8000)

    def test_mask_is_passed_through_as_collide_with_bits(self):
        self.assertEqual(_mmd_collision_mask_to_bullet_filter_mask(0xFFFF), 0xFFFF)
        self.assertEqual(_mmd_collision_mask_to_bullet_filter_mask(0xFFFD), 0xFFFD)
        self.assertEqual(_mmd_collision_mask_to_bullet_filter_mask(-1), 0x0000)
        self.assertEqual(_mmd_collision_mask_to_bullet_filter_mask(0x1FFFF), 0xFFFF)


class TestGravityResolution(unittest.TestCase):
    """Bullet gravity の既定値と明示指定を検証。"""

    def test_default_gravity_is_mmd_tuned_magnitude(self):
        conv = physics_converter.PhysicsConverter()
        self.assertEqual(conv._resolve_bullet_gravity(), (0.0, -98.0, 0.0))

    def test_explicit_scalar_gravity_overrides_default(self):
        conv = physics_converter.PhysicsConverter({"gravity": 30.0})
        self.assertEqual(conv._resolve_bullet_gravity(), (0.0, -30.0, 0.0))


class TestSetAttrClamped(unittest.TestCase):
    """_set_attr_clamped が範囲取得結果に従って setAttr することを検証。"""

    def _make_converter(self):
        # __init__ は cmds を使うため、生成せず空インスタンスを用意する
        conv = physics_converter.PhysicsConverter.__new__(physics_converter.PhysicsConverter)
        conv.logger = MagicMock()
        return conv

    def test_clamps_angular_damping_above_max(self):
        """angularDamping([0,1]) に 1.5 を渡すと 1.0 に丸めて setAttr する。"""
        conv = self._make_converter()
        fake_cmds = MagicMock()

        def attribute_query(attr, node=None, **kwargs):
            if kwargs.get("minExists"):
                return True
            if kwargs.get("maxExists"):
                return True
            if kwargs.get("minimum"):
                return [0.0]
            if kwargs.get("maximum"):
                return [1.0]
            return False

        fake_cmds.attributeQuery.side_effect = attribute_query
        physics_converter.cmds = fake_cmds
        conv._set_attr_clamped("someShape", "angularDamping", 1.5)
        fake_cmds.setAttr.assert_called_once_with("someShape.angularDamping", 1.0)

    def test_passes_value_when_no_bounds(self):
        """範囲が定義されていない属性はそのまま setAttr する。"""
        conv = self._make_converter()
        fake_cmds = MagicMock()
        fake_cmds.attributeQuery.return_value = False
        physics_converter.cmds = fake_cmds
        conv._set_attr_clamped("someShape", "mass", 3.0)
        fake_cmds.setAttr.assert_called_once_with("someShape.mass", 3.0)

    def test_invalid_value_falls_back_to_zero(self):
        """数値化できない値は 0.0 として扱う。"""
        conv = self._make_converter()
        fake_cmds = MagicMock()
        fake_cmds.attributeQuery.return_value = False
        physics_converter.cmds = fake_cmds
        conv._set_attr_clamped("someShape", "friction", None)
        fake_cmds.setAttr.assert_called_once_with("someShape.friction", 0.0)


if __name__ == "__main__":
    unittest.main()
