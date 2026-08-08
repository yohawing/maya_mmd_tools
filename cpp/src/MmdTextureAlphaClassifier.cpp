/**
 * @file MmdTextureAlphaClassifier.cpp
 * @brief Atlas-safe per-material texture alpha classification.
 */

#include "MmdTextureAlphaClassifier.h"

#include <algorithm>
#include <cmath>
#include <limits>

namespace {

struct AlphaStats {
    std::uint8_t minAlpha = mmd::kMmdTextureAlphaOpaqueThreshold;
    std::uint8_t maxAlpha = 0U;
    std::uint64_t middleTotal = 0U;
    std::size_t middleCount = 0U;
    std::size_t sampleCount = 0U;
};

void recordAlpha(AlphaStats& stats, std::uint8_t alpha)
{
    if (stats.sampleCount >= mmd::kMmdTextureAlphaMaxRasterSamples) {
        return;
    }
    stats.minAlpha = std::min(stats.minAlpha, alpha);
    stats.maxAlpha = std::max(stats.maxAlpha, alpha);
    ++stats.sampleCount;
    if (alpha > 0U &&
        alpha < mmd::kMmdTextureAlphaOpaqueThreshold) {
        stats.middleTotal += alpha;
        ++stats.middleCount;
    }
}

std::uint8_t sampleAlpha(const std::vector<std::uint8_t>& alpha,
                         std::size_t width,
                         std::size_t height,
                         double u,
                         double v)
{
    const long long rawX = std::llround(u * static_cast<double>(width));
    const long long rawY = std::llround(v * static_cast<double>(height));
    const long long signedWidth = static_cast<long long>(width);
    const long long signedHeight = static_cast<long long>(height);
    const std::size_t x = static_cast<std::size_t>(
        ((rawX % signedWidth) + signedWidth) % signedWidth);
    const std::size_t y = static_cast<std::size_t>(
        ((rawY % signedHeight) + signedHeight) % signedHeight);
    return alpha[y * width + x];
}

void recordTriangleTile(AlphaStats& stats,
                        const std::vector<std::uint8_t>& alpha,
                        std::size_t width,
                        std::size_t height,
                        double ax,
                        double ay,
                        double bx,
                        double by,
                        double cx,
                        double cy,
                        std::size_t resolution)
{
    if (stats.sampleCount >= mmd::kMmdTextureAlphaMaxRasterSamples) {
        return;
    }
    const double scale = static_cast<double>(resolution);
    const double rax = ax * scale;
    const double ray = ay * scale;
    const double rbx = bx * scale;
    const double rby = by * scale;
    const double rcx = cx * scale;
    const double rcy = cy * scale;
    const int minX = std::max(0, static_cast<int>(std::floor(
                                     std::min({rax, rbx, rcx}))));
    const int maxX = std::min(
        static_cast<int>(resolution) - 1,
        static_cast<int>(std::ceil(std::max({rax, rbx, rcx}))));
    const int minY = std::max(0, static_cast<int>(std::floor(
                                     std::min({ray, rby, rcy}))));
    const int maxY = std::min(
        static_cast<int>(resolution) - 1,
        static_cast<int>(std::ceil(std::max({ray, rby, rcy}))));
    const double denominator =
        (rby - rcy) * (rax - rcx) + (rcx - rbx) * (ray - rcy);
    if (minX > maxX || minY > maxY || std::abs(denominator) < 1.0e-9) {
        return;
    }

    const double inverse = 1.0 / denominator;
    for (int y = minY; y <= maxY; ++y) {
        const double py = static_cast<double>(y) + 0.5;
        for (int x = minX; x <= maxX; ++x) {
            if (stats.sampleCount >= mmd::kMmdTextureAlphaMaxRasterSamples) {
                return;
            }
            const double px = static_cast<double>(x) + 0.5;
            const double wa =
                ((rby - rcy) * (px - rcx) +
                 (rcx - rbx) * (py - rcy)) * inverse;
            const double wb =
                ((rcy - ray) * (px - rcx) +
                 (rax - rcx) * (py - rcy)) * inverse;
            const double wc = 1.0 - wa - wb;
            if (wa >= 0.0 && wb >= 0.0 && wc >= 0.0) {
                recordAlpha(stats,
                            sampleAlpha(alpha, width, height,
                                        px / static_cast<double>(resolution),
                                        py / static_cast<double>(resolution)));
            }
        }
    }
}

void recordTriangle(AlphaStats& stats,
                    const std::vector<std::uint8_t>& alpha,
                    std::size_t width,
                    std::size_t height,
                    double ax,
                    double ay,
                    double bx,
                    double by,
                    double cx,
                    double cy,
                    std::size_t resolution)
{
    if (stats.sampleCount >= mmd::kMmdTextureAlphaMaxRasterSamples) {
        return;
    }
    const std::size_t before = stats.sampleCount;
    const double minU = std::min({ax, bx, cx});
    const double maxU = std::max({ax, bx, cx});
    const double minV = std::min({ay, by, cy});
    const double maxV = std::max({ay, by, cy});
    const int firstU = static_cast<int>(std::ceil(-maxU));
    const int lastU = static_cast<int>(std::floor(1.0 - minU));
    const int firstV = static_cast<int>(std::ceil(-maxV));
    const int lastV = static_cast<int>(std::floor(1.0 - minV));
    if (lastU - firstU <=
            static_cast<int>(mmd::kMmdTextureAlphaMaxTileSpan) &&
        lastV - firstV <=
            static_cast<int>(mmd::kMmdTextureAlphaMaxTileSpan)) {
        for (int shiftU = firstU; shiftU <= lastU; ++shiftU) {
            for (int shiftV = firstV; shiftV <= lastV; ++shiftV) {
                recordTriangleTile(stats, alpha, width, height,
                                   ax + shiftU, ay + shiftV,
                                   bx + shiftU, by + shiftV,
                                   cx + shiftU, cy + shiftV, resolution);
            }
        }
    }
    if (stats.sampleCount == before &&
        stats.sampleCount < mmd::kMmdTextureAlphaMaxRasterSamples) {
        recordAlpha(stats, sampleAlpha(alpha, width, height, ax, ay));
        recordAlpha(stats, sampleAlpha(alpha, width, height, bx, by));
        recordAlpha(stats, sampleAlpha(alpha, width, height, cx, cy));
    }
}

mmd::MmdTextureAlphaMode evaluate(const AlphaStats& stats)
{
    if (stats.sampleCount == 0U ||
        stats.minAlpha >= mmd::kMmdTextureAlphaOpaqueThreshold) {
        return mmd::MmdTextureAlphaMode::Opaque;
    }
    const double partialRatio =
        static_cast<double>(stats.middleCount) /
        static_cast<double>(stats.sampleCount);
    if (partialRatio >= mmd::kMmdTextureAlphaPartialRatioThreshold) {
        return mmd::MmdTextureAlphaMode::Blend;
    }
    const double averageMiddle =
        stats.middleCount == 0U
            ? 0.0
            : static_cast<double>(stats.middleTotal) /
                  static_cast<double>(stats.middleCount);
    return averageMiddle + mmd::kMmdTextureAlphaBlendThreshold < stats.maxAlpha
               ? mmd::MmdTextureAlphaMode::Cutout
               : mmd::MmdTextureAlphaMode::Blend;
}

}  // namespace

