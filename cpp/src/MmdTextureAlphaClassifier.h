/**
 * @file MmdTextureAlphaClassifier.h
 * @brief Atlas-safe per-material texture alpha classification.
 */

#pragma once

#include <cstddef>
#include <cstdint>
#include <vector>

namespace mmd {

// Cross-runtime classifier contract.  Keep these values in sync with
// mmd_tools.converters.texture_alpha: opaque=255, partial ratio=0.25,
// blend threshold=100, whole-texture fallback stride=4, and bounded UV/tile
// rasterization.  This is a behavioral specification, not an ABI surface.
inline constexpr std::uint8_t kMmdTextureAlphaOpaqueThreshold = 255U;
inline constexpr double kMmdTextureAlphaPartialRatioThreshold = 0.25;
inline constexpr double kMmdTextureAlphaBlendThreshold = 100.0;
inline constexpr std::size_t kMmdTextureAlphaDefaultResolution = 512U;
inline constexpr std::size_t kMmdTextureAlphaMaxTileSpan = 64U;
inline constexpr double kMmdTextureAlphaMaxUvMagnitude = 1000000.0;
inline constexpr std::size_t kMmdTextureAlphaWholeScanStride = 4U;
inline constexpr std::size_t kMmdTextureAlphaMaxRasterSamples =
    kMmdTextureAlphaDefaultResolution * kMmdTextureAlphaDefaultResolution;

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
    std::size_t resolution = kMmdTextureAlphaDefaultResolution);

const char* mmdTextureAlphaModeName(MmdTextureAlphaMode mode);

}  // namespace mmd
