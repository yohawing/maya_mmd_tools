# コーディング規約

Maya MMD Toolsプロジェクトのコーディング規約とガイドラインです。

## 基本原則

- Python 3.7以降を使用してください。
- PEP 8に準拠したコードスタイルを使用してください。
- コメントとドキュメンテーションは、コードの可読性を高めるために重要です。関数やクラスの説明を適切に記述してください。
- 可能な限り、コードの再利用性を考慮してください。共通の機能は`core`ディレクトリに配置し、他のモジュールからインポートして使用します。
- 例外処理を適切に行い、エラーが発生した場合はユーザーにわかりやすいメッセージを表示してください。
- 外部モジュールを追加したくないので、ライブラリは標準のものをなるべく使用してください。
- IMPORTANT: 1つのファイルが長すぎる場合は、機能ごとにファイルを分割してください。
- IMPORTANT: 基本的に１つのファイルに複数のクラスを作るのは避けてください。

## Python コーディングスタイル

### Docstring
Googleスタイルのdocstringを使用して、関数やクラスの目的、引数、戻り値を明確に記述してください。

```python
def parse_pmx_header(self, file):
    """PMXファイルのヘッダー情報を解析する。

    Args:
        file: 読み込み対象のファイルオブジェクト

    Returns:
        dict: ヘッダー情報を含む辞書

    Raises:
        MMDParseException: ヘッダーの解析に失敗した場合
    """
```

### 命名規則
- クラス: PascalCase
- 関数/変数: snake_case
- 定数: UPPER_SNAKE_CASE
- プライベート: 先頭に _

### インポート順序
標準ライブラリ → サードパーティ → ローカル（ruffが自動整理）

```python
# 標準ライブラリ
import os
import sys
from typing import Dict, List

# サードパーティ（Maya）
import maya.cmds as cmds
import maya.api.OpenMaya as om

# ローカル
from mmd_tools.core import utils
from mmd_tools.core.logger import get_logger
```

### UI開発
- UIの作成は、pyside2にフォールバック可能なpyside6のコードで記述してください。
- 高速化が期待できる箇所は、Maya Python API2.0を使用してください。

## ロガーの使用方法

このプロジェクトでは、Maya環境に最適化された統一的なロガーシステムを使用します。
`mmd_tools.core.logger`モジュールの`get_logger`関数を使用してロガーインスタンスを取得してください。

### 基本的な使用方法

```python
from mmd_tools.core.logger import get_logger

# ロガーインスタンスを取得（モジュール名を使用）
logger = get_logger(__name__)

# ログメッセージの出力
logger.debug("デバッグメッセージ")
logger.info("情報メッセージ")
logger.warning("警告メッセージ")
logger.error("エラーメッセージ")
logger.critical("重大なエラーメッセージ")
```

### 注意事項

- ロガー名には通常`__name__`を使用してください。これによりモジュール階層に基づいたログ出力が可能になります
- パフォーマンスを考慮し、頻繁に呼ばれる処理では`logger.debug()`の使用を控えめにしてください
- エラーハンドリング時は、例外情報も含めてログに記録することを推奨します：
  ```python
  logger.error("エラーが発生しました", exc_info=True)
  ```

## よく使うUtility関数

### `mmd_tools.core.maya_utils`

OpenMaya API 2.0を使用してアトリビュート値を設定します。
cmds.setAttrの代わりに使用します。

```python
maya_utils.set_attribute("pCube1", "customAttr1", 1.0, "float")
maya_utils.set_attribute("pCube1", "customAttr2", "example", "str")
maya_utils.set_attribute("pCube1", "customAttr3", [0.5, 0.5, 0.5], "double3")
```

OpenMaya API 2.0を使用してアトリビュート値を取得します。
cmds.getAttrの代わりに使用します。
```python
value = maya_utils.get_attribute("pCube1", "customAttr1")
```

MayaオブジェクトにExtraAttributeを設定します。無ければ作成します。
```python
maya_utils.set_custom_attributes("pCube1", {"floatAttr": 1.0, "doubleAttr": "test", "double3Attr": (1.0, 2.0, 3.0)})
```

## ユーティリティークラスの使用方法

`mmd_tools.core.utils`モジュールには、Maya環境に依存しないユーティリティ関数が含まれています。
`mmd_tools.core.maya_utils`モジュールには、Maya環境に特化したユーティリティ関数が含まれています。

新しい汎用的な関数などを実装する時は、Utilityモジュールに追加できないか検討してください。

## コード品質の維持

### Lintツール
プロジェクトではruffを使用してコードスタイルをチェックします：

```bash
# コードスタイルチェック
ruff check .

# 自動修正
ruff format .
```

### テスト
新しい機能を追加する際は、必ず対応するテストを書いてください：

```python
# tests/unit/test_your_module.py
class TestYourModule(TestCase):
    def test_your_function(self):
        """関数の基本的な動作をテスト"""
        result = your_function(input_data)
        self.assertEqual(result, expected_output)
```

## 参考リンク

- [PEP 8 -- Style Guide for Python Code](https://www.python.org/dev/peps/pep-0008/)
- [Google Python Style Guide](https://google.github.io/styleguide/pyguide.html)
- [Maya Python API 2.0 Documentation](https://help.autodesk.com/view/MAYAUL/2024/ENU/?guid=MAYA_API_REF_py_ref_index_html)