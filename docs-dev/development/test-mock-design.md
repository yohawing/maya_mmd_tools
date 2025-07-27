# テストモックについて

## 概要

Unitテストや統合テストのベースクラスに統合され、テストデータを提供するためのモッククラスを定義します。

現在のテストスイートは実際のMMDファイルを使用した統合テストが中心となっており、外部依存を排除した純粋なユニットテストのためのモッククラスが不足しています。この設計により、パーサーの各コンポーネントを独立してテストできるようになります。

## 設計原則

### 独立性
- 各モッククラスは外部ファイルに依存せず、自己完結的にテストデータを生成
- 実際のファイルI/Oを行わず、メモリ上でのみ動作

### 可読性
- テストデータの構造と内容を理解しやすい形で定義
- コードレビューやデバッグが容易

### 拡張性
- 新しいテストケースの追加が簡単
- 異常系や境界値のテストデータも容易に生成可能

## モッククラスの詳細設計

### PmdMock

**目的**: PMDパーサーのユニットテスト用バイナリデータを提供

**主要メソッド**:
```python
class PmdMock:
    @staticmethod
    def create_minimal_pmd() -> bytes:
        """最小限のPMDファイルバイナリデータを生成"""
        
    @staticmethod
    def create_full_pmd() -> bytes:
        """全機能を含むPMDファイルバイナリデータを生成"""
        
    @staticmethod
    def create_invalid_pmd() -> bytes:
        """不正なPMDファイルバイナリデータを生成（エラーテスト用）"""
        
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
        joint_count: int = 0
    ) -> bytes:
        """カスタムパラメータでPMDファイルバイナリデータを生成"""
```

**生成データの特徴**:
- 立方体メッシュ（8頂点、12面）をベースとした最小限のモデル
- 日本語・英語の両方のモデル名とコメント
- MMDで使用される、基本的なボーン構造を模倣
- 異常系テスト用の破損データ

### PmxMock

**目的**: PMXパーサーのユニットテスト用バイナリデータを提供

**主要メソッド**:
```python
class PmxMock:
    @staticmethod
    def create_minimal_pmx(version: float = 2.0) -> bytes:
        """最小限のPMXファイルバイナリデータを生成"""
        
    @staticmethod
    def create_full_pmx(version: float = 2.1) -> bytes:
        """全機能を含むPMXファイルバイナリデータを生成"""
        
    @staticmethod
    def create_invalid_pmx() -> bytes:
        """不正なPMXファイルバイナリデータを生成（エラーテスト用）"""
        
    @staticmethod
    def create_custom_pmx(
        version: float = 2.0,
        encoding: int = 0,  # 0=UTF16LE, 1=UTF8
        vertex_count: int = 8,
        face_count: int = 12,
        texture_count: int = 1,
        material_count: int = 1,
        bone_count: int = 3,
        morph_count: int = 5,
        display_frame_count: int = 1,
        rigid_body_count: int = 0,
        joint_count: int = 0,
        soft_body_count: int = 0
    ) -> bytes:
        """カスタムパラメータでPMXファイルバイナリデータを生成"""
```

**生成データの特徴**:
- PMX 2.0/2.1の両バージョンに対応
- UTF-16LE/UTF-8エンコーディングの両方に対応
- 頂点モーフ、ボーンモーフ、材質モーフなど各種モーフ
- 表示枠、剛体、ジョイントなど物理演算要素

### VmdMock

**目的**: VMDパーサーのユニットテスト用バイナリデータを提供

**主要メソッド**:
```python
class VmdMock:
    @staticmethod
    def create_minimal_vmd() -> bytes:
        """最小限のVMDファイルバイナリデータを生成"""
        
    @staticmethod
    def create_full_vmd() -> bytes:
        """全機能を含むVMDファイルバイナリデータを生成"""
        
    @staticmethod
    def create_camera_vmd() -> bytes:
        """カメラアニメーション用VMDファイルバイナリデータを生成"""
        
    @staticmethod
    def create_invalid_vmd() -> bytes:
        """不正なVMDファイルバイナリデータを生成（エラーテスト用）"""
        
    @staticmethod
    def create_custom_vmd(
        model_name: str = "TestModel",
        bone_frame_count: int = 10,
        morph_frame_count: int = 5,
        camera_frame_count: int = 0,
        light_frame_count: int = 0,
        shadow_frame_count: int = 0,
        ik_frame_count: int = 0
    ) -> bytes:
        """カスタムパラメータでVMDファイルバイナリデータを生成"""
```

