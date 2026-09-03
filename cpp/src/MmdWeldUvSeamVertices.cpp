/**
 * MmdWeldUvSeamVertices.cpp
 *
 * Native mesh-topology normalization for the Python PMX importer.
 *
 * PMX stores the primary UV on a vertex.  A UV seam therefore often creates
 * several PMX vertices with the same position.  Maya stores UV assignment per
 * face corner, so those source vertices can share one Maya vertex as long as
 * their deformation payload is identical.  This command performs that
 * normalization in C++ before skin and morph nodes are built.
 */

#include "MmdWeldUvSeamVertices.h"

#include <maya/MArgDatabase.h>
#include <maya/MDagPath.h>
#include <maya/MDagModifier.h>
#include <maya/MFnDagNode.h>
#include <maya/MFnDependencyNode.h>
#include <maya/MFnData.h>
#include <maya/MFnIntArrayData.h>
#include <maya/MFnMesh.h>
#include <maya/MFnSet.h>
#include <maya/MFnSingleIndexedComponent.h>
#include <maya/MFnTypedAttribute.h>
#include <maya/MFloatArray.h>
#include <maya/MFloatVectorArray.h>
#include <maya/MGlobal.h>
#include <maya/MIntArray.h>
#include <maya/MObjectArray.h>
#include <maya/MPointArray.h>
#include <maya/MPlug.h>
#include <maya/MSelectionList.h>
#include <maya/MStringArray.h>
#include <maya/MVectorArray.h>

#include "mmd_runtime.h"
#include "third_party/json.hpp"

#include <algorithm>
#include <array>
#include <chrono>
#include <cctype>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <fstream>
#include <filesystem>
#include <limits>
#include <string>
#include <unordered_map>
#include <vector>

using nlohmann::json;

namespace {

constexpr const char* kSourceVertexAttribute = "mmd_source_vertex_indices";
constexpr const char* kSourceToLocalAttribute = "mmd_source_to_local_indices";
constexpr const char* kSourceToLocalCapability = "sourceToLocalV1";
constexpr const char* kMorphEquivalentCapability = "morphEquivalentV1";
constexpr const char* kBatchCapability = "batchV1";
constexpr const char* kProfileCapability = "profileV1";

struct WeldProfile {
    double preparationSeconds = 0.0;
    double applicationSeconds = 0.0;
    unsigned int pmxReadCount = 0;
    unsigned int geometryParseCount = 0;
    unsigned int nonGeometryParseCount = 0;
};

struct UvSetData {
    MString     name;
    MFloatArray u;
    MFloatArray v;
    MIntArray   counts;
    MIntArray   ids;
};

struct RawGeometry {
    std::vector<float>              positions;
    std::vector<uint32_t>           skinIndices;
    std::vector<float>              skinWeights;
    std::vector<float>              edgeScale;
    std::vector<uint8_t>            sdefEnabled;
    std::vector<float>              sdefC;
    std::vector<float>              sdefR0;
    std::vector<float>              sdefR1;
    std::vector<float>              sdefRw0;
    std::vector<float>              sdefRw1;
    std::vector<uint8_t>            qdefEnabled;
    std::vector<std::vector<float>>    additionalUvs;
    std::vector<std::vector<uint32_t>> morphSignatures;
};

struct WeldKey {
    std::vector<uint32_t> words;

