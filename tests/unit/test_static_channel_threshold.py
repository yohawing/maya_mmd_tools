"""
runtime bake 静的チャンネル判定閾値の純Python 回帰テスト。

``VmdConverter._append_bone_locals_to_channel_arrays`` は各フレームの
ローカル姿勢 (tx, ty, tz, rx_deg, ry_deg, rz_deg) を受け取り、

- 変動量 <= epsilon → チャンネルを *静的* とみなし ``MDoubleArray`` を生成しない
- 変動量 >  epsilon → ``MDoubleArray`` を生成し全フレームの値を保持する

このテストは maya / mayapy に依存せず純Python で実行できる。
``om.MDoubleArray`` は ``tests.common.maya_stub._MDoubleArray`` スタブで置き換える。
"""

import math
import unittest

# Maya スタブを対象 import より先に登録する
from tests.common.maya_stub import install_maya_stub, install_om_double_array_stub

install_maya_stub()
install_om_double_array_stub()

from mmd_tools.converters.vmd_converter import VmdConverter  # noqa: E402

_ATTRS = VmdConverter._runtime_joint_attrs()


# ---------------------------------------------------------------------------
# ヘルパー
# ---------------------------------------------------------------------------

def _make_converter(eps_translate: float = 1e-4, eps_rotate_deg: float = 0.01) -> VmdConverter:
    """テスト用 VmdConverter を生成し、閾値と bone_index_to_joint を確定値で設定する。"""
    c = VmdConverter()
    c._static_eps_translate = eps_translate
    c._static_eps_rotate = math.radians(eps_rotate_deg)
    c.bone_index_to_joint = {0: "joint_A"}
    return c


def _make_channel_values() -> dict:
    return {"joint_A": {attr: None for attr in _ATTRS}}


def _make_static_state() -> dict:
    return {"joint_A": {
        attr: {"first": None, "is_static": True, "count": 0}
        for attr in _ATTRS
    }}


def _apply_frames(converter: VmdConverter, frames: list) -> tuple:
    """``_append_bone_locals_to_channel_arrays`` に複数フレームを順に渡す。

    Args:
        converter: テスト用 VmdConverter
        frames: 各要素が ``{bone_idx: (tx, ty, tz, rx_deg, ry_deg, rz_deg)}``

    Returns:
        (channel_values, static_state)
    """
    cv = _make_channel_values()
    ss = _make_static_state()
    for bone_locals in frames:
        converter._append_bone_locals_to_channel_arrays(bone_locals, cv, ss)
    return cv, ss


# ---------------------------------------------------------------------------
# テストケース
# ---------------------------------------------------------------------------

