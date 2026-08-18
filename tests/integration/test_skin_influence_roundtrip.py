"""PMX skin influence import/readback and export-policy coverage.

The fixture is intentionally generated as a small real PMX binary.  Import
readback runs through the production importer in both unified and
material-split modes for BDEF1/BDEF2/BDEF4/SDEF/QDEF, including numeric SDEF
weights.  Public export preserves canonical imported SDEF payloads; a derived
fixture that changes only SDEF tags to BDEF2 keeps separate BDEF/QDEF
round-trip coverage.
"""

from __future__ import annotations

from copy import deepcopy

from maya import cmds

from mmd_tools.actions.export_model_action import ExportModelAction, ExportModelRequest
from mmd_tools.core.constants import (
    ATTR_MMD_BONE_INDEX,
    ATTR_MMD_BONE_PARENT_INDEX,
    ATTR_MMD_SOURCE_VERTEX_INDICES,
)
from mmd_tools.core.mmd_parser import parse_pmx_file
from mmd_tools.core.pmx_data import PmxData
from mmd_tools.core.pmx_data.bone import PmxBone, PmxBoneFlag
from mmd_tools.core.pmx_data.face import PmxFace
from mmd_tools.core.pmx_data.header import PmxEncoding
from mmd_tools.core.pmx_data.material import PmxMaterial
from mmd_tools.core.pmx_data.vertex import PmxVertex
from mmd_tools.core.settings import settings
from mmd_tools.io.pmx_importer import import_pmx_file
from tests.common.maya_test_base import MayaTestBase


_WEIGHT_MODES = (0, 1, 2, 3, 4)
_EXPECTED_EXPORT_MODES = {0: 0, 1: 1, 2: 2, 4: 2}


def _make_fixture() -> PmxData:
    """Build a tiny PMX 2.1 binary source with two materials and five modes."""
    pmx = PmxData()
    pmx.header.magic = b"PMX "
    pmx.header.version = 2.1
    pmx.header.header_size = 8
    pmx.header.encoding = PmxEncoding.UTF16LE
    pmx.header.vertex_index_size = 1
    pmx.header.texture_index_size = 1
    pmx.header.material_index_size = 1
    pmx.header.bone_index_size = 1
    pmx.header.morph_index_size = 1
    pmx.header.rigid_body_index_size = 1
    pmx.header.model_name = "SkinInfluenceRoundtrip"
    pmx.header.model_name_english = "SkinInfluenceRoundtrip"
    pmx.header.comment = "all PMX skin modes"
    pmx.header.comment_english = "all PMX skin modes"

    # Five weighted bones plus an unused child that represents an IK target.
    bone_names = ["Root", "Bone1", "Bone2", "Bone3", "Bone4", "IKTarget"]
    for index, name in enumerate(bone_names):
        bone = PmxBone(bone_index_size=1, encoding=PmxEncoding.UTF16LE)
        bone.name = name
        bone.name_english = name
        bone.position = (0.0, float(index), 0.0)
        bone.parent_bone_index = index - 1 if index else -1
        bone.bone_flag = int(PmxBoneFlag.ROTATABLE)
        bone.connect_position_offset = (0.0, 1.0, 0.0)
        # Keep the final child visibly IK-like while avoiding a solver setup in
        # this import gate (setup_rig=False below).
        if index == 5:
            bone.bone_flag |= int(PmxBoneFlag.IK)
            bone.ik_target_bone_index = 4
            bone.ik_loop_count = 1
            bone.ik_limit_angle = 0.5
            bone.ik_links = []
        pmx.bones.append(bone)

    # Each mode gets a distinct set of positions, which lets the export check
    # map vertices back even when material-split import reorders them.
    source_weights = [
        (0, [0], []),
        (1, [0, 1], [0.25]),
        (2, [0, 1, 2, 3], [0.1, 0.2, 0.3, 0.4]),
        (3, [1, 2], [0.4]),
        (4, [1, 2, 3, 4], [0.1, 0.2, 0.3, 0.4]),
    ]
    for mode_index, (weight_type, bone_indices, bone_weights) in enumerate(source_weights):
        for corner in range(3):
            vertex = PmxVertex(bone_index_size=1, additional_uv_count=0)
            vertex.position = (
                float(mode_index * 3 + corner),
                float(mode_index),
                float(corner) * 0.25,
            )
            vertex.normal = (0.0, 1.0, 0.0)
            vertex.uv = (float(corner) * 0.5, float(mode_index) * 0.2)
            vertex.weight_transform_type = weight_type
            vertex.bone_indices = list(bone_indices)
            vertex.bone_weights = list(bone_weights)
            if weight_type == 3:
                vertex.sdef_c = (0.1, 0.2, 0.3)
                vertex.sdef_r0 = (0.0, 0.1, 0.0)
                vertex.sdef_r1 = (0.0, 0.0, 0.1)
            vertex.edge_magnification = 1.0
            pmx.vertices.append(vertex)

    # Five triangles: first three use MaterialA, last two use MaterialB.
    for mode_index in range(5):
        face = PmxFace(vertex_index_size=1)
        base = mode_index * 3
        face.indices = (base, base + 1, base + 2)
        pmx.faces.append(face)

    for index, (name, face_count) in enumerate((("MaterialA", 9), ("MaterialB", 6))):
        material = PmxMaterial(texture_index_size=1, encoding=PmxEncoding.UTF16LE, material_index=index)
        material.name = name
        material.name_english = name
        material.face_count = face_count
        material.texture_index = -1
        material.sphere_texture_index = -1
        material.toon_texture_index = -1
        pmx.materials.append(material)
    return pmx


