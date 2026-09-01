/**
 * @file MmdRenderShape.cpp
 * @brief Custom DAG shape and transient VP2 witness diagnostic command.
 */

#include "MmdRenderShape.h"

#include <maya/MArgDatabase.h>
#include <maya/MFnDependencyNode.h>
#include <maya/MFnData.h>
#include <maya/MFnMesh.h>
#include <maya/MFnNumericAttribute.h>
#include <maya/MFnTypedAttribute.h>
#include <maya/MFloatVectorArray.h>
#include <maya/MGlobal.h>
#include <maya/MItDependencyNodes.h>
#include <maya/MPointArray.h>
#include <maya/MPoint.h>
#include <maya/MPlug.h>
#include <maya/MSelectionList.h>
#include <maya/MSyntax.h>
#include <maya/MViewport2Renderer.h>

#include <algorithm>
#include <cmath>
#include <iomanip>
#include <sstream>
#include <utility>

namespace {

// 0x00128001-0x0012800B are already owned by the Python/C++ MMD nodes.
constexpr unsigned int kMmdRenderShapeId = 0x0012800C;
constexpr char kMmdRenderShapeClassification[] =
    "drawdb/geometry/mmdRenderShape";
constexpr char kMmdRenderShapeRegistrantId[] = "mayaMmdToolsMmdRenderShape";

MString mStringFromUtf8(const std::string& value)
{
    MString result;
    result.setUTF8(value.c_str());
    return result;
}

std::size_t passIndex(mmd::MmdDrawPass pass)
{
    return static_cast<std::size_t>(pass);
}

bool hasFiniteMaterial(const mmd::MmdRenderQueueInput& input)
{
    const auto finiteColor = [](const auto& color) {
        return std::all_of(color.begin(), color.end(), [](float value) {
            return std::isfinite(value);
        });
    };
    return std::isfinite(input.diffuseAlpha) &&
           std::isfinite(input.specularPower) &&
           std::isfinite(input.edgeAlpha) &&
           std::isfinite(input.edgeSize) &&
           finiteColor(input.diffuseColor) &&
           finiteColor(input.specularColor) &&
           finiteColor(input.ambientColor) &&
           finiteColor(input.edgeColor);
}

bool hasFinitePoint(const MPoint& point)
{
    return std::isfinite(point.x) && std::isfinite(point.y) &&
           std::isfinite(point.z) && std::isfinite(point.w);
}

bool hasFiniteVector(const MFloatVector& vector)
{
    return std::isfinite(vector.x) && std::isfinite(vector.y) &&
           std::isfinite(vector.z);
}

std::string jsonEscape(const std::string& value)
{
    std::ostringstream stream;
    stream << '"';
    for (const unsigned char character : value) {
        switch (character) {
        case '"':
            stream << "\\\"";
            break;
        case '\\':
            stream << "\\\\";
            break;
        case '\b':
            stream << "\\b";
            break;
        case '\f':
            stream << "\\f";
            break;
        case '\n':
            stream << "\\n";
            break;
        case '\r':
            stream << "\\r";
            break;
        case '\t':
            stream << "\\t";
            break;
        default:
            if (character < 0x20U) {
                stream << "\\u" << std::hex << std::setw(4)
                       << std::setfill('0') << static_cast<unsigned int>(character)
                       << std::dec << std::setfill(' ');
            } else {
                stream << static_cast<char>(character);
            }
            break;
        }
    }
    stream << '"';
    return stream.str();
}

void appendJsonString(std::ostringstream& stream,
                      const char* key,
                      const std::string& value,
                      bool& first)
{
    if (!first) {
        stream << ',';
    }
    first = false;
    stream << jsonEscape(key) << ':' << jsonEscape(value);
}

void appendJsonBool(std::ostringstream& stream,
                    const char* key,
                    bool value,
                    bool& first)
{
    if (!first) {
        stream << ',';
    }
    first = false;
    stream << jsonEscape(key) << ':' << (value ? "true" : "false");
}

void appendJsonNumber(std::ostringstream& stream,
                      const char* key,
                      std::size_t value,
                      bool& first)
{
    if (!first) {
        stream << ',';
    }
    first = false;
    stream << jsonEscape(key) << ':' << value;
}

void appendJsonFloat(std::ostringstream& stream,
                     const char* key,
                     float value,
                     bool& first)
{
    if (!first) {
        stream << ',';
    }
    first = false;
    stream << jsonEscape(key) << ':' << std::setprecision(9) << value;
}

void appendJsonInt(std::ostringstream& stream,
                   const char* key,
                   int value,
                   bool& first)
{
    if (!first) {
        stream << ',';
    }
    first = false;
    stream << jsonEscape(key) << ':' << value;
}

}  // namespace

const MTypeId MmdRenderShape::id(kMmdRenderShapeId);
const MString MmdRenderShape::drawDbClassification(
    kMmdRenderShapeClassification);
const MString MmdRenderShape::drawRegistrantId(kMmdRenderShapeRegistrantId);
MObject MmdRenderShape::aInputMesh;
MObject MmdRenderShape::aProxyReady;
MObject MmdRenderShape::aSourceVisibility;

MmdRenderShape::MmdRenderShape() = default;
MmdRenderShape::~MmdRenderShape() = default;

void* MmdRenderShape::creator()
{
    return new MmdRenderShape();
}

MStatus MmdRenderShape::initialize()
{
    MStatus status;
    MFnTypedAttribute typedAttribute;
    aInputMesh = typedAttribute.create(
        "inputMesh", "in", MFnData::kMesh, MObject::kNullObj, &status);
    if (!status) {
        return status;
    }
    typedAttribute.setStorable(true);
    typedAttribute.setWritable(true);
    typedAttribute.setReadable(false);
    status = addAttribute(aInputMesh);
    if (!status) {
        return status;
    }

    MFnNumericAttribute numericAttribute;
    aProxyReady = numericAttribute.create(
        "proxyReady", "pr", MFnNumericData::kBoolean, false, &status);
    if (!status) {
        return status;
    }
    // This input belongs exclusively to the shape lifecycle.  It is not
    // serialized, keyed, or exposed to authoring UI; a reopened scene starts
    // source-visible until VP2 commits current buffers again.
    numericAttribute.setWritable(true);
    numericAttribute.setReadable(true);
    numericAttribute.setStorable(false);
    numericAttribute.setKeyable(false);
    numericAttribute.setHidden(true);
    status = addAttribute(aProxyReady);
    if (!status) {
        return status;
    }

    aSourceVisibility = numericAttribute.create(
        "sourceVisibility", "sv", MFnNumericData::kBoolean, true, &status);
    if (!status) {
        return status;
    }
    // This is a transient source-control output.  It is intentionally not
    // storable, so a saved scene always reopens source-visible by default.
    // This output is evaluated by Maya's normal DG path from aProxyReady.
    // The lifecycle helper never writes the user-owned source visibility
    // destination directly.
    numericAttribute.setWritable(false);
    numericAttribute.setReadable(true);
    numericAttribute.setStorable(false);
    numericAttribute.setKeyable(false);
    status = addAttribute(aSourceVisibility);
    if (!status) {
        return status;
    }
    attributeAffects(aInputMesh, aSourceVisibility);
    attributeAffects(aProxyReady, aSourceVisibility);
    return MS::kSuccess;
}

