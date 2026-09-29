/**
 * @file MmdRenderShape.cpp
 * @brief Custom DAG shape and transient VP2 witness diagnostic command.
 */

#include "MmdRenderShape.h"
#include "MmdRenderProfiler.h"

#include <maya/MArgDatabase.h>
#include <maya/MFnAttribute.h>
#include <maya/MFnCompoundAttribute.h>
#include <maya/MFnDependencyNode.h>
#include <maya/MFnData.h>
#include <maya/MFnMesh.h>
#include <maya/MFnNumericAttribute.h>
#include <maya/MFnTypedAttribute.h>
#include <maya/MFloatVectorArray.h>
#include <maya/MGlobal.h>
#include <maya/MItDependencyNodes.h>
#include <maya/MIntArray.h>
#include <maya/MObjectArray.h>
#include <maya/MPointArray.h>
#include <maya/MPoint.h>
#include <maya/MPlug.h>
#include <maya/MEvaluationNode.h>
#include <maya/MSelectionList.h>
#include <maya/MSyntax.h>
#include <maya/MViewport2Renderer.h>

#include <algorithm>
#include <cmath>
#include <iomanip>
#include <limits>
#include <sstream>
#include <unordered_map>
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
           finiteColor(input.edgeColor) &&
           finiteColor(input.mainTextureMultiply) &&
           finiteColor(input.mainTextureAdd) &&
           finiteColor(input.sphereTextureMultiply) &&
           finiteColor(input.sphereTextureAdd) &&
           finiteColor(input.toonTextureMultiply) &&
           finiteColor(input.toonTextureAdd);
}

void configureMaterialAttribute(MFnAttribute& attribute)
{
    attribute.setWritable(true);
    attribute.setReadable(true);
    attribute.setStorable(true);
    attribute.setKeyable(false);
    attribute.setDisconnectBehavior(MFnAttribute::kNothing);
}

MObject createMaterialFloat3Attribute(const char* name,
                                      const char* shortName,
                                      float defaultValue,
                                      MStatus* status)
{
    MFnNumericAttribute numeric;
    MObject attribute = numeric.create(
        name, shortName, MFnNumericData::k3Float, defaultValue, status);
    if (status && *status) {
        configureMaterialAttribute(numeric);
    }
    return attribute;
}

MObject createMaterialFloatAttribute(const char* name,
                                     const char* shortName,
                                     float defaultValue,
                                     MStatus* status)
{
    MFnNumericAttribute numeric;
    MObject attribute = numeric.create(
        name, shortName, MFnNumericData::kFloat, defaultValue, status);
    if (status && *status) {
        configureMaterialAttribute(numeric);
    }
    return attribute;
}

MObject createMaterialFloat4Attribute(const char* name,
                                      const char* shortName,
                                      float defaultValue,
                                      MStatus* status)
{
    MFnNumericAttribute numeric;
    MObject components[4];
    const char* suffixes[4] = {"R", "G", "B", "A"};
    for (unsigned int index = 0U; index < 4U; ++index) {
        const MString componentName = MString(name) + suffixes[index];
        const MString componentShortName =
            MString(shortName) + suffixes[index];
        components[index] = numeric.create(
            componentName, componentShortName, MFnNumericData::kFloat,
            defaultValue, status);
        if (!status || !*status) {
            return MObject::kNullObj;
        }
        configureMaterialAttribute(numeric);
    }

    MFnCompoundAttribute compound;
    MObject attribute = compound.create(name, shortName, status);
    if (!status || !*status) {
        return MObject::kNullObj;
    }
    for (const MObject& component : components) {
        *status = compound.addChild(component);
        if (!*status) {
            return MObject::kNullObj;
        }
    }
    configureMaterialAttribute(compound);
    return attribute;
}

bool isMaterialValuesPlug(const MPlug& plug)
{
    MPlug current = plug;
    while (!current.isNull()) {
        if (current.attribute() == MmdRenderShape::aMaterialValues ||
            current.attribute() == MmdRenderShape::aMaterialSettings) {
            return true;
        }
        MStatus status;
        const MPlug parent = current.parent(&status);
        if (!status || parent.isNull()) {
            return false;
        }
        current = parent;
    }
    return false;
}

bool readMaterialValuesChild(const MPlug& element,
                             unsigned int childIndex,
                             float* values,
                             unsigned int valueCount)
{
    MStatus status;
    const MPlug field = element.child(childIndex, &status);
    if (!status || field.isNull()) {
        return false;
    }
    if (valueCount == 1U) {
        const float value = field.asFloat(&status);
        if (!status || !std::isfinite(value)) {
            return false;
        }
        values[0] = value;
        return true;
    }

    const unsigned int componentCount = field.numChildren(&status);
    if (!status || componentCount != valueCount) {
        return false;
    }
    for (unsigned int index = 0U; index < valueCount; ++index) {
        const MPlug component = field.child(index, &status);
        if (!status || component.isNull()) {
            return false;
        }
        const float value = component.asFloat(&status);
        if (!status || !std::isfinite(value)) {
            return false;
        }
        values[index] = value;
    }
    return true;
}

enum MaterialValuesChild : unsigned int {
    kDiffuseColorRGB = 0U,
    kSpecularColor,
    kShininess,
    kAmbientColor,
    kEdgeColorRGB,
    kEdgeColorA,
    kEdgeSize,
    kMainTextureMultiply,
    kMainTextureAdd,
    kSphereTextureMultiply,
    kSphereTextureAdd,
    kToonTextureMultiply,
    kToonTextureAdd,
};

