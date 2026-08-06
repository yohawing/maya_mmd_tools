/**
 * @file MmdRenderGeometryOverride.h
 * @brief Minimal VP2 geometry ownership for MmdRenderShape.
 */

#pragma once

#include <maya/MPxGeometryOverride.h>
#include <maya/MShaderManager.h>
#include <maya/MTextureManager.h>

#include <cstddef>
#include <string>
#include <unordered_map>

#include "MmdRenderQueue.h"

class MmdRenderShape;

class MmdRenderGeometryOverride : public MHWRender::MPxGeometryOverride {
public:
    static MHWRender::MPxGeometryOverride* creator(const MObject& object);

    ~MmdRenderGeometryOverride() override;

    MHWRender::DrawAPI supportedDrawAPIs() const override;
    bool hasUIDrawables() const override;
    bool supportsEvaluationManagerParallelUpdate() const override;
    bool requiresGeometryUpdate() const override;
    bool requiresUpdateRenderItems(const MDagPath& path) const override;

    void updateDG() override;
    void updateRenderItems(const MDagPath& path,
                           MHWRender::MRenderItemList& list) override;
    void populateGeometry(const MHWRender::MGeometryRequirements& requirements,
                           const MHWRender::MRenderItemList& renderItems,
                           MHWRender::MGeometry& data) override;
    void cleanUp() override;

private:
    explicit MmdRenderGeometryOverride(const MObject& object);

    MHWRender::MTexture* acquireNativeTexture(
        const std::string& path,
        MHWRender::MTextureManager* textureManager);
    bool setNativeMaterialParameters(
        MHWRender::MShaderInstance* shader,
        const mmd::MmdRenderQueueInput& material,
        MHWRender::MTextureManager* textureManager);

    MmdRenderShape* shape_ = nullptr;
    std::unordered_map<std::string, MHWRender::MShaderInstance*>
        materialShaders_;
    std::unordered_map<std::string, MHWRender::MTexture*> materialTextures_;
};
