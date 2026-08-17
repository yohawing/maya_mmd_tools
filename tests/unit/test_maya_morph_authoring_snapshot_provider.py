from unittest.mock import Mock, patch

from mmd_tools.adapters.maya_morph_authoring_snapshot_provider import (
    MayaMorphAuthoringSnapshotProvider,
)
from mmd_tools.core.model_authoring_spec import MmdModelAuthoringSpec, MmdModelSpec
from mmd_tools.core.morph_read_projection import (
    MorphAuthoringReadSnapshot,
    MorphBlendShapeReadProjection,
)
from mmd_tools.core.morph_topology import MorphTopologyInspection


def test_provider_delegates_to_one_backend_with_the_injected_adapter():
    cmds_adapter = Mock()
    cmds_adapter.attribute_exists.return_value = True
    snapshot = MorphAuthoringReadSnapshot(
        spec=MmdModelAuthoringSpec(model=MmdModelSpec(name="model")),
        projection=MorphBlendShapeReadProjection("|root", "controller", (), (), ()),
        topology_inspection=MorphTopologyInspection({}, {}, ()),
    )
    backend = Mock()
    backend.read_morph_authoring_snapshot.return_value = snapshot

    with patch(
        "mmd_tools.adapters.maya_morph_authoring_snapshot_provider."
        "MayaSceneMetadataBackend",
        return_value=backend,
    ) as backend_type:
        provider = MayaMorphAuthoringSnapshotProvider(cmds_adapter)
        actual = provider.read_morph_authoring_snapshot("|root")

    backend_type.assert_called_once_with(cmds_adapter)
    backend.read_morph_authoring_snapshot.assert_called_once_with("|root")
    assert actual is snapshot


def test_unmarked_root_uses_runtime_only_projection_with_the_injected_adapter():
    cmds_adapter = Mock()
    cmds_adapter.attribute_exists.return_value = False
    projection = MorphBlendShapeReadProjection("|root", "", (), (), ())
    read_adapter = Mock()
    read_adapter.read_runtime_only_projection.return_value = projection
    backend = Mock()

    with patch(
        "mmd_tools.adapters.maya_morph_authoring_snapshot_provider."
        "MayaSceneMetadataBackend",
        return_value=backend,
    ):
        with patch(
            "mmd_tools.adapters.maya_morph_authoring_snapshot_provider."
            "MayaMorphReadProjectionAdapter",
            return_value=read_adapter,
        ) as read_adapter_type:
            snapshot = MayaMorphAuthoringSnapshotProvider(
                cmds_adapter
            ).read_morph_authoring_snapshot("|root")

    read_adapter_type.assert_called_once_with(cmds_adapter)
    read_adapter.read_runtime_only_projection.assert_called_once_with("|root")
    backend.read_morph_authoring_snapshot.assert_not_called()
    assert snapshot.spec is None
    assert snapshot.projection is projection
    assert snapshot.topology_inspection == MorphTopologyInspection({}, {}, ())


def test_english_only_model_marker_keeps_strict_backend_failure_visible():
    cmds_adapter = Mock()
    cmds_adapter.attribute_exists.side_effect = lambda attr, _root: attr == "mmd_model_name_en"
    backend = Mock()
    backend.read_morph_authoring_snapshot.side_effect = RuntimeError("missing JP metadata")

    with patch(
        "mmd_tools.adapters.maya_morph_authoring_snapshot_provider."
        "MayaSceneMetadataBackend",
        return_value=backend,
    ):
        provider = MayaMorphAuthoringSnapshotProvider(cmds_adapter)
        try:
            provider.read_morph_authoring_snapshot("|root")
        except RuntimeError as exc:
            assert str(exc) == "missing JP metadata"
        else:
            raise AssertionError("English-only semantic root used runtime-only fallback")

    backend.read_morph_authoring_snapshot.assert_called_once_with("|root")


def test_provider_rejects_invalid_backend_result():
    with patch(
        "mmd_tools.adapters.maya_morph_authoring_snapshot_provider."
        "MayaSceneMetadataBackend"
    ) as backend_type:
        backend_type.return_value.read_morph_authoring_snapshot.return_value = object()
        cmds_adapter = Mock()
        cmds_adapter.attribute_exists.return_value = True
        provider = MayaMorphAuthoringSnapshotProvider(cmds_adapter)

    try:
        provider.read_morph_authoring_snapshot("|root")
    except TypeError as exc:
        assert str(exc) == "invalid morph authoring snapshot"
    else:
        raise AssertionError("invalid backend result was accepted")