bool readMaterialValuesRecord(const MPlug& element,
                              mmd::MmdRenderQueueInput& material)
{
    return
        readMaterialValuesChild(element, kDiffuseColorRGB,
                                material.diffuseColor.data(), 3U) &&
        readMaterialValuesChild(element, kSpecularColor,
                                material.specularColor.data(), 3U) &&
        readMaterialValuesChild(element, kShininess, &material.specularPower,
                                1U) &&
        readMaterialValuesChild(element, kAmbientColor,
                                material.ambientColor.data(), 3U) &&
        readMaterialValuesChild(element, kEdgeColorRGB,
                                material.edgeColor.data(), 3U) &&
        readMaterialValuesChild(element, kEdgeColorA, &material.edgeAlpha,
                                1U) &&
        readMaterialValuesChild(element, kEdgeSize, &material.edgeSize, 1U) &&
        readMaterialValuesChild(element, kMainTextureMultiply,
                                material.mainTextureMultiply.data(), 4U) &&
        readMaterialValuesChild(element, kMainTextureAdd,
                                material.mainTextureAdd.data(), 4U) &&
        readMaterialValuesChild(element, kSphereTextureMultiply,
                                material.sphereTextureMultiply.data(), 4U) &&
        readMaterialValuesChild(element, kSphereTextureAdd,
                                material.sphereTextureAdd.data(), 4U) &&
        readMaterialValuesChild(element, kToonTextureMultiply,
                                material.toonTextureMultiply.data(), 4U) &&
        readMaterialValuesChild(element, kToonTextureAdd,
                                material.toonTextureAdd.data(), 4U);
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

}  // namespace

const MTypeId MmdRenderShape::id(kMmdRenderShapeId);
const MString MmdRenderShape::drawDbClassification(
    kMmdRenderShapeClassification);
const MString MmdRenderShape::drawRegistrantId(kMmdRenderShapeRegistrantId);
MObject MmdRenderShape::aInputMesh;
MObject MmdRenderShape::aMaterialAlpha;
MObject MmdRenderShape::aMaterialValues;
MObject MmdRenderShape::aMaterialValueChildren[13];
MObject MmdRenderShape::aMaterialSettings;
MObject MmdRenderShape::aMaterialSettingChildren[7];
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
    aMaterialAlpha = numericAttribute.create(
        "materialAlpha", "ma", MFnNumericData::kFloat, 1.0F, &status);
    if (!status) {
        return status;
    }
    numericAttribute.setArray(true);
    numericAttribute.setIndexMatters(true);
    numericAttribute.setUsesArrayDataBuilder(true);
    numericAttribute.setWritable(true);
    numericAttribute.setReadable(true);
    numericAttribute.setStorable(true);
    numericAttribute.setKeyable(false);
    // Keep a source-disconnected element's authored value available.  If an
    // element is absent, updateEvaluatedMaterialAlpha leaves the queue value
    // unchanged rather than applying the attribute default to all materials.
    numericAttribute.setDisconnectBehavior(MFnAttribute::kNothing);
    status = addAttribute(aMaterialAlpha);
    if (!status) {
        return status;
    }

    const MObject materialValueChildren[] = {
        createMaterialFloat3Attribute(
            "DiffuseColorRGB", "dcrgb", 1.0F, &status),
        createMaterialFloat3Attribute(
            "SpecularColor", "sc", 0.0F, &status),
        createMaterialFloatAttribute(
            "Shininess", "sh", 0.0F, &status),
        createMaterialFloat3Attribute(
            "AmbientColor", "ac", 0.3F, &status),
        createMaterialFloat3Attribute(
            "EdgeColorRGB", "ecrgb", 0.0F, &status),
        createMaterialFloatAttribute(
            "EdgeColorA", "eca", 1.0F, &status),
        createMaterialFloatAttribute(
            "EdgeSize", "es", 0.0F, &status),
        createMaterialFloat4Attribute(
            "MainTextureMultiply", "mtm", 1.0F, &status),
        createMaterialFloat4Attribute(
            "MainTextureAdd", "mta", 0.0F, &status),
        createMaterialFloat4Attribute(
            "SphereTextureMultiply", "stm", 1.0F, &status),
        createMaterialFloat4Attribute(
            "SphereTextureAdd", "sta", 0.0F, &status),
        createMaterialFloat4Attribute(
            "ToonTextureMultiply", "ttm", 1.0F, &status),
        createMaterialFloat4Attribute(
            "ToonTextureAdd", "tta", 0.0F, &status),
    };
    if (!status) {
        return status;
    }
    for (unsigned int index = 0U; index < 13U; ++index) {
        aMaterialValueChildren[index] = materialValueChildren[index];
    }
    // The scalar children above are deliberately material input values, not
    // queue controls.  Keep their authored values available through a sparse
    // PMX-indexed compound array and leave absent records untouched.
    MFnCompoundAttribute materialValuesAttribute;
    aMaterialValues = materialValuesAttribute.create(
        "materialValues", "mv", &status);
    if (!status) {
        return status;
    }
    for (const MObject& child : materialValueChildren) {
        status = materialValuesAttribute.addChild(child);
        if (!status) {
            return status;
        }
    }
    materialValuesAttribute.setArray(true);
    materialValuesAttribute.setIndexMatters(true);
    materialValuesAttribute.setUsesArrayDataBuilder(true);
    configureMaterialAttribute(materialValuesAttribute);
    status = addAttribute(aMaterialValues);
    if (!status) {
        return status;
    }

    MFnCompoundAttribute settingsAttribute;
    aMaterialSettings = settingsAttribute.create("materialSettings", "ms", &status);
    if (!status) return status;
    const char* settingNames[] = {"drawFlags", "sphereMode", "sharedToon",
        "toonIndex", "mainTexturePath", "sphereTexturePath", "toonTexturePath"};
    const char* settingShortNames[] = {"df", "sm", "st", "ti", "mtp", "stp", "ttp"};
    for (unsigned int index = 0; index < 7U; ++index) {
        if (index < 4U) {
            aMaterialSettingChildren[index] = numericAttribute.create(
                settingNames[index], settingShortNames[index], MFnNumericData::kInt,
                index == 3U ? -1 : 0, &status);
            if (!status) return status;
            configureMaterialAttribute(numericAttribute);
        } else {
            aMaterialSettingChildren[index] = typedAttribute.create(
                settingNames[index], settingShortNames[index], MFnData::kString,
                MObject::kNullObj, &status);
            if (!status) return status;
            configureMaterialAttribute(typedAttribute);
        }
        status = settingsAttribute.addChild(aMaterialSettingChildren[index]);
        if (!status) return status;
    }
    settingsAttribute.setArray(true);
    settingsAttribute.setIndexMatters(true);
    settingsAttribute.setUsesArrayDataBuilder(true);
    configureMaterialAttribute(settingsAttribute);
    status = addAttribute(aMaterialSettings);
    if (!status) return status;

    aProxyReady = numericAttribute.create(
        "proxyReady", "pr", MFnNumericData::kBoolean, false, &status);
    if (!status) {
        return status;
    }
    // Legacy scene-compatibility input. No current renderer writes it; keep it
    // nonpersistent until saved-scene migration proves the attributes removable.
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
    // Legacy source-visibility output retained for old saved connections. It
    // remains nonstorable and evaluates from aProxyReady, whose current value
    // is always false.
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

