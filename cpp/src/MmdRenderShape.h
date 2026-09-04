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
#include <maya/MDataBlock.h>
#include <maya/MObject.h>
#include <maya/MObjectHandle.h>
#include <maya/MPxCommand.h>
#include <maya/MPxSurfaceShape.h>
#include <maya/MPlugArray.h>
#include <maya/MSelectionMask.h>
#include <maya/MString.h>
#include <maya/MTypeId.h>

#include <array>
#include <cstddef>
#include <cstdint>
#include <string>
#include <utility>
#include <vector>

#include "MmdRenderQueue.h"

class MmdRenderShape : public MPxSurfaceShape {
public:
    static const MTypeId id;
    static const MString drawDbClassification;
    static const MString drawRegistrantId;
    // Maya's evaluated source mesh.  The importer may leave this input
    // unconnected while the static VP2 witness is used; a later authoring
    // path can connect a standard mesh without changing the render queue.
    static MObject aInputMesh;
    // Optional authored/evaluated alpha values.  Array logical indices are
    // PMX material indices; absent elements leave the queue's base alpha
    // untouched.
    static MObject aMaterialAlpha;
    // Optional authored/evaluated material values.  Array logical indices are
    // PMX material indices; absent elements leave the queue's base values
    // untouched.
    static MObject aMaterialValues;
    static MObject aMaterialValueChildren[13];
    // Internal, non-persistent DG input.  VP2 publishes readiness here so
    // Maya dirties and reevaluates the connected visibility output.
    static MObject aProxyReady;
    // Transient output driving the ordinary source mesh visibility.  It is
    // true until a proxy has valid DG data and committed VP2 buffers.
    static MObject aSourceVisibility;

    MmdRenderShape();
    ~MmdRenderShape() override;

    static void* creator();
    static MStatus initialize();
    static MmdRenderShape* fromMObject(const MObject& object,
                                       MStatus* status = nullptr);
    static bool prepareForPluginUnload();

    void postConstructor() override;
    MStatus preEvaluation(const MDGContext& context,
                          const MEvaluationNode& evaluationNode) override;
    MStatus setDependentsDirty(const MPlug& plug,
                               MPlugArray& plugArray) override;
    MStatus compute(const MPlug& plug, MDataBlock& data) override;
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

    bool setMaterialSplitGeometry(
        const std::vector<std::vector<float>>& submeshPositions,
        const std::vector<std::vector<float>>& submeshNormals,
        const std::vector<std::vector<float>>& submeshUvs,
        const std::vector<std::vector<uint32_t>>& submeshIndices,
        const std::vector<mmd::MmdRenderQueueInput>& queueInputs,
        double scale,
        const std::vector<std::vector<uint32_t>>& submeshSourceIndices);

    /**
     * Replace only the flattened position/normal streams with an evaluated
     * Maya mesh.  Queue order, UVs, and indices remain owned by the static
     * material split.  No state is changed when validation fails.
     */
    bool updateEvaluatedMesh(const MObject& meshObject);

    /** Mark the static geometry usable after an absent input mesh. */
    void useStaticGeometry();

    /** Return false after an invalid connected input has failed closed. */
    bool hasValidGeometry() const;

    /**
     * Publish transient proxy readiness and update the connected source
     * visibility output.  The output remains source-visible whenever the
     * proxy is not fully ready.
     */
    bool setProxyReady(bool ready);

    /** Update one material's effective alpha and rebuild the ordered items. */
    bool updateMaterialAlpha(std::size_t materialIndex, float diffuseAlpha);

    /** Pull present DG alpha elements and apply only changed effective values. */
    void updateEvaluatedMaterialAlpha();

    /** Pull present DG material-value records without rebuilding the queue. */
    void updateEvaluatedMaterialValues();