def _make_exportable_fixture(source_data: PmxData) -> PmxData:
    """Copy a source fixture with SDEF tags converted to equivalent BDEF2 tags."""
    export_data = deepcopy(source_data)
    for vertex in export_data.vertices:
        if vertex.weight_transform_type == 3:
            vertex.weight_transform_type = 1
    return export_data


def _weight_map(vertex) -> dict[int, float]:
    """Decode a PMX vertex to positive bone-index weights."""
    if vertex.weight_transform_type == 0:
        pairs = [(vertex.bone_indices[0], 1.0)]
    elif vertex.weight_transform_type in (1, 3):
        weight = float(vertex.bone_weights[0])
        pairs = [(vertex.bone_indices[0], weight), (vertex.bone_indices[1], 1.0 - weight)]
    else:
        pairs = list(zip(vertex.bone_indices[:4], vertex.bone_weights[:4]))
    return {int(index): float(weight) for index, weight in pairs if float(weight) > 1e-7}


def _mesh_shapes(root: str) -> list[str]:
    """Return imported mesh shapes below a model root."""
    shapes = cmds.listRelatives(root, allDescendents=True, type="mesh", fullPath=True) or []
    return [
        shape
        for shape in shapes
        if not cmds.getAttr(f"{shape}.intermediateObject")
    ]


def _mesh_source_indices(shape: str) -> list[int]:
    """Read split-mesh source indices, falling back to unified order."""
    transform = (cmds.listRelatives(shape, parent=True, fullPath=True) or [shape])[0]
    if cmds.attributeQuery(ATTR_MMD_SOURCE_VERTEX_INDICES, node=transform, exists=True):
        return [int(value) for value in cmds.getAttr(f"{transform}.{ATTR_MMD_SOURCE_VERTEX_INDICES}") or []]
    return list(range(int(cmds.polyEvaluate(shape, vertex=True))))


def _skin_cluster(shape: str) -> str:
    """Return the imported mesh skinCluster."""
    history = cmds.listHistory(shape, pruneDagObjects=True) or []
    matches = [node for node in history if cmds.nodeType(node) == "skinCluster"]
    if len(matches) != 1:
        raise AssertionError(f"expected one skinCluster for {shape}, got {matches}")
    return matches[0]


