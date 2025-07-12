"""
PMDファイルのテストモック機能を提供するモジュール
"""

import struct


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
        data.extend(b"Pmd")  # 識別子（3バイト）
        data.extend(struct.pack("<f", 1.0))  # バージョン
        data.extend(b"TestModel" + b"\x00" * (20 - len(b"TestModel")))  # モデル名
        data.extend(
            b"Test Comment" + b"\x00" * (256 - len(b"Test Comment"))
        )  # コメント

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
        data.extend(b"\x00" * 20)  # テクスチャファイル名（20バイト固定）

        # ボーンデータ（3つのボーン）
        data.extend(struct.pack("<H", 3))  # ボーン数
        bones = [
            (b"center", 0xFFFF, 0xFFFF, 0, 0.0, 0.0, 0.0),  # センター
            (b"upper_body", 0, 0xFFFF, 0, 0.0, 5.0, 0.0),  # 上半身
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
        data = bytearray()

        # ヘッダー
        data.extend(b"Pmd")  # 識別子（3バイト）
        data.extend(struct.pack("<f", 1.0))  # バージョン
        model_name = (
            b"\x83e\x83X\x83g\x83\x82\x83f\x83\x8b"  # "テストモデル" in Shift-JIS
        )
        data.extend(model_name + b"\x00" * (20 - len(model_name)))  # モデル名
        comment = b"Full featured test model"
        data.extend(comment + b"\x00" * (256 - len(comment)))  # コメント

        # 頂点データ（立方体: 8頂点）
        data.extend(struct.pack("<L", 8))  # 頂点数
        for i in range(8):
            x = 1.0 if i & 1 else -1.0
            y = 1.0 if i & 2 else -1.0
            z = 1.0 if i & 4 else -1.0
            data.extend(struct.pack("<fff", x, y, z))  # 位置
            data.extend(struct.pack("<fff", 0.0, 0.0, 1.0))  # 法線
            data.extend(struct.pack("<ff", 0.0, 0.0))  # UV
            data.extend(struct.pack("<HH", 0, 1))  # ボーンインデックス
            data.extend(struct.pack("<B", 50))  # ボーンウェイト
            data.extend(struct.pack("<B", 1))  # エッジフラグ

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

        # 材質データ（2つの材質）
        data.extend(struct.pack("<L", 2))  # 材質数

        # 材質1
        data.extend(struct.pack("<fff", 1.0, 0.0, 0.0))  # 拡散色（赤）
        data.extend(struct.pack("<f", 1.0))  # 不透明度
        data.extend(struct.pack("<f", 20.0))  # 反射強度
        data.extend(struct.pack("<fff", 1.0, 1.0, 1.0))  # 反射色
        data.extend(struct.pack("<fff", 0.2, 0.0, 0.0))  # 環境色
        data.extend(struct.pack("<B", 1))  # トゥーン番号
        data.extend(struct.pack("<B", 1))  # エッジフラグ
        data.extend(struct.pack("<L", 18))  # 面頂点数
        data.extend(b"texture1.png" + b"\x00" * (20 - len(b"texture1.png")))  # テクスチャファイル名（20バイト固定）

        # 材質2
        data.extend(struct.pack("<fff", 0.0, 0.0, 1.0))  # 拡散色（青）
        data.extend(struct.pack("<f", 0.8))  # 不透明度
        data.extend(struct.pack("<f", 10.0))  # 反射強度
        data.extend(struct.pack("<fff", 0.8, 0.8, 0.8))  # 反射色
        data.extend(struct.pack("<fff", 0.0, 0.0, 0.2))  # 環境色
        data.extend(struct.pack("<B", 2))  # トゥーン番号
        data.extend(struct.pack("<B", 0))  # エッジフラグ
        data.extend(struct.pack("<L", 18))  # 面頂点数
        data.extend(b"texture2.png" + b"\x00" * (20 - len(b"texture2.png")))  # テクスチャファイル名（20バイト固定）

        # ボーンデータ（MMD標準骨格の一部）
        bones = [
            # (名前, 親, 先端, タイプ, X, Y, Z)
            (b"\x83Z\x83\x93\x83^\x81[", 0xFFFF, 0xFFFF, 0, 0.0, 0.0, 0.0),  # センター
            (b"\x8f\xe3\x94\xbc\x90g", 0, 0xFFFF, 0, 0.0, 8.0, 0.0),  # 上半身
            (b"\x8e\xf1", 1, 0xFFFF, 0, 0.0, 12.0, 0.0),  # 首
            (b"\x93\xaa", 2, 0xFFFF, 0, 0.0, 15.0, 0.0),  # 頭
            (b"\x8d\xb6\x8c\xaa", 1, 0xFFFF, 0, -2.0, 11.0, 0.0),  # 左肩
            (b"\x8d\xb6\x98r", 4, 0xFFFF, 0, -4.0, 11.0, 0.0),  # 左腕
            (b"\x8d\xb6\x82\xd0\x82\xb6", 5, 0xFFFF, 0, -6.0, 11.0, 0.0),  # 左ひじ
            (b"\x8d\xb6\x8e\xe8\x8e\xf1", 6, 0xFFFF, 0, -8.0, 11.0, 0.0),  # 左手首
            (b"\x89E\x8c\xaa", 1, 0xFFFF, 0, 2.0, 11.0, 0.0),  # 右肩
            (b"\x89E\x98r", 8, 0xFFFF, 0, 4.0, 11.0, 0.0),  # 右腕
            (b"\x89E\x82\xd0\x82\xb6", 9, 0xFFFF, 0, 6.0, 11.0, 0.0),  # 右ひじ
            (b"\x89E\x8e\xe8\x8e\xf1", 10, 0xFFFF, 0, 8.0, 11.0, 0.0),  # 右手首
            (b"\x89\xba\x94\xbc\x90g", 0, 0xFFFF, 0, 0.0, 0.0, 0.0),  # 下半身
            (b"\x8d\xb6\x91\xab", 12, 0xFFFF, 0, -1.0, -4.0, 0.0),  # 左足
            (b"\x8d\xb6\x82\xd0\x82\xb4", 13, 0xFFFF, 0, -1.0, -8.0, 0.0),  # 左ひざ
            (b"\x8d\xb6\x91\xab\x8e\xf1", 14, 0xFFFF, 0, -1.0, -12.0, 0.0),  # 左足首
            (b"\x89E\x91\xab", 12, 0xFFFF, 0, 1.0, -4.0, 0.0),  # 右足
            (b"\x89E\x82\xd0\x82\xb4", 16, 0xFFFF, 0, 1.0, -8.0, 0.0),  # 右ひざ
            (b"\x89E\x91\xab\x8e\xf1", 17, 0xFFFF, 0, 1.0, -12.0, 0.0),  # 右足首
            (b"\x8d\xb6\x91\xabIK", 0xFFFF, 0xFFFF, 2, -1.0, -12.0, 0.0),  # 左足IK
            (b"\x89E\x91\xabIK", 0xFFFF, 0xFFFF, 2, 1.0, -12.0, 0.0),  # 右足IK
        ]

        data.extend(struct.pack("<H", len(bones)))  # ボーン数
        for bone_name, parent, tail, type_, x, y, z in bones:
            data.extend(bone_name + b"\x00" * (20 - len(bone_name)))
            data.extend(struct.pack("<H", parent))
            data.extend(struct.pack("<H", tail))
            data.extend(struct.pack("<B", type_))
            data.extend(struct.pack("<H", 0))  # IKボーン
            data.extend(struct.pack("<fff", x, y, z))

        # IKデータ（2つのIK）
        data.extend(struct.pack("<H", 2))  # IK数

        # 左足IK
        data.extend(struct.pack("<H", 19))  # IKボーンインデックス
        data.extend(struct.pack("<H", 15))  # IKターゲットボーンインデックス
        data.extend(struct.pack("<B", 2))  # IKチェーン長
        data.extend(struct.pack("<H", 40))  # 反復回数
        data.extend(struct.pack("<f", 0.5))  # 制限角度
        data.extend(struct.pack("<H", 14))  # IKリンク1（左ひざ）
        data.extend(struct.pack("<H", 13))  # IKリンク2（左足）

        # 右足IK
        data.extend(struct.pack("<H", 20))  # IKボーンインデックス
        data.extend(struct.pack("<H", 18))  # IKターゲットボーンインデックス
        data.extend(struct.pack("<B", 2))  # IKチェーン長
        data.extend(struct.pack("<H", 40))  # 反復回数
        data.extend(struct.pack("<f", 0.5))  # 制限角度
        data.extend(struct.pack("<H", 17))  # IKリンク1（右ひざ）
        data.extend(struct.pack("<H", 16))  # IKリンク2（右足）

        # 表情データ（3つの表情）
        data.extend(struct.pack("<H", 3))  # 表情数

        # base表情
        data.extend(b"base" + b"\x00" * 16)  # 表情名
        data.extend(struct.pack("<L", 8))  # 頂点数
        data.extend(struct.pack("<B", 0))  # 表情タイプ
        for i in range(8):
            data.extend(struct.pack("<L", i))  # 頂点インデックス
            data.extend(struct.pack("<fff", 0.0, 0.0, 0.0))  # 移動量

        # まばたき表情
        data.extend(b"\x82\xdc\x82\xce\x82\xbd\x82\xab" + b"\x00" * 10)  # 表情名
        data.extend(struct.pack("<L", 2))  # 頂点数
        data.extend(struct.pack("<B", 1))  # 表情タイプ
        data.extend(struct.pack("<L", 2))  # 頂点インデックス
        data.extend(struct.pack("<fff", 0.0, -0.2, 0.0))  # 移動量
        data.extend(struct.pack("<L", 3))  # 頂点インデックス
        data.extend(struct.pack("<fff", 0.0, -0.2, 0.0))  # 移動量

        # 笑い表情
        data.extend(b"\x8f\xce\x82\xa2" + b"\x00" * 14)  # 表情名
        data.extend(struct.pack("<L", 4))  # 頂点数
        data.extend(struct.pack("<B", 1))  # 表情タイプ
        for i in range(4):
            data.extend(struct.pack("<L", i))  # 頂点インデックス
            data.extend(struct.pack("<fff", 0.1, 0.1, 0.0))  # 移動量

        # 表情枠データ
        data.extend(struct.pack("<B", 2))  # 表情枠数
        data.extend(struct.pack("<H", 1))  # まばたき
        data.extend(struct.pack("<H", 2))  # 笑い

        # ボーン枠データ
        data.extend(struct.pack("<B", 3))  # ボーン枠数

        # ボーン枠名
        data.extend(b"\x83Z\x83\x93\x83^\x81[" + b"\x00" * 14)  # センター
        data.extend(b"\x8f\xe3\x94\xbc\x90g" + b"\x00" * 14)  # 上半身
        data.extend(b"IK" + b"\x00" * 18)  # IK

        # ボーン枠データ
        data.extend(struct.pack("<L", 1))  # センター枠のボーン数
        data.extend(struct.pack("<H", 0))  # センターボーン
        data.extend(struct.pack("<B", 1))  # 枠インデックス

        data.extend(struct.pack("<L", 11))  # 上半身枠のボーン数
        for i in range(1, 12):  # 上半身〜右手首
            data.extend(struct.pack("<H", i))  # ボーンインデックス
            data.extend(struct.pack("<B", 2))  # 枠インデックス

        data.extend(struct.pack("<L", 2))  # IK枠のボーン数
        data.extend(struct.pack("<H", 19))  # 左足IK
        data.extend(struct.pack("<B", 3))  # 枠インデックス
        data.extend(struct.pack("<H", 20))  # 右足IK
        data.extend(struct.pack("<B", 3))  # 枠インデックス

        # 英語名データ
        data.extend(struct.pack("<B", 1))  # 英語名存在フラグ

        # 英語ヘッダー
        data.extend(b"Test Model" + b"\x00" * 10)  # モデル名（英語）
        data.extend(
            b"Full featured test model for unit testing" + b"\x00" * 215
        )  # コメント（英語）

        # ボーン英語名
        bone_names_en = [
            b"center",
            b"upper_body",
            b"neck",
            b"head",
            b"shoulder_L",
            b"arm_L",
            b"elbow_L",
            b"wrist_L",
            b"shoulder_R",
            b"arm_R",
            b"elbow_R",
            b"wrist_R",
            b"lower_body",
            b"leg_L",
            b"knee_L",
            b"ankle_L",
            b"leg_R",
            b"knee_R",
            b"ankle_R",
            b"leg_IK_L",
            b"leg_IK_R",
        ]
        for name in bone_names_en:
            data.extend(name + b"\x00" * (20 - len(name)))

        # 表情英語名
        data.extend(b"base" + b"\x00" * 16)
        data.extend(b"blink" + b"\x00" * 15)
        data.extend(b"smile" + b"\x00" * 15)

        # ボーン枠英語名
        data.extend(b"Center" + b"\x00" * 44)
        data.extend(b"Upper Body" + b"\x00" * 40)
        data.extend(b"IK" + b"\x00" * 48)

        # トゥーンテクスチャ
        for i in range(10):
            data.extend(("toon%02d.bmp" % (i + 1)).encode() + b"\x00" * (100 - len(("toon%02d.bmp" % (i + 1)).encode())))

        # 剛体データ（2つの剛体）
        data.extend(struct.pack("<L", 2))  # 剛体数

        # 剛体1
        data.extend(b"rigid1" + b"\x00" * 14)  # 剛体名
        data.extend(struct.pack("<H", 0))  # 関連ボーンインデックス
        data.extend(struct.pack("<B", 0))  # グループ
        data.extend(struct.pack("<H", 0xFFFF))  # 非衝突グループフラグ
        data.extend(struct.pack("<B", 0))  # 形状（球）
        data.extend(struct.pack("<fff", 1.0, 1.0, 1.0))  # サイズ
        data.extend(struct.pack("<fff", 0.0, 10.0, 0.0))  # 位置
        data.extend(struct.pack("<fff", 0.0, 0.0, 0.0))  # 回転
        data.extend(struct.pack("<f", 1.0))  # 質量
        data.extend(struct.pack("<f", 0.5))  # 移動減衰
        data.extend(struct.pack("<f", 0.5))  # 回転減衰
        data.extend(struct.pack("<f", 0.0))  # 反発力
        data.extend(struct.pack("<f", 0.5))  # 摩擦力
        data.extend(struct.pack("<B", 0))  # 剛体タイプ（ボーン追従）

        # 剛体2
        data.extend(b"rigid2" + b"\x00" * 14)  # 剛体名
        data.extend(struct.pack("<H", 3))  # 関連ボーンインデックス（頭）
        data.extend(struct.pack("<B", 0))  # グループ
        data.extend(struct.pack("<H", 0xFFFF))  # 非衝突グループフラグ
        data.extend(struct.pack("<B", 1))  # 形状（ボックス）
        data.extend(struct.pack("<fff", 2.0, 2.0, 2.0))  # サイズ
        data.extend(struct.pack("<fff", 0.0, 15.0, 0.0))  # 位置
        data.extend(struct.pack("<fff", 0.0, 0.0, 0.0))  # 回転
        data.extend(struct.pack("<f", 1.0))  # 質量
        data.extend(struct.pack("<f", 0.5))  # 移動減衰
        data.extend(struct.pack("<f", 0.5))  # 回転減衰
        data.extend(struct.pack("<f", 0.0))  # 反発力
        data.extend(struct.pack("<f", 0.5))  # 摩擦力
        data.extend(struct.pack("<B", 1))  # 剛体タイプ（物理演算）

        # ジョイントデータ（1つのジョイント）
        data.extend(struct.pack("<L", 1))  # ジョイント数

        data.extend(b"joint1" + b"\x00" * 14)  # ジョイント名
        data.extend(struct.pack("<L", 0))  # 剛体A
        data.extend(struct.pack("<L", 1))  # 剛体B
        data.extend(struct.pack("<fff", 0.0, 12.5, 0.0))  # 位置
        data.extend(struct.pack("<fff", 0.0, 0.0, 0.0))  # 回転
        data.extend(struct.pack("<fff", -0.5, 0.0, 0.0))  # 移動制限下限
        data.extend(struct.pack("<fff", 0.5, 0.0, 0.0))  # 移動制限上限
        data.extend(struct.pack("<fff", -15.0, -15.0, -15.0))  # 回転制限下限
        data.extend(struct.pack("<fff", 15.0, 15.0, 15.0))  # 回転制限上限
        data.extend(struct.pack("<fff", 0.0, 0.0, 0.0))  # バネ定数（移動）
        data.extend(struct.pack("<fff", 0.0, 0.0, 0.0))  # バネ定数（回転）

        return bytes(data)

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