**生成データの特徴**:
- モデル用とカメラ用の両方のVMDデータ
- ボーンフレーム、モーフフレーム、カメラフレーム
- 補間データの設定
- IK表示/非表示フレーム

## TestFixtureProvider

**目的**: tests/dataディレクトリの実際のファイルを管理し、統合テストに提供
**分類**: Test Fixture Provider - テストフィクスチャを提供するクラス

**主要メソッド**:
```python
class TestFixtureProvider:
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
    
    def _scan_files(self):
        """ディレクトリを再帰的に探索してファイルキャッシュを作成"""
        
    def get_pmd_file(self, name: str = None) -> str:
        """PMDファイルのパスを取得
        
        Args:
            name: 特定のファイル名（拡張子なし）。Noneの場合は最初に見つかったファイル
            
        Returns:
            ファイルパス
            
        Raises:
            FileNotFoundError: ファイルが見つからない場合
        """
        
    def get_pmx_file(self, name: str = None) -> str:
        """PMXファイルのパスを取得"""
        
    def get_vmd_file(self, name: str = None) -> str:
        """VMDファイルのパスを取得"""
        
    def get_texture_file(self, model_name: str, texture_name: str) -> str:
        """テクスチャファイルのパスを取得"""
        
    def get_available_pmd_files(self) -> List[str]:
        """利用可能なPMDファイルの一覧を取得"""
        
    def get_available_pmx_files(self) -> List[str]:
        """利用可能なPMXファイルの一覧を取得"""
        
    def get_available_vmd_files(self) -> List[str]:
        """利用可能なVMDファイルの一覧を取得"""
        
    def load_pmd_data(self, name: str = None) -> dict:
        """PMDファイルをロードしてパース済みデータを返す（キャッシュあり）"""
        
    def load_pmx_data(self, name: str = None) -> dict:
        """PMXファイルをロードしてパース済みデータを返す（キャッシュあり）"""
        
    def load_vmd_data(self, name: str = None) -> dict:
        """VMDファイルをロードしてパース済みデータを返す（キャッシュあり）"""
        
    def create_temp_file(self, content: bytes, extension: str) -> str:
        """一時ファイルを作成してパスを返す"""
        
    def cleanup_temp_files(self):
        """作成した一時ファイルを削除"""
```

**キャッシュ機能**:
- **ファイルキャッシュ**: 初期化時に一度だけディレクトリを再帰的に探索してファイルパスをキャッシュ
- **データキャッシュ**: パース済みデータをメモリにキャッシュ
- **高速化**: 重複するファイル読み込みとディレクトリ探索を避けて高速化
- **テスト間でデータを共有**: 同じインスタンスを使い回せばキャッシュが効く

**利用可能なテストデータ**:
- tests/dataディレクトリに配置された実際のPMD、PMX、VMDファイルを使用
- 初期化時にディレクトリを再帰的に探索してファイル一覧を作成
- 外部から（テストランナーから）指定されたモデルを受け取ることも可能
- モデル名はハードコードせず、動的に利用可能なファイルから選択

**設計方針**:
- **パフォーマンス重視**: 初期化時の一度だけの探索でテスト実行を高速化
- **柔軟性**: ファイル名を指定しない場合は最初に見つかったファイルを使用
- **エラーハンドリング**: ファイルが見つからない場合は明確な例外を発生

## 使用例

### ユニットテスト（モッククラス使用）

```python
class TestPmdParser(TestBase):
    def test_parse_minimal_pmd(self):
        """最小限のPMDファイルのパーステスト"""
        binary_data = PmdMock.create_minimal_pmd()
        parser = PmdParser()
        result = parser.parse(binary_data)
        
        self.assertEqual(result.header.model_name, "TestModel")
        self.assertEqual(len(result.vertices), 8)
        self.assertEqual(len(result.faces), 12)
        
    def test_parse_invalid_pmd(self):
        """不正なPMDファイルのエラーハンドリングテスト"""
        binary_data = PmdMock.create_invalid_pmd()
        parser = PmdParser()
        
        with self.assertRaises(InvalidPmdFileError):
            parser.parse(binary_data)
```

### 統合テスト（TestFixtureProvider使用）

