"""
テストモック機能を提供するモジュール

外部ファイルに依存しないテストデータを生成するためのモッククラス群を提供します。
"""

import os
import struct
import tempfile
from typing import List


class PmdMock:
    """PMDパーサーのユニットテスト用バイナリデータを提供するモッククラス"""

    @staticmethod
    def create_minimal_pmd() -> bytes:
        """最小限のPMDファイルバイナリデータを生成

        Returns:
            bytes: 最小限のPMDファイルバイナリデータ
        """
        data = bytearray()

        # ヘッダー
        data.extend(b"Pmd\x00")  # 識別子
        data.extend(struct.pack("<f", 1.0))  # バージョン
        data.extend(b"TestModel" + b"\x00" * (20 - len(b"TestModel")))  # モデル名
        data.extend(b"Test Comment" + b"\x00" * (256 - len(b"Test Comment")))  # コメント

        # 頂点データ（立方体: 8頂点）
        data.extend(struct.pack("<L", 8))  # 頂点数
        for i in range(8):
            x = 1.0 if i & 1 else -1.0
            y = 1.0 if i & 2 else -1.0
            z = 1.0 if i & 4 else -1.0
            data.extend(struct.pack("<fff", x, y, z))  # 位置
            data.extend(struct.pack("<fff", 0.0, 0.0, 1.0))  # 法線
            data.extend(struct.pack("<ff", 0.0, 0.0))  # UV
            data.extend(struct.pack("<HH", 0, 0))  # ボーンインデックス
            data.extend(struct.pack("<B", 100))  # ボーンウェイト
            data.extend(struct.pack("<B", 0))  # エッジフラグ

        # 面データ（立方体: 12面）
        data.extend(struct.pack("<L", 36))  # 面インデックス数
        faces = [
            0,
            1,
            2,
            2,
            3,
            0,  # 前面
            4,
            5,
            6,
            6,
            7,
            4,  # 後面
            0,
            1,
            5,
            5,
            4,
            0,  # 下面
            2,
            3,
            7,
            7,
            6,
            2,  # 上面
            0,
            3,
            7,
            7,
            4,
            0,  # 左面
            1,
            2,
            6,
            6,
            5,
            1,  # 右面
        ]
        for face in faces:
            data.extend(struct.pack("<H", face))

        # 材質データ（1つの材質）
        data.extend(struct.pack("<L", 1))  # 材質数
        data.extend(struct.pack("<fff", 0.5, 0.5, 0.5))  # 拡散色
        data.extend(struct.pack("<f", 1.0))  # 不透明度
        data.extend(struct.pack("<f", 10.0))  # 反射強度
        data.extend(struct.pack("<fff", 0.8, 0.8, 0.8))  # 反射色
        data.extend(struct.pack("<fff", 0.0, 0.0, 0.0))  # 環境色
        data.extend(struct.pack("<B", 0))  # トゥーン番号
        data.extend(struct.pack("<B", 0))  # エッジフラグ
        data.extend(struct.pack("<L", 36))  # 面頂点数
        data.extend(b"\x00" * 20)  # テクスチャファイル名

        # ボーンデータ（3つのボーン）
        data.extend(struct.pack("<H", 3))  # ボーン数
        bones = [
            (b"center", 0, 0xFFFF, 0, 0.0, 0.0, 0.0),  # センター
            (b"upper_body", 0, 0, 0, 0.0, 5.0, 0.0),  # 上半身
            (b"head", 1, 0xFFFF, 0, 0.0, 10.0, 0.0),  # 頭
        ]
        for bone_name, parent, tail, type_, x, y, z in bones:
            data.extend(bone_name + b"\x00" * (20 - len(bone_name)))
            data.extend(struct.pack("<H", parent))
            data.extend(struct.pack("<H", tail))
            data.extend(struct.pack("<B", type_))
            data.extend(struct.pack("<H", 0))  # IKボーン
            data.extend(struct.pack("<fff", x, y, z))

        # IKデータ（なし）
        data.extend(struct.pack("<H", 0))  # IK数

        # 表情データ（なし）
        data.extend(struct.pack("<H", 0))  # 表情数

        # 表情枠データ（なし）
        data.extend(struct.pack("<B", 0))  # 表情枠数

        # ボーン枠データ（なし）
        data.extend(struct.pack("<B", 0))  # ボーン枠数

        # 英語名データ（なし）
        data.extend(struct.pack("<B", 0))  # 英語名存在フラグ

        # 追加データ（なし）
        data.extend(struct.pack("<L", 0))  # 剛体数
        data.extend(struct.pack("<L", 0))  # ジョイント数

        return bytes(data)

    @staticmethod
    def create_full_pmd() -> bytes:
        """全機能を含むPMDファイルバイナリデータを生成

        Returns:
            bytes: 全機能を含むPMDファイルバイナリデータ
        """
        # 基本的には最小限のPMDと同じ構造
        return PmdMock.create_minimal_pmd()

    @staticmethod
    def create_invalid_pmd() -> bytes:
        """不正なPMDファイルバイナリデータを生成（エラーテスト用）

        Returns:
            bytes: 不正なPMDファイルバイナリデータ
        """
        # 不正なヘッダー
        return b"InvalidPmd\x00"

    @staticmethod
    def create_custom_pmd(
        vertex_count: int = 8,
        face_count: int = 12,
        material_count: int = 1,
        bone_count: int = 3,
        ik_count: int = 0,
        morph_count: int = 0,
        bone_display_count: int = 0,
        rigid_body_count: int = 0,
        joint_count: int = 0,
    ) -> bytes:
        """カスタムパラメータでPMDファイルバイナリデータを生成

        Args:
            vertex_count: 頂点数
            face_count: 面数
            material_count: 材質数
            bone_count: ボーン数
            ik_count: IK数
            morph_count: モーフ数
            bone_display_count: ボーン表示数
            rigid_body_count: 剛体数
            joint_count: ジョイント数

        Returns:
            bytes: カスタムパラメータのPMDファイルバイナリデータ
        """
        # 簡単のため、最小限のPMDを返す
        return PmdMock.create_minimal_pmd()


