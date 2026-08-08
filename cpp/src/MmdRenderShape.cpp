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
    const std::vector<std::vector<float>>& submeshNormals,
    const std::vector<std::vector<float>>& submeshUvs,
    const std::vector<std::vector<uint32_t>>& submeshIndices,
    const std::vector<mmd::MmdRenderQueueInput>& queueInputs,
    double scale)
{
    auto reject = [](const std::string& reason) {
        MGlobal::displayError(
            MString("[mmdRenderShape] Geometry rejected: ") + reason.c_str());
        return false;
    };

    if (!std::isfinite(scale) || scale <= 0.0 || queueInputs.empty() ||
        submeshPositions.size() != submeshNormals.size() ||
        submeshPositions.size() != submeshUvs.size() ||
        submeshPositions.size() != submeshIndices.size()) {
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

    geometry_ = std::move(next);
    boundingBox_ = nextBounds;
    clearRenderItemWitness();
    clearMaterialBindingDiagnostics();
    return true;
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

    geometry_.queueInputs = std::move(nextInputs);
    geometry_.renderQueue = nextQueue;
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
    } else {
        stream << " geometry=pending";
    }
    return stream.str();
}

std::string MmdRenderShape::materialBindingDiagnosticsJson() const
{
    std::ostringstream stream;
    stream << "{\"version\":1,\"status\":"
           << jsonEscape(renderItemWitnessValid_ ? "ready" : "pending")
           << ",\"items\":[";
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
        setResult(MString(shape->materialBindingDiagnosticsJson().c_str()));
    } else {
        setResult(MString(shape->renderItemWitness().c_str()));
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
    setResult(MString(shape->renderItemWitness().c_str()));
    return MS::kSuccess;
}

bool MmdRenderQueueUpdateCommand::isUndoable() const
{
    return false;
}
