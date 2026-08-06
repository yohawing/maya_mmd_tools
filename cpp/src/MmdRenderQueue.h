/**
 * @file MmdRenderQueue.h
 * @brief MMD material/submesh render-pass ordering contract.
 *
 * This module owns the deterministic ordering contract used by native MMD
 * geometry loading.  The opt-in Maya VP2 shape consumes the resulting entries
 * to create one render item per material/submesh, while keeping this contract
 * independent of Maya so its ordering rules remain testable without a DCC
 * process.
 */

#pragma once

#include <array>
#include <cstddef>
#include <string>
#include <vector>

namespace mmd {

/** Native pass buckets, ordered from the depth-writing pass to transparency. */
enum class MmdDrawPass : unsigned char {
    Opaque = 0,
    Cutout = 1,
    Transparent = 2,
};

/** Input record for one material-split submesh. */
struct MmdRenderQueueInput {
    std::size_t materialIndex = 0;
    std::size_t submeshIndex = 0;
    std::string transparencyMode;
    float diffuseAlpha = 1.0f;
    // Material values used by the native VP2 MMD shader.  Texture paths are
    // resolved against the PMX directory before the shape receives them;
    // the geometry override owns the Maya texture handles.
    std::array<float, 3> diffuseColor = {1.0f, 1.0f, 1.0f};
    std::array<float, 3> specularColor = {0.0f, 0.0f, 0.0f};
    float specularPower = 0.0f;
    std::array<float, 3> ambientColor = {0.3f, 0.3f, 0.3f};
    std::array<float, 3> edgeColor = {0.0f, 0.0f, 0.0f};
    float edgeAlpha = 1.0f;
    float edgeSize = 0.0f;
    bool edgeDrawing = false;
    int sphereMode = 0;
    int sharedToonIndex = -1;
    std::string mainTexturePath;
    std::string sphereTexturePath;
    std::string toonTexturePath;
    bool doubleSided = false;
};

/** Ordered queue record consumed by a native material-split loader. */
struct MmdRenderQueueEntry {
    std::size_t materialIndex = 0;
    std::size_t submeshIndex = 0;
    MmdDrawPass pass = MmdDrawPass::Opaque;
    // Index into the input vector.  The key fields are not unique for every
    // loader, so the ordered entry must retain its exact source record.
    std::size_t inputIndex = 0;
};

/**
 * Classify a material using an explicit mode when available.
 *
 * Accepted explicit modes are opaque, cutout/alpha-test, and
 * transparent/blend.  An unknown or empty mode falls back to diffuse alpha:
 * alpha below 0.999 is transparent, otherwise opaque.
 */
MmdDrawPass classifyMmdDrawPass(const std::string& transparencyMode,
                                float diffuseAlpha);

/** Return the stable diagnostic name for a pass bucket. */
const char* mmdDrawPassName(MmdDrawPass pass);

/**
 * Build the native queue.
 *
 * Entries are grouped by pass (Opaque, Cutout, Transparent), then ordered by
 * PMX material index, then by split-submesh index.  Stable sorting preserves
 * input order for exact ties, so the result is deterministic even when a
 * loader supplies duplicate material/submesh records.
 */
std::vector<MmdRenderQueueEntry> buildMmdRenderQueue(
    const std::vector<MmdRenderQueueInput>& inputs);

/** Find the exact source material record represented by an ordered entry. */
const MmdRenderQueueInput* findMmdRenderQueueInput(
    const std::vector<MmdRenderQueueInput>& inputs,
    const MmdRenderQueueEntry& entry);

}  // namespace mmd