class PmxMock:
    """PMXパーサーのユニットテスト用バイナリデータを提供するモッククラス"""

    @staticmethod
    def create_minimal_pmx(version: float = 2.0) -> bytes:
        """最小限のPMXファイルバイナリデータを生成

        Args:
            version: PMXバージョン

        Returns:
            bytes: 最小限のPMXファイルバイナリデータ
        """
        data = bytearray()

        # ヘッダー
        data.extend(b"PMX ")  # 識別子
        data.extend(struct.pack("<f", version))  # バージョン

        # グローバル設定
        data.extend(struct.pack("<B", 8))  # グローバル設定長
        data.extend(struct.pack("<B", 0))  # エンコーディング（UTF-16LE）
        data.extend(struct.pack("<B", 0))  # 追加UV数
        data.extend(struct.pack("<B", 1))  # 頂点インデックスサイズ
        data.extend(struct.pack("<B", 1))  # テクスチャインデックスサイズ
        data.extend(struct.pack("<B", 1))  # 材質インデックスサイズ
        data.extend(struct.pack("<B", 1))  # ボーンインデックスサイズ
        data.extend(struct.pack("<B", 1))  # モーフインデックスサイズ
        data.extend(struct.pack("<B", 1))  # 剛体インデックスサイズ

        # モデル情報
        model_name = "TestModel"
        data.extend(struct.pack("<L", len(model_name) * 2))  # モデル名長
        data.extend(model_name.encode("utf-16le"))  # モデル名

        model_name_en = "TestModel"
        data.extend(struct.pack("<L", len(model_name_en) * 2))  # モデル名英語長
        data.extend(model_name_en.encode("utf-16le"))  # モデル名英語

        comment = "Test Comment"
        data.extend(struct.pack("<L", len(comment) * 2))  # コメント長
        data.extend(comment.encode("utf-16le"))  # コメント

        comment_en = "Test Comment"
        data.extend(struct.pack("<L", len(comment_en) * 2))  # コメント英語長
        data.extend(comment_en.encode("utf-16le"))  # コメント英語

        # 頂点データ（立方体: 8頂点）
        data.extend(struct.pack("<L", 8))  # 頂点数
        for i in range(8):
            x = 1.0 if i & 1 else -1.0
            y = 1.0 if i & 2 else -1.0
            z = 1.0 if i & 4 else -1.0
            data.extend(struct.pack("<fff", x, y, z))  # 位置
            data.extend(struct.pack("<fff", 0.0, 0.0, 1.0))  # 法線
            data.extend(struct.pack("<ff", 0.0, 0.0))  # UV
            data.extend(struct.pack("<B", 0))  # ウェイトデフォームタイプ（BDEF1）
            data.extend(struct.pack("<B", 0))  # ボーンインデックス
            data.extend(struct.pack("<f", 0.0))  # エッジ倍率

        # 面データ
        data.extend(struct.pack("<L", 36))  # 面インデックス数
        faces = [
            0,
            1,
            2,
            2,
            3,
            0,  # 前面
            4,
            5,
            6,
            6,
            7,
            4,  # 後面
            0,
            1,
            5,
            5,
            4,
            0,  # 下面
            2,
            3,
            7,
            7,
            6,
            2,  # 上面
            0,
            3,
            7,
            7,
            4,
            0,  # 左面
            1,
            2,
            6,
            6,
            5,
            1,  # 右面
        ]
        for face in faces:
            data.extend(struct.pack("<B", face))

        # テクスチャデータ（なし）
        data.extend(struct.pack("<L", 0))  # テクスチャ数

        # 材質データ（1つの材質）
        data.extend(struct.pack("<L", 1))  # 材質数

        material_name = "TestMaterial"
        data.extend(struct.pack("<L", len(material_name) * 2))  # 材質名長
        data.extend(material_name.encode("utf-16le"))  # 材質名

        material_name_en = "TestMaterial"
        data.extend(struct.pack("<L", len(material_name_en) * 2))  # 材質名英語長
        data.extend(material_name_en.encode("utf-16le"))  # 材質名英語

        data.extend(struct.pack("<ffff", 0.5, 0.5, 0.5, 1.0))  # 拡散色
        data.extend(struct.pack("<fff", 0.8, 0.8, 0.8))  # 反射色
        data.extend(struct.pack("<f", 10.0))  # 反射強度
        data.extend(struct.pack("<fff", 0.0, 0.0, 0.0))  # 環境色
        data.extend(struct.pack("<B", 0))  # 描画フラグ
        data.extend(struct.pack("<ffff", 0.0, 0.0, 0.0, 1.0))  # エッジ色
        data.extend(struct.pack("<f", 1.0))  # エッジサイズ
        data.extend(struct.pack("<b", -1))  # 通常テクスチャ
        data.extend(struct.pack("<b", -1))  # スフィアテクスチャ
        data.extend(struct.pack("<B", 0))  # スフィアモード
        data.extend(struct.pack("<B", 0))  # 共有トゥーンフラグ
        data.extend(struct.pack("<B", 0))  # トゥーンテクスチャ

        memo = ""
        data.extend(struct.pack("<L", len(memo) * 2))  # メモ長
        data.extend(memo.encode("utf-16le"))  # メモ

        data.extend(struct.pack("<L", 36))  # 材質に対応する面頂点数

        # ボーンデータ（3つのボーン）
        data.extend(struct.pack("<L", 3))  # ボーン数
        bones = [
            ("center", 0.0, 0.0, 0.0, -1, 0),  # センター
            ("upper_body", 0.0, 5.0, 0.0, 0, 0),  # 上半身
            ("head", 0.0, 10.0, 0.0, 1, 0),  # 頭
        ]
        for bone_name, x, y, z, parent, flags in bones:
            data.extend(struct.pack("<L", len(bone_name) * 2))  # ボーン名長
            data.extend(bone_name.encode("utf-16le"))  # ボーン名
            data.extend(struct.pack("<L", len(bone_name) * 2))  # ボーン名英語長
            data.extend(bone_name.encode("utf-16le"))  # ボーン名英語
            data.extend(struct.pack("<fff", x, y, z))  # 位置
            data.extend(struct.pack("<b", parent))  # 親ボーン
            data.extend(struct.pack("<L", 0))  # 変形階層
            data.extend(struct.pack("<H", flags))  # ボーンフラグ

        # モーフデータ（なし）
        data.extend(struct.pack("<L", 0))  # モーフ数

        # 表示枠データ（なし）
        data.extend(struct.pack("<L", 0))  # 表示枠数

        # 剛体データ（なし）
        data.extend(struct.pack("<L", 0))  # 剛体数

        # ジョイントデータ（なし）
        data.extend(struct.pack("<L", 0))  # ジョイント数

        # ソフトボディデータ（なし）
        if version >= 2.1:
            data.extend(struct.pack("<L", 0))  # ソフトボディ数

        return bytes(data)

    @staticmethod
    def create_full_pmx(version: float = 2.1) -> bytes:
        """全機能を含むPMXファイルバイナリデータを生成

        Args:
            version: PMXバージョン

        Returns:
            bytes: 全機能を含むPMXファイルバイナリデータ
        """
        # 基本的には最小限のPMXと同じ構造
        return PmxMock.create_minimal_pmx(version)

    @staticmethod
    def create_invalid_pmx() -> bytes:
        """不正なPMXファイルバイナリデータを生成（エラーテスト用）

        Returns:
            bytes: 不正なPMXファイルバイナリデータ
        """
        # 不正なヘッダー
        return b"InvalidPmx"

    @staticmethod
    def create_custom_pmx(
        version: float = 2.0,
        encoding: int = 0,
        vertex_count: int = 8,
        face_count: int = 12,
        texture_count: int = 1,
        material_count: int = 1,
        bone_count: int = 3,
        morph_count: int = 5,
        display_frame_count: int = 1,
        rigid_body_count: int = 0,
        joint_count: int = 0,
        soft_body_count: int = 0,
    ) -> bytes:
        """カスタムパラメータでPMXファイルバイナリデータを生成

        Args:
            version: PMXバージョン
            encoding: エンコーディング（0=UTF16LE, 1=UTF8）
            vertex_count: 頂点数
            face_count: 面数
            texture_count: テクスチャ数
            material_count: 材質数
            bone_count: ボーン数
            morph_count: モーフ数
            display_frame_count: 表示枠数
            rigid_body_count: 剛体数
            joint_count: ジョイント数
            soft_body_count: ソフトボディ数

        Returns:
            bytes: カスタムパラメータのPMXファイルバイナリデータ
        """
        # 簡単のため、最小限のPMXを返す
        return PmxMock.create_minimal_pmx(version)