MStatus MmdRenderShape::preEvaluation(
    const MDGContext& context, const MEvaluationNode& evaluationNode)
{
    if (context.isNormal()) {
        MStatus status;
        if (evaluationNode.dirtyPlugExists(aMaterialSettings, &status) && status) {
            materialInputsDirty_ = true;
            MHWRender::MRenderer::setGeometryDrawDirty(thisMObject());
        }
        for (const MObject& child : aMaterialSettingChildren) {
            if (evaluationNode.dirtyPlugExists(child, &status) && status) {
                materialInputsDirty_ = true;
                MHWRender::MRenderer::setGeometryDrawDirty(thisMObject());
                break;
            }
        }
        if (evaluationNode.dirtyPlugExists(aInputMesh, &status) && status) {
            meshInputDirty_ = true;
            MHWRender::MRenderer::setGeometryDrawDirty(thisMObject());
        }
        if (evaluationNode.dirtyPlugExists(aMaterialAlpha, &status) &&
            status) {
            materialInputsDirty_ = true;
            MHWRender::MRenderer::setGeometryDrawDirty(thisMObject());
        } else if (evaluationNode.dirtyPlugExists(aMaterialValues, &status) &&
                   status) {
            materialInputsDirty_ = true;
            MHWRender::MRenderer::setGeometryDrawDirty(thisMObject());
        } else {
            bool materialValuesDirty = false;
            for (const MObject& child : aMaterialValueChildren) {
                if (evaluationNode.dirtyPlugExists(child, &status) &&
                    status) {
                    materialValuesDirty = true;
                    break;
                }
            }
            if (materialValuesDirty) {
                materialInputsDirty_ = true;
                MHWRender::MRenderer::setGeometryDrawDirty(thisMObject());
            }
        }
    }
    return MS::kSuccess;
}

MStatus MmdRenderShape::setDependentsDirty(const MPlug& plug,
                                           MPlugArray& /*plugArray*/)
{
    if (!plug.isNull() && plug.attribute() == aInputMesh) {
        meshInputDirty_ = true;
        MHWRender::MRenderer::setGeometryDrawDirty(thisMObject());
    }
    if (!plug.isNull() &&
        (plug.attribute() == aMaterialAlpha || isMaterialValuesPlug(plug))) {
        materialInputsDirty_ = true;
        MHWRender::MRenderer::setGeometryDrawDirty(thisMObject());
    }
    return MS::kSuccess;
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
            if (!nodeStatus || !shape) {
                MGlobal::displayError(
                    "[mmdRenderShape] Failed to inspect source visibility before plugin unload.");
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
            "[mmdRenderShape] Legacy source visibility is true, but live proxy nodes "
            "must be deleted before plugin unload.");
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
    const std::vector<std::vector<uint32_t>>& submeshSourceIndices,
    const std::vector<std::vector<uint32_t>>& submeshSourceCorners)
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
         submeshPositions.size() != submeshSourceIndices.size()) ||
        (!submeshSourceCorners.empty() &&
         submeshPositions.size() != submeshSourceCorners.size())) {
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
    // FastLoad supplies triangles in source PMX material/face order. Their
    // Maya winding is reversed by buildMesh. Preserve a corner for each
    // authored render vertex even when UV welding shares its source position.
    // Scene restoration supplies explicit corners for arbitrary Maya polygons.
    auto sourceCorners = submeshSourceCorners;
    if (sourceCorners.empty()) {
        sourceCorners.resize(submeshPositions.size());
        uint32_t cornerOffset = 0U;
        for (std::size_t group = 0; group < submeshPositions.size(); ++group) {
            auto& corners = sourceCorners[group];
            corners.assign(submeshPositions[group].size() / 3U,
                           std::numeric_limits<uint32_t>::max());
            const auto& indices = submeshIndices[group];
            for (std::size_t i = 0; i < indices.size(); ++i) {
                if (indices[i] >= corners.size()) return reject("invalid source corner vertex");
                if (corners[indices[i]] == std::numeric_limits<uint32_t>::max()) {
                    corners[indices[i]] = cornerOffset + static_cast<uint32_t>(
                        (i / 3U) * 3U + 2U - i % 3U);
                }
            }
            cornerOffset += static_cast<uint32_t>(indices.size());
        }
    }
    for (std::size_t group = 0; group < sourceCorners.size(); ++group) {
        if (sourceCorners[group].size() != submeshPositions[group].size() / 3U)
            return reject("mismatched source-corner data");
    }
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
            next.sourceCornerIndices.push_back(sourceCorners[entry.submeshIndex][i / 3U]);

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
                    !std::isfinite(nz) || !std::isfinite(length)) {
                    return reject("non-finite normal");
                }
                // A zero authored normal is missing data, as in buildMesh.
                // Seed the same default as an absent normal stream; the
                // connected source mesh supplies Maya's geometric corner
                // normal on evaluation. Keep valid authored normals intact.
                next.normals.push_back(length > 0.0 ? static_cast<float>(nx / length) : 0.0F);
                next.normals.push_back(length > 0.0 ? static_cast<float>(ny / length) : 1.0F);
                next.normals.push_back(length > 0.0 ? static_cast<float>(-nz / length) : 0.0F);
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
    ++renderDataRevision_;
    ++geometryBufferRevision_;
    meshInputDirty_ = true;
    staticPositions_ = std::move(nextStaticPositions);
    materialInputsDirty_ = true;
    staticNormals_ = std::move(nextStaticNormals);
    boundingBox_ = nextBounds;
    staticBoundingBox_ = nextBounds;
    geometryValid_ = true;
    evaluatedGeometryActive_ = false;
    evaluatedNormalRepairCount_ = 0U;
    evaluatedNormalStaticFallbackCount_ = 0U;
    evaluatedNormalRepairWarningEmitted_ = false;
    renderFallbackReason_.clear();
    return true;
}