    bool operator==(const WeldKey& other) const
    {
        return words == other.words;
    }
};

struct WeldKeyHash {
    size_t operator()(const WeldKey& key) const noexcept
    {
        size_t hash = 1469598103934665603ULL;
        for (uint32_t word : key.words) {
            hash ^= static_cast<size_t>(word);
            hash *= 1099511628211ULL;
        }
        return hash;
    }
};

std::vector<uint8_t> readBinaryFile(const std::string& path)
{
    std::ifstream stream(std::filesystem::u8path(path), std::ios::binary | std::ios::ate);
    if (!stream) {
        return {};
    }
    const std::streamsize size = stream.tellg();
    if (size <= 0) {
        return {};
    }
    stream.seekg(0, std::ios::beg);
    std::vector<uint8_t> bytes(static_cast<size_t>(size));
    if (!stream.read(reinterpret_cast<char*>(bytes.data()), size)) {
        return {};
    }
    return bytes;
}

std::vector<float> takeFloatBuffer(mmd_runtime_ffi_byte_buffer_t buffer)
{
    std::vector<float> result;
    if (buffer.data && buffer.len >= sizeof(float)) {
        const size_t count = buffer.len / sizeof(float);
        result.resize(count);
        std::memcpy(result.data(), buffer.data, count * sizeof(float));
    }
    mmd_runtime_byte_buffer_free(buffer);
    return result;
}

std::vector<uint32_t> takeU32Buffer(mmd_runtime_ffi_byte_buffer_t buffer)
{
    std::vector<uint32_t> result;
    if (buffer.data && buffer.len >= sizeof(uint32_t)) {
        const size_t count = buffer.len / sizeof(uint32_t);
        result.resize(count);
        std::memcpy(result.data(), buffer.data, count * sizeof(uint32_t));
    }
    mmd_runtime_byte_buffer_free(buffer);
    return result;
}

std::vector<uint8_t> takeU8Buffer(mmd_runtime_ffi_byte_buffer_t buffer)
{
    std::vector<uint8_t> result;
    if (buffer.data && buffer.len > 0) {
        result.assign(buffer.data, buffer.data + buffer.len);
    }
    mmd_runtime_byte_buffer_free(buffer);
    return result;
}

json takeJsonBuffer(mmd_runtime_ffi_byte_buffer_t buffer)
{
    json result;
    if (buffer.data && buffer.len > 0) {
        const char* begin = reinterpret_cast<const char*>(buffer.data);
        result = json::parse(begin, begin + buffer.len, nullptr, false);
    }
    mmd_runtime_byte_buffer_free(buffer);
    return result;
}

uint32_t floatBits(float value)
{
    uint32_t bits = 0;
    static_assert(sizeof(bits) == sizeof(value), "float and uint32_t must match");
    std::memcpy(&bits, &value, sizeof(bits));
    return bits;
}

void appendDoubleBits(std::vector<uint32_t>& words, double value)
{
    uint64_t bits = 0;
    static_assert(sizeof(bits) == sizeof(value), "double and uint64_t must match");
    std::memcpy(&bits, &value, sizeof(bits));
    words.push_back(static_cast<uint32_t>(bits));
    words.push_back(static_cast<uint32_t>(bits >> 32U));
}

bool buildMorphSignatures(const json& nonGeometry, size_t sourceCount,
                          std::vector<std::vector<uint32_t>>& signatures)
{
    signatures.assign(sourceCount, {});
    if (!nonGeometry.is_object() || !nonGeometry.contains("morphs") ||
        !nonGeometry["morphs"].is_array()) {
        return false;
    }

    const json& morphs = nonGeometry["morphs"];
    for (size_t morphIndex = 0; morphIndex < morphs.size(); ++morphIndex) {
        const json& morph = morphs[morphIndex];
        if (!morph.is_object()) {
            return false;
        }

        const std::string type = morph.value("type", std::string());
        const char* offsetsField = nullptr;
        const char* valueField = nullptr;
        size_t componentCount = 0;
        uint32_t morphKind = 0;
        if (type == "vertex") {
            offsetsField = "vertexOffsets";
            valueField = "position";
            componentCount = 3;
            morphKind = 1;
        } else if (type == "uv") {
            offsetsField = "uvOffsets";
            valueField = "uv";
            componentCount = 4;
            morphKind = 3;
        } else if (type == "additionalUv") {
            offsetsField = "additionalUvOffsets";
            valueField = "uv";
            componentCount = 4;
            morphKind = 4;
        } else {
            continue;
        }

        const auto offsetsIt = morph.find(offsetsField);
        if (offsetsIt == morph.end() || !offsetsIt->is_array()) {
            return false;
        }

        std::unordered_map<uint32_t, std::array<double, 4>> accumulated;
        std::unordered_map<uint32_t, uint32_t> additionalUvKinds;
        for (const json& offset : *offsetsIt) {
            if (!offset.is_object()) {
                return false;
            }
            const auto sourceIt = offset.find("vertexIndex");
            if (sourceIt == offset.end() || !sourceIt->is_number_integer()) {
                return false;
            }
            const int64_t sourceValue = sourceIt->get<int64_t>();
            if (sourceValue < 0 || static_cast<size_t>(sourceValue) >= sourceCount) {
                return false;
            }
            const uint32_t sourceIndex = static_cast<uint32_t>(sourceValue);

            uint32_t offsetKind = morphKind;
            if (type == "additionalUv") {
                const auto uvIndexIt = offset.find("uvIndex");
                if (uvIndexIt == offset.end() || !uvIndexIt->is_number_integer()) {
                    return false;
                }
                const int64_t uvIndex = uvIndexIt->get<int64_t>();
                if (uvIndex < 0 || uvIndex > 3) {
                    return false;
                }
                offsetKind += static_cast<uint32_t>(uvIndex);
                const auto inserted = additionalUvKinds.emplace(sourceIndex, offsetKind);
                if (!inserted.second && inserted.first->second != offsetKind) {
                    return false;
                }
            }

            const auto valuesIt = offset.find(valueField);
            if (valuesIt == offset.end() || !valuesIt->is_array() ||
                valuesIt->size() != componentCount) {
                return false;
            }
            std::array<double, 4>& values = accumulated[sourceIndex];
            for (size_t component = 0; component < componentCount; ++component) {
                if (!(*valuesIt)[component].is_number()) {
                    return false;
                }
                const double value = (*valuesIt)[component].get<double>();
                if (!std::isfinite(value)) {
                    return false;
                }
                values[component] += value;
                if (!std::isfinite(values[component])) {
                    return false;
                }
            }
        }

        for (const auto& entry : accumulated) {
            const uint32_t sourceIndex = entry.first;
            const std::array<double, 4>& values = entry.second;
            bool nonZero = false;
            for (size_t component = 0; component < componentCount; ++component) {
                nonZero = nonZero || values[component] != 0.0;
            }
            if (!nonZero) {
                continue;
            }

            std::vector<uint32_t>& signature = signatures[sourceIndex];
            signature.push_back(0xC0FFEE02U);
            signature.push_back(static_cast<uint32_t>(morphIndex));
            signature.push_back(
                type == "additionalUv" ? additionalUvKinds[sourceIndex] : morphKind);
            signature.push_back(static_cast<uint32_t>(componentCount));
            for (size_t component = 0; component < componentCount; ++component) {
                const double normalized = values[component] == 0.0 ? 0.0 : values[component];
                appendDoubleBits(signature, normalized);
            }
        }
    }
    return true;
}

void appendMarker(WeldKey& key, uint32_t marker, size_t count)
{
    key.words.push_back(marker);
    key.words.push_back(static_cast<uint32_t>(std::min<size_t>(count, std::numeric_limits<uint32_t>::max())));
}

void appendFloats(WeldKey& key, uint32_t marker, const std::vector<float>& values,
                  size_t offset, size_t count)
{
    appendMarker(key, marker, count);
    if (offset > values.size() || count > values.size() - offset) {
        key.words.push_back(0xFFFFFFFFU);
        return;
    }
    for (size_t i = 0; i < count; ++i) {
        key.words.push_back(floatBits(values[offset + i]));
    }
}

void appendU32(WeldKey& key, uint32_t marker, const std::vector<uint32_t>& values,
               size_t offset, size_t count)
{
    appendMarker(key, marker, count);
    if (offset > values.size() || count > values.size() - offset) {
        key.words.push_back(0xFFFFFFFFU);
        return;
    }
    key.words.insert(key.words.end(), values.begin() + static_cast<std::ptrdiff_t>(offset),
                     values.begin() + static_cast<std::ptrdiff_t>(offset + count));
}

void appendU8(WeldKey& key, uint32_t marker, const std::vector<uint8_t>& values,
              size_t offset, size_t count)
{
    appendMarker(key, marker, count);
    if (offset > values.size() || count > values.size() - offset) {
        key.words.push_back(0xFFFFFFFFU);
        return;
    }
    for (size_t i = 0; i < count; ++i) {
        key.words.push_back(values[offset + i]);
    }
}

bool loadRawGeometry(const std::vector<uint8_t>& bytes, RawGeometry& raw,
                     WeldProfile* profile = nullptr)
{
    if (bytes.empty()) {
        return false;
    }

    mmd_runtime_pmx_geometry_t* geometry =
        mmd_runtime_pmx_geometry_create(bytes.data(), bytes.size());
    if (profile) {
        profile->geometryParseCount += 1U;
    }
    if (!geometry) {
        return false;
    }

    raw.positions = takeFloatBuffer(mmd_runtime_pmx_geometry_positions_buffer(geometry));
    raw.skinIndices = takeU32Buffer(mmd_runtime_pmx_geometry_skin_indices_buffer(geometry));
    raw.skinWeights = takeFloatBuffer(mmd_runtime_pmx_geometry_skin_weights_buffer(geometry));
    raw.edgeScale = takeFloatBuffer(mmd_runtime_pmx_geometry_edge_scale_buffer(geometry));
    raw.sdefEnabled = takeU8Buffer(mmd_runtime_pmx_geometry_sdef_enabled_buffer(geometry));
    raw.sdefC = takeFloatBuffer(mmd_runtime_pmx_geometry_sdef_c_buffer(geometry));
    raw.sdefR0 = takeFloatBuffer(mmd_runtime_pmx_geometry_sdef_r0_buffer(geometry));
    raw.sdefR1 = takeFloatBuffer(mmd_runtime_pmx_geometry_sdef_r1_buffer(geometry));
    raw.sdefRw0 = takeFloatBuffer(mmd_runtime_pmx_geometry_sdef_rw0_buffer(geometry));
    raw.sdefRw1 = takeFloatBuffer(mmd_runtime_pmx_geometry_sdef_rw1_buffer(geometry));
    raw.qdefEnabled = takeU8Buffer(mmd_runtime_pmx_geometry_qdef_enabled_buffer(geometry));

    const size_t additionalUvCount =
        mmd_runtime_pmx_geometry_additional_uv_count(geometry);
    raw.additionalUvs.reserve(additionalUvCount);
    for (size_t i = 0; i < additionalUvCount; ++i) {
        raw.additionalUvs.push_back(takeFloatBuffer(
            mmd_runtime_pmx_geometry_additional_uvs_buffer(geometry, i)));
    }

    mmd_runtime_pmx_geometry_free(geometry);

    if (raw.positions.empty()) {
        return false;
    }

    const size_t sourceCount = raw.positions.size() / 3U;
    if (profile) {
        profile->nonGeometryParseCount += 1U;
    }
    const json nonGeometry = takeJsonBuffer(
        mmd_runtime_parse_pmx_non_geometry_json(bytes.data(), bytes.size()));
    return buildMorphSignatures(nonGeometry, sourceCount, raw.morphSignatures);
}

WeldKey makeWeldKey(const RawGeometry& raw, size_t sourceIndex)
{
    WeldKey key;
    appendFloats(key, 1U, raw.positions, sourceIndex * 3U, 3U);

    // Primary UV and authored normals are intentionally absent.  They are
    // face-corner data in Maya and are exactly what this command must split.
    appendU32(key, 2U, raw.skinIndices, sourceIndex * 4U, 4U);
    appendFloats(key, 3U, raw.skinWeights, sourceIndex * 4U, 4U);
    appendFloats(key, 4U, raw.edgeScale, sourceIndex, 1U);
    appendU8(key, 5U, raw.sdefEnabled, sourceIndex, 1U);
    appendFloats(key, 6U, raw.sdefC, sourceIndex * 3U, 3U);
    appendFloats(key, 7U, raw.sdefR0, sourceIndex * 3U, 3U);
    appendFloats(key, 8U, raw.sdefR1, sourceIndex * 3U, 3U);
    appendFloats(key, 9U, raw.sdefRw0, sourceIndex * 3U, 3U);
    appendFloats(key, 10U, raw.sdefRw1, sourceIndex * 3U, 3U);
    appendU8(key, 11U, raw.qdefEnabled, sourceIndex, 1U);
    for (size_t uvIndex = 0; uvIndex < raw.additionalUvs.size(); ++uvIndex) {
        appendFloats(key, static_cast<uint32_t>(100U + uvIndex), raw.additionalUvs[uvIndex],
                     sourceIndex * 4U, 4U);
    }

    // Exact sparse morph signatures make seam copies weldable when every
    // Vertex/UV/Additional-UV morph deforms them identically. Missing and
    // explicitly-zero offsets both contribute an empty signature.
    if (sourceIndex < raw.morphSignatures.size()) {
        appendMarker(key, 200U, raw.morphSignatures[sourceIndex].size());
        key.words.insert(key.words.end(), raw.morphSignatures[sourceIndex].begin(),
                         raw.morphSignatures[sourceIndex].end());
    }
    return key;
}

bool readSourceVertexIndices(const MObject& transform, size_t vertexCount,
                             std::vector<uint32_t>& sourceIndices)
{
    sourceIndices.clear();
    MStatus status;
    MFnDependencyNode dependencyFn(transform, &status);
    if (!status) {
        return false;
    }

    MPlug plug = dependencyFn.findPlug(kSourceVertexAttribute, true, &status);
    if (status && !plug.isNull()) {
        const MObject dataObject = plug.asMObject(&status);
        if (!status || dataObject.isNull()) {
            return false;
        }
        MFnIntArrayData dataFn(dataObject, &status);
        if (!status) {
            return false;
        }
        MIntArray values = dataFn.array(&status);
        if (!status || values.length() != vertexCount) {
            return false;
        }
        sourceIndices.reserve(vertexCount);
        for (unsigned int i = 0; i < values.length(); ++i) {
            if (values[i] < 0) {
                return false;
            }
            sourceIndices.push_back(static_cast<uint32_t>(values[i]));
        }
        return true;
    }

    sourceIndices.resize(vertexCount);
    for (size_t i = 0; i < vertexCount; ++i) {
        sourceIndices[i] = static_cast<uint32_t>(i);
    }
    return true;
}

bool writeSourceVertexIndices(const MObject& transform,
                              const std::vector<uint32_t>& sourceIndices)
{
    MStatus status;
    MFnDependencyNode dependencyFn(transform, &status);
    if (!status) {
        return false;
    }

    MPlug plug = dependencyFn.findPlug(kSourceVertexAttribute, true, &status);
    if (!status || plug.isNull()) {
        MFnTypedAttribute typedAttribute;
        const MObject attribute = typedAttribute.create(
            kSourceVertexAttribute, kSourceVertexAttribute, MFnData::kIntArray, &status);
        if (!status) {
            return false;
        }
        typedAttribute.setStorable(true);
        status = dependencyFn.addAttribute(attribute);
        if (!status) {
            return false;
        }
        plug = dependencyFn.findPlug(kSourceVertexAttribute, true, &status);
    }
    if (!status || plug.isNull()) {
        return false;
    }

    MIntArray values;
    values.setLength(static_cast<unsigned int>(sourceIndices.size()));
    for (size_t i = 0; i < sourceIndices.size(); ++i) {
        if (sourceIndices[i] > static_cast<uint32_t>(std::numeric_limits<int>::max())) {
            return false;
        }
        values[static_cast<unsigned int>(i)] = static_cast<int>(sourceIndices[i]);
    }
    MFnIntArrayData dataFn;
    const MObject dataObject = dataFn.create(values, &status);
    if (!status) {
        return false;
    }
    return plug.setMObject(dataObject) == MS::kSuccess;
}

bool readSourceToLocalIndices(const MObject& transform, size_t sourceCount,
                              size_t vertexCount, std::vector<int>& values)
{
    values.clear();
    MStatus status;
    MFnDependencyNode dependencyFn(transform, &status);
    if (!status) {
        return false;
    }
    MPlug plug = dependencyFn.findPlug(kSourceToLocalAttribute, true, &status);
    if (!status || plug.isNull()) {
        return true;
    }
    const MObject dataObject = plug.asMObject(&status);
    if (!status || dataObject.isNull()) {
        return false;
    }
    MFnIntArrayData dataFn(dataObject, &status);
    if (!status) {
        return false;
    }
    const MIntArray rawValues = dataFn.array(&status);
    if (!status || rawValues.length() != sourceCount) {
        return false;
    }
    values.reserve(sourceCount);
    for (unsigned int source = 0; source < rawValues.length(); ++source) {
        const int local = rawValues[source];
        if (local < -1 || (local >= 0 && static_cast<size_t>(local) >= vertexCount)) {
            return false;
        }
        values.push_back(local);
    }
    return true;
}

bool writeSourceToLocalIndices(const MObject& transform,
                               const std::vector<int>& sourceToLocal)
{
    MStatus status;
    MFnDependencyNode dependencyFn(transform, &status);
    if (!status) {
        return false;
    }
    MPlug plug = dependencyFn.findPlug(kSourceToLocalAttribute, true, &status);
    if (!status || plug.isNull()) {
        MFnTypedAttribute typedAttribute;
        const MObject attribute = typedAttribute.create(
            kSourceToLocalAttribute, kSourceToLocalAttribute, MFnData::kIntArray, &status);
        typedAttribute.setStorable(true);
        if (!status) {
            return false;
        }
        status = dependencyFn.addAttribute(attribute);
        if (!status) {
            return false;
        }
        plug = dependencyFn.findPlug(kSourceToLocalAttribute, true, &status);
    }
    if (!status || plug.isNull()) {
        return false;
    }

    MIntArray values;
    values.setLength(static_cast<unsigned int>(sourceToLocal.size()));
    for (size_t source = 0; source < sourceToLocal.size(); ++source) {
        values[static_cast<unsigned int>(source)] = sourceToLocal[source];
    }
    MFnIntArrayData dataFn;
    const MObject dataObject = dataFn.create(values, &status);
    if (!status) {
        return false;
    }
    return plug.setMObject(dataObject) == MS::kSuccess;
}

bool collectUvSets(const MFnMesh& meshFn, std::vector<UvSetData>& uvSets)
{
    MStringArray names;
    MStatus status = meshFn.getUVSetNames(names);
    if (!status) {
        return false;
    }
    uvSets.clear();
    uvSets.reserve(names.length());
    for (unsigned int i = 0; i < names.length(); ++i) {
        UvSetData data;
        data.name = names[i];
        if (!meshFn.getUVs(data.u, data.v, &data.name) ||
            !meshFn.getAssignedUVs(data.counts, data.ids, &data.name)) {
            return false;
        }
        uvSets.push_back(data);
    }
    return true;
}

bool collectFaceVertexNormals(const MFnMesh& meshFn, const MIntArray& faceCounts,
                              std::vector<MVector>& normals)
{
    normals.clear();
    size_t cornerCount = 0;
    for (unsigned int i = 0; i < faceCounts.length(); ++i) {
        if (faceCounts[i] < 0) {
            return false;
        }
        cornerCount += static_cast<size_t>(faceCounts[i]);
    }
    MFloatVectorArray meshNormals;
    MIntArray normalCounts;
    MIntArray normalIds;
    if (!meshFn.getNormals(meshNormals, MSpace::kObject) ||
        !meshFn.getNormalIds(normalCounts, normalIds) ||
        normalCounts.length() != faceCounts.length() ||
        normalIds.length() != cornerCount) {
        return false;
    }

    normals.reserve(cornerCount);
    for (unsigned int corner = 0; corner < normalIds.length(); ++corner) {
        const int normalId = normalIds[corner];
        if (normalId < 0 || normalId >= static_cast<int>(meshNormals.length())) {
            return false;
        }
        const MFloatVector& normal = meshNormals[static_cast<unsigned int>(normalId)];
        normals.emplace_back(static_cast<double>(normal.x), static_cast<double>(normal.y),
                             static_cast<double>(normal.z));
    }
    return normals.size() == cornerCount;
}

bool transferShaders(const MObjectArray& shaders, const MIntArray& shaderIndices,
                     const MDagPath& newMeshPath)
{
    for (unsigned int shaderIndex = 0; shaderIndex < shaders.length(); ++shaderIndex) {
        if (shaders[shaderIndex].isNull()) {
            continue;
        }
        MIntArray faceIds;
        for (unsigned int face = 0; face < shaderIndices.length(); ++face) {
            if (shaderIndices[face] == static_cast<int>(shaderIndex)) {
                faceIds.append(static_cast<int>(face));
            }
        }
        if (faceIds.length() == 0) {
            continue;
        }

        MStatus status;
        MFnSingleIndexedComponent componentFn;
        const MObject component = componentFn.create(MFn::kMeshPolygonComponent, &status);
        if (!status || !componentFn.addElements(faceIds)) {
            return false;
        }
        MFnSet setFn(shaders[shaderIndex], &status);
        if (!status || !setFn.addMember(newMeshPath, component)) {
            return false;
        }
    }
    return true;
}

MStatus validateWeldMesh(const MString& meshName, const RawGeometry& raw)
{
    MSelectionList selection;
    MStatus status = selection.add(meshName);
    if (!status || selection.length() == 0U) {
        return MS::kFailure;
    }

    MDagPath meshPath;
    status = selection.getDagPath(0, meshPath);
    if (!status) {
        return status;
    }
    if (meshPath.node().hasFn(MFn::kTransform)) {
        status = meshPath.extendToShape();
        if (!status) {
            return status;
        }
    }
    if (!meshPath.node().hasFn(MFn::kMesh)) {
        return MS::kFailure;
    }

    MFnMesh meshFn(meshPath, &status);
    if (!status) {
        return status;
    }
    MObject transformObject = meshPath.transform(&status);
    if (!status) {
        return status;
    }

    MPointArray points;
    MIntArray faceCounts;
    MIntArray polygonConnects;
    if (!meshFn.getPoints(points, MSpace::kObject) ||
        !meshFn.getVertices(faceCounts, polygonConnects)) {
        return MS::kFailure;
    }
    if (points.length() == 0U || faceCounts.length() == 0U) {
        return MS::kSuccess;
    }

    size_t polygonCursor = 0;
    for (unsigned int face = 0; face < faceCounts.length(); ++face) {
        const int count = faceCounts[face];
        if (count < 0 || polygonCursor + static_cast<size_t>(count) > polygonConnects.length()) {
            return MS::kFailure;
        }
        for (int local = 0; local < count; ++local) {
            const int vertex = polygonConnects[static_cast<unsigned int>(polygonCursor + local)];
            if (vertex < 0 || vertex >= static_cast<int>(points.length())) {
                return MS::kFailure;
            }
        }
        polygonCursor += static_cast<size_t>(count);
    }
    if (polygonCursor != polygonConnects.length()) {
        return MS::kFailure;
    }

    std::vector<uint32_t> sourceIndices;
    if (!readSourceVertexIndices(transformObject, points.length(), sourceIndices)) {
        return MS::kFailure;
    }
    const size_t sourceCount = raw.positions.size() / 3U;
    for (uint32_t sourceIndex : sourceIndices) {
        if (sourceIndex >= sourceCount) {
            return MS::kFailure;
        }
    }
    std::vector<int> sourceToOldLocal;
    if (!readSourceToLocalIndices(
            transformObject, sourceCount, points.length(), sourceToOldLocal)) {
        return MS::kFailure;
    }
    std::vector<UvSetData> uvSets;
    if (!collectUvSets(meshFn, uvSets)) {
        return MS::kFailure;
    }
    std::vector<MVector> faceVertexNormals;
    if (!collectFaceVertexNormals(meshFn, faceCounts, faceVertexNormals)) {
        return MS::kFailure;
    }
    return MS::kSuccess;
}

MStatus weldMesh(const MString& meshName, const RawGeometry& raw,
                 unsigned int& oldVertexCount, unsigned int& newVertexCount,
                 bool failOnNoOp = false)
{
    MSelectionList selection;
    MStatus status = selection.add(meshName);
    if (!status || selection.length() == 0) {
        MGlobal::displayError("[mmdWeldUvSeamVertices] Mesh was not found.");
        return MS::kFailure;
    }

    MDagPath meshPath;
    status = selection.getDagPath(0, meshPath);
    if (!status) {
        return status;
    }
    if (meshPath.node().hasFn(MFn::kTransform)) {
        status = meshPath.extendToShape();
        if (!status) {
            MGlobal::displayError("[mmdWeldUvSeamVertices] Transform has no mesh shape.");
            return status;
        }
    }
    if (!meshPath.node().hasFn(MFn::kMesh)) {
        MGlobal::displayError("[mmdWeldUvSeamVertices] Target is not a mesh.");
        return MS::kFailure;
    }

    MFnMesh meshFn(meshPath, &status);
    if (!status) {
        return status;
    }
    MObject transformObject = meshPath.transform(&status);
    if (!status) {
        return status;
    }

    MPointArray oldPoints;
    MIntArray faceCounts;
    MIntArray polygonConnects;
    if (!meshFn.getPoints(oldPoints, MSpace::kObject) ||
        !meshFn.getVertices(faceCounts, polygonConnects)) {
        MGlobal::displayError("[mmdWeldUvSeamVertices] Failed to read mesh topology.");
        return MS::kFailure;
    }
    oldVertexCount = oldPoints.length();
    newVertexCount = oldVertexCount;
    if (oldVertexCount == 0 || faceCounts.length() == 0) {
        return MS::kSuccess;
    }

    const size_t sourceCount = raw.positions.size() / 3U;

    std::vector<uint32_t> sourceIndices;
    if (!readSourceVertexIndices(transformObject, oldVertexCount, sourceIndices)) {
        MGlobal::displayWarning(
            "[mmdWeldUvSeamVertices] Source-vertex mapping is invalid; keeping original topology.");
        return failOnNoOp ? MS::kFailure : MS::kSuccess;
    }
    for (uint32_t sourceIndex : sourceIndices) {
        if (sourceIndex >= sourceCount) {
            MGlobal::displayWarning(
                "[mmdWeldUvSeamVertices] Source-vertex mapping is out of range; keeping original topology.");
            return failOnNoOp ? MS::kFailure : MS::kSuccess;
        }
    }
    std::vector<int> sourceToOldLocal;
    if (!readSourceToLocalIndices(
            transformObject, sourceCount, oldVertexCount, sourceToOldLocal)) {
        MGlobal::displayWarning(
            "[mmdWeldUvSeamVertices] Source-to-local mapping is invalid; keeping original topology.");
        return failOnNoOp ? MS::kFailure : MS::kSuccess;
    }
    if (sourceToOldLocal.empty()) {
        sourceToOldLocal.assign(sourceCount, -1);
        for (size_t local = 0; local < sourceIndices.size(); ++local) {
            sourceToOldLocal[sourceIndices[local]] = static_cast<int>(local);
        }
    }

    std::unordered_map<WeldKey, unsigned int, WeldKeyHash> candidateGroups;
    candidateGroups.reserve(oldVertexCount);
    std::vector<unsigned int> groupByVertex(oldVertexCount, 0U);
    unsigned int groupCount = 0;
    for (unsigned int vertex = 0; vertex < oldVertexCount; ++vertex) {
        WeldKey key = makeWeldKey(raw, sourceIndices[vertex]);
        const auto inserted = candidateGroups.emplace(std::move(key), groupCount);
        if (inserted.second) {
            ++groupCount;
        }
        groupByVertex[vertex] = inserted.first->second;
    }

    // Do not turn a face into a degenerate polygon.  This is rare for UV
    // seam duplicates, but it matters for malformed/overlapping PMX meshes.
    std::vector<bool> conflictingGroup(groupCount, false);
    size_t cursor = 0;
    for (unsigned int face = 0; face < faceCounts.length(); ++face) {
        std::unordered_map<unsigned int, unsigned int> firstVertex;
        const int count = faceCounts[face];
        if (count < 0 || cursor + static_cast<size_t>(count) > polygonConnects.length()) {
            return MS::kFailure;
        }
        for (int local = 0; local < count; ++local) {
            const int oldVertex = polygonConnects[static_cast<unsigned int>(cursor + local)];
            if (oldVertex < 0 || oldVertex >= static_cast<int>(oldVertexCount)) {
                return MS::kFailure;
            }
            const unsigned int group = groupByVertex[static_cast<unsigned int>(oldVertex)];
            const auto inserted = firstVertex.emplace(group, static_cast<unsigned int>(oldVertex));
            if (!inserted.second && inserted.first->second != static_cast<unsigned int>(oldVertex)) {
                conflictingGroup[group] = true;
            }
        }
        cursor += static_cast<size_t>(count);
    }

    std::vector<unsigned int> localByVertex(oldVertexCount, 0U);
    std::vector<uint32_t> newSourceIndices;
    std::vector<unsigned int> representativeByGroup(groupCount, std::numeric_limits<unsigned int>::max());
    unsigned int localCount = 0;
    for (unsigned int vertex = 0; vertex < oldVertexCount; ++vertex) {
        const unsigned int group = groupByVertex[vertex];
        if (conflictingGroup[group]) {
            localByVertex[vertex] = localCount++;
            newSourceIndices.push_back(sourceIndices[vertex]);
            continue;
        }
        unsigned int& representative = representativeByGroup[group];
        if (representative == std::numeric_limits<unsigned int>::max()) {
            representative = vertex;
            localByVertex[vertex] = localCount++;
            newSourceIndices.push_back(sourceIndices[vertex]);
        } else {
            localByVertex[vertex] = localByVertex[representative];
        }
    }
    newVertexCount = localCount;
    std::vector<int> sourceToNewLocal(sourceCount, -1);
    for (size_t source = 0; source < sourceToOldLocal.size(); ++source) {
        const int oldLocal = sourceToOldLocal[source];
        if (oldLocal >= 0) {
            sourceToNewLocal[source] = static_cast<int>(
                localByVertex[static_cast<unsigned int>(oldLocal)]);
        }
    }
    if (newVertexCount >= oldVertexCount) {
        if (!writeSourceToLocalIndices(transformObject, sourceToNewLocal)) {
            MGlobal::displayWarning(
                "[mmdWeldUvSeamVertices] Failed to write source-to-local mapping; keeping original topology.");
            return failOnNoOp ? MS::kFailure : MS::kSuccess;
        }
        // A valid mesh with no mergeable UV seam is a successful no-op.  The
        // batch contract reserves failure for invalid input or an operation
        // that could not preserve the mesh attributes.
        return MS::kSuccess;
    }

    std::vector<UvSetData> uvSets;
    if (!collectUvSets(meshFn, uvSets)) {
        MGlobal::displayWarning(
            "[mmdWeldUvSeamVertices] UV data could not be read; keeping original topology.");
        return failOnNoOp ? MS::kFailure : MS::kSuccess;
    }
    MStatus currentUvStatus;
    const MString currentUvSetName = meshFn.currentUVSetName(&currentUvStatus);

    std::vector<MVector> faceVertexNormals;
    if (!collectFaceVertexNormals(meshFn, faceCounts, faceVertexNormals)) {
        MGlobal::displayWarning(
            "[mmdWeldUvSeamVertices] Face-vertex normals could not be read; keeping original topology.");
        return failOnNoOp ? MS::kFailure : MS::kSuccess;
    }

    MObjectArray shaders;
    MIntArray shaderIndices;
    meshFn.getConnectedShaders(0, shaders, shaderIndices);

    MPointArray newPoints;
    newPoints.setLength(newVertexCount);
    // Assign representatives explicitly so an actual origin vertex is handled
    // the same way as every other point.
    for (unsigned int group = 0; group < groupCount; ++group) {
        const unsigned int representative = representativeByGroup[group];
        if (representative != std::numeric_limits<unsigned int>::max()) {
            newPoints[localByVertex[representative]] = oldPoints[representative];
        }
    }
    for (unsigned int vertex = 0; vertex < oldVertexCount; ++vertex) {
        if (conflictingGroup[groupByVertex[vertex]]) {
            newPoints[localByVertex[vertex]] = oldPoints[vertex];
        }
    }

    MIntArray newPolygonConnects;
    newPolygonConnects.setLength(polygonConnects.length());
    for (unsigned int i = 0; i < polygonConnects.length(); ++i) {
        newPolygonConnects[i] = static_cast<int>(
            localByVertex[static_cast<unsigned int>(polygonConnects[i])]);
    }

    MStatus createStatus;
    MFnMesh newMeshFn;
    const MObject newMeshObject = newMeshFn.create(
        static_cast<int>(newVertexCount), static_cast<int>(faceCounts.length()),
        newPoints, faceCounts, newPolygonConnects, transformObject, &createStatus);
    if (!createStatus || newMeshObject.isNull()) {
        MGlobal::displayError("[mmdWeldUvSeamVertices] Failed to create welded mesh.");
        return MS::kFailure;
    }

    MDagPath newMeshPath;
    if (!MDagPath::getAPathTo(newMeshObject, newMeshPath)) {
        return MS::kFailure;
    }

    // Copy all UV sets using their original per-face-corner IDs.  A newly
    // created MFnMesh already owns an empty map1 set.  Recreating map1 makes
    // Maya silently allocate map11, leaving TEXCOORD0 bound to the empty map1.
    // Reuse any set created with the mesh and only create genuinely new sets.
    for (const UvSetData& uvSet : uvSets) {
        MStatus uvStatus;
        MStringArray existingNames;
        uvStatus = newMeshFn.getUVSetNames(existingNames);
        bool hasUvSet = false;
        for (unsigned int i = 0; uvStatus && i < existingNames.length(); ++i) {
            if (existingNames[i] == uvSet.name) {
                hasUvSet = true;
                break;
            }
        }

        MString targetName = uvSet.name;
        if (uvStatus && !hasUvSet) {
            targetName = newMeshFn.createUVSetWithName(uvSet.name, nullptr, &uvStatus);
        }
        if (!uvStatus || !newMeshFn.setUVs(uvSet.u, uvSet.v, &targetName) ||
            !newMeshFn.assignUVs(uvSet.counts, uvSet.ids, &targetName)) {
            MDagModifier cleanup;
            cleanup.deleteNode(newMeshObject);
            cleanup.doIt();
            MGlobal::displayError("[mmdWeldUvSeamVertices] Failed to copy UV sets.");
            return MS::kFailure;
        }
    }
    if (currentUvStatus && currentUvSetName.length() != 0U &&
        !newMeshFn.setCurrentUVSetName(currentUvSetName)) {
        MDagModifier cleanup;
        cleanup.deleteNode(newMeshObject);
        cleanup.doIt();
        MGlobal::displayError("[mmdWeldUvSeamVertices] Failed to restore current UV set.");
        return MS::kFailure;
    }

    // Restore authored face-corner normals.  Merging the geometric vertex is
    // allowed to keep a hard normal seam, so using per-vertex normals here
    // would reintroduce the exact visual regression this command avoids.
    MVectorArray normals;
    MIntArray normalFaces;
    MIntArray normalVertices;
    cursor = 0;
    for (unsigned int face = 0; face < faceCounts.length(); ++face) {
        for (int localVertex = 0; localVertex < faceCounts[face]; ++localVertex) {
            normals.append(faceVertexNormals[cursor]);
            normalFaces.append(static_cast<int>(face));
            normalVertices.append(newPolygonConnects[static_cast<unsigned int>(cursor)]);
            ++cursor;
        }
    }
    if (normals.length() > 0 &&
        !newMeshFn.setFaceVertexNormals(normals, normalFaces, normalVertices, MSpace::kObject)) {
        MDagModifier cleanup;
        cleanup.deleteNode(newMeshObject);
        cleanup.doIt();
        MGlobal::displayError("[mmdWeldUvSeamVertices] Failed to copy face-vertex normals.");
        return MS::kFailure;
    }

    if (!transferShaders(shaders, shaderIndices, newMeshPath)) {
        MDagModifier cleanup;
        cleanup.deleteNode(newMeshObject);
        cleanup.doIt();
        MGlobal::displayError("[mmdWeldUvSeamVertices] Failed to copy shading assignments.");
        return MS::kFailure;
    }

    if (!writeSourceVertexIndices(transformObject, newSourceIndices)) {
        MDagModifier cleanup;
        cleanup.deleteNode(newMeshObject);
        cleanup.doIt();
        MGlobal::displayError("[mmdWeldUvSeamVertices] Failed to write source-vertex mapping.");
        return MS::kFailure;
    }

    if (!writeSourceToLocalIndices(transformObject, sourceToNewLocal)) {
        writeSourceVertexIndices(transformObject, sourceIndices);
        MDagModifier cleanup;
        cleanup.deleteNode(newMeshObject);
        cleanup.doIt();
        MGlobal::displayError("[mmdWeldUvSeamVertices] Failed to write source-to-local mapping.");
        return MS::kFailure;
    }

    MDagModifier deleteOld;
    deleteOld.deleteNode(meshPath.node());
    status = deleteOld.doIt();
    if (!status) {
        writeSourceVertexIndices(transformObject, sourceIndices);
        writeSourceToLocalIndices(transformObject, sourceToOldLocal);
        MGlobal::displayError("[mmdWeldUvSeamVertices] Failed to replace original mesh shape.");
        MDagModifier cleanup;
        cleanup.deleteNode(newMeshObject);
        cleanup.doIt();
        return status;
    }

    return MS::kSuccess;
}

} // namespace