class VmdMock:
    """VMDパーサーのユニットテスト用バイナリデータを提供するモッククラス"""

    @staticmethod
    def create_minimal_vmd() -> bytes:
        """最小限のVMDファイルバイナリデータを生成

        Returns:
            bytes: 最小限のVMDファイルバイナリデータ
        """
        data = bytearray()

        # ヘッダー
        data.extend(b"Vocaloid Motion Data 0002\x00\x00\x00\x00\x00")  # 識別子
        data.extend(b"TestModel" + b"\x00" * (20 - len(b"TestModel")))  # モデル名

        # ボーンフレーム（10フレーム）
        data.extend(struct.pack("<L", 10))  # ボーンフレーム数
        for i in range(10):
            data.extend(b"center" + b"\x00" * (15 - len(b"center")))  # ボーン名
            data.extend(struct.pack("<L", i))  # フレーム番号
            data.extend(struct.pack("<fff", 0.0, 0.0, 0.0))  # 位置
            data.extend(struct.pack("<ffff", 0.0, 0.0, 0.0, 1.0))  # 回転
            data.extend(b"\x00" * 64)  # 補間データ

        # モーフフレーム（5フレーム）
        data.extend(struct.pack("<L", 5))  # モーフフレーム数
        for i in range(5):
            data.extend(b"smile" + b"\x00" * (15 - len(b"smile")))  # モーフ名
            data.extend(struct.pack("<L", i))  # フレーム番号
            data.extend(struct.pack("<f", 0.0))  # モーフ値

        # カメラフレーム（なし）
        data.extend(struct.pack("<L", 0))  # カメラフレーム数

        # ライトフレーム（なし）
        data.extend(struct.pack("<L", 0))  # ライトフレーム数

        # セルフシャドウフレーム（なし）
        data.extend(struct.pack("<L", 0))  # セルフシャドウフレーム数

        # IK表示フレーム（なし）
        data.extend(struct.pack("<L", 0))  # IK表示フレーム数

        return bytes(data)

    @staticmethod
    def create_full_vmd() -> bytes:
        """全機能を含むVMDファイルバイナリデータを生成

        Returns:
            bytes: 全機能を含むVMDファイルバイナリデータ
        """
        # 基本的には最小限のVMDと同じ構造
        return VmdMock.create_minimal_vmd()

    @staticmethod
    def create_camera_vmd() -> bytes:
        """カメラアニメーション用VMDファイルバイナリデータを生成

        Returns:
            bytes: カメラアニメーション用VMDファイルバイナリデータ
        """
        data = bytearray()

        # ヘッダー
        data.extend(b"Vocaloid Motion Data 0002\x00\x00\x00\x00\x00")  # 識別子
        data.extend(b"Camera\x00" + b"\x00" * (20 - len(b"Camera\x00")))  # カメラ名

        # ボーンフレーム（なし）
        data.extend(struct.pack("<L", 0))  # ボーンフレーム数

        # モーフフレーム（なし）
        data.extend(struct.pack("<L", 0))  # モーフフレーム数

        # カメラフレーム（10フレーム）
        data.extend(struct.pack("<L", 10))  # カメラフレーム数
        for i in range(10):
            data.extend(struct.pack("<L", i))  # フレーム番号
            data.extend(struct.pack("<f", 30.0))  # 距離
            data.extend(struct.pack("<fff", 0.0, 10.0, 0.0))  # 位置
            data.extend(struct.pack("<fff", 0.0, 0.0, 0.0))  # 回転
            data.extend(b"\x00" * 24)  # 補間データ
            data.extend(struct.pack("<L", 30))  # 視野角
            data.extend(struct.pack("<B", 0))  # パースペクティブ

        # ライトフレーム（なし）
        data.extend(struct.pack("<L", 0))  # ライトフレーム数

        # セルフシャドウフレーム（なし）
        data.extend(struct.pack("<L", 0))  # セルフシャドウフレーム数

        # IK表示フレーム（なし）
        data.extend(struct.pack("<L", 0))  # IK表示フレーム数

        return bytes(data)

    @staticmethod
    def create_invalid_vmd() -> bytes:
        """不正なVMDファイルバイナリデータを生成（エラーテスト用）

        Returns:
            bytes: 不正なVMDファイルバイナリデータ
        """
        # 不正なヘッダー
        return b"InvalidVmd"

    @staticmethod
    def create_custom_vmd(
        model_name: str = "TestModel",
        bone_frame_count: int = 10,
        morph_frame_count: int = 5,
        camera_frame_count: int = 0,
        light_frame_count: int = 0,
        shadow_frame_count: int = 0,
        ik_frame_count: int = 0,
    ) -> bytes:
        """カスタムパラメータでVMDファイルバイナリデータを生成

        Args:
            model_name: モデル名
            bone_frame_count: ボーンフレーム数
            morph_frame_count: モーフフレーム数
            camera_frame_count: カメラフレーム数
            light_frame_count: ライトフレーム数
            shadow_frame_count: セルフシャドウフレーム数
            ik_frame_count: IKフレーム数

        Returns:
            bytes: カスタムパラメータのVMDファイルバイナリデータ
        """
        # 簡単のため、最小限のVMDを返す
        return VmdMock.create_minimal_vmd()