void MmdRenderShape::updateEvaluatedData()
{
    MProfilingScope profile(mmdRenderProfileCategory(), MProfiler::kColorE_L1,
                            "MMD.UpdateEvaluatedData");
    if (geometry_.positions.empty()) {
        const MPlug upstream = MPlug(thisMObject(), aInputMesh).source();
        if (upstream.isNull() || !restoreGeometryFromSource(upstream.node())) {
            return;
        }
    }
    if (materialInputsDirty_) {
        // Clear before evaluation so callbacks raised by a read remain pending.
        materialInputsDirty_ = false;
        const bool alphaReady = updateEvaluatedMaterialAlpha();
        const bool valuesReady = updateEvaluatedMaterialValues();
        const bool settingsReady = updateEvaluatedMaterialSettings();
        if (!alphaReady || !valuesReady || !settingsReady) materialInputsDirty_ = true;
    }

    if (!consumeMeshInputDirty()) {
        return;
    }

    MPlug inputPlug(thisMObject(), MmdRenderShape::aInputMesh);
    if (inputPlug.isNull()) {
        // A shape created by an older scene/plugin version may not expose the
        // optional input.  Preserve its static geometry in that case.
        useStaticGeometry();
        return;
    }

    MStatus connectionStatus;
    const bool connected = inputPlug.isConnected(&connectionStatus);
    if (!connectionStatus) {
        updateEvaluatedMesh(MObject::kNullObj);
        return;
    }

    MStatus meshStatus;
    MDataHandle inputHandle = [&] {
        MProfilingScope inputProfile(mmdRenderProfileCategory(), MProfiler::kColorE_L1,
                                     "MMD.DemandInputMesh");
        return inputPlug.asMDataHandle(&meshStatus);
    }();
    // MPlug owns the returned handle's storage; keep it alive through mesh
    // consumption and release it on every exit, including failed evaluation.
    struct InputHandleRelease {
        const MPlug& plug;
        MDataHandle& handle;
        ~InputHandleRelease()
        {
            MProfilingScope releaseProfile(mmdRenderProfileCategory(), MProfiler::kColorE_L1,
                                           "MMD.ReleaseInputMesh");
            plug.destructHandle(handle);
        }
    } release{inputPlug, inputHandle};
    const MObject meshObject = [&] {
        MProfilingScope extractProfile(mmdRenderProfileCategory(), MProfiler::kColorE_L1,
                                       "MMD.ExtractInputMesh");
        return meshStatus && inputHandle.type() == MFnData::kMesh
            ? inputHandle.asMesh() : MObject::kNullObj;
    }();
    if (!meshObject.isNull()) {
        updateEvaluatedMesh(meshObject);
        return;
    }

    if (connected || !meshStatus) {
        // A connected but unevaluable mesh is an input failure, not a request
        // to silently keep stale render data visible.
        updateEvaluatedMesh(MObject::kNullObj);
    } else {
        useStaticGeometry();
    }
}

