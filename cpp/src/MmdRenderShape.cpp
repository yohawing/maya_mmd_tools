/**
 * @file MmdRenderShape.cpp
 * @brief Custom DAG shape and transient VP2 witness diagnostic command.
 */

#include "MmdRenderShape.h"

#include <maya/MArgDatabase.h>
#include <maya/MFnDependencyNode.h>
#include <maya/MGlobal.h>
#include <maya/MPoint.h>
#include <maya/MSelectionList.h>
#include <maya/MSyntax.h>

#include <algorithm>
#include <cmath>
#include <sstream>

namespace {

// 0x00128001-0x0012800B are already owned by the Python/C++ MMD nodes.
constexpr unsigned int kMmdRenderShapeId = 0x0012800C;
constexpr char kMmdRenderShapeClassification[] =
    "drawdb/geometry/mmdRenderShape";
constexpr char kMmdRenderShapeRegistrantId[] = "mayaMmdToolsMmdRenderShape";

std::size_t passIndex(mmd::MmdDrawPass pass)
{
    return static_cast<std::size_t>(pass);
}

}  // namespace

const MTypeId MmdRenderShape::id(kMmdRenderShapeId);
const MString MmdRenderShape::drawDbClassification(
    kMmdRenderShapeClassification);
const MString MmdRenderShape::drawRegistrantId(kMmdRenderShapeRegistrantId);

MmdRenderShape::MmdRenderShape() = default;
MmdRenderShape::~MmdRenderShape() = default;

void* MmdRenderShape::creator()
{
    return new MmdRenderShape();
}

MStatus MmdRenderShape::initialize()
{
    // Geometry is intentionally transient for this first ownership witness.
    // No built-in mesh attributes are added, so this node cannot accidentally
    // enter the regular MFnMesh importer path or receive a mesh override.
    return MS::kSuccess;
}

void MmdRenderShape::postConstructor()
{
    // MPxSurfaceShape instances can receive shading assignments only after
    // Maya has created their internal DAG object.
    setRenderable(true);
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
    const std::vector<std::vector<uint32_t>>& submeshIndices,
    const std::vector<mmd::MmdRenderQueueEntry>& renderQueue,
    double scale)
{
    auto reject = [](const std::string& reason) {
        MGlobal::displayError(
            MString("[mmdRenderShape] Geometry rejected: ") + reason.c_str());
        return false;
    };

    if (!std::isfinite(scale) || scale <= 0.0 || renderQueue.empty() ||
        submeshPositions.size() != submeshIndices.size()) {
        return reject("invalid scale, empty queue, or mismatched submesh buffers");
    }

    for (std::size_t queueIndex = 0; queueIndex < renderQueue.size();
         ++queueIndex) {
        const mmd::MmdRenderQueueEntry& entry = renderQueue[queueIndex];
        if (passIndex(entry.pass) >= geometry_.indices.size() ||
            entry.submeshIndex >= submeshPositions.size()) {
            return reject("queue entry " + std::to_string(queueIndex) +
                          " references an invalid pass or submesh");
        }
        const std::vector<float>& positions =
            submeshPositions[entry.submeshIndex];
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
    next.renderQueue = renderQueue;
    MBoundingBox nextBounds;
    bool hasBounds = false;

    for (const mmd::MmdRenderQueueEntry& entry : renderQueue) {
        const std::vector<float>& positions =
            submeshPositions[entry.submeshIndex];
        const std::vector<uint32_t>& indices =
            submeshIndices[entry.submeshIndex];
        const uint32_t vertexOffset =
            static_cast<uint32_t>(next.positions.size() / 3U);

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
            const MPoint point(x, y, z);
            if (!hasBounds) {
                nextBounds = MBoundingBox(point, point);
                hasBounds = true;
            } else {
                nextBounds.expand(point);
            }
        }

        std::vector<uint32_t>& passIndices = next.indices[passIndex(entry.pass)];
        passIndices.reserve(passIndices.size() + indices.size());
        // PMX indices are supplied in the same winding convention as the
        // regular MFnMesh path, so preserve its explicit winding reversal.
        for (std::size_t i = 0; i < indices.size(); i += 3U) {
            passIndices.push_back(vertexOffset + indices[i + 2U]);
            passIndices.push_back(vertexOffset + indices[i + 1U]);
            passIndices.push_back(vertexOffset + indices[i]);
        }
    }

    if (!hasBounds || next.positions.empty()) {
        return reject("no bounded position data");
    }

    geometry_ = std::move(next);
    boundingBox_ = nextBounds;
    clearRenderItemWitness();
    return true;
}

const MmdRenderShape::GeometryData& MmdRenderShape::geometry() const
{
    return geometry_;
}

bool MmdRenderShape::hasPassGeometry(mmd::MmdDrawPass pass) const
{
    return !geometry_.indices[passIndex(pass)].empty();
}

void MmdRenderShape::clearRenderItemWitness()
{
    renderItemWitnessValid_ = false;
    renderItemWitnessPasses_.clear();
    geometryWitnessValid_ = false;
    geometryWitnessVertexCount_ = 0U;
    geometryWitnessIndexCount_ = 0U;
    geometryWitnessDescriptorSummary_.clear();
}

void MmdRenderShape::recordRenderItemWitness(
    const std::vector<mmd::MmdDrawPass>& passes)
{
    renderItemWitnessPasses_ = passes;
    renderItemWitnessValid_ = true;
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
        return "pending";
    }

    std::ostringstream stream;
    stream << "ready items=" << renderItemWitnessPasses_.size() << " order=";
    for (std::size_t i = 0; i < renderItemWitnessPasses_.size(); ++i) {
        if (i != 0U) {
            stream << ',';
        }
        stream << mmd::mmdDrawPassName(renderItemWitnessPasses_[i]);
    }
    if (geometryWitnessValid_) {
        stream << " geometry=vertices=" << geometryWitnessVertexCount_
               << ",indices=" << geometryWitnessIndexCount_;
        if (!geometryWitnessDescriptorSummary_.empty()) {
            stream << ",streams=" << geometryWitnessDescriptorSummary_;
        }
    } else {
        stream << " geometry=pending";
    }
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

    setResult(MString(shape->renderItemWitness().c_str()));
    return MS::kSuccess;
}

bool MmdRenderWitnessCommand::isUndoable() const
{
    return false;
}