void MmdRenderShape::postConstructor()
{
    // MPxSurfaceShape instances can receive shading assignments only after
    // Maya has created their internal DAG object.
    setRenderable(true);
}

MStatus MmdRenderShape::compute(const MPlug& plug, MDataBlock& data)
{
    if (plug != aSourceVisibility) {
        return MS::kUnknownParameter;
    }

    MStatus status;
    MDataHandle output = data.outputValue(aSourceVisibility, &status);
    if (!status) {
        return status;
    }
    const bool proxyReady = data.inputValue(aProxyReady, &status).asBool();
    if (!status) {
        return status;
    }
    output.setBool(!proxyReady);
    output.setClean();
    return MS::kSuccess;
}

MSelectionMask MmdRenderShape::getShapeSelectionMask() const
{
    return MSelectionMask(MSelectionMask::kSelectMeshes);
}

MmdRenderShape* MmdRenderShape::fromMObject(const MObject& object,
                                            MStatus* status)
{
    MStatus localStatus;
    MFnDependencyNode dependencyNode(object, &localStatus);
    if (!localStatus) {
        MGlobal::displayError(
            "[mmdRenderShape] MFnDependencyNode attach failed.");
        if (status) {
            *status = localStatus;
        }
        return nullptr;
    }

    MPxNode* node = dependencyNode.userNode(&localStatus);
    if (!localStatus) {
        MGlobal::displayError(
            MString("[mmdRenderShape] userNode lookup failed for type ") +
            dependencyNode.typeName().asChar());
        if (status) {
            *status = localStatus;
        }
        return nullptr;
    }

    MmdRenderShape* shape = dynamic_cast<MmdRenderShape*>(node);
    if (!shape) {
        localStatus = MS::kFailure;
    }
    if (status) {
        *status = localStatus;
    }
    return shape;
}

bool MmdRenderShape::prepareForPluginUnload()
{
    MStatus status;
    bool foundLiveProxy = false;
    // A plug-in surface shape is still enumerated as a dependency node here;
    // filtering by kPluginShape skips live instances in Maya 2024.
    MItDependencyNodes iterator(MFn::kDependencyNode, &status);
    if (!status) {
        return false;
    }

    for (; !iterator.isDone(&status);) {
        if (!status) {
            return false;
        }
        MStatus nodeStatus;
        const MObject node = iterator.thisNode(&nodeStatus);
        if (!nodeStatus) {
            return false;
        }

        MFnDependencyNode dependency(node, &nodeStatus);
        if (!nodeStatus) {
            return false;
        }
        if (dependency.typeId(&nodeStatus) == MmdRenderShape::id) {
            if (!nodeStatus) {
                return false;
            }
            MmdRenderShape* shape = fromMObject(node, &nodeStatus);
            if (!nodeStatus || !shape || !shape->setProxyReady(false)) {
                MGlobal::displayError(
                    "[mmdRenderShape] Failed to restore source visibility before plugin unload.");
                return false;
            }
            MPlug sourceVisibility(node, aSourceVisibility);
            bool sourceVisible = false;
            if (sourceVisibility.isNull() ||
                !sourceVisibility.getValue(sourceVisible) || !sourceVisible) {
                MGlobal::displayError(
                    "[mmdRenderShape] Source visibility output did not evaluate true before plugin unload.");
                return false;
            }
            foundLiveProxy = true;
        }

        status = iterator.next();
        if (!status) {
            return false;
        }
    }
    if (foundLiveProxy) {
        MGlobal::displayError(
            "[mmdRenderShape] Source visibility was restored, but live VP2 proxy nodes "
            "must be deleted before plugin unload.");
        return false;
    }
    return true;
}

bool MmdRenderShape::setProxyReady(bool ready)
{
    const bool nextReady = ready && geometryValid_ && geometryWitnessValid_ &&
                           renderItemWitnessValid_;
    if (aProxyReady.isNull() || aSourceVisibility.isNull()) {
        return false;
    }
    // supportsEvaluationManagerParallelUpdate() is false, so this lifecycle
    // transition is made on Maya's serial VP2/DG boundary.  Updating the
    // hidden input dirties aSourceVisibility through attributeAffects.
    MPlug readiness(thisMObject(), aProxyReady);
    if (readiness.isNull()) {
        return false;
    }
    MStatus status;
    const bool currentReady = readiness.asBool(&status);
    if (!status) {
        return false;
    }
    if (currentReady != nextReady && !readiness.setBool(nextReady)) {
        return false;
    }
    return true;
}

bool MmdRenderShape::isBounded() const
{
    return true;
}

MBoundingBox MmdRenderShape::boundingBox() const
{
    return boundingBox_;
}

bool MmdRenderShape::setMaterialSplitGeometry(
    const std::vector<std::vector<float>>& submeshPositions,
    const std::vector<std::vector<float>>& submeshNormals,
    const std::vector<std::vector<float>>& submeshUvs,
    const std::vector<std::vector<uint32_t>>& submeshIndices,
    const std::vector<mmd::MmdRenderQueueInput>& queueInputs,
    double scale)
{
    return setMaterialSplitGeometry(
        submeshPositions, submeshNormals, submeshUvs, submeshIndices,
        queueInputs, scale, {});
}

