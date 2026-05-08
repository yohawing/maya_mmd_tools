"""
Maya MMD ToolsのNamespace管理ユーティリティモジュール。

複数モデルインポート時の名前衝突を避けるため、
Mayaのnamespace機能を利用した管理機能を提供します。
"""

import re
from contextlib import contextmanager
from typing import Optional, List

from maya import cmds

from .logger import get_logger
from .maya_utils import sanitize_text

logger = get_logger(__name__)


class NamespaceUtils:
    """Namespace管理のためのユーティリティクラス"""

    @staticmethod
    def generate_namespace(model_name: str) -> str:
        """
        モデル名からnamespaceを生成します。

        Args:
            model_name: モデル名（日本語可）

        Returns:
            生成されたnamespace名
        """
        # 日本語を英数字に変換
        sanitized = sanitize_text(model_name)

        # 空の場合のデフォルト
        if not sanitized:
            sanitized = "MMDModel"

        # Maya namespace制約に合わせて調整
        # 数字で始まる場合は接頭辞を追加
        if sanitized and sanitized[0].isdigit():
            sanitized = f"Model_{sanitized}"

        # 特殊文字を除去（アンダースコアは許可）
        sanitized = re.sub(r"[^a-zA-Z0-9_]", "_", sanitized)

        # 連続するアンダースコアを1つに
        sanitized = re.sub(r"_+", "_", sanitized)

        # 先頭と末尾のアンダースコアを除去
        sanitized = sanitized.strip("_")

        # 空になった場合のフォールバック
        if not sanitized:
            sanitized = "MMDModel"

        logger.debug(f"Generated namespace: {model_name} -> {sanitized}")
        return sanitized

    @staticmethod
    def ensure_unique_namespace(base_name: str) -> str:
        """
        重複しないnamespaceを確保します。

        Args:
            base_name: ベースとなるnamespace名

        Returns:
            重複しないnamespace名
        """
        if not cmds.namespace(exists=base_name):
            return base_name

        # 連番を付与
        counter = 2
        while True:
            candidate = f"{base_name}_{counter}"
            if not cmds.namespace(exists=candidate):
                logger.debug(f"Unique namespace found: {candidate}")
                return candidate
            counter += 1

    @staticmethod
    def create_namespace(namespace_name: str) -> bool:
        """
        namespaceを作成します。

        Args:
            namespace_name: 作成するnamespace名

        Returns:
            作成に成功したかどうか
        """
        try:
            if cmds.namespace(exists=namespace_name):
                logger.warning(f"Namespace already exists: {namespace_name}")
                return True

            cmds.namespace(add=namespace_name)
            logger.info(f"Created namespace: {namespace_name}")
            return True

        except Exception as e:
            logger.error(f"Failed to create namespace {namespace_name}: {e}")
            return False

    @staticmethod
    @contextmanager
    def namespace_context(namespace_name: Optional[str]):
        """
        namespace内で作業するためのcontext manager。

        Args:
            namespace_name: 作業するnamespace名。Noneの場合は何もしない。

        Yields:
            namespace名（作成された場合）

        Example:
            with namespace_context("myModel") as ns:
                # この中で作成されるオブジェクトは myModel:* になる
                cmds.polyCube(name="cube1")
        """
        if not namespace_name:
            # namespace_nameがNoneの場合は何もしない
            yield None
            return

        # 現在のnamespaceを保存
        current_ns = cmds.namespaceInfo(currentNamespace=True)

        try:
            # namespaceが存在しない場合は作成
            if not cmds.namespace(exists=namespace_name):
                cmds.namespace(add=namespace_name)
                logger.debug(f"Created namespace in context: {namespace_name}")

            # namespaceに切り替え
            cmds.namespace(set=f":{namespace_name}")
            logger.debug(f"Switched to namespace: {namespace_name}")
            yield namespace_name

        finally:
            # 元のnamespaceに戻す
            cmds.namespace(set=current_ns)
            logger.debug(f"Restored namespace: {current_ns}")

    @staticmethod
    def cleanup_namespace(namespace_name: str, force: bool = True):
        """
        エラー時のnamespaceクリーンアップ。

        Args:
            namespace_name: 削除するnamespace名
            force: 中身があっても強制削除するか
        """
        try:
            if not cmds.namespace(exists=namespace_name):
                logger.debug(f"Namespace does not exist: {namespace_name}")
                return

            # namespace内のオブジェクトを確認
            objects = cmds.ls(f"{namespace_name}:*", long=True)

            if objects:
                if force:
                    # 強制削除モード：namespace内のオブジェクトを削除
                    logger.warning(f"Force deleting {len(objects)} objects in namespace: {namespace_name}")
                    cmds.delete(objects)
                else:
                    # マージモード：親namespaceにマージ
                    logger.info(f"Merging namespace: {namespace_name}")

            # namespaceを削除
            cmds.namespace(removeNamespace=namespace_name, mergeNamespaceWithParent=not force)
            logger.info(f"Cleaned up namespace: {namespace_name}")

        except Exception as e:
            logger.error(f"Failed to cleanup namespace {namespace_name}: {e}")

    @staticmethod
    def list_model_namespaces() -> List[str]:
        """
        MMDモデル用のnamespaceをリスト化。

        Returns:
            モデル用namespace名のリスト
        """
        all_namespaces = cmds.namespaceInfo(listOnlyNamespaces=True, recurse=True)

        # システムnamespaceを除外
        system_namespaces = {"UI", "shared"}
        model_namespaces = [ns for ns in all_namespaces if ns not in system_namespaces and not ns.startswith(":")]

        return model_namespaces

    @staticmethod
    def get_namespace_from_node(node_name: str) -> Optional[str]:
        """
        ノード名からnamespaceを取得。

        Args:
            node_name: ノード名

        Returns:
            namespace名（ルートnamespaceの場合はNone）
        """
        if ":" not in node_name:
            return None

        # 最後の:より前がnamespace
        namespace = node_name.rsplit(":", 1)[0]
        return namespace if namespace else None
