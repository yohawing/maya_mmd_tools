# PMD仕様 サマリー (v1.0対応)

## 概要

PMDは、MikuMikuDance（MMD）で使用される3Dモデルフォーマットです。PMXの前身として開発され、MMDの基本的な機能を提供します。

### 基本情報
- **形式**: バイナリのみ
- **バイト順**: リトルエンディアン
- **テキストエンコード**: Shift_JIS
- **現在の最新バージョン**: 1.0
- **互換性**: バージョン1.0固定

## 基本データ型

| 型名 | サイズ | 説明 |
|------|--------|------|
| BYTE | 1 | 符号なし |
| WORD | 2 | 符号なし |
| DWORD | 4 | 符号なし |
| float | 4 | 単精度実数 |
| float2 | 8 | Vector2 (X,Y) |
| float3 | 12 | Vector3 (X,Y,Z) |
| char[n] | n | 文字列（固定長） |

### 文字列の特徴
- **文字コード**: Shift_JIS
- **終端**: 0x00必須
- **パディング**: 0xFD
- **最大頂点数**: 65535（面頂点リストの頂点番号がWORDのため）

## データ構造概要

```
PMDヘッダ
頂点数 + 頂点データ
面頂点数 + 面頂点データ
材質数 + 材質データ
ボーン数 + ボーンデータ
IK数 + IKデータ
表情数 + 表情データ
表情枠表示リスト
ボーン枠名リスト
ボーン枠表示リスト
英語対応（オプション）
トゥーンテクスチャリスト
物理演算_剛体リスト
物理演算_ジョイントリスト
```

## PMDヘッダ

```
3 bytes: "Pmd" (ASCII)
4 bytes: float version (1.0)
20 bytes: char model_name (モデル名)
256 bytes: char comment (コメント)
```

## 頂点データ

```
DWORD: vert_count (頂点数)
t_vertex[vert_count]: 頂点データ (38Bytes/頂点)
```

### t_vertex構造
```
float3: pos (x, y, z) - 座標 (12 bytes)
float3: normal_vec (nx, ny, nz) - 法線ベクトル (12 bytes)
float2: uv (u, v) - UV座標 (8 bytes)
WORD[2]: bone_num - ボーン番号1、番号2 (4 bytes)
BYTE: bone_weight - ボーン1への影響度 (0-100) (1 byte)
BYTE: edge_flag - エッジフラグ (0:通常、1:エッジ無効) (1 byte)
```

### データサイズの注意点
- **総サイズ**: 38 bytes/頂点
- **配列順序**: WORD[2]とBYTE[2]は気づきにくい構造

### ボーンウェイト
- **bone_weight**: ボーン1への影響度（0-100）
- **ボーン2への影響度**: 100 - bone_weight

## 面頂点データ

```
DWORD: face_vert_count (面頂点数)
WORD[face_vert_count]: face_vert_index (頂点番号)
```

### 面の構成
- **3頂点で1面**: 三角形ポリゴン
- **頂点順序**: 時計回りで表面向き（実験結果：00 01 02で手前向き）
- **材質毎の面数**: 材質データで管理

## 材質データ

```
DWORD: material_count (材質数)
t_material[material_count]: 材質データ (70Bytes/材質)
```

### t_material構造
```
float3: diffuse_color (dr, dg, db) - 減衰色
float: alpha - 減衰色の不透明度
float: specularity - 光沢度
float3: specular_color (sr, sg, sb) - 光沢色
float3: mirror_color (mr, mg, mb) - 環境色(ambient)
BYTE: toon_index - トゥーンファイル番号
BYTE: edge_flag - 輪郭、影フラグ
DWORD: face_vert_count - この材質での面頂点数
char[20]: texture_file_name - テクスチャファイル名（終端0x00省略可）
```

### トゥーンインデックス
- **0xFF**: toon0.bmp
- **0x00**: toon01.bmp
- **0x01**: toon02.bmp
- **...**: ...
- **0x09**: toon10.bmp

## ボーンデータ

```
WORD: bone_count (ボーン数)
t_bone[bone_count]: ボーンデータ (39Bytes/ボーン)
```