bool MmdRenderShape::restoreGeometryFromSource(const MObject& sourceMesh)
{
    // A saved scene owns the ordinary mesh and its shading assignments. Build
    // the transient draw topology from those once, without rereading the PMX
    // or serializing a second copy of its authored mesh/material data.
    MStatus status;
    MFnMesh mesh(sourceMesh, &status);
    if (!status) return false;
    MObjectArray sets;
    MIntArray faceShaders, triangleCounts, triangleVertices, normalCounts, normalIds;
    MPointArray points;
    if (!mesh.getConnectedShaders(0, sets, faceShaders) ||
        !mesh.getTriangles(triangleCounts, triangleVertices) ||
        !mesh.getPoints(points, MSpace::kObject) ||
        !mesh.getNormalIds(normalCounts, normalIds)) return false;
    if (faceShaders.length() != triangleCounts.length()) return false;
    if (normalCounts.length() != triangleCounts.length()) return false;

    std::vector<std::vector<float>> positions(sets.length()), normals(sets.length()), uvs(sets.length());
    std::vector<std::vector<uint32_t>> indices(sets.length()), sources(sets.length());
    std::vector<std::vector<uint32_t>> sourceCorners(sets.length());
    // Share only identical corners of the same source vertex and material.
    // UV seams and authored face normals must remain distinct after reload.
    std::vector<std::unordered_map<uint32_t, std::vector<uint32_t>>> sharedVertices(sets.length());
    std::vector<mmd::MmdRenderQueueInput> inputs;
    for (unsigned int i = 0; i < sets.length(); ++i) {
        MFnDependencyNode set(sets[i]);
        const MPlug shaderPlug = set.findPlug("surfaceShader", true, &status);
        if (!status || shaderPlug.source().isNull()) return false;
        MFnDependencyNode shader(shaderPlug.source().node());
        const MPlug materialIndex = shader.findPlug("mmd_material_index", true, &status);
        if (!status) return false;
        const int index = materialIndex.asInt(&status);
        if (!status || index < 0) return false;
        mmd::MmdRenderQueueInput input;
        input.materialIndex = static_cast<std::size_t>(index);
        input.submeshIndex = i;
        // The queue is transient, but the standardSurface policy survives in
        // the saved scene. Restore it before classifying draw passes so
        // cutout and blend materials do not reopen as opaque.
        const MPlug transparency = shader.findPlug("mmdTransparencyMode", true, &status);
        if (status && !transparency.isNull()) {
            const MString mode = transparency.asString(&status);
            if (!status) return false;
            input.transparencyMode = mode.asUTF8();
        }
        inputs.push_back(input);
    }
    unsigned int triangleOffset = 0;
    unsigned int cornerOffset = 0;
    for (unsigned int face = 0; face < triangleCounts.length(); ++face) {
        const int group = faceShaders[face];
        if (group < 0 || static_cast<unsigned int>(group) >= sets.length()) return false;
        MIntArray faceVertices;
        if (!mesh.getPolygonVertices(face, faceVertices)) return false;
        if (normalCounts[face] != static_cast<int>(faceVertices.length()) ||
            cornerOffset + faceVertices.length() > normalIds.length()) return false;
        for (int triangle = 0; triangle < triangleCounts[face]; ++triangle) {
            if (triangleOffset + 3 > triangleVertices.length()) return false;
            // The initializer converts PMX winding/coordinates to Maya space.
            for (int corner = 2; corner >= 0; --corner) {
                const int vertex = triangleVertices[triangleOffset + corner];
                if (vertex < 0 || static_cast<unsigned int>(vertex) >= points.length()) return false;
                MVector normal;
                if (!mesh.getFaceVertexNormal(face, vertex, normal, MSpace::kObject)) return false;
                float u = 0.0F, v = 0.0F;
                unsigned int sourceCorner = normalIds.length();
                for (unsigned int local = 0; local < faceVertices.length(); ++local) {
                    if (faceVertices[local] == vertex) {
                        sourceCorner = cornerOffset + local;
                        mesh.getPolygonUV(face, local, u, v);
                        break;
                    }
                }
                if (sourceCorner >= normalIds.length()) return false;
                const MPoint& point = points[vertex];
                const float nx = static_cast<float>(normal.x);
                const float ny = static_cast<float>(normal.y);
                const float nz = static_cast<float>(-normal.z);
                const float flippedV = 1.0F - v;
                auto& candidates = sharedVertices[group][static_cast<uint32_t>(vertex)];
                const auto existing = std::find_if(candidates.begin(), candidates.end(), [&](uint32_t index) {
                    return normalIds[sourceCorners[group][index]] == normalIds[sourceCorner] &&
                           normals[group][index * 3U] == nx &&
                           normals[group][index * 3U + 1U] == ny &&
                           normals[group][index * 3U + 2U] == nz &&
                           uvs[group][index * 2U] == u &&
                           uvs[group][index * 2U + 1U] == flippedV;
                });
                if (existing != candidates.end()) {
                    indices[group].push_back(*existing);
                    continue;
                }
                candidates.push_back(static_cast<uint32_t>(sources[group].size()));
                positions[group].insert(positions[group].end(),
                    {static_cast<float>(point.x), static_cast<float>(point.y), static_cast<float>(-point.z)});
                normals[group].insert(normals[group].end(),
                    {nx, ny, nz});
                uvs[group].insert(uvs[group].end(), {u, flippedV});
                indices[group].push_back(static_cast<uint32_t>(sources[group].size()));
                sources[group].push_back(static_cast<uint32_t>(vertex));
                sourceCorners[group].push_back(sourceCorner);
            }
            triangleOffset += 3;
        }
        cornerOffset += faceVertices.length();
    }
    return setMaterialSplitGeometry(positions, normals, uvs, indices, inputs, 1.0, sources,
                                    sourceCorners);
}

bool MmdRenderShape::updateEvaluatedMesh(const MObject& meshObject)
{
    MProfilingScope profile(mmdRenderProfileCategory(), MProfiler::kColorE_L1,
                            "MMD.UpdateEvaluatedMesh");
    ++geometryUpdateCount_;
    ++renderDataRevision_;
    ++geometryBufferRevision_;
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
        // A transient evaluation failure must remain eligible for retry.
        meshInputDirty_ = true;
        evaluatedNormalRepairCount_ = 0U;
        evaluatedNormalStaticFallbackCount_ = 0U;
        evaluatedNormalRepairWarningEmitted_ = false;
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
        geometry_.sourceVertexIndices.size() != renderVertexCount ||
        geometry_.sourceCornerIndices.size() != renderVertexCount) {
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
    if (![&] {
            MProfilingScope fetchProfile(mmdRenderProfileCategory(), MProfiler::kColorE_L1,
                                         "MMD.GetPoints");
            return meshFn.getPoints(points, MSpace::kObject);
        }()) {
        return reject("could not read object-space positions");
    }
    MFloatVectorArray normals;
    MIntArray normalCounts, normalIds, faceCounts, faceVertices;
    if (![&] {
            MProfilingScope fetchProfile(mmdRenderProfileCategory(), MProfiler::kColorE_L1,
                                         "MMD.GetVertexNormals");
            return meshFn.getNormals(normals, MSpace::kObject) &&
                   meshFn.getNormalIds(normalCounts, normalIds) &&
                   meshFn.getVertices(faceCounts, faceVertices);
        }()) {
        return reject("could not read object-space face-corner normals");
    }
    if (static_cast<std::size_t>(points.length()) < expectedSourceVertexCount ||
        normalIds.length() != faceVertices.length() ||
        normalCounts.length() != faceCounts.length()) {
        return reject("input mesh vertex/normal count does not match source topology");
    }
    for (unsigned int face = 0; face < faceCounts.length(); ++face) {
        if (normalCounts[face] != faceCounts[face])
            return reject("input mesh normal corners do not match source topology");
    }

    // Maya can expose a zero or non-finite vertex normal for a degenerate
    // evaluated face.  Keep authored/evaluated normals whenever they are
    // usable.  Invalid slots use the immutable import-time stream instead of
    // triggering another normal calculation during every DG update.  The
    // repair list stays empty on the normal path.
    MProfilingScope repackProfile(mmdRenderProfileCategory(), MProfiler::kColorE_L1,
                                  "MMD.ExpandEvaluatedStreams");
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
        const uint32_t sourceCorner = geometry_.sourceCornerIndices[renderVertex];
        // Unreferenced import vertices have no corner and are never drawn.
        MFloatVector inputNormal(0.0F, 0.0F, 0.0F);
        if (sourceCorner != std::numeric_limits<uint32_t>::max()) {
            if (sourceCorner >= normalIds.length() ||
                faceVertices[sourceCorner] != static_cast<int>(sourceIndex))
                return reject("source corner no longer matches input mesh topology");
            const int normalId = normalIds[sourceCorner];
            if (normalId < 0 || static_cast<unsigned int>(normalId) >= normals.length())
                return reject("source corner has an invalid normal index");
            inputNormal = normals[normalId];
        }
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
    if (normalRepairCount > 0U && !evaluatedNormalRepairWarningEmitted_) {
        std::ostringstream warning;
        warning << "[mmdRenderShape] Repaired " << normalRepairCount
                << " invalid evaluated mesh normal(s) with "
                << staticFallbackCount << " import-time static fallback(s).";
        MGlobal::displayWarning(MString(warning.str().c_str()));
        evaluatedNormalRepairWarningEmitted_ = true;
    }
    // Keep the latest counts in the diagnostic witness even when the set of
    // invalid slots changes during playback; warning emission is independent
    // from per-frame state updates.
    evaluatedNormalRepairCount_ = normalRepairCount;
    evaluatedNormalStaticFallbackCount_ = staticFallbackCount;
    renderFallbackReason_.clear();
    return true;
}

