/**
 * @file MmdRenderOverride.cpp
 * @brief Native caster render-override capability spike.
 */

#include "MmdRenderOverride.h"

#include "MmdRenderShape.h"

#include <maya/MArgDatabase.h>
#include <maya/MDagPath.h>
#include <maya/MFn.h>
#include <maya/MFnDependencyNode.h>
#include <maya/MGlobal.h>
#include <maya/MItDependencyNodes.h>
#include <maya/MPoint.h>
#include <maya/MRenderTargetManager.h>
#include <maya/MStringArray.h>

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <filesystem>
#include <iomanip>
#include <limits>
#include <sstream>
#include <string>

namespace {

const MString& casterColorTargetName()
{
    static const MString name("__mmdNativeCasterColorTarget__");
    return name;
}

const MString& casterDepthTargetName()
{
    static const MString name("__mmdNativeCasterDepthTarget__");
    return name;
}

std::filesystem::path gShaderPath;
float gDepthBias = MmdNativeCasterRenderOverride::kDefaultDepthBias;

constexpr float kClearDepth = 1.0F;
constexpr float kDepthEpsilon = 1.0e-6F;
constexpr float kMatrixEpsilon = 1.0e-6F;

struct TargetDiagnostics {
    unsigned int width = 0U;
    unsigned int height = 0U;
    unsigned int multiSampleCount = 0U;
    unsigned int arraySliceCount = 0U;
    int format = -1;
    bool isCubeMap = false;
    std::string name;
};

struct CasterDiagnostics {
    bool registered = false;
    bool setup = false;
    bool selectionBuilt = false;
    std::size_t selectedCount = 0U;
    bool colorTargetAcquired = false;
    bool depthTargetAcquired = false;
    TargetDiagnostics colorTarget;
    TargetDiagnostics depthTarget;
    bool shaderAvailable = false;
    bool matrixBound = false;
    bool matrixValidated = false;
    bool depthBiasBound = false;
    float depthBias = MmdNativeCasterRenderOverride::kDefaultDepthBias;
    bool drawAttempted = false;
    bool frameComplete = false;
    bool operationInsertedBeforeScene = false;
    bool occupancySupported = false;
    bool occupied = false;
    std::size_t clearSamples = 0U;
    std::size_t writtenSamples = 0U;
    std::size_t finiteSamples = 0U;
    std::size_t nonFiniteSamples = 0U;
    std::size_t outOfRangeSamples = 0U;
    std::size_t writtenOutOfRangeSamples = 0U;
    float writtenMin = 0.0F;
    float writtenMax = 0.0F;
    double writtenMean = 0.0;
    std::uint64_t writtenFootprintHash = 1469598103934665603ULL;
    bool writtenDepthFinite = false;
    bool writtenDepthInRange = false;
    bool released = false;
    std::string error;
};

CasterDiagnostics gDiagnostics;

std::filesystem::path findShaderPath(const MString& loadPath)
{
    try {
        std::filesystem::path directory =
            std::filesystem::u8path(loadPath.asUTF8()).parent_path();
        while (!directory.empty()) {
            const std::filesystem::path candidate =
                directory / "mmd_tools" / "shaders" / "MMDShader.fx";
            if (std::filesystem::is_regular_file(candidate)) {
                return std::filesystem::absolute(candidate).lexically_normal();
            }
            const std::filesystem::path parent = directory.parent_path();
            if (parent == directory) {
                break;
            }
            directory = parent;
        }
    } catch (const std::filesystem::filesystem_error&) {
    }
    return {};
}

std::string shaderPath()
{
    const char* configured = std::getenv("MMD_TOOLS_NATIVE_SHADER_PATH");
    if (configured && *configured) {
        return configured;
    }
    if (!gShaderPath.empty()) {
        return gShaderPath.u8string();
    }
    return "mmd_tools/shaders/MMDShader.fx";
}

std::string jsonBool(bool value)
{
    return value ? "true" : "false";
}

std::string jsonString(const std::string& value)
{
    std::ostringstream stream;
    stream << '"';
    for (const char character : value) {
        if (character == '\\' || character == '"') {
            stream << '\\';
        }
        stream << character;
    }
    stream << '"';
    return stream.str();
}

MMatrix casterLightViewProjection()
{
    // This is a row-vector, finite, non-reversed orthographic caster-space
    // transform.  MPoint * MMatrix and HLSL mul(rowVector, row_major matrix)
    // therefore use the same translation row and positive clip-Z direction.
    // The scale leaves room for the deterministic +0.35/+0.55 depth bias
    // control while covering the small imported fixtures used by this probe.
    const double values[4][4] = {
        {0.25, 0.0, 0.0, 0.0},
        {0.0, 0.25, 0.0, 0.0},
        {0.0, 0.0, 0.04, 0.0},
        {0.10, -0.10, 0.0, 1.0},
    };
    return MMatrix(values);
}

bool approximatelyEqual(double lhs, double rhs)
{
    return std::abs(lhs - rhs) <= kMatrixEpsilon;
}

bool validateCasterMatrix(const MMatrix& matrix)
{
    // Validate through Maya's row-vector point operator instead of only
    // checking entries.  A transposed matrix or a translation in the wrong
    // row consequently fails before any draw is attempted.
    const MPoint origin = MPoint(0.0, 0.0, 0.0, 1.0) * matrix;
    const MPoint xAxis = MPoint(1.0, 0.0, 0.0, 1.0) * matrix;
    const MPoint yAxis = MPoint(0.0, 1.0, 0.0, 1.0) * matrix;
    const MPoint zAxis = MPoint(0.0, 0.0, 1.0, 1.0) * matrix;
    const MPoint points[] = {origin, xAxis, yAxis, zAxis};
    for (const MPoint& point : points) {
        if (!std::isfinite(point.x) || !std::isfinite(point.y) ||
            !std::isfinite(point.z) || !std::isfinite(point.w)) {
            return false;
        }
    }
    return approximatelyEqual(origin.x, 0.10) &&
           approximatelyEqual(origin.y, -0.10) &&
           approximatelyEqual(origin.z, 0.0) &&
           approximatelyEqual(origin.w, 1.0) &&
           approximatelyEqual(xAxis.x - origin.x, 0.25) &&
           approximatelyEqual(xAxis.y - origin.y, 0.0) &&
           approximatelyEqual(xAxis.z - origin.z, 0.0) &&
           approximatelyEqual(yAxis.x - origin.x, 0.0) &&
           approximatelyEqual(yAxis.y - origin.y, 0.25) &&
           approximatelyEqual(yAxis.z - origin.z, 0.0) &&
           approximatelyEqual(zAxis.x - origin.x, 0.0) &&
           approximatelyEqual(zAxis.y - origin.y, 0.0) &&
           approximatelyEqual(zAxis.z - origin.z, 0.04) &&
           approximatelyEqual(zAxis.w - origin.w, 0.0);
}

void resetDepthDiagnostics()
{
    gDiagnostics.clearSamples = 0U;
    gDiagnostics.writtenSamples = 0U;
    gDiagnostics.finiteSamples = 0U;
    gDiagnostics.nonFiniteSamples = 0U;
    gDiagnostics.outOfRangeSamples = 0U;
    gDiagnostics.writtenOutOfRangeSamples = 0U;
    gDiagnostics.writtenMin = 0.0F;
    gDiagnostics.writtenMax = 0.0F;
    gDiagnostics.writtenMean = 0.0;
    gDiagnostics.writtenFootprintHash = 1469598103934665603ULL;
    gDiagnostics.writtenDepthFinite = false;
    gDiagnostics.writtenDepthInRange = false;
}

void hashFootprint(std::uint64_t& hash, unsigned int x, unsigned int y)
{
    // FNV-1a over the integer pixel coordinate.  A/B depth bias must preserve
    // this footprint because only clip-Z changes.
    const std::uint32_t coordinate[] = {x, y};
    for (const std::uint32_t component : coordinate) {
        for (unsigned int byte = 0U; byte < sizeof(component); ++byte) {
            hash ^= static_cast<std::uint8_t>(component >> (byte * 8U));
            hash *= 1099511628211ULL;
        }
    }
}

}  // namespace

