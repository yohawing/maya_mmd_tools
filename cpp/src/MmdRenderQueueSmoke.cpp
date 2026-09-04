/**
 * @file MmdRenderQueueSmoke.cpp
 * @brief Standalone smoke test for the native MMD render queue contract.
 *
 * This executable deliberately has no Maya or mmd-anim dependency.  It is a
 * small build/run witness for pass classification and deterministic ordering.
 */

#include "MmdRenderQueue.h"
#include "MmdTextureAlphaClassifier.h"

#include <iostream>
#include <limits>
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
    const std::vector<std::uint32_t> triangle = {0U, 1U, 2U};
    const std::vector<float> leftUvs = {
        0.0F, 0.0F, 0.49F, 0.0F, 0.0F, 1.0F};
    const std::vector<float> rightUvs = {
        0.63F, 0.0F, 0.87F, 0.0F, 0.87F, 1.0F};
    const std::vector<float> fullUvs = {
        0.0F, 0.0F, 0.99F, 0.0F, 0.0F, 0.99F};
    const std::vector<std::uint8_t> atlasAlpha = {
        255U, 255U, 255U, 64U,
        255U, 255U, 255U, 64U,
        255U, 255U, 255U, 64U,
        255U, 255U, 255U, 64U,
    };
    const std::vector<std::uint8_t> binaryAlpha = {
        0U, 0U, 0U, 0U,
        255U, 255U, 255U, 255U,
        0U, 0U, 0U, 0U,
        255U, 255U, 255U, 255U,
    };
    // These named vectors mirror tests/unit/test_texture_alpha.py.  Keep the
    // list as a behavioral conformance witness rather than coupling the
    // Python implementation to a native ABI.
    const std::vector<std::uint8_t> opaqueAlpha(16U, 255U);
    const std::vector<std::uint8_t> blendAlpha(16U, 128U);
    const std::vector<std::uint8_t> degenerateAlpha = {
        64U, 255U, 255U, 255U,
        255U, 255U, 255U, 255U,
        255U, 255U, 255U, 255U,
        255U, 255U, 255U, 255U,
    };
    const std::vector<std::uint8_t> wrappedAlpha = {
        255U, 64U, 255U, 255U,
        255U, 255U, 255U, 255U,
        255U, 255U, 255U, 255U,
        255U, 255U, 255U, 255U,
    };
    const std::vector<std::uint8_t> extremeFallbackAlpha = {
        255U, 64U, 255U, 255U,
        255U, 255U, 255U, 255U,
        255U, 255U, 255U, 255U,
        255U, 255U, 255U, 255U,
    };
    const std::vector<float> degenerateUvs = {
        0.0F, 0.0F, 0.0F, 0.0F, 0.0F, 0.0F};
    const std::vector<float> wrappedUvs = {
        1.0F, 0.0F, 1.25F, 0.0F, 1.0F, 1.0F};
    const std::vector<float> extremeUvs = {
        std::numeric_limits<float>::infinity(), 0.0F,
        0.0F, 0.0F, 0.0F, 0.0F,
        1000001.0F, 0.0F, 1000001.0F, 0.0F, 1000001.0F, 1.0F,
        0.0F, 0.0F, 65.0F, 0.0F, 0.0F, 1.0F};
    const std::vector<std::uint32_t> extremeIndices = {
        0U, 1U, 2U, 3U, 4U, 5U, 6U, 7U, 8U};
    const auto leftAlpha = mmd::classifyMmdTextureAlpha(
        atlasAlpha, 4U, 4U, leftUvs, triangle, 4U);
    const auto rightAlpha = mmd::classifyMmdTextureAlpha(
        atlasAlpha, 4U, 4U, rightUvs, triangle, 4U);
    const auto binaryMode = mmd::classifyMmdTextureAlpha(
        binaryAlpha, 4U, 4U, fullUvs, triangle, 4U);
    const auto opaqueMode = mmd::classifyMmdTextureAlpha(
        opaqueAlpha, 4U, 4U, fullUvs, triangle, 4U);
    const auto blendMode = mmd::classifyMmdTextureAlpha(
        blendAlpha, 4U, 4U, fullUvs, triangle, 4U);
    const auto degenerateMode = mmd::classifyMmdTextureAlpha(
        degenerateAlpha, 4U, 4U, degenerateUvs, triangle, 4U);
    const auto wrappedMode = mmd::classifyMmdTextureAlpha(
        wrappedAlpha, 4U, 4U, wrappedUvs, triangle, 4U);
    const auto extremeMode = mmd::classifyMmdTextureAlpha(
        extremeFallbackAlpha, 4U, 4U, extremeUvs, extremeIndices, 4U);
    const bool alphaContract =
        leftAlpha == mmd::MmdTextureAlphaMode::Opaque &&
        rightAlpha == mmd::MmdTextureAlphaMode::Blend &&
        binaryMode == mmd::MmdTextureAlphaMode::Cutout &&
        opaqueMode == mmd::MmdTextureAlphaMode::Opaque &&
        blendMode == mmd::MmdTextureAlphaMode::Blend &&
        degenerateMode == mmd::MmdTextureAlphaMode::Blend &&
        wrappedMode == mmd::MmdTextureAlphaMode::Blend &&
        extremeMode == mmd::MmdTextureAlphaMode::Opaque;

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
    materialInput.selfShadow = false;
    mmd::MmdRenderQueueInput secondMaterialInput = materialInput;
    secondMaterialInput.diffuseAlpha = 0.65f;
    secondMaterialInput.diffuseColor = {0.1f, 0.3f, 0.9f};
    secondMaterialInput.selfShadowMap = false;
    secondMaterialInput.selfShadow = true;
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
        !firstMaterial->selfShadow &&
        secondMaterial->diffuseColor ==
            std::array<float, 3>{0.1f, 0.3f, 0.9f} &&
        secondMaterial->diffuseAlpha == 0.65f &&
        !secondMaterial->selfShadowMap &&
        secondMaterial->selfShadow &&
        materialQueue[0].pass == mmd::MmdDrawPass::Transparent &&
        materialQueue[1].pass == mmd::MmdDrawPass::Transparent;

    mmd::MmdRenderQueueInput textureFactorInput;
    textureFactorInput.materialIndex = 11;
    textureFactorInput.submeshIndex = 0;
    textureFactorInput.transparencyMode = "opaque";
    textureFactorInput.mainTexturePath = "opaque.png";
    textureFactorInput.mainTextureAvailable = true;
    const auto opaqueTextureQueue =
        mmd::buildMmdRenderQueue({textureFactorInput});
    textureFactorInput.mainTextureMultiply[3] = 0.85F;
    const auto blendedTextureQueue =
        mmd::buildMmdRenderQueue({textureFactorInput});
    textureFactorInput.mainTextureMultiply[3] = 1.0F;
    const auto restoredTextureQueue =
        mmd::buildMmdRenderQueue({textureFactorInput});

    mmd::MmdRenderQueueInput noTextureInput = textureFactorInput;
    noTextureInput.mainTexturePath.clear();
    noTextureInput.mainTextureAvailable = false;
    noTextureInput.mainTextureMultiply[3] = 0.1F;
    noTextureInput.mainTextureAdd[3] = 0.4F;
    const auto noTextureQueue = mmd::buildMmdRenderQueue({noTextureInput});

    mmd::MmdRenderQueueInput missingTextureInput = textureFactorInput;
    missingTextureInput.mainTexturePath = "missing.png";
    missingTextureInput.mainTextureAvailable = false;
    missingTextureInput.mainTextureMultiply[3] = 0.1F;
    const auto missingTextureQueue =
        mmd::buildMmdRenderQueue({missingTextureInput});

    mmd::MmdRenderQueueInput cutoutInput = textureFactorInput;
    cutoutInput.transparencyMode = "cutout";
    cutoutInput.mainTextureMultiply[3] = 0.1F;
    const auto cutoutQueue = mmd::buildMmdRenderQueue({cutoutInput});
    mmd::MmdRenderQueueInput blendInput = textureFactorInput;
    blendInput.transparencyMode = "blend";
    blendInput.mainTextureMultiply[3] = 1.0F;
    const auto blendQueue = mmd::buildMmdRenderQueue({blendInput});
    const bool textureFactorContract =
        opaqueTextureQueue.size() == 1 &&
        opaqueTextureQueue[0].pass == mmd::MmdDrawPass::Opaque &&
        blendedTextureQueue.size() == 1 &&
        blendedTextureQueue[0].pass == mmd::MmdDrawPass::Transparent &&
        restoredTextureQueue.size() == 1 &&
        restoredTextureQueue[0].pass == mmd::MmdDrawPass::Opaque &&
        noTextureQueue.size() == 1 &&
        noTextureQueue[0].pass == mmd::MmdDrawPass::Opaque &&
        missingTextureQueue.size() == 1 &&
        missingTextureQueue[0].pass == mmd::MmdDrawPass::Opaque &&
        cutoutQueue.size() == 1 &&
        cutoutQueue[0].pass == mmd::MmdDrawPass::Cutout &&
        blendQueue.size() == 1 &&
        blendQueue[0].pass == mmd::MmdDrawPass::Transparent;

    const bool correct = queue.size() == 5 &&
                         expectEntry(queue[0], mmd::MmdDrawPass::Opaque, 0, 1, 2) &&
                         expectEntry(queue[1], mmd::MmdDrawPass::Opaque, 2, 4, 4) &&
                         expectEntry(queue[2], mmd::MmdDrawPass::Cutout, 2, 2, 1) &&
                         expectEntry(queue[3], mmd::MmdDrawPass::Transparent, 1, 3, 3) &&
                         expectEntry(queue[4], mmd::MmdDrawPass::Transparent, 5, 0, 0);
    if (!correct || !materialContract || !alphaContract ||
        !textureFactorContract) {
        std::cerr << "mmd render queue/material contract failed"
                  << " (alpha left=" << mmd::mmdTextureAlphaModeName(leftAlpha)
                  << ", right=" << mmd::mmdTextureAlphaModeName(rightAlpha)
                  << ", binary=" << mmd::mmdTextureAlphaModeName(binaryMode)
                  << ", opaque=" << mmd::mmdTextureAlphaModeName(opaqueMode)
                  << ", blend=" << mmd::mmdTextureAlphaModeName(blendMode)
                  << ", degenerate="
                  << mmd::mmdTextureAlphaModeName(degenerateMode)
                  << ", wrap=" << mmd::mmdTextureAlphaModeName(wrappedMode)
                  << ", extreme=" << mmd::mmdTextureAlphaModeName(extremeMode)
                  << ")\n";
        return 1;
    }

    for (const mmd::MmdRenderQueueEntry& entry : queue) {
        std::cout << mmd::mmdDrawPassName(entry.pass) << " material="
                  << entry.materialIndex << " submesh=" << entry.submeshIndex
                  << '\n';
    }
    return 0;
}
