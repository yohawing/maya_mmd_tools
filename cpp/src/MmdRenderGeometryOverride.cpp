/**
 * @file MmdRenderGeometryOverride.cpp
 * @brief VP2 render-item and geometry ownership for the opt-in witness shape.
 */

#include "MmdRenderGeometryOverride.h"

#include "MmdRenderShape.h"

#include <maya/MHWGeometry.h>
#include <maya/MGlobal.h>
#include <maya/MShaderManager.h>
#include <maya/MViewport2Renderer.h>

#include <algorithm>
#include <array>
#include <cstddef>
#include <cstring>
#include <limits>
#include <sstream>
#include <vector>

namespace {

using namespace MHWRender;

constexpr std::array<const char*, 3> kRenderItemNames = {
    "mmdRenderOpaque",
    "mmdRenderCutout",
    "mmdRenderTransparent",
};

MString renderItemName(mmd::MmdDrawPass pass)
{
    return MString(kRenderItemNames[static_cast<std::size_t>(pass)]);
}

MRenderItem* findOrCreateItem(MRenderItemList& list,
                              const MString& name,
                              MGeometry::DrawMode drawMode)
{
    int index = list.indexOf(name);
    MRenderItem* item = nullptr;
    if (index < 0) {
        item = MRenderItem::Create(name, MRenderItem::MaterialSceneItem,
                                   MGeometry::kTriangles);
        if (!item || !list.append(item)) {
            if (item) {
                MRenderItem::Destroy(item);
            }
            return nullptr;
        }
    } else {
        item = list.itemAt(index);
    }
    if (item) {
        item->setDrawMode(drawMode);
        item->enable(true);
    }
    return item;
}

void disableItems(const MRenderItemList& list)
{
    for (int i = 0; i < list.length(); ++i) {
        const MRenderItem* constItem = list.itemAt(i);
        if (constItem) {
            const_cast<MRenderItem*>(constItem)->enable(false);
        }
    }
}

}  // namespace

MHWRender::MPxGeometryOverride* MmdRenderGeometryOverride::creator(
    const MObject& object)
{
    return new MmdRenderGeometryOverride(object);
}

MmdRenderGeometryOverride::MmdRenderGeometryOverride(const MObject& object)
    : MPxGeometryOverride(object)
{
    MStatus status;
    shape_ = MmdRenderShape::fromMObject(object, &status);
    if (!status || !shape_) {
        shape_ = nullptr;
    }
}

MmdRenderGeometryOverride::~MmdRenderGeometryOverride()
{
    if (solidShader_) {
        MRenderer* renderer = MRenderer::theRenderer();
        const MShaderManager* shaderManager =
            renderer ? renderer->getShaderManager() : nullptr;
        if (shaderManager) {
            shaderManager->releaseShader(solidShader_);
        }
        solidShader_ = nullptr;
    }
}

MHWRender::DrawAPI MmdRenderGeometryOverride::supportedDrawAPIs() const
{
    return MHWRender::DrawAPI::kOpenGL |
           MHWRender::DrawAPI::kDirectX11 |
           MHWRender::DrawAPI::kOpenGLCoreProfile;
}

bool MmdRenderGeometryOverride::hasUIDrawables() const
{
    return false;
}

bool MmdRenderGeometryOverride::supportsEvaluationManagerParallelUpdate() const
{
    // The first witness deliberately keeps its transient shape state on the
    // node and avoids claiming worker-thread ownership until that boundary is
    // persisted as Maya data.
    return false;
}

bool MmdRenderGeometryOverride::requiresGeometryUpdate() const
{
    return shape_ != nullptr;
}

bool MmdRenderGeometryOverride::requiresUpdateRenderItems(
    const MDagPath& /*path*/) const
{
    return shape_ != nullptr;
}

void MmdRenderGeometryOverride::updateDG()
{
    // All witness data is populated before the shape is made visible.  No
    // built-in mesh plugs are read here, preserving the ordinary importer.
}

