#include "MmdUvSeamWeldPlan.h"

#include "mmd_runtime.h"
#include "third_party/json.hpp"

#include <algorithm>
#include <array>
#include <cmath>
#include <cstring>
#include <limits>
#include <string>
#include <unordered_map>

using nlohmann::json;

namespace {

struct WeldKey {
    std::vector<uint32_t> words;

    bool operator==(const WeldKey& other) const { return words == other.words; }
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

uint32_t floatBits(float value)
{
    uint32_t bits = 0;
    static_assert(sizeof(bits) == sizeof(value), "float and uint32_t must match");
    std::memcpy(&bits, &value, sizeof(bits));
    return bits;
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
        if (!morph.is_object()) return false;
        const std::string type = morph.value("type", std::string());
        const char* offsetsField = nullptr;
        const char* valueField = nullptr;
        size_t componentCount = 0;
        uint32_t morphKind = 0;
        if (type == "vertex") { offsetsField = "vertexOffsets"; valueField = "position"; componentCount = 3; morphKind = 1; }
        else if (type == "uv") { offsetsField = "uvOffsets"; valueField = "uv"; componentCount = 4; morphKind = 3; }
        else if (type == "additionalUv") { offsetsField = "additionalUvOffsets"; valueField = "uv"; componentCount = 4; morphKind = 4; }
        else continue;
        const auto offsetsIt = morph.find(offsetsField);
        if (offsetsIt == morph.end() || !offsetsIt->is_array()) return false;
        std::unordered_map<uint32_t, std::array<double, 4>> accumulated;
        std::unordered_map<uint32_t, uint32_t> additionalUvKinds;
        for (const json& offset : *offsetsIt) {
            if (!offset.is_object()) return false;
            const auto sourceIt = offset.find("vertexIndex");
            if (sourceIt == offset.end() || !sourceIt->is_number_integer()) return false;
            const int64_t sourceValue = sourceIt->get<int64_t>();
            if (sourceValue < 0 || static_cast<size_t>(sourceValue) >= sourceCount) return false;
            const uint32_t sourceIndex = static_cast<uint32_t>(sourceValue);
            uint32_t offsetKind = morphKind;
            if (type == "additionalUv") {
                const auto uvIndexIt = offset.find("uvIndex");
                if (uvIndexIt == offset.end() || !uvIndexIt->is_number_integer()) return false;
                const int64_t uvIndex = uvIndexIt->get<int64_t>();
                if (uvIndex < 0 || uvIndex > 3) return false;
                offsetKind += static_cast<uint32_t>(uvIndex);
                const auto inserted = additionalUvKinds.emplace(sourceIndex, offsetKind);
                if (!inserted.second && inserted.first->second != offsetKind) return false;
            }
            const auto valuesIt = offset.find(valueField);
            if (valuesIt == offset.end() || !valuesIt->is_array() || valuesIt->size() != componentCount) return false;
            std::array<double, 4>& values = accumulated[sourceIndex];
            for (size_t component = 0; component < componentCount; ++component) {
                if (!(*valuesIt)[component].is_number()) return false;
                const double value = (*valuesIt)[component].get<double>();
                if (!std::isfinite(value)) return false;
                values[component] += value;
                if (!std::isfinite(values[component])) return false;
            }
        }
        for (const auto& entry : accumulated) {
            const uint32_t sourceIndex = entry.first;
            const std::array<double, 4>& values = entry.second;
            bool nonZero = false;
            for (size_t component = 0; component < componentCount; ++component) nonZero = nonZero || values[component] != 0.0;
            if (!nonZero) continue;
            std::vector<uint32_t>& signature = signatures[sourceIndex];
            signature.push_back(0xC0FFEE02U);
            signature.push_back(static_cast<uint32_t>(morphIndex));
            signature.push_back(type == "additionalUv" ? additionalUvKinds[sourceIndex] : morphKind);
            signature.push_back(static_cast<uint32_t>(componentCount));
            for (size_t component = 0; component < componentCount; ++component) appendDoubleBits(signature, values[component] == 0.0 ? 0.0 : values[component]);
        }
    }
    return true;
}

void appendMarker(WeldKey& key, uint32_t marker, size_t count)
{
    key.words.push_back(marker);
    key.words.push_back(static_cast<uint32_t>(std::min<size_t>(
        count, std::numeric_limits<uint32_t>::max())));
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

WeldKey makeWeldKey(const MmdUvSeamWeldGeometry& geometry, size_t sourceIndex)
{
    WeldKey key;
    appendFloats(key, 1U, geometry.positions, sourceIndex * 3U, 3U);
    // Primary UV and normals are face-corner data in Maya, and intentionally
    // do not affect geometric vertex equivalence.
    appendU32(key, 2U, geometry.skinIndices, sourceIndex * 4U, 4U);
    appendFloats(key, 3U, geometry.skinWeights, sourceIndex * 4U, 4U);
    appendFloats(key, 4U, geometry.edgeScale, sourceIndex, 1U);
    appendU8(key, 5U, geometry.sdefEnabled, sourceIndex, 1U);
    appendFloats(key, 6U, geometry.sdefC, sourceIndex * 3U, 3U);
    appendFloats(key, 7U, geometry.sdefR0, sourceIndex * 3U, 3U);
    appendFloats(key, 8U, geometry.sdefR1, sourceIndex * 3U, 3U);
    appendFloats(key, 9U, geometry.sdefRw0, sourceIndex * 3U, 3U);
    appendFloats(key, 10U, geometry.sdefRw1, sourceIndex * 3U, 3U);
    appendU8(key, 11U, geometry.qdefEnabled, sourceIndex, 1U);
    for (size_t uvIndex = 0; uvIndex < geometry.additionalUvs.size(); ++uvIndex) {
        appendFloats(key, static_cast<uint32_t>(100U + uvIndex),
                     geometry.additionalUvs[uvIndex], sourceIndex * 4U, 4U);
    }
    if (sourceIndex < geometry.morphSignatures.size()) {
        appendMarker(key, 200U, geometry.morphSignatures[sourceIndex].size());
        key.words.insert(key.words.end(), geometry.morphSignatures[sourceIndex].begin(),
                         geometry.morphSignatures[sourceIndex].end());
    }
    return key;
}

}  // namespace

bool loadMmdUvSeamWeldGeometry(const std::vector<uint8_t>& bytes,
                               MmdUvSeamWeldGeometry& raw,
                               MmdUvSeamWeldGeometryLoadDiagnostics* diagnostics)
{
    if (bytes.empty()) return false;
    mmd_runtime_pmx_geometry_t* geometry = mmd_runtime_pmx_geometry_create(bytes.data(), bytes.size());
    if (diagnostics) ++diagnostics->geometryParseCount;
    if (!geometry) return false;
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
    const size_t additionalUvCount = mmd_runtime_pmx_geometry_additional_uv_count(geometry);
    raw.additionalUvs.reserve(additionalUvCount);
    for (size_t i = 0; i < additionalUvCount; ++i)
        raw.additionalUvs.push_back(takeFloatBuffer(mmd_runtime_pmx_geometry_additional_uvs_buffer(geometry, i)));
    mmd_runtime_pmx_geometry_free(geometry);
    if (raw.positions.empty()) return false;
    if (diagnostics) ++diagnostics->nonGeometryParseCount;
    const json nonGeometry = takeJsonBuffer(mmd_runtime_parse_pmx_non_geometry_json(bytes.data(), bytes.size()));
    return buildMorphSignatures(nonGeometry, raw.positions.size() / 3U, raw.morphSignatures);
}

bool buildMmdUvSeamWeldPlan(const MmdUvSeamWeldGeometry& geometry,
                            const std::vector<uint32_t>& sourceIndices,
                            const std::vector<int>& sourceToOldLocal,
                            const std::vector<int>& faceCounts,
                            const std::vector<int>& polygonConnects,
                            MmdUvSeamWeldPlan& plan)
{
    const size_t oldVertexCount = sourceIndices.size();
    const size_t sourceCount = geometry.positions.size() / 3U;
    if (oldVertexCount > std::numeric_limits<uint32_t>::max() ||
        sourceToOldLocal.size() != sourceCount) {
        return false;
    }
    for (uint32_t source : sourceIndices) {
        if (source >= sourceCount) {
            return false;
        }
    }
    for (int oldLocal : sourceToOldLocal) {
        if (oldLocal < -1 || oldLocal >= static_cast<int>(oldVertexCount)) {
            return false;
        }
    }

    std::unordered_map<WeldKey, uint32_t, WeldKeyHash> candidateGroups;
    candidateGroups.reserve(oldVertexCount);
    std::vector<uint32_t> groupByVertex(oldVertexCount, 0U);
    uint32_t groupCount = 0;
    for (uint32_t vertex = 0; vertex < oldVertexCount; ++vertex) {
        const auto inserted = candidateGroups.emplace(makeWeldKey(geometry, sourceIndices[vertex]), groupCount);
        if (inserted.second) {
            ++groupCount;
        }
        groupByVertex[vertex] = inserted.first->second;
    }

    std::vector<bool> conflictingGroup(groupCount, false);
    size_t cursor = 0;
    for (int count : faceCounts) {
        std::unordered_map<uint32_t, uint32_t> firstVertex;
        if (count < 0 || cursor + static_cast<size_t>(count) > polygonConnects.size()) {
            return false;
        }
        for (int local = 0; local < count; ++local) {
            const int oldVertex = polygonConnects[cursor + static_cast<size_t>(local)];
            if (oldVertex < 0 || oldVertex >= static_cast<int>(oldVertexCount)) {
                return false;
            }
            const uint32_t group = groupByVertex[static_cast<size_t>(oldVertex)];
            const auto inserted = firstVertex.emplace(group, static_cast<uint32_t>(oldVertex));
            if (!inserted.second && inserted.first->second != static_cast<uint32_t>(oldVertex)) {
                conflictingGroup[group] = true;
            }
        }
        cursor += static_cast<size_t>(count);
    }

    plan = {};
    plan.localByVertex.resize(oldVertexCount, 0U);
    std::vector<uint32_t> representativeByGroup(
        groupCount, std::numeric_limits<uint32_t>::max());
    for (uint32_t vertex = 0; vertex < oldVertexCount; ++vertex) {
        const uint32_t group = groupByVertex[vertex];
        if (conflictingGroup[group]) {
            plan.localByVertex[vertex] = plan.vertexCount++;
            plan.localToSource.push_back(sourceIndices[vertex]);
            continue;
        }
        uint32_t& representative = representativeByGroup[group];
        if (representative == std::numeric_limits<uint32_t>::max()) {
            representative = vertex;
            plan.localByVertex[vertex] = plan.vertexCount++;
            plan.localToSource.push_back(sourceIndices[vertex]);
        } else {
            plan.localByVertex[vertex] = plan.localByVertex[representative];
        }
    }
    plan.sourceToLocal.assign(sourceCount, -1);
    for (size_t source = 0; source < sourceToOldLocal.size(); ++source) {
        const int oldLocal = sourceToOldLocal[source];
        if (oldLocal >= 0) {
            plan.sourceToLocal[source] = static_cast<int>(
                plan.localByVertex[static_cast<size_t>(oldLocal)]);
        }
    }
    plan.remappedPolygonConnects.resize(polygonConnects.size());
    for (size_t i = 0; i < polygonConnects.size(); ++i) {
        const int oldVertex = polygonConnects[i];
        if (oldVertex < 0 || oldVertex >= static_cast<int>(oldVertexCount)) {
            return false;
        }
        plan.remappedPolygonConnects[i] = static_cast<int>(
            plan.localByVertex[static_cast<size_t>(oldVertex)]);
    }
    return true;
}
