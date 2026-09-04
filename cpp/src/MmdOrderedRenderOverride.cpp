/**
 * @file MmdOrderedRenderOverride.cpp
 * @brief Opt-in MMD ordered draw operation for the DX11 viewport.
 *
 * The operation deliberately owns only Maya object handles and its own GPU
 * resources.  Geometry is copied from an evaluated MmdRenderShape during
 * execute(), then discarded after the draw list has been uploaded.
 */

#include "MmdOrderedRenderOverride.h"

#include "MmdNativeMaterial.h"
#include "MmdRenderOverride.h"
#include "MmdRenderShape.h"

#include <maya/MDagPath.h>
#include <maya/MArgDatabase.h>
#include <maya/MDoubleArray.h>
#include <maya/MDrawContext.h>
#include <maya/MFn.h>
#include <maya/MFnDependencyNode.h>
#include <maya/MFnSet.h>
#include <maya/MGlobal.h>
#include <maya/MItDag.h>
#include <maya/MMatrix.h>
#include <maya/MObjectHandle.h>
#include <maya/MSelectionList.h>
#include <maya/MStatus.h>
#include <maya/MShaderManager.h>
#include <maya/MTextureManager.h>
#include <maya/MViewport2Renderer.h>

#ifdef _WIN32
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <d3d11.h>
#include <d3dcompiler.h>
#endif

#include <cstddef>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <limits>
#include <memory>
#include <sstream>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <utility>
#include <vector>

namespace {

constexpr const char* kOverrideName = "mmdOrdered";
constexpr const char* kOperationName = "mmdOrderedOperation";
constexpr const char* kOpaqueOperationName = "mmdOrderedOpaqueOperation";
constexpr const char* kOpaqueSceneName = "mmdOrderedOpaqueScene";
constexpr const char* kPreSceneUIName = "mmdOrderedPreSceneUI";
constexpr const char* kNonMmdTransparentSceneName =
    "mmdOrderedNonMmdTransparentScene";
constexpr const char* kPostSceneUIName = "mmdOrderedPostSceneUI";
constexpr unsigned int kOrderedTargetSize = 2048U;

MmdOrderedRenderOverride* gOrderedOverride = nullptr;
bool gRegistered = false;

std::string jsonEscape(const std::string& value)
{
    std::string result;
    result.reserve(value.size());
    for (const char character : value) {
        if (character == '\\' || character == '"') {
            result.push_back('\\');
        }
        result.push_back(character);
    }
    return result;
}

bool isMmdShape(const MDagPath& path)
{
    MStatus status;
    MFnDependencyNode dependencyNode(path.node(), &status);
    return status && dependencyNode.typeName() == MString("mmdRenderShape");
}

bool isolateContainsPath(const MSelectionList& members, const MDagPath& path)
{
    MDagPath ancestor = path;
    while (ancestor.length() > 0U) {
        MStatus status;
        if (members.hasItem(ancestor, MObject::kNullObj, &status) && status) {
            return true;
        }
        if (!ancestor.pop()) {
            break;
        }
    }
    return false;
}

bool buildPanelShapePaths(const MString& destination,
                          std::vector<MDagPath>& shapePaths,
                          MSelectionList& nonMmdSelection,
                          std::string& error)
{
    if (destination.length() == 0U) {
        error = "ordered setup has no destination panel";
        return false;
    }

    int isolateState = 0;
    const MString quotedDestination =
        MString("\"") + destination + MString("\"");
    if (!MGlobal::executeCommand(
            MString("isolateSelect -q -state ") + quotedDestination,
            isolateState,
            false,
            false)) {
        error = "isolateSelect -q -state failed";
        return false;
    }

    MSelectionList isolateMembers;
    if (isolateState != 0) {
        MStatus commandStatus;
        const MString setName = MGlobal::executeCommandStringResult(
            MString("isolateSelect -q -viewObjects ") + quotedDestination,
            false,
            false,
            &commandStatus);
        if (!commandStatus) {
            error = "isolateSelect -q -viewObjects failed";
            return false;
        }
        if (setName.length() > 0U) {
            MSelectionList setSelection;
            if (!setSelection.add(setName)) {
                error = "isolateSelect view set could not be resolved";
                return false;
            }
            MObject setObject;
            if (!setSelection.getDependNode(0U, setObject)) {
                error = "isolateSelect view set is not a dependency node";
                return false;
            }
            MFnSet viewSet(setObject, &commandStatus);
            if (!commandStatus || !viewSet.getMembers(isolateMembers, true)) {
                error = "MFnSet::getMembers failed for isolate set";
                return false;
            }
        }
    }

    MStatus iteratorStatus;
    MItDag iterator(MItDag::kDepthFirst, MFn::kShape, &iteratorStatus);
    if (!iteratorStatus) {
        error = "MItDag shape enumeration failed";
        return false;
    }
    for (;;) {
        const bool done = iterator.isDone(&iteratorStatus);
        if (!iteratorStatus) {
            error = "MItDag shape enumeration failed";
            return false;
        }
        if (done) {
            break;
        }
        MDagPath path;
        if (!iterator.getPath(path)) {
            error = "MItDag path lookup failed";
            return false;
        }
        MStatus visibilityStatus;
        const bool visible = path.isVisible(&visibilityStatus);
        if (!visibilityStatus) {
            error = "MItDag visibility lookup failed";
            return false;
        }
        const bool templated = path.isTemplated(&visibilityStatus);
        if (!visibilityStatus) {
            error = "MItDag visibility lookup failed";
            return false;
        }
        if (visible && !templated &&
            (isolateState == 0 ||
             isolateContainsPath(isolateMembers, path))) {
            if (isMmdShape(path)) {
                shapePaths.push_back(path);
            } else if (!nonMmdSelection.add(path, MObject::kNullObj, true)) {
                error = "could not build non-MMD scene selection";
                return false;
            }
        }
        if (!iterator.next()) {
            error = "MItDag shape enumeration failed";
            return false;
        }
    }
    return true;
}

class OrderedSceneRender : public MHWRender::MSceneRender {
public:
    OrderedSceneRender(const MString& name,
                       MHWRender::MSceneRender::MSceneFilterOption filter,
                       unsigned int clearMask,
                       const MSelectionList* selection = nullptr)
        : MSceneRender(name)
        , filter_(filter)
        , hasSelection_(selection != nullptr)
    {
        if (selection) {
            selection_ = *selection;
        }
        clearOperation().setMask(clearMask);
    }

    MHWRender::MSceneRender::MSceneFilterOption renderFilterOverride() override
    {
        return filter_;
    }

    const MSelectionList* objectSetOverride() override
    {
        return hasSelection_ ? &selection_ : nullptr;
    }

private:
    MHWRender::MSceneRender::MSceneFilterOption filter_;
    MSelectionList selection_;
    bool hasSelection_ = false;
};

}  // namespace