void MmdRenderGeometryOverride::updateRenderItems(
    const MDagPath& /*path*/, MHWRender::MRenderItemList& list)
{
    if (!shape_) {
        return;
    }

    // Disable stale items before rebuilding the pass list.  They are enabled
    // by findOrCreateItem only after the current shape still owns geometry for
    // that pass; populateGeometry disables them again on any buffer failure.
    shape_->clearRenderItemWitness();
    disableItems(list);

    MRenderer* renderer = MRenderer::theRenderer();
    const MShaderManager* shaderManager =
        renderer ? renderer->getShaderManager() : nullptr;
    if (!solidShader_ && shaderManager) {
        solidShader_ = shaderManager->getStockShader(MShaderManager::k3dSolidShader);
    }
    if (solidShader_) {
        // The stock solid shader has no material-node input on this transient
        // witness shape.  Set an opaque diagnostic color explicitly so a
        // transparent-queue item does not disappear because its default
        // alpha is zero.  This is only draw-preparation visibility; it is not
        // the MMD material alpha implementation.
        static const float kWitnessColor[4] = {1.0F, 1.0F, 1.0F, 1.0F};
        solidShader_->setParameter("solidColor", kWitnessColor);
    }

    std::vector<mmd::MmdDrawPass> witnessPasses;
    witnessPasses.reserve(3U);
    for (std::size_t i = 0; i < kRenderItemNames.size(); ++i) {
        const mmd::MmdDrawPass pass = static_cast<mmd::MmdDrawPass>(i);
        if (!shape_->hasPassGeometry(pass)) {
            continue;
        }

        MRenderItem* item = findOrCreateItem(
            list, renderItemName(pass),
            static_cast<MGeometry::DrawMode>(MGeometry::kShaded |
                                             MGeometry::kTextured));
        if (!item) {
            return;
        }
        if (solidShader_) {
            if (!item->setShader(solidShader_)) {
                MGlobal::displayError(
                    MString("[mmdRenderOverride] Failed to assign stock shader to ") +
                    item->name());
                disableItems(list);
                shape_->clearRenderItemWitness();
                return;
            }
        } else {
            MGlobal::displayError(
                "[mmdRenderOverride] Stock solid shader is unavailable.");
            disableItems(list);
            shape_->clearRenderItemWitness();
            return;
        }
        if (pass == mmd::MmdDrawPass::Transparent) {
            item->setTreatAsTransparent(true);
            item->setSupportsAdvancedTransparency(true);
        }
        item->castsShadows(pass != mmd::MmdDrawPass::Transparent);
        item->receivesShadows(pass != mmd::MmdDrawPass::Transparent);
        witnessPasses.push_back(pass);
    }

    // The commandPort diagnostic becomes ready only after every item in the
    // pass-ordered list has been created.  This is draw-preparation evidence,
    // not a visual parity or GoldenOracle claim.
    shape_->recordRenderItemWitness(witnessPasses);
}

