/**
 * @file MmdNativeMaterial.h
 * @brief Shared native MMD material parameter binding.
 */

#pragma once

#include <maya/MShaderManager.h>
#include <maya/MTextureManager.h>

#include "MmdRenderQueue.h"
#include "MmdRenderShape.h"

namespace mmd {

/**
 * Bind an already-resolved native material to one shader instance.
 *
 * Texture acquisition and handle ownership remain with the caller.  This
 * keeps the geometry override's cache and destruction path independent from
 * other native drawing operations.
 */
bool bindNativeMaterialParameters(
    MHWRender::MShaderInstance* shader,
    const MmdRenderQueueInput& material,
    MHWRender::MTexture* mainTexture,
    MHWRender::MTexture* sphereTexture,
    MHWRender::MTexture* toonTexture,
    bool toonTextureRequested,
    MmdRenderShape::MaterialBindingDiagnostic* diagnostic);

}  // namespace mmd