class MmdNativeCasterRenderOverride::CasterSceneRender
    : public MHWRender::MSceneRender {
public:
    CasterSceneRender()
        : MHWRender::MSceneRender("mmdNativeCasterScene")
    {
        // The R32F color target carries the rasterized clip depth.  Use the
        // same 1.0 sentinel as D32 and strict LESS so clear and written
        // samples remain distinguishable in CPU readback.
        float clearColor[4] = {kClearDepth, kClearDepth, kClearDepth,
                               kClearDepth};
        clearOperation().setOverridesColors(true);
        clearOperation().setMask(MHWRender::MClearOperation::kClearAll);
        clearOperation().setClearColor(clearColor);
        clearOperation().setClearDepth(kClearDepth);
    }

    void setSelection(const MSelectionList& selection)
    {
        selection_ = selection;
    }

    void setTargets(MHWRender::MRenderTarget* color,
                    MHWRender::MRenderTarget* depth)
    {
        targets_[0] = color;
        targets_[1] = depth;
    }

    void setShader(MHWRender::MShaderInstance* shader)
    {
        shader_ = shader;
    }

    const MHWRender::MShaderInstance* shaderOverride() override
    {
        return shader_;
    }

    const MSelectionList* objectSetOverride() override
    {
        return &selection_;
    }

    MHWRender::MSceneRender::MSceneFilterOption renderFilterOverride() override
    {
        return MHWRender::MSceneRender::kRenderShadedItems;
    }

    MHWRender::MRenderTarget* const* targetOverrideList(
        unsigned int& listSize) override
    {
        listSize = 2U;
        return targets_;
    }

    int writableTargets(unsigned int& count) override
    {
        count = 2U;
        return 0;
    }

    bool getInputTargetDescription(
        const MString& name,
        MHWRender::MRenderTargetDescription& description) override
    {
        if (name == casterColorTargetName()) {
            description = MHWRender::MRenderTargetDescription(
                name, MmdNativeCasterRenderOverride::kTargetSize,
                MmdNativeCasterRenderOverride::kTargetSize, 1U,
                MHWRender::kR32_FLOAT, 1U, false);
            return true;
        }
        if (name == casterDepthTargetName()) {
            description = MHWRender::MRenderTargetDescription(
                name, MmdNativeCasterRenderOverride::kTargetSize,
                MmdNativeCasterRenderOverride::kTargetSize, 1U,
                MHWRender::kD32_FLOAT, 1U, false);
            return true;
        }
        return false;
    }

    void preSceneRender(const MHWRender::MDrawContext&) override
    {
        gDiagnostics.drawAttempted = true;
        if (!shader_) {
            return;
        }
        const MMatrix matrix = casterLightViewProjection();
        gDiagnostics.matrixValidated = validateCasterMatrix(matrix);
        if (!gDiagnostics.matrixValidated) {
            gDiagnostics.error =
                "row-vector caster matrix validation failed";
            return;
        }
        const MStatus status = shader_->setParameter(
            MString("CasterLightViewProjection"), matrix);
        gDiagnostics.matrixBound = status == MS::kSuccess;
        if (!gDiagnostics.matrixBound) {
            gDiagnostics.error = "CasterLightViewProjection binding failed";
            return;
        }
        const MStatus biasStatus = shader_->setParameter(
            MString("CasterDepthBias"), gDepthBias);
        gDiagnostics.depthBiasBound = biasStatus == MS::kSuccess;
        if (!gDiagnostics.depthBiasBound) {
            gDiagnostics.error = "CasterDepthBias binding failed";
        }
    }

    void postSceneRender(const MHWRender::MDrawContext&) override
    {
        if (!targets_[0]) {
            return;
        }
        int rowPitch = 0;
        std::size_t slicePitch = 0U;
        void* raw = targets_[0]->rawData(rowPitch, slicePitch);
        const std::size_t minimumRowPitch =
            static_cast<std::size_t>(MmdNativeCasterRenderOverride::kTargetSize) *
            sizeof(float);
        const std::size_t minimumSlicePitch =
            static_cast<std::size_t>(rowPitch) *
            MmdNativeCasterRenderOverride::kTargetSize;
        if (!raw || rowPitch <= 0 ||
            static_cast<std::size_t>(rowPitch) < minimumRowPitch ||
            slicePitch < minimumSlicePitch) {
            if (raw) {
                MHWRender::MRenderTarget::freeRawData(raw);
            }
            return;
        }
        gDiagnostics.occupancySupported = true;
        const unsigned int width = MmdNativeCasterRenderOverride::kTargetSize;
        const unsigned int height = MmdNativeCasterRenderOverride::kTargetSize;
        const unsigned char* bytes = static_cast<const unsigned char*>(raw);
        for (unsigned int y = 0U; y < height; ++y) {
            const float* row = reinterpret_cast<const float*>(
                bytes + static_cast<std::size_t>(y) * rowPitch);
            for (unsigned int x = 0U; x < width; ++x) {
                const float value = row[x];
                if (!std::isfinite(value)) {
                    ++gDiagnostics.nonFiniteSamples;
                    continue;
                }
                ++gDiagnostics.finiteSamples;
                if (value < 0.0F || value > kClearDepth) {
                    ++gDiagnostics.outOfRangeSamples;
                }
                if (std::abs(value - kClearDepth) <= kDepthEpsilon) {
                    ++gDiagnostics.clearSamples;
                    continue;
                }
                ++gDiagnostics.writtenSamples;
                gDiagnostics.occupied = true;
                hashFootprint(gDiagnostics.writtenFootprintHash, x, y);
                if (gDiagnostics.writtenSamples == 1U) {
                    gDiagnostics.writtenMin = value;
                    gDiagnostics.writtenMax = value;
                } else {
                    gDiagnostics.writtenMin =
                        std::min(gDiagnostics.writtenMin, value);
                    gDiagnostics.writtenMax =
                        std::max(gDiagnostics.writtenMax, value);
                }
                gDiagnostics.writtenMean += value;
                if (value < 0.0F || value > kClearDepth) {
                    ++gDiagnostics.writtenOutOfRangeSamples;
                }
            }
        }
        if (gDiagnostics.writtenSamples > 0U) {
            gDiagnostics.writtenMean /=
                static_cast<double>(gDiagnostics.writtenSamples);
        }
        gDiagnostics.writtenDepthFinite =
            gDiagnostics.nonFiniteSamples == 0U;
        gDiagnostics.writtenDepthInRange =
            gDiagnostics.writtenSamples > 0U &&
            gDiagnostics.writtenOutOfRangeSamples == 0U &&
            gDiagnostics.writtenMin >= 0.0F &&
            gDiagnostics.writtenMax <= kClearDepth;
        MHWRender::MRenderTarget::freeRawData(raw);
        gDiagnostics.frameComplete = true;
    }

private:
    MSelectionList selection_;
    MHWRender::MRenderTarget* targets_[2] = {nullptr, nullptr};
    MHWRender::MShaderInstance* shader_ = nullptr;
};

