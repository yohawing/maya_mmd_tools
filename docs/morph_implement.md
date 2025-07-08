# MorphConverter 実装計画

## 概要

このドキュメントでは、MMDのモーフデータをMayaのblendShapeノードに変換する`MorphConverter`クラスの詳細な実装計画について説明します。

## アーキテクチャ設計

### 全体構成

```
MorphConverter
├── MorphConverterFactory
├── BaseMorphHandler (抽象基底クラス)
├── VertexMorphHandler
├── UVMorphHandler
├── MaterialMorphHandler
├── GroupMorphHandler
├── BoneMorphHandler
└── MorphValidator
```

### クラス責務

#### MorphConverter (メインクラス)
- PMD/PMXモーフデータの統一的な変換インターface
- 設定管理とログ出力
- 全体的なエラーハンドリング

#### MorphConverterFactory
- モーフタイプに応じた適切なハンドラーの選択
- ハンドラーのインスタンス管理

#### BaseMorphHandler (抽象基底クラス)
- 各モーフハンドラーの共通インターface
- 基本的なユーティリティメソッド

#### 各モーフハンドラー
- 特定のモーフタイプの変換ロジック
- Maya APIの専門的な使用

#### MorphValidator
- 変換前後のデータ検証
- 品質チェックとレポート

## 実装詳細

### Phase 1: 基盤実装

#### 1.1 基底クラスとインターface

```python
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional

class BaseMorphHandler(ABC):
    """モーフハンドラーの基底クラス"""
    
    def __init__(self, settings: dict):
        self.settings = settings
        self.logger = self._setup_logger()
    
    @abstractmethod
    def can_handle(self, morph_data: Any) -> bool:
        """このハンドラーが対象のモーフタイプを処理できるかチェック"""
        pass
    
    @abstractmethod
    def convert(self, morph_data: Any, mesh_node: str, **kwargs) -> Dict[str, Any]:
        """モーフデータをMayaのblendShapeに変換"""
        pass
    
    def validate_input(self, morph_data: Any, mesh_node: str) -> bool:
        """入力データの検証"""
        pass
    
    def _sanitize_name(self, name: str) -> str:
        """Maya互換の名前に変換"""
        pass
```

#### 1.2 ファクトリークラス

```python
class MorphConverterFactory:
    """モーフタイプに応じたハンドラーを提供するファクトリー"""
    
    def __init__(self):
        self._handlers = {}
        self._register_default_handlers()
    
    def _register_default_handlers(self):
        """デフォルトハンドラーの登録"""
        self.register_handler('vertex', VertexMorphHandler)
        self.register_handler('uv', UVMorphHandler)
        self.register_handler('material', MaterialMorphHandler)
        self.register_handler('group', GroupMorphHandler)
        self.register_handler('bone', BoneMorphHandler)
    
    def register_handler(self, morph_type: str, handler_class):
        """カスタムハンドラーの登録"""
        self._handlers[morph_type] = handler_class
    
    def get_handler(self, morph_data: Any, settings: dict) -> BaseMorphHandler:
        """適切なハンドラーを取得"""
        for handler_class in self._handlers.values():
            handler = handler_class(settings)
            if handler.can_handle(morph_data):
                return handler
        raise ValueError(f"No handler found for morph type: {type(morph_data)}")
```

### Phase 2: 具体的なモーフハンドラー

#### 2.1 頂点モーフハンドラー

