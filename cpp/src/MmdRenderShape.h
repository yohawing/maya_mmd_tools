/**
 * @file MmdRenderShape.h
 * @brief Opt-in custom DAG shape used by the native VP2 ownership witness.
 *
 * The ordinary importer continues to create MFnMesh nodes.  This shape is
 * created only by ``mmdFastLoad -vp2Ownership true`` and owns the
 * material-split vertex/index data until the matching MPxGeometryOverride
 * has handed it to Viewport 2.0.
 */

#pragma once

#include <maya/MBoundingBox.h>
#include <maya/MObject.h>
#include <maya/MPxCommand.h>
#include <maya/MPxSurfaceShape.h>
#include <maya/MSelectionMask.h>
#include <maya/MString.h>
#include <maya/MTypeId.h>

#include <array>
#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

#include "MmdRenderQueue.h"

class MmdRenderShape : public MPxSurfaceShape {
public:
    static const MTypeId id;
    static const MString drawDbClassification;
    static const MString drawRegistrantId;

    MmdRenderShape();
    ~MmdRenderShape() override;

    static void* creator();
    static MStatus initialize();
    static MmdRenderShape* fromMObject(const MObject& object,
                                       MStatus* status = nullptr);

    void postConstructor() override;
    bool isBounded() const override;
    MBoundingBox boundingBox() const override;
    MSelectionMask getShapeSelectionMask() const override;

    /**
     * Replace the transient geometry owned by this witness shape.
     *
     * Each queue entry identifies one material-split submesh.  The method
     * validates every index before mutating the shape so unsupported or
     * malformed split data fails closed instead of producing partial draw
     * ownership.
     */
    bool setMaterialSplitGeometry(
        const std::vector<std::vector<float>>& submeshPositions,
        const std::vector<std::vector<uint32_t>>& submeshIndices,
        const std::vector<mmd::MmdRenderQueueEntry>& renderQueue,
        double scale);

    struct GeometryData {
        std::vector<float> positions;
        std::array<std::vector<uint32_t>, 3> indices;
        std::vector<mmd::MmdRenderQueueEntry> renderQueue;
    };

    const GeometryData& geometry() const;
    bool hasPassGeometry(mmd::MmdDrawPass pass) const;

    // The override records this after it has created the native render items.
    // This is intentionally transient diagnostic state, not a parity claim.
    void clearRenderItemWitness();
    void recordRenderItemWitness(const std::vector<mmd::MmdDrawPass>& passes);
    void recordGeometryWitness(std::size_t vertexCount,
                               std::size_t indexCount,
                               const std::string& descriptorSummary);
    std::string renderItemWitness() const;

private:
    GeometryData geometry_;
    MBoundingBox boundingBox_;
    bool renderItemWitnessValid_ = false;
    std::vector<mmd::MmdDrawPass> renderItemWitnessPasses_;
    bool geometryWitnessValid_ = false;
    std::size_t geometryWitnessVertexCount_ = 0U;
    std::size_t geometryWitnessIndexCount_ = 0U;
    std::string geometryWitnessDescriptorSummary_;
};

/**
 * Diagnostic command for commandPort/GUI smoke.
 *
 * ``mmdRenderWitness -node <shape>`` returns ``pending`` until the custom
 * geometry override has created its render items, then returns the pass order.
 */
class MmdRenderWitnessCommand : public MPxCommand {
public:
    static void* creator();
    static MSyntax newSyntax();

    MStatus doIt(const MArgList& args) override;
    bool isUndoable() const override;
};