class MmdOrderedRenderOverride::OrderedRenderOperation
    : public MHWRender::MUserRenderOperation {
public:
    using MShaderInstance = MHWRender::MShaderInstance;
    using MTexture = MHWRender::MTexture;
    using MTextureManager = MHWRender::MTextureManager;

    struct ShapeRecord {
        MDagPath path;
        MObjectHandle handle;
    };

    OrderedRenderOperation(const MString& name,
                           MmdOrderedRenderOverride* owner,
                           std::vector<ShapeRecord> records,
                           const std::string& shaderPath)
        : MUserRenderOperation(name)
        , owner_(owner)
        , records_(std::move(records))
        , shaderPath_(shaderPath)
    {
        resetFrame();
    }

    ~OrderedRenderOperation() override { releaseResources(); }

    void setRecords(std::vector<ShapeRecord> records)
    {
        records_ = std::move(records);
        resetFrame();
    }

    void resetWitness()
    {
        drawCount_ = 0U;
        casterDrawCount_ = 0U;
        receiverDrawCount_ = 0U;
        casterMaterialIndices_.clear();
        pmxOrder_.clear();
        outlineOrder_.clear();
        lastError_.clear();
        frameResources_ = MmdNativeCasterRenderOverride::FrameResources();
        frameResourcesReady_ = false;
        shadowReady_ = false;
        targetWidth_ = 0U;
        targetHeight_ = 0U;
        targetHandleReady_ = false;
    }

    void resetFrame()
    {
        framePlans_.clear();
        framePrepared_ = false;
        framePreparationFailed_ = false;
        resetWitness();
    }

    MStatus execute(const MHWRender::MDrawContext& drawContext) override
    {
        return executePass(drawContext, false);
    }

    MStatus executePass(const MHWRender::MDrawContext& drawContext,
                        bool opaquePhase)
    {
#ifndef _WIN32
        return fail("MMD ordered render requires DirectX 11");
#else
        if (!prepareFrame(drawContext)) {
            return MStatus::kFailure;
        }

        if (opaquePhase && frameResourcesReady_ &&
            frameResources_.selfShadowMode > 0) {
            if (!renderCasters(drawContext)) {
                return MStatus::kFailure;
            }
        }

        for (const DrawPlan& plan : framePlans_) {
            const bool planOpaque =
                plan.order.pass != mmd::MmdDrawPass::Transparent;
            if (planOpaque != opaquePhase) {
                continue;
            }
            MShaderInstance* shader =
                shaderFor(plan.material, plan.order.pass, plan.outline);
            if (!shader || shader->bind(drawContext) != MStatus::kSuccess) {
                return fail("ordered shader bind failed");
            }
            int selfShadowMode = 0;
            if (!bindMaterial(shader, plan.material) ||
                !setBodyShadowParameters(shader, plan, selfShadowMode) ||
                !setFrameParameters(shader, drawContext, plan.world) ||
                shader->updateParameters(drawContext) != MStatus::kSuccess ||
                shader->activatePass(drawContext, 0U) != MStatus::kSuccess) {
                shader->unbind(drawContext);
                resetInputAssembler();
                return fail("ordered shader parameter update failed");
            }

            const UINT stride = sizeof(NativeVertex);
            const UINT offset = 0U;
            context_->IASetInputLayout(inputLayout_);
            context_->IASetVertexBuffers(0U, 1U, &vertexBuffer_, &stride, &offset);
            context_->IASetIndexBuffer(indexBuffer_, DXGI_FORMAT_R32_UINT, 0U);
            context_->IASetPrimitiveTopology(D3D11_PRIMITIVE_TOPOLOGY_TRIANGLELIST);
            context_->DrawIndexed(plan.indexCount,
                                  plan.firstIndex,
                                  0);
            ++drawCount_;
            if (selfShadowMode > 0) {
                ++receiverDrawCount_;
            }
            pmxOrder_.push_back(plan.order);
            outlineOrder_.push_back(plan.outline);
            resetInputAssembler();
            if (shader->unbind(drawContext) != MStatus::kSuccess) {
                return fail("ordered shader unbind failed");
            }
        }
        return MStatus::kSuccess;
#endif
    }

    bool requiresResetDeviceStates() const override { return true; }

    std::string diagnosticsJson(bool captureShadowDepth) const
    {
        std::ostringstream result;
        result << "{\"override\":\"" << kOverrideName
               << "\",\"registered\":true,\"state\":\""
               << (lastError_.empty() ? "active" : "error")
               << "\",\"enabled\":true,\"drawCount\":" << drawCount_
               << ",\"casterDrawCount\":" << casterDrawCount_
               << ",\"casterMaterialIndices\":[";
        for (std::size_t index = 0U; index < casterMaterialIndices_.size();
             ++index) {
            if (index != 0U) {
                result << ',';
            }
            result << casterMaterialIndices_[index];
        }
        result << "],\"receiverDrawCount\":" << receiverDrawCount_
               << ",\"frameResourcesReady\":"
               << (frameResourcesReady_ ? "true" : "false")
               << ",\"sameFrameShadowReady\":"
               << (shadowReady_ ? "true" : "false")
               << ",\"selfShadowMode\":"
               << frameResources_.selfShadowMode
               << ",\"targetSize\":{\"width\":" << targetWidth_
               << ",\"height\":" << targetHeight_
               << "},\"targetHandleReady\":"
               << (targetHandleReady_ ? "true" : "false")
               << ",\"error\":\"" << jsonEscape(lastError_)
               << "\",\"pmxOrder\":[";
        for (std::size_t index = 0U; index < pmxOrder_.size(); ++index) {
            if (index != 0U) {
                result << ',';
            }
            result << "{\"materialIndex\":"
                   << pmxOrder_[index].materialIndex
                   << ",\"submeshIndex\":"
                   << pmxOrder_[index].submeshIndex
                   << ",\"pass\":\""
                   << mmd::mmdDrawPassName(pmxOrder_[index].pass)
                   << "\",\"outline\":"
                   << (outlineOrder_[index] ? "true" : "false") << "}";
        }
        result << "]";
        if (captureShadowDepth) {
            result << ",\"shadowDepth\":" << shadowDepthJson();
        }
        result << "}";
        return result.str();
    }

    bool prepareForPluginUnload()
    {
#ifdef _WIN32
        return releaseResourcesForUnload();
#else
        return true;
#endif
    }

private:
    // Explicit witness readback only. Normal frame rendering never maps the
    // 2048-square GPU target to the CPU.
    std::string shadowDepthJson() const
    {
        if (!shadowReady_ || !frameResources_.colorTarget) {
            return "{\"available\":false}";
        }
        int rowPitch = 0;
        std::size_t slicePitch = 0U;
        void* raw = frameResources_.colorTarget->rawData(rowPitch, slicePitch);
        const unsigned int size = MmdNativeCasterRenderOverride::kTargetSize;
        if (!raw || rowPitch < static_cast<int>(size * sizeof(float)) ||
            slicePitch < static_cast<std::size_t>(rowPitch) * size) {
            if (raw) {
                MHWRender::MRenderTarget::freeRawData(raw);
            }
            return "{\"available\":false}";
        }
        std::size_t written = 0U;
        std::size_t invalid = 0U;
        float minimum = 1.0F;
        float maximum = 0.0F;
        std::uint64_t hash = 1469598103934665603ULL;
        const auto* bytes = static_cast<const unsigned char*>(raw);
        for (unsigned int y = 0U; y < size; ++y) {
            for (unsigned int x = 0U; x < size; ++x) {
                float value;
                const auto* pixel = bytes + static_cast<std::size_t>(y) * rowPitch +
                                    static_cast<std::size_t>(x) * sizeof(float);
                std::memcpy(&value, pixel, sizeof(value));
                for (std::size_t index = 0U; index < sizeof(value); ++index) {
                    hash = (hash ^ pixel[index]) * 1099511628211ULL;
                }
                if (!std::isfinite(value) || value < 0.0F || value > 1.0F) {
                    ++invalid;
                } else if (value < 1.0F) {
                    ++written;
                    minimum = std::min(minimum, value);
                    maximum = std::max(maximum, value);
                }
            }
        }
        MHWRender::MRenderTarget::freeRawData(raw);
        std::ostringstream result;
        result << "{\"available\":true,\"writtenSamples\":" << written
               << ",\"invalidSamples\":" << invalid
               << ",\"minimum\":" << minimum << ",\"maximum\":" << maximum
               << ",\"hash\":\"" << std::hex << hash << "\"}";
        return result.str();
    }

    struct DrawPlan {
        mmd::MmdRenderQueueInput material;
        mmd::MmdRenderQueueEntry order;
        bool outline = false;
        MMatrix world = MMatrix::identity;
        unsigned int firstIndex = 0U;
        unsigned int indexCount = 0U;
    };

#ifdef _WIN32
    struct NativeVertex {
        float position[3];
        float texCoord0[2];
        float texCoord1[2];
        float vertexColor0[4];
        float vertexColor1[4];
        float normal[3];
        float tangent[3];
        float binormal[3];
    };

    static_assert(sizeof(NativeVertex) == 96U,
                  "unexpected ordered vertex packing");

    static constexpr D3D11_INPUT_ELEMENT_DESC kInputLayout[8] = {
        {"POSITION", 0U, DXGI_FORMAT_R32G32B32_FLOAT, 0U,
         static_cast<UINT>(offsetof(NativeVertex, position)),
         D3D11_INPUT_PER_VERTEX_DATA, 0U},
        {"TEXCOORD", 0U, DXGI_FORMAT_R32G32_FLOAT, 0U,
         static_cast<UINT>(offsetof(NativeVertex, texCoord0)),
         D3D11_INPUT_PER_VERTEX_DATA, 0U},
        {"TEXCOORD", 1U, DXGI_FORMAT_R32G32_FLOAT, 0U,
         static_cast<UINT>(offsetof(NativeVertex, texCoord1)),
         D3D11_INPUT_PER_VERTEX_DATA, 0U},
        {"COLOR", 0U, DXGI_FORMAT_R32G32B32A32_FLOAT, 0U,
         static_cast<UINT>(offsetof(NativeVertex, vertexColor0)),
         D3D11_INPUT_PER_VERTEX_DATA, 0U},
        {"COLOR", 1U, DXGI_FORMAT_R32G32B32A32_FLOAT, 0U,
         static_cast<UINT>(offsetof(NativeVertex, vertexColor1)),
         D3D11_INPUT_PER_VERTEX_DATA, 0U},
        {"NORMAL", 0U, DXGI_FORMAT_R32G32B32_FLOAT, 0U,
         static_cast<UINT>(offsetof(NativeVertex, normal)),
         D3D11_INPUT_PER_VERTEX_DATA, 0U},
        {"TANGENT", 0U, DXGI_FORMAT_R32G32B32_FLOAT, 0U,
         static_cast<UINT>(offsetof(NativeVertex, tangent)),
         D3D11_INPUT_PER_VERTEX_DATA, 0U},
        {"BINORMAL", 0U, DXGI_FORMAT_R32G32B32_FLOAT, 0U,
         static_cast<UINT>(offsetof(NativeVertex, binormal)),
         D3D11_INPUT_PER_VERTEX_DATA, 0U},
    };

    struct D3DRelease {
        template <typename T>
        static void release(T*& object)
        {
            if (object) {
                object->Release();
                object = nullptr;
            }
        }
    };

    class RawTargetScope {
    public:
        explicit RawTargetScope(ID3D11DeviceContext* context)
            : context_(context)
        {
            if (!context_) {
                return;
            }
            context_->OMGetRenderTargets(
                D3D11_SIMULTANEOUS_RENDER_TARGET_COUNT,
                renderTargets_, &depthStencilView_);
            viewportCount_ = D3D11_VIEWPORT_AND_SCISSORRECT_OBJECT_COUNT_PER_PIPELINE;
            context_->RSGetViewports(&viewportCount_, viewports_);
            captured_ = true;
        }

        ~RawTargetScope()
        {
            if (!captured_) {
                return;
            }
            context_->OMSetRenderTargets(
                D3D11_SIMULTANEOUS_RENDER_TARGET_COUNT, renderTargets_,
                depthStencilView_);
            context_->RSSetViewports(viewportCount_, viewports_);
            for (ID3D11RenderTargetView*& renderTarget : renderTargets_) {
                D3DRelease::release(renderTarget);
            }
            D3DRelease::release(depthStencilView_);
        }

        bool bind(MHWRender::MRenderTarget* colorTarget,
                  MHWRender::MRenderTarget* depthTarget)
        {
            if (!captured_ || !colorTarget || !depthTarget) {
                return false;
            }
            ID3D11RenderTargetView* colorView =
                static_cast<ID3D11RenderTargetView*>(
                    colorTarget->resourceHandle());
            ID3D11DepthStencilView* depthView =
                static_cast<ID3D11DepthStencilView*>(
                    depthTarget->resourceHandle());
            if (!colorView || !depthView) {
                return false;
            }

            context_->OMSetRenderTargets(1U, &colorView, depthView);
            const D3D11_VIEWPORT viewport = {
                0.0F,
                0.0F,
                static_cast<float>(kOrderedTargetSize),
                static_cast<float>(kOrderedTargetSize),
                0.0F,
                1.0F};
            context_->RSSetViewports(1U, &viewport);
            const float clearColor[4] = {1.0F, 1.0F, 1.0F, 1.0F};
            context_->ClearRenderTargetView(colorView, clearColor);
            context_->ClearDepthStencilView(depthView, D3D11_CLEAR_DEPTH,
                                             1.0F, 0U);
            return true;
        }

        bool captured() const { return captured_; }

    private:
        static constexpr UINT kMaxRenderTargets =
            D3D11_SIMULTANEOUS_RENDER_TARGET_COUNT;
        static constexpr UINT kMaxViewports =
            D3D11_VIEWPORT_AND_SCISSORRECT_OBJECT_COUNT_PER_PIPELINE;

        ID3D11DeviceContext* context_ = nullptr;
        ID3D11RenderTargetView* renderTargets_[kMaxRenderTargets] = {};
        ID3D11DepthStencilView* depthStencilView_ = nullptr;
        D3D11_VIEWPORT viewports_[kMaxViewports] = {};
        UINT viewportCount_ = 0U;
        bool captured_ = false;
    };

    const char* techniqueFor(const mmd::MmdRenderQueueInput& material,
                             mmd::MmdDrawPass pass,
                             bool outline) const
    {
        if (outline) {
            if (pass == mmd::MmdDrawPass::Transparent) {
                return material.doubleSided
                           ? "MMDNativeOutlineTranslucentDoubleSided"
                           : "MMDNativeOutlineTranslucent";
            }
            return material.doubleSided ? "MMDNativeOutlineDoubleSided"
                                        : "MMDNativeOutline";
        }
        if (pass == mmd::MmdDrawPass::Transparent) {
            return material.doubleSided ? "MMDNativeTranslucentDoubleSided"
                                        : "MMDNativeTranslucent";
        }
        return material.doubleSided ? "MMDNativeOpaqueDoubleSided"
                                    : "MMDNativeOpaque";
    }

    const char* casterTechniqueFor(const DrawPlan& plan) const
    {
        return plan.order.pass == mmd::MmdDrawPass::Cutout
                   ? "MMDNativeCasterCutout"
                   : "MMDNativeCaster";
    }

    bool isCasterPlan(const DrawPlan& plan) const
    {
        return !plan.outline && plan.material.selfShadowMap &&
               plan.order.pass != mmd::MmdDrawPass::Transparent;
    }

    MStatus fail(const std::string& message)
    {
        lastError_ = message;
        if (owner_) {
            owner_->requestFallback(message);
        }
        return MStatus::kFailure;
    }

    bool collectFrame(std::vector<DrawPlan>& plans,
                      std::vector<NativeVertex>& vertices,
                      std::vector<unsigned int>& indices,
                      MSelectionList& visibleSelection)
    {
        for (std::size_t shapeIndex = 0U; shapeIndex < records_.size();
             ++shapeIndex) {
            const ShapeRecord& record = records_[shapeIndex];
            if (!record.handle.isValid() || !record.handle.isAlive() ||
                !record.path.isValid()) {
                fail("ordered shape handle/path is invalid");
                return false;
            }
            MStatus visibilityStatus;
            const bool visible = record.path.isVisible(&visibilityStatus);
            if (!visibilityStatus) {
                fail("ordered shape visibility lookup failed");
                return false;
            }
            if (!visible) {
                continue;
            }
            MStatus templatedStatus;
            const bool templated = record.path.isTemplated(&templatedStatus);
            if (!templatedStatus) {
                fail("ordered shape template lookup failed");
                return false;
            }
            if (templated) {
                continue;
            }
            MStatus status;
            MmdRenderShape* shape =
                MmdRenderShape::fromMObject(record.handle.object(), &status);
            if (!shape || !status || !shape->hasValidGeometry()) {
                fail("ordered shape geometry is unavailable");
                return false;
            }

            if (!visibleSelection.add(record.path)) {
                fail("ordered caster selection could not be built");
                return false;
            }

            const MmdRenderShape::GeometryData& geometry = shape->geometry();
            const std::size_t vertexCount = geometry.positions.size() / 3U;
            if (vertexCount == 0U || geometry.positions.size() % 3U != 0U ||
                geometry.normals.size() < vertexCount * 3U ||
                geometry.uvs.size() < vertexCount * 2U) {
                fail("ordered shape geometry streams are incomplete");
                return false;
            }
            if (geometry.queueGeometry.empty()) {
                continue;
            }
            const unsigned int vertexBase =
                static_cast<unsigned int>(vertices.size());
            if (vertices.size() > std::numeric_limits<unsigned int>::max() -
                                      vertexCount) {
                fail("ordered vertex buffer is too large");
                return false;
            }
            for (std::size_t vertex = 0U; vertex < vertexCount; ++vertex) {
                NativeVertex packet = {};
                const std::size_t source = vertex * 3U;
                packet.position[0] = geometry.positions[source];
                packet.position[1] = geometry.positions[source + 1U];
                packet.position[2] = geometry.positions[source + 2U];
                packet.normal[0] = geometry.normals[source];
                packet.normal[1] = geometry.normals[source + 1U];
                packet.normal[2] = geometry.normals[source + 2U];
                packet.texCoord0[0] = geometry.uvs[vertex * 2U];
                packet.texCoord0[1] = geometry.uvs[vertex * 2U + 1U];
                vertices.push_back(packet);
            }

            for (const MmdRenderShape::QueueGeometry& queueGeometry :
                 geometry.queueGeometry) {
                if (queueGeometry.indices.empty() ||
                    queueGeometry.indices.size() >
                        std::numeric_limits<UINT>::max()) {
                    fail("ordered queue entry has no valid indices");
                    return false;
                }
                if (indices.size() >
                    std::numeric_limits<UINT>::max() -
                        queueGeometry.indices.size()) {
                    fail("ordered index buffer is too large");
                    return false;
                }
                DrawPlan plan;
                plan.material = queueGeometry.material;
                plan.order = queueGeometry.entry;
                MStatus matrixStatus;
                plan.world = record.path.inclusiveMatrix(&matrixStatus);
                if (!matrixStatus) {
                    fail("ordered shape world matrix is unavailable");
                    return false;
                }
                plan.firstIndex = static_cast<UINT>(indices.size());
                plan.indexCount = static_cast<UINT>(queueGeometry.indices.size());
                for (const uint32_t localIndex : queueGeometry.indices) {
                    const std::size_t sourceIndex =
                        static_cast<std::size_t>(queueGeometry.vertexOffset) +
                        static_cast<std::size_t>(localIndex);
                    if (sourceIndex >= vertexCount) {
                        fail("ordered queue index exceeds shape vertices");
                        return false;
                    }
                    indices.push_back(vertexBase +
                                     static_cast<unsigned int>(sourceIndex));
                }
                const bool outline = queueGeometry.material.edgeDrawing &&
                                     queueGeometry.material.edgeSize > 0.0F &&
                                     queueGeometry.material.edgeAlpha > 0.0F;
                const bool transparent =
                    queueGeometry.entry.pass == mmd::MmdDrawPass::Transparent;
                if (outline && !transparent) {
                    DrawPlan outlinePlan = plan;
                    outlinePlan.outline = true;
                    plans.push_back(std::move(outlinePlan));
                }
                plans.push_back(std::move(plan));
                if (outline && transparent) {
                    DrawPlan outlinePlan = plans.back();
                    outlinePlan.outline = true;
                    plans.push_back(std::move(outlinePlan));
                }
            }
        }
        return true;
    }

    void updateTargetDiagnostics()
    {
        if (!frameResources_.colorTarget || !frameResources_.depthTarget) {
            return;
        }
        MHWRender::MRenderTargetDescription description;
        frameResources_.colorTarget->targetDescription(description);
        targetWidth_ = description.width();
        targetHeight_ = description.height();
        targetHandleReady_ =
            frameResources_.colorTarget->resourceHandle() != nullptr &&
            frameResources_.depthTarget->resourceHandle() != nullptr;
    }

    bool setCasterParameters(MShaderInstance* shader, const DrawPlan& plan)
    {
        return shader->setParameter("World", plan.world) &&
               shader->setParameter("CasterLightViewProjection",
                                    frameResources_.lightViewProjection) &&
               shader->setParameter("CasterDepthBias",
                                    frameResources_.depthBias);
    }

    bool setBodyShadowParameters(MShaderInstance* shader,
                                 const DrawPlan& plan,
                                 int& selfShadowMode)
    {
        const bool receiverEligible =
            !plan.outline && plan.material.selfShadow && frameResourcesReady_ &&
            shadowReady_ && frameResources_.selfShadowMode > 0 &&
            frameResources_.colorTarget != nullptr;
        selfShadowMode = receiverEligible ? frameResources_.selfShadowMode : 0;

        if (selfShadowMode > 0) {
            MHWRender::MRenderTargetAssignment assignment{
                frameResources_.colorTarget};
            if (shader->setParameter("NativeCasterDepthTexture", assignment) !=
                MStatus::kSuccess) {
                return false;
            }
            MmdNativeCasterRenderOverride::registerReceiverShader(shader);
            receiverShaders_.insert(shader);
        }
        if (frameResourcesReady_ &&
            (shader->setParameter("CasterLightViewProjection",
                                  frameResources_.lightViewProjection) !=
                 MStatus::kSuccess ||
             shader->setParameter("CasterDepthBias", frameResources_.depthBias) !=
                 MStatus::kSuccess)) {
            return false;
        }
        return shader->setParameter("NativeSelfShadowMode", selfShadowMode) ==
               MStatus::kSuccess;
    }

    MShaderInstance* casterShaderFor(const DrawPlan& plan)
    {
        const char* technique = casterTechniqueFor(plan);
        const std::string key = std::string("caster:") + technique;
        return shaderForTechnique(key, technique, false, "caster");
    }

    bool preflightCasters(const std::vector<DrawPlan>& plans,
                          const MHWRender::MDrawContext& drawContext)
    {
        for (const DrawPlan& plan : plans) {
            if (!isCasterPlan(plan)) {
                continue;
            }
            MShaderInstance* shader = casterShaderFor(plan);
            if (!shader || shader->bind(drawContext) != MStatus::kSuccess) {
                fail("ordered caster preflight shader bind failed");
                return false;
            }
            const bool parametersReady =
                bindMaterial(shader, plan.material) &&
                setCasterParameters(shader, plan);
            const bool parametersUpdated =
                parametersReady &&
                shader->updateParameters(drawContext) == MStatus::kSuccess;
            const bool passActivated =
                parametersUpdated &&
                shader->activatePass(drawContext, 0U) == MStatus::kSuccess;
            const MStatus unbindStatus = shader->unbind(drawContext);
            if (!parametersReady || !parametersUpdated || !passActivated ||
                unbindStatus != MStatus::kSuccess) {
                fail("ordered caster preflight failed");
                return false;
            }
        }
        return true;
    }

    bool renderCasters(const MHWRender::MDrawContext& drawContext)
    {
        RawTargetScope targetScope(context_);
        if (!targetScope.captured() ||
            !targetScope.bind(frameResources_.colorTarget,
                              frameResources_.depthTarget)) {
            shadowReady_ = false;
            fail("ordered caster target binding failed");
            return false;
        }
        targetHandleReady_ = true;
        for (const DrawPlan& plan : framePlans_) {
            if (!isCasterPlan(plan)) {
                continue;
            }
            MShaderInstance* shader = casterShaderFor(plan);
            if (!shader || shader->bind(drawContext) != MStatus::kSuccess) {
                shadowReady_ = false;
                fail("ordered caster shader bind failed");
                return false;
            }
            const bool parametersReady =
                bindMaterial(shader, plan.material) &&
                setCasterParameters(shader, plan);
            const bool parametersUpdated =
                parametersReady &&
                shader->updateParameters(drawContext) == MStatus::kSuccess;
            const bool passActivated =
                parametersUpdated &&
                shader->activatePass(drawContext, 0U) == MStatus::kSuccess;
            if (!parametersReady || !parametersUpdated || !passActivated) {
                shader->unbind(drawContext);
                resetInputAssembler();
                shadowReady_ = false;
                fail("ordered caster shader parameter update failed");
                return false;
            }

            const UINT stride = sizeof(NativeVertex);
            const UINT offset = 0U;
            context_->IASetInputLayout(inputLayout_);
            context_->IASetVertexBuffers(0U, 1U, &vertexBuffer_, &stride,
                                         &offset);
            context_->IASetIndexBuffer(indexBuffer_, DXGI_FORMAT_R32_UINT, 0U);
            context_->IASetPrimitiveTopology(D3D11_PRIMITIVE_TOPOLOGY_TRIANGLELIST);
            context_->DrawIndexed(plan.indexCount, plan.firstIndex, 0);
            ++casterDrawCount_;
            casterMaterialIndices_.push_back(plan.order.materialIndex);
            resetInputAssembler();
            if (shader->unbind(drawContext) != MStatus::kSuccess) {
                shadowReady_ = false;
                fail("ordered caster shader unbind failed");
                return false;
            }
        }
        shadowReady_ = true;
        return true;
    }

    bool prepareFrame(const MHWRender::MDrawContext& drawContext)
    {
        if (framePrepared_) {
            return true;
        }
        if (framePreparationFailed_) {
            return false;
        }

        std::vector<DrawPlan> plans;
        std::vector<NativeVertex> vertices;
        std::vector<unsigned int> indices;
        MSelectionList visibleSelection;
        if (!collectFrame(plans, vertices, indices, visibleSelection)) {
            framePreparationFailed_ = true;
            return false;
        }
        if (plans.empty()) {
            framePlans_ = std::move(plans);
            framePrepared_ = true;
            return true;
        }
        if (!ensureResources(drawContext) || !uploadFrame(vertices, indices)) {
            framePreparationFailed_ = true;
            return false;
        }
        MmdNativeCasterRenderOverride* nativeCasterOwner =
            owner_ ? owner_->nativeCasterOwner_ : nullptr;
        if (!nativeCasterOwner) {
            fail("ordered native caster resource owner is unavailable");
            framePreparationFailed_ = true;
            return false;
        }
        const MStatus resourceStatus = nativeCasterOwner->prepareFrameResources(
            visibleSelection, frameResources_, true);
        if (resourceStatus != MStatus::kSuccess) {
            fail("ordered native caster frame resource preparation failed");
            framePreparationFailed_ = true;
            return false;
        }
        frameResourcesReady_ = frameResources_.ready;
        updateTargetDiagnostics();
        if (!preflight(plans, drawContext) ||
            (frameResourcesReady_ && frameResources_.selfShadowMode > 0 &&
             !preflightCasters(plans, drawContext))) {
            framePreparationFailed_ = true;
            return false;
        }
        framePlans_ = std::move(plans);
        framePrepared_ = true;
        return true;
    }

    bool ensureResources(const MHWRender::MDrawContext&)
    {
        MHWRender::MRenderer* renderer = MHWRender::MRenderer::theRenderer(false);
        if (!renderer || renderer->drawAPI() != MHWRender::kDirectX11) {
            fail("DirectX 11 renderer is unavailable");
            return false;
        }
        ID3D11Device* device =
            static_cast<ID3D11Device*>(renderer->GPUDeviceHandle());
        if (!device) {
            fail("MRenderer::GPUDeviceHandle returned null");
            return false;
        }
        if (device_ != device) {
            if (!releaseResourcesForUnload()) {
                fail("ordered resource retirement failed during device change");
                return false;
            }
            device_ = device;
            device_->GetImmediateContext(&context_);
            if (!context_) {
                fail("ID3D11Device::GetImmediateContext failed");
                return false;
            }
        }
        if (inputLayout_) {
            return true;
        }
        if (shaderPath_.empty()) {
            fail("MMDNativeShader.fx path is unavailable");
            return false;
        }
        ID3DBlob* vertexShader = nullptr;
        ID3DBlob* errorBlob = nullptr;
        const std::filesystem::path shaderFilePath =
            std::filesystem::u8path(shaderPath_);
        const HRESULT compileStatus = D3DCompileFromFile(
            shaderFilePath.c_str(), nullptr,
            D3D_COMPILE_STANDARD_FILE_INCLUDE, "MainVS", "vs_5_0",
            D3DCOMPILE_ENABLE_STRICTNESS, 0U, &vertexShader, &errorBlob);
        if (FAILED(compileStatus)) {
            D3DRelease::release(errorBlob);
            D3DRelease::release(vertexShader);
            fail("D3DCompileFromFile(MainVS) failed");
            return false;
        }
        const HRESULT layoutStatus = device_->CreateInputLayout(
            kInputLayout, 8U, vertexShader->GetBufferPointer(),
            vertexShader->GetBufferSize(), &inputLayout_);
        D3DRelease::release(errorBlob);
        D3DRelease::release(vertexShader);
        if (FAILED(layoutStatus)) {
            fail("CreateInputLayout failed");
            return false;
        }
        return true;
    }

    bool ensureDynamicBuffer(ID3D11Buffer*& buffer,
                             UINT byteWidth,
                             UINT bindFlags)
    {
        if (buffer &&
            bufferCapacity_[bindFlags == D3D11_BIND_INDEX_BUFFER] >= byteWidth) {
            return true;
        }
        D3D11_BUFFER_DESC description = {};
        description.Usage = D3D11_USAGE_DYNAMIC;
        description.ByteWidth = byteWidth;
        description.BindFlags = bindFlags;
        description.CPUAccessFlags = D3D11_CPU_ACCESS_WRITE;
        ID3D11Buffer* replacement = nullptr;
        if (FAILED(device_->CreateBuffer(&description, nullptr, &replacement))) {
            fail(bindFlags == D3D11_BIND_INDEX_BUFFER
                     ? "CreateBuffer(index) failed"
                     : "CreateBuffer(vertex) failed");
            return false;
        }
        D3DRelease::release(buffer);
        buffer = replacement;
        bufferCapacity_[bindFlags == D3D11_BIND_INDEX_BUFFER] = byteWidth;
        return true;
    }

    bool uploadFrame(const std::vector<NativeVertex>& vertices,
                     const std::vector<unsigned int>& indices)
    {
        if (vertices.size() > std::numeric_limits<UINT>::max() / sizeof(NativeVertex) ||
            indices.size() > std::numeric_limits<UINT>::max() / sizeof(unsigned int)) {
            return fail("ordered frame exceeds DX11 buffer size") == MStatus::kSuccess;
        }
        const UINT vertexBytes =
            static_cast<UINT>(vertices.size() * sizeof(NativeVertex));
        const UINT indexBytes =
            static_cast<UINT>(indices.size() * sizeof(unsigned int));
        if (!ensureDynamicBuffer(vertexBuffer_, vertexBytes,
                                 D3D11_BIND_VERTEX_BUFFER) ||
            !ensureDynamicBuffer(indexBuffer_, indexBytes,
                                 D3D11_BIND_INDEX_BUFFER)) {
            return false;
        }
        D3D11_MAPPED_SUBRESOURCE mapped = {};
        if (FAILED(context_->Map(vertexBuffer_, 0U, D3D11_MAP_WRITE_DISCARD, 0U,
                                 &mapped))) {
            return fail("Map(vertex) failed") == MStatus::kSuccess;
        }
        std::memcpy(mapped.pData, vertices.data(), vertexBytes);
        context_->Unmap(vertexBuffer_, 0U);
        if (FAILED(context_->Map(indexBuffer_, 0U, D3D11_MAP_WRITE_DISCARD, 0U,
                                 &mapped))) {
            return fail("Map(index) failed") == MStatus::kSuccess;
        }
        std::memcpy(mapped.pData, indices.data(), indexBytes);
        context_->Unmap(indexBuffer_, 0U);
        return true;
    }

    MShaderInstance* shaderForTechnique(const std::string& key,
                                        const char* technique,
                                        bool transparent,
                                        const char* role)
    {
        const auto found = shaders_.find(key);
        if (found != shaders_.end()) {
            return found->second;
        }
        MHWRender::MRenderer* renderer =
            MHWRender::MRenderer::theRenderer(false);
        const MHWRender::MShaderManager* manager =
            renderer ? renderer->getShaderManager() : nullptr;
        if (!manager) {
            fail("MShaderManager is unavailable");
            return nullptr;
        }
        MString effectPath;
        effectPath.setUTF8(shaderPath_.c_str());
        MHWRender::MShaderCompileMacro macro{
            MString("_MAYA_PLUGIN_HANDLES_ALL_UNIFORMS_"), MString("TRUE")};
        MShaderInstance* shader = manager->getEffectsFileShader(
            effectPath, MString(technique), &macro, 1U);
        if (!shader) {
            fail(std::string("getEffectsFileShader failed for ordered ") +
                 role);
            return nullptr;
        }
        if (shader->setIsTransparent(transparent) != MStatus::kSuccess) {
            manager->releaseShader(shader);
            fail(std::string("setIsTransparent failed for ordered ") + role);
            return nullptr;
        }
        shaders_.emplace(key, shader);
        return shader;
    }

    MShaderInstance* shaderFor(const mmd::MmdRenderQueueInput& material,
                               mmd::MmdDrawPass pass,
                               bool outline)
    {
        const char* technique = techniqueFor(material, pass, outline);
        const std::string key(technique);
        return shaderForTechnique(
            key, technique,
            std::string(technique).find("Translucent") != std::string::npos,
            "material");
    }

    MTexture* acquireTexture(const std::string& path)
    {
        if (path.empty()) {
            return nullptr;
        }
        const auto found = textures_.find(path);
        if (found != textures_.end()) {
            return found->second;
        }
        MHWRender::MRenderer* renderer = MHWRender::MRenderer::theRenderer(false);
        MTextureManager* manager = renderer ? renderer->getTextureManager() : nullptr;
        if (!manager) {
            fail("MTextureManager is unavailable");
            return nullptr;
        }
        MString mayaPath;
        mayaPath.setUTF8(path.c_str());
        MTexture* texture = manager->acquireTexture(mayaPath, 0U, false);
        textures_.emplace(path, texture);
        return texture;
    }

    bool bindMaterial(MShaderInstance* shader,
                      const mmd::MmdRenderQueueInput& material)
    {
        const std::string toonPath = material.toonTexturePath.empty()
                                         ? mmd::nativeMaterialSharedToonPath(
                                               material.sharedToonIndex)
                                         : material.toonTexturePath;
        MTexture* mainTexture = acquireTexture(material.mainTexturePath);
        MTexture* sphereTexture = acquireTexture(material.sphereTexturePath);
        MTexture* toonTexture = acquireTexture(toonPath);
        return mmd::bindNativeMaterialParameters(
            shader, material, mainTexture, sphereTexture, toonTexture,
            !toonPath.empty(), nullptr);
    }

    bool setFrameParameters(MShaderInstance* shader,
                            const MHWRender::MDrawContext& drawContext,
                            const MMatrix& world)
    {
        MStatus status;
        const MMatrix view = drawContext.getMatrix(
            MHWRender::MFrameContext::kViewMtx, &status);
        if (!status) {
            return false;
        }
        const MMatrix viewInverse = drawContext.getMatrix(
            MHWRender::MFrameContext::kViewInverseMtx, &status);
        if (!status) {
            return false;
        }
        const MMatrix projection = drawContext.getMatrix(
            MHWRender::MFrameContext::kProjectionMtx, &status);
        if (!status) {
            return false;
        }
        const MMatrix viewProjection = drawContext.getMatrix(
            MHWRender::MFrameContext::kViewProjMtx, &status);
        if (!status) {
            return false;
        }
        const MDoubleArray viewPosition = drawContext.getTuple(
            MHWRender::MFrameContext::kViewPosition, &status);
        if (!status || viewPosition.length() < 3U) {
            return false;
        }
        int originX = 0;
        int originY = 0;
        int width = 0;
        int height = 0;
        if (drawContext.getViewportDimensions(originX, originY, width, height) !=
            MStatus::kSuccess) {
            return false;
        }
        const float viewPositionValue[3] = {
            static_cast<float>(viewPosition[0]),
            static_cast<float>(viewPosition[1]),
            static_cast<float>(viewPosition[2])};
        const float screenSize[2] = {static_cast<float>(width),
                                     static_cast<float>(height)};
        const MMatrix worldInverse = world.inverse();
        const MMatrix worldInverseTranspose = worldInverse.transpose();
        return shader->setParameter("View", view) &&
               shader->setParameter("ViewInv", viewInverse) &&
               shader->setParameter("Projection", projection) &&
               shader->setParameter("ViewProjection", viewProjection) &&
               shader->setParameter("ViewPosition", viewPositionValue) &&
               shader->setParameter("ScreenSize", screenSize) &&
               shader->setParameter("DevicePixelRatio", 1.0F) &&
               shader->setParameter("World", world) &&
               shader->setParameter("WorldInverse", worldInverse) &&
               shader->setParameter("WorldInverseTranspose",
                                    worldInverseTranspose) &&
               shader->setParameter("WorldViewProjection",
                                    world * viewProjection);
    }

    bool preflight(const std::vector<DrawPlan>& plans,
                   const MHWRender::MDrawContext& drawContext)
    {
        for (const DrawPlan& plan : plans) {
            MShaderInstance* shader =
                shaderFor(plan.material, plan.order.pass, plan.outline);
            if (!shader) {
                if (lastError_.empty()) {
                    fail("ordered preflight shader is unavailable");
                }
                return false;
            }
            if (!bindMaterial(shader, plan.material)) {
                if (lastError_.empty()) {
                    fail("ordered preflight material binding failed");
                }
                return false;
            }
            if (shader->bind(drawContext) != MStatus::kSuccess) {
                fail("ordered preflight shader bind failed");
                return false;
            }
            const bool bodyModeReset =
                shader->setParameter("NativeSelfShadowMode", 0) ==
                MStatus::kSuccess;
            const bool frameParametersReady =
                bodyModeReset && setFrameParameters(shader, drawContext,
                                                    plan.world);
            const bool parametersUpdated =
                frameParametersReady &&
                shader->updateParameters(drawContext) == MStatus::kSuccess;
            const bool passActivated =
                parametersUpdated &&
                shader->activatePass(drawContext, 0U) == MStatus::kSuccess;
            const MStatus unbindStatus = shader->unbind(drawContext);
            if (!frameParametersReady) {
                fail("ordered preflight frame parameters failed");
                return false;
            }
            if (!parametersUpdated) {
                fail("ordered preflight shader parameter update failed");
                return false;
            }
            if (!passActivated) {
                fail("ordered preflight shader pass activation failed");
                return false;
            }
            if (unbindStatus != MStatus::kSuccess) {
                fail("ordered preflight shader unbind failed");
                return false;
            }
        }
        return true;
    }

    void resetInputAssembler()
    {
        ID3D11Buffer* noBuffer = nullptr;
        const UINT noStride = 0U;
        const UINT noOffset = 0U;
        context_->IASetInputLayout(nullptr);
        context_->IASetVertexBuffers(0U, 1U, &noBuffer, &noStride, &noOffset);
        context_->IASetIndexBuffer(nullptr, DXGI_FORMAT_R32_UINT, 0U);
    }

    bool releaseShaderCache()
    {
        MHWRender::MRenderer* renderer =
            MHWRender::MRenderer::theRenderer(false);
        const MHWRender::MShaderManager* shaderManager =
            renderer ? renderer->getShaderManager() : nullptr;
        if (!shaderManager &&
            (!shaders_.empty() || !receiverShaders_.empty())) {
            lastError_ = "MShaderManager unavailable while retiring ordered shaders";
            return false;
        }
        if (shaderManager) {
            for (const auto& shader : shaders_) {
                if (!shader.second) {
                    continue;
                }
                const bool receiver =
                    receiverShaders_.count(shader.second) != 0U;
                if (receiver) {
                    MmdNativeCasterRenderOverride::beginReceiverShaderRetire(
                        shader.second);
                }
                shaderManager->releaseShader(shader.second);
                if (receiver) {
                    MmdNativeCasterRenderOverride::finishReceiverShaderRetire(
                        shader.second);
                }
            }
        }
        receiverShaders_.clear();
        shaders_.clear();
        return true;
    }

    bool releaseResourcesForUnload()
    {
        if (!releaseShaderCache()) {
            return false;
        }
        MHWRender::MRenderer* renderer =
            MHWRender::MRenderer::theRenderer(false);
        MTextureManager* textureManager =
            renderer ? renderer->getTextureManager() : nullptr;
        if (textureManager) {
            for (const auto& texture : textures_) {
                if (texture.second) {
                    textureManager->releaseTexture(texture.second);
                }
            }
        }
        textures_.clear();
        D3DRelease::release(inputLayout_);
        D3DRelease::release(vertexBuffer_);
        D3DRelease::release(indexBuffer_);
        D3DRelease::release(context_);
        device_ = nullptr;
        bufferCapacity_[0] = 0U;
        bufferCapacity_[1] = 0U;
        return true;
    }

    void releaseResources()
    {
        (void)releaseResourcesForUnload();
    }

    std::unordered_map<std::string, MShaderInstance*> shaders_;
    std::unordered_map<std::string, MTexture*> textures_;
    ID3D11Device* device_ = nullptr;
    ID3D11DeviceContext* context_ = nullptr;
    ID3D11InputLayout* inputLayout_ = nullptr;
    ID3D11Buffer* vertexBuffer_ = nullptr;
    ID3D11Buffer* indexBuffer_ = nullptr;
    UINT bufferCapacity_[2] = {0U, 0U};
#else
    MStatus fail(const std::string& message)
    {
        lastError_ = message;
        if (owner_) {
            owner_->requestFallback(message);
        }
        return MStatus::kFailure;
    }

    void releaseResources() {}
#endif

    MmdOrderedRenderOverride* owner_ = nullptr;
    std::vector<ShapeRecord> records_;
    std::string shaderPath_;
    unsigned int drawCount_ = 0U;
    unsigned int casterDrawCount_ = 0U;
    unsigned int receiverDrawCount_ = 0U;
    std::string lastError_;
    std::vector<DrawPlan> framePlans_;
    bool framePrepared_ = false;
    bool framePreparationFailed_ = false;
    MmdNativeCasterRenderOverride::FrameResources frameResources_;
    bool frameResourcesReady_ = false;
    bool shadowReady_ = false;
    unsigned int targetWidth_ = 0U;
    unsigned int targetHeight_ = 0U;
    bool targetHandleReady_ = false;
    std::unordered_set<MShaderInstance*> receiverShaders_;
    std::vector<std::size_t> casterMaterialIndices_;
    std::vector<mmd::MmdRenderQueueEntry> pmxOrder_;
    std::vector<bool> outlineOrder_;
};

