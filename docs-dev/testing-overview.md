# テストドキュメント

## 概要

本ドキュメントは、Autodesk Maya 用 MikuMikuDance (MMD) ファイルインポートプラグインのテスト戦略について記述します。プラグインの各コンポーネント（MMD ファイルパーサー、Maya データコンバーター、UI）の品質と正確性を保証することを目的とします。

## テスト実行システム

### アーキテクチャ

プロジェクトでは、すべてのテスト（ユニット/統合）をMaya環境内で実行する統一されたテストシステムを採用しています：

```
tests/
├── common/                  # テスト共通ユーティリティ
│   ├── test_base.py        # 基本テストクラス
│   ├── maya_test_base.py   # Mayaテスト基本クラス
│   └── custom_test_runner.py # カスタムテストランナー
├── unit/                    # ユニットテスト
├── integration/             # 統合テスト
├── run_tests.py            # メインエントリーポイント
└── maya_test_runner.py     # Maya環境内でのテスト実行
```

### 特徴

- **統一された実行環境**: すべてのテストがmayapy経由で実行されるため、環境差異による問題を排除
- **シンプルな構造**: run_tests.pyが引数解析とmayapy起動のみを担当し、実際のテスト実行はmaya_test_runner.pyが処理
- **柔軟なテストフィルタリング**: 特定のテストモジュール、クラス、メソッドを指定して実行可能
- **カラー対応出力**: テスト結果が色分けされて表示され、視認性が向上

### Nox タスクランナー

開発用の共通入口として `noxfile.py` を使用します。Nox は追加の仮想環境を作らず、既存の `mayapy` / CMake / Cargo ランナーを呼び出す薄いタスクランナーとして扱います。

基本コマンド:

```bash
# 既存のユニットテストを実行
uvx nox -s tests

# 既存の統合テストを実行
uvx nox -s tests -- --type integration

# mmd-anim FFI をビルド
uvx nox -s ffi_build

# Python から native runtime をロードできるか確認
uvx nox -s native_smoke

# Maya C++ プラグインをビルド
uvx nox -s cpp_build -- --maya 2024 --config Debug

# mayapy で C++ プラグインをロードし、mmdRuntimeInstance ノードを作成
uvx nox -s maya_smoke -- --maya 2024 --config Debug

# C++/native 経路をまとめて CLI だけで検証
uvx nox -s cpp_verify -- --maya 2024 --config Debug
```

Maya の場所は `MAYA_LOCATION` または `MAYA_LOCATION_2024`、devkit の場所は `MAYA_DEVKIT_ROOT` または `MAYA_DEVKIT_ROOT_2024` で上書きできます。Windows では既定で `C:/Program Files/Autodesk/Maya2024`、macOS では `/Applications/Autodesk/maya2024/Maya.app/Contents` を探索します。

### 基本的なテスト実行方法

#### 全てのテストを実行

```bash
# 全てのユニットテストを実行
python tests/run_tests.py --type unit

# 全ての統合テストを実行
python tests/run_tests.py --type integration
```

#### 特定のテストを実行

```bash
# 特定のテストモジュールを実行
python tests/run_tests.py --type unit --test test_pmd_parser
python tests/run_tests.py --type integration --test test_maya_utils

# 特定のテストクラスを実行
python tests/run_tests.py --type unit --test TestPmdParser

# 特定のテストメソッドを実行
python tests/run_tests.py --type unit --test test_parse_pmd_header_success
```

### 高度なオプション

#### Maya バージョンの指定

```bash
# 特定のMayaバージョンを指定（デフォルトは2024）
python tests/run_tests.py --type integration --maya 2023 --test test_maya_utils
python tests/run_tests.py --type integration --maya 2025
```


### テストの発見とデバッグ

#### 利用可能なテストの確認

存在しないテスト名を指定すると、利用可能なテストの一覧が表示されます：

```bash
python tests/run_tests.py --type integration --test nonexistent_test
```

出力例：
```
Error: No tests found matching '--test nonexistent_test' in the 'integration' suite.

Available tests in this suite:
  - test_animation_converter.TestAnimationConverter.test_convert_vmd_animation
  - test_maya_utils.TestMayaUtils.test_assign_material
  - test_maya_utils.TestMayaUtils.test_create_material
  - test_maya_utils.TestMayaUtils.test_create_mesh_with_uvs
  - test_maya_utils.TestMayaUtils.test_sanitize_maya_name
  - test_maya_utils.TestMayaUtils.test_set_custom_attributes
  - test_mesh_converter.TestMeshConverter.test_convert_pmd_mesh
  - test_mesh_converter.TestMeshConverter.test_convert_pmx_mesh
  ...
```