namespace mmd {

MmdTextureAlphaMode classifyMmdTextureAlpha(
    const std::vector<std::uint8_t>& alpha,
    std::size_t width,
    std::size_t height,
    const std::vector<float>& uvs,
    const std::vector<std::uint32_t>& indices,
    std::size_t resolution)
{
    if (width == 0U || height == 0U ||
        width > std::numeric_limits<std::size_t>::max() / height ||
        width > static_cast<std::size_t>(std::numeric_limits<int>::max()) ||
        height > static_cast<std::size_t>(std::numeric_limits<int>::max()) ||
        alpha.size() != width * height ||
        uvs.size() % 2U != 0U || indices.size() % 3U != 0U ||
        resolution == 0U) {
        return MmdTextureAlphaMode::Opaque;
    }
    if (std::all_of(alpha.begin(), alpha.end(),
                    [](std::uint8_t value) {
                        return value >= mmd::kMmdTextureAlphaOpaqueThreshold;
                    })) {
        return MmdTextureAlphaMode::Opaque;
    }

    AlphaStats stats;
    const std::size_t scanResolution =
        std::min({resolution, std::max(width, height),
                  mmd::kMmdTextureAlphaDefaultResolution});
    for (std::size_t offset = 0U; offset < indices.size(); offset += 3U) {
        const std::uint32_t ia = indices[offset];
        const std::uint32_t ib = indices[offset + 1U];
        const std::uint32_t ic = indices[offset + 2U];
        if (static_cast<std::size_t>(ia) * 2U + 1U >= uvs.size() ||
            static_cast<std::size_t>(ib) * 2U + 1U >= uvs.size() ||
            static_cast<std::size_t>(ic) * 2U + 1U >= uvs.size()) {
            continue;
        }
        const double ax = uvs[static_cast<std::size_t>(ia) * 2U];
        const double ay = 1.0 - uvs[static_cast<std::size_t>(ia) * 2U + 1U];
        const double bx = uvs[static_cast<std::size_t>(ib) * 2U];
        const double by = 1.0 - uvs[static_cast<std::size_t>(ib) * 2U + 1U];
        const double cx = uvs[static_cast<std::size_t>(ic) * 2U];
        const double cy = 1.0 - uvs[static_cast<std::size_t>(ic) * 2U + 1U];
        if (!std::isfinite(ax) || !std::isfinite(ay) ||
            !std::isfinite(bx) || !std::isfinite(by) ||
            !std::isfinite(cx) || !std::isfinite(cy)) {
            continue;
        }
        if (std::abs(ax) > mmd::kMmdTextureAlphaMaxUvMagnitude ||
            std::abs(ay) > mmd::kMmdTextureAlphaMaxUvMagnitude ||
            std::abs(bx) > mmd::kMmdTextureAlphaMaxUvMagnitude ||
            std::abs(by) > mmd::kMmdTextureAlphaMaxUvMagnitude ||
            std::abs(cx) > mmd::kMmdTextureAlphaMaxUvMagnitude ||
            std::abs(cy) > mmd::kMmdTextureAlphaMaxUvMagnitude) {
            continue;
        }
        recordTriangle(stats, alpha, width, height,
                       ax, ay, bx, by, cx, cy, scanResolution);
        if (stats.sampleCount >= mmd::kMmdTextureAlphaMaxRasterSamples) {
            break;
        }
    }
    if (stats.sampleCount == 0U) {
        for (std::size_t index = 0U;
             index < alpha.size() &&
             stats.sampleCount < mmd::kMmdTextureAlphaMaxRasterSamples;
             index += mmd::kMmdTextureAlphaWholeScanStride) {
            recordAlpha(stats, alpha[index]);
        }
    }
    return evaluate(stats);
}

const char* mmdTextureAlphaModeName(MmdTextureAlphaMode mode)
{
    switch (mode) {
    case MmdTextureAlphaMode::Cutout:
        return "cutout";
    case MmdTextureAlphaMode::Blend:
        return "blend";
    case MmdTextureAlphaMode::Opaque:
    default:
        return "opaque";
    }
}

}  // namespace mmd
