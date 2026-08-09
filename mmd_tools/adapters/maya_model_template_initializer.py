"""Create a small, strictly readable MMD model from a packaged template.

The initializer owns the structural Maya transaction for the product's model
setup workflow.  It deliberately creates only the template's current root
bone, default material, and a deterministic triangle mesh; no procedural
semi-standard skeleton is inferred.  Semantic values are persisted through
the same ``mmd_*`` fields consumed by ``MayaSceneMetadataBackend``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
import json
import re
from typing import Any

from mmd_tools.core.constants import (
    ATTR_MMD_COMMENT,
    ATTR_MMD_COMMENT_EN,
    ATTR_MMD_DISPLAY_FRAMES_JSON,
    ATTR_MMD_IMPORT_SCALE,
    ATTR_MMD_MODEL_NAME,
    ATTR_MMD_MODEL_NAME_EN,
    ATTR_MMD_MODEL_REGISTRY,
    ATTR_MMD_REGISTRY_MATERIAL_MEMBERS,
    ATTR_MMD_REGISTRY_MORPH_MEMBERS,
    ATTR_MMD_REGISTRY_ROOT,
    ATTR_MMD_REGISTRY_SCHEMA,
)
from mmd_tools.core.model_authoring_spec import MmdMaterialSpec, MmdModelAuthoringSpec
from mmd_tools.core.model_template import MmdModelTemplate, instantiate_model_template


class MayaModelTemplateInitializerError(RuntimeError):
    """Raised when template creation cannot complete without semantic loss."""


@dataclass(frozen=True)
class ModelTemplateCreateResult:
    """Canonical result returned after strict scene read-back."""

    template_id: str
    root: str
    registry: str
    spec: MmdModelAuthoringSpec
    display_frames: tuple[Mapping[str, Any], ...]

    @property
    def fingerprint(self) -> str:
        """Return the deterministic semantic fingerprint of the created model."""
        return self.spec.fingerprint()


class MayaModelTemplateInitializer:
    """Create model setup using an injected Maya command adapter."""

    def __init__(
        self,
        cmds_adapter: Any,
        *,
        metadata_backend_factory: Callable[[Any], Any] | None = None,
        material_authoring_factory: Callable[[Any], Any] | None = None,
        mesh_factory: Callable[[str, str, str, str, Any], Any] | None = None,
    ) -> None:
        self._cmds = cmds_adapter
        self._metadata_backend_factory = metadata_backend_factory
        self._material_authoring_factory = material_authoring_factory
        self._mesh_factory = mesh_factory

    def create(
        self,
        template_id: str,
        model_name: str,
        model_name_english: str = "",
    ) -> ModelTemplateCreateResult:
        """Create one model root and return the strict read-back specification."""
        template = instantiate_model_template(template_id, model_name, model_name_english)
        root: str | None = None
        undo_open = False
        try:
            self._open_undo()
            undo_open = True
            root = self._create_root(template)
            registry = self._create_registry(root)
            self._write_root_metadata(root, template)

            joint = self._create_root_joint(root)
            bound_bone = replace(template.spec.bones[0], binding_identity=joint)
            self._register_bone(root, bound_bone)

            bound_material, shader, shading_group = self._create_material(root, template)
            self._create_mesh(root, joint, shader, shading_group)
            display_frames = self._write_display_frames(root, template)

            target_spec = replace(
                template.spec,
                bones=(bound_bone,),
                materials=(bound_material,),
            )
            backend = self._metadata_backend_factory_instance()
            from mmd_tools.adapters.scene_metadata_adapter import SceneMetadataAdapter

            observed = SceneMetadataAdapter(backend).read_spec(root)
            if observed.fingerprint() != target_spec.fingerprint():
                raise MayaModelTemplateInitializerError(
                    "created metadata fingerprint mismatch: "
                    f"expected {target_spec.fingerprint()}, got {observed.fingerprint()}"
                )
            self._close_undo()
            undo_open = False
            return ModelTemplateCreateResult(
                template_id=template.template_id,
                root=root,
                registry=registry,
                spec=observed,
                display_frames=display_frames,
            )
        except Exception as exc:
            if undo_open:
                self._rollback_undo()
            if isinstance(exc, MayaModelTemplateInitializerError):
                raise
            raise MayaModelTemplateInitializerError(f"template creation failed: {exc}") from exc

    def _open_undo(self) -> None:
        self._require_method("undo_info")
        try:
            if not bool(self._cmds.undo_info(query=True, state=True)):
                raise MayaModelTemplateInitializerError("Maya undo must be enabled")
            self._cmds.undo_info(openChunk=True, chunkName="Create MMD Model")
        except MayaModelTemplateInitializerError:
            raise
        except Exception as exc:
            raise MayaModelTemplateInitializerError(f"could not open model transaction: {exc}") from exc

    def _close_undo(self) -> None:
        try:
            self._cmds.undo_info(closeChunk=True)
        except Exception as exc:
            raise MayaModelTemplateInitializerError(f"could not commit model transaction: {exc}") from exc

    def _rollback_undo(self) -> None:
        try:
            self._cmds.undo_info(closeChunk=True)
        finally:
            self._cmds.undo()

    def _create_root(self, template: MmdModelTemplate) -> str:
        slug = re.sub(r"[^A-Za-z0-9_]", "_", template.spec.model.name_english or "model").strip("_")
        slug = slug or "model"
        return self._create_node("transform", f"mmdModel_{slug}")

    def _create_root_joint(self, root: str) -> str:
        return self._create_node("joint", "root", parent=root)

    def _create_registry(self, root: str) -> str:
        registry = self._create_node("network", f"{root.rsplit('|', 1)[-1]}_registry")
        self._ensure_attr(root, ATTR_MMD_MODEL_REGISTRY, "message")
        self._ensure_attr(registry, ATTR_MMD_REGISTRY_ROOT, "message")
        self._ensure_attr(registry, ATTR_MMD_REGISTRY_SCHEMA, "string")
        self._ensure_attr(registry, ATTR_MMD_REGISTRY_MORPH_MEMBERS, "message_multi")
        self._ensure_attr(registry, ATTR_MMD_REGISTRY_MATERIAL_MEMBERS, "message_multi")
        self._set_attr(registry, ATTR_MMD_REGISTRY_SCHEMA, "1", "string")
        self._connect(f"{root}.message", f"{registry}.{ATTR_MMD_REGISTRY_ROOT}")
        self._connect(f"{registry}.message", f"{root}.{ATTR_MMD_MODEL_REGISTRY}")
        return registry

    def _write_root_metadata(self, root: str, template: MmdModelTemplate) -> None:
        model = template.spec.model
        for attr, value in (
            (ATTR_MMD_MODEL_NAME, model.name),
            (ATTR_MMD_MODEL_NAME_EN, model.name_english),
            (ATTR_MMD_COMMENT, model.comment),
            (ATTR_MMD_COMMENT_EN, model.comment_english),
        ):
            self._ensure_attr(root, attr, "string")
            self._set_attr(root, attr, value, "string")
        self._ensure_attr(root, ATTR_MMD_IMPORT_SCALE, "double")
        self._set_attr(root, ATTR_MMD_IMPORT_SCALE, 1.0, "double")

    def _register_bone(self, root: str, bone: Any) -> None:
        from mmd_tools.adapters.maya_bone_authoring import register_existing_joint

        register_existing_joint(root, bone, self._cmds)

    def _create_material(
        self, root: str, template: MmdModelTemplate
    ) -> tuple[Any, str, str]:
        material = template.spec.materials[0]
        if self._material_authoring_factory is None:
            from mmd_tools.adapters.maya_material_authoring import MayaMaterialAuthoring

            authoring = MayaMaterialAuthoring(self._cmds)
        else:
            authoring = self._material_authoring_factory(self._cmds)
        try:
            bound, shader, shading_group = authoring.create_material(root, material)
        except Exception as exc:
            raise MayaModelTemplateInitializerError(f"failed to create template material: {exc}") from exc
        if not isinstance(bound, MmdMaterialSpec) or bound.binding_identity != shader:
            raise MayaModelTemplateInitializerError("material authoring returned an invalid bound spec")
        if not isinstance(shader, str) or not shader or shader.startswith("|"):
            raise MayaModelTemplateInitializerError(f"material shader identity must be a DG name: {shader!r}")
        if not isinstance(shading_group, str) or not shading_group or shading_group.startswith("|"):
            raise MayaModelTemplateInitializerError(
                f"material shading-group identity must be a DG name: {shading_group!r}"
            )
        return bound, shader, shading_group

    def _create_mesh(self, root: str, joint: str, shader: str, shading_group: str) -> None:
        if self._mesh_factory is not None:
            self._mesh_factory(root, joint, shader, shading_group, self._cmds)
            return
        raw = getattr(self._cmds, "_cmds", None)
        poly_create = getattr(raw, "polyCreateFacet", None)
        parent = getattr(raw, "parent", None)
        skin_cluster = getattr(raw, "skinCluster", None)
        if not callable(poly_create) or not callable(parent) or not callable(skin_cluster):
            raise MayaModelTemplateInitializerError(
                "a mesh_factory or Maya cmds polyCreateFacet/parent/skinCluster is required"
            )
        mesh_result = poly_create(
            p=[(-0.5, 0.0, 0.0), (0.5, 0.0, 0.0), (0.0, 1.0, 0.0)],
            name="mmdTemplateMesh",
        )
        mesh = self._canonical_node(
            mesh_result[0]
            if isinstance(mesh_result, Sequence) and not isinstance(mesh_result, (str, bytes, bytearray))
            else mesh_result,
            dag=True,
        )
        parented = parent(mesh, root)
        if parented:
            mesh = self._canonical_node(
                parented[0]
                if isinstance(parented, Sequence) and not isinstance(parented, (str, bytes, bytearray))
                else parented,
                dag=True,
            )
        self._call("sets", mesh, e=True, forceElement=shading_group)
        skin = skin_cluster(joint, mesh, toSelectedBones=True, maximumInfluences=1, normalizeWeights=1)
        if isinstance(skin, (str, bytes, bytearray)) or not isinstance(skin, Sequence) or not skin:
            raise MayaModelTemplateInitializerError("could not create a root-joint skinCluster")
        if not isinstance(skin[0], str) or not skin[0]:
            raise MayaModelTemplateInitializerError("skinCluster returned an invalid node identity")
        skin_percent = getattr(raw, "skinPercent", None)
        if callable(skin_percent):
            skin_percent(skin[0], f"{mesh}.vtx[*]", transformValue=[(joint, 1.0)])

    def _write_display_frames(
        self, root: str, template: MmdModelTemplate
    ) -> tuple[Mapping[str, Any], ...]:
        frames: list[dict[str, Any]] = []
        for frame in template.display_frames:
            elements = []
            for element in frame["elements"]:
                element_type = {"bone": 0, "morph": 1}[element["type"]]
                elements.append({"type": element_type, "index": element["index"]})
            frames.append(
                {
                    "name": frame["name"],
                    "name_english": frame["name_english"],
                    "special_flag": 1 if frame["special"] else 0,
                    "elements": elements,
                }
            )
        payload = json.dumps(frames, ensure_ascii=False, separators=(",", ":"))
        self._set_attr(root, ATTR_MMD_DISPLAY_FRAMES_JSON, payload, "string")
        return tuple(frames)

    def _metadata_backend_factory_instance(self) -> Any:
        if self._metadata_backend_factory is not None:
            return self._metadata_backend_factory(self._cmds)
        from mmd_tools.adapters.maya_scene_metadata_backend import MayaSceneMetadataBackend

        return MayaSceneMetadataBackend(self._cmds)

    def _create_node(self, node_type: str, name: str, **kwargs: Any) -> str:
        return self._canonical_node(
            self._call("create_node", node_type, name=name, **kwargs),
            dag=node_type in {"transform", "joint", "mesh"},
        )

    def _canonical_node(self, node: Any, *, dag: bool = False) -> str:
        if not isinstance(node, str) or not node:
            raise MayaModelTemplateInitializerError(f"created node identity is invalid: {node!r}")
        if not dag and node.startswith("|"):
            raise MayaModelTemplateInitializerError(f"DG identity must not be a DAG path: {node!r}")
        if dag and node.startswith("|"):
            if not self._call("object_exists", node):
                raise MayaModelTemplateInitializerError(f"created node does not exist: {node!r}")
            return node
        ls = self._call("ls", node, long=True)
        if len(ls) != 1 or not isinstance(ls[0], str) or (dag and not ls[0].startswith("|")):
            raise MayaModelTemplateInitializerError(f"node identity is not canonical: {node!r}")
        if not self._call("object_exists", ls[0]):
            raise MayaModelTemplateInitializerError(f"created node does not exist: {ls[0]!r}")
        return ls[0]

    def _ensure_attr(self, node: str, attr: str, kind: str) -> None:
        if self._call("attribute_exists", attr, node):
            return
        if kind == "string":
            self._call("add_attr", node, longName=attr, dataType="string")
        elif kind == "message":
            self._call("add_attr", node, longName=attr, attributeType="message")
        elif kind == "message_multi":
            self._call("add_attr", node, longName=attr, attributeType="message", multi=True)
        elif kind == "vector":
            self._call("add_attr", node, longName=attr, attributeType="double3")
            for suffix in ("X", "Y", "Z"):
                self._call("add_attr", node, longName=f"{attr}{suffix}", attributeType="double", parent=attr)
        elif kind == "double":
            self._call("add_attr", node, longName=attr, attributeType="double")
        else:
            self._call("add_attr", node, longName=attr, attributeType="long")

    def _set_attr(self, node: str, attr: str, value: Any, kind: str) -> None:
        self._ensure_attr(node, attr, kind)
        path = f"{node}.{attr}"
        if kind == "string":
            self._call("set_attr", path, value, type="string")
        elif kind == "vector":
            self._call("set_attr", path, *value, type="double3")
        else:
            self._call("set_attr", path, value)

    def _connect(self, source: str, destination: str) -> None:
        self._call("connect_attr", source, destination, force=True)

    def _call(self, method: str, *args: Any, **kwargs: Any) -> Any:
        try:
            return getattr(self._cmds, method)(*args, **kwargs)
        except AttributeError as exc:
            raise MayaModelTemplateInitializerError(f"injected adapter is missing {method}()") from exc
        except Exception as exc:
            raise MayaModelTemplateInitializerError(f"adapter {method}() failed: {exc}") from exc

    def _require_method(self, method: str) -> None:
        if not callable(getattr(self._cmds, method, None)):
            raise MayaModelTemplateInitializerError(f"injected adapter is missing {method}()")


__all__ = [
    "MayaModelTemplateInitializer",
    "MayaModelTemplateInitializerError",
    "ModelTemplateCreateResult",
]
