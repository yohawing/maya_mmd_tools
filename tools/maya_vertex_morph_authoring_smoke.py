"""Validate transactional Vertex Morph target edits in Maya standalone."""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

from maya import cmds, standalone

from mmd_tools.adapters.maya_cmds_adapter import MayaCmdsAdapter
from mmd_tools.adapters.maya_scene_metadata_backend import MayaSceneMetadataBackend
from mmd_tools.adapters.maya_morph_authoring import (
    MayaMorphAuthoringError,
    _apply_vertex_target_plan,
    _new_vertex_target_plans,
    _vertex_target_plan,
)
from mmd_tools.core.constants import (
    ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON,
    ATTR_MMD_SOURCE_VERTEX_INDICES,
)
from mmd_tools.core import maya_attribute_utils
from mmd_tools.core.model_authoring_spec import MmdMorphSpec
from mmd_tools.ui.presenters.animation_presenter import AnimationPresenter
from tests.common.maya_plugin_setup import load_mmd_tools_plugin


def _target(root: str, name: str, source_indices: tuple[int, ...], target_index: int):
    mesh = cmds.polyPlane(name=name, sx=1, sy=1)[0]
    mesh = cmds.parent(mesh, root)[0]
    maya_attribute_utils.add_typed_attribute(mesh, ATTR_MMD_SOURCE_VERTEX_INDICES, "longArray")
    maya_attribute_utils.set_attribute(
        mesh,
        ATTR_MMD_SOURCE_VERTEX_INDICES,
        list(source_indices),
        "longArray",
    )
    target = cmds.duplicate(mesh, name=f"{name}_target")[0]
    cmds.xform(f"{target}.vtx[0]", relative=True, translation=(0.1, 0.0, 0.0))
    blend_shape = cmds.blendShape(target, mesh, name=f"{name}_blendShape")[0]
    cmds.delete(target)
    if target_index != 0:
        group0 = f"{blend_shape}.inputTarget[0].inputTargetGroup[0]"
        groupn = f"{blend_shape}.inputTarget[0].inputTargetGroup[{target_index}]"
        cmds.setAttr(
            f"{groupn}.inputTargetItem[6000].inputComponentsTarget",
            1,
            "vtx[0]",
            type="componentList",
        )
        cmds.setAttr(
            f"{groupn}.inputTargetItem[6000].inputPointsTarget",
            1,
            (0.1, 0.0, 0.0, 1.0),
            type="pointArray",
        )
        cmds.removeMultiInstance(group0, b=True)
    plug = f"{blend_shape}.weight[{target_index}]"
    cmds.aliasAttr("oldAlias", plug)
    cmds.addAttr(blend_shape, longName=ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON, dataType="string")
    cmds.setAttr(
        f"{blend_shape}.{ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON}",
        json.dumps({str(target_index): {"name": "Move", "index": 0}}),
        type="string",
    )
    return blend_shape, plug