void MmdRenderGeometryOverride::populateGeometry(
    const MHWRender::MGeometryRequirements& requirements,
    const MHWRender::MRenderItemList& renderItems,
    MHWRender::MGeometry& data)
{
    if (!shape_) {
        return;
    }

    const MmdRenderShape::GeometryData& geometry = shape_->geometry();
    const unsigned int vertexCount =
        static_cast<unsigned int>(geometry.positions.size() / 3U);

    const MHWRender::MVertexBufferDescriptorList& descriptors =
        requirements.vertexRequirements();
    auto failClosed = [&](const char* reason) {
        MGlobal::displayError(
            MString("[mmdRenderOverride] Geometry population failed: ") +
            reason);
        disableItems(renderItems);
        shape_->clearRenderItemWitness();
    };
    if (vertexCount == 0U) {
        failClosed("shape has no vertices");
        return;
    }

    bool positionBufferCommitted = false;
    std::ostringstream descriptorSummary;
    for (int i = 0; i < descriptors.length(); ++i) {
        MHWRender::MVertexBufferDescriptor descriptor;
        if (!descriptors.getDescriptor(i, descriptor)) {
            failClosed("could not read a vertex buffer descriptor");
            return;
        }
        if (descriptor.dataType() != MHWRender::MGeometry::kFloat ||
            descriptor.dimension() <= 0) {
            failClosed("only positive-dimension float vertex streams are supported");
            return;
        }
        if (descriptorSummary.tellp() > 0) {
            descriptorSummary << ';';
        }
        descriptorSummary << MHWRender::MGeometry::semanticString(
                                 descriptor.semantic())
                          .asChar()
                          << ':' << descriptor.dimension() << ':'
                          << MHWRender::MGeometry::dataTypeString(
                                 descriptor.dataType())
                                 .asChar();

        MHWRender::MVertexBuffer* buffer = data.createVertexBuffer(descriptor);
        if (!buffer) {
            failClosed("could not create a vertex buffer");
            return;
        }
        float* destination = static_cast<float*>(buffer->acquire(vertexCount, false));
        if (!destination) {
            failClosed("could not acquire a vertex buffer");
            return;
        }
        const unsigned int dimension = descriptor.dimension();
        std::fill(destination, destination + vertexCount * dimension, 0.0F);

        switch (descriptor.semantic()) {
        case MHWRender::MGeometry::kPosition:
            for (unsigned int vertex = 0; vertex < vertexCount; ++vertex) {
                const std::size_t source = static_cast<std::size_t>(vertex) * 3U;
                const std::size_t target =
                    static_cast<std::size_t>(vertex) * dimension;
                for (unsigned int component = 0;
                     component < dimension && component < 3U; ++component) {
                    destination[target + component] = geometry.positions[source + component];
                }
                if (dimension > 3U) {
                    destination[target + 3U] = 1.0F;
                }
            }
            positionBufferCommitted = true;
            break;
        case MHWRender::MGeometry::kNormal:
            for (unsigned int vertex = 0; vertex < vertexCount; ++vertex) {
                const std::size_t target =
                    static_cast<std::size_t>(vertex) * dimension;
                if (dimension > 1U) {
                    destination[target + 1U] = 1.0F;
                }
            }
            break;
        case MHWRender::MGeometry::kTexture:
            for (unsigned int vertex = 0; vertex < vertexCount; ++vertex) {
                const std::size_t target =
                    static_cast<std::size_t>(vertex) * dimension;
                if (dimension > 1U) {
                    destination[target + 1U] = 0.0F;
                }
            }
            break;
        default:
            // Keep unsupported streams deterministic if Maya adds a
            // requirement to the stock shader in a future version.
            break;
        }
        buffer->commit(destination);
    }

    std::size_t associatedIndexCount = 0U;
    for (int i = 0; i < renderItems.length(); ++i) {
        const MRenderItem* item = renderItems.itemAt(i);
        if (!item) {
            continue;
        }
        const MString itemName = item->name();
        mmd::MmdDrawPass pass = mmd::MmdDrawPass::Opaque;
        bool matched = false;
        for (std::size_t passIndex = 0; passIndex < kRenderItemNames.size();
             ++passIndex) {
            if (itemName == kRenderItemNames[passIndex]) {
                pass = static_cast<mmd::MmdDrawPass>(passIndex);
                matched = true;
                break;
            }
        }
        if (!matched) {
            continue;
        }

        const std::vector<uint32_t>& indices =
            geometry.indices[static_cast<std::size_t>(pass)];
        if (indices.empty() ||
            indices.size() > std::numeric_limits<unsigned int>::max()) {
            failClosed("render item has no valid index data");
            return;
        }
        MHWRender::MIndexBuffer* indexBuffer =
            data.createIndexBuffer(MHWRender::MGeometry::kUnsignedInt32);
        if (!indexBuffer) {
            failClosed("could not create an index buffer");
            return;
        }
        uint32_t* destination =
            static_cast<uint32_t*>(indexBuffer->acquire(
                static_cast<unsigned int>(indices.size()), false));
        if (!destination) {
            failClosed("could not acquire an index buffer");
            return;
        }
        std::memcpy(destination, indices.data(), indices.size() * sizeof(uint32_t));
        indexBuffer->commit(destination);
        if (!item->associateWithIndexBuffer(indexBuffer)) {
            failClosed("could not associate an index buffer");
            return;
        }
        associatedIndexCount += indices.size();
    }

    if (!positionBufferCommitted || associatedIndexCount == 0U) {
        failClosed("position or index buffers were not committed");
        return;
    }

    shape_->recordGeometryWitness(
        vertexCount, associatedIndexCount, descriptorSummary.str());
}

void MmdRenderGeometryOverride::cleanUp()
{
    // MGeometry owns the transient buffers.  The override owns only the stock
    // shader, released in its destructor while Maya's renderer is alive.
}
