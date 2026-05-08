"""
PMXファイルのテストモック機能を提供するモジュール
"""

import struct


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

        material_name = "テスト材質"
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
            ("センター", "center", 0.0, 0.0, 0.0, -1, 0),  # センター
            ("上半身", "upper_body", 0.0, 5.0, 0.0, 0, 0),  # 上半身
            ("頭", "head", 0.0, 10.0, 0.0, 1, 0),  # 頭
        ]
        for bone_name, name_en, x, y, z, parent, flags in bones:
            data.extend(struct.pack("<L", len(bone_name) * 2))  # ボーン名長
            data.extend(bone_name.encode("utf-16le"))  # ボーン名
            data.extend(struct.pack("<L", len(name_en) * 2))  # ボーン名英語長
            data.extend(name_en.encode("utf-16le"))  # ボーン名英語
            data.extend(struct.pack("<fff", x, y, z))  # 位置
            data.extend(struct.pack("<b", parent))  # 親ボーン
            data.extend(struct.pack("<L", 0))  # 変形階層
            data.extend(struct.pack("<H", flags))  # ボーンフラグ

            # フラグに応じた追加データ
            if flags & 0x0001:  # 接続先表示方法（ボーン指定）
                data.extend(struct.pack("<b", 0))  # 接続先ボーンインデックス
            else:  # 接続先表示方法（相対座標オフセット）
                data.extend(struct.pack("<fff", 0.0, 0.0, 0.0))  # オフセット

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

        # テクスチャデータ（1つのテクスチャ）
        data.extend(struct.pack("<L", 1))  # テクスチャ数
        texture_path = "test_texture.png"
        data.extend(struct.pack("<L", len(texture_path) * 2))  # テクスチャパス長
        data.extend(texture_path.encode("utf-16le"))  # テクスチャパス

        # 材質データ（1つの材質）
        data.extend(struct.pack("<L", 1))  # 材質数

        material_name = "テスト材質"
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
        data.extend(struct.pack("<b", 0))  # 通常テクスチャ
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
            ("センター", "center", 0.0, 0.0, 0.0, -1, 0),  # センター
            ("上半身", "upper_body", 0.0, 5.0, 0.0, 0, 0),  # 上半身
            ("頭", "head", 0.0, 10.0, 0.0, 1, 0),  # 頭
        ]
        for bone_name, name_en, x, y, z, parent, flags in bones:
            data.extend(struct.pack("<L", len(bone_name) * 2))  # ボーン名長
            data.extend(bone_name.encode("utf-16le"))  # ボーン名
            data.extend(struct.pack("<L", len(name_en) * 2))  # ボーン名英語長
            data.extend(name_en.encode("utf-16le"))  # ボーン名英語
            data.extend(struct.pack("<fff", x, y, z))  # 位置
            data.extend(struct.pack("<b", parent))  # 親ボーン
            data.extend(struct.pack("<L", 0))  # 変形階層
            data.extend(struct.pack("<H", flags))  # ボーンフラグ

            # フラグに応じた追加データ
            if flags & 0x0001:  # 接続先表示方法（ボーン指定）
                data.extend(struct.pack("<b", 0))  # 接続先ボーンインデックス
            else:  # 接続先表示方法（相対座標オフセット）
                data.extend(struct.pack("<fff", 0.0, 0.0, 0.0))  # オフセット

        # モーフデータ（1つのモーフ）
        data.extend(struct.pack("<L", 1))  # モーフ数

        morph_name = "TestMorph"
        data.extend(struct.pack("<L", len(morph_name) * 2))  # モーフ名長
        data.extend(morph_name.encode("utf-16le"))  # モーフ名
        data.extend(struct.pack("<L", len(morph_name) * 2))  # モーフ名英語長
        data.extend(morph_name.encode("utf-16le"))  # モーフ名英語
        data.extend(struct.pack("<B", 1))  # 操作パネル
        data.extend(struct.pack("<B", 1))  # モーフ種類（頂点モーフ）
        data.extend(struct.pack("<L", 1))  # オフセット数
        # 頂点モーフオフセット
        data.extend(struct.pack("<B", 0))  # 頂点インデックス
        data.extend(struct.pack("<fff", 0.0, 1.0, 0.0))  # オフセット値

        # 表示枠データ（1つの表示枠）
        data.extend(struct.pack("<L", 1))  # 表示枠数

        frame_name = "TestFrame"
        data.extend(struct.pack("<L", len(frame_name) * 2))  # 表示枠名長
        data.extend(frame_name.encode("utf-16le"))  # 表示枠名
        data.extend(struct.pack("<L", len(frame_name) * 2))  # 表示枠名英語長
        data.extend(frame_name.encode("utf-16le"))  # 表示枠名英語
        data.extend(struct.pack("<B", 0))  # 特殊枠フラグ
        data.extend(struct.pack("<L", 1))  # 枠内要素数
        # 枠内要素（ボーン）
        data.extend(struct.pack("<B", 0))  # 要素対象（0=ボーン、1=モーフ）
        data.extend(struct.pack("<B", 0))  # 要素インデックス

        # 剛体データ（1つの剛体）
        data.extend(struct.pack("<L", 1))  # 剛体数

        rigid_name = "TestRigid"
        data.extend(struct.pack("<L", len(rigid_name) * 2))  # 剛体名長
        data.extend(rigid_name.encode("utf-16le"))  # 剛体名
        data.extend(struct.pack("<L", len(rigid_name) * 2))  # 剛体名英語長
        data.extend(rigid_name.encode("utf-16le"))  # 剛体名英語
        data.extend(struct.pack("<b", 0))  # 関連ボーン
        data.extend(struct.pack("<B", 0))  # グループ
        data.extend(struct.pack("<H", 0xFFFF))  # 非衝突グループフラグ
        data.extend(struct.pack("<B", 0))  # 形状（0=球、1=箱、2=カプセル）
        data.extend(struct.pack("<fff", 1.0, 1.0, 1.0))  # サイズ
        data.extend(struct.pack("<fff", 0.0, 0.0, 0.0))  # 位置
        data.extend(struct.pack("<fff", 0.0, 0.0, 0.0))  # 回転
        data.extend(struct.pack("<f", 1.0))  # 質量
        data.extend(struct.pack("<f", 0.5))  # 移動減衰
        data.extend(struct.pack("<f", 0.5))  # 回転減衰
        data.extend(struct.pack("<f", 0.5))  # 反発力
        data.extend(struct.pack("<f", 0.5))  # 摩擦力
        data.extend(struct.pack("<B", 0))  # 物理演算（0=ボーン追従、1=物理演算、2=物理+ボーン）

        # ジョイントデータ（1つのジョイント）
        data.extend(struct.pack("<L", 1))  # ジョイント数

        joint_name = "TestJoint"
        data.extend(struct.pack("<L", len(joint_name) * 2))  # ジョイント名長
        data.extend(joint_name.encode("utf-16le"))  # ジョイント名
        data.extend(struct.pack("<L", len(joint_name) * 2))  # ジョイント名英語長
        data.extend(joint_name.encode("utf-16le"))  # ジョイント名英語
        data.extend(struct.pack("<B", 0))  # ジョイント種類（0=スプリング6DOF）
        data.extend(struct.pack("<b", 0))  # 関連剛体A
        data.extend(struct.pack("<b", -1))  # 関連剛体B
        data.extend(struct.pack("<fff", 0.0, 0.0, 0.0))  # 位置
        data.extend(struct.pack("<fff", 0.0, 0.0, 0.0))  # 回転
        data.extend(struct.pack("<fff", 0.0, 0.0, 0.0))  # 移動制限下限
        data.extend(struct.pack("<fff", 0.0, 0.0, 0.0))  # 移動制限上限
        data.extend(struct.pack("<fff", 0.0, 0.0, 0.0))  # 回転制限下限
        data.extend(struct.pack("<fff", 0.0, 0.0, 0.0))  # 回転制限上限
        data.extend(struct.pack("<fff", 0.0, 0.0, 0.0))  # バネ定数（移動）
        data.extend(struct.pack("<fff", 0.0, 0.0, 0.0))  # バネ定数（回転）

        # ソフトボディデータ（なし）
        if version >= 2.1:
            data.extend(struct.pack("<L", 0))  # ソフトボディ数

        return bytes(data)

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