class TestStaticChannelThreshold(unittest.TestCase):

    def test_single_frame_never_creates_array(self):
        """1フレームのみ: first を記録するだけで配列は作られない。"""
        c = _make_converter()
        cv, ss = _apply_frames(c, [{0: (1.0, 2.0, 3.0, 10.0, 20.0, 30.0)}])
        for attr in _ATTRS:
            with self.subTest(attr=attr):
                self.assertIsNone(cv["joint_A"][attr])
                self.assertTrue(ss["joint_A"][attr]["is_static"])
                self.assertIsNotNone(ss["joint_A"][attr]["first"])

    def test_all_zero_multiple_frames_stays_static(self):
        """5フレーム全ゼロ → 全チャンネル静的・配列なし。"""
        c = _make_converter()
        frames = [{0: (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)} for _ in range(5)]
        cv, ss = _apply_frames(c, frames)
        for attr in _ATTRS:
            with self.subTest(attr=attr):
                self.assertIsNone(cv["joint_A"][attr])
                self.assertTrue(ss["joint_A"][attr]["is_static"])

    def test_translate_within_epsilon_stays_static(self):
        """並進変動 < epsilon_translate → 静的のまま。"""
        eps = 1e-4
        c = _make_converter(eps_translate=eps)
        frames = [
            {0: (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)},
            {0: (eps / 2, 0.0, 0.0, 0.0, 0.0, 0.0)},
        ]
        cv, ss = _apply_frames(c, frames)
        self.assertIsNone(cv["joint_A"]["translateX"])
        self.assertTrue(ss["joint_A"]["translateX"]["is_static"])

    def test_translate_at_exact_epsilon_boundary_stays_static(self):
        """|value - first| == epsilon は <= 判定で静的扱い。"""
        eps = 1e-4
        c = _make_converter(eps_translate=eps)
        frames = [
            {0: (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)},
            {0: (eps, 0.0, 0.0, 0.0, 0.0, 0.0)},
        ]
        cv, ss = _apply_frames(c, frames)
        self.assertIsNone(cv["joint_A"]["translateX"])
        self.assertTrue(ss["joint_A"]["translateX"]["is_static"])

    def test_translate_just_above_epsilon_creates_array(self):
        """|value - first| = epsilon + tiny → 動的・配列生成。"""
        eps = 1e-4
        c = _make_converter(eps_translate=eps)
        frames = [
            {0: (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)},
            {0: (eps + 1e-10, 0.0, 0.0, 0.0, 0.0, 0.0)},
        ]
        cv, ss = _apply_frames(c, frames)
        arr = cv["joint_A"]["translateX"]
        self.assertIsNotNone(arr)
        self.assertFalse(ss["joint_A"]["translateX"]["is_static"])
        self.assertEqual(len(arr), 2)
        self.assertAlmostEqual(arr[0], 0.0)
        self.assertAlmostEqual(arr[1], eps + 1e-10)

    def test_translate_two_frames_exceeds_epsilon_values(self):
        """2フレームで閾値超過: 配列に [first, value] が格納される。"""
        c = _make_converter(eps_translate=1e-4)
        first_val = 0.0
        second_val = 2e-4
        frames = [
            {0: (first_val, 0.0, 0.0, 0.0, 0.0, 0.0)},
            {0: (second_val, 0.0, 0.0, 0.0, 0.0, 0.0)},
        ]
        cv, ss = _apply_frames(c, frames)
        arr = cv["joint_A"]["translateX"]
        self.assertIsNotNone(arr)
        self.assertEqual(len(arr), 2)
        self.assertAlmostEqual(arr[0], first_val)
        self.assertAlmostEqual(arr[1], second_val)

    def test_static_backfill_on_transition(self):
        """静的→動的遷移時: 過去静的フレームを first 値で埋め戻す。"""
        c = _make_converter(eps_translate=1e-4)
        frames = [
            {0: (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)},   # first=0.0, count=1
            {0: (5e-5, 0.0, 0.0, 0.0, 0.0, 0.0)},   # static, count=2
            {0: (2e-4, 0.0, 0.0, 0.0, 0.0, 0.0)},   # exceeds → backfill 2×0.0 + 2e-4
        ]
        cv, ss = _apply_frames(c, frames)
        arr = cv["joint_A"]["translateX"]
        self.assertIsNotNone(arr)
        self.assertEqual(len(arr), 3)
        self.assertAlmostEqual(arr[0], 0.0)  # backfill from first
        self.assertAlmostEqual(arr[1], 0.0)  # backfill from first
        self.assertAlmostEqual(arr[2], 2e-4)

    def test_append_continues_after_transition(self):
        """動的遷移後も後続フレームが append される。"""
        c = _make_converter(eps_translate=1e-4)
        frames = [
            {0: (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)},
            {0: (2e-4, 0.0, 0.0, 0.0, 0.0, 0.0)},  # transition
            {0: (3e-4, 0.0, 0.0, 0.0, 0.0, 0.0)},  # append
            {0: (4e-4, 0.0, 0.0, 0.0, 0.0, 0.0)},  # append
        ]
        cv, ss = _apply_frames(c, frames)
        arr = cv["joint_A"]["translateX"]
        self.assertEqual(len(arr), 4)
        self.assertAlmostEqual(arr[0], 0.0)
        self.assertAlmostEqual(arr[1], 2e-4)
        self.assertAlmostEqual(arr[2], 3e-4)
        self.assertAlmostEqual(arr[3], 4e-4)

    def test_rotate_within_epsilon_stays_static(self):
        """回転変動 < epsilon_rotate_deg → 静的。"""
        c = _make_converter(eps_rotate_deg=0.01)
        frames = [
            {0: (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)},
            {0: (0.0, 0.0, 0.0, 0.005, 0.0, 0.0)},  # 0.005 deg < 0.01 deg
        ]
        cv, ss = _apply_frames(c, frames)
        self.assertIsNone(cv["joint_A"]["rotateX"])
        self.assertTrue(ss["joint_A"]["rotateX"]["is_static"])

    def test_rotate_exceeds_epsilon_creates_array(self):
        """回転変動 > epsilon_rotate_deg → 動的・配列にラジアン値が入る。"""
        c = _make_converter(eps_rotate_deg=0.01)
        frames = [
            {0: (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)},
            {0: (0.0, 0.0, 0.0, 0.02, 0.0, 0.0)},  # 0.02 deg > 0.01 deg
        ]
        cv, ss = _apply_frames(c, frames)
        arr = cv["joint_A"]["rotateX"]
        self.assertIsNotNone(arr)
        self.assertFalse(ss["joint_A"]["rotateX"]["is_static"])
        self.assertEqual(len(arr), 2)
        self.assertAlmostEqual(arr[0], 0.0)
        self.assertAlmostEqual(arr[1], math.radians(0.02))

    def test_translate_epsilon_independent_of_rotate(self):
        """並進閾値超過が回転の静的判定に影響しない (チャンネル独立)。"""
        c = _make_converter(eps_translate=1e-4, eps_rotate_deg=0.01)
        frames = [
            {0: (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)},
            {0: (2e-4, 0.0, 0.0, 0.005, 0.0, 0.0)},  # tx exceeds, rx static
        ]
        cv, ss = _apply_frames(c, frames)
        self.assertIsNotNone(cv["joint_A"]["translateX"])   # dynamic
        self.assertIsNone(cv["joint_A"]["rotateX"])         # still static

    def test_unknown_bone_index_is_silently_skipped(self):
        """存在しないボーンインデックスは channel_values を変えない。"""
        c = _make_converter()
        frames = [
            {0: (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)},
            {99: (100.0, 0.0, 0.0, 0.0, 0.0, 0.0)},  # not in bone_index_to_joint
        ]
        cv, ss = _apply_frames(c, frames)
        for attr in _ATTRS:
            with self.subTest(attr=attr):
                self.assertIsNone(cv["joint_A"][attr])

    def test_negative_translate_delta_exceeds_epsilon(self):
        """負の方向への変動も閾値超過で動的になる。"""
        c = _make_converter(eps_translate=1e-4)
        frames = [
            {0: (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)},
            {0: (-2e-4, 0.0, 0.0, 0.0, 0.0, 0.0)},
        ]
        cv, ss = _apply_frames(c, frames)
        arr = cv["joint_A"]["translateX"]
        self.assertIsNotNone(arr)
        self.assertAlmostEqual(arr[1], -2e-4)

    def test_tz_channel_independent_of_tx(self):
        """tz チャンネルが tx と独立して判定される。"""
        c = _make_converter(eps_translate=1e-4)
        frames = [
            {0: (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)},
            {0: (2e-4, 0.0, 5e-5, 0.0, 0.0, 0.0)},  # tx dynamic, tz static
        ]
        cv, ss = _apply_frames(c, frames)
        self.assertIsNotNone(cv["joint_A"]["translateX"])
        self.assertIsNone(cv["joint_A"]["translateZ"])

    def test_count_tracks_frames_correctly(self):
        """count が実際のフレーム数に追随する。"""
        c = _make_converter()
        frames = [{0: (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)} for _ in range(4)]
        _, ss = _apply_frames(c, frames)
        self.assertEqual(ss["joint_A"]["translateX"]["count"], 4)


if __name__ == "__main__":
    unittest.main()