def main() -> int:
    standalone.initialize(name="python")
    try:
        load_mmd_tools_plugin(
            Path(__file__).resolve().parents[1],
            required_node_types=("mmdMorphController",),
            cmds_module=cmds,
        )
        root = cmds.createNode("transform", name="Model")
        face_bs, face_plug = _target(root, "face", (4, 7, 8, 10), 3)
        body_bs, body_plug = _target(root, "body", (9, 12, 13, 15), 8)
        controller = cmds.createNode("mmdMorphController", name="vertexSmokeController")
        cmds.addAttr(root, longName="mmd_morph_controller", attributeType="message")
        cmds.addAttr(root, longName="mmd_import_scale", attributeType="double")
        cmds.setAttr(f"{root}.mmd_import_scale", 2.0)
        cmds.connectAttr(f"{controller}.message", f"{root}.mmd_morph_controller")
        cmds.connectAttr(f"{controller}.outputWeight[0]", face_plug)
        cmds.connectAttr(f"{controller}.outputWeight[0]", body_plug)
        adapter = MayaCmdsAdapter(cmds)
        old = MmdMorphSpec(
            name="Move",
            index=0,
            morph_type="vertex",
            offsets=({"vertex_index": 4, "position_offset": (0.1, 0.0, 0.0)},),
            binding_identity="vertexNode",
        )
        new = replace(
            old,
            name="Move Wide",
            panel=2,
            offsets=(
                {"vertex_index": 4, "position_offset": (1.0, 2.0, 3.0)},
                {"vertex_index": 9, "position_offset": (-1.0, 0.5, -2.0)},
            ),
        )
        controller_plan = {
            "controller": cmds.ls(controller, long=True)[0],
            "outputs": {"vertexNode": (f"{face_bs}.oldAlias", f"{body_bs}.w[8]")},
        }
        plans = _vertex_target_plan(
            adapter,
            cmds.ls(root, long=True)[0],
            {"vertexNode": old},
            {"vertexNode": new},
            [],
            controller_plan,
            lambda _root: 2.0,
        )
        _apply_vertex_target_plan(adapter, "unusedController", plans)

        assert cmds.aliasAttr(face_plug, query=True) == "Move_Wide"
        assert cmds.aliasAttr(body_plug, query=True) == "Move_Wide"
        for blend_shape, target_index, expected in (
            (face_bs, 3, (2.0, 4.0, -6.0, 1.0)),
            (body_bs, 8, (-2.0, 1.0, 4.0, 1.0)),
        ):
            item = f"{blend_shape}.inputTarget[0].inputTargetGroup[{target_index}].inputTargetItem[6000]"
            assert cmds.getAttr(f"{item}.inputComponentsTarget") == ["vtx[0]"]
            point = cmds.getAttr(f"{item}.inputPointsTarget")[0]
            assert all(abs(float(actual) - wanted) < 1e-6 for actual, wanted in zip(point, expected))
            mapping = json.loads(
                cmds.getAttr(f"{blend_shape}.{ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON}")
            )
            assert mapping[str(target_index)] == {"name": "Move Wide", "index": 0}

        face_item = f"{face_bs}.inputTarget[0].inputTargetGroup[3].inputTargetItem[6000]"
        cmds.setAttr(
            f"{face_item}.inputComponentsTarget",
            1,
            "vtx[0:1]",
            type="componentList",
        )
        cmds.setAttr(
            f"{face_item}.inputPointsTarget",
            2,
            (2.0, 4.0, -6.0, 1.0),
            (0.0, 2.0, 0.0, 1.0),
            type="pointArray",
        )
        offsets = MayaSceneMetadataBackend(adapter)._morph_repository._read_vertex_blendshape_offsets(
            cmds.ls(root, long=True)[0],
            "vertexNode",
            "Move Wide",
            0,
        )
        assert offsets == [
            {"vertex_index": 4, "position_offset": [1.0, 2.0, 3.0]},
            {"vertex_index": 7, "position_offset": [0.0, 1.0, 0.0]},
            {"vertex_index": 9, "position_offset": [-1.0, 0.5, -2.0]},
        ]

        empty = MmdMorphSpec(name="Empty", index=1, morph_type="vertex")
        cmds.createNode("network", name="emptyNode")
        create_plans = _new_vertex_target_plans(
            adapter,
            cmds.ls(root, long=True)[0],
            [empty],
            lambda _root: 2.0,
        )
        _apply_vertex_target_plan(adapter, controller, tuple(create_plans))
        created_plugs = []
        for target in create_plans[0]["targets"]:
            blend_shape = target["blend_shape"]
            target_index = target["target_index"]
            plug = f"{blend_shape}.weight[{target_index}]"
            created_plugs.append(plug)
            assert cmds.aliasAttr(plug, query=True) == "Empty"
            mapping = json.loads(
                cmds.getAttr(f"{blend_shape}.{ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON}")
            )
            assert mapping[str(target_index)] == {"name": "Empty", "index": 1}

        presenter = object.__new__(AnimationPresenter)
        presenter.maya_adapter = adapter
        presenter._morph_indices = {}
        presenter._morph_targets = {}
        presenter._network_morph_targets = {}
        presenter._morph_controller = controller
        presenter._collect_morph_infos(cmds.ls(root, long=True)[0], {})
        assert presenter._morph_indices["Empty"] == 1
        presenter._set_morph_weight("Empty", 0.5)
        for plug in created_plugs:
            assert abs(float(cmds.getAttr(plug)) - 0.5) < 1e-6, plug
        cmds.setAttr(f"{controller}.inputWeight[1]", 0.0)

        sculpt_target = create_plans[0]["targets"][0]
        sculpt_blend_shape = sculpt_target["blend_shape"]
        sculpt_target_index = sculpt_target["target_index"]
        sculpt_shape = sculpt_target["shape"]
        sculpt_mesh = cmds.listRelatives(sculpt_shape, parent=True, fullPath=True)[0]
        sculpt_plug = f"{sculpt_blend_shape}.weight[{sculpt_target_index}]"
        cmds.disconnectAttr(f"{controller}.outputWeight[1]", sculpt_plug)
        cmds.setAttr(sculpt_plug, 1.0)
        cmds.sculptTarget(sculpt_blend_shape, edit=True, target=sculpt_target_index)
        cmds.move(0.1, 0.0, 0.0, f"{sculpt_mesh}.vtx[0]", relative=True)
        cmds.sculptTarget(sculpt_blend_shape, edit=True, target=-1)
        sculpt_item = (
            f"{sculpt_blend_shape}.inputTarget[0].inputTargetGroup[{sculpt_target_index}]"
            ".inputTargetItem[6000]"
        )
        sculpt_components = cmds.getAttr(f"{sculpt_item}.inputComponentsTarget") or []
        sculpt_points = cmds.getAttr(f"{sculpt_item}.inputPointsTarget") or []
        assert sculpt_components, {
            "blend_shape": sculpt_blend_shape,
            "shape": sculpt_shape,
            "target_index": sculpt_target_index,
            "weight": cmds.getAttr(sculpt_plug),
        }
        assert sculpt_points
        cmds.connectAttr(f"{controller}.outputWeight[1]", sculpt_plug)
        cmds.setAttr(f"{controller}.inputWeight[1]", 1.0)
        for plug in created_plugs:
            assert abs(float(cmds.getAttr(plug)) - 1.0) < 1e-6, plug

        delete_plans = _vertex_target_plan(
            adapter,
            cmds.ls(root, long=True)[0],
            {"emptyNode": replace(empty, binding_identity="emptyNode")},
            {},
            [],
            {
                "controller": cmds.ls(controller, long=True)[0],
                "outputs": {
                    "emptyNode": tuple(
                        f"{plug.split('.', 1)[0]}.Empty" for plug in created_plugs
                    )
                },
            },
            None,
        )
        for plug in created_plugs:
            cmds.disconnectAttr(f"{controller}.outputWeight[1]", plug)
        _apply_vertex_target_plan(adapter, controller, delete_plans)
        remaining_aliases = {plug: cmds.aliasAttr(plug, query=True) for plug in created_plugs}
        assert all(not alias for alias in remaining_aliases.values()), remaining_aliases

        reindexed = replace(new, index=2)
        face_mapping_path = f"{face_bs}.{ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON}"
        face_mapping = cmds.getAttr(face_mapping_path)
        cmds.setAttr(
            face_mapping_path,
            json.dumps({"3": {"name": "Stale", "index": 0}}),
            type="string",
        )
        try:
            _vertex_target_plan(
                adapter,
                cmds.ls(root, long=True)[0],
                {"vertexNode": new},
                {"vertexNode": reindexed},
                [],
                {
                    "controller": cmds.ls(controller, long=True)[0],
                    "outputs": {"vertexNode": (f"{face_bs}.weight[3]",)},
                },
                None,
            )
        except MayaMorphAuthoringError as exc:
            assert "stale_raw_name_mapping" in str(exc)
        else:
            raise AssertionError("stale raw-name mapping was accepted")
        finally:
            cmds.setAttr(face_mapping_path, face_mapping, type="string")

        try:
            _vertex_target_plan(
                adapter,
                cmds.ls(root, long=True)[0],
                {"vertexNode": new},
                {"vertexNode": reindexed},
                [],
                {
                    "controller": cmds.ls(controller, long=True)[0],
                    "outputs": {
                        "vertexNode": (
                            f"{face_bs}.Move_Wide",
                            f"{face_bs}.weight[3]",
                        )
                    },
                },
                None,
            )
        except MayaMorphAuthoringError as exc:
            assert "duplicate_blendshape_candidate" in str(exc)
        else:
            raise AssertionError("duplicate blendShape candidate was accepted")

        reindex_plans = _vertex_target_plan(
            adapter,
            cmds.ls(root, long=True)[0],
            {"vertexNode": new},
            {"vertexNode": reindexed},
            [],
            {
                "controller": cmds.ls(controller, long=True)[0],
                "outputs": {
                    "vertexNode": (
                        f"{face_bs}.weight[3]",
                        f"{body_bs}.Move_Wide",
                    )
                },
            },
            None,
        )
        _apply_vertex_target_plan(adapter, controller, reindex_plans)
        for blend_shape, target_index in ((face_bs, 3), (body_bs, 8)):
            mapping = json.loads(
                cmds.getAttr(f"{blend_shape}.{ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON}")
            )
            assert mapping[str(target_index)] == {"name": "Move Wide", "index": 2}

        print(
            json.dumps(
                {"success": True, "targets": 2, "topology": True, "sculpt_edit": True},
                separators=(",", ":"),
            )
        )
        return 0
    finally:
        standalone.uninitialize()


if __name__ == "__main__":
    raise SystemExit(main())