void MmdRenderShape::useStaticGeometry()
{
    if (!geometryValid_ || evaluatedGeometryActive_) {
        ++renderDataRevision_;
        ++geometryBufferRevision_;
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
        evaluatedNormalRepairWarningEmitted_ = false;
        renderFallbackReason_.clear();
    }
}

bool MmdRenderShape::hasValidGeometry() const
{
    return geometryValid_ && !geometry_.positions.empty();
}

bool MmdRenderShape::updateEvaluatedMaterialAlpha()
{
    MProfilingScope profile(mmdRenderProfileCategory(), MProfiler::kColorE_L1,
                            "MMD.UpdateMaterialAlpha");
    bool valid = true;
    MPlug alphaPlug(thisMObject(), aMaterialAlpha);
    if (alphaPlug.isNull()) {
        return false;
    }

    MStatus alphaCountStatus;
    MIntArray materialIndices;
    // Enumerate connected/authored elements without evaluating every material
    // on every split shape. Missing elements must remain missing.
    const unsigned int alphaCount =
        alphaPlug.getExistingArrayAttributeIndices(materialIndices, &alphaCountStatus);
    if (!alphaCountStatus) {
        return false;
    }
    std::vector<std::pair<std::size_t, float>> updates;
    updates.reserve(alphaCount);
    for (unsigned int physicalIndex = 0U; physicalIndex < alphaCount;
         ++physicalIndex) {
        const unsigned int materialIndex =
            static_cast<unsigned int>(materialIndices[physicalIndex]);
        if (std::none_of(geometry_.queueInputs.begin(), geometry_.queueInputs.end(),
                         [materialIndex](const mmd::MmdRenderQueueInput& input) {
                             return input.materialIndex == materialIndex;
                         })) {
            continue;
        }
        MStatus elementStatus;
        MPlug alphaElement = alphaPlug.elementByLogicalIndex(
            materialIndex, &elementStatus);
        if (!elementStatus || alphaElement.isNull()) {
            valid = false;
            continue;
        }
        const float diffuseAlpha = alphaElement.asFloat(&elementStatus);
        if (!elementStatus || !std::isfinite(diffuseAlpha)) {
            valid = false;
            continue;
        }
        const float effectiveAlpha =
            std::max(0.0F, std::min(1.0F, diffuseAlpha));

        for (const mmd::MmdRenderQueueInput& input : geometry_.queueInputs) {
            if (input.materialIndex == materialIndex &&
                input.diffuseAlpha != effectiveAlpha) {
                updates.emplace_back(materialIndex, effectiveAlpha);
                break;
            }
        }
    }
    if (!updates.empty()) {
        valid = applyMaterialAlphaUpdates(updates) && valid;
    }
    return valid;
}