### エラーハンドリング

#### テストの発見に失敗した場合

```bash
# 指定したテストが見つからない場合
python tests/run_tests.py --type integration --test invalid_test

# 出力: エラーメッセージと利用可能なテストの一覧
```

#### Maya環境の問題

```bash
# mayapyが見つからない場合
Error: mayapy executable not found at C:\Program Files\Autodesk\Maya2024\bin\mayapy.exe.

# 別のMayaバージョンを試す
python tests/run_tests.py --type integration --maya 2023
```

## テストフィクスチャ

### TestFixtureProviderの使用

`TestFixtureProvider`は、テストで使用するMMDファイル（PMD、PMX、VMD）やテクスチャファイルへのアクセスを提供するクラスです。`tests/data`ディレクトリに配置されたテストファイルを自動的に探索し、キャッシュして高速にアクセスできるようにします。

#### 基本的な使用方法

```python
from tests.common.test_fixture_provider import TestFixtureProvider

class TestPmdParser(unittest.TestCase):
    def setUp(self):
        self.fixture_provider = TestFixtureProvider()
        
    def test_parse_pmd_file(self):
        """PMDファイルのパーステスト"""
        # デフォルトのPMDファイルを取得
        pmd_path = self.fixture_provider.get_pmd_file()
        
        # 特定のPMDファイルを取得（拡張子なしで指定）
        specific_pmd = self.fixture_provider.get_pmd_file('miku_v2')
        
        # パーサーでファイルを読み込む
        parser = PmdParser()
        result = parser.parse(pmd_path)
        self.assertIsNotNone(result)
```

#### 主要メソッド

##### ファイルパス取得メソッド

- `get_pmd_file(name=None)`: PMDファイルのパスを取得
- `get_pmx_file(name=None)`: PMXファイルのパスを取得
- `get_vmd_file(name=None)`: VMDファイルのパスを取得
- `get_texture_file(model_name, texture_name)`: テクスチャファイルのパスを取得

##### 利用可能なファイル一覧取得メソッド

- `get_available_pmd_files()`: 利用可能なPMDファイル名のリスト
- `get_available_pmx_files()`: 利用可能なPMXファイル名のリスト
- `get_available_vmd_files()`: 利用可能なVMDファイル名のリスト

##### データロードメソッド（キャッシュ機能付き）

- `load_pmd_data(name=None)`: PMDファイルをパースしてTupleで返す
- `load_pmx_data(name=None)`: PMXファイルをパースしてTupleで返す
- `load_vmd_data(name=None)`: VMDファイルをパースしてTupleで返す

##### 一時ファイル作成メソッド

- `create_temp_file(content, extension)`: 一時ファイルを作成してパスを返す
- `cleanup_temp_files()`: 作成した一時ファイルをすべて削除

#### 高度な使用例

```python
class TestTextureHandling(unittest.TestCase):
    def setUp(self):
        self.fixture_provider = TestFixtureProvider()
        
    def tearDown(self):
        # 一時ファイルのクリーンアップ
        self.fixture_provider.cleanup_temp_files()
        
    def test_texture_conversion(self):
        """テクスチャ変換のテスト"""
        # 利用可能なPMXファイルを確認
        available_files = self.fixture_provider.get_available_pmx_files()
        print(f"Available PMX files: {available_files}")
        
        # PMXデータをロード（キャッシュされる）
        pmx_data = self.fixture_provider.load_pmx_data('model_with_textures')
        
        # 一時的なテクスチャファイルを作成
        test_texture = b'\x89PNG\r\n\x1a\n...'  # PNGバイナリデータ
        temp_texture_path = self.fixture_provider.create_temp_file(test_texture, '.png')
        
        # テクスチャ変換処理
        converter = TextureConverter()
        result = converter.convert(temp_texture_path)
        self.assertTrue(result)
```

#### カスタムデータディレクトリの指定

