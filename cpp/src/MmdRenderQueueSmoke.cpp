/**
 * @file MmdRenderQueueSmoke.cpp
 * @brief Standalone smoke test for the native MMD render queue contract.
 *
 * This executable deliberately has no Maya or mmd-anim dependency.  It is a
 * small build/run witness for pass classification and deterministic ordering.
 */

#include "MmdRenderQueue.h"

#include <iostream>
#include <string>
#include <vector>

namespace {

bool expectEntry(const mmd::MmdRenderQueueEntry& entry,
                 mmd::MmdDrawPass pass,
                 std::size_t materialIndex,
                 std::size_t submeshIndex,
                 std::size_t inputIndex)
{
    return entry.pass == pass && entry.materialIndex == materialIndex &&
           entry.submeshIndex == submeshIndex &&
           entry.inputIndex == inputIndex;
}

}  // namespace

int main()
{
    const std::vector<mmd::MmdRenderQueueInput> inputs = {
        {5, 0, "blend", 1.0f},
        {2, 2, "cutout", 1.0f},
        {0, 1, "opaque", 1.0f},
        {1, 3, "", 0.4f},
        {2, 4, "opaque", 1.0f},
    };
    const std::vector<mmd::MmdRenderQueueEntry> queue =
        mmd::buildMmdRenderQueue(inputs);

    mmd::MmdRenderQueueInput materialInput;
    materialInput.materialIndex = 7;
    materialInput.submeshIndex = 0;
    materialInput.transparencyMode = "blend";
    materialInput.diffuseAlpha = 0.35f;
    materialInput.diffuseColor = {0.8f, 0.2f, 0.1f};
    materialInput.selfShadowMap = true;
    mmd::MmdRenderQueueInput secondMaterialInput = materialInput;
    secondMaterialInput.diffuseAlpha = 0.65f;
    secondMaterialInput.diffuseColor = {0.1f, 0.3f, 0.9f};
    secondMaterialInput.selfShadowMap = false;
    const std::vector<mmd::MmdRenderQueueInput> materialInputs = {
        materialInput, secondMaterialInput};
    const std::vector<mmd::MmdRenderQueueEntry> materialQueue =
        mmd::buildMmdRenderQueue(materialInputs);
    const mmd::MmdRenderQueueInput* firstMaterial =
        materialQueue.empty()
            ? nullptr
            : mmd::findMmdRenderQueueInput(materialInputs, materialQueue[0]);
    const mmd::MmdRenderQueueInput* secondMaterial =
        materialQueue.size() < 2
            ? nullptr
            : mmd::findMmdRenderQueueInput(materialInputs, materialQueue[1]);
    const bool materialContract =
        firstMaterial && secondMaterial && materialQueue.size() == 2 &&
        materialQueue[0].inputIndex == 0 && materialQueue[1].inputIndex == 1 &&
        firstMaterial->diffuseColor == std::array<float, 3>{0.8f, 0.2f, 0.1f} &&
        firstMaterial->diffuseAlpha == 0.35f &&
        firstMaterial->selfShadowMap &&
        secondMaterial->diffuseColor ==
            std::array<float, 3>{0.1f, 0.3f, 0.9f} &&
        secondMaterial->diffuseAlpha == 0.65f &&
        !secondMaterial->selfShadowMap &&
        materialQueue[0].pass == mmd::MmdDrawPass::Transparent &&
        materialQueue[1].pass == mmd::MmdDrawPass::Transparent;

    const bool correct = queue.size() == 5 &&
                         expectEntry(queue[0], mmd::MmdDrawPass::Opaque, 0, 1, 2) &&
                         expectEntry(queue[1], mmd::MmdDrawPass::Opaque, 2, 4, 4) &&
                         expectEntry(queue[2], mmd::MmdDrawPass::Cutout, 2, 2, 1) &&
                         expectEntry(queue[3], mmd::MmdDrawPass::Transparent, 1, 3, 3) &&
                         expectEntry(queue[4], mmd::MmdDrawPass::Transparent, 5, 0, 0);
    if (!correct || !materialContract) {
        std::cerr << "mmd render queue/material contract failed\n";
        return 1;
    }

    for (const mmd::MmdRenderQueueEntry& entry : queue) {
        std::cout << mmd::mmdDrawPassName(entry.pass) << " material="
                  << entry.materialIndex << " submesh=" << entry.submeshIndex
                  << '\n';
    }
    return 0;
}