```python
class TestPmdImporter(MayaTestBase):
    def setUp(self):
        super().setUp()
        self.fixture_provider = TestFixtureProvider()
        
    def test_import_real_pmd(self):
        """実際のPMDファイルのインポートテスト"""
        # 利用可能なファイルがあるかチェック
        available_files = self.fixture_provider.get_available_pmd_files()
        if not available_files:
            self.skipTest("No PMD files available")
        
        # 最初に見つかったファイルを使用
        pmd_path = self.fixture_provider.get_pmd_file()
        importer = PmdImporter()
        
        result = importer.import_file(pmd_path)
        
        self.assertTrue(result.success)
        # ファイル名から期待するメッシュ名を生成
        expected_mesh_name = os.path.splitext(os.path.basename(pmd_path))[0] + "_mesh"
        self.assertIsNotNone(cmds.ls(expected_mesh_name))
        
    def test_import_specific_pmd(self):
        """特定のPMDファイルのインポートテスト"""
        available_files = self.fixture_provider.get_available_pmd_files()
        if len(available_files) < 2:
            self.skipTest("Need at least 2 PMD files for specific file test")
        
        # 2番目のファイルを使用（最初のファイルとは異なる）
        file_name = os.path.splitext(os.path.basename(available_files[1]))[0]
        pmd_path = self.fixture_provider.get_pmd_file(file_name)
        importer = PmdImporter()
        
        result = importer.import_file(pmd_path)
        self.assertTrue(result.success)
        
    def tearDown(self):
        self.fixture_provider.cleanup_temp_files()
        super().tearDown()
```

## 実装計画

### フェーズ1: モッククラスの基本実装
- PmdMock, PmxMock, VmdMockの基本構造
- 最小限のテストデータ生成機能
- 既存のユニットテストとの統合

### フェーズ2: TestFixtureProviderの実装
- 実ファイルの管理機能
- キャッシュ機能の実装
- 既存の統合テストとの統合

### フェーズ3: 高度なテスト機能
- 異常系テストデータの充実
- パフォーマンステスト用の大容量データ生成
- テストカバレッジの向上

## 期待される効果

### テストの高速化
- 実ファイルI/Oの削減によるユニットテストの高速化
- 必要最小限のデータのみを使用した効率的なテスト

### テストの信頼性向上
- 外部依存を排除した安定したテスト
- 異常系テストの充実によるエラーハンドリングの改善

### 開発効率の向上
- 新機能開発時の迅速なテスト実装
- デバッグ時のテストデータ操作の簡素化

## モックについて

- テストモックは、テストのために実際のオブジェクトやサービスの代わりに使用されるオブジェクトです。
- テストモックは、特定の動作を模倣するために設計されており、テストの実行を容易にします。
- テストモックは、依存関係のあるオブジェクトやサービスを模倣するために使用されます。
- 本プロジェクトでは、主にファイル入出力の依存関係を排除し、純粋なパーサーロジックのテストを可能にするために使用します。


### 標準的なMMD人体骨格階層

モックではこの構造を模すようにしてください。

```
センター
├── 上半身
│   ├── 首
│   │   └── 頭
│   │       ├── 目_L
│   │       │   └── 目先_L
│   │       ├── 目_R
│   │       │   └── 目先_R
│   │       ├── 両目
│   │       │   └── 両目先
│   │       └── 頭先
│   ├── 肩_L
│   │   └── 腕_L
│   │       └── ひじ_L
│   │           └── 手首_L
│   │               ├── 親指1_L
│   │               │   └── 親指2_L
│   │               │       └── 親指先_L
│   │               ├── 人指1_L
│   │               │   └── 人指2_L
│   │               │       └── 人指3_L
│   │               │           └── 人指先_L
│   │               ├── 中指1_L → 中指先_L
│   │               ├── 薬指1_L → 薬指先_L
│   │               ├── 小指1_L → 小指先_L
│   │               └── 手先_L
│   └── 肩_R
│       └── 腕_R
│           └── ひじ_R
│               └── 手首_R
│                   └── (右手指群)
├── 下半身
│   ├── 足_L
│   │   └── ひざ_L
│   │       └── 足首_L
│   │           └── つま先_L
│   ├── 足_R
│   │   └── ひざ_R
│   │       └── 足首_R
│   │           └── つま先_R
│   └── 下半身先
├── 足IK_L
│   └── つま先IK_L
│       └── つま先IK先_L
└── 足IK_R
    └── つま先IK_R
        └── つま先IK先_R
```