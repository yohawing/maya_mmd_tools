"""Validate transactional Vertex Morph target edits in Maya standalone."""

from __future__ import annotations

from dataclasses import replace
import json

from maya import cmds, standalone

from mmd_tools.adapters.maya_cmds_adapter import MayaCmdsAdapter
from mmd_tools.adapters.maya_morph_authoring import (
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
        root = cmds.createNode("transform", name="Model")
        face_bs, face_plug = _target(root, "face", (4, 7, 8, 10), 3)
        body_bs, body_plug = _target(root, "body", (9, 12, 13, 15), 8)
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
            "outputs": {"vertexNode": (f"{face_bs}.oldAlias", f"{body_bs}.oldAlias")}
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

        controller = cmds.createNode("network", name="vertexSmokeController")
        cmds.addAttr(controller, longName="outputWeight", attributeType="double", multi=True)
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

        delete_plans = _vertex_target_plan(
            adapter,
            cmds.ls(root, long=True)[0],
            {"emptyNode": replace(empty, binding_identity="emptyNode")},
            {},
            [],
            {"outputs": {"emptyNode": tuple(f"{plug.split('.', 1)[0]}.Empty" for plug in created_plugs)}},
            None,
        )
        for plug in created_plugs:
            cmds.disconnectAttr(f"{controller}.outputWeight[1]", plug)
        _apply_vertex_target_plan(adapter, controller, delete_plans)
        remaining_aliases = {plug: cmds.aliasAttr(plug, query=True) for plug in created_plugs}
        assert all(not alias for alias in remaining_aliases.values()), remaining_aliases

        reindexed = replace(new, index=2)
        reindex_plans = _vertex_target_plan(
            adapter,
            cmds.ls(root, long=True)[0],
            {"vertexNode": new},
            {"vertexNode": reindexed},
            [],
            {"outputs": {"vertexNode": (f"{face_bs}.Move_Wide", f"{body_bs}.Move_Wide")}},
            None,
        )
        _apply_vertex_target_plan(adapter, controller, reindex_plans)
        for blend_shape, target_index in ((face_bs, 3), (body_bs, 8)):
            mapping = json.loads(
                cmds.getAttr(f"{blend_shape}.{ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON}")
            )
            assert mapping[str(target_index)] == {"name": "Move Wide", "index": 2}

        print(json.dumps({"success": True, "targets": 2, "topology": True}, separators=(",", ":")))
        return 0
    finally:
        standalone.uninitialize()


if __name__ == "__main__":
    raise SystemExit(main())
