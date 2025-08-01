# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Namespace機能の実装（複数モデル対応）
  - PMX/PMDインポーターでNamespace自動生成
  - 日本語モデル名の英数字変換
  - 同じモデルの連番管理
  - VMDインポーターのNamespace検出改善
- NamespaceUtilsクラスの追加
  - namespace生成・管理機能
  - context managerサポート
  - エラー時の自動クリーンアップ

### Changed
- インポート設定に`use_namespace`オプションを実装（UI既存、機能追加）

## [0.1.0-alpha.1] - 2025-01-27

### Added
- UIテキストの多言語対応機能（日本語/英語切り替え）
- モーフコンバーターの最適化設計
- MaterialタブUIの改善とテクスチャ・スフィアマップ表示機能
- ボーンタブUIの改善（MMD日本語名表示、接続先表示改善）
- VMDインポーターにモーフアニメーションサポート
- VMDインポーターにカメラ・照明アニメーションサポート
- Maya Python API 2.0を使用した高速化処理
- リリースチェックリストドキュメント（`docs/release_checklist.md`）

### Changed
- ボーンリストの表示順序をMMDボーンインデックスに基づくように変更
- カスタムアトリビュート名の標準化
- cmds.addAttr/setAttrをmaya_utils関数に置き換え（パフォーマンス向上）
- mesh_converterのマテリアルアトリビュート処理を改善

### Fixed
- 統合テストの安定性向上
- UIの各種バグ修正
- ユニットテストの安定性向上

### Removed
- 不要なモックを削除
- 基本情報パネルから親ボーン選択ボタンを削除
- 接続先選択ボタンを削除

### Known Issues
- 大規模なモデルでのパフォーマンス問題
- 一部のPMXファイルで読み込みエラーが発生する可能性
- 物理演算は未対応
- エクスポート機能は未実装

### Notes
これは初回アルファリリースです。本番環境での使用は推奨しません。
バグ報告やフィードバックは[GitHub Issues](https://github.com/yohawing/maya_mmd_tools/issues)までお願いします。