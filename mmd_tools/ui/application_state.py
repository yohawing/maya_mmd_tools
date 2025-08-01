"""
アプリケーション全体の状態を管理するクラス
全てのタブ間で共有される情報を一元管理します
"""

from maya import cmds
from ..core.logger import get_logger
from ..core.maya_utils import (
    find_all_mmd_models,
    get_mmd_model_display_name,
    get_parent_mmd_root,
)
from .qt_compat import QObject, Signal

logger = get_logger(__name__)


class ApplicationState(QObject):
    """アプリケーション全体の状態を管理"""
    
    # シグナル定義
    current_model_changed = Signal(str)  # 現在のモデルが変更された
    model_list_updated = Signal(list)    # モデルリストが更新された
    status_message = Signal(str)         # ステータスメッセージ
    progress_updated = Signal(int)       # 進捗状況 (0-100)
    
    def __init__(self):
        super().__init__()
        self._current_model_root = None
        self._available_models = []
        self._model_info_cache = {}  # モデル情報のキャッシュ
        
    @property
    def current_model_root(self):
        """現在選択中のモデルルートノード"""
        return self._current_model_root
    
    @current_model_root.setter
    def current_model_root(self, value):
        """現在のモデルを設定"""
        logger.debug(f"ApplicationState: Setting current model to {value}")
        if value != self._current_model_root:
            old_value = self._current_model_root
            self._current_model_root = value
            
            # 存在チェック
            if value and not cmds.objExists(value):
                logger.warning(f"Model root '{value}' does not exist")
                self._current_model_root = None
                value = None
            
            if old_value != self._current_model_root:
                logger.info(f"Current model changed: {old_value} -> {self._current_model_root}")
                self.current_model_changed.emit(self._current_model_root or "")
                
                # モデル情報をキャッシュ
                if self._current_model_root:
                    self._cache_model_info(self._current_model_root)
    
    @property
    def available_models(self):
        """利用可能なモデルのリスト"""
        return self._available_models
    
    def refresh_model_list(self):
        """シーン内のMMDモデルリストを更新"""
        try:
            old_models = self._available_models.copy()
            self._available_models = find_all_mmd_models()
            
            if old_models != self._available_models:
                logger.info(f"Model list updated: {len(self._available_models)} models found")
                self.model_list_updated.emit(self._available_models)
            
            # 現在のモデルがリストにない場合はクリア
            if self._current_model_root and self._current_model_root not in self._available_models:
                logger.warning(f"Current model '{self._current_model_root}' no longer exists")
                self.current_model_root = None
                
            # 現在のモデルがない場合
            elif not self._current_model_root and self._available_models:
                # Maya選択から推測
                if not self.select_model_from_maya_selection():
                    # 推測できない場合は最初のモデルを選択
                    self.current_model_root = self._available_models[0]
                
        except Exception as e:
            logger.error(f"Failed to refresh model list: {e}", exc_info=True)
            self._available_models = []
            self.model_list_updated.emit([])
    
    def select_model_from_maya_selection(self):
        """Mayaの選択からモデルを推測して選択"""
        selected = cmds.ls(selection=True)
        if not selected:
            return False
            
        for obj in selected:
            parent_root = get_parent_mmd_root(obj)
            if parent_root:
                # 完全パスと短い名前の両方をチェック
                short_name = parent_root.split('|')[-1]
                if parent_root in self._available_models or short_name in self._available_models:
                    # available_modelsにある形式で設定
                    if parent_root in self._available_models:
                        self.current_model_root = parent_root
                    else:
                        self.current_model_root = short_name
                    return True
        
        return False
    
    def get_model_info(self, model_root=None):
        """モデル情報を取得（キャッシュ使用）"""
        if model_root is None:
            model_root = self._current_model_root
            
        if not model_root or not cmds.objExists(model_root):
            return None
            
        # キャッシュチェック
        if model_root in self._model_info_cache:
            return self._model_info_cache[model_root]
        
        # 情報を収集してキャッシュ
        self._cache_model_info(model_root)
        return self._model_info_cache.get(model_root)
    
    def _cache_model_info(self, model_root):
        """モデル情報をキャッシュ"""
        try:
            # namespace情報を取得
            namespace = None
            if ':' in model_root:
                # 最後の':'より前がnamespace
                namespace = model_root.rsplit(':', 1)[0]
                # パイプが含まれている場合は最後の要素を取得
                if '|' in namespace:
                    namespace = namespace.split('|')[-1]
            
            info = {
                'root': model_root,
                'namespace': namespace,
                'display_name': get_mmd_model_display_name(model_root),
                'name_jp': self._get_attr_safe(model_root, 'mmd_model_name_jp', ''),
                'name_en': self._get_attr_safe(model_root, 'mmd_model_name_en', ''),
                'vertex_count': 0,
                'material_count': 0,
                'bone_count': 0,
                'morph_count': 0,
            }
            
            # 統計情報を収集
            if cmds.objExists(model_root):
                # メッシュ情報
                shapes = cmds.listRelatives(model_root, allDescendents=True, type="mesh") or []
                for shape in shapes:
                    vertex_count = cmds.polyEvaluate(shape, vertex=True)
                    if vertex_count:
                        info['vertex_count'] += vertex_count
                
                # マテリアル数
                if shapes:
                    shading_groups = cmds.listConnections(shapes, type='shadingEngine') or []
                    shading_groups = list(set(shading_groups))
                    materials = []
                    for sg in shading_groups:
                        mats = cmds.ls(cmds.listConnections(sg), materials=True) or []
                        materials.extend(mats)
                    info['material_count'] = len(set(materials))
                
                # ボーン数
                joints = cmds.listRelatives(model_root, allDescendents=True, type="joint") or []
                info['bone_count'] = len(joints)
                
                # モーフ数（ブレンドシェイプ）
                if shapes:
                    blend_shapes = cmds.ls(cmds.listHistory(shapes), type='blendShape') or []
                    for bs in blend_shapes:
                        targets = cmds.blendShape(bs, query=True, target=True) or []
                        info['morph_count'] += len(targets)
            
            self._model_info_cache[model_root] = info
            
        except Exception as e:
            logger.error(f"Failed to cache model info for {model_root}: {e}", exc_info=True)
            self._model_info_cache[model_root] = None
    
    def _get_attr_safe(self, node, attr, default):
        """属性を安全に取得"""
        try:
            if cmds.attributeQuery(attr, node=node, exists=True):
                value = cmds.getAttr(f"{node}.{attr}")
                return value if value is not None else default
        except:
            pass
        return default
    
    def clear_cache(self):
        """キャッシュをクリア"""
        self._model_info_cache.clear()
    
    def emit_status(self, message):
        """ステータスメッセージを送信"""
        self.status_message.emit(message)
    
    def emit_progress(self, value):
        """進捗状況を送信"""
        self.progress_updated.emit(max(0, min(100, value)))