class MmdOrderedRenderOverride::OpaqueRenderOperation
    : public MHWRender::MUserRenderOperation {
public:
    explicit OpaqueRenderOperation(OrderedRenderOperation* owner)
        : MUserRenderOperation(MString(kOpaqueOperationName))
        , owner_(owner)
    {
    }

    MStatus execute(const MHWRender::MDrawContext& drawContext) override
    {
        return owner_ ? owner_->executePass(drawContext, true)
                      : MStatus::kFailure;
    }

    bool requiresResetDeviceStates() const override { return true; }

private:
    OrderedRenderOperation* owner_ = nullptr;
};

MmdOrderedRenderOverride::MmdOrderedRenderOverride(
    MmdNativeCasterRenderOverride* nativeCasterOwner)
    : MRenderOverride(overrideName())
    , nativeCasterOwner_(nativeCasterOwner)
{
    if (!nativeCasterOwner_) {
        privateNativeCasterOwner_.reset(new MmdNativeCasterRenderOverride());
        nativeCasterOwner_ = privateNativeCasterOwner_.get();
    }
    gOrderedOverride = this;
}

MmdOrderedRenderOverride::~MmdOrderedRenderOverride()
{
    MHWRender::MRenderOperation* detached = nullptr;
    if (operationsInstalled_) {
        detached = mOperations.take(MString(kOperationName));
    }
    mOperations.clear();
    if (detached) {
        delete detached;
    } else if (!operationsInstalled_) {
        delete operation_;
    }
    operation_ = nullptr;
    nativeCasterOwner_ = nullptr;
    if (gOrderedOverride == this) {
        gOrderedOverride = nullptr;
    }
}