```python
# デフォルトは tests/data ディレクトリ
default_provider = TestFixtureProvider()

# カスタムディレクトリを指定
custom_provider = TestFixtureProvider(data_dir='/path/to/custom/test/data')
```

### 注意事項

- TestFixtureProviderは初期化時にディレクトリを探索してファイルをキャッシュするため、大量のテストファイルがある場合でも高速に動作します
- ファイル名は拡張子なしで指定します（例: 'miku_v2.pmd' → 'miku_v2'）
- テストファイルが見つからない場合は`FileNotFoundError`が発生します
- 一時ファイルは`tearDown`で必ず`cleanup_temp_files()`を呼び出してクリーンアップしてください

### テストデータの配置

テストで使用するデータファイルは以下のディレクトリに配置されています：

- `tests/data/`: 統合テスト用のMMDファイル（PMD、PMX、VMD）とテクスチャファイル
- `tests/data/for_unit_test/`: 単体テスト用の軽量なテストデータ
  - `test_1bone_cube.pmx`: 1ボーンのみを持つシンプルなキューブモデル
  - `test_basic_bone.pmx`: 基本的なボーン構造を持つモデル
  - `test_semi_basic_bone.pmx`: やや複雑なボーン構造を持つモデル

## モックシステムの詳細

### PMD/PMX/VMDファイルフォーマットのモック

プロジェクトには、テスト用のMMDファイルフォーマットモックが実装されています：

```
tests/common/
├── pmd_mock.py           # PMDファイルフォーマットのモック
├── pmx_mock.py           # PMXファイルフォーマットのモック
└── vmd_mock.py           # VMDファイルフォーマットのモック
```

これらのモックは、実際のバイナリファイルを作成せずにMMDデータ構造をテストできるようにします。

### 使用例

```python
# PMDモックの使用
from tests.common.pmd_mock import create_test_pmd_data

pmd_data = create_test_pmd_data()
# pmd_dataを使用してパーサーやコンバーターをテスト

# PMXモックの使用
from tests.common.pmx_mock import create_test_pmx_data

pmx_data = create_test_pmx_data()
# pmx_dataを使用してテスト

# VMDモックの使用
from tests.common.vmd_mock import create_test_vmd_data

vmd_data = create_test_vmd_data()
# vmd_dataを使用してアニメーションテスト
```

## UIテスト

UIのテストは2つのカテゴリに分類されます。

### 新規追加テスト: UITranslatorのGUIテスト

多言語対応システム（UITranslator）のGUIテストが`tests/gui/guitest_translator.py`に追加されました。

#### テスト内容
- UITranslatorのシングルトン動作確認
- サポート言語（日本語、英語、繁体字中国語、簡体字中国語）の確認
- 翻訳ファイルの読み込み確認
- 各言語での翻訳動作確認
- 言語切り替え時のUI更新確認
- BaseTabクラスとの統合確認
- 特殊文字を含む翻訳の処理確認

#### 実行方法
```bash
# すべてのGUIテストを実行（translatorテストを含む）
python tests/run_gui_tests.py

# Maya内から直接実行する場合
# Script Editorで以下を実行:
import sys
sys.path.append(r'F:\Develop\maya_mmd_tools')
from tests.gui.guitest_translator import TestUITranslator
import unittest
suite = unittest.TestLoader().loadTestsFromTestCase(TestUITranslator)
unittest.TextTestRunner(verbosity=2).run(suite)
```

### テストディレクトリ構造

```
tests/
├── unit/                    # ユニットテスト（UIのビジネスロジックを含む）
│   ├── test_application_state.py
│   ├── test_info_presenter.py
│   └── test_import_export_presenter.py
└── gui/                     # GUI環境でのみ実行可能なテスト
    ├── gui_test_base.py
    ├── run_gui_tests.py
    └── test_ui_components.py
```

### 1. UIビジネスロジックテスト（Unit）

Maya standalone環境で実行可能なUIのビジネスロジックテスト。PresenterやApplicationStateなどをテストします。

#### 特徴
- Qtウィジェットはモック化される
- Maya APIは完全に利用可能
- CI/CDパイプラインで自動実行可能
- 高速に実行できる

#### 実行方法
```bash
# すべてのユニットテストを実行（UIビジネスロジックを含む）
python tests/run_tests.py --type unit

# 特定のUIテストを実行
python tests/run_tests.py --type unit --test test_info_presenter
```

