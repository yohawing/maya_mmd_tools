"""Mayaシーン内のMMDモデル状態を取得するサービス。

ApplicationStateやPresenterがMayaコマンドへ直接依存しすぎないように、
モデル列挙・選択解決・概要情報収集を集約します。
"""

from ..adapters import MayaCmdsAdapter
from ..core.constants import ATTR_MMD_MODEL_NAME, ATTR_MMD_MODEL_NAME_EN, SCENE_ROOT_SUFFIX
from ..core.logger import get_logger
from ..core.maya_identity import canonical_node_identity
from ..core.name_display import preferred_pmx_display_name

logger = get_logger(__name__)


class SceneModelService:
    """MayaシーンからMMDモデルに関する状態を読み取るサービス。"""

    def __init__(self, cmds_module=None, cmds_adapter=None):
        if cmds_adapter is not None:
            self._cmds_adapter = cmds_adapter
        else:
            self._cmds_adapter = MayaCmdsAdapter(cmds_module=cmds_module)

    def object_exists(self, node):
        """ノードが存在するかを返す。"""
        if not node:
            return False
        return bool(self._cmds_adapter.object_exists(node))

    def canonical_node(self, node):
        """Resolve a model-root alias to one unique long Maya identity."""
        return canonical_node_identity(self._cmds_adapter, node)

    def attribute_exists(self, node, attr):
        """ノードに属性が存在するかを返す。"""
        if not node or not attr:
            return False
        return bool(self._cmds_adapter.attribute_exists(attr, node=node))

    def list_mmd_models(self):
        """シーン内のMMDモデル root を名前順で返す。"""
        namespaced = self._cmds_adapter.ls(
            "*:*{}".format(SCENE_ROOT_SUFFIX), type="transform", long=True
        ) or []
        plain = self._cmds_adapter.ls(
            "*{}".format(SCENE_ROOT_SUFFIX), type="transform", long=True
        ) or []

        mmd_models = set()
        for transform in set(namespaced + plain):
            identity = self.canonical_node(transform)
            if not identity:
                continue
            if self._cmds_adapter.attribute_exists(
                ATTR_MMD_MODEL_NAME, node=identity
            ) or self._cmds_adapter.attribute_exists(
                ATTR_MMD_MODEL_NAME_EN, node=identity
            ):
                mmd_models.add(identity)

        return sorted(mmd_models)

    def get_parent_mmd_root(self, node):
        """指定ノードの親階層からMMDモデル root を探す。"""
        try:
            current = node
            while current:
                if current.endswith(SCENE_ROOT_SUFFIX) and (
                    self._cmds_adapter.attribute_exists(ATTR_MMD_MODEL_NAME, node=current)
                    or self._cmds_adapter.attribute_exists(ATTR_MMD_MODEL_NAME_EN, node=current)
                ):
                    return current

                parents = self._cmds_adapter.list_relatives(current, parent=True, fullPath=True) or []
                if not parents:
                    break
                current = parents[0]
        except Exception as e:
            logger.warning(f"Failed to find parent MMD root for {node}: {e}")

        return None

    def get_model_display_name(self, model_root, language="ja"):
        """MMDモデルの表示名を返す。"""
        name_jp = ""
        name_en = ""
        try:
            if self._cmds_adapter.attribute_exists(ATTR_MMD_MODEL_NAME, node=model_root):
                name_jp = self._cmds_adapter.get_attr(f"{model_root}.{ATTR_MMD_MODEL_NAME}")

            if self._cmds_adapter.attribute_exists(ATTR_MMD_MODEL_NAME_EN, node=model_root):
                name_en = self._cmds_adapter.get_attr(f"{model_root}.{ATTR_MMD_MODEL_NAME_EN}")
        except Exception:
            pass

        fallback = str(model_root).rsplit("|", 1)[-1].replace(SCENE_ROOT_SUFFIX, "")
        return preferred_pmx_display_name(
            name_jp,
            name_en,
            fallback=fallback,
            language=language,
        )

    def get_selected_nodes(self, node_type=None):
        """Mayaの現在選択を返す。"""
        kwargs = {"selection": True, "long": True}
        if node_type:
            kwargs["type"] = node_type
        return self._cmds_adapter.ls(**kwargs) or []

    def resolve_model_from_selection(self, available_models):
        """現在選択から、available_models に含まれるMMDモデル root を推測する。"""
        selected = self.get_selected_nodes()
        if not selected:
            return None

        available_identities = set()
        for model_root in available_models or []:
            identity = self.canonical_node(model_root)
            if identity:
                available_identities.add(identity)

        if not available_identities:
            return None

        selected_identities = set()
        for obj in selected:
            parent_root = self.get_parent_mmd_root(obj)
            if not parent_root:
                continue

            identity = self.canonical_node(parent_root)
            if identity and identity in available_identities:
                selected_identities.add(identity)

        # A multi-root selection is ambiguous; never guess a current model.
        if len(selected_identities) != 1:
            return None

        return selected_identities.pop()

    def get_model_info(self, model_root):
        """モデル概要情報を収集する。"""
        if not model_root or not self.object_exists(model_root):
            return None

        try:
            namespace = None
            if ":" in model_root:
                namespace = model_root.rsplit(":", 1)[0]
                if "|" in namespace:
                    namespace = namespace.split("|")[-1]

            info = {
                "root": model_root,
                "namespace": namespace,
                "display_name": self.get_model_display_name(model_root),
                "name_jp": self.get_attr_safe(model_root, ATTR_MMD_MODEL_NAME, ""),
                "name_en": self.get_attr_safe(model_root, ATTR_MMD_MODEL_NAME_EN, ""),
                "vertex_count": 0,
                "material_count": 0,
                "bone_count": 0,
                "morph_count": 0,
            }

            shapes = self._cmds_adapter.list_relatives(model_root, allDescendents=True, type="mesh") or []
            for shape in shapes:
                vertex_count = self._cmds_adapter.poly_evaluate(shape, vertex=True)
                if vertex_count:
                    info["vertex_count"] += vertex_count

            if shapes:
                shading_groups = self._cmds_adapter.list_connections(shapes, type="shadingEngine") or []
                materials = []
                for sg in set(shading_groups):
                    mats = self._cmds_adapter.ls(
                        self._cmds_adapter.list_connections(sg), materials=True
                    ) or []
                    materials.extend(mats)
                info["material_count"] = len(set(materials))

            joints = self._cmds_adapter.list_relatives(model_root, allDescendents=True, type="joint") or []
            info["bone_count"] = len(joints)

            if shapes:
                blend_shapes = self._cmds_adapter.ls(
                    self._cmds_adapter.list_history(shapes), type="blendShape"
                ) or []
                for blend_shape in blend_shapes:
                    targets = self._cmds_adapter.blend_shape(blend_shape, query=True, target=True) or []
                    info["morph_count"] += len(targets)

            return info
        except Exception as e:
            logger.error(f"Failed to get model info for {model_root}: {e}", exc_info=True)
            return None

    def get_attr_safe(self, node, attr, default=None):
        """属性値を安全に取得する。"""
        try:
            if self._cmds_adapter.attribute_exists(attr, node=node):
                value = self._cmds_adapter.get_attr(f"{node}.{attr}")
                return value if value is not None else default
        except Exception:
            pass
        return default

    def select_nodes(self, nodes, replace=True):
        """ノードを選択する。"""
        self._cmds_adapter.select(nodes, replace=replace)