bool MmdRenderShape::updateEvaluatedMaterialValues()
{
    MProfilingScope profile(mmdRenderProfileCategory(), MProfiler::kColorE_L1,
                            "MMD.UpdateMaterialValues");
    bool valid = true;
    MPlug valuesPlug(thisMObject(), aMaterialValues);
    if (valuesPlug.isNull()) {
        return false;
    }

    MStatus valuesCountStatus;
    MIntArray materialIndices;
    const unsigned int valuesCount =
        valuesPlug.getExistingArrayAttributeIndices(materialIndices, &valuesCountStatus);
    if (!valuesCountStatus) {
        return false;
    }

    std::vector<std::pair<std::size_t, mmd::MmdRenderQueueInput>> updates;
    updates.reserve(valuesCount);
    for (unsigned int physicalIndex = 0U; physicalIndex < valuesCount;
         ++physicalIndex) {
        const unsigned int materialIndex =
            static_cast<unsigned int>(materialIndices[physicalIndex]);

        // Split shapes can retain bindings for every material in the model.
        // Only read values that can be applied to this shape's draw queue.
        if (std::none_of(geometry_.queueInputs.begin(), geometry_.queueInputs.end(),
                         [materialIndex](const mmd::MmdRenderQueueInput& input) {
                             return input.materialIndex == materialIndex;
        })) {
            continue;
        }

        MStatus elementStatus;
        const MPlug element = valuesPlug.elementByLogicalIndex(
            materialIndex, &elementStatus);
        if (!elementStatus || element.isNull()) {
            valid = false;
            continue;
        }
        mmd::MmdRenderQueueInput materialValues;
        if (!readMaterialValuesRecord(element, materialValues)) {
            valid = false;
            MGlobal::displayError(
                "[mmdRenderShape] Material value record rejected: "
                "missing or non-finite numeric value.");
            continue;
        }
        updates.emplace_back(materialIndex, std::move(materialValues));
    }
    if (updates.empty()) {
        return valid;
    }

    std::unordered_map<std::size_t, std::size_t> updateIndexByMaterial;
    updateIndexByMaterial.reserve(updates.size());
    for (std::size_t updateIndex = 0U; updateIndex < updates.size();
         ++updateIndex) {
        updateIndexByMaterial.emplace(updates[updateIndex].first, updateIndex);
    }

    bool valuesChanged = false;
    for (mmd::MmdRenderQueueInput& input : geometry_.queueInputs) {
        const auto updateIt = updateIndexByMaterial.find(input.materialIndex);
        if (updateIt != updateIndexByMaterial.end()) {
            const auto& update = updates[updateIt->second].second;
            if (!mmd::sameMmdMaterialValues(input, update)) {
                mmd::copyMmdMaterialValues(input, update);
                valuesChanged = true;
            }
        }
    }
    for (QueueGeometry& queueGeometry : geometry_.queueGeometry) {
        const auto updateIt = updateIndexByMaterial.find(
            queueGeometry.entry.materialIndex);
        if (updateIt != updateIndexByMaterial.end()) {
            const auto& update = updates[updateIt->second].second;
            if (!mmd::sameMmdMaterialValues(queueGeometry.material, update)) {
                mmd::copyMmdMaterialValues(queueGeometry.material, update);
                valuesChanged = true;
            }
        }
    }
    if (valuesChanged) {
        valid = resyncMaterialQueue(geometry_.queueInputs) && valid;
    }
    return valid;
}

bool MmdRenderShape::updateEvaluatedMaterialSettings()
{
    MProfilingScope profile(mmdRenderProfileCategory(), MProfiler::kColorE_L1,
                            "MMD.UpdateMaterialSettings");
    MPlug settings(thisMObject(), aMaterialSettings);
    MStatus status;
    MIntArray materialIndices;
    const unsigned int count = settings.getExistingArrayAttributeIndices(materialIndices, &status);
    if (!status) return false;
    if (count == 0U) return true;
    auto nextInputs = geometry_.queueInputs;
    bool changed = false;
    for (unsigned int physical = 0; physical < count; ++physical) {
        const unsigned int materialIndex = static_cast<unsigned int>(materialIndices[physical]);
        // Split shapes retain model-wide bindings; evaluate only settings
        // that can be applied to this shape's draw queue.
        if (std::none_of(nextInputs.begin(), nextInputs.end(),
                         [materialIndex](const mmd::MmdRenderQueueInput& input) {
                             return input.materialIndex == materialIndex;
                         })) {
            continue;
        }
        const MPlug element = settings.elementByLogicalIndex(materialIndex, &status);
        if (!status) return false;
        const int flags = element.child(0U).asInt(&status);
        if (!status) return false;
        const int sphereMode = element.child(1U).asInt(&status);
        if (!status) return false;
        const bool sharedToon = element.child(2U).asInt(&status) != 0;
        if (!status) return false;
        const int toonIndex = element.child(3U).asInt(&status);
        if (!status) return false;
        const std::string mainPath = element.child(4U).asString(&status).asUTF8();
        if (!status) return false;
        const std::string spherePath = element.child(5U).asString(&status).asUTF8();
        if (!status) return false;
        const std::string toonPath = element.child(6U).asString(&status).asUTF8();
        if (!status) return false;
        const int sharedIndex = sharedToon ? toonIndex : -1;
        for (auto& input : nextInputs) {
            if (input.materialIndex != materialIndex) continue;
            if (input.doubleSided == bool(flags & 1) &&
                input.selfShadowMap == bool(flags & 4) &&
                input.selfShadow == bool(flags & 8) &&
                input.edgeDrawing == bool(flags & 16) &&
                input.sphereMode == sphereMode && input.sharedToonIndex == sharedIndex &&
                input.mainTexturePath == mainPath && input.sphereTexturePath == spherePath &&
                input.toonTexturePath == toonPath) continue;
            if (input.mainTexturePath != mainPath) input.mainTextureAvailable = false;
            input.doubleSided = bool(flags & 1);
            input.selfShadowMap = bool(flags & 4);
            input.selfShadow = bool(flags & 8);
            input.edgeDrawing = bool(flags & 16);
            input.sphereMode = sphereMode;
            input.sharedToonIndex = sharedIndex;
            input.mainTexturePath = mainPath;
            input.sphereTexturePath = spherePath;
            input.toonTexturePath = toonPath;
            changed = true;
        }
    }
    if (changed) {
        if (!resyncMaterialQueue(nextInputs)) return false;
    }
    return true;
}

