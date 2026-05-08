# Maya MMD Tools 開発者ドキュメント

このディレクトリには、Maya MMD Toolsの開発者向けドキュメントを置いています。

現在は `0.1.0` リリースに向けて構成を絞り込み、設計、ファイル仕様、テスト、リリース作業に必要な文書だけを残しています。

## ドキュメント構成

### 全体設計

- [architecture.md](architecture.md) - アプリケーション全体の設計、主要レイヤー、Maya連携方針
- [setting.md](setting.md) - 設定管理システム
- [ascii-translation.md](ascii-translation.md) - MMD多言語名をMaya互換名へ変換する仕組み

### ファイルフォーマット仕様

- [spec-pmd.md](spec-pmd.md) - PMDファイル仕様
- [spec-pmx.md](spec-pmx.md) - PMXファイル仕様
- [spec-vmd.md](spec-vmd.md) - VMDファイル仕様

### テスト

- [testing-overview.md](testing-overview.md) - テスト戦略、実行方法、Maya環境でのテスト構成
- [testing-mock.md](testing-mock.md) - PMD/PMX/VMDモックとテストデータ設計

### リリース

- [release-process.md](release-process.md) - `0.1.0` リリースチェックリストと手順
- [release-versioning.md](release-versioning.md) - `0.x` 系の単純なバージョニング方針

## 開発者向けクイックスタート

### コードベースを把握する

1. [architecture.md](architecture.md) で全体構成を確認します。
2. PMD/PMX/VMDの構造を触る場合は、該当する `spec-*.md` を参照します。
3. 設定や名前変換を触る場合は、[setting.md](setting.md) と [ascii-translation.md](ascii-translation.md) を確認します。

### テストを実行する

ユニットテスト:

```bash
python tests/run_tests.py --type unit
```

統合テスト:

```bash
python tests/run_tests.py --type integration
```

GUIテスト:

```bash
python tests/run_gui_tests.py
```

詳しくは [testing-overview.md](testing-overview.md) を参照してください。

## ドキュメント更新方針

- 実装範囲が変わった場合は、関連する `docs-dev/` 文書も更新します。
- ユーザー向けの手順は [../docs/README.md](../docs/README.md) に集約します。
- リリース方針やバージョン方針を変えた場合は、[release-process.md](release-process.md) と [release-versioning.md](release-versioning.md) を同時に確認します。
- ドキュメントの見出しには番号を付けません。セクションを入れ替えやすくするためです。

## 関連リンク

- [ユーザードキュメント](../docs/README.md)
- [プロジェクトREADME](../README.md)
- [開発エージェント向け指示](../AGENTS.md)
