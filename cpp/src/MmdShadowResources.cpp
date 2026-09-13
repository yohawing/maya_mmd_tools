/** @file MmdShadowResources.cpp
 * @brief Private shadow targets and light projection for MMD Render.
 */
#include "MmdShadowResources.h"
#include "MmdRenderShape.h"
#include <maya/MBoundingBox.h>
#include <maya/MDagPath.h>
#include <maya/MFnDependencyNode.h>
#include <maya/MItDependencyNodes.h>
#include <maya/MPlug.h>
#include <maya/MPoint.h>
#include <maya/MVector.h>
#include <algorithm>
#include <array>
#include <cmath>
#include <limits>
#include <mutex>
#include <unordered_set>
#include <vector>

namespace {
std::mutex gReceiverMutex;
std::unordered_set<MHWRender::MShaderInstance*> gReceiverShaders;
std::unordered_set<MHWRender::MShaderInstance*> gRetiringReceiverShaders;
constexpr float kMatrixEpsilon = 1.0e-6F;
constexpr double kClipGuard = 0.02;
constexpr double kDepthBiasReserve = 0.60;
constexpr double kBoundsMargin = 0.05;
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

bool buildCasterLightMatrix(
    const MSelectionList& selection,
    MmdShadowResources::FrameResources& resources)
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
            return false;
        }
    }
    if (lightCount != 1U) {
        return false;
    }
    MStatus lightPathStatus;
    if (lightPath.isInstanced(&lightPathStatus) || !lightPathStatus) {
        return false;
    }

    MFnDependencyNode lightNode(lightPath.node());
    MStatus attributeStatus;
    const MPlug mode = lightNode.findPlug("mmd_self_shadow_mode", true,
                                          &attributeStatus);
    if (attributeStatus) {
        resources.selfShadowMode = mode.asInt(&attributeStatus);
        if (!attributeStatus || resources.selfShadowMode < 0 ||
            resources.selfShadowMode > 2) {
            return false;
        }
    }
    const MPlug distance = lightNode.findPlug("mmd_self_shadow_distance", true,
                                              &attributeStatus);
    if (attributeStatus) {
        resources.selfShadowDistance = distance.asDouble(&attributeStatus);
        if (!attributeStatus || !std::isfinite(resources.selfShadowDistance)) {
            return false;
        }
    }

    const MMatrix lightWorld = lightPath.inclusiveMatrix();
    const MPoint lightOrigin = MPoint(0.0, 0.0, 0.0, 1.0) * lightWorld;
    const MPoint lightTip = MPoint(0.0, 0.0, -1.0, 1.0) * lightWorld;
    if (!finitePoint(lightOrigin) || !finitePoint(lightTip)) {
        return false;
    }
    MVector lightDirection(lightTip.x - lightOrigin.x,
                           lightTip.y - lightOrigin.y,
                           lightTip.z - lightOrigin.z);
    if (!normalizeVector(lightDirection)) {
        return false;
    }

    MVector up = std::abs(lightDirection.y) > 0.95 ? MVector(1.0, 0.0, 0.0)
                                                   : MVector(0.0, 1.0, 0.0);
    MVector right = crossProduct(up, lightDirection);
    if (!normalizeVector(right)) {
        return false;
    }
    up = crossProduct(lightDirection, right);
    if (!normalizeVector(up)) {
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
            return false;
        }
        MmdRenderShape* shape = MmdRenderShape::fromMObject(path.node());
        if (!shape) {
            return false;
        }
        const MBoundingBox localBounds = shape->boundingBox();
        const MPoint minimum = localBounds.min();
        const MPoint maximum = localBounds.max();
        if (!finitePoint(minimum) || !finitePoint(maximum) ||
            minimum.x > maximum.x || minimum.y > maximum.y ||
            minimum.z > maximum.z) {
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
        return false;
    }

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
    const double xRange = lightBounds[3] - lightBounds[0];
    const double yRange = lightBounds[4] - lightBounds[1];
    const double zRange = lightBounds[5] - lightBounds[2];
    const double zAvailable = 1.0 - kClipGuard - kDepthBiasReserve;
    if (!std::isfinite(xRange) || !std::isfinite(yRange) ||
        !std::isfinite(zRange) || xRange <= kMatrixEpsilon ||
        yRange <= kMatrixEpsilon || zRange <= kMatrixEpsilon ||
        zAvailable <= kMatrixEpsilon) {
        return false;
    }
    const double extent = std::max(xRange, yRange);
    if (!std::isfinite(extent) || extent <= kMatrixEpsilon) {
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
            return false;
        }
        for (const double bias : validationBiases) {
            if (clip.z + bias > 1.0 + kMatrixEpsilon) {
                return false;
            }
        }
    }
    resources.lightViewProjection = matrix;
    return true;
}

} // namespace