### t_bone構造
```
char[20]: bone_name - ボーン名
WORD: parent_bone_index - 親ボーン番号 (0xFFFF:なし)
WORD: tail_pos_bone_index - tail位置のボーン番号 (0xFFFF:末端)
BYTE: bone_type - ボーンの種類
WORD: ik_parent_bone_index - IKボーン番号 (0:なし)
float3: bone_head_pos - ボーンヘッドの位置
```

### ボーンの種類
- **0**: 回転
- **1**: 回転と移動
- **2**: IK
- **3**: 不明
- **4**: IK影響下
- **5**: 回転影響下
- **6**: IK接続先
- **7**: 非表示
- **8**: 捻り (MMD 4.0～)
- **9**: 回転運動 (MMD 4.0～)

## IKデータ

```
WORD: ik_data_count (IKデータ数)
t_ik_data[ik_data_count]: IKデータ (可変長)
```

### t_ik_data構造
```
WORD: ik_bone_index - IKボーン番号
WORD: ik_target_bone_index - IKターゲットボーン番号
BYTE: ik_chain_length - IKチェーンの長さ
WORD: iterations - 再帰演算回数
float: control_weight - 演算1回あたりの制限角度
WORD[ik_chain_length]: ik_child_bone_index - IK影響下のボーン番号
```

### IKアルゴリズム
- **制御方式**: CCD-IK（Cyclic Coordinate Descent）
- **演算回数**: iterationsで指定
- **制限角度**: control_weightで指定（ラジアン）

## 表情データ

```
WORD: skin_count (表情数)
t_skin_data[skin_count]: 表情データ (可変長)
```

### t_skin_data構造
```
char[20]: skin_name - 表情名
DWORD: skin_vert_count - 表情用頂点数
BYTE: skin_type - 表情の種類
t_skin_vert_data[skin_vert_count]: 表情用頂点データ (16Bytes/頂点)
```

### 表情の種類
- **0**: base（基準）
- **1**: まゆ
- **2**: 目
- **3**: リップ
- **4**: その他

### t_skin_vert_data構造

#### type: base の場合
```
DWORD: skin_vert_index - 表情用頂点番号
float3: skin_vert_pos - 表情用頂点の座標
```

#### type: base以外の場合
```
DWORD: base_skin_vert_index - base表情での頂点番号
float3: skin_vert_pos_offset - 座標オフセット値
```

## 表示枠データ

### 表情枠用表示リスト
```
BYTE: skin_disp_count - 表示する表情数
WORD[skin_disp_count]: skin_index - 表情番号
```

### ボーン枠用枠名リスト
```
BYTE: bone_disp_name_count - 枠名数（センター枠除く）
char disp_name[bone_disp_name_count][50] - 枠名
```

### ボーン枠用表示リスト
```
DWORD: bone_disp_count - 表示するボーン数
t_bone_disp[bone_disp_count]: 枠用ボーンデータ (3Bytes/ボーン)
```

#### t_bone_disp構造
```
WORD: bone_index - 枠用ボーン番号
BYTE: bone_disp_frame_index - 表示枠番号 (00:センター、01～:その他)
```

## 英語対応（オプション）

```
BYTE: english_name_compatibility - 英名対応フラグ (01:対応あり)
```

### 英名対応ありの場合
```
char[20]: model_name_eg - モデル名（英語）
char[256]: comment_eg - コメント（英語）
char bone_name_eg[bone_count][20] - ボーン名（英語）
char skin_name_eg[skin_count-1][20] - 表情名（英語、base除く）
char disp_name_eg[bone_disp_name_count][50] - 枠名（英語）
```

## トゥーンテクスチャリスト

```
char toon_file_name[10][100] - トゥーンテクスチャファイル名（10個固定）
```

## 物理演算_剛体データ

```
DWORD: rigidbody_count - 剛体数
t_rigidbody[rigidbody_count]: 剛体データ (83Bytes/剛体)
```