#### テスト例
```python
from unittest.mock import Mock
from tests.common.maya_test_base import MayaTestBase
from mmd_tools.ui.presenters.info_presenter import InfoPresenter

class TestInfoPresenter(MayaTestBase):
    def setUp(self):
        super().setUp()
        # Qtウィジェットをモック化
        self.mock_view = Mock()
        self.presenter = InfoPresenter(self.mock_view)
    
    def test_update_model_info(self):
        """モデル情報更新のテスト"""
        # テスト用のMMDモデルを作成
        model = self._create_test_mmd_model()
        
        # プレゼンターのロジックをテスト
        self.presenter.update_model_info(model)
        
        # viewへの呼び出しを検証
        self.mock_view.set_model_name_jp.assert_called()
```

### 2. GUIインタラクションテスト

実際のQtウィジェットの作成、表示、インタラクションをテストします。
MayaのGUIセッションが必要です。

#### 特徴
- 実際のQtウィジェットを作成・操作
- ウィンドウの表示やUIイベントをテスト
- 実行には完全なMaya GUI環境が必要

#### 実行方法

##### 方法1: コマンドラインからの自動実行 (推奨)

新しく導入されたテストランナーを使用し、コマンドラインからGUIテストを自動実行します。
この方法は、Mayaの起動、テスト実行、終了までを完全に自動化します。

```bash
# tests/gui ディレクトリ内のすべてのGUIテストを実行
python tests/run_gui_tests.py

# 特定のMayaバージョンを指定して実行
python tests/run_gui_tests.py --maya_version 2023
```

このコマンドは以下の処理を自動的に行います。
1. Mayaアプリケーションをバックグラウンドで起動します。
2. `commandPort` を通じてMayaに接続します。
3. `tests/gui` ディレクトリ内のテストを実行するよう指示します。
4. テストのログを `logs/ui_test_results.log` に出力し、同時にコンソールにも表示します。
5. テスト完了後、Mayaアプリケーションを自動的に終了します。

##### 方法2: Maya GUI内からの手動実行 (旧方式)

従来通り、MayaのScript Editorやシェルフから手動でテストを実行することも可能です。

**Script Editorから実行:**
```python
# Maya Script Editorで以下を実行
import sys
# プロジェクトルートへのパスを適宜変更してください
sys.path.append(r'F:\Develop\maya_mmd_tools')
from tests.gui import run_gui_tests

# すべてのGUIテストを実行
run_gui_tests.run()
```

**シェルフボタンから実行:**
1. `scripts/run_ui_tests_gui.py` の内容は、コマンドラインランナーに置き換えられました。シェルフから実行したい場合は、上記Script Editorのコードをシェルフボタンに登録してください。

#### GuiTestBaseクラス
GUI環境でのテストをサポートする基底クラスが提供されています：

```python
from tests.common.gui_test_base import GuiTestBase, requires_gui

@requires_gui
class TestMainWindow(GuiTestBase):
    def test_window_creation(self):
        """ウィンドウが正しく作成されるかテスト"""
        from mmd_tools.ui.main_window import MainWindow
        window = MainWindow()
        self.assertIsNotNone(window)
        window.close()
```

### GUIテストの注意事項

- コマンドラインからの実行 (`scripts/run_ui_tests_gui.py`) を推奨します。
- CI/CD環境など、GUIを持たない環境ではGUIインタラクションテストは実行できません。


## テストの実行環境

### 必要な環境

*   **Maya:** テストは特定の Maya バージョン（例: Maya 2023, 2024）で実行されることを想定します。
*   **Python:** Maya にバンドルされている Python 環境 (`mayapy.exe`) を使用します。
*   **OS:** Windows、macOS、Linux（WSL環境でのWindows版Mayaの実行もサポート）

### WSL環境での実行

WSL環境でWindows版のMayaを使用する場合、run_tests.pyが自動的にパスを変換します：

```bash
# WSL環境から実行
python tests/run_tests.py --type unit

# 自動的にWindowsパスに変換されて実行される
# /mnt/c/Program Files/Autodesk/Maya2024/bin/mayapy.exe
```

## テストの自動化 (今後の検討事項)

*   CI/CD パイプラインへのテスト組み込み
*   テストカバレッジの測定と可視化
*   パフォーマンステストの追加