```python
class VertexMorphHandler(BaseMorphHandler):
    """頂点モーフ（PMD/PMX）の変換を処理"""
    
    def can_handle(self, morph_data: Any) -> bool:
        if hasattr(morph_data, 'morph_type'):
            # PMX
            return morph_data.morph_type == PmxMorphType.VertexMorph
        else:
            # PMD (type 1-4 are all vertex morphs)
            return hasattr(morph_data, 'vertices') and morph_data.morph_type > 0
    
    def convert(self, morph_data: Any, mesh_node: str, **kwargs) -> Dict[str, Any]:
        """頂点モーフをblendShapeターゲットに変換"""
        try:
            # 1. モーフターゲットメッシュを作成
            target_mesh = self._create_morph_target(morph_data, mesh_node)
            
            # 2. blendShapeノードに追加
            blend_shape_result = self._add_to_blendshape(target_mesh, mesh_node, morph_data)
            
            # 3. クリーンアップ
            self._cleanup_temp_objects(target_mesh)
            
            return {
                'success': True,
                'blend_shape_node': blend_shape_result['blend_shape_node'],
                'target_index': blend_shape_result['target_index'],
                'morph_name': morph_data.name
            }
            
        except Exception as e:
            self.logger.error(f"Failed to convert vertex morph {morph_data.name}: {e}")
            return {'success': False, 'error': str(e)}
    
    def _create_morph_target(self, morph_data: Any, base_mesh: str) -> str:
        """モーフターゲットメッシュを作成"""
        # メッシュを複製
        target_mesh = cmds.duplicate(base_mesh, name=f"{base_mesh}_morph_temp")[0]
        
        # 頂点位置を変更（OpenMaya API 2.0使用）
        self._apply_vertex_offsets(target_mesh, morph_data)
        
        return target_mesh
    
    def _apply_vertex_offsets(self, mesh_node: str, morph_data: Any):
        """頂点オフセットを適用（OpenMaya API 2.0使用）"""
        # DAGパスを取得
        sel_list = om.MSelectionList()
        sel_list.add(mesh_node)
        dag_path = sel_list.getDagPath(0)
        
        # MFnMeshを取得
        mesh_fn = om.MFnMesh(dag_path)
        
        # 現在の頂点位置を取得
        points = mesh_fn.getPoints(om.MSpace.kObject)
        
        # モーフオフセットを適用
        if hasattr(morph_data, 'offsets'):  # PMX
            for offset in morph_data.offsets:
                if 'vertex_index' in offset and 'position_offset' in offset:
                    vertex_index = offset['vertex_index']
                    offset_pos = offset['position_offset']
                    points[vertex_index] += om.MVector(offset_pos[0], offset_pos[1], offset_pos[2])
        else:  # PMD
            for vertex_index, offset_pos in morph_data.vertices:
                points[vertex_index] += om.MVector(offset_pos[0], offset_pos[1], offset_pos[2])
        
        # 変更された頂点位置を設定
        mesh_fn.setPoints(points, om.MSpace.kObject)
```

#### 2.2 UVモーフハンドラー

```python
class UVMorphHandler(BaseMorphHandler):
    """UVモーフ（PMX専用）の変換を処理"""
    
    def can_handle(self, morph_data: Any) -> bool:
        if hasattr(morph_data, 'morph_type'):
            return PmxMorphType.UVMorph <= morph_data.morph_type <= PmxMorphType.AdditionalUVMorph4
        return False
    
    def convert(self, morph_data: Any, mesh_node: str, **kwargs) -> Dict[str, Any]:
        """UVモーフをMayaのUVアニメーションシステムに変換"""
        try:
            # UVセットを作成
            uv_set_name = self._create_uv_set(morph_data, mesh_node)
            
            # アニメーション用のセットアップ
            animation_setup = self._setup_uv_animation(mesh_node, uv_set_name, morph_data)
            
            return {
                'success': True,
                'uv_set_name': uv_set_name,
                'animation_nodes': animation_setup,
                'morph_name': morph_data.name
            }
            
        except Exception as e:
            self.logger.error(f"Failed to convert UV morph {morph_data.name}: {e}")
            return {'success': False, 'error': str(e)}
```

#### 2.3 材質モーフハンドラー

```python
class MaterialMorphHandler(BaseMorphHandler):
    """材質モーフ（PMX専用）の変換を処理"""
    
    def can_handle(self, morph_data: Any) -> bool:
        if hasattr(morph_data, 'morph_type'):
            return morph_data.morph_type == PmxMorphType.MaterialMorph
        return False
    
    def convert(self, morph_data: Any, mesh_node: str, **kwargs) -> Dict[str, Any]:
        """材質モーフをMayaのマテリアルアニメーションに変換"""
        try:
            # マテリアルごとにアニメーション可能なアトリビュートを作成
            animation_nodes = []
            
            for offset in morph_data.offsets:
                material_animation = self._setup_material_animation(offset, mesh_node)
                animation_nodes.append(material_animation)
            
            return {
                'success': True,
                'animation_nodes': animation_nodes,
                'morph_name': morph_data.name
            }
            
        except Exception as e:
            self.logger.error(f"Failed to convert material morph {morph_data.name}: {e}")
            return {'success': False, 'error': str(e)}
```