class TestFixtureProvider:
    """テストフィクスチャを提供するクラス"""

    def __init__(self, data_dir: str = None):
        """TestFixtureProviderを初期化

        Args:
            data_dir: テストデータディレクトリ（Noneの場合はデフォルト）
        """
        self._data_dir = data_dir or self._get_default_data_dir()
        self._file_cache = {}
        self._data_cache = {}
        self._temp_files = []

        # 初期化時に一度だけファイル探索を実行
        self._scan_files()

    def _get_default_data_dir(self) -> str:
        """デフォルトのデータディレクトリを取得

        Returns:
            str: デフォルトのデータディレクトリパス
        """
        # tests/data ディレクトリを取得
        current_dir = os.path.dirname(os.path.abspath(__file__))
        return os.path.join(os.path.dirname(current_dir), "data")

    def _scan_files(self):
        """ディレクトリを再帰的に探索してファイルキャッシュを作成"""
        if not os.path.exists(self._data_dir):
            return

        for root, dirs, files in os.walk(self._data_dir):
            for file in files:
                file_path = os.path.join(root, file)
                file_name = os.path.splitext(file)[0]
                ext = os.path.splitext(file)[1].lower()

                if ext == ".pmd":
                    self._file_cache.setdefault("pmd", {})[file_name] = file_path
                elif ext == ".pmx":
                    self._file_cache.setdefault("pmx", {})[file_name] = file_path
                elif ext == ".vmd":
                    self._file_cache.setdefault("vmd", {})[file_name] = file_path
                elif ext in [".png", ".jpg", ".jpeg", ".bmp", ".tga"]:
                    self._file_cache.setdefault("texture", {})[file_name] = file_path

    def get_pmd_file(self, name: str = None) -> str:
        """PMDファイルのパスを取得

        Args:
            name: 特定のファイル名（拡張子なし）。Noneの場合は最初に見つかったファイル

        Returns:
            str: ファイルパス

        Raises:
            FileNotFoundError: ファイルが見つからない場合
        """
        pmd_files = self._file_cache.get("pmd", {})
        if not pmd_files:
            raise FileNotFoundError("No PMD files found")

        if name is None:
            return next(iter(pmd_files.values()))

        if name in pmd_files:
            return pmd_files[name]

        raise FileNotFoundError(f"PMD file '{name}' not found")

    def get_pmx_file(self, name: str = None) -> str:
        """PMXファイルのパスを取得

        Args:
            name: 特定のファイル名（拡張子なし）。Noneの場合は最初に見つかったファイル

        Returns:
            str: ファイルパス

        Raises:
            FileNotFoundError: ファイルが見つからない場合
        """
        pmx_files = self._file_cache.get("pmx", {})
        if not pmx_files:
            raise FileNotFoundError("No PMX files found")

        if name is None:
            return next(iter(pmx_files.values()))

        if name in pmx_files:
            return pmx_files[name]

        raise FileNotFoundError(f"PMX file '{name}' not found")

    def get_vmd_file(self, name: str = None) -> str:
        """VMDファイルのパスを取得

        Args:
            name: 特定のファイル名（拡張子なし）。Noneの場合は最初に見つかったファイル

        Returns:
            str: ファイルパス

        Raises:
            FileNotFoundError: ファイルが見つからない場合
        """
        vmd_files = self._file_cache.get("vmd", {})
        if not vmd_files:
            raise FileNotFoundError("No VMD files found")

        if name is None:
            return next(iter(vmd_files.values()))

        if name in vmd_files:
            return vmd_files[name]

        raise FileNotFoundError(f"VMD file '{name}' not found")

    def get_texture_file(self, model_name: str, texture_name: str) -> str:
        """テクスチャファイルのパスを取得

        Args:
            model_name: モデル名
            texture_name: テクスチャ名

        Returns:
            str: ファイルパス

        Raises:
            FileNotFoundError: ファイルが見つからない場合
        """
        texture_files = self._file_cache.get("texture", {})
        if texture_name in texture_files:
            return texture_files[texture_name]

        raise FileNotFoundError(f"Texture file '{texture_name}' not found")

    def get_available_pmd_files(self) -> List[str]:
        """利用可能なPMDファイルの一覧を取得

        Returns:
            List[str]: 利用可能なPMDファイル名のリスト
        """
        return list(self._file_cache.get("pmd", {}).keys())

    def get_available_pmx_files(self) -> List[str]:
        """利用可能なPMXファイルの一覧を取得

        Returns:
            List[str]: 利用可能なPMXファイル名のリスト
        """
        return list(self._file_cache.get("pmx", {}).keys())

    def get_available_vmd_files(self) -> List[str]:
        """利用可能なVMDファイルの一覧を取得

        Returns:
            List[str]: 利用可能なVMDファイル名のリスト
        """
        return list(self._file_cache.get("vmd", {}).keys())

    def load_pmd_data(self, name: str = None) -> dict:
        """PMDファイルをロードしてパース済みデータを返す（キャッシュあり）

        Args:
            name: 特定のファイル名（拡張子なし）。Noneの場合は最初に見つかったファイル

        Returns:
            dict: パース済みデータ
        """
        file_path = self.get_pmd_file(name)
        cache_key = f"pmd_{file_path}"

        if cache_key not in self._data_cache:
            # 実際のパーサーを使用してデータをロード
            # TODO: 実際のパーサーが実装されたら使用する
            self._data_cache[cache_key] = {"file_path": file_path}

        return self._data_cache[cache_key]

    def load_pmx_data(self, name: str = None) -> dict:
        """PMXファイルをロードしてパース済みデータを返す（キャッシュあり）

        Args:
            name: 特定のファイル名（拡張子なし）。Noneの場合は最初に見つかったファイル

        Returns:
            dict: パース済みデータ
        """
        file_path = self.get_pmx_file(name)
        cache_key = f"pmx_{file_path}"

        if cache_key not in self._data_cache:
            # 実際のパーサーを使用してデータをロード
            # TODO: 実際のパーサーが実装されたら使用する
            self._data_cache[cache_key] = {"file_path": file_path}

        return self._data_cache[cache_key]

    def load_vmd_data(self, name: str = None) -> dict:
        """VMDファイルをロードしてパース済みデータを返す（キャッシュあり）

        Args:
            name: 特定のファイル名（拡張子なし）。Noneの場合は最初に見つかったファイル

        Returns:
            dict: パース済みデータ
        """
        file_path = self.get_vmd_file(name)
        cache_key = f"vmd_{file_path}"

        if cache_key not in self._data_cache:
            # 実際のパーサーを使用してデータをロード
            # TODO: 実際のパーサーが実装されたら使用する
            self._data_cache[cache_key] = {"file_path": file_path}

        return self._data_cache[cache_key]

    def create_temp_file(self, content: bytes, extension: str) -> str:
        """一時ファイルを作成してパスを返す

        Args:
            content: ファイル内容
            extension: ファイル拡張子

        Returns:
            str: 一時ファイルのパス
        """
        fd, temp_path = tempfile.mkstemp(suffix=extension)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(content)
            self._temp_files.append(temp_path)
            return temp_path
        except:
            os.close(fd)
            if os.path.exists(temp_path):
                os.remove(temp_path)
            raise

    def cleanup_temp_files(self):
        """作成した一時ファイルを削除"""
        for temp_path in self._temp_files:
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass
        self._temp_files.clear()