bool MmdRenderShape::setMaterialSplitGeometry(
    const std::vector<std::vector<float>>& submeshPositions,
    const std::vector<std::vector<float>>& submeshNormals,
    const std::vector<std::vector<float>>& submeshUvs,
    const std::vector<std::vector<uint32_t>>& submeshIndices,
    const std::vector<mmd::MmdRenderQueueInput>& queueInputs,
    double scale,
    const std::vector<std::vector<uint32_t>>& submeshSourceIndices)
{
    auto reject = [](const std::string& reason) {
        MGlobal::displayError(
            MString("[mmdRenderShape] Geometry rejected: ") + reason.c_str());
        return false;
    };

    if (!std::isfinite(scale) || scale <= 0.0 || queueInputs.empty() ||
        submeshPositions.size() != submeshNormals.size() ||
        submeshPositions.size() != submeshUvs.size() ||
        submeshPositions.size() != submeshIndices.size() ||
        (!submeshSourceIndices.empty() &&
         submeshPositions.size() != submeshSourceIndices.size())) {
        return reject("invalid scale, empty queue, or mismatched submesh buffers");
    }

    const std::vector<mmd::MmdRenderQueueEntry> renderQueue =
        mmd::buildMmdRenderQueue(queueInputs);

    for (const mmd::MmdRenderQueueInput& input : queueInputs) {
        if (!hasFiniteMaterial(input)) {
            return reject("material contains a non-finite value");
        }
    }

    for (std::size_t queueIndex = 0; queueIndex < renderQueue.size();
         ++queueIndex) {
        const mmd::MmdRenderQueueEntry& entry = renderQueue[queueIndex];
        if (passIndex(entry.pass) >
                static_cast<std::size_t>(mmd::MmdDrawPass::Transparent) ||
            entry.submeshIndex >= submeshPositions.size()) {
            return reject("queue entry " + std::to_string(queueIndex) +
                          " references an invalid pass or submesh (pass=" +
                          std::to_string(passIndex(entry.pass)) + ", submesh=" +
                          std::to_string(entry.submeshIndex) + ", positions=" +
                          std::to_string(submeshPositions.size()) + ")");
        }
        const std::vector<float>& positions =
            submeshPositions[entry.submeshIndex];
        const std::vector<float>& normals =
            submeshNormals[entry.submeshIndex];
        const std::vector<float>& uvs = submeshUvs[entry.submeshIndex];
        const std::vector<uint32_t>& indices =
            submeshIndices[entry.submeshIndex];
        if (positions.empty() || positions.size() % 3U != 0U ||
            indices.empty() || indices.size() % 3U != 0U) {
            return reject(
                "queue entry " + std::to_string(queueIndex) +
                " has positions=" + std::to_string(positions.size()) +
                " indices=" + std::to_string(indices.size()));
        }
        const std::size_t vertexCount = positions.size() / 3U;
        if ((!normals.empty() && normals.size() != positions.size()) ||
            (!uvs.empty() && uvs.size() != vertexCount * 2U)) {
            return reject("queue entry has mismatched normal or UV data");
        }
        const std::vector<uint32_t>* sourceIndices = nullptr;
        if (!submeshSourceIndices.empty()) {
            sourceIndices = &submeshSourceIndices[entry.submeshIndex];
            if (!sourceIndices->empty() && sourceIndices->size() != vertexCount) {
                return reject("queue entry has mismatched source-index data");
            }
        }
        for (std::size_t indexOffset = 0; indexOffset < indices.size();
             ++indexOffset) {
            const uint32_t index = indices[indexOffset];
            if (index >= vertexCount) {
                return reject(
                    "queue entry " + std::to_string(queueIndex) +
                    " index " + std::to_string(indexOffset) + " value " +
                    std::to_string(index) + " exceeds vertex count " +
                    std::to_string(vertexCount));
            }
        }
    }

    GeometryData next;
    next.queueInputs = queueInputs;
    next.renderQueue = renderQueue;
    next.queueGeometry.reserve(renderQueue.size());
    MBoundingBox nextBounds;
    bool hasBounds = false;

    for (const mmd::MmdRenderQueueEntry& entry : renderQueue) {
        const std::vector<float>& positions =
            submeshPositions[entry.submeshIndex];
        const std::vector<float>& normals =
            submeshNormals[entry.submeshIndex];
        const std::vector<float>& uvs = submeshUvs[entry.submeshIndex];
        const std::vector<uint32_t>& indices =
            submeshIndices[entry.submeshIndex];
        QueueGeometry queueGeometry;
        queueGeometry.entry = entry;
        queueGeometry.uvStreamAvailable = !uvs.empty();
        const mmd::MmdRenderQueueInput* material =
            mmd::findMmdRenderQueueInput(queueInputs, entry);
        if (!material) {
            return reject("queue entry has no material input");
        }
        queueGeometry.material = *material;
        queueGeometry.vertexOffset =
            static_cast<uint32_t>(next.positions.size() / 3U);
        const std::vector<uint32_t>* sourceIndices = nullptr;
        if (!submeshSourceIndices.empty()) {
            sourceIndices = &submeshSourceIndices[entry.submeshIndex];
        }
        const uint32_t fallbackSourceOffset =
            static_cast<uint32_t>(next.sourceVertexIndices.size());

        for (std::size_t i = 0; i < positions.size(); i += 3U) {
            const double x = static_cast<double>(positions[i]) * scale;
            const double y = static_cast<double>(positions[i + 1]) * scale;
            const double z = -static_cast<double>(positions[i + 2]) * scale;
            if (!std::isfinite(x) || !std::isfinite(y) || !std::isfinite(z)) {
                return reject("non-finite transformed position");
            }
            next.positions.push_back(static_cast<float>(x));
            next.positions.push_back(static_cast<float>(y));
            next.positions.push_back(static_cast<float>(z));
            next.sourceVertexIndices.push_back(
                sourceIndices && !sourceIndices->empty()
                    ? (*sourceIndices)[i / 3U]
                    : fallbackSourceOffset + static_cast<uint32_t>(i / 3U));

            if (normals.empty()) {
                next.normals.push_back(0.0F);
                next.normals.push_back(1.0F);
                next.normals.push_back(0.0F);
            } else {
                const double nx = static_cast<double>(normals[i]);
                const double ny = static_cast<double>(normals[i + 1U]);
                const double nz = static_cast<double>(normals[i + 2U]);
                const double length = std::sqrt(nx * nx + ny * ny + nz * nz);
                if (!std::isfinite(nx) || !std::isfinite(ny) ||
                    !std::isfinite(nz) || !std::isfinite(length) ||
                    length <= 0.0) {
                    return reject("non-finite or zero-length normal");
                }
                next.normals.push_back(static_cast<float>(nx / length));
                next.normals.push_back(static_cast<float>(ny / length));
                next.normals.push_back(static_cast<float>(-nz / length));
            }

            if (uvs.empty()) {
                next.uvs.push_back(0.0F);
                next.uvs.push_back(0.0F);
            } else {
                const float u = uvs[(i / 3U) * 2U];
                const float v = uvs[(i / 3U) * 2U + 1U];
                if (!std::isfinite(u) || !std::isfinite(v)) {
                    return reject("non-finite UV");
                }
                next.uvs.push_back(u);
                // Keep Maya-space UVs in the same convention as buildMesh;
                // MMDShader.fx flips V once in its vertex shader.
                next.uvs.push_back(1.0F - v);
            }
            const MPoint point(x, y, z);
            if (!hasBounds) {
                nextBounds = MBoundingBox(point, point);
                hasBounds = true;
            } else {
                nextBounds.expand(point);
            }
        }

        queueGeometry.indices.reserve(indices.size());
        // PMX indices are supplied in the same winding convention as the
        // regular MFnMesh path, so preserve its explicit winding reversal.
        for (std::size_t i = 0; i < indices.size(); i += 3U) {
            queueGeometry.indices.push_back(indices[i + 2U]);
            queueGeometry.indices.push_back(indices[i + 1U]);
            queueGeometry.indices.push_back(indices[i]);
        }
        next.queueGeometry.push_back(std::move(queueGeometry));
    }

    if (!hasBounds || next.positions.empty()) {
        return reject("no bounded position data");
    }

    // Prepare the immutable fallback before publishing any part of the new
    // geometry.  A later disconnect can therefore restore the exact authored
    // streams without reconstructing the material split.
    std::vector<float> nextStaticPositions = next.positions;
    std::vector<float> nextStaticNormals = next.normals;
    geometry_ = std::move(next);
    staticPositions_ = std::move(nextStaticPositions);
    staticNormals_ = std::move(nextStaticNormals);
    boundingBox_ = nextBounds;
    staticBoundingBox_ = nextBounds;
    geometryValid_ = true;
    evaluatedGeometryActive_ = false;
    evaluatedNormalRepairCount_ = 0U;
    evaluatedNormalStaticFallbackCount_ = 0U;
    clearRenderItemWitness();
    clearMaterialBindingDiagnostics();
    return true;
}