### Phase 3: メインコンバータークラス

```python
class MorphConverter:
    """MMDのモーフデータをMayaのブレンドシェイプに変換するメインクラス"""
    
    def __init__(self):
        self.settings = settings.get_section("import.morph")
        self.factory = MorphConverterFactory()
        self.validator = MorphValidator()
        self.blend_shape_nodes = {}  # メッシュノードごとのblendShapeノード管理
        self.logger = self._setup_logger()
    
    def convert_pmd_morphs(self, pmd_data, mesh_node: str) -> Dict[str, Any]:
        """PMDのモーフデータをMayaのブレンドシェイプに変換"""
        if not self.settings.get("import_morphs", True):
            self.logger.info("Morph import is disabled in settings")
            return {'success': True, 'morphs_converted': 0}
        
        try:
            results = []
            successful_conversions = 0
            
            # プログレス初期化
            total_morphs = len(pmd_data.morphs)
            self._init_progress("Converting PMD Morphs", total_morphs)
            
            for i, morph in enumerate(pmd_data.morphs):
                try:
                    # ベースモーフはスキップ
                    if morph.morph_type == 0:
                        continue
                    
                    # 入力検証
                    if not self.validator.validate_pmd_morph(morph, mesh_node):
                        self.logger.warning(f"Validation failed for morph: {morph.name}")
                        continue
                    
                    # 適切なハンドラーを取得
                    handler = self.factory.get_handler(morph, self.settings)
                    
                    # 変換実行
                    result = handler.convert(morph, mesh_node)
                    
                    if result['success']:
                        results.append(result)
                        successful_conversions += 1
                        
                        # blendShapeノードの管理
                        self._manage_blendshape_node(result, mesh_node)
                    
                    self._update_progress(i + 1)
                    
                except Exception as e:
                    self.logger.error(f"Failed to convert morph {morph.name}: {e}")
                    continue
            
            # モーフのグループ化
            if self.settings.get("group_morphs_by_panel", True):
                self._group_morphs_by_panel(results)
            
            # 最終検証
            self.validator.validate_conversion_results(results, mesh_node)
            
            return {
                'success': True,
                'morphs_converted': successful_conversions,
                'total_morphs': total_morphs,
                'blend_shape_nodes': self.blend_shape_nodes.get(mesh_node, []),
                'results': results
            }
            
        except Exception as e:
            self.logger.error(f"Failed to convert PMD morphs: {e}")
            return {'success': False, 'error': str(e)}
        finally:
            self._cleanup_progress()
    
    def convert_pmx_morphs(self, pmx_data, mesh_node: str) -> Dict[str, Any]:
        """PMXのモーフデータをMayaのブレンドシェイプに変換"""
        if not self.settings.get("import_morphs", True):
            self.logger.info("Morph import is disabled in settings")
            return {'success': True, 'morphs_converted': 0}
        
        try:
            results = []
            successful_conversions = 0
            
            # プログレス初期化
            total_morphs = len(pmx_data.morphs)
            self._init_progress("Converting PMX Morphs", total_morphs)
            
            # モーフタイプ別に分類して処理
            morph_groups = self._categorize_pmx_morphs(pmx_data.morphs)
            
            # 依存関係を考慮した処理順序
            processing_order = ['vertex', 'uv', 'material', 'bone', 'group']
            
            for morph_type in processing_order:
                if morph_type not in morph_groups:
                    continue
                
                for morph in morph_groups[morph_type]:
                    try:
                        # 入力検証
                        if not self.validator.validate_pmx_morph(morph, mesh_node):
                            self.logger.warning(f"Validation failed for morph: {morph.name}")
                            continue
                        
                        # 適切なハンドラーを取得
                        handler = self.factory.get_handler(morph, self.settings)
                        
                        # 変換実行
                        result = handler.convert(morph, mesh_node)
                        
                        if result['success']:
                            results.append(result)
                            successful_conversions += 1
                            
                            # blendShapeノードの管理
                            self._manage_blendshape_node(result, mesh_node)
                        
                    except Exception as e:
                        self.logger.error(f"Failed to convert morph {morph.name}: {e}")
                        continue
            
            # モーフのグループ化
            if self.settings.get("group_morphs_by_panel", True):
                self._group_morphs_by_panel(results)
            
            # 最終検証
            self.validator.validate_conversion_results(results, mesh_node)
            
            return {
                'success': True,
                'morphs_converted': successful_conversions,
                'total_morphs': total_morphs,
                'blend_shape_nodes': self.blend_shape_nodes.get(mesh_node, []),
                'results': results
            }
            
        except Exception as e:
            self.logger.error(f"Failed to convert PMX morphs: {e}")
            return {'success': False, 'error': str(e)}
        finally:
            self._cleanup_progress()
    
    def _manage_blendshape_node(self, result: Dict[str, Any], mesh_node: str):
        """blendShapeノードの管理"""
        if mesh_node not in self.blend_shape_nodes:
            self.blend_shape_nodes[mesh_node] = []
        
        if 'blend_shape_node' in result:
            blend_shape_node = result['blend_shape_node']
            if blend_shape_node not in self.blend_shape_nodes[mesh_node]:
                self.blend_shape_nodes[mesh_node].append(blend_shape_node)
    
    def _categorize_pmx_morphs(self, morphs: List) -> Dict[str, List]:
        """PMXモーフをタイプ別に分類"""
        categories = {
            'vertex': [],
            'uv': [],
            'material': [],
            'bone': [],
            'group': []
        }
        
        for morph in morphs:
            if morph.morph_type == PmxMorphType.VertexMorph:
                categories['vertex'].append(morph)
            elif PmxMorphType.UVMorph <= morph.morph_type <= PmxMorphType.AdditionalUVMorph4:
                categories['uv'].append(morph)
            elif morph.morph_type == PmxMorphType.MaterialMorph:
                categories['material'].append(morph)
            elif morph.morph_type == PmxMorphType.BoneMorph:
                categories['bone'].append(morph)
            elif morph.morph_type == PmxMorphType.GroupMorph:
                categories['group'].append(morph)
        
        return categories
    
    def _group_morphs_by_panel(self, results: List[Dict[str, Any]]):
        """パネル別にモーフをグループ化"""
        panels = {
            1: "Eyebrow",
            2: "Eye", 
            3: "Mouth",
            4: "Other"
        }
        
        for panel_id, panel_name in panels.items():
            panel_morphs = [r for r in results if self._get_morph_panel(r) == panel_id]
            if panel_morphs:
                self._create_morph_set(panel_name, panel_morphs)
    
    def _create_morph_set(self, set_name: str, morphs: List[Dict[str, Any]]):
        """モーフのセットを作成"""
        set_members = []
        for morph in morphs:
            if 'blend_shape_node' in morph:
                set_members.append(f"{morph['blend_shape_node']}.{morph['morph_name']}")
        
        if set_members:
            cmds.sets(set_members, name=f"mmd_morph_{set_name.lower()}_set")
```

