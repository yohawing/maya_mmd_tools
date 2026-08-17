"""Native material queue reindex contracts.

The command is Maya-bound, so the focused unit coverage checks the source-level
transaction and undo contracts; the executable queue smoke covers ordering.
"""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CPP = ROOT / "cpp" / "src"


def test_native_reindex_command_is_undoable_and_handle_backed() -> None:
    header = (CPP / "MmdRenderShape.h").read_text(encoding="utf-8")
    source = (CPP / "MmdRenderShape.cpp").read_text(encoding="utf-8")

    assert "MObjectHandle nodeHandle_;" in header
    assert "MStatus redoIt() override;" in header
    assert "MStatus undoIt() override;" in header
    assert "MStatus MmdRenderQueueReindexCommand::redoIt()" in source
    assert "MStatus MmdRenderQueueReindexCommand::undoIt()" in source
    assert (
        "bool MmdRenderQueueReindexCommand::isUndoable() const\n{\n    return true;"
        in source
    )
    assert "MHWRender::MRenderer::setGeometryDrawDirty(node, true);" in source


def test_native_reindex_validates_before_moving_queue_geometry() -> None:
    source = (CPP / "MmdRenderShape.cpp").read_text(encoding="utf-8")
    function = source[source.index("bool MmdRenderShape::reindexMaterialQueue(") :]
    function = function[: function.index("const MmdRenderShape::GeometryData&")]

    validation = function[: function.index("// Build the complete reordered value")]
    assert "std::move(geometry_.queueGeometry" not in validation
    assert "geometry_.queueGeometry = std::move(reordered);" in function
    assert "geometry_.renderQueue = std::move(nextQueue);" in function
