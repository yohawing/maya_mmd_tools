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
    for (const MmdRenderQueueInput& input : inputs) {
        queue.push_back({input.materialIndex,
                         input.submeshIndex,
                         classifyMmdDrawPass(input.transparencyMode,
                                             input.diffuseAlpha)});
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

}  // namespace mmd