MmdNativeCasterRenderOverride::MmdNativeCasterRenderOverride()
    // Use a constructor-local literal rather than a namespace-static MString:
    // pluginMain creates the override during initializePlugin, so cross-TU
    // initialization order could otherwise register an empty name.
    : MHWRender::MRenderOverride(MString("mmdNativeCaster"))
{
    // The UI label is provided by uiName() so modelEditor -rom can list this
    // override for a live viewport panel.
}

MmdNativeCasterRenderOverride::~MmdNativeCasterRenderOverride()
{
    mOperations.clear();
    releaseShader();
    releaseTargets();
}

MHWRender::DrawAPI MmdNativeCasterRenderOverride::supportedDrawAPIs() const
{
    return MHWRender::kDirectX11;
}

bool MmdNativeCasterRenderOverride::buildCasterSelection(
    MSelectionList& selection) const
{
    unsigned int count = 0U;
    MItDependencyNodes iterator(MFn::kDependencyNode);
    for (; !iterator.isDone(); iterator.next()) {
        const MObject object = iterator.item();
        MFnDependencyNode node(object);
        if (node.typeId() != MmdRenderShape::id) {
            continue;
        }
        MDagPath path;
        if (MDagPath::getAPathTo(object, path) == MS::kSuccess &&
            selection.add(path) == MS::kSuccess) {
            ++count;
        }
    }
    return count > 0U;
}