### Phase 4: バリデーターとユーティリティ

```python
class MorphValidator:
    """モーフ変換の検証を行うクラス"""
    
    def validate_pmd_morph(self, morph_data: Any, mesh_node: str) -> bool:
        """PMDモーフデータの検証"""
        try:
            # 基本的な検証
            if not hasattr(morph_data, 'name') or not morph_data.name:
                return False
            
            if not hasattr(morph_data, 'vertices') or not morph_data.vertices:
                return False
            
            # メッシュの頂点数チェック
            mesh_vertex_count = cmds.polyEvaluate(mesh_node, vertex=True)
            
            for vertex_index, _ in morph_data.vertices:
                if vertex_index >= mesh_vertex_count:
                    self.logger.warning(f"Vertex index {vertex_index} exceeds mesh vertex count {mesh_vertex_count}")
                    return False
            
            return True
            
        except Exception as e:
            self.logger.error(f"Validation error for PMD morph: {e}")
            return False
    
    def validate_pmx_morph(self, morph_data: Any, mesh_node: str) -> bool:
        """PMXモーフデータの検証"""
        try:
            # 基本的な検証
            if not hasattr(morph_data, 'name') or not morph_data.name:
                return False
            
            if not hasattr(morph_data, 'morph_type'):
                return False
            
            # モーフタイプ別の検証
            if morph_data.morph_type == PmxMorphType.VertexMorph:
                return self._validate_vertex_morph_data(morph_data, mesh_node)
            elif PmxMorphType.UVMorph <= morph_data.morph_type <= PmxMorphType.AdditionalUVMorph4:
                return self._validate_uv_morph_data(morph_data, mesh_node)
            elif morph_data.morph_type == PmxMorphType.MaterialMorph:
                return self._validate_material_morph_data(morph_data, mesh_node)
            
            return True
            
        except Exception as e:
            self.logger.error(f"Validation error for PMX morph: {e}")
            return False
    
    def validate_conversion_results(self, results: List[Dict[str, Any]], mesh_node: str):
        """変換結果の検証とレポート"""
        successful = len([r for r in results if r.get('success', False)])
        total = len(results)
        
        self.logger.info(f"Morph conversion completed: {successful}/{total} successful")
        
        if successful < total:
            failed = total - successful
            self.logger.warning(f"{failed} morphs failed to convert")
```