    /** Swap two adjacent material indices without rebuilding geometry buffers. */
    bool reindexMaterialQueue(std::size_t firstIndex, std::size_t secondIndex);

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
        bool selfShadow = false;
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
        std::array<float, 3> materialValuesDiffuseColor =
            {1.0F, 1.0F, 1.0F};
        std::array<float, 3> materialValuesSpecularColor =
            {0.0F, 0.0F, 0.0F};
        float materialValuesShininess = 0.0F;
        std::array<float, 3> materialValuesAmbientColor =
            {0.3F, 0.3F, 0.3F};
        std::array<float, 3> materialValuesEdgeColorRGB =
            {0.0F, 0.0F, 0.0F};
        float materialValuesEdgeColorA = 1.0F;
        float materialValuesEdgeSize = 0.0F;
        std::array<float, 4> materialValuesMainTextureMultiply =
            {1.0F, 1.0F, 1.0F, 1.0F};
        std::array<float, 4> materialValuesMainTextureAdd =
            {0.0F, 0.0F, 0.0F, 0.0F};
        std::array<float, 4> materialValuesSphereTextureMultiply =
            {1.0F, 1.0F, 1.0F, 1.0F};
        std::array<float, 4> materialValuesSphereTextureAdd =
            {0.0F, 0.0F, 0.0F, 0.0F};
        std::array<float, 4> materialValuesToonTextureMultiply =
            {1.0F, 1.0F, 1.0F, 1.0F};
        std::array<float, 4> materialValuesToonTextureAdd =
            {0.0F, 0.0F, 0.0F, 0.0F};
    };

    struct GeometryData {
        std::vector<float> positions;
        std::vector<float> normals;
        std::vector<float> uvs;
        // One source mesh vertex index per flattened render vertex.  Material
        // seams may therefore repeat the same source index in this stream.
        std::vector<uint32_t> sourceVertexIndices;
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
    /** Record a fallback reason and return true only when it changed. */
    bool recordRenderFallbackReason(const std::string& reason);
    void recordMaterialBindingDiagnostic(
        const MaterialBindingDiagnostic& diagnostic);
    void recordGeometryWitness(std::size_t vertexCount,
                               std::size_t indexCount,
                               const std::string& descriptorSummary);
    std::string renderItemWitness() const;
    std::string materialBindingDiagnosticsJson() const;

private:
    bool applyMaterialAlphaUpdates(
        const std::vector<std::pair<std::size_t, float>>& updates);

    GeometryData geometry_;
    // Immutable authored streams used when the optional input mesh is absent
    // again after an evaluated update.
    std::vector<float> staticPositions_;
    std::vector<float> staticNormals_;
    MBoundingBox boundingBox_;
    MBoundingBox staticBoundingBox_;
    bool geometryValid_ = true;
    bool evaluatedGeometryActive_ = false;
    bool renderItemWitnessValid_ = false;
    std::vector<mmd::MmdRenderQueueEntry> renderItemWitnessEntries_;
    bool geometryWitnessValid_ = false;
    std::size_t geometryWitnessVertexCount_ = 0U;
    std::size_t geometryWitnessIndexCount_ = 0U;
    std::string geometryWitnessDescriptorSummary_;
    // Number of transient render-vertex normal slots repaired for the current
    // DG update.  This is diagnostic-only state; the repair is applied to the
    // VP2 streams and never mutates the source Maya mesh.
    std::size_t evaluatedNormalRepairCount_ = 0U;
    std::size_t evaluatedNormalStaticFallbackCount_ = 0U;
    std::string renderFallbackReason_;
    std::vector<MaterialBindingDiagnostic> materialBindingDiagnostics_;
};

/**
 * Diagnostic command for commandPort/GUI smoke.
 *
 * ``mmdRenderWitness -node <shape>`` returns ``pending`` until the custom
 * geometry override has created its render items, a transient ``failed``
 * reason when it falls back, then the pass order after recovery.
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

/** Native queue ordering update used by the Material reindex fast path. */
class MmdRenderQueueReindexCommand : public MPxCommand {
public:
    static void* creator();
    static MSyntax newSyntax();

    MStatus doIt(const MArgList& args) override;
    MStatus redoIt() override;
    MStatus undoIt() override;
    bool isUndoable() const override;

private:
    MStatus applySwap();

    MObjectHandle nodeHandle_;
    std::size_t firstIndex_ = 0U;
    std::size_t secondIndex_ = 0U;
};