bool MmdNativeCasterRenderOverride::acquireTargets()
{
    MHWRender::MRenderer* renderer = MHWRender::MRenderer::theRenderer();
    targetManager_ = renderer ? const_cast<MHWRender::MRenderTargetManager*>(
                                  renderer->getRenderTargetManager())
                              : nullptr;
    if (!targetManager_) {
        gDiagnostics.error = "render target manager unavailable";
        return false;
    }
    const MHWRender::MRenderTargetDescription colorDescription(
        casterColorTargetName(), kTargetSize, kTargetSize,
        1U, MHWRender::kR32_FLOAT, 1U, false);
    const MHWRender::MRenderTargetDescription depthDescription(
        casterDepthTargetName(), kTargetSize, kTargetSize,
        1U, MHWRender::kD32_FLOAT, 1U, false);
    colorTarget_ = targetManager_->acquireRenderTarget(colorDescription);
    depthTarget_ = targetManager_->acquireRenderTarget(depthDescription);
    gDiagnostics.colorTargetAcquired = colorTarget_ != nullptr;
    gDiagnostics.depthTargetAcquired = depthTarget_ != nullptr;
    if (colorTarget_) {
        MHWRender::MRenderTargetDescription actualDescription;
        colorTarget_->targetDescription(actualDescription);
        gDiagnostics.colorTarget.width = actualDescription.width();
        gDiagnostics.colorTarget.height = actualDescription.height();
        gDiagnostics.colorTarget.multiSampleCount =
            actualDescription.multiSampleCount();
        gDiagnostics.colorTarget.arraySliceCount =
            actualDescription.arraySliceCount();
        gDiagnostics.colorTarget.format =
            static_cast<int>(actualDescription.rasterFormat());
        gDiagnostics.colorTarget.isCubeMap = actualDescription.isCubeMap();
        gDiagnostics.colorTarget.name = actualDescription.name().asUTF8();
    }
    if (depthTarget_) {
        MHWRender::MRenderTargetDescription actualDescription;
        depthTarget_->targetDescription(actualDescription);
        gDiagnostics.depthTarget.width = actualDescription.width();
        gDiagnostics.depthTarget.height = actualDescription.height();
        gDiagnostics.depthTarget.multiSampleCount =
            actualDescription.multiSampleCount();
        gDiagnostics.depthTarget.arraySliceCount =
            actualDescription.arraySliceCount();
        gDiagnostics.depthTarget.format =
            static_cast<int>(actualDescription.rasterFormat());
        gDiagnostics.depthTarget.isCubeMap = actualDescription.isCubeMap();
        gDiagnostics.depthTarget.name = actualDescription.name().asUTF8();
    }
    if (!colorTarget_ || !depthTarget_) {
        gDiagnostics.error = "caster render target acquisition failed";
        releaseTargets();
        return false;
    }
    return true;
}