bool MmdRenderShape::updateEvaluatedMesh(const MObject& meshObject)
{
    auto reject = [this](const std::string& reason) {
        const bool reasonChanged = recordRenderFallbackReason(reason);
        if (reasonChanged) {
            MGlobal::displayError(
                MString("[mmdRenderShape] Evaluated mesh rejected: ") +
                reason.c_str());
        }
        // Keep the previous streams intact, but make them unavailable to the
        // override until a later DG update supplies a valid mesh (or the
        // input is disconnected and static geometry is explicitly restored).
        geometryValid_ = false;
        evaluatedNormalRepairCount_ = 0U;
        evaluatedNormalStaticFallbackCount_ = 0U;
        clearMaterialBindingDiagnostics();
        return false;
    };

    if (meshObject.isNull()) {
        return reject("input mesh data is null");
    }

    MStatus status;
    MFnMesh meshFn(meshObject, &status);
    if (!status) {
        return reject("input data is not a mesh");
    }

    const std::size_t renderVertexCount = geometry_.positions.size() / 3U;
    if (geometry_.positions.empty() || geometry_.positions.size() % 3U != 0U ||
        geometry_.sourceVertexIndices.size() != renderVertexCount) {
        return reject("static geometry has no complete source mapping");
    }

    std::size_t expectedSourceVertexCount = 0U;
    for (const uint32_t sourceIndex : geometry_.sourceVertexIndices) {
        const std::size_t nextCount = static_cast<std::size_t>(sourceIndex) + 1U;
        if (nextCount > expectedSourceVertexCount) {
            expectedSourceVertexCount = nextCount;
        }
    }
    if (expectedSourceVertexCount == 0U) {
        return reject("source mapping is empty");
    }

    MPointArray points;
    if (!meshFn.getPoints(points, MSpace::kObject)) {
        return reject("could not read object-space positions");
    }
    MFloatVectorArray normals;
    if (!meshFn.getVertexNormals(true, normals, MSpace::kObject)) {
        return reject("could not read object-space vertex normals");
    }
    if (static_cast<std::size_t>(points.length()) < expectedSourceVertexCount ||
        normals.length() != points.length()) {
        return reject("input mesh vertex/normal count does not match source topology");
    }

    // Maya can expose a zero or non-finite vertex normal for a degenerate
    // evaluated face.  Keep authored/evaluated normals whenever they are
    // usable.  Invalid slots use the immutable import-time stream instead of
    // triggering another normal calculation during every DG update.  The
    // repair list stays empty on the normal path.
    std::vector<float> nextPositions;
    std::vector<float> nextNormals;
    nextPositions.reserve(renderVertexCount * 3U);
    nextNormals.reserve(renderVertexCount * 3U);
    MBoundingBox nextBounds;
    bool hasBounds = false;
    std::vector<std::pair<std::size_t, uint32_t>> normalRepairRenderVertices;
    for (std::size_t renderVertex = 0U;
         renderVertex < geometry_.sourceVertexIndices.size();
         ++renderVertex) {
        const uint32_t sourceIndex =
            geometry_.sourceVertexIndices[renderVertex];
        if (sourceIndex >= points.length()) {
            return reject("source mapping index exceeds input mesh vertex count");
        }

        const MPoint& point = points[sourceIndex];
        const MFloatVector& inputNormal = normals[sourceIndex];
        if (!hasFinitePoint(point)) {
            return reject("input mesh contains a non-finite position");
        }

        const bool normalFinite = hasFiniteVector(inputNormal);
        const double inputNormalLength =
            normalFinite
                ? std::sqrt(static_cast<double>(inputNormal.x) * inputNormal.x +
                            static_cast<double>(inputNormal.y) * inputNormal.y +
                            static_cast<double>(inputNormal.z) * inputNormal.z)
                : 0.0;
        const bool needsRepair =
            !normalFinite || !std::isfinite(inputNormalLength) ||
            inputNormalLength <= 0.0;
        if (needsRepair) {
            normalRepairRenderVertices.emplace_back(renderVertex, sourceIndex);
            nextNormals.push_back(0.0F);
            nextNormals.push_back(0.0F);
            nextNormals.push_back(0.0F);
        } else {
            const float nx = static_cast<float>(inputNormal.x /
                                                 inputNormalLength);
            const float ny = static_cast<float>(inputNormal.y /
                                                 inputNormalLength);
            const float nz = static_cast<float>(inputNormal.z /
                                                 inputNormalLength);
            if (!std::isfinite(nx) || !std::isfinite(ny) ||
                !std::isfinite(nz)) {
                return reject("evaluated mesh values overflow float streams");
            }
            nextNormals.push_back(nx);
            nextNormals.push_back(ny);
            nextNormals.push_back(nz);
        }

        const float px = static_cast<float>(point.x);
        const float py = static_cast<float>(point.y);
        const float pz = static_cast<float>(point.z);
        if (!std::isfinite(px) || !std::isfinite(py) ||
            !std::isfinite(pz)) {
            return reject("evaluated mesh values overflow float streams");
        }
        nextPositions.push_back(px);
        nextPositions.push_back(py);
        nextPositions.push_back(pz);

        if (!hasBounds) {
            nextBounds = MBoundingBox(point, point);
            hasBounds = true;
        } else {
            nextBounds.expand(point);
        }
    }

    const std::size_t normalRepairCount = normalRepairRenderVertices.size();
    std::size_t staticFallbackCount = 0U;
    for (const auto& repairVertex : normalRepairRenderVertices) {
        const std::size_t renderVertex = repairVertex.first;
        const uint32_t sourceIndex = repairVertex.second;
        if (staticNormals_.size() != geometry_.sourceVertexIndices.size() * 3U) {
            return reject("normal repair failed for source vertex " +
                          std::to_string(sourceIndex));
        }
        const std::size_t staticOffset = renderVertex * 3U;
        const MFloatVector normal(
            staticNormals_[staticOffset],
            staticNormals_[staticOffset + 1U],
            staticNormals_[staticOffset + 2U]);
        ++staticFallbackCount;

        const double normalLength =
            std::sqrt(static_cast<double>(normal.x) * normal.x +
                      static_cast<double>(normal.y) * normal.y +
                      static_cast<double>(normal.z) * normal.z);
        if (!hasFiniteVector(normal) || !std::isfinite(normalLength) ||
            normalLength <= 0.0) {
            return reject("normal repair failed for source vertex " +
                          std::to_string(sourceIndex));
        }

        const float nx = static_cast<float>(normal.x / normalLength);
        const float ny = static_cast<float>(normal.y / normalLength);
        const float nz = static_cast<float>(normal.z / normalLength);
        if (!std::isfinite(nx) || !std::isfinite(ny) ||
            !std::isfinite(nz)) {
            return reject("evaluated mesh values overflow float streams");
        }
        const std::size_t normalOffset = renderVertex * 3U;
        nextNormals[normalOffset] = nx;
        nextNormals[normalOffset + 1U] = ny;
        nextNormals[normalOffset + 2U] = nz;
    }

    if (!hasBounds || nextPositions.size() != geometry_.positions.size() ||
        nextNormals.size() != geometry_.normals.size()) {
        return reject("evaluated mesh expansion changed render topology");
    }

    // Commit only after every source index and every expanded value has been
    // validated.  Queue/material/UV/index data is deliberately untouched.
    geometry_.positions = std::move(nextPositions);
    geometry_.normals = std::move(nextNormals);
    boundingBox_ = nextBounds;
    geometryValid_ = true;
    evaluatedGeometryActive_ = true;
    if (normalRepairCount != evaluatedNormalRepairCount_ ||
        staticFallbackCount != evaluatedNormalStaticFallbackCount_) {
        if (normalRepairCount > 0U) {
            std::ostringstream warning;
            warning << "[mmdRenderShape] Repaired " << normalRepairCount
                    << " invalid evaluated mesh normal(s) with "
                    << staticFallbackCount << " import-time static fallback(s).";
            MGlobal::displayWarning(MString(warning.str().c_str()));
        }
        evaluatedNormalRepairCount_ = normalRepairCount;
        evaluatedNormalStaticFallbackCount_ = staticFallbackCount;
    }
    clearRenderItemWitness();
    clearMaterialBindingDiagnostics();
    return true;
}

