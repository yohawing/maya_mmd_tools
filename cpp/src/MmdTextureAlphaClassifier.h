/**
 * @file MmdTextureAlphaClassifier.h
 * @brief Atlas-safe per-material texture alpha classification.
 */

#pragma once

#include <cstddef>
#include <cstdint>
#include <vector>

namespace mmd {

enum class MmdTextureAlphaMode : unsigned char {
    Opaque = 0,
    Cutout = 1,
    Blend = 2,
};

MmdTextureAlphaMode classifyMmdTextureAlpha(
    const std::vector<std::uint8_t>& alpha,
    std::size_t width,
    std::size_t height,
    const std::vector<float>& uvs,
    const std::vector<std::uint32_t>& indices,
    std::size_t resolution = 512U);

const char* mmdTextureAlphaModeName(MmdTextureAlphaMode mode);

}  // namespace mmd