void MmdNativeCasterRenderOverride::releaseTargets()
{
    if (targetManager_) {
        if (colorTarget_) {
            targetManager_->releaseRenderTarget(colorTarget_);
        }
        if (depthTarget_) {
            targetManager_->releaseRenderTarget(depthTarget_);
        }
    }
    colorTarget_ = nullptr;
    depthTarget_ = nullptr;
    targetManager_ = nullptr;
    gDiagnostics.released = true;
}

void MmdNativeCasterRenderOverride::releaseShader()
{
    if (shaderManager_ && shader_) {
        shaderManager_->releaseShader(shader_);
    }
    shader_ = nullptr;
    shaderManager_ = nullptr;
}

MStatus MmdNativeCasterRenderOverride::setup(const MString& destination)
{
    (void)destination;
    gDiagnostics.setup = false;
    gDiagnostics.released = false;
    gDiagnostics.error.clear();
    gDiagnostics.selectionBuilt = false;
    gDiagnostics.selectedCount = 0U;
    gDiagnostics.colorTarget = TargetDiagnostics();
    gDiagnostics.depthTarget = TargetDiagnostics();
    gDiagnostics.shaderAvailable = false;
    gDiagnostics.matrixBound = false;
    gDiagnostics.matrixValidated = false;
    gDiagnostics.depthBiasBound = false;
    gDiagnostics.depthBias = gDepthBias;
    gDiagnostics.drawAttempted = false;
    gDiagnostics.frameComplete = false;
    gDiagnostics.operationInsertedBeforeScene = false;
    gDiagnostics.occupancySupported = false;
    gDiagnostics.occupied = false;
    resetDepthDiagnostics();
    mOperations.clear();
    releaseShader();
    releaseTargets();
    gDiagnostics.released = false;

    MSelectionList selection;
    gDiagnostics.selectionBuilt = buildCasterSelection(selection);
    gDiagnostics.selectedCount = static_cast<std::size_t>(selection.length());
    if (!gDiagnostics.selectionBuilt) {
        gDiagnostics.error = "no mmdRenderShape caster selected";
    }
    if (!acquireTargets()) {
        return MS::kFailure;
    }

    MHWRender::MRenderer* renderer = MHWRender::MRenderer::theRenderer();
    const MHWRender::MShaderManager* shaderManager =
        renderer ? renderer->getShaderManager() : nullptr;
    if (!shaderManager) {
        gDiagnostics.error = "shader manager unavailable";
        releaseTargets();
        return MS::kFailure;
    }
    MHWRender::MShaderInstance* shader =
        shaderManager->getEffectsFileShader(
            MString(shaderPath().c_str()), MString("MMDNativeCaster"));
    gDiagnostics.shaderAvailable = shader != nullptr;
    if (!shader) {
        gDiagnostics.error = "MMDNativeCaster shader unavailable";
        releaseTargets();
        return MS::kFailure;
    }
    shaderManager_ = shaderManager;
    shader_ = shader;

    casterOperation_ = new CasterSceneRender();
    casterOperation_->setSelection(selection);
    casterOperation_->setTargets(colorTarget_, depthTarget_);
    casterOperation_->setShader(shader);
    renderer->getStandardViewportOperations(mOperations);
    gDiagnostics.operationInsertedBeforeScene = mOperations.insertBefore(
        MHWRender::MRenderOperation::kStandardSceneName, casterOperation_);
    if (!gDiagnostics.operationInsertedBeforeScene) {
        delete casterOperation_;
        casterOperation_ = nullptr;
        gDiagnostics.error = "failed to insert caster before standard scene";
        releaseShader();
        releaseTargets();
        return MS::kFailure;
    }
    gDiagnostics.setup = true;
    return MS::kSuccess;
}