void MmdRenderShape::useStaticGeometry()
{
    if (!geometryValid_ || evaluatedGeometryActive_) {
        // Build both replacements before swapping either stream so a failed
        // allocation cannot expose a half-restored geometry state.
        std::vector<float> restoredPositions = staticPositions_;
        std::vector<float> restoredNormals = staticNormals_;
        geometry_.positions.swap(restoredPositions);
        geometry_.normals.swap(restoredNormals);
        boundingBox_ = staticBoundingBox_;
        geometryValid_ = true;
        evaluatedGeometryActive_ = false;
        evaluatedNormalRepairCount_ = 0U;
        evaluatedNormalStaticFallbackCount_ = 0U;
        clearRenderItemWitness();
        clearMaterialBindingDiagnostics();
    }
}

bool MmdRenderShape::hasValidGeometry() const
{
    return geometryValid_ && !geometry_.positions.empty();
}

bool MmdRenderShape::updateMaterialAlpha(std::size_t materialIndex,
                                         float diffuseAlpha)
{
    if (!std::isfinite(diffuseAlpha)) {
        MGlobal::displayError(
            "[mmdRenderShape] Queue alpha update rejected: non-finite alpha.");
        return false;
    }

    const float clampedAlpha = std::max(0.0F, std::min(1.0F, diffuseAlpha));
    std::vector<mmd::MmdRenderQueueInput> nextInputs = geometry_.queueInputs;
    bool found = false;
    for (mmd::MmdRenderQueueInput& input : nextInputs) {
        if (input.materialIndex != materialIndex) {
            continue;
        }
        input.diffuseAlpha = clampedAlpha;
        const bool explicitlyCutout =
            mmd::classifyMmdDrawPass(input.transparencyMode, 1.0F) ==
            mmd::MmdDrawPass::Cutout;
        if (!explicitlyCutout) {
            input.transparencyMode = clampedAlpha < 0.999F ? "blend" : "opaque";
        }
        found = true;
    }
    if (!found) {
        return false;
    }

    const std::vector<mmd::MmdRenderQueueEntry> nextQueue =
        mmd::buildMmdRenderQueue(nextInputs);
    std::vector<QueueGeometry> reordered;
    reordered.reserve(nextQueue.size());
    std::vector<bool> consumed(geometry_.queueGeometry.size(), false);
    for (const mmd::MmdRenderQueueEntry& entry : nextQueue) {
        std::size_t existingIndex = geometry_.queueGeometry.size();
        for (std::size_t candidateIndex = 0;
             candidateIndex < geometry_.queueGeometry.size(); ++candidateIndex) {
            const QueueGeometry& candidate =
                geometry_.queueGeometry[candidateIndex];
            if (!consumed[candidateIndex] &&
                candidate.entry.inputIndex == entry.inputIndex) {
                existingIndex = candidateIndex;
                break;
            }
        }
        if (existingIndex == geometry_.queueGeometry.size()) {
            MGlobal::displayError(
                "[mmdRenderShape] Queue alpha update lost a submesh.");
            return false;
        }
        consumed[existingIndex] = true;
        QueueGeometry item = std::move(geometry_.queueGeometry[existingIndex]);
        item.entry = entry;
        const mmd::MmdRenderQueueInput* material =
            mmd::findMmdRenderQueueInput(nextInputs, entry);
        if (!material) {
            MGlobal::displayError(
                "[mmdRenderShape] Queue alpha update lost material data.");
            return false;
        }
        item.material = *material;
        reordered.push_back(std::move(item));
    }
    if (reordered.size() != geometry_.queueGeometry.size()) {
        return false;
    }

    geometry_.queueInputs = std::move(nextInputs);
    geometry_.renderQueue = nextQueue;
    geometry_.queueGeometry = std::move(reordered);
    clearRenderItemWitness();
    clearMaterialBindingDiagnostics();
    return true;
}

