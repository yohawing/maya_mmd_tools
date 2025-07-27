# リリースチェックリスト

このドキュメントは、Maya MMD Toolsのリリース作業手順をまとめたものです。

## リリース前準備

### コード品質確認

- [ ] **すべてのテストが通過している**
  ```bash
  # ユニットテスト
  python tests/run_tests.py --type unit
  
  # 統合テスト  
  python tests/run_tests.py --type integration
  
  # GUIテスト
  python tests/run_gui_tests.py
  ```

- [ ] **リンターとフォーマッターでコードスタイルを確認**
  ```bash
  # ruffでコードスタイルチェック
  ruff check .
  
  # ruffでコードフォーマット
  ruff format .
  ```

- [ ] **未コミットの変更がない**
  ```bash
  git status
  ```

### ドキュメント更新

- [ ] **バージョン番号の更新**
  - `mmd_tools/__init__.py` の `__version__` を更新
  - 例: `1.0.0` → `1.1.0`

- [ ] **CHANGELOG.mdの作成・更新**
  - 新規機能、改善点、バグ修正を記載
  - 破壊的変更がある場合は明記

- [ ] **README.mdの確認**
  - 新機能の説明を追加
  - インストール手順に変更がないか確認

- [ ] **機能別ドキュメントの更新**
  - `docs/design_*.md` - 新機能の設計ドキュメント
  - `docs/testing.md` - テスト手順の更新
  - `docs/settings.md` - 新しい設定項目の追加

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

## リリース作業

### Gitブランチ操作

- [ ] **developブランチを最新にする**
  ```bash
  git checkout develop
  git pull origin develop
  ```

- [ ] **リリースブランチを作成**
  ```bash
  git checkout -b release/v1.1.0
  ```

- [ ] **バージョン関連ファイルをコミット**
  ```bash
  git add mmd_tools/__init__.py CHANGELOG.md
  git commit -m "chore: bump version to 1.1.0"
  ```

- [ ] **mainブランチにマージ**
  ```bash
  git checkout main
  git merge --no-ff release/v1.1.0
  ```

- [ ] **タグを作成**
  ```bash
  git tag -a v1.1.0 -m "Release version 1.1.0"
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
  git push origin v1.1.0
  ```

- [ ] **GitHubでリリースを作成**
  1. https://github.com/yohawing/maya_mmd_tools/releases にアクセス
  2. "Create a new release"をクリック
  3. タグを選択: `v1.1.0`
  4. リリースタイトル: `Maya MMD Tools v1.1.0`
  5. リリースノートにCHANGELOGの内容をコピー
  6. アセットファイルをアップロード（必要に応じて）

### 配布物の準備

- [ ] **ZIPファイルの作成**
  ```bash
  # リリース用ディレクトリを作成
  mkdir maya_mmd_tools_v1.1.0
  
  # 必要なファイルをコピー
  cp -r mmd_tools maya_mmd_tools_v1.1.0/
  cp maya_mmd_tools.mod maya_mmd_tools_v1.1.0/
  cp userSetup.py maya_mmd_tools_v1.1.0/
  cp README.md maya_mmd_tools_v1.1.0/
  cp LICENSE maya_mmd_tools_v1.1.0/
  
  # ZIPファイルを作成
  zip -r maya_mmd_tools_v1.1.0.zip maya_mmd_tools_v1.1.0
  ```

## リリース後作業

### アナウンス

- [ ] **プロジェクト管理ツールの更新**
  - Linearでリリースマイルストーンを完了
  - 次回リリースの計画を作成

- [ ] **ユーザーへの通知**（必要に応じて）
  - フォーラムやSNSでのアナウンス
  - 重要な変更がある場合は移行ガイドを提供

### 次回開発準備

- [ ] **開発用バージョン番号の更新**
  ```python
  # mmd_tools/__init__.py
  __version__ = "1.2.0-dev"
  ```

- [ ] **新しい開発サイクルの開始**
  - 新機能のIssue作成
  - 次回リリースのマイルストーン設定

## トラブルシューティング

### よくある問題

**テストが失敗する場合**
- Maya環境変数が正しく設定されているか確認
- `MAYA_LOCATION`と`PYTHONPATH`を確認

**リリースタグが作成できない場合**
- 既存のタグと重複していないか確認: `git tag -l`
- リモートのタグも確認: `git ls-remote --tags origin`

**配布物に問題がある場合**
- `__pycache__`ディレクトリが含まれていないか確認
- `.pyc`ファイルが含まれていないか確認
- 必要なすべてのファイルが含まれているか確認

## チェックリストテンプレート

リリース時は以下のテンプレートをコピーして使用してください：

```markdown
## Maya MMD Tools v1.1.0 リリースチェックリスト

### 準備完了確認
- [ ] すべてのユニットテストがパス
- [ ] すべての統合テストがパス
- [ ] コードスタイルチェック完了
- [ ] バージョン番号更新
- [ ] CHANGELOG.md更新
- [ ] ドキュメント更新

### リリース作業
- [ ] リリースブランチ作成
- [ ] mainブランチへマージ
- [ ] タグ作成 (v1.1.0)
- [ ] GitHubリリース作成
- [ ] 配布物ZIP作成

### 確認事項
- [ ] Maya 2024での動作確認
- [ ] 主要機能の手動テスト完了

担当者: @yohawing
日付: 2025-07-27
```