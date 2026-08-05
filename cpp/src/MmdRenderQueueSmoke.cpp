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
                 std::size_t submeshIndex)
{
    return entry.pass == pass && entry.materialIndex == materialIndex &&
           entry.submeshIndex == submeshIndex;
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

    const bool correct = queue.size() == 5 &&
                         expectEntry(queue[0], mmd::MmdDrawPass::Opaque, 0, 1) &&
                         expectEntry(queue[1], mmd::MmdDrawPass::Opaque, 2, 4) &&
                         expectEntry(queue[2], mmd::MmdDrawPass::Cutout, 2, 2) &&
                         expectEntry(queue[3], mmd::MmdDrawPass::Transparent, 1, 3) &&
                         expectEntry(queue[4], mmd::MmdDrawPass::Transparent, 5, 0);
    if (!correct) {
        std::cerr << "mmd render queue contract failed\n";
        return 1;
    }

    for (const mmd::MmdRenderQueueEntry& entry : queue) {
        std::cout << mmd::mmdDrawPassName(entry.pass) << " material="
                  << entry.materialIndex << " submesh=" << entry.submeshIndex
                  << '\n';
    }
    return 0;
}