bool MmdRenderShape::reindexMaterialQueue(std::size_t firstIndex,
                                           std::size_t secondIndex)
{
    if (firstIndex == secondIndex ||
        (firstIndex < secondIndex ? secondIndex - firstIndex
                                   : firstIndex - secondIndex) != 1U) {
        return false;
    }

    std::vector<mmd::MmdRenderQueueInput> nextInputs = geometry_.queueInputs;
    bool foundFirst = false;
    bool foundSecond = false;
    for (mmd::MmdRenderQueueInput& input : nextInputs) {
        if (input.materialIndex == firstIndex) {
            input.materialIndex = secondIndex;
            foundFirst = true;
        } else if (input.materialIndex == secondIndex) {
            input.materialIndex = firstIndex;
            foundSecond = true;
        }
    }
    if (!foundFirst || !foundSecond) {
        return false;
    }

    std::vector<mmd::MmdRenderQueueEntry> nextQueue =
        mmd::buildMmdRenderQueue(nextInputs);
    std::vector<bool> consumed(geometry_.queueGeometry.size(), false);
    std::vector<std::size_t> sourceIndices;
    sourceIndices.reserve(nextQueue.size());
    for (const mmd::MmdRenderQueueEntry& entry : nextQueue) {
        std::size_t existingIndex = geometry_.queueGeometry.size();
        for (std::size_t candidateIndex = 0;
             candidateIndex < geometry_.queueGeometry.size(); ++candidateIndex) {
            const QueueGeometry& candidate =
                geometry_.queueGeometry[candidateIndex];
            if (!consumed[candidateIndex] &&
                candidate.entry.inputIndex == entry.inputIndex) {
                existingIndex = candidateIndex;
                break;
            }
        }
        if (existingIndex == geometry_.queueGeometry.size()) {
            return false;
        }
        if (!mmd::findMmdRenderQueueInput(nextInputs, entry)) {
            return false;
        }
        consumed[existingIndex] = true;
        sourceIndices.push_back(existingIndex);
    }

    if (sourceIndices.size() != geometry_.queueGeometry.size() ||
        std::any_of(consumed.begin(), consumed.end(),
                    [](bool value) { return !value; })) {
        return false;
    }

    // Build the complete reordered value before touching geometry_.  Copies
    // keep every failure point above (including allocation) transactional;
    // the final vector moves below are noexcept container swaps.
    std::vector<QueueGeometry> reordered;
    reordered.reserve(sourceIndices.size());
    for (std::size_t queueIndex = 0; queueIndex < nextQueue.size();
         ++queueIndex) {
        QueueGeometry item = geometry_.queueGeometry[sourceIndices[queueIndex]];
        item.entry = nextQueue[queueIndex];
        const mmd::MmdRenderQueueInput* material =
            mmd::findMmdRenderQueueInput(nextInputs, nextQueue[queueIndex]);
        if (!material) {
            return false;
        }
        item.material = *material;
        reordered.push_back(std::move(item));
    }

    geometry_.queueInputs = std::move(nextInputs);
    geometry_.renderQueue = std::move(nextQueue);
    geometry_.queueGeometry = std::move(reordered);
    clearRenderItemWitness();
    clearMaterialBindingDiagnostics();
    return true;
}

const MmdRenderShape::GeometryData& MmdRenderShape::geometry() const
{
    return geometry_;
}

bool MmdRenderShape::hasPassGeometry(mmd::MmdDrawPass pass) const
{
    return std::any_of(
        geometry_.queueGeometry.begin(), geometry_.queueGeometry.end(),
        [pass](const QueueGeometry& item) { return item.entry.pass == pass; });
}

void MmdRenderShape::clearRenderItemWitness()
{
    renderItemWitnessValid_ = false;
    renderItemWitnessEntries_.clear();
    geometryWitnessValid_ = false;
    geometryWitnessVertexCount_ = 0U;
    geometryWitnessIndexCount_ = 0U;
    geometryWitnessDescriptorSummary_.clear();
    setProxyReady(false);
}

void MmdRenderShape::clearMaterialBindingDiagnostics()
{
    materialBindingDiagnostics_.clear();
}

void MmdRenderShape::recordRenderItemWitness(
    const std::vector<mmd::MmdRenderQueueEntry>& entries)
{
    renderItemWitnessEntries_ = entries;
    renderItemWitnessValid_ = true;
    renderFallbackReason_.clear();
}

bool MmdRenderShape::recordRenderFallbackReason(const std::string& reason)
{
    const bool changed = renderFallbackReason_ != reason;
    clearRenderItemWitness();
    renderFallbackReason_ = reason;
    return changed;
}

void MmdRenderShape::recordMaterialBindingDiagnostic(
    const MaterialBindingDiagnostic& diagnostic)
{
    materialBindingDiagnostics_.push_back(diagnostic);
}

void MmdRenderShape::recordGeometryWitness(std::size_t vertexCount,
                                           std::size_t indexCount,
                                           const std::string& descriptorSummary)
{
    geometryWitnessVertexCount_ = vertexCount;
    geometryWitnessIndexCount_ = indexCount;
    geometryWitnessDescriptorSummary_ = descriptorSummary;
    geometryWitnessValid_ = true;
}

