# リリースチェックリスト

このドキュメントは、Maya MMD Toolsのリリース作業手順をまとめたものです。

## 現在のリリース方針

次の一旦のリリースは `0.1.0` として切ります。

`0.x` は早期リリースとして扱い、`alpha` / `beta` / `rc` のようなサフィックスは当面使いません。未安定であることを示したい場合は、GitHub Release側でPre-releaseに設定します。

### 対象機能

- PMD/PMXモデルインポート
- VMDアニメーションインポート
- 基本UI
- 多言語対応
- Namespace対応

### 対象外

- PMD/PMX/VMDエクスポート
- 物理演算の完全対応
- C++ノード実装

## リリース前準備

### コード品質確認

- [ ] **すべてのテストが通過している**
  ```bash
  python tests/run_tests.py --type unit
  python tests/run_tests.py --type integration
  python tests/run_gui_tests.py
  ```

- [ ] **リンターとフォーマッターでコードスタイルを確認**
  ```bash
  ruff check .
  ruff format .
  ```

- [ ] **未コミットの変更がない**
  ```bash
  git status
  ```

### ドキュメント更新

- [ ] **バージョン番号の確認**
  - `mmd_tools/__init__.py`: `__version__ = "0.1.0"`
  - `pyproject.toml`: `version = "0.1.0"`

- [ ] **CHANGELOG.mdの更新**
  - 新規機能、改善点、バグ修正を記載
  - 既知の制限としてエクスポート未実装、物理演算未完成を明記

- [ ] **README.mdの確認**
  - `0.x` 早期リリースの注意事項
  - 実装済み機能と未実装機能
  - インストール手順

- [ ] **機能別ドキュメントの更新**
  - `docs-dev/testing-overview.md` - テスト手順
  - `docs/` - ユーザー向け手順
  - `docs-dev/` - 開発者向け設計文書

- [ ] **docs-dev更新タスクの完了**
  - リリース方針、バージョニング、タスク管理が `0.1.0` 方針と一致している
  - 実装済み/未実装の機能境界が開発者向け文書に反映されている
  - 古いブランチ前提、C++ノード実装前提、未実装エクスポート前提がリリース対象として残っていない

### 動作確認

- [ ] **Maya 2024での動作確認**
  - Windows 11での動作確認
  - macOS 15.6での動作確認（可能であれば）

- [ ] **主要機能の手動テスト**
  - [ ] PMDファイルのインポート
  - [ ] PMXファイルのインポート
  - [ ] VMDファイルのインポート（ボーン・モーフ・カメラ・照明）
  - [ ] UIの各タブ機能（Info、Material、Morph、Bone）
  - [ ] 多言語対応（日本語/英語）
  - [ ] Namespace付き複数モデル読み込み

## リリース作業

### Gitブランチ操作

- [ ] **developブランチを最新にする**
  ```bash
  git checkout develop
  git pull origin develop
  ```

- [ ] **リリースブランチを作成**
  ```bash
  git checkout -b release/v0.1.0
  ```

- [ ] **バージョン関連ファイルをコミット**
  ```bash
  git add mmd_tools/__init__.py pyproject.toml CHANGELOG.md README.md docs-dev/release-process.md
  git commit -m "chore: prepare 0.1.0 release"
  ```

- [ ] **mainブランチにマージ**
  ```bash
  git checkout main
  git merge --no-ff release/v0.1.0
  ```

- [ ] **タグを作成**
  ```bash
  git tag -a v0.1.0 -m "Release version 0.1.0"
  ```

- [ ] **developブランチに変更を反映**
  ```bash
  git checkout develop
  git merge --no-ff main
  ```

### GitHubリリース

- [ ] **タグをプッシュ**
  ```bash
  git push origin main
  git push origin develop
  git push origin v0.1.0
  ```

- [ ] **GitHubでリリースを作成**
  1. https://github.com/yohawing/maya_mmd_tools/releases にアクセス
  2. "Create a new release"をクリック
  3. タグを選択: `v0.1.0`
  4. リリースタイトル: `Maya MMD Tools v0.1.0`
  5. 必要なら "Set as a pre-release" を有効化
  6. リリースノートにCHANGELOGの内容をコピー
  7. アセットファイルをアップロード（必要に応じて）

### 配布物の準備

- [ ] **ZIPファイルの作成**
  ```bash
  mkdir maya_mmd_tools_v0.1.0
  cp -r mmd_tools maya_mmd_tools_v0.1.0/
  cp maya_mmd_tools.mod maya_mmd_tools_v0.1.0/
  cp userSetup.py maya_mmd_tools_v0.1.0/
  cp README.md maya_mmd_tools_v0.1.0/
  cp LICENSE maya_mmd_tools_v0.1.0/
  zip -r maya_mmd_tools_v0.1.0.zip maya_mmd_tools_v0.1.0
  ```

## リリース後作業

### アナウンス

- [ ] **プロジェクト管理ツールの更新**
  - GitHub Issuesでリリースマイルストーンを完了
  - 次回リリースの計画を作成

- [ ] **ユーザーへの通知**（必要に応じて）
  - GitHub Discussions、SNS、フォーラムなどで告知
  - 既知の制限とフィードバック先を明記

### 次回開発準備

- [ ] **次回開発用バージョンの検討**
  ```python
  # mmd_tools/__init__.py
  __version__ = "0.1.1"
  ```

- [ ] **新しい開発サイクルの開始**
  - 新機能のIssue作成
  - 次回リリースのマイルストーン設定

## トラブルシューティング

### テストが失敗する場合

- Maya環境変数が正しく設定されているか確認
- `MAYA_LOCATION`と`PYTHONPATH`を確認
- `docs-dev/testing-overview.md`の実行手順を確認

### リリースタグが作成できない場合

- 既存のタグと重複していないか確認:
  ```bash
  git tag -l
  git ls-remote --tags origin
  ```

### 配布物に問題がある場合

- `__pycache__`、`.pyc`、ビルド成果物が含まれていないか確認
- `plug-ins/`配下のビルド成果物を含めない
- 必要なファイルが含まれているか確認

## チェックリストテンプレート

```markdown
## Maya MMD Tools v0.1.0 リリースチェックリスト

### 準備完了確認
- [ ] すべてのユニットテストがパス
- [ ] すべての統合テストがパス
- [ ] GUIテストがパス
- [ ] コードスタイルチェック完了
- [ ] バージョン番号確認
- [ ] CHANGELOG.md更新
- [ ] ドキュメント更新
- [ ] docs-dev更新タスク完了

### リリース作業
- [ ] リリースブランチ作成
- [ ] mainブランチへマージ
- [ ] タグ作成 (v0.1.0)
- [ ] GitHubリリース作成
- [ ] 配布物ZIP作成

### 確認事項
- [ ] Maya 2024での動作確認
- [ ] 主要機能の手動テスト完了

担当者: @yohawing
日付: 2026-05-08
```
