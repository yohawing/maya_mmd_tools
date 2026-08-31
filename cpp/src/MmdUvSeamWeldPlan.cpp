#include "MmdUvSeamWeldPlan.h"

#include <algorithm>
#include <cstring>
#include <limits>
#include <unordered_map>

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
