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
#include <map>
#include <string>

class MmdShadowResources;

class MmdOrderedRenderOverride : public MHWRender::MRenderOverride {
public:
    MmdOrderedRenderOverride();
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

    struct FallbackState {
        bool requested = false;
        bool frameActive = false;
        bool retryPending = false;
        bool rawRetryActive = false;
        bool latched = false;
        std::string reason;
    };

    void requestFallback(const std::string& reason,
                         bool currentFrameUsesStandard = false);
    void clearFallback();
    FallbackState& activeFallbackState();
    const FallbackState* activeFallbackState() const;
    std::string fallbackDiagnosticReason() const;

    std::unique_ptr<MmdShadowResources> nativeCasterOwner_;
    OrderedRenderOperation* operation_ = nullptr;
    bool operationsInstalled_ = false;
    std::string activeDestination_;
    std::map<std::string, FallbackState> fallbackStates_;
};

class MmdOrderedRenderWitnessCommand : public MPxCommand {
public:
    static void* creator();
    static MSyntax newSyntax();
    MStatus doIt(const MArgList& args) override;
    bool isUndoable() const override;
};