void* MmdWeldUvSeamVertices::creator()
{
    return new MmdWeldUvSeamVertices();
}

MSyntax MmdWeldUvSeamVertices::newSyntax()
{
    MSyntax syntax;
    syntax.addFlag("-m", "-mesh", MSyntax::kString);
    syntax.addFlag("-f", "-file", MSyntax::kString);
    syntax.addFlag("-qc", "-queryCapabilities", MSyntax::kBoolean);
    syntax.addFlag("-b", "-batch", MSyntax::kBoolean);
    syntax.addFlag("-p", "-profile", MSyntax::kBoolean);
    syntax.makeFlagMultiUse("-m");
    syntax.enableEdit(false);
    return syntax;
}

MStatus MmdWeldUvSeamVertices::doIt(const MArgList& args)
{
    MStatus parseStatus;
    MArgDatabase argData(newSyntax(), args, &parseStatus);
    if (!parseStatus) {
        return parseStatus;
    }
    if (argData.isFlagSet("-qc")) {
        bool queryCapabilities = false;
        const MStatus queryStatus = argData.getFlagArgument("-qc", 0, queryCapabilities);
        if (!queryStatus || !queryCapabilities) {
            MGlobal::displayError(
                "[mmdWeldUvSeamVertices] -queryCapabilities requires true.");
            return MS::kFailure;
        }
        MStringArray capabilities;
        capabilities.append(kSourceToLocalCapability);
        capabilities.append(kMorphEquivalentCapability);
        capabilities.append(kBatchCapability);
        capabilities.append(kProfileCapability);
        setResult(capabilities);
        return MS::kSuccess;
    }
    if (!argData.isFlagSet("-m") || !argData.isFlagSet("-f")) {
        MGlobal::displayError(
            "[mmdWeldUvSeamVertices] Required flags: -mesh <transform> -file <pmx>." );
        return MS::kFailure;
    }

    bool batch = false;
    if (argData.isFlagSet("-b")) {
        const MStatus batchStatus = argData.getFlagArgument("-b", 0, batch);
        if (!batchStatus || !batch) {
            MGlobal::displayError(
                "[mmdWeldUvSeamVertices] -batch requires true.");
            return MS::kFailure;
        }
    }
    bool profileRequested = false;
    if (argData.isFlagSet("-p")) {
        const MStatus profileStatus = argData.getFlagArgument("-p", 0, profileRequested);
        if (!profileStatus || !profileRequested) {
            MGlobal::displayError(
                "[mmdWeldUvSeamVertices] -profile requires true.");
            return MS::kFailure;
        }
    }
    const MString pmxPath = argData.flagArgumentString("-f", 0);
    MStringArray meshNames;
    if (batch) {
        const unsigned int meshUseCount = argData.numberOfFlagUses("-m");
        for (unsigned int use = 0; use < meshUseCount; ++use) {
            MArgList meshArgs;
            if (!argData.getFlagArgumentList("-m", use, meshArgs) ||
                meshArgs.length() != 1U) {
                MGlobal::displayError(
                    "[mmdWeldUvSeamVertices] Each -mesh use needs one transform.");
                return MS::kFailure;
            }
            meshNames.append(meshArgs.asString(0));
        }
        if (meshNames.length() == 0U) {
            MGlobal::displayError(
                "[mmdWeldUvSeamVertices] -batch requires at least one -mesh.");
            return MS::kFailure;
        }
    } else {
        meshNames.append(argData.flagArgumentString("-m", 0));
    }

    WeldProfile weldProfile;
    const auto preparationStart = std::chrono::steady_clock::now();
    const std::vector<uint8_t> bytes = readBinaryFile(pmxPath.asUTF8());
    weldProfile.pmxReadCount = 1U;
    RawGeometry raw;
    const bool rawLoaded = loadRawGeometry(bytes, raw, &weldProfile);
    weldProfile.preparationSeconds = std::chrono::duration<double>(
        std::chrono::steady_clock::now() - preparationStart).count();

    MStringArray result;
    std::vector<MStatus> preflightStatuses;
    bool allPreflightSucceeded = rawLoaded;
    if (batch) {
        preflightStatuses.resize(meshNames.length(), MS::kFailure);
        if (rawLoaded) {
            for (unsigned int meshIndex = 0; meshIndex < meshNames.length(); ++meshIndex) {
                preflightStatuses[meshIndex] = validateWeldMesh(meshNames[meshIndex], raw);
                allPreflightSucceeded = allPreflightSucceeded &&
                    preflightStatuses[meshIndex] == MS::kSuccess;
                if (!preflightStatuses[meshIndex]) {
                    MGlobal::displayError(
                        MString("[mmdWeldUvSeamVertices] Batch preflight failed for ") +
                        meshNames[meshIndex]);
                }
            }
        }
    }
    bool allSucceeded = rawLoaded && (!batch || allPreflightSucceeded);
    for (unsigned int meshIndex = 0; meshIndex < meshNames.length(); ++meshIndex) {
        const MString& meshName = meshNames[meshIndex];
        unsigned int oldVertexCount = 0;
        unsigned int newVertexCount = 0;
        const auto applicationStart = std::chrono::steady_clock::now();
        const bool canApply = !batch ||
            (rawLoaded && allPreflightSucceeded &&
             preflightStatuses[meshIndex] == MS::kSuccess);
        const MStatus status = canApply
            ? weldMesh(meshName, raw, oldVertexCount, newVertexCount, batch)
            : MS::kFailure;
        weldProfile.applicationSeconds += std::chrono::duration<double>(
            std::chrono::steady_clock::now() - applicationStart).count();
        const bool succeeded = status == MS::kSuccess;
        allSucceeded = allSucceeded && succeeded;

        if (batch) {
            const char* batchStatus = succeeded
                ? "ok"
                : (!rawLoaded || preflightStatuses[meshIndex] != MS::kSuccess
                    ? "preflight_failed"
                    : "blocked");
            result.append(meshName);
            result.append(batchStatus);
            result.append(MString(std::to_string(oldVertexCount).c_str()));
            result.append(MString(std::to_string(newVertexCount).c_str()));
        } else if (!succeeded) {
            return status;
        } else {
            result.append(meshName);
            result.append(MString(std::to_string(oldVertexCount).c_str()));
            result.append(MString(std::to_string(newVertexCount).c_str()));
            if (profileRequested) {
                result.append(MString(std::to_string(weldProfile.preparationSeconds).c_str()));
                result.append(MString(std::to_string(weldProfile.applicationSeconds).c_str()));
                result.append(MString(std::to_string(weldProfile.pmxReadCount).c_str()));
                result.append(MString(std::to_string(weldProfile.geometryParseCount).c_str()));
                result.append(MString(std::to_string(weldProfile.nonGeometryParseCount).c_str()));
            }
            setResult(result);
            if (newVertexCount < oldVertexCount) {
                MGlobal::displayInfo(
                    MString("[mmdWeldUvSeamVertices] Welded ") +
                    std::to_string(oldVertexCount - newVertexCount).c_str() +
                    " UV-seam vertex slots.");
            }
            return MS::kSuccess;
        }
    }

    if (batch) {
        result.append("__profile__");
        result.append(MString(std::to_string(weldProfile.preparationSeconds).c_str()));
        result.append(MString(std::to_string(weldProfile.applicationSeconds).c_str()));
        result.append(MString(std::to_string(weldProfile.pmxReadCount).c_str()));
        result.append(MString(std::to_string(weldProfile.geometryParseCount).c_str()));
        result.append(MString(std::to_string(weldProfile.nonGeometryParseCount).c_str()));
        setResult(result);
        if (!allSucceeded) {
            MGlobal::displayError(
                "[mmdWeldUvSeamVertices] One or more batch mesh welds failed.");
        }
        // Keep the per-mesh status in the result so the Python importer can
        // fail closed while preserving a diagnostic record for every target.
        return MS::kSuccess;
    }
    return MS::kFailure;
}

bool MmdWeldUvSeamVertices::isUndoable() const
{
    return false;
}