MHWRender::DrawAPI MmdOrderedRenderOverride::supportedDrawAPIs() const
{
    return MHWRender::kDirectX11;
}

MString MmdOrderedRenderOverride::uiName() const
{
    return MString("MMD Ordered");
}

MStatus MmdOrderedRenderOverride::setup(const MString& destination)
{
    if (operationsInstalled_ && !fallbackRequested_) {
        if (operation_) {
            operation_->resetFrame();
        }
        return MRenderOverride::setup(destination);
    }

    if (operationsInstalled_ && fallbackRequested_) {
        MHWRender::MRenderOperation* detached =
            mOperations.take(MString(kOperationName));
        mOperations.clear();
        if (!detached) {
            operation_ = nullptr;
        }
        operationsInstalled_ = false;
    }

    if (fallbackRequested_) {
        MHWRender::MRenderer* fallbackRenderer =
            MHWRender::MRenderer::theRenderer(false);
        mOperations.clear();
        if (fallbackRenderer) {
            fallbackRenderer->getStandardViewportOperations(mOperations);
        }
        return MRenderOverride::setup(destination);
    }

    MHWRender::MRenderer* renderer = MHWRender::MRenderer::theRenderer(false);
    if (!renderer) {
        requestFallback("VP2 renderer is unavailable");
        return MRenderOverride::setup(destination);
    }

    mOperations.clear();
    renderer->getStandardViewportOperations(mOperations);

    std::vector<MDagPath> shapePaths;
    MSelectionList nonMmdSelection;
    std::string error;
    if (!buildPanelShapePaths(destination, shapePaths, nonMmdSelection, error)) {
        requestFallback(error);
        return MRenderOverride::setup(destination);
    }
    if (shapePaths.empty()) {
        if (operation_) {
            operation_->resetFrame();
        }
        clearFallback();
        return MRenderOverride::setup(destination);
    }

    std::vector<OrderedRenderOperation::ShapeRecord> records;
    records.reserve(shapePaths.size());
    for (const MDagPath& path : shapePaths) {
        records.push_back({path, MObjectHandle(path.node())});
    }
    std::unique_ptr<OrderedRenderOperation> newOperation;
    if (!operation_) {
        newOperation.reset(new OrderedRenderOperation(
            kOperationName,
            this,
            std::move(records),
            mmd::nativeMaterialShaderPath()));
        operation_ = newOperation.get();
    } else {
        operation_->setRecords(std::move(records));
    }
    OrderedRenderOperation* orderedOperation = operation_;
    std::unique_ptr<OpaqueRenderOperation> opaqueOperation(
        new OpaqueRenderOperation(orderedOperation));
    std::unique_ptr<OrderedSceneRender> transparent(new OrderedSceneRender(
        kNonMmdTransparentSceneName,
        MHWRender::MSceneRender::kRenderTransparentShadedItems,
        MHWRender::MClearOperation::kClearNone,
        &nonMmdSelection));
    std::unique_ptr<OrderedSceneRender> opaque(new OrderedSceneRender(
        kOpaqueSceneName,
        MHWRender::MSceneRender::kRenderOpaqueShadedItems,
        MHWRender::MClearOperation::kClearNone,
        &nonMmdSelection));
    std::unique_ptr<OrderedSceneRender> postSceneUI(new OrderedSceneRender(
        kPostSceneUIName,
        MHWRender::MSceneRender::kRenderPostSceneUIItems,
        MHWRender::MClearOperation::kClearNone));

    if (mOperations.indexOf(MHWRender::MRenderOperation::kStandardSceneName) <
            0 ||
        !mOperations.insertAfter(
            MHWRender::MRenderOperation::kStandardSceneName,
            transparent.get())) {
        mOperations.clear();
        renderer->getStandardViewportOperations(mOperations);
        requestFallback("could not insert non-MMD transparent scene");
        if (newOperation) {
            operation_ = nullptr;
        }
        return MRenderOverride::setup(destination);
    }
    transparent.release();
    if (!mOperations.replace(MHWRender::MRenderOperation::kStandardSceneName,
                             opaque.get())) {
        mOperations.clear();
        renderer->getStandardViewportOperations(mOperations);
        requestFallback("could not replace standard opaque scene");
        if (newOperation) {
            operation_ = nullptr;
        }
        return MRenderOverride::setup(destination);
    }
    opaque.release();
    if (!mOperations.insertAfter(kOpaqueSceneName, opaqueOperation.get())) {
        mOperations.clear();
        renderer->getStandardViewportOperations(mOperations);
        requestFallback("could not insert MMD ordered opaque operation");
        if (newOperation) {
            operation_ = nullptr;
        }
        return MRenderOverride::setup(destination);
    }
    opaqueOperation.release();
    std::unique_ptr<OrderedSceneRender> preSceneUI(new OrderedSceneRender(
        kPreSceneUIName,
        MHWRender::MSceneRender::kRenderPreSceneUIItems,
        MHWRender::MClearOperation::kClearDepth |
            MHWRender::MClearOperation::kClearStencil));
    if (!mOperations.insertBefore(kOpaqueSceneName, preSceneUI.get())) {
        mOperations.clear();
        renderer->getStandardViewportOperations(mOperations);
        requestFallback("could not insert pre-scene UI operation");
        if (newOperation) {
            operation_ = nullptr;
        }
        return MRenderOverride::setup(destination);
    }
    preSceneUI.release();
    if (!mOperations.insertAfter(kNonMmdTransparentSceneName,
                                 postSceneUI.get())) {
        mOperations.clear();
        renderer->getStandardViewportOperations(mOperations);
        requestFallback("could not insert post-scene UI operation");
        if (newOperation) {
            operation_ = nullptr;
        }
        return MRenderOverride::setup(destination);
    }
    postSceneUI.release();
    if (!mOperations.insertAfter(kNonMmdTransparentSceneName, orderedOperation)) {
        mOperations.clear();
        renderer->getStandardViewportOperations(mOperations);
        requestFallback("could not insert MMD ordered operation");
        if (newOperation) {
            operation_ = nullptr;
        }
        return MRenderOverride::setup(destination);
    }
    if (newOperation) {
        newOperation.release();
    }
    operationsInstalled_ = true;
    fallbackReason_.clear();
    return MRenderOverride::setup(destination);
}

