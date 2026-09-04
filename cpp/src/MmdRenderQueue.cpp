/**
 * @file MmdRenderQueue.cpp
 * @brief Implementation of the native MMD material ordering contract.
 */

#include "MmdRenderQueue.h"

#include <algorithm>
#include <cctype>
#include <cmath>

namespace mmd {
namespace {

std::string normalizedMode(const std::string& mode)
{
    std::string normalized;
    normalized.reserve(mode.size());
    for (unsigned char character : mode) {
        if (std::isspace(character) != 0) {
            continue;
        }
        normalized.push_back(static_cast<char>(std::tolower(character)));
    }
    return normalized;
}

int passRank(MmdDrawPass pass)
{
    return static_cast<int>(pass);
}

}  // namespace

MmdDrawPass classifyMmdDrawPass(const std::string& transparencyMode,
                                float diffuseAlpha)
{
    const std::string mode = normalizedMode(transparencyMode);
    if (mode == "cutout" || mode == "alphatest" || mode == "alpha-test" ||
        mode == "alpha_test") {
        return MmdDrawPass::Cutout;
    }
    if (mode == "transparent" || mode == "translucent" || mode == "blend") {
        return MmdDrawPass::Transparent;
    }
    if (mode == "opaque") {
        return MmdDrawPass::Opaque;
    }

    if (std::isfinite(diffuseAlpha) && diffuseAlpha < 0.999f) {
        return MmdDrawPass::Transparent;
    }
    return MmdDrawPass::Opaque;
}

MmdDrawPass classifyMmdDrawPass(const MmdRenderQueueInput& input)
{
    const std::string mode = normalizedMode(input.transparencyMode);
    if (mode == "cutout" || mode == "alphatest" || mode == "alpha-test" ||
        mode == "alpha_test" || mode == "transparent" ||
        mode == "translucent" || mode == "blend") {
        return classifyMmdDrawPass(mode, input.diffuseAlpha);
    }

    float effectiveAlpha = input.diffuseAlpha;
    if (!input.mainTexturePath.empty() && input.mainTextureAvailable &&
        std::isfinite(input.mainTextureMultiply[3]) &&
        std::isfinite(input.mainTextureAdd[3])) {
        // The native shader applies this factor to sampled texture alpha.  A
        // texture classified opaque at import has alpha 1, so the queue must
        // use the same effective alpha for a material-morph update.  The
        // factor is meaningful only after the Maya texture handle exists.
        effectiveAlpha *= input.mainTextureMultiply[3] +
                          input.mainTextureAdd[3];
    }
    // "opaque" is the import-time default for a texture known to be opaque.
    // Once a morph changes its alpha factor, classify that effective value
    // instead of treating the import-time label as an explicit override.
    if (mode == "opaque" && !input.mainTexturePath.empty() &&
        input.mainTextureAvailable) {
        return classifyMmdDrawPass("", effectiveAlpha);
    }
    return classifyMmdDrawPass(mode, effectiveAlpha);
}

const char* mmdDrawPassName(MmdDrawPass pass)
{
    switch (pass) {
    case MmdDrawPass::Opaque:
        return "Opaque";
    case MmdDrawPass::Cutout:
        return "Cutout";
    case MmdDrawPass::Transparent:
        return "Transparent";
    }
    return "Unknown";
}

std::vector<MmdRenderQueueEntry> buildMmdRenderQueue(
    const std::vector<MmdRenderQueueInput>& inputs)
{
    std::vector<MmdRenderQueueEntry> queue;
    queue.reserve(inputs.size());
    for (std::size_t inputIndex = 0; inputIndex < inputs.size();
         ++inputIndex) {
        const MmdRenderQueueInput& input = inputs[inputIndex];
        queue.push_back({input.materialIndex,
                         input.submeshIndex,
                         classifyMmdDrawPass(input),
                         inputIndex});
    }

    std::stable_sort(
        queue.begin(), queue.end(), [](const MmdRenderQueueEntry& left,
                                      const MmdRenderQueueEntry& right) {
            const int leftPass = passRank(left.pass);
            const int rightPass = passRank(right.pass);
            if (leftPass != rightPass) {
                return leftPass < rightPass;
            }
            if (left.materialIndex != right.materialIndex) {
                return left.materialIndex < right.materialIndex;
            }
            return left.submeshIndex < right.submeshIndex;
        });
    return queue;
}

const MmdRenderQueueInput* findMmdRenderQueueInput(
    const std::vector<MmdRenderQueueInput>& inputs,
    const MmdRenderQueueEntry& entry)
{
    if (entry.inputIndex >= inputs.size()) {
        return nullptr;
    }
    const MmdRenderQueueInput& input = inputs[entry.inputIndex];
    if (input.materialIndex != entry.materialIndex ||
        input.submeshIndex != entry.submeshIndex) {
        return nullptr;
    }
    return &input;
}

}  // namespace mmd
