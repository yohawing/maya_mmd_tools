"""
アプリケーション全体の状態を管理するクラス
全てのタブ間で共有される情報を一元管理します
"""

from ..core.logger import get_logger
from ..services.scene_model_service import SceneModelService
from .qt_compat import QObject, Signal

logger = get_logger(__name__)


class ApplicationState(QObject):
    """アプリケーション全体の状態を管理"""

    # シグナル定義
    current_model_changed = Signal(str)  # 現在のモデルが変更された
    model_list_updated = Signal(list)  # モデルリストが更新された
    status_message = Signal(str)  # ステータスメッセージ
    progress_updated = Signal(int)  # 進捗状況 (0-100)

    def __init__(self, scene_model_service=None):
        super().__init__()
        self._scene_model_service = scene_model_service if scene_model_service is not None else SceneModelService()
        self._current_model_root = None
        self._available_models = []
        self._model_info_cache = {}  # モデル情報のキャッシュ

    @property
    def scene_model_service(self):
        """Mayaシーン内のMMDモデル状態を取得するサービス。"""
        return self._scene_model_service

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
            invalid_model_root = False

            # 存在チェック
            if value and not self._scene_model_service.object_exists(value):
                logger.warning(f"Model root '{value}' does not exist")
                self._current_model_root = None
                value = None
                invalid_model_root = True

            if old_value != self._current_model_root or invalid_model_root:
                logger.debug(f"Current model changed: {old_value} -> {self._current_model_root}")
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
            self._available_models = self._scene_model_service.list_mmd_models()

            if old_models != self._available_models:
                logger.debug(f"Model list updated: {len(self._available_models)} models found")
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
        model_root = self._scene_model_service.resolve_model_from_selection(self._available_models)
        if not model_root:
            return False

        self.current_model_root = model_root
        return True

    def get_model_info(self, model_root=None):
        """モデル情報を取得（キャッシュ使用）"""
        if model_root is None:
            model_root = self._current_model_root

        if not model_root or not self._scene_model_service.object_exists(model_root):
            return None

        # キャッシュチェック
        if model_root in self._model_info_cache:
            return self._model_info_cache[model_root]

        # 情報を収集してキャッシュ
        self._cache_model_info(model_root)
        return self._model_info_cache.get(model_root)

    def _cache_model_info(self, model_root):
        """モデル情報をキャッシュ"""
        self._model_info_cache[model_root] = self._scene_model_service.get_model_info(model_root)

    def clear_cache(self):
        """キャッシュをクリア"""
        self._model_info_cache.clear()

    def emit_status(self, message):
        """ステータスメッセージを送信"""
        self.status_message.emit(message)

    def emit_progress(self, value):
        """進捗状況を送信"""
        self.progress_updated.emit(max(0, min(100, value)))
