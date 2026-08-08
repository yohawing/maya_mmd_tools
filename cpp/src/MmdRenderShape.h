/**
 * @file MmdRenderShape.h
 * @brief Opt-in custom DAG shape used by the native VP2 ownership witness.
 *
 * The ordinary importer continues to create MFnMesh nodes.  This shape is
 * created only by ``mmdFastLoad -vp2Ownership true`` and owns the
 * material-split vertex/index data until the matching MPxGeometryOverride
 * has handed it to Viewport 2.0.  The override creates one render item per
 * material/submesh queue entry so the native ordering contract is observable
 * independently of Maya's ordinary mesh material batching.
 */

#pragma once

#include <maya/MBoundingBox.h>
#include <maya/MObject.h>
#include <maya/MPxCommand.h>
#include <maya/MPxSurfaceShape.h>
#include <maya/MSelectionMask.h>
#include <maya/MString.h>
#include <maya/MTypeId.h>

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
     * Each input identifies one material-split submesh.  The method builds
     * the deterministic queue, validates every index before mutating the
     * shape, and fails closed on malformed split data.
     */
    bool setMaterialSplitGeometry(
        const std::vector<std::vector<float>>& submeshPositions,
        const std::vector<std::vector<float>>& submeshNormals,
        const std::vector<std::vector<float>>& submeshUvs,
        const std::vector<std::vector<uint32_t>>& submeshIndices,
        const std::vector<mmd::MmdRenderQueueInput>& queueInputs,
        double scale);

    /** Update one material's effective alpha and rebuild the ordered items. */
    bool updateMaterialAlpha(std::size_t materialIndex, float diffuseAlpha);

    struct QueueGeometry {
        mmd::MmdRenderQueueEntry entry;
        // Keep the material input beside the ordered item so the VP2 override
        // can bind per-item material values without looking up mutable shape
        // state by index.
        mmd::MmdRenderQueueInput material;
        // Preserve whether the source split actually supplied UVs.  The
        // transient flattened buffer always has a fallback UV stream, so a
        // diagnostic must not mistake that fallback for authored UV data.
        bool uvStreamAvailable = false;
        uint32_t vertexOffset = 0U;
        std::vector<uint32_t> indices;
    };

    /**
     * Per-render-item native material binding evidence.
     *
     * This is intentionally diagnostic-only state.  It records requested
     * material paths separately from handles/parameter calls that succeeded;
     * it does not participate in queue ordering or shader math.
     */
    struct MaterialBindingDiagnostic {
        std::size_t queueIndex = 0U;
        std::size_t materialIndex = 0U;
        std::size_t submeshIndex = 0U;
        std::string renderItemName;
        std::string pass;
        bool outline = false;
        std::string technique;
        bool uvStreamAvailable = false;
        float diffuseAlpha = 1.0F;
        bool textureAlphaBlend = false;
        bool effectiveTransparent = false;
        bool selfShadowMap = false;
        bool casterEligible = false;
        bool casterRenderFilterParticipation = false;
        std::string renderItemType;
        std::string casterExclusionReason;
        std::string mainTexturePath;
        std::string sphereTexturePath;
        std::string toonTexturePath;
        std::string toonTextureSource;
        bool mainTextureRequested = false;
        bool sphereTextureRequested = false;
        bool toonTextureRequested = false;
        bool mainTextureAcquired = false;
        bool sphereTextureAcquired = false;
        bool toonTextureAcquired = false;
        bool scalarParameterBindingSuccess = false;
        bool mainTextureBindingSuccess = false;
        bool sphereTextureBindingSuccess = false;
        bool toonTextureBindingSuccess = false;
        bool switchParameterBindingSuccess = false;
        bool shaderAvailable = false;
        bool parameterBindingSuccess = false;
        bool shaderAssignmentSuccess = false;
        bool bindingSuccess = false;
        int sphereMode = 0;
    };

    struct GeometryData {
        std::vector<float> positions;
        std::vector<float> normals;
        std::vector<float> uvs;
        std::vector<mmd::MmdRenderQueueInput> queueInputs;
        std::vector<mmd::MmdRenderQueueEntry> renderQueue;
        std::vector<QueueGeometry> queueGeometry;
    };

    const GeometryData& geometry() const;
    bool hasPassGeometry(mmd::MmdDrawPass pass) const;

    // The override records this after it has created the native render items.
    // This is intentionally transient diagnostic state, not a parity claim.
    void clearRenderItemWitness();
    void clearMaterialBindingDiagnostics();
    void recordRenderItemWitness(
        const std::vector<mmd::MmdRenderQueueEntry>& entries);
    void recordMaterialBindingDiagnostic(
        const MaterialBindingDiagnostic& diagnostic);
    void recordGeometryWitness(std::size_t vertexCount,
                               std::size_t indexCount,
                               const std::string& descriptorSummary);
    std::string renderItemWitness() const;
    std::string materialBindingDiagnosticsJson() const;

private:
    GeometryData geometry_;
    MBoundingBox boundingBox_;
    bool renderItemWitnessValid_ = false;
    std::vector<mmd::MmdRenderQueueEntry> renderItemWitnessEntries_;
    bool geometryWitnessValid_ = false;
    std::size_t geometryWitnessVertexCount_ = 0U;
    std::size_t geometryWitnessIndexCount_ = 0U;
    std::string geometryWitnessDescriptorSummary_;
    std::vector<MaterialBindingDiagnostic> materialBindingDiagnostics_;
};

/**
 * Diagnostic command for commandPort/GUI smoke.
 *
 * ``mmdRenderWitness -node <shape>`` returns ``pending`` until the custom
 * geometry override has created its render items, then returns the pass order.
 * Add ``-json true`` for deterministic structured per-item material-binding
 * diagnostics while preserving the human-readable result by default.
 */
class MmdRenderWitnessCommand : public MPxCommand {
public:
    static void* creator();
    static MSyntax newSyntax();

    MStatus doIt(const MArgList& args) override;
    bool isUndoable() const override;
};

/**
 * Diagnostic/native queue update command.
 *
 * ``mmdRenderQueueUpdate -node <shape> -materialIndex <index> -alpha <value>``
 * applies a material alpha change to the opt-in shape and marks its VP2
 * geometry dirty.  This is the smallest live witness for morph-equivalent
 * queue changes; it does not alter the ordinary MFnMesh importer.
 */
class MmdRenderQueueUpdateCommand : public MPxCommand {
public:
    static void* creator();
    static MSyntax newSyntax();

    MStatus doIt(const MArgList& args) override;
    bool isUndoable() const override;
};
