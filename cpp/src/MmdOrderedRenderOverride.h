/**
 * @file MmdOrderedRenderOverride.h
 * @brief Opt-in raw DX11 ordered draw for MMD proxy shapes.
 */

#pragma once

#include <maya/MArgList.h>
#include <maya/MPxCommand.h>
#include <maya/MSyntax.h>
#include <maya/MString.h>
#include <maya/MViewport2Renderer.h>

#include <memory>
#include <string>

class MmdNativeCasterRenderOverride;

class MmdOrderedRenderOverride : public MHWRender::MRenderOverride {
public:
    explicit MmdOrderedRenderOverride(
        MmdNativeCasterRenderOverride* nativeCasterOwner = nullptr);
    ~MmdOrderedRenderOverride() override;

    MHWRender::DrawAPI supportedDrawAPIs() const override;
    MString uiName() const override;
    MStatus setup(const MString& destination) override;
    MStatus cleanup() override;

    static const MString& overrideName();
    static void setPluginLoadPath(const MString& loadPath);
    static void markRegistered(bool registered);
    bool prepareForPluginUnload();
    static std::string diagnosticsJson(bool captureShadowDepth = false);

private:
    class OrderedRenderOperation;
    class OpaqueRenderOperation;

    void requestFallback(const std::string& reason);
    void clearFallback();

    MmdNativeCasterRenderOverride* nativeCasterOwner_ = nullptr;
    std::unique_ptr<MmdNativeCasterRenderOverride>
        privateNativeCasterOwner_;
    OrderedRenderOperation* operation_ = nullptr;
    bool operationsInstalled_ = false;
    bool fallbackRequested_ = false;
    std::string fallbackReason_;
};

class MmdOrderedRenderWitnessCommand : public MPxCommand {
public:
    static void* creator();
    static MSyntax newSyntax();
    MStatus doIt(const MArgList& args) override;
    bool isUndoable() const override;
};