bool MmdRenderShape::resyncMaterialQueue(
    const std::vector<mmd::MmdRenderQueueInput>& nextInputs)
{
    const std::vector<mmd::MmdRenderQueueEntry> nextQueue =
        mmd::buildMmdRenderQueue(nextInputs);
    const std::size_t queueSize = geometry_.queueGeometry.size();
    if (nextQueue.size() != nextInputs.size() || queueSize != nextInputs.size()) {
        MGlobal::displayError(
            "[mmdRenderShape] Material update changed queue size.");
        return false;
    }

    std::vector<std::size_t> sourceIndexByInput(nextInputs.size(), queueSize);
    for (std::size_t candidateIndex = 0; candidateIndex < queueSize;
         ++candidateIndex) {
        const std::size_t inputIndex =
            geometry_.queueGeometry[candidateIndex].entry.inputIndex;
        if (inputIndex >= sourceIndexByInput.size() ||
            sourceIndexByInput[inputIndex] != queueSize) {
            MGlobal::displayError(
                "[mmdRenderShape] Material update has duplicate or invalid input index.");
            return false;
        }
        sourceIndexByInput[inputIndex] = candidateIndex;
    }

    ++renderDataRevision_;
    bool orderChanged = geometry_.renderQueue.size() != nextQueue.size();
    if (!orderChanged) {
        for (std::size_t index = 0; index < nextQueue.size(); ++index) {
            if (geometry_.renderQueue[index].inputIndex !=
                    nextQueue[index].inputIndex ||
                geometry_.queueGeometry[index].entry.inputIndex !=
                    nextQueue[index].inputIndex) {
                orderChanged = true;
                break;
            }
        }
    }
    if (!orderChanged) {
        for (std::size_t index = 0; index < nextQueue.size(); ++index) {
            QueueGeometry& item = geometry_.queueGeometry[index];
            item.entry = nextQueue[index];
            item.material = nextInputs[nextQueue[index].inputIndex];
        }
        geometry_.queueInputs = nextInputs;
        geometry_.renderQueue = nextQueue;
        return true;
    }

    ++geometryBufferRevision_;
    std::vector<QueueGeometry> reordered;
    reordered.reserve(nextQueue.size());
    for (const mmd::MmdRenderQueueEntry& entry : nextQueue) {
        QueueGeometry item = std::move(
            geometry_.queueGeometry[sourceIndexByInput[entry.inputIndex]]);
        item.entry = entry;
        item.material = nextInputs[entry.inputIndex];
        reordered.push_back(std::move(item));
    }
    geometry_.queueInputs = nextInputs;
    geometry_.renderQueue = nextQueue;
    geometry_.queueGeometry = std::move(reordered);
    return true;
}

bool MmdRenderShape::applyMaterialAlphaUpdates(
    const std::vector<std::pair<std::size_t, float>>& updates)
{
    if (updates.empty()) {
        return true;
    }

    for (const auto& update : updates) {
        if (!std::isfinite(update.second)) {
            MGlobal::displayError(
                "[mmdRenderShape] Queue alpha update rejected: non-finite alpha.");
            return false;
        }
    }

    std::vector<mmd::MmdRenderQueueInput> nextInputs = geometry_.queueInputs;
    for (const auto& update : updates) {
        const std::size_t materialIndex = update.first;
        const float clampedAlpha =
            std::max(0.0F, std::min(1.0F, update.second));
        bool updateFound = false;
        for (mmd::MmdRenderQueueInput& input : nextInputs) {
            if (input.materialIndex != materialIndex) {
                continue;
            }
            input.diffuseAlpha = clampedAlpha;
            // Preserve blend/cutout selected by the material or texture alpha.
            // Otherwise let the effective diffuse alpha classify the queue;
            // writing "blend" here would make a temporary fade permanent.
            if (mmd::classifyMmdDrawPass(input.transparencyMode, 1.0F) ==
                mmd::MmdDrawPass::Opaque) {
                input.transparencyMode.clear();
            }
            updateFound = true;
        }
        if (!updateFound) {
            return false;
        }
    }

    if (!resyncMaterialQueue(nextInputs)) {
        return false;
    }
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
    // Queue material indices now address different DG input records.
    materialInputsDirty_ = true;
    ++renderDataRevision_;
    ++geometryBufferRevision_;
    return true;
}

const MmdRenderShape::GeometryData& MmdRenderShape::geometry() const
{
    return geometry_;
}

bool MmdRenderShape::recordRenderFallbackReason(const std::string& reason)
{
    const bool changed = renderFallbackReason_ != reason;
    renderFallbackReason_ = reason;
    return changed;
}

std::string MmdRenderShape::renderItemWitness() const
{
    if (!renderFallbackReason_.empty()) {
        return "failed reason=" + renderFallbackReason_;
    }
    return "pending";
}

std::string MmdRenderShape::materialBindingDiagnosticsJson() const
{
    std::ostringstream stream;
    const char* status = renderFallbackReason_.empty() ? "pending" : "failed";
    stream << "{\"version\":1,\"status\":"
           << jsonEscape(status) << ",\"fallbackReason\":"
           << jsonEscape(renderFallbackReason_)
           << ",\"geometryUpdates\":" << geometryUpdateCount_
           // Version 1 exposed this top-level key. The retired geometry
           // override no longer uploads buffers, so preserve the schema with
           // its only valid value instead of retaining dead counter state.
           << ",\"bufferUploads\":0"
           << ",\"repairedNormals\":" << evaluatedNormalRepairCount_
           << ",\"staticNormalFallbacks\":"
           << evaluatedNormalStaticFallbackCount_
           << ",\"items\":[]}";
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
    MStatus parseStatus;
    // Keep the syntax alive while the parser resolves flags after DAG lookup.
    const MSyntax commandSyntax = newSyntax();
    MArgDatabase argData(commandSyntax, args, &parseStatus);
    if (!parseStatus) {
        return parseStatus;
    }
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

    MmdRenderShape* shape = MmdRenderShape::fromMObject(node, &status);
    if (status && shape && shape->geometry().queueInputs.empty()) {
        // A reopened scene may not have drawn this shape yet. Its queue is
        // transient, and the caller has already swapped the DG material
        // indices. Restore from those current indices instead of swapping
        // them a second time. Subsequent undo/redo swap the restored cache.
        shape->updateEvaluatedData();
        const auto& restoredInputs = shape->geometry().queueInputs;
        const auto hasMaterial = [&](std::size_t index) {
            return std::any_of(restoredInputs.begin(), restoredInputs.end(),
                               [index](const mmd::MmdRenderQueueInput& input) {
                                   return input.materialIndex == index;
                               });
        };
        if (hasMaterial(firstIndex_) && hasMaterial(secondIndex_)) {
            MHWRender::MRenderer::setGeometryDrawDirty(node, true);
            setResult(mStringFromUtf8(shape->renderItemWitness()));
            return MS::kSuccess;
        }
    }
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