### t_rigidbody構造
```
char[20]: rigidbody_name - 剛体名
WORD: rigidbody_rel_bone_index - 関連ボーン番号
BYTE: rigidbody_group_index - グループ番号
WORD: rigidbody_group_target - グループ対象（0xFFFFとの差）
BYTE: shape_type - 形状タイプ
float: shape_w - 形状の幅（半径）
float: shape_h - 形状の高さ
float: shape_d - 形状の奥行
float3: pos_pos - 位置
float3: pos_rot - 回転（ラジアン）
float: rigidbody_weight - 質量
float: rigidbody_pos_dim - 移動減衰
float: rigidbody_rot_dim - 回転減衰
float: rigidbody_recoil - 反発力
float: rigidbody_friction - 摩擦力
BYTE: rigidbody_type - 物理演算タイプ
```

### 形状タイプ
- **0**: 球
- **1**: 箱
- **2**: カプセル

### 物理演算タイプ
- **0**: Bone追従
- **1**: 物理演算
- **2**: 物理演算（Bone位置合わせ）

## 物理演算_ジョイントデータ

```
DWORD: joint_count - ジョイント数
t_joint[joint_count]: ジョイントデータ (124Bytes/ジョイント)
```

### t_joint構造
```
char[20]: joint_name - ジョイント名
DWORD: joint_rigidbody_a - 剛体A
DWORD: joint_rigidbody_b - 剛体B
float3: joint_pos - 位置
float3: joint_rot - 回転（ラジアン）
float3: constrain_pos_1 - 制限：移動1
float3: constrain_pos_2 - 制限：移動2
float3: constrain_rot_1 - 制限：回転1
float3: constrain_rot_2 - 制限：回転2
float3: spring_pos - ばね：移動
float3: spring_rot - ばね：回転
```

### 制限値の注意点
設定ボックスの並び順と記録される値の並び順が異なります：
- **設定ボックス**: 移動1x - 移動2x 移動1y - 移動2y 移動1z - 移動2z
- **記録順**: 移動1x 移動1y 移動1z 移動2x 移動2y 移動2z

### 物理演算
PMDの物理演算は以下の順序で実行されます：

```
ボーン変形（物理前） → 物理演算 → ボーン変形（物理後）
```

#### 物理演算タイプ
- **0（Bone追従）**: ボーンに追従
- **1（物理演算）**: 物理演算のみ
- **2（物理演算+Bone位置合わせ）**: 物理演算とボーン位置の合わせ

## 実装時の注意点

### 文字列処理
1. **文字コード**: Shift_JIS固定
2. **終端処理**: 0x00必須（例外あり）
3. **パディング**: 0xFD使用
4. **固定長**: 各文字列フィールドは固定長

### データ制限
1. **頂点数**: 最大65535（WORDのため）
2. **面の向き**: 時計回りで表面
3. **ボーン階層**: 親子関係の循環参照に注意
4. **IKチェーン**: 長さ制限とループ回数

### 物理演算
1. **座標系**: 右手座標系
2. **単位系**: メートル、キログラム、秒
3. **回転**: ラジアン単位
4. **制限値**: 設定順と記録順の差異に注意

### 互換性
1. **バージョン**: 1.0固定
2. **エンディアン**: リトルエンディアン必須
3. **英語対応**: オプション機能
4. **トゥーン**: 10個固定ファイル

## PMXとの主な違い

### 制限事項
- 固定長文字列（PMXは可変長）
- Shift_JIS固定（PMXはUTF-8/UTF-16対応）
- 頂点数制限（PMXは最適化サイズ）
- 単純なモーフシステム（PMXは高度）

### 機能差
- 追加UV非対応
- 材質パラメータ制限
- 単純な物理演算
- 表示枠の制限

### データサイズ
- 固定サイズ構造
- 非効率的なパディング
- 英語対応での重複データ

---
*本文書は元のPMD仕様書から実装に必要な重要項目を抽出したサマリーです。*
*詳細な実装については元の仕様書を参照してください。*

- **PMD仕様書**: [PMD形式めも](https://blog.goo.ne.jp/torisu_tetosuki/e/209ad341d3ece2b1b4df24abf619d6e4)