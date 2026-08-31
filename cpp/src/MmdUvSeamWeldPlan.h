/**
 * Conservative, Maya-independent UV-seam weld planning.
 *
 * This keeps the topology decision reusable by import paths while leaving
 * mesh construction and attribute persistence to their Maya-facing callers.
 */

#pragma once

#include <cstddef>
#include <cstdint>
#include <vector>

struct MmdUvSeamWeldGeometry {
    std::vector<float> positions;
    std::vector<uint32_t> skinIndices;
    std::vector<float> skinWeights;
    std::vector<float> edgeScale;
    std::vector<uint8_t> sdefEnabled;
    std::vector<float> sdefC;
    std::vector<float> sdefR0;
    std::vector<float> sdefR1;
    std::vector<float> sdefRw0;
    std::vector<float> sdefRw1;
    std::vector<uint8_t> qdefEnabled;
    std::vector<std::vector<float>> additionalUvs;
    std::vector<std::vector<uint32_t>> morphSignatures;
};

// Optional parse counters for callers that expose loading diagnostics.
// A successful load performs one geometry and one non-geometry PMX parse.
struct MmdUvSeamWeldGeometryLoadDiagnostics {
    unsigned int geometryParseCount = 0;
    unsigned int nonGeometryParseCount = 0;
};

// Loads every Maya-independent payload that contributes to weld equivalence.
// The PMX bytes are parsed once for geometry and once for sparse morph data.
bool loadMmdUvSeamWeldGeometry(
    const std::vector<uint8_t>& bytes,
    MmdUvSeamWeldGeometry& geometry,
    MmdUvSeamWeldGeometryLoadDiagnostics* diagnostics = nullptr);

struct MmdUvSeamWeldPlan {
    std::vector<int> sourceToLocal;
    std::vector<uint32_t> localToSource;
    std::vector<uint32_t> localByVertex;
    std::vector<int> remappedPolygonConnects;
    uint32_t vertexCount = 0;
};

// Builds a conservative plan. It welds only exactly equivalent deformation
// payloads and retains all same-face duplicate candidates as separate locals.
bool buildMmdUvSeamWeldPlan(const MmdUvSeamWeldGeometry& geometry,
                            const std::vector<uint32_t>& sourceIndices,
                            const std::vector<int>& sourceToOldLocal,
                            const std::vector<int>& faceCounts,
                            const std::vector<int>& polygonConnects,
                            MmdUvSeamWeldPlan& plan);
