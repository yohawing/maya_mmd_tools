# MMDエクスポート機能設計書

## 概要

シンプルな設計方針：
- 各Parserクラスに書き込み機能を追加
- エクスポーターは単にデータを集めてParserの書き込みメソッドを呼ぶだけ

## アーキテクチャ

```
データ収集 → Parser (読み書き両対応) → ファイル
```

## 実装方針

### Parser拡張

既存のParserクラスに`write_file()`メソッドを追加：

```python
class PmdParser:
    # 既存の読み込み機能
    def parse_file(self, file_path):
        """PMDファイルを読み込む"""
        
    # 新規追加：書き込み機能
    def write_file(self, file_path):
        """PMDファイルに書き込む"""
        with open(file_path, 'wb') as f:
            self._write_header(f)
            self._write_vertices(f)
            self._write_faces(f)
            self._write_materials(f)
            self._write_bones(f)
            self._write_ik_data(f)
            self._write_morphs(f)
            self._write_display_frames(f)
            self._write_rigid_bodies(f)
            self._write_joints(f)
```

### データクラス拡張

各データクラスに`write()`メソッドを追加：

```python
class PmdHeader:
    # 既存の読み込み機能
    def parse(self, f):
        """バイナリから読み込む"""
        
    # 新規追加：書き込み機能
    def write(self, f):
        """バイナリに書き込む"""
        f.write(b"Pmd")
        f.write(struct.pack("<f", self.version))
        f.write(utils.encodePMDString(self.model_name, 20))
        f.write(utils.encodePMDString(self.comment, 256))
```

### エクスポーター

エクスポーターは最小限の役割：

```python
class PmdExporter:
    def export(self, file_path, maya_data):
        # 1. Mayaデータから PmdParser インスタンスを作成
        parser = self._create_pmd_from_maya(maya_data)
        
        # 2. ファイルに書き込む
        parser.write_file(file_path)
    
    def _create_pmd_from_maya(self, maya_data):
        """MayaデータからPmdParserインスタンスを作成"""
        parser = PmdParser()
        
        # ヘッダー設定
        parser.header.model_name = maya_data.get('name', 'model')
        parser.header.comment = maya_data.get('comment', '')
        
        # 頂点データ変換
        for v in maya_data.get('vertices', []):
            vertex = PmdVertex()
            vertex.position = self._convert_position(v['pos'])
            vertex.normal = self._convert_normal(v['normal'])
            vertex.uv = v['uv']
            parser.vertices.append(vertex)
        
        # 他のデータも同様に変換...
        
        return parser
```

## 実装手順

1. **utilsに文字列エンコード関数を追加**
   ```python
   def encodePMDString(text, length):
       """文字列をShift-JISでエンコードして固定長にする"""
   ```

2. **各データクラスにwrite()メソッドを追加**
   - 各クラスのparse()の逆の処理を実装

3. **Parserクラスにwrite_file()メソッドを追加**
   - 各データクラスのwrite()を順番に呼ぶ

4. **エクスポーターでMayaデータを変換**
   - 座標系変換（X軸反転）
   - スケール変換（0.1倍）

## メリット

- Parser/データクラスが読み書き両対応で一貫性がある
- エクスポーターがシンプル
- テストが書きやすい（読み込み→書き込み→読み込みで検証）
- 既存コードの構造を活かせる