std::string MmdRenderShape::renderItemWitness() const
{
    if (!renderItemWitnessValid_) {
        if (!renderFallbackReason_.empty()) {
            return "failed reason=" + renderFallbackReason_;
        }
        return "pending";
    }

    std::ostringstream stream;
    stream << "ready items=" << renderItemWitnessEntries_.size() << " order=";
    for (std::size_t i = 0; i < renderItemWitnessEntries_.size(); ++i) {
        if (i != 0U) {
            stream << ',';
        }
        const mmd::MmdRenderQueueEntry& entry = renderItemWitnessEntries_[i];
        stream << mmd::mmdDrawPassName(entry.pass) << "[m"
               << entry.materialIndex << "/s" << entry.submeshIndex << "]";
    }
    if (geometryWitnessValid_) {
        stream << " geometry=vertices=" << geometryWitnessVertexCount_
               << ",indices=" << geometryWitnessIndexCount_;
        if (!geometryWitnessDescriptorSummary_.empty()) {
            stream << ",streams=" << geometryWitnessDescriptorSummary_;
        }
        stream << ",repairedNormals=" << evaluatedNormalRepairCount_;
        stream << ",staticNormalFallbacks="
               << evaluatedNormalStaticFallbackCount_;
    } else {
        stream << " geometry=pending";
    }
    return stream.str();
}

std::string MmdRenderShape::materialBindingDiagnosticsJson() const
{
    std::ostringstream stream;
    const char* status = renderItemWitnessValid_
                             ? "ready"
                             : (renderFallbackReason_.empty() ? "pending"
                                                              : "failed");
    stream << "{\"version\":1,\"status\":"
           << jsonEscape(status) << ",\"fallbackReason\":"
           << jsonEscape(renderFallbackReason_) << ",\"items\":[";
    for (std::size_t index = 0; index < materialBindingDiagnostics_.size();
         ++index) {
        if (index != 0U) {
            stream << ',';
        }
        const MaterialBindingDiagnostic& diagnostic =
            materialBindingDiagnostics_[index];
        stream << '{';
        bool first = true;
        appendJsonNumber(stream, "queueIndex", diagnostic.queueIndex, first);
        appendJsonNumber(stream, "materialIndex", diagnostic.materialIndex,
                         first);
        appendJsonNumber(stream, "submeshIndex", diagnostic.submeshIndex,
                         first);
        appendJsonString(stream, "renderItemName", diagnostic.renderItemName,
                         first);
        appendJsonString(stream, "pass", diagnostic.pass, first);
        appendJsonBool(stream, "outline", diagnostic.outline, first);
        appendJsonString(stream, "technique", diagnostic.technique, first);
        appendJsonBool(stream, "uvStreamAvailable",
                       diagnostic.uvStreamAvailable, first);
        appendJsonFloat(stream, "diffuseAlpha", diagnostic.diffuseAlpha, first);
        appendJsonBool(stream, "textureAlphaBlend",
                       diagnostic.textureAlphaBlend, first);
        appendJsonBool(stream, "effectiveTransparent",
                       diagnostic.effectiveTransparent, first);
        appendJsonBool(stream, "selfShadowMap", diagnostic.selfShadowMap,
                       first);
        appendJsonBool(stream, "selfShadow", diagnostic.selfShadow, first);
        appendJsonBool(stream, "casterEligible", diagnostic.casterEligible,
                       first);
        appendJsonBool(stream, "casterRenderFilterParticipation",
                       diagnostic.casterRenderFilterParticipation, first);
        appendJsonString(stream, "renderItemType",
                         diagnostic.renderItemType, first);
        appendJsonString(stream, "casterExclusionReason",
                         diagnostic.casterExclusionReason, first);
        appendJsonString(stream, "mainTexturePath",
                         diagnostic.mainTexturePath, first);
        appendJsonString(stream, "sphereTexturePath",
                         diagnostic.sphereTexturePath, first);
        appendJsonString(stream, "toonTexturePath",
                         diagnostic.toonTexturePath, first);
        appendJsonString(stream, "toonTextureSource",
                         diagnostic.toonTextureSource, first);
        appendJsonBool(stream, "mainTextureRequested",
                       diagnostic.mainTextureRequested, first);
        appendJsonBool(stream, "sphereTextureRequested",
                       diagnostic.sphereTextureRequested, first);
        appendJsonBool(stream, "toonTextureRequested",
                       diagnostic.toonTextureRequested, first);
        appendJsonBool(stream, "mainTextureAcquired",
                       diagnostic.mainTextureAcquired, first);
        appendJsonBool(stream, "sphereTextureAcquired",
                       diagnostic.sphereTextureAcquired, first);
        appendJsonBool(stream, "toonTextureAcquired",
                       diagnostic.toonTextureAcquired, first);
        appendJsonBool(stream, "scalarParameterBindingSuccess",
                       diagnostic.scalarParameterBindingSuccess, first);
        appendJsonBool(stream, "mainTextureBindingSuccess",
                       diagnostic.mainTextureBindingSuccess, first);
        appendJsonBool(stream, "sphereTextureBindingSuccess",
                       diagnostic.sphereTextureBindingSuccess, first);
        appendJsonBool(stream, "toonTextureBindingSuccess",
                       diagnostic.toonTextureBindingSuccess, first);
        appendJsonBool(stream, "switchParameterBindingSuccess",
                       diagnostic.switchParameterBindingSuccess, first);
        appendJsonBool(stream, "shaderAvailable",
                       diagnostic.shaderAvailable, first);
        appendJsonBool(stream, "parameterBindingSuccess",
                       diagnostic.parameterBindingSuccess, first);
        appendJsonBool(stream, "shaderAssignmentSuccess",
                       diagnostic.shaderAssignmentSuccess, first);
        appendJsonBool(stream, "bindingSuccess", diagnostic.bindingSuccess,
                       first);
        appendJsonInt(stream, "sphereMode", diagnostic.sphereMode, first);
        stream << '}';
    }
    stream << "]}";
    return stream.str();
}

void* MmdRenderWitnessCommand::creator()
{
    return new MmdRenderWitnessCommand();
}

MSyntax MmdRenderWitnessCommand::newSyntax()
{
    MSyntax syntax;
    syntax.addFlag("-n", "-node", MSyntax::kString);
    syntax.addFlag("-j", "-json", MSyntax::kBoolean);
    syntax.enableEdit(false);
    return syntax;
}

MStatus MmdRenderWitnessCommand::doIt(const MArgList& args)
{
    MArgDatabase argData(newSyntax(), args);
    if (!argData.isFlagSet("-node")) {
        MGlobal::displayError(
            "[mmdRenderWitness] Required flag missing: -node/-n <shape>");
        return MS::kFailure;
    }

    MSelectionList selection;
    const MString nodeName = argData.flagArgumentString("-node", 0);
    MStatus status = selection.add(nodeName);
    if (!status || selection.length() == 0U) {
        MGlobal::displayError(MString("[mmdRenderWitness] Node not found: ") +
                              nodeName);
        return MS::kFailure;
    }

    MObject node;
    status = selection.getDependNode(0U, node);
    if (!status) {
        return status;
    }
    MmdRenderShape* shape = MmdRenderShape::fromMObject(node, &status);
    if (!status || !shape) {
        MGlobal::displayError(
            "[mmdRenderWitness] Node is not an mmdRenderShape.");
        return MS::kFailure;
    }

    if (argData.isFlagSet("-json") &&
        argData.flagArgumentBool("-json", 0)) {
        setResult(mStringFromUtf8(shape->materialBindingDiagnosticsJson()));
    } else {
        setResult(mStringFromUtf8(shape->renderItemWitness()));
    }
    return MS::kSuccess;
}

