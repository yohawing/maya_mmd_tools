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
#include <maya/MRenderTargetManager.h>
#include <maya/MStringArray.h>

#include <algorithm>
#include <cmath>
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

struct CasterDiagnostics {
    bool registered = false;
    bool setup = false;
    bool selectionBuilt = false;
    std::size_t selectedCount = 0U;
    bool colorTargetAcquired = false;
    bool depthTargetAcquired = false;
    unsigned int colorWidth = 0U;
    unsigned int colorHeight = 0U;
    unsigned int depthWidth = 0U;
    unsigned int depthHeight = 0U;
    int colorFormat = -1;
    int depthFormat = -1;
    bool shaderAvailable = false;
    bool matrixBound = false;
    bool drawAttempted = false;
    bool frameComplete = false;
    bool operationInsertedBeforeScene = false;
    bool occupancySupported = false;
    bool occupied = false;
    std::size_t nonClearSamples = 0U;
    float nonClearMin = 0.0F;
    float nonClearMax = 0.0F;
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

MMatrix casterLightViewProjection()
{
    // Keep the spike deterministic and finite while covering the imported
    // model's local world-space extent.  This is intentionally a fixed
    // clip-space transform; a production caster will replace it with the
    // scene light's view/projection owner once receiver composition exists.
    const double values[4][4] = {
        {0.5, 0.0, 0.0, 0.0},
        {0.0, 0.5, 0.0, 0.0},
        {0.0, 0.0, 0.5, 0.0},
        {0.0, 0.0, 0.0, 1.0},
    };
    return MMatrix(values);
}

}  // namespace

class MmdNativeCasterRenderOverride::CasterSceneRender
    : public MHWRender::MSceneRender {
public:
    CasterSceneRender()
        : MHWRender::MSceneRender("mmdNativeCasterScene")
    {
        float clearColor[4] = {0.0F, 0.0F, 0.0F, 0.0F};
        clearOperation().setOverridesColors(true);
        clearOperation().setMask(MHWRender::MClearOperation::kClearAll);
        clearOperation().setClearColor(clearColor);
        clearOperation().setClearDepth(1.0F);
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
        const MStatus status = shader_->setParameter(
            MString("CasterLightViewProjection"), casterLightViewProjection());
        gDiagnostics.matrixBound = status == MS::kSuccess;
        if (!gDiagnostics.matrixBound) {
            gDiagnostics.error = "CasterLightViewProjection binding failed";
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
                if (std::isfinite(value) && std::abs(value) > 1.0e-5F) {
                    ++gDiagnostics.nonClearSamples;
                    gDiagnostics.occupied = true;
                    if (gDiagnostics.nonClearSamples == 1U) {
                        gDiagnostics.nonClearMin = value;
                        gDiagnostics.nonClearMax = value;
                    } else {
                        gDiagnostics.nonClearMin =
                            std::min(gDiagnostics.nonClearMin, value);
                        gDiagnostics.nonClearMax =
                            std::max(gDiagnostics.nonClearMax, value);
                    }
                }
            }
        }
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
    // pluginMain owns a static override instance, so cross-TU initialization
    // order could otherwise register an empty name before kOverrideName was
    // initialized.
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
        gDiagnostics.colorWidth = actualDescription.width();
        gDiagnostics.colorHeight = actualDescription.height();
        gDiagnostics.colorFormat =
            static_cast<int>(actualDescription.rasterFormat());
    }
    if (depthTarget_) {
        MHWRender::MRenderTargetDescription actualDescription;
        depthTarget_->targetDescription(actualDescription);
        gDiagnostics.depthWidth = actualDescription.width();
        gDiagnostics.depthHeight = actualDescription.height();
        gDiagnostics.depthFormat =
            static_cast<int>(actualDescription.rasterFormat());
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
    gDiagnostics.shaderAvailable = false;
    gDiagnostics.matrixBound = false;
    gDiagnostics.drawAttempted = false;
    gDiagnostics.frameComplete = false;
    gDiagnostics.operationInsertedBeforeScene = false;
    gDiagnostics.occupancySupported = false;
    gDiagnostics.occupied = false;
    gDiagnostics.nonClearSamples = 0U;
    gDiagnostics.nonClearMin = 0.0F;
    gDiagnostics.nonClearMax = 0.0F;
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
    stream << '{'
           << "\"version\":1"
           << ",\"registered\":" << jsonBool(gDiagnostics.registered)
           << ",\"setup\":" << jsonBool(gDiagnostics.setup)
           << ",\"selectionBuilt\":" << jsonBool(gDiagnostics.selectionBuilt)
           << ",\"selectedCount\":" << gDiagnostics.selectedCount
           << ",\"colorTargetAcquired\":"
           << jsonBool(gDiagnostics.colorTargetAcquired)
           << ",\"depthTargetAcquired\":"
           << jsonBool(gDiagnostics.depthTargetAcquired)
           << ",\"colorWidth\":" << gDiagnostics.colorWidth
           << ",\"colorHeight\":" << gDiagnostics.colorHeight
           << ",\"depthWidth\":" << gDiagnostics.depthWidth
           << ",\"depthHeight\":" << gDiagnostics.depthHeight
           << ",\"colorFormat\":" << gDiagnostics.colorFormat
           << ",\"depthFormat\":" << gDiagnostics.depthFormat
           << ",\"shaderAvailable\":"
           << jsonBool(gDiagnostics.shaderAvailable)
           << ",\"matrixBound\":" << jsonBool(gDiagnostics.matrixBound)
           << ",\"drawAttempted\":" << jsonBool(gDiagnostics.drawAttempted)
           << ",\"frameComplete\":" << jsonBool(gDiagnostics.frameComplete)
           << ",\"operationInsertedBeforeScene\":"
           << jsonBool(gDiagnostics.operationInsertedBeforeScene)
           << ",\"occupancySupported\":"
           << jsonBool(gDiagnostics.occupancySupported)
           << ",\"occupied\":" << jsonBool(gDiagnostics.occupied)
           << ",\"nonClearSamples\":" << gDiagnostics.nonClearSamples
           << ",\"nonClearMin\":" << gDiagnostics.nonClearMin
           << ",\"nonClearMax\":" << gDiagnostics.nonClearMax
           << ",\"released\":" << jsonBool(gDiagnostics.released)
           << ",\"error\":\"";
    for (const char character : gDiagnostics.error) {
        if (character == '\\' || character == '"') {
            stream << '\\';
        }
        stream << character;
    }
    stream << "\"}";
    return stream.str();
}

void* MmdNativeCasterWitnessCommand::creator()
{
    return new MmdNativeCasterWitnessCommand();
}

MSyntax MmdNativeCasterWitnessCommand::newSyntax()
{
    return MSyntax();
}

MStatus MmdNativeCasterWitnessCommand::doIt(const MArgList&)
{
    setResult(MString(MmdNativeCasterRenderOverride::diagnosticsJson().c_str()));
    return MS::kSuccess;
}

bool MmdNativeCasterWitnessCommand::isUndoable() const
{
    return false;
}