MStatus MmdNativeCasterRenderOverride::cleanup()
{
    mOperations.clear();
    casterOperation_ = nullptr;
    releaseShader();
    releaseTargets();
    gDiagnostics.setup = false;
    return MS::kSuccess;
}

void MmdNativeCasterRenderOverride::setPluginLoadPath(const MString& loadPath)
{
    gShaderPath = findShaderPath(loadPath);
}

void MmdNativeCasterRenderOverride::markRegistered(bool registered)
{
    gDiagnostics.registered = registered;
}

const MString& MmdNativeCasterRenderOverride::overrideName()
{
    static const MString name("mmdNativeCaster");
    return name;
}

std::string MmdNativeCasterRenderOverride::diagnosticsJson()
{
    std::ostringstream stream;
    stream << std::setprecision(9) << '{'
           << "\"version\":1"
           << ",\"registered\":" << jsonBool(gDiagnostics.registered)
           << ",\"setup\":" << jsonBool(gDiagnostics.setup)
           << ",\"selectionBuilt\":" << jsonBool(gDiagnostics.selectionBuilt)
           << ",\"selectedCount\":" << gDiagnostics.selectedCount
           << ",\"colorTargetAcquired\":"
           << jsonBool(gDiagnostics.colorTargetAcquired)
           << ",\"depthTargetAcquired\":"
           << jsonBool(gDiagnostics.depthTargetAcquired)
           << ",\"colorTarget\":{\"name\":"
           << jsonString(gDiagnostics.colorTarget.name)
           << ",\"width\":" << gDiagnostics.colorTarget.width
           << ",\"height\":" << gDiagnostics.colorTarget.height
           << ",\"multiSampleCount\":"
           << gDiagnostics.colorTarget.multiSampleCount
           << ",\"arraySliceCount\":"
           << gDiagnostics.colorTarget.arraySliceCount
           << ",\"format\":" << gDiagnostics.colorTarget.format
           << ",\"isCubeMap\":"
           << jsonBool(gDiagnostics.colorTarget.isCubeMap) << '}'
           << ",\"depthTarget\":{\"name\":"
           << jsonString(gDiagnostics.depthTarget.name)
           << ",\"width\":" << gDiagnostics.depthTarget.width
           << ",\"height\":" << gDiagnostics.depthTarget.height
           << ",\"multiSampleCount\":"
           << gDiagnostics.depthTarget.multiSampleCount
           << ",\"arraySliceCount\":"
           << gDiagnostics.depthTarget.arraySliceCount
           << ",\"format\":" << gDiagnostics.depthTarget.format
           << ",\"isCubeMap\":"
           << jsonBool(gDiagnostics.depthTarget.isCubeMap) << '}'
           // Keep the version-1 flat target fields for existing consumers;
           // the nested descriptions above are the authoritative full shape.
           << ",\"colorWidth\":" << gDiagnostics.colorTarget.width
           << ",\"colorHeight\":" << gDiagnostics.colorTarget.height
           << ",\"depthWidth\":" << gDiagnostics.depthTarget.width
           << ",\"depthHeight\":" << gDiagnostics.depthTarget.height
           << ",\"colorFormat\":" << gDiagnostics.colorTarget.format
           << ",\"depthFormat\":" << gDiagnostics.depthTarget.format
           << ",\"shaderAvailable\":"
           << jsonBool(gDiagnostics.shaderAvailable)
           << ",\"matrixBound\":" << jsonBool(gDiagnostics.matrixBound)
           << ",\"matrixValidated\":"
           << jsonBool(gDiagnostics.matrixValidated)
           << ",\"depthBiasBound\":"
           << jsonBool(gDiagnostics.depthBiasBound)
           << ",\"depthBias\":" << gDiagnostics.depthBias
           << ",\"drawAttempted\":" << jsonBool(gDiagnostics.drawAttempted)
           << ",\"frameComplete\":" << jsonBool(gDiagnostics.frameComplete)
           << ",\"operationInsertedBeforeScene\":"
           << jsonBool(gDiagnostics.operationInsertedBeforeScene)
           << ",\"occupancySupported\":"
           << jsonBool(gDiagnostics.occupancySupported)
           << ",\"occupied\":" << jsonBool(gDiagnostics.occupied)
           << ",\"clearValue\":" << kClearDepth
           << ",\"clearSamples\":" << gDiagnostics.clearSamples
           << ",\"writtenSamples\":" << gDiagnostics.writtenSamples
           << ",\"writtenMin\":" << gDiagnostics.writtenMin
           << ",\"writtenMax\":" << gDiagnostics.writtenMax
           << ",\"writtenMean\":" << gDiagnostics.writtenMean
           << ",\"finiteSamples\":" << gDiagnostics.finiteSamples
           << ",\"nonFiniteSamples\":" << gDiagnostics.nonFiniteSamples
           << ",\"outOfRangeSamples\":" << gDiagnostics.outOfRangeSamples
           << ",\"writtenOutOfRangeSamples\":"
           << gDiagnostics.writtenOutOfRangeSamples
           << ",\"writtenFootprintHash\":\"0x" << std::hex
           << gDiagnostics.writtenFootprintHash << std::dec << "\""
           << ",\"writtenDepthFinite\":"
           << jsonBool(gDiagnostics.writtenDepthFinite)
           << ",\"writtenDepthInRange\":"
           << jsonBool(gDiagnostics.writtenDepthInRange)
           // Keep the old occupancy keys as compatibility aliases for local
           // diagnostics that predate the real-depth witness.
           << ",\"nonClearSamples\":" << gDiagnostics.writtenSamples
           << ",\"nonClearMin\":" << gDiagnostics.writtenMin
           << ",\"nonClearMax\":" << gDiagnostics.writtenMax
           << ",\"released\":" << jsonBool(gDiagnostics.released)
           << ",\"error\":" << jsonString(gDiagnostics.error) << '}';
    return stream.str();
}

void* MmdNativeCasterWitnessCommand::creator()
{
    return new MmdNativeCasterWitnessCommand();
}

MSyntax MmdNativeCasterWitnessCommand::newSyntax()
{
    MSyntax syntax;
    syntax.addFlag("-db", "-depthBias", MSyntax::kDouble);
    return syntax;
}

MStatus MmdNativeCasterWitnessCommand::doIt(const MArgList& args)
{
    MStatus status;
    MArgDatabase argumentData(newSyntax(), args, &status);
    if (!status) {
        return status;
    }
    if (argumentData.isFlagSet("-depthBias")) {
        double requestedBias = 0.0;
        status = argumentData.getFlagArgument("-depthBias", 0, requestedBias);
        if (!status || !std::isfinite(requestedBias) || requestedBias < 0.0 ||
            requestedBias > 1.0) {
            MGlobal::displayError(
                "mmdNativeCasterWitness depthBias must be finite in [0, 1]");
            return MS::kFailure;
        }
        gDepthBias = static_cast<float>(requestedBias);
    }
    setResult(MString(MmdNativeCasterRenderOverride::diagnosticsJson().c_str()));
    return MS::kSuccess;
}

bool MmdNativeCasterWitnessCommand::isUndoable() const
{
    return false;
}