bool MmdRenderWitnessCommand::isUndoable() const
{
    return false;
}

void* MmdRenderQueueUpdateCommand::creator()
{
    return new MmdRenderQueueUpdateCommand();
}

MSyntax MmdRenderQueueUpdateCommand::newSyntax()
{
    MSyntax syntax;
    syntax.addFlag("-n", "-node", MSyntax::kString);
    syntax.addFlag("-m", "-materialIndex", MSyntax::kLong);
    syntax.addFlag("-a", "-alpha", MSyntax::kDouble);
    syntax.enableEdit(false);
    return syntax;
}

MStatus MmdRenderQueueUpdateCommand::doIt(const MArgList& args)
{
    MArgDatabase argData(newSyntax(), args);
    if (!argData.isFlagSet("-node") || !argData.isFlagSet("-materialIndex") ||
        !argData.isFlagSet("-alpha")) {
        MGlobal::displayError(
            "[mmdRenderQueueUpdate] Required flags: -node, -materialIndex, -alpha");
        return MS::kFailure;
    }

    MSelectionList selection;
    const MString nodeName = argData.flagArgumentString("-node", 0);
    MStatus status = selection.add(nodeName);
    if (!status || selection.length() == 0U) {
        MGlobal::displayError(MString("[mmdRenderQueueUpdate] Node not found: ") +
                              nodeName);
        return MS::kFailure;
    }

    MObject node;
    status = selection.getDependNode(0U, node);
    if (!status) {
        return status;
    }
    MmdRenderShape* shape = MmdRenderShape::fromMObject(node, &status);
    if (!status || !shape) {
        MGlobal::displayError(
            "[mmdRenderQueueUpdate] Node is not an mmdRenderShape.");
        return MS::kFailure;
    }

    const int materialIndex = argData.flagArgumentInt("-materialIndex", 0);
    const double alpha = argData.flagArgumentDouble("-alpha", 0);
    if (materialIndex < 0 || !shape->updateMaterialAlpha(
                                  static_cast<std::size_t>(materialIndex),
                                  static_cast<float>(alpha))) {
        MGlobal::displayError(
            "[mmdRenderQueueUpdate] Material alpha update was rejected.");
        return MS::kFailure;
    }

    MHWRender::MRenderer::setGeometryDrawDirty(node, true);
    setResult(mStringFromUtf8(shape->renderItemWitness()));
    return MS::kSuccess;
}

bool MmdRenderQueueUpdateCommand::isUndoable() const
{
    return false;
}

void* MmdRenderQueueReindexCommand::creator()
{
    return new MmdRenderQueueReindexCommand();
}

MSyntax MmdRenderQueueReindexCommand::newSyntax()
{
    MSyntax syntax;
    syntax.addFlag("-n", "-node", MSyntax::kString);
    syntax.addFlag("-f", "-firstMaterialIndex", MSyntax::kLong);
    syntax.addFlag("-s", "-secondMaterialIndex", MSyntax::kLong);
    syntax.enableEdit(false);
    return syntax;
}

MStatus MmdRenderQueueReindexCommand::doIt(const MArgList& args)
{
    MArgDatabase argData(newSyntax(), args);
    if (!argData.isFlagSet("-node") ||
        !argData.isFlagSet("-firstMaterialIndex") ||
        !argData.isFlagSet("-secondMaterialIndex")) {
        MGlobal::displayError(
            "[mmdRenderQueueReindex] Required flags: -node, -firstMaterialIndex, -secondMaterialIndex");
        return MS::kFailure;
    }

    MSelectionList selection;
    const MString nodeName = argData.flagArgumentString("-node", 0);
    MStatus status = selection.add(nodeName);
    if (!status || selection.length() == 0U) {
        MGlobal::displayError(MString("[mmdRenderQueueReindex] Node not found: ") +
                              nodeName);
        return MS::kFailure;
    }
    MObject node;
    status = selection.getDependNode(0U, node);
    if (!status) {
        return status;
    }
    const int firstIndex = argData.flagArgumentInt("-firstMaterialIndex", 0);
    const int secondIndex = argData.flagArgumentInt("-secondMaterialIndex", 0);
    if (firstIndex < 0 || secondIndex < 0 || firstIndex == secondIndex ||
        (firstIndex < secondIndex ? secondIndex - firstIndex
                                   : firstIndex - secondIndex) != 1) {
        MGlobal::displayError(
            "[mmdRenderQueueReindex] Material queue reindex was rejected.");
        return MS::kFailure;
    }

    nodeHandle_ = MObjectHandle(node);
    firstIndex_ = static_cast<std::size_t>(firstIndex);
    secondIndex_ = static_cast<std::size_t>(secondIndex);
    return applySwap();
}

MStatus MmdRenderQueueReindexCommand::redoIt()
{
    return applySwap();
}

MStatus MmdRenderQueueReindexCommand::undoIt()
{
    // The queue operation is an involution: swapping the same adjacent pair
    // restores the exact prior ordering without retaining mutable geometry.
    return applySwap();
}

MStatus MmdRenderQueueReindexCommand::applySwap()
{
    if (!nodeHandle_.isValid() || !nodeHandle_.isAlive()) {
        MGlobal::displayError(
            "[mmdRenderQueueReindex] Target mmdRenderShape is no longer alive.");
        return MS::kFailure;
    }
    MStatus status;
    const MObject node = nodeHandle_.object();
    MmdRenderShape* shape = MmdRenderShape::fromMObject(node, &status);
    if (!status || !shape) {
        MGlobal::displayError(
            "[mmdRenderQueueReindex] Node is not an mmdRenderShape.");
        return MS::kFailure;
    }
    if (!shape->reindexMaterialQueue(firstIndex_, secondIndex_)) {
        MGlobal::displayError(
            "[mmdRenderQueueReindex] Material queue reindex was rejected.");
        return MS::kFailure;
    }

    MHWRender::MRenderer::setGeometryDrawDirty(node, true);
    setResult(mStringFromUtf8(shape->renderItemWitness()));
    return MS::kSuccess;
}

bool MmdRenderQueueReindexCommand::isUndoable() const
{
    return true;
}
