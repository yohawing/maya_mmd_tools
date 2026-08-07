/**
 * @file MmdRenderOverride.h
 * @brief Opt-in native caster render override capability spike.
 *
 * The override inserts one MSceneRender operation before Maya's standard
 * viewport operations.  It targets only mmdRenderShape nodes and writes a
 * fixed caster depth/color witness to private 2048x2048 targets; the normal
 * scene, HUD, and present operations remain owned by Maya.
 */

#pragma once

#include <maya/MMatrix.h>
#include <maya/MArgList.h>
#include <maya/MObject.h>
#include <maya/MPxCommand.h>
#include <maya/MRenderTargetManager.h>
#include <maya/MSelectionList.h>
#include <maya/MShaderManager.h>
#include <maya/MString.h>
#include <maya/MSyntax.h>
#include <maya/MViewport2Renderer.h>

#include <cstddef>
#include <string>

class MmdNativeCasterRenderOverride : public MHWRender::MRenderOverride {
public:
    static constexpr unsigned int kTargetSize = 2048U;

    MmdNativeCasterRenderOverride();
    ~MmdNativeCasterRenderOverride() override;

    MHWRender::DrawAPI supportedDrawAPIs() const override;
    MString uiName() const override { return MString("MMD Native Caster"); }
    MStatus setup(const MString& destination) override;
    MStatus cleanup() override;

    static void setPluginLoadPath(const MString& loadPath);
    static void markRegistered(bool registered);
    static const MString& overrideName();
    static std::string diagnosticsJson();

private:
    class CasterSceneRender;

    bool acquireTargets();
    void releaseTargets();
    void releaseShader();
    bool buildCasterSelection(MSelectionList& selection) const;

    CasterSceneRender* casterOperation_ = nullptr;
    MHWRender::MRenderTargetManager* targetManager_ = nullptr;
    MHWRender::MRenderTarget* colorTarget_ = nullptr;
    MHWRender::MRenderTarget* depthTarget_ = nullptr;
    const MHWRender::MShaderManager* shaderManager_ = nullptr;
    MHWRender::MShaderInstance* shader_ = nullptr;
};

class MmdNativeCasterWitnessCommand : public MPxCommand {
public:
    static void* creator();
    static MSyntax newSyntax();
    MStatus doIt(const MArgList& args) override;
    bool isUndoable() const override;
};
