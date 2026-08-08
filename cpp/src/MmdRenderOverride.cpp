/**
 * @file MmdRenderOverride.cpp
 * @brief Native caster render-override capability spike.
 */

#include "MmdRenderOverride.h"

#include "MmdRenderShape.h"

#include <maya/MArgDatabase.h>
#include <maya/MBoundingBox.h>
#include <maya/MDagPath.h>
#include <maya/MFn.h>
#include <maya/MFnDagNode.h>
#include <maya/MFnDependencyNode.h>
#include <maya/MGlobal.h>
#include <maya/MHWGeometry.h>
#include <maya/MItDependencyNodes.h>
#include <maya/MPoint.h>
#include <maya/MPlug.h>
#include <maya/MRenderTargetManager.h>
#include <maya/MStringArray.h>
#include <maya/MVector.h>

#include <algorithm>
#include <array>
#include <cmath>
#include <cstring>
#include <cstdint>
#include <cstdlib>
#include <filesystem>
#include <iomanip>
#include <limits>
#include <condition_variable>
#include <mutex>
#include <sstream>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <vector>

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
float gHardShadowBias =
    MmdNativeCasterRenderOverride::kDefaultHardShadowBias;
bool gReceiverProbe = false;
bool gHardShadowRequested = false;
bool gHardShadowEffective = false;
std::mutex gReceiverMutex;
std::condition_variable gReceiverCv;
bool gOverrideSetup = false;
std::unordered_set<MHWRender::MShaderInstance*> gReceiverShaders;
std::unordered_map<MHWRender::MShaderInstance*, MHWRender::MRenderTarget*>
    gReceiverBindings;
std::unordered_set<MHWRender::MShaderInstance*> gRetiringReceiverShaders;
std::unordered_map<MHWRender::MShaderInstance*, std::size_t> gReceiverPins;

constexpr float kClearDepth = 1.0F;
constexpr float kDepthEpsilon = 1.0e-6F;
constexpr float kMatrixEpsilon = 1.0e-6F;
constexpr double kClipGuard = 0.02;
constexpr double kDepthBiasReserve = 0.60;
constexpr double kBoundsMargin = 0.05;

MMatrix identityMatrix()
{
    const double values[4][4] = {
        {1.0, 0.0, 0.0, 0.0},
        {0.0, 1.0, 0.0, 0.0},
        {0.0, 0.0, 1.0, 0.0},
        {0.0, 0.0, 0.0, 1.0},
    };
    return MMatrix(values);
}

MMatrix gCasterMatrix = identityMatrix();
bool gCasterMatrixValid = false;

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
    std::string matrixSource;
    std::string lightPath;
    std::array<double, 3> lightDirection = {0.0, 0.0, 0.0};
    std::array<double, 6> worldBounds = {0.0, 0.0, 0.0, 0.0, 0.0, 0.0};
    std::array<double, 6> lightBounds = {0.0, 0.0, 0.0, 0.0, 0.0, 0.0};
    std::string matrixHash;
    std::string casterMatrixHash;
    std::string receiverMatrixHash;
    bool cornersInClip = false;
    bool depthBiasBound = false;
    float depthBias = MmdNativeCasterRenderOverride::kDefaultDepthBias;
    bool drawAttempted = false;
    std::size_t drawCallbackCount = 0U;
    std::vector<std::string> drawnRenderItems;
    std::vector<std::string> drawnRenderItemDagPaths;
    std::vector<std::string> drawnRenderItemTypes;
    std::vector<bool> drawnRenderItemCastsShadows;
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
    std::size_t receiverShaderRegistered = 0U;
    std::size_t receiverAssignmentSuccess = 0U;
    std::size_t receiverAssignmentFailure = 0U;
    std::size_t receiverLiveAssignmentOwners = 0U;
    bool receiverProbeEnabled = false;
    bool receiverTargetResourceHandleNonNull = false;
    bool receiverTargetSameFrame = false;
    bool receiverTargetsRetained = false;
    bool hardShadowRequested = false;
    bool hardShadowEffective = false;
    bool hardShadowFrameEffective = false;
    float hardShadowBias =
        MmdNativeCasterRenderOverride::kDefaultHardShadowBias;
    bool hardShadowBound = false;
    bool hardShadowBiasBound = false;
    bool failClosedHardShadowDisableAttempted = false;
    bool failClosedHardShadowDisableSuccess = false;
    std::size_t failClosedHardShadowDisableFailureCount = 0U;
    bool failClosedProbeDisableAttempted = false;
    bool failClosedProbeDisableSuccess = false;
    std::size_t failClosedProbeDisableFailureCount = 0U;
    bool released = false;
    std::string error;
};

CasterDiagnostics gDiagnostics;

void casterDrawCallback(MHWRender::MDrawContext&,
                        const MHWRender::MRenderItemList& renderItems,
                        MHWRender::MShaderInstance*)
{
    ++gDiagnostics.drawCallbackCount;
    for (int index = 0; index < renderItems.length(); ++index) {
        const MHWRender::MRenderItem* item = renderItems.itemAt(index);
        if (!item) {
            continue;
        }
        gDiagnostics.drawnRenderItems.emplace_back(item->name().asChar());
        gDiagnostics.drawnRenderItemDagPaths.emplace_back(
            item->sourceDagPath().fullPathName().asChar());
        gDiagnostics.drawnRenderItemTypes.emplace_back(
            item->type() == MHWRender::MRenderItem::MaterialSceneItem
                ? "MaterialSceneItem"
                : "NonMaterialSceneItem");
        gDiagnostics.drawnRenderItemCastsShadows.push_back(
            item->castsShadows());
    }
}

void releaseReceiverPin(MHWRender::MShaderInstance* shader)
{
    if (!shader) {
        return;
    }
    std::lock_guard<std::mutex> lock(gReceiverMutex);
    const auto pin = gReceiverPins.find(shader);
    if (pin != gReceiverPins.end()) {
        if (pin->second > 1U) {
            --pin->second;
        } else {
            gReceiverPins.erase(pin);
        }
    }
    gReceiverCv.notify_all();
}

struct ReceiverParameterWriteResult {
    bool attempted = false;
    std::size_t failures = 0U;
};

ReceiverParameterWriteResult writeReceiverIntParameter(
    const char* parameter,
    int value)
{
    std::vector<MHWRender::MShaderInstance*> shaders;
    {
        std::lock_guard<std::mutex> lock(gReceiverMutex);
        shaders.reserve(gReceiverShaders.size());
        for (MHWRender::MShaderInstance* shader : gReceiverShaders) {
            shaders.push_back(shader);
            ++gReceiverPins[shader];
        }
    }
    ReceiverParameterWriteResult result;
    result.attempted = !shaders.empty();
    for (MHWRender::MShaderInstance* shader : shaders) {
        const MStatus status = shader
                                   ? shader->setParameter(MString(parameter), value)
                                   : MS::kFailure;
        if (status != MS::kSuccess) {
            ++result.failures;
        }
        releaseReceiverPin(shader);
    }
    return result;
}

