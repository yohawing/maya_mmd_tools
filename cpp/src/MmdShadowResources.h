/** @file MmdShadowResources.h
 * @brief Private shadow resources owned by MMD Render.
 */
#pragma once
#include <maya/MMatrix.h>
#include <maya/MRenderTargetManager.h>
#include <maya/MSelectionList.h>
#include <maya/MShaderManager.h>
#include <maya/MViewport2Renderer.h>

class MmdShadowResources {
public:
    static constexpr unsigned int kTargetSize = 2048U;
    static constexpr float kDefaultDepthBias = 0.35F;
    static constexpr float kDefaultHardShadowBias = 0.001F;
    MmdShadowResources() = default;
    ~MmdShadowResources();
    MmdShadowResources(const MmdShadowResources&) = delete;
    MmdShadowResources& operator=(const MmdShadowResources&) = delete;

    struct FrameResources {
        // The matrix and bias are snapshots for one preparation call.  The
        // targets are owned by this override and remain borrowed until the
        // override releases them; cleanup may retain them while receiver
        // assignments are still registered.  Callers must not release or
        // retain ownership of these pointers.  Cached shader assignments keep
        // the existing receiver registry retirement contract.
        MHWRender::MRenderTarget* colorTarget = nullptr;
        MHWRender::MRenderTarget* depthTarget = nullptr;
        MMatrix lightViewProjection;
        float depthBias = 0.0F;
        int selfShadowMode = 0;
        double selfShadowDistance = 0.0;
        bool ready = false;
    };

    MStatus prepareFrameResources(const MSelectionList& selection,
                                  FrameResources& resources,
                                  bool requireEnabledSelfShadow = false);

    static void registerReceiverShader(MHWRender::MShaderInstance* shader);
    static bool beginReceiverShaderRetire(MHWRender::MShaderInstance* shader);
    static void finishReceiverShaderRetire(MHWRender::MShaderInstance* shader);
    static bool shutdownReady();
private:
    bool acquireTargets();
    bool releaseTargets();
    MHWRender::MRenderTargetManager* targetManager_ = nullptr;
    MHWRender::MRenderTarget* colorTarget_ = nullptr;
    MHWRender::MRenderTarget* depthTarget_ = nullptr;
};