MStatus MmdOrderedRenderOverride::cleanup()
{
    if (operationsInstalled_) {
        MHWRender::MRenderOperation* detached =
            mOperations.take(MString(kOperationName));
        mOperations.clear();
        if (detached != operation_) {
            operation_ = nullptr;
        }
    } else {
        mOperations.clear();
    }
    operationsInstalled_ = false;
    return MRenderOverride::cleanup();
}

const MString& MmdOrderedRenderOverride::overrideName()
{
    static const MString name(kOverrideName);
    return name;
}

void MmdOrderedRenderOverride::setPluginLoadPath(const MString& loadPath)
{
    mmd::setNativeMaterialPluginLoadPath(loadPath);
}

void MmdOrderedRenderOverride::markRegistered(bool registered)
{
    gRegistered = registered;
}

bool MmdOrderedRenderOverride::prepareForPluginUnload()
{
    if (operation_ && !operation_->prepareForPluginUnload()) {
        return false;
    }
    if (operation_) {
        operation_->resetFrame();
    }
    return true;
}

void MmdOrderedRenderOverride::requestFallback(const std::string& reason)
{
    fallbackRequested_ = true;
    fallbackReason_ = reason;
}

void MmdOrderedRenderOverride::clearFallback()
{
    fallbackRequested_ = false;
    fallbackReason_.clear();
}