// Disable only the hard-shadow mask on every currently live receiver shader.
// The registry is pinned while setParameter executes so a scene reset or
// plug-in unload cannot release the borrowed shader between lookup and the
// fail-closed write.  No null target assignment is attempted; the persistent
// target remains owned by the override until the shader retires.
void disableHardShadowForFailClosed()
{
    {
        std::lock_guard<std::mutex> lock(gReceiverMutex);
        gHardShadowEffective = false;
        gDiagnostics.hardShadowEffective = false;
        gDiagnostics.failClosedHardShadowDisableAttempted = false;
        gDiagnostics.failClosedHardShadowDisableSuccess = false;
        gDiagnostics.failClosedHardShadowDisableFailureCount = 0U;
    }
    const ReceiverParameterWriteResult result =
        writeReceiverIntParameter("NativeCasterHardShadow", 0);
    gDiagnostics.failClosedHardShadowDisableAttempted = result.attempted;
    gDiagnostics.failClosedHardShadowDisableFailureCount = result.failures;
    gDiagnostics.failClosedHardShadowDisableSuccess =
        gDiagnostics.failClosedHardShadowDisableFailureCount == 0U;
    if (!gDiagnostics.failClosedHardShadowDisableSuccess) {
        if (!gDiagnostics.error.empty()) {
            gDiagnostics.error += "; ";
        }
        gDiagnostics.error += "fail-closed hard shadow disable failed";
    }
}

void disableReceiverProbeForFailClosed()
{
    // Missing-light, renderer-loss, and cleanup boundaries disable both
    // diagnostic modes.  Hard-shadow state has a separate requested/effective
    // pair, so turning it off here never discards the user's request.
    disableHardShadowForFailClosed();
    {
        std::lock_guard<std::mutex> lock(gReceiverMutex);
        gReceiverProbe = false;
        gDiagnostics.receiverProbeEnabled = false;
        gDiagnostics.failClosedProbeDisableAttempted = false;
        gDiagnostics.failClosedProbeDisableSuccess = false;
        gDiagnostics.failClosedProbeDisableFailureCount = 0U;
    }
    const ReceiverParameterWriteResult result =
        writeReceiverIntParameter("NativeCasterProbe", 0);
    gDiagnostics.failClosedProbeDisableAttempted = result.attempted;
    gDiagnostics.failClosedProbeDisableFailureCount = result.failures;
    gDiagnostics.failClosedProbeDisableSuccess =
        gDiagnostics.failClosedProbeDisableFailureCount == 0U;
    if (!gDiagnostics.failClosedProbeDisableSuccess) {
        if (!gDiagnostics.error.empty()) {
            gDiagnostics.error += "; ";
        }
        gDiagnostics.error += "fail-closed receiver probe disable failed";
    }
}

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

MVector crossProduct(const MVector& lhs, const MVector& rhs)
{
    return MVector(lhs.y * rhs.z - lhs.z * rhs.y,
                   lhs.z * rhs.x - lhs.x * rhs.z,
                   lhs.x * rhs.y - lhs.y * rhs.x);
}

bool normalizeVector(MVector& value)
{
    const double length = value.length();
    if (!std::isfinite(length) || length <= kMatrixEpsilon) {
        return false;
    }
    value /= length;
    return std::isfinite(value.x) && std::isfinite(value.y) &&
           std::isfinite(value.z);
}

bool finitePoint(const MPoint& point)
{
    return std::isfinite(point.x) && std::isfinite(point.y) &&
           std::isfinite(point.z) && std::isfinite(point.w);
}

std::string hashMatrix(const MMatrix& matrix)
{
    std::uint64_t hash = 1469598103934665603ULL;
    for (unsigned int row = 0U; row < 4U; ++row) {
        for (unsigned int column = 0U; column < 4U; ++column) {
            const double value = matrix[row][column];
            const auto* bytes = reinterpret_cast<const std::uint8_t*>(&value);
            for (unsigned int index = 0U; index < sizeof(value); ++index) {
                hash ^= bytes[index];
                hash *= 1099511628211ULL;
            }
        }
    }
    std::ostringstream stream;
    stream << "0x" << std::hex << hash;
    return stream.str();
}

void resetCasterMatrixDiagnostics()
{
    gCasterMatrixValid = false;
    gCasterMatrix = identityMatrix();
    gDiagnostics.matrixSource.clear();
    gDiagnostics.lightPath.clear();
    gDiagnostics.lightDirection = {0.0, 0.0, 0.0};
    gDiagnostics.worldBounds = {0.0, 0.0, 0.0, 0.0, 0.0, 0.0};
    gDiagnostics.lightBounds = {0.0, 0.0, 0.0, 0.0, 0.0, 0.0};
    gDiagnostics.matrixHash.clear();
    gDiagnostics.casterMatrixHash.clear();
    gDiagnostics.receiverMatrixHash.clear();
    gDiagnostics.cornersInClip = false;
}