## 設定システム

### 設定項目の拡張

```json
{
  "import": {
    "morph": {
      "import_morphs": true,
      "create_blendshape_node": true,
      "group_morphs_by_panel": true,
      "max_morph_targets_per_blendshape": 100,
      "enable_uv_morphs": true,
      "enable_material_morphs": true,
      "enable_bone_morphs": true,
      "enable_group_morphs": true,
      "morph_weight_range": [0.0, 1.0],
      "use_original_morph_names": false,
      "create_morph_sets": true,
      "parallel_processing": false,
      "memory_optimization": true
    }
  }
}
```

## パフォーマンス最適化

### メモリ管理
- 大きなメッシュの場合は段階的処理
- 不要な一時オブジェクトの早期削除
- メモリ使用量の監視

### 処理の最適化
- OpenMaya API 2.0の積極的活用
- バッチ処理による効率化
- 必要に応じた並列処理

## エラーハンドリング

### エラーレベル
1. **Critical**: 変換全体が停止
2. **Error**: 特定のモーフの変換失敗
3. **Warning**: 品質低下を伴う変換
4. **Info**: 正常な処理状況

### フォールバック戦略
- 一部のモーフが失敗しても継続
- 代替手法での変換試行
- ユーザーへの詳細な状況報告

## テスト戦略

### ユニットテスト
- 各ハンドラーの単体テスト
- バリデーターのテスト
- エラーハンドリングのテスト

### 統合テスト
- 実際のMMDファイルでの変換テスト
- Mayaシーンでの動作確認
- パフォーマンステスト

### テストデータ
- 各モーフタイプのサンプルデータ
- 大規模なモーフデータ
- 異常なデータパターン

## 実装スケジュール

### Week 1-2: 基盤実装
- 基底クラスとファクトリーの実装
- 基本的なバリデーターの実装
- 設定システムの拡張

### Week 3-4: 頂点モーフハンドラー
- PMD頂点モーフの完全対応
- PMX頂点モーフの完全対応
- パフォーマンス最適化

### Week 5-6: 高度なモーフハンドラー
- UVモーフハンドラーの実装
- 材質モーフハンドラーの実装
- グループモーフハンドラーの実装

### Week 7-8: 統合とテスト
- 全体的な統合テスト
- パフォーマンステスト
- バグ修正とドキュメント整備

## 拡張性への配慮

### プラグインアーキテクチャ
- 新しいモーフタイプの追加容易性
- カスタムハンドラーの登録機能
- ユーザー定義の変換ルール

### バージョン対応
- PMX仕様の変更への対応
- 後方互換性の維持
- 設定フォーマットのマイグレーション

この実装計画により、堅牢で拡張性の高い`MorphConverter`を構築できます。段階的な実装により、各フェーズでの動作確認と品質保証を行いながら、最終的に高機能なモーフ変換システムを完成させることができます。