std::string MmdOrderedRenderOverride::diagnosticsJson(bool captureShadowDepth)
{
    if (!gOrderedOverride) {
        return std::string("{\"override\":\"mmdOrdered\",\"registered\":") +
               (gRegistered ? "true" : "false") +
               ",\"state\":\"unavailable\"}";
    }
    if (gOrderedOverride->fallbackReason_.empty() &&
        gOrderedOverride->operation_) {
        return gOrderedOverride->operation_->diagnosticsJson(captureShadowDepth);
    }
    std::ostringstream result;
    result << "{\"override\":\"mmdOrdered\",\"registered\":"
           << (gRegistered ? "true" : "false")
           << ",\"state\":\"fallback\",\"drawCount\":0"
           << ",\"error\":\""
           << jsonEscape(gOrderedOverride->fallbackReason_)
           << "\",\"pmxOrder\":[]}";
    return result.str();
}

void* MmdOrderedRenderWitnessCommand::creator()
{
    return new MmdOrderedRenderWitnessCommand;
}

MSyntax MmdOrderedRenderWitnessCommand::newSyntax()
{
    MSyntax syntax;
    syntax.addFlag("-sd", "-shadowDepth");
    return syntax;
}

MStatus MmdOrderedRenderWitnessCommand::doIt(const MArgList& args)
{
    MStatus status;
    const MSyntax commandSyntax = newSyntax();
    MArgDatabase arguments(commandSyntax, args, &status);
    if (!status) {
        return status;
    }
    setResult(MString(MmdOrderedRenderOverride::diagnosticsJson(
        arguments.isFlagSet("-sd")).c_str()));
    return MS::kSuccess;
}

bool MmdOrderedRenderWitnessCommand::isUndoable() const
{
    return false;
}