bool buildCasterLightMatrix(const MSelectionList& selection)
{
    MDagPath lightPath;
    unsigned int lightCount = 0U;
    MItDependencyNodes iterator(MFn::kTransform);
    for (; !iterator.isDone(); iterator.next()) {
        const MObject object = iterator.item();
        MFnDependencyNode node(object);
        MStatus status;
        MPlug marker = node.findPlug(MString("mmd_light"), true, &status);
        if (!status || marker.isNull()) {
            continue;
        }
        MStatus valueStatus;
        if (!marker.asBool(&valueStatus) || !valueStatus) {
            continue;
        }
        ++lightCount;
        if (MDagPath::getAPathTo(object, lightPath) != MS::kSuccess) {
            gDiagnostics.error = "tagged mmd_light has no DAG path";
            return false;
        }
    }
    if (lightCount != 1U) {
        gDiagnostics.error = lightCount == 0U
                                 ? "exactly one tagged mmd_light is required"
                                 : "ambiguous tagged mmd_light authority";
        return false;
    }
    MStatus lightPathStatus;
    if (lightPath.isInstanced(&lightPathStatus) || !lightPathStatus) {
        gDiagnostics.error = "tagged mmd_light must be a unique non-instanced DAG path";
        return false;
    }
    gDiagnostics.matrixSource = "tagged_mmd_light_minus_z";
    gDiagnostics.lightPath = lightPath.fullPathName().asUTF8();

    const MMatrix lightWorld = lightPath.inclusiveMatrix();
    const MPoint lightOrigin = MPoint(0.0, 0.0, 0.0, 1.0) * lightWorld;
    const MPoint lightTip = MPoint(0.0, 0.0, -1.0, 1.0) * lightWorld;
    if (!finitePoint(lightOrigin) || !finitePoint(lightTip)) {
        gDiagnostics.error = "mmd_light world matrix is nonfinite";
        return false;
    }
    MVector lightDirection(lightTip.x - lightOrigin.x,
                           lightTip.y - lightOrigin.y,
                           lightTip.z - lightOrigin.z);
    if (!normalizeVector(lightDirection)) {
        gDiagnostics.error = "mmd_light direction is invalid";
        return false;
    }
    gDiagnostics.lightDirection = {lightDirection.x, lightDirection.y,
                                   lightDirection.z};

    MVector up = std::abs(lightDirection.y) > 0.95 ? MVector(1.0, 0.0, 0.0)
                                                   : MVector(0.0, 1.0, 0.0);
    MVector right = crossProduct(up, lightDirection);
    if (!normalizeVector(right)) {
        gDiagnostics.error = "mmd_light basis is invalid";
        return false;
    }
    up = crossProduct(lightDirection, right);
    if (!normalizeVector(up)) {
        gDiagnostics.error = "mmd_light up basis is invalid";
        return false;
    }

    std::array<double, 6> worldBounds = {
        std::numeric_limits<double>::infinity(),
        std::numeric_limits<double>::infinity(),
        std::numeric_limits<double>::infinity(),
        -std::numeric_limits<double>::infinity(),
        -std::numeric_limits<double>::infinity(),
        -std::numeric_limits<double>::infinity(),
    };
    std::vector<MPoint> corners;
    for (unsigned int index = 0U; index < selection.length(); ++index) {
        MDagPath path;
        if (selection.getDagPath(index, path) != MS::kSuccess) {
            gDiagnostics.error = "selected caster DAG path unavailable";
            return false;
        }
        MmdRenderShape* shape = MmdRenderShape::fromMObject(path.node());
        if (!shape) {
            gDiagnostics.error = "selected caster is not mmdRenderShape";
            return false;
        }
        const MBoundingBox localBounds = shape->boundingBox();
        const MPoint minimum = localBounds.min();
        const MPoint maximum = localBounds.max();
        if (!finitePoint(minimum) || !finitePoint(maximum) ||
            minimum.x > maximum.x || minimum.y > maximum.y ||
            minimum.z > maximum.z) {
            gDiagnostics.error = "selected caster bounds are invalid";
            return false;
        }
        const MMatrix world = path.inclusiveMatrix();
        const double xs[] = {minimum.x, maximum.x};
        const double ys[] = {minimum.y, maximum.y};
        const double zs[] = {minimum.z, maximum.z};
        for (const double x : xs) {
            for (const double y : ys) {
                for (const double z : zs) {
                    const MPoint point = MPoint(x, y, z, 1.0) * world;
                    if (!finitePoint(point)) {
                        gDiagnostics.error = "selected caster world bounds are nonfinite";
                        return false;
                    }
                    corners.push_back(point);
                    worldBounds[0] = std::min(worldBounds[0], point.x);
                    worldBounds[1] = std::min(worldBounds[1], point.y);
                    worldBounds[2] = std::min(worldBounds[2], point.z);
                    worldBounds[3] = std::max(worldBounds[3], point.x);
                    worldBounds[4] = std::max(worldBounds[4], point.y);
                    worldBounds[5] = std::max(worldBounds[5], point.z);
                }
            }
        }
    }
    if (corners.empty()) {
        gDiagnostics.error = "selected caster bounds are empty";
        return false;
    }
    gDiagnostics.worldBounds = worldBounds;

    std::array<double, 6> lightBounds = {
        std::numeric_limits<double>::infinity(),
        std::numeric_limits<double>::infinity(),
        std::numeric_limits<double>::infinity(),
        -std::numeric_limits<double>::infinity(),
        -std::numeric_limits<double>::infinity(),
        -std::numeric_limits<double>::infinity(),
    };
    for (const MPoint& point : corners) {
        const double x = point.x * right.x + point.y * right.y + point.z * right.z;
        const double y = point.x * up.x + point.y * up.y + point.z * up.z;
        const double z = point.x * lightDirection.x + point.y * lightDirection.y +
                         point.z * lightDirection.z;
        if (!std::isfinite(x) || !std::isfinite(y) || !std::isfinite(z)) {
            gDiagnostics.error = "selected caster light bounds are nonfinite";
            return false;
        }
        lightBounds[0] = std::min(lightBounds[0], x);
        lightBounds[1] = std::min(lightBounds[1], y);
        lightBounds[2] = std::min(lightBounds[2], z);
        lightBounds[3] = std::max(lightBounds[3], x);
        lightBounds[4] = std::max(lightBounds[4], y);
        lightBounds[5] = std::max(lightBounds[5], z);
    }
    const auto expandRange = [](double& minimum, double& maximum) {
        double range = maximum - minimum;
        if (!std::isfinite(range) || range <= kMatrixEpsilon) {
            range = 1.0;
        }
        const double margin = std::max(range * kBoundsMargin, 1.0e-3);
        minimum -= margin;
        maximum += margin;
    };
    expandRange(lightBounds[0], lightBounds[3]);
    expandRange(lightBounds[1], lightBounds[4]);
    expandRange(lightBounds[2], lightBounds[5]);
    gDiagnostics.lightBounds = lightBounds;
    const double xRange = lightBounds[3] - lightBounds[0];
    const double yRange = lightBounds[4] - lightBounds[1];
    const double zRange = lightBounds[5] - lightBounds[2];
    const double zAvailable = 1.0 - kClipGuard - kDepthBiasReserve;
    if (!std::isfinite(xRange) || !std::isfinite(yRange) ||
        !std::isfinite(zRange) || xRange <= kMatrixEpsilon ||
        yRange <= kMatrixEpsilon || zRange <= kMatrixEpsilon ||
        zAvailable <= kMatrixEpsilon) {
        gDiagnostics.error = "caster light bounds cannot form a projection";
        return false;
    }
    const double extent = std::max(xRange, yRange);
    if (!std::isfinite(extent) || extent <= kMatrixEpsilon) {
        gDiagnostics.error = "caster light bounds have no square extent";
        return false;
    }
    // A square shadow target uses one common XY scale.  Center each axis in
    // that square so a non-square model gets conservative letterboxing rather
    // than a view-dependent shear/stretch.
    const double sx = 2.0 / extent;
    const double sy = sx;
    const double sz = zAvailable / zRange;
    const double tx = -(lightBounds[3] + lightBounds[0]) / extent;
    const double ty = -(lightBounds[4] + lightBounds[1]) / extent;
    const double tz = kClipGuard - lightBounds[2] * sz;
    const double values[4][4] = {
        {right.x * sx, up.x * sy, lightDirection.x * sz, 0.0},
        {right.y * sx, up.y * sy, lightDirection.y * sz, 0.0},
        {right.z * sx, up.z * sy, lightDirection.z * sz, 0.0},
        {tx, ty, tz, 1.0},
    };
    const MMatrix matrix(values);
    const double validationBiases[] = {0.0, 0.35, 0.55, kDepthBiasReserve};
    for (const MPoint& point : corners) {
        const MPoint clip = point * matrix;
        if (!finitePoint(clip) || std::abs(clip.w) <= kMatrixEpsilon ||
            clip.x < -1.0 - kMatrixEpsilon || clip.x > 1.0 + kMatrixEpsilon ||
            clip.y < -1.0 - kMatrixEpsilon || clip.y > 1.0 + kMatrixEpsilon ||
            clip.z < -kMatrixEpsilon) {
            gDiagnostics.error = "caster bounds fall outside clip space";
            return false;
        }
        for (const double bias : validationBiases) {
            if (clip.z + bias > 1.0 + kMatrixEpsilon) {
                gDiagnostics.error = "caster depth bias clips selected bounds";
                return false;
            }
        }
    }
    gCasterMatrix = matrix;
    gCasterMatrixValid = true;
    gDiagnostics.matrixHash = hashMatrix(matrix);
    gDiagnostics.cornersInClip = true;
    return true;
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

void resetCasterFrameDiagnostics()
{
    gDiagnostics.setup = false;
    gDiagnostics.selectionBuilt = false;
    gDiagnostics.selectedCount = 0U;
    gDiagnostics.colorTargetAcquired = false;
    gDiagnostics.depthTargetAcquired = false;
    gDiagnostics.colorTarget = TargetDiagnostics();
    gDiagnostics.depthTarget = TargetDiagnostics();
    gDiagnostics.shaderAvailable = false;
    gDiagnostics.matrixBound = false;
    gDiagnostics.matrixValidated = false;
    gDiagnostics.depthBiasBound = false;
    gDiagnostics.depthBias = gDepthBias;
    gDiagnostics.drawAttempted = false;
    gDiagnostics.drawCallbackCount = 0U;
    gDiagnostics.drawnRenderItems.clear();
    gDiagnostics.drawnRenderItemDagPaths.clear();
    gDiagnostics.drawnRenderItemTypes.clear();
    gDiagnostics.drawnRenderItemCastsShadows.clear();
    gDiagnostics.frameComplete = false;
    gDiagnostics.operationInsertedBeforeScene = false;
    gDiagnostics.occupancySupported = false;
    gDiagnostics.occupied = false;
    gDiagnostics.receiverTargetResourceHandleNonNull = false;
    gDiagnostics.receiverTargetSameFrame = false;
    gDiagnostics.receiverAssignmentSuccess = 0U;
    gDiagnostics.receiverAssignmentFailure = 0U;
    gDiagnostics.hardShadowRequested = gHardShadowRequested;
    gDiagnostics.hardShadowEffective = false;
    gDiagnostics.hardShadowFrameEffective = false;
    gDiagnostics.hardShadowBias = gHardShadowBias;
    gDiagnostics.hardShadowBound = false;
    gDiagnostics.hardShadowBiasBound = false;
    gDiagnostics.failClosedHardShadowDisableAttempted = false;
    gDiagnostics.failClosedHardShadowDisableSuccess = false;
    gDiagnostics.failClosedHardShadowDisableFailureCount = 0U;
    gDiagnostics.failClosedProbeDisableAttempted = false;
    gDiagnostics.failClosedProbeDisableSuccess = false;
    gDiagnostics.failClosedProbeDisableFailureCount = 0U;
    resetCasterMatrixDiagnostics();
    resetDepthDiagnostics();
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
        // Transparent MaterialSceneItems stay eligible for Maya's ordinary
        // viewport transparent pass, but must never enter this caster pass.
        return MHWRender::MSceneRender::kRenderOpaqueShadedItems;
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
        gDiagnostics.drawCallbackCount = 0U;
        gDiagnostics.drawnRenderItems.clear();
        gDiagnostics.drawnRenderItemDagPaths.clear();
        gDiagnostics.drawnRenderItemTypes.clear();
        gDiagnostics.drawnRenderItemCastsShadows.clear();
        if (!shader_) {
            return;
        }
        if (!gCasterMatrixValid) {
            gDiagnostics.matrixValidated = false;
            return;
        }
        const MMatrix matrix = gCasterMatrix;
        gDiagnostics.matrixValidated = true;
        gDiagnostics.casterMatrixHash = hashMatrix(matrix);
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
        // The resource handle is device-backed and may only become valid once
        // the operation has rendered.  Check it at the same frame boundary as
        // the raw depth readback instead of treating target acquisition alone
        // as proof that the receiver got a usable input.
        gDiagnostics.receiverTargetResourceHandleNonNull =
            targets_[0]->resourceHandle() != nullptr;
        {
            std::lock_guard<std::mutex> lock(gReceiverMutex);
            gDiagnostics.receiverLiveAssignmentOwners =
                gReceiverBindings.size();
            for (const auto& binding : gReceiverBindings) {
                if (binding.second == targets_[0]) {
                    gDiagnostics.receiverTargetSameFrame = true;
                    break;
                }
            }
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
    {
        std::lock_guard<std::mutex> lock(gReceiverMutex);
        gOverrideSetup = false;
    }
    mOperations.clear();
    disableReceiverProbeForFailClosed();
    // Body shaders are borrowed by the geometry overrides.  Their owners must
    // retire first; release the caster shader and targets only after the
    // registry is empty.  If a host keeps a geometry override alive, retain
    // the targets instead of leaving a dangling assignment behind.
    releaseShader();
    if (!releaseTargets()) {
        gDiagnostics.error =
            "receiver shaders still live; caster targets retained";
    }
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

bool MmdNativeCasterRenderOverride::bindReceiverShader(
    MHWRender::MShaderInstance* shader)
{
    if (!shader || !colorTarget_) {
        return false;
    }

    if (!updateReceiverShaderParameters(shader)) {
        return false;
    }
    MHWRender::MRenderTargetAssignment assignment{colorTarget_};
    const MStatus assignmentStatus = shader->setParameter(
        MString("NativeCasterDepthTexture"), assignment);
    if (assignmentStatus == MS::kSuccess) {
        ++gDiagnostics.receiverAssignmentSuccess;
        std::lock_guard<std::mutex> lock(gReceiverMutex);
        gReceiverBindings[shader] = colorTarget_;
        gDiagnostics.receiverLiveAssignmentOwners = gReceiverBindings.size();
    } else {
        ++gDiagnostics.receiverAssignmentFailure;
    }
    if (assignmentStatus != MS::kSuccess) {
        if (gDiagnostics.error.empty()) {
            gDiagnostics.error = "receiver shader binding failed";
        }
        return false;
    }
    return true;
}

bool MmdNativeCasterRenderOverride::updateReceiverShaderParameters(
    MHWRender::MShaderInstance* shader)
{
    if (!shader) {
        return false;
    }
    if (!gCasterMatrixValid) {
        if (gDiagnostics.error.empty()) {
            gDiagnostics.error = "caster matrix authority unavailable";
        }
        return false;
    }
    const MMatrix matrix = gCasterMatrix;
    gDiagnostics.receiverMatrixHash = hashMatrix(matrix);
    const MStatus matrixStatus =
        shader->setParameter(MString("CasterLightViewProjection"), matrix);
    const MStatus biasStatus =
        shader->setParameter(MString("CasterDepthBias"), gDepthBias);
    const MStatus hardShadowBiasStatus = shader->setParameter(
        MString("NativeCasterShadowBias"), gHardShadowBias);
    const MStatus probeStatus =
        shader->setParameter(MString("NativeCasterProbe"),
                             gReceiverProbe ? 1 : 0);
    const MStatus hardShadowStatus = shader->setParameter(
        MString("NativeCasterHardShadow"), gHardShadowEffective ? 1 : 0);
    gDiagnostics.hardShadowBiasBound =
        hardShadowBiasStatus == MS::kSuccess;
    gDiagnostics.hardShadowBound = hardShadowStatus == MS::kSuccess;
    if (matrixStatus != MS::kSuccess || biasStatus != MS::kSuccess ||
        hardShadowBiasStatus != MS::kSuccess ||
        probeStatus != MS::kSuccess || hardShadowStatus != MS::kSuccess) {
        if (gDiagnostics.error.empty()) {
            gDiagnostics.error = "receiver shader parameter binding failed";
        }
        return false;
    }
    return true;
}

void MmdNativeCasterRenderOverride::registerReceiverShader(
    MHWRender::MShaderInstance* shader)
{
    if (!shader) {
        return;
    }
    {
        std::lock_guard<std::mutex> lock(gReceiverMutex);
        const auto result = gReceiverShaders.insert(shader);
        if (!result.second) {
            return;
        }
        gDiagnostics.receiverShaderRegistered = gReceiverShaders.size();
    }
}

bool MmdNativeCasterRenderOverride::beginReceiverShaderRetire(
    MHWRender::MShaderInstance* shader)
{
    if (!shader) {
        return false;
    }
    std::unique_lock<std::mutex> lock(gReceiverMutex);
    const bool live = gReceiverShaders.erase(shader) > 0U;
    const bool bound = gReceiverBindings.count(shader) != 0U;
    const bool pinned = gReceiverPins.count(shader) != 0U;
    if (live || bound || pinned) {
        gRetiringReceiverShaders.insert(shader);
    }
    gDiagnostics.receiverShaderRegistered = gReceiverShaders.size();
    gDiagnostics.receiverLiveAssignmentOwners = gReceiverBindings.size();
    if (live || bound || pinned) {
        gReceiverCv.wait(lock, [shader] {
            const auto pin = gReceiverPins.find(shader);
            return pin == gReceiverPins.end() || pin->second == 0U;
        });
    }
    return live || bound || pinned;
}

void MmdNativeCasterRenderOverride::finishReceiverShaderRetire(
    MHWRender::MShaderInstance* shader)
{
    if (!shader) {
        return;
    }
    std::lock_guard<std::mutex> lock(gReceiverMutex);
    // The shader has been released by its geometry owner.  Keep no stale
    // pointer in either registry even if a host issued duplicate callbacks.
    gReceiverShaders.erase(shader);
    gReceiverBindings.erase(shader);
    gRetiringReceiverShaders.erase(shader);
    gReceiverPins.erase(shader);
    gReceiverCv.notify_all();
    gDiagnostics.receiverShaderRegistered = gReceiverShaders.size();
    gDiagnostics.receiverLiveAssignmentOwners = gReceiverBindings.size();
}

bool MmdNativeCasterRenderOverride::shutdownReady()
{
    std::lock_guard<std::mutex> lock(gReceiverMutex);
    return !gOverrideSetup && gReceiverShaders.empty() &&
           gReceiverBindings.empty() && gRetiringReceiverShaders.empty() &&
           gReceiverPins.empty();
}

void MmdNativeCasterRenderOverride::setReceiverProbe(bool enabled)
{
    gReceiverProbe = enabled;
    gDiagnostics.receiverProbeEnabled = enabled;
}

void MmdNativeCasterRenderOverride::setHardShadowCompare(bool enabled)
{
    gHardShadowRequested = enabled;
    gDiagnostics.hardShadowRequested = enabled;
    // The effective flag is granted only by setup after matrix, target, and
    // receiver binding have all succeeded.  A disable request is applied
    // immediately with the same pinned, checked fail-closed path used by
    // cleanup and missing-light handling.
    if (!enabled) {
        disableHardShadowForFailClosed();
    } else {
        gHardShadowEffective = false;
        gDiagnostics.hardShadowEffective = false;
    }
}

void MmdNativeCasterRenderOverride::setHardShadowBias(float bias)
{
    gHardShadowBias = bias;
    gDiagnostics.hardShadowBias = bias;
}

bool MmdNativeCasterRenderOverride::acquireTargets()
{
    MHWRender::MRenderer* renderer = MHWRender::MRenderer::theRenderer();
    MHWRender::MRenderTargetManager* targetManager =
        renderer ? const_cast<MHWRender::MRenderTargetManager*>(
                       renderer->getRenderTargetManager())
                 : nullptr;
    if (!targetManager) {
        gDiagnostics.error = "render target manager unavailable";
        return false;
    }
    const MHWRender::MRenderTargetDescription colorDescription(
        casterColorTargetName(), kTargetSize, kTargetSize,
        1U, MHWRender::kR32_FLOAT, 1U, false);
    const MHWRender::MRenderTargetDescription depthDescription(
        casterDepthTargetName(), kTargetSize, kTargetSize,
        1U, MHWRender::kD32_FLOAT, 1U, false);
    MHWRender::MRenderTarget* colorTarget =
        targetManager->acquireRenderTarget(colorDescription);
    MHWRender::MRenderTarget* depthTarget =
        targetManager->acquireRenderTarget(depthDescription);
    gDiagnostics.colorTargetAcquired = colorTarget != nullptr;
    gDiagnostics.depthTargetAcquired = depthTarget != nullptr;
    if (!colorTarget || !depthTarget) {
        if (colorTarget) {
            targetManager->releaseRenderTarget(colorTarget);
        }
        if (depthTarget) {
            targetManager->releaseRenderTarget(depthTarget);
        }
        gDiagnostics.error = "caster render target acquisition failed";
        return false;
    }
    targetManager_ = targetManager;
    colorTarget_ = colorTarget;
    depthTarget_ = depthTarget;
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
    return true;
}

bool MmdNativeCasterRenderOverride::releaseTargets()
{
    std::size_t registered = 0U;
    std::size_t owners = 0U;
    std::size_t retiring = 0U;
    std::size_t pins = 0U;
    {
        std::lock_guard<std::mutex> lock(gReceiverMutex);
        registered = gReceiverShaders.size();
        owners = gReceiverBindings.size();
        retiring = gRetiringReceiverShaders.size();
        pins = gReceiverPins.size();
    }
    gDiagnostics.receiverShaderRegistered = registered;
    gDiagnostics.receiverLiveAssignmentOwners = owners;
    gDiagnostics.receiverTargetsRetained =
        (colorTarget_ != nullptr || depthTarget_ != nullptr) &&
        (registered != 0U || owners != 0U || retiring != 0U || pins != 0U);
    // A body shader owns a borrowed target assignment.  Maya does not expose
    // a documented null-target unbind operation, so retain the target until
    // every exact shader owner has retired rather than leaving a dangling
    // device pointer during scene reset or plug-in unload.
    if (registered != 0U || owners != 0U || retiring != 0U || pins != 0U) {
        gDiagnostics.error =
            "receiver shaders still live; targets retained until retire";
        gDiagnostics.released = false;
        return false;
    }
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
    gDiagnostics.receiverTargetsRetained = false;
    return true;
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
    {
        std::lock_guard<std::mutex> lock(gReceiverMutex);
        gOverrideSetup = false;
    }
    // Effective hard-shadow state is per successful setup/frame.  Keep the
    // request and normalized bias across retries, but never carry an enabled
    // receiver mask into a setup that has not yet revalidated its inputs.
    gHardShadowEffective = false;
    gDiagnostics.released = false;
    gDiagnostics.error.clear();
    resetCasterFrameDiagnostics();
    std::size_t existingReceiverBindings = 0U;
    {
        std::lock_guard<std::mutex> lock(gReceiverMutex);
        gDiagnostics.receiverShaderRegistered = gReceiverShaders.size();
        gDiagnostics.receiverLiveAssignmentOwners = gReceiverBindings.size();
        for (const auto& binding : gReceiverBindings) {
            if (gReceiverShaders.count(binding.first) != 0U) {
                ++existingReceiverBindings;
            }
        }
    }
    // Reusing a persistent assignment is already a successful exact target
    // binding for this frame; do not issue another setParameter call merely
    // to inflate the diagnostic counter.
    gDiagnostics.receiverAssignmentSuccess = existingReceiverBindings;
    gDiagnostics.receiverAssignmentFailure = 0U;
    gDiagnostics.receiverProbeEnabled = gReceiverProbe;
    gDiagnostics.receiverTargetResourceHandleNonNull = false;
    gDiagnostics.receiverTargetSameFrame = false;
    gDiagnostics.receiverTargetsRetained =
        colorTarget_ != nullptr || depthTarget_ != nullptr;
    gDiagnostics.hardShadowRequested = gHardShadowRequested;
    gDiagnostics.hardShadowEffective = false;
    gDiagnostics.hardShadowBias = gHardShadowBias;
    mOperations.clear();
    MHWRender::MRenderer* renderer = MHWRender::MRenderer::theRenderer();
    if (!renderer) {
        gDiagnostics.error = "renderer unavailable";
        disableReceiverProbeForFailClosed();
        return MS::kSuccess;
    }
    MSelectionList selection;
    gDiagnostics.selectionBuilt = buildCasterSelection(selection);
    gDiagnostics.selectedCount = static_cast<std::size_t>(selection.length());
    if (!gDiagnostics.selectionBuilt || !buildCasterLightMatrix(selection)) {
        // Fail closed: leave Maya's standard scene operations intact and do
        // not insert a caster operation without a unique scene light and
        // finite selection bounds.
        disableReceiverProbeForFailClosed();
        renderer->getStandardViewportOperations(mOperations);
        gDiagnostics.setup = false;
        return MS::kSuccess;
    }
    // Keep the private target and caster shader alive across cleanup/setup
    // cycles.  Body shader assignments borrow the exact target and cannot be
    // safely cleared with an undocumented null assignment.
    if (!colorTarget_ || !depthTarget_) {
        if (!acquireTargets()) {
            return MS::kFailure;
        }
    }

    auto describeTarget = [](MHWRender::MRenderTarget* target,
                             TargetDiagnostics& diagnostic) {
        if (!target) {
            return;
        }
        MHWRender::MRenderTargetDescription actualDescription;
        target->targetDescription(actualDescription);
        diagnostic.width = actualDescription.width();
        diagnostic.height = actualDescription.height();
        diagnostic.multiSampleCount = actualDescription.multiSampleCount();
        diagnostic.arraySliceCount = actualDescription.arraySliceCount();
        diagnostic.format = static_cast<int>(actualDescription.rasterFormat());
        diagnostic.isCubeMap = actualDescription.isCubeMap();
        diagnostic.name = actualDescription.name().asUTF8();
    };
    describeTarget(colorTarget_, gDiagnostics.colorTarget);
    describeTarget(depthTarget_, gDiagnostics.depthTarget);
    gDiagnostics.colorTargetAcquired = colorTarget_ != nullptr;
    gDiagnostics.depthTargetAcquired = depthTarget_ != nullptr;
    if (!colorTarget_ || !depthTarget_) {
        gDiagnostics.error = "caster render target acquisition failed";
        disableHardShadowForFailClosed();
        return MS::kFailure;
    }

    const MHWRender::MShaderManager* shaderManager =
        renderer ? renderer->getShaderManager() : nullptr;
    if (!shaderManager) {
        gDiagnostics.error = "shader manager unavailable";
        disableHardShadowForFailClosed();
        return MS::kFailure;
    }
    if (!shader_) {
        MHWRender::MShaderInstance* shader =
            shaderManager->getEffectsFileShader(
                MString(shaderPath().c_str()), MString("MMDNativeCaster"),
                nullptr, 0U, true, casterDrawCallback, nullptr);
        gDiagnostics.shaderAvailable = shader != nullptr;
        if (!shader) {
            gDiagnostics.error = "MMDNativeCaster shader unavailable";
            disableHardShadowForFailClosed();
            return MS::kFailure;
        }
        shaderManager_ = shaderManager;
        shader_ = shader;
    } else {
        gDiagnostics.shaderAvailable = true;
    }

    casterOperation_ = new CasterSceneRender();
    casterOperation_->setSelection(selection);
    casterOperation_->setTargets(colorTarget_, depthTarget_);
    casterOperation_->setShader(shader_);
    renderer->getStandardViewportOperations(mOperations);
    gDiagnostics.operationInsertedBeforeScene = mOperations.insertBefore(
        MHWRender::MRenderOperation::kStandardSceneName, casterOperation_);
    if (!gDiagnostics.operationInsertedBeforeScene) {
        delete casterOperation_;
        casterOperation_ = nullptr;
        gDiagnostics.error = "failed to insert caster before standard scene";
        disableHardShadowForFailClosed();
        return MS::kFailure;
    }
    gDiagnostics.setup = true;
    // Body shaders may have been published before this setup call.  Bind all
    // exact borrowed pointers now; registrations published during geometry
    // update are picked up by the next setup boundary before the next caster
    // operation executes.
    std::vector<MHWRender::MShaderInstance*> shaders;
    std::unordered_map<MHWRender::MShaderInstance*, MHWRender::MRenderTarget*>
        bound;
    {
        std::lock_guard<std::mutex> lock(gReceiverMutex);
        shaders.reserve(gReceiverShaders.size());
        for (MHWRender::MShaderInstance* receiver : gReceiverShaders) {
            shaders.push_back(receiver);
            ++gReceiverPins[receiver];
        }
        bound = gReceiverBindings;
        gOverrideSetup = true;
    }
    bool receiverBindingsReady = true;
    for (MHWRender::MShaderInstance* receiver : shaders) {
        bool receiverReady = false;
        if (bound.count(receiver) == 0U) {
            receiverReady = bindReceiverShader(receiver);
        } else {
            receiverReady = updateReceiverShaderParameters(receiver);
        }
        receiverBindingsReady = receiverBindingsReady && receiverReady;
    }
    // A hard-shadow mask is enabled only after every borrowed receiver has a
    // valid matrix/parameter update and target assignment.  Apply the final
    // effective flag in a second pinned pass so a partial binding can never
    // leave one shader visibly masked while another failed closed.
    if (gHardShadowRequested && receiverBindingsReady && !shaders.empty()) {
        gHardShadowEffective = true;
        gDiagnostics.hardShadowEffective = true;
        gDiagnostics.hardShadowFrameEffective = true;
        bool hardShadowReady = true;
        for (MHWRender::MShaderInstance* receiver : shaders) {
            const MStatus status = receiver
                                       ? receiver->setParameter(
                                             MString("NativeCasterHardShadow"), 1)
                                       : MS::kFailure;
            hardShadowReady = status == MS::kSuccess && hardShadowReady;
        }
        if (!hardShadowReady) {
            gDiagnostics.hardShadowFrameEffective = false;
            disableHardShadowForFailClosed();
        }
    } else if (gHardShadowRequested && !receiverBindingsReady) {
        disableHardShadowForFailClosed();
    }
    // Keep the original snapshot pins held across both parameter-binding
    // passes (and the fail-closed pass above), then release them together.
    // This closes the lifetime gap where a raw shader pointer could otherwise
    // retire between the first update and hard-shadow enable.
    for (MHWRender::MShaderInstance* receiver : shaders) {
        releaseReceiverPin(receiver);
    }
    return MS::kSuccess;
}

MStatus MmdNativeCasterRenderOverride::cleanup()
{
    {
        std::lock_guard<std::mutex> lock(gReceiverMutex);
        gOverrideSetup = false;
    }
    // Do not leave a receiver-side diagnostic mask enabled after the private
    // caster operation is removed.  The pinned, checked writes retain the
    // target lifetime until each borrowed shader retires; no null assignment
    // is used.
    disableHardShadowForFailClosed();
    mOperations.clear();
    casterOperation_ = nullptr;
    gDiagnostics.setup = false;
    gDiagnostics.receiverTargetsRetained =
        colorTarget_ != nullptr || depthTarget_ != nullptr;
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
           << ",\"matrixSource\":" << jsonString(gDiagnostics.matrixSource)
           << ",\"lightPath\":" << jsonString(gDiagnostics.lightPath)
           << ",\"lightDirection\":["
           << gDiagnostics.lightDirection[0] << ','
           << gDiagnostics.lightDirection[1] << ','
           << gDiagnostics.lightDirection[2] << ']'
           << ",\"worldBounds\":["
           << gDiagnostics.worldBounds[0] << ','
           << gDiagnostics.worldBounds[1] << ','
           << gDiagnostics.worldBounds[2] << ','
           << gDiagnostics.worldBounds[3] << ','
           << gDiagnostics.worldBounds[4] << ','
           << gDiagnostics.worldBounds[5] << ']'
           << ",\"lightBounds\":["
           << gDiagnostics.lightBounds[0] << ','
           << gDiagnostics.lightBounds[1] << ','
           << gDiagnostics.lightBounds[2] << ','
           << gDiagnostics.lightBounds[3] << ','
           << gDiagnostics.lightBounds[4] << ','
           << gDiagnostics.lightBounds[5] << ']'
           << ",\"matrixHash\":" << jsonString(gDiagnostics.matrixHash)
           << ",\"casterMatrixHash\":"
           << jsonString(gDiagnostics.casterMatrixHash)
           << ",\"receiverMatrixHash\":"
           << jsonString(gDiagnostics.receiverMatrixHash)
           << ",\"cornersInClip\":"
           << jsonBool(gDiagnostics.cornersInClip)
           << ",\"depthBiasBound\":"
           << jsonBool(gDiagnostics.depthBiasBound)
           << ",\"depthBias\":" << gDiagnostics.depthBias
           << ",\"drawAttempted\":" << jsonBool(gDiagnostics.drawAttempted)
           << ",\"drawCallbackCount\":" << gDiagnostics.drawCallbackCount
           << ",\"drawnRenderItems\":[";
    for (std::size_t index = 0U;
         index < gDiagnostics.drawnRenderItems.size(); ++index) {
        if (index != 0U) {
            stream << ',';
        }
        stream << jsonString(gDiagnostics.drawnRenderItems[index]);
    }
    stream << "],\"drawnRenderItemDagPaths\":[";
    for (std::size_t index = 0U;
         index < gDiagnostics.drawnRenderItemDagPaths.size(); ++index) {
        if (index != 0U) {
            stream << ',';
        }
        stream << jsonString(gDiagnostics.drawnRenderItemDagPaths[index]);
    }
    stream << "],\"drawnRenderItemTypes\":[";
    for (std::size_t index = 0U;
         index < gDiagnostics.drawnRenderItemTypes.size(); ++index) {
        if (index != 0U) {
            stream << ',';
        }
        stream << jsonString(gDiagnostics.drawnRenderItemTypes[index]);
    }
    stream << "],\"drawnRenderItemCastsShadows\":[";
    for (std::size_t index = 0U;
         index < gDiagnostics.drawnRenderItemCastsShadows.size(); ++index) {
        if (index != 0U) {
            stream << ',';
        }
        stream << jsonBool(gDiagnostics.drawnRenderItemCastsShadows[index]);
    }
    stream << ']'
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
           << ",\"receiverShaderRegistered\":"
           << gDiagnostics.receiverShaderRegistered
           << ",\"receiverAssignmentSuccess\":"
           << gDiagnostics.receiverAssignmentSuccess
           << ",\"receiverAssignmentFailure\":"
           << gDiagnostics.receiverAssignmentFailure
           << ",\"receiverLiveAssignmentOwners\":"
           << gDiagnostics.receiverLiveAssignmentOwners
           << ",\"receiverProbeEnabled\":"
           << jsonBool(gDiagnostics.receiverProbeEnabled)
           << ",\"receiverTargetResourceHandleNonNull\":"
           << jsonBool(gDiagnostics.receiverTargetResourceHandleNonNull)
           << ",\"receiverTargetSameFrame\":"
           << jsonBool(gDiagnostics.receiverTargetSameFrame)
           << ",\"receiverTargetsRetained\":"
           << jsonBool(gDiagnostics.receiverTargetsRetained)
           << ",\"hardShadowRequested\":"
           << jsonBool(gDiagnostics.hardShadowRequested)
           << ",\"hardShadowEffective\":"
           << jsonBool(gDiagnostics.hardShadowEffective)
           << ",\"hardShadowFrameEffective\":"
           << jsonBool(gDiagnostics.hardShadowFrameEffective)
           << ",\"hardShadowBias\":" << gDiagnostics.hardShadowBias
           << ",\"hardShadowBound\":"
           << jsonBool(gDiagnostics.hardShadowBound)
           << ",\"hardShadowBiasBound\":"
           << jsonBool(gDiagnostics.hardShadowBiasBound)
           << ",\"hardShadowBindSuccess\":"
           << jsonBool(gDiagnostics.hardShadowBound &&
                       gDiagnostics.hardShadowBiasBound &&
                       gDiagnostics.receiverAssignmentFailure == 0U)
           // Keep compare-oriented aliases explicit for consumers that use
           // the command name rather than the shader flag name.
           << ",\"hardShadowCompareRequested\":"
           << jsonBool(gDiagnostics.hardShadowRequested)
           << ",\"hardShadowCompareEffective\":"
           << jsonBool(gDiagnostics.hardShadowEffective)
           << ",\"failClosedHardShadowDisableAttempted\":"
           << jsonBool(gDiagnostics.failClosedHardShadowDisableAttempted)
           << ",\"failClosedHardShadowDisableSuccess\":"
           << jsonBool(gDiagnostics.failClosedHardShadowDisableSuccess)
           << ",\"failClosedHardShadowDisableFailureCount\":"
           << gDiagnostics.failClosedHardShadowDisableFailureCount
           << ",\"failClosedProbeDisableAttempted\":"
           << jsonBool(gDiagnostics.failClosedProbeDisableAttempted)
           << ",\"failClosedProbeDisableSuccess\":"
           << jsonBool(gDiagnostics.failClosedProbeDisableSuccess)
           << ",\"failClosedProbeDisableFailureCount\":"
           << gDiagnostics.failClosedProbeDisableFailureCount
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
    syntax.addFlag("-rp", "-receiverProbe", MSyntax::kBoolean);
    syntax.addFlag("-hc", "-hardShadowCompare", MSyntax::kBoolean);
    syntax.addFlag("-hb", "-hardShadowBias", MSyntax::kDouble);
    return syntax;
}

MStatus MmdNativeCasterWitnessCommand::doIt(const MArgList& args)
{
    MStatus status;
    MArgDatabase argumentData(newSyntax(), args, &status);
    if (!status) {
        return status;
    }
    bool requestedReceiverProbe = gReceiverProbe;
    bool requestedHardShadow = gHardShadowRequested;
    if (argumentData.isFlagSet("-receiverProbe")) {
        status = argumentData.getFlagArgument("-receiverProbe", 0,
                                              requestedReceiverProbe);
        if (!status) {
            MGlobal::displayError(
                "mmdNativeCasterWitness receiverProbe must be boolean");
            return MS::kFailure;
        }
    }
    if (argumentData.isFlagSet("-hardShadowCompare")) {
        status = argumentData.getFlagArgument("-hardShadowCompare", 0,
                                              requestedHardShadow);
        if (!status) {
            MGlobal::displayError(
                "mmdNativeCasterWitness hardShadowCompare must be boolean");
            return MS::kFailure;
        }
    }
    if (requestedReceiverProbe && requestedHardShadow) {
        MGlobal::displayError(
            "mmdNativeCasterWitness receiverProbe and hardShadowCompare are mutually exclusive");
        return MS::kFailure;
    }
    if (argumentData.isFlagSet("-depthBias")) {
        double requestedBias = 0.0;
        status = argumentData.getFlagArgument("-depthBias", 0, requestedBias);
        if (!status || !std::isfinite(requestedBias) || requestedBias < 0.0 ||
            requestedBias > kDepthBiasReserve) {
            MGlobal::displayError(
                "mmdNativeCasterWitness depthBias must be finite in [0, 0.6]");
            return MS::kFailure;
        }
        gDepthBias = static_cast<float>(requestedBias);
        // Keep already-published body shaders in sync when the command is
        // issued while the override is live; setup will bind the same value
        // for the next frame when the target is not active yet.
        MmdNativeCasterRenderOverride::setReceiverProbe(gReceiverProbe);
    }
    if (argumentData.isFlagSet("-hardShadowBias")) {
        double requestedBias = 0.0;
        status = argumentData.getFlagArgument("-hardShadowBias", 0,
                                              requestedBias);
        if (!status || !std::isfinite(requestedBias) || requestedBias < 0.0 ||
            requestedBias > 0.01) {
            MGlobal::displayError(
                "mmdNativeCasterWitness hardShadowBias must be finite in [0, 0.01]");
            return MS::kFailure;
        }
        MmdNativeCasterRenderOverride::setHardShadowBias(
            static_cast<float>(requestedBias));
    }
    if (argumentData.isFlagSet("-hardShadowCompare")) {
        bool enabled = false;
        status = argumentData.getFlagArgument("-hardShadowCompare", 0, enabled);
        if (!status) {
            MGlobal::displayError(
                "mmdNativeCasterWitness hardShadowCompare must be boolean");
            return MS::kFailure;
        }
        MmdNativeCasterRenderOverride::setHardShadowCompare(enabled);
    }
    if (argumentData.isFlagSet("-receiverProbe")) {
        bool enabled = false;
        status = argumentData.getFlagArgument("-receiverProbe", 0, enabled);
        if (!status) {
            MGlobal::displayError(
                "mmdNativeCasterWitness receiverProbe must be boolean");
            return MS::kFailure;
        }
        MmdNativeCasterRenderOverride::setReceiverProbe(enabled);
    }
    setResult(MString(MmdNativeCasterRenderOverride::diagnosticsJson().c_str()));
    return MS::kSuccess;
}

bool MmdNativeCasterWitnessCommand::isUndoable() const
{
    return false;
}
