# Maya MMD Tools - 開発者ドキュメント

このディレクトリには、Maya MMD Toolsの開発者向けドキュメントが含まれています。

## 📚 ドキュメント構成

### 🏗️ アーキテクチャ
- [overview.md](overview.md) - 全体アーキテクチャ設計
- [plugin-structure.md](plugin-structure.md) - Mayaプラグイン統合

### 💻 実装詳細
#### コア機能
- [logger.md](logger.md) - ロギングシステム

#### コンバーター
- [morph-converter.md](morph-converter.md) - モーフコンバーター
- [vmd-converter.md](vmd-converter.md) - VMDコンバーター
- [physics-converter.md](physics-converter.md) - 物理演算コンバーター
- [material-converter.md](material-converter.md) - マテリアル・シェーダー

#### UI
- [main-window.md](main-window.md) - メインウィンドウUI

#### 最適化
- [morph-optimization.md](morph-optimization.md) - モーフ最適化

### 📋 仕様書
#### ファイルフォーマット
- [pmd-spec.md](pmd-spec.md) - PMDファイル仕様
- [pmx-spec.md](pmx-spec.md) - PMXファイル仕様
- [vmd-spec.md](vmd-spec.md) - VMDファイル仕様

#### 内部仕様
- [bone-mapping.md](bone-mapping.md) - ボーンマッピング・IK仕様
- [unicode-dict.md](unicode-dict.md) - Unicode辞書仕様

### 🔧 開発ガイド
- [testing.md](testing.md) - テストガイド
- [test-mock-design.md](test-mock-design.md) - テストモック設計
- [configuration.md](configuration.md) - 設定システム
- [coding-standards.md](coding-standards.md) - コーディング規約

### 📊 プロジェクト管理
- [task-tracking.md](task-tracking.md) - タスク管理
- [versioning.md](versioning.md) - バージョニング戦略
- [release-process.md](release-process.md) - リリースプロセス

## 🚀 クイックスタート（開発者向け）

1. **開発環境のセットアップ**
   - Maya 2024のインストール
   - Python 3.7以降の環境構築
   - 必要なツールのインストール

2. **コードベースの理解**
   - [アーキテクチャ概要](overview.md)を読む
   - [コーディング規約](coding-standards.md)を確認

3. **開発開始**
   - テストの実行方法は[testing.md](testing.md)を参照
   - コントリビューション方法は[CONTRIBUTING.md](../CONTRIBUTING.md)を参照

## 📝 ドキュメント更新方針

- コード変更と同時にドキュメントを更新
- PRレビュー時にドキュメントの更新を確認
- 設計変更は必ずドキュメント化

## 🔗 関連リンク

- [ユーザードキュメント](../docs/README.md)
- [プロジェクトREADME](../README.md)
- [開発ガイドライン](../CLAUDE.md)