MmdShadowResources::~MmdShadowResources()
{
    releaseTargets();
}

void MmdShadowResources::registerReceiverShader(MHWRender::MShaderInstance* shader)
{
    if (shader) {
        std::lock_guard<std::mutex> lock(gReceiverMutex);
        gReceiverShaders.insert(shader);
    }
}

bool MmdShadowResources::beginReceiverShaderRetire(MHWRender::MShaderInstance* shader)
{
    std::lock_guard<std::mutex> lock(gReceiverMutex);
    const bool tracked = gReceiverShaders.erase(shader) > 0U;
    if (tracked) {
        gRetiringReceiverShaders.insert(shader);
    }
    return tracked;
}

void MmdShadowResources::finishReceiverShaderRetire(MHWRender::MShaderInstance* shader)
{
    std::lock_guard<std::mutex> lock(gReceiverMutex);
    gReceiverShaders.erase(shader);
    gRetiringReceiverShaders.erase(shader);
}

bool MmdShadowResources::shutdownReady()
{
    std::lock_guard<std::mutex> lock(gReceiverMutex);
    return gReceiverShaders.empty() && gRetiringReceiverShaders.empty();
}

bool MmdShadowResources::acquireTargets()
{
    MHWRender::MRenderer* renderer = MHWRender::MRenderer::theRenderer();
    MHWRender::MRenderTargetManager* targetManager =
        renderer ? const_cast<MHWRender::MRenderTargetManager*>(
                       renderer->getRenderTargetManager())
                 : nullptr;
    if (!targetManager) {
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
    if (!colorTarget || !depthTarget) {
        if (colorTarget) {
            targetManager->releaseRenderTarget(colorTarget);
        }
        if (depthTarget) {
            targetManager->releaseRenderTarget(depthTarget);
        }
        return false;
    }
    targetManager_ = targetManager;
    colorTarget_ = colorTarget;
    depthTarget_ = depthTarget;
    return true;
}

bool MmdShadowResources::releaseTargets()
{
    // Shader assignments borrow these targets. Keep them until releaseShader
    // has completed; Maya provides no documented null-target unbind operation.
    if (!shutdownReady()) {
        return false;
    }
    if (targetManager_) {
        if (colorTarget_) targetManager_->releaseRenderTarget(colorTarget_);
        if (depthTarget_) targetManager_->releaseRenderTarget(depthTarget_);
    }
    colorTarget_ = nullptr;
    depthTarget_ = nullptr;
    targetManager_ = nullptr;
    return true;
}

MStatus MmdShadowResources::prepareFrameResources(
    const MSelectionList& selection, FrameResources& resources,
    bool requireEnabledSelfShadow)
{
    resources = FrameResources();
    if (!buildCasterLightMatrix(selection, resources) ||
        (requireEnabledSelfShadow && resources.selfShadowMode == 0)) {
        // Missing or invalid light/geometry disables only self-shadow drawing.
        return MS::kSuccess;
    }
    if ((!colorTarget_ || !depthTarget_) && !acquireTargets()) {
        return MS::kFailure;
    }
    resources.colorTarget = colorTarget_;
    resources.depthTarget = depthTarget_;
    resources.depthBias = kDefaultDepthBias;
    resources.ready = true;
    return MS::kSuccess;
}