def _joint_index(joint: str) -> int:
    return int(cmds.getAttr(f"{joint}.{ATTR_MMD_BONE_INDEX}"))


def _read_skin_weights(skin_cluster: str, shape: str, vertex_index: int) -> dict[int, float]:
    """Read one vertex's skinCluster values keyed by PMX bone index."""
    influences = cmds.skinCluster(skin_cluster, query=True, influence=True) or []
    values = cmds.skinPercent(
        skin_cluster,
        f"{shape}.vtx[{vertex_index}]",
        query=True,
        value=True,
    ) or []
    return {
        _joint_index(joint): float(value)
        for joint, value in zip(influences, values)
        if float(value) > 1e-7
    }


def _assert_weight_maps(test_case, actual: dict[int, float], expected: dict[int, float], label: str) -> None:
    test_case.assertEqual(set(actual), set(expected), label)
    for bone_index, expected_weight in expected.items():
        test_case.assertAlmostEqual(actual[bone_index], expected_weight, places=5, msg=label)


class TestSkinInfluenceRoundtrip(MayaTestBase):
    """Production PMX import/export round-trip for all five skin modes."""

    def setUp(self):
        super().setUp()
        settings.set("import.model.create_mmd_shaders", False)

    def test_all_pmx_weight_modes_import_readback_and_export_policy(self):
        """Read back all modes and preserve SDEF plus BDEF/QDEF export coverage."""
        source_path = self.get_temp_filename("skin_influence_modes.pmx")
        source_data = _make_fixture()
        source_data.write_file(source_path)
        expected_vertices = parse_pmx_file(source_path, use_native_pmx_parse=False)
        exportable_source_path = self.get_temp_filename("skin_influence_modes_exportable.pmx")
        _make_exportable_fixture(source_data).write_file(exportable_source_path)
        expected_export_vertices = parse_pmx_file(
            exportable_source_path,
            use_native_pmx_parse=False,
        )
        previous_split = settings.get("import.model.separate_meshes_by_material", False)

        try:
            for separate in (False, True):
                with self.subTest(separate_meshes_by_material=separate):
                    cmds.file(new=True, force=True)
                    settings.set("import.model.separate_meshes_by_material", separate)
                    import_data = parse_pmx_file(source_path)
                    root = import_pmx_file(
                        import_data,
                        source_path,
                        options={
                            "setup_rig": False,
                            "setup_bone_orientation": False,
                            "import_physics": False,
                            "import_morphs": False,
                        },
                    )
                    self.assertTrue(root and cmds.objExists(root))

                    joints = cmds.listRelatives(root, allDescendents=True, type="joint", fullPath=True) or []
                    joints_by_index = {_joint_index(joint): joint for joint in joints}
                    self.assertEqual(set(joints_by_index), set(range(6)))
                    self.assertEqual(
                        int(cmds.getAttr(f"{joints_by_index[5]}.{ATTR_MMD_BONE_PARENT_INDEX}")),
                        4,
                    )

                    observed_influences = set()
                    shapes = _mesh_shapes(root)
                    self.assertEqual(
                        len(shapes),
                        2 if separate else 1,
                        f"unexpected imported shapes: {shapes}",
                    )
                    for shape in shapes:
                        source_indices = _mesh_source_indices(shape)
                        skin_cluster = _skin_cluster(shape)
                        influence_joints = cmds.skinCluster(
                            skin_cluster, query=True, influence=True
                        ) or []
                        influence_indices = {_joint_index(joint) for joint in influence_joints}
                        self.assertNotIn(5, influence_indices)
                        self.assertTrue(influence_indices)
                        mesh_positive_influences = set()
                        for local_index, source_index in enumerate(source_indices):
                            expected = _weight_map(expected_vertices.vertices[source_index])
                            actual = _read_skin_weights(skin_cluster, shape, local_index)
                            _assert_weight_maps(
                                self,
                                actual,
                                expected,
                                f"{shape} local vertex {local_index} source {source_index}",
                            )
                            mesh_positive_influences.update(actual)
                        self.assertEqual(mesh_positive_influences, influence_indices)
                        observed_influences.update(influence_indices)
                    self.assertEqual(observed_influences, set(range(5)))

                    output_path = self.get_temp_filename(
                        f"skin_influence_modes_{'split' if separate else 'unified'}.pmx"
                    )
                    export_result = ExportModelAction().execute(
                        ExportModelRequest(
                            file_path=output_path,
                            options={"export_format": "pmx", "target_model": root},
                        )
                    )
                    self.assertTrue(export_result.succeeded, export_result.status_message)
                    exported_vertices = parse_pmx_file(
                        output_path,
                        use_native_pmx_parse=False,
                    ).vertices
                    exported_sdef = [
                        vertex
                        for vertex in exported_vertices
                        if vertex.weight_transform_type == 3
                    ]
                    expected_sdef = [
                        vertex
                        for vertex in expected_vertices.vertices
                        if vertex.weight_transform_type == 3
                    ]
                    self.assertEqual(len(exported_sdef), len(expected_sdef))
                    self.assertEqual(
                        [vertex.bone_indices for vertex in exported_sdef],
                        [vertex.bone_indices for vertex in expected_sdef],
                    )

                    cmds.file(new=True, force=True)
                    settings.set("import.model.separate_meshes_by_material", separate)
                    exportable_import_data = parse_pmx_file(exportable_source_path)
                    exportable_root = import_pmx_file(
                        exportable_import_data,
                        exportable_source_path,
                        options={
                            "setup_rig": False,
                            "setup_bone_orientation": False,
                            "import_physics": False,
                            "import_morphs": False,
                        },
                    )
                    self.assertTrue(exportable_root and cmds.objExists(exportable_root))
                    positive_output_path = self.get_temp_filename(
                        f"skin_influence_modes_positive_{'split' if separate else 'unified'}.pmx"
                    )
                    positive_export_result = ExportModelAction().execute(
                        ExportModelRequest(
                            file_path=positive_output_path,
                            options={
                                "export_format": "pmx",
                                "target_model": exportable_root,
                            },
                        )
                    )
                    self.assertTrue(positive_export_result.succeeded, positive_export_result.status_message)
                    exported = parse_pmx_file(
                        positive_output_path,
                        use_native_pmx_parse=False,
                        require_native_pmx_parse=False,
                    )
                    self.assertEqual(
                        [bone.name for bone in exported.bones],
                        [bone.name for bone in expected_vertices.bones],
                    )
                    self.assertEqual(
                        [bone.parent_bone_index for bone in exported.bones],
                        [bone.parent_bone_index for bone in expected_vertices.bones],
                    )
                    self.assertEqual(len(exported.materials), 2)
                    self.assertEqual(len(exported.vertices), len(expected_export_vertices.vertices))

                    unmatched_source_indices = set(range(len(expected_vertices.vertices)))
                    for exported_index, exported_vertex in enumerate(exported.vertices):
                        matching = [
                            source_index
                            for source_index in unmatched_source_indices
                            if max(
                                abs(float(exported_vertex.position[axis]) - float(expected_vertices.vertices[source_index].position[axis]))
                                for axis in range(3)
                            )
                            <= 1e-5
                        ]
                        self.assertEqual(len(matching), 1, f"exported vertex {exported_index} source mapping")
                        source_index = matching[0]
                        unmatched_source_indices.remove(source_index)
                        source_vertex = expected_vertices.vertices[source_index]
                        _assert_weight_maps(
                            self,
                            _weight_map(exported_vertex),
                            _weight_map(source_vertex),
                            f"exported vertex {exported_index} source {source_index}",
                        )
                        self.assertEqual(
                            exported_vertex.weight_transform_type,
                            _EXPECTED_EXPORT_MODES[
                                expected_export_vertices.vertices[source_index].weight_transform_type
                            ],
                        )
                    self.assertFalse(unmatched_source_indices)
        finally:
            settings.set("import.model.separate_meshes_by_material", previous_split)
