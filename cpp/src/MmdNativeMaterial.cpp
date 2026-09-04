/**
 * @file MmdNativeMaterial.cpp
 * @brief Shared native MMD material parameter binding.
 */

#include "MmdNativeMaterial.h"

#include "MmdRenderOverride.h"

#include <maya/MGlobal.h>

#include <cstdlib>
#include <filesystem>

namespace mmd {

namespace {

std::filesystem::path gBundledNativeShaderPath;

std::filesystem::path findBundledNativeShaderPath(const MString& loadPath)
{
    if (loadPath.length() == 0) {
        return {};
    }

    try {
        const std::filesystem::path pluginPath =
            std::filesystem::u8path(loadPath.asUTF8());
        std::filesystem::path directory = pluginPath.parent_path();
        while (!directory.empty()) {
            const std::filesystem::path candidate =
                directory / "mmd_tools" / "shaders" / "MMDNativeShader.fx";
            if (std::filesystem::is_regular_file(candidate)) {
                return std::filesystem::absolute(candidate).lexically_normal();
            }

            const std::filesystem::path parent = directory.parent_path();
            if (parent == directory) {
                break;
            }
            directory = parent;
        }
    } catch (const std::filesystem::filesystem_error&) {
        // Keep the relative fallback for direct plug-in consumers.
    }
    return {};
}

}  // namespace

void setNativeMaterialPluginLoadPath(const MString& loadPath)
{
    gBundledNativeShaderPath = findBundledNativeShaderPath(loadPath);
}

std::string nativeMaterialShaderPath()
{
    const char* configured = std::getenv("MMD_TOOLS_NATIVE_SHADER_PATH");
    if (configured && *configured) {
        return configured;
    }
    if (!gBundledNativeShaderPath.empty()) {
        return gBundledNativeShaderPath.u8string();
    }
    return "mmd_tools/shaders/MMDNativeShader.fx";
}

std::string nativeMaterialSharedToonPath(int sharedToonIndex)
{
    if (sharedToonIndex < 0 || sharedToonIndex > 9) {
        return {};
    }

    std::filesystem::path toonDirectory;
    const char* configured = std::getenv("MMD_TOOLS_NATIVE_TOON_DIR");
    if (configured && *configured) {
        toonDirectory = std::filesystem::u8path(configured);
    } else {
        toonDirectory =
            std::filesystem::u8path(nativeMaterialShaderPath()).parent_path() /
            "toon_textures";
    }
    const std::string fileName =
        std::string("toon") + (sharedToonIndex < 9 ? "0" : "") +
        std::to_string(sharedToonIndex + 1) + ".bmp";
    return (toonDirectory / fileName).lexically_normal().u8string();
}

bool bindNativeMaterialParameters(
    MHWRender::MShaderInstance* shader,
    const MmdRenderQueueInput& material,
    MHWRender::MTexture* mainTexture,
    MHWRender::MTexture* sphereTexture,
    MHWRender::MTexture* toonTexture,
    bool toonTextureRequested,
    MmdRenderShape::MaterialBindingDiagnostic* diagnostic)
{
    if (!shader) {
        return false;
    }

    // Bind the scalar/color subset and texture switches explicitly so an
    // effect instance never inherits authored values from another item.
    const float lightDirection[3] = {-0.5F, -1.0F, -1.0F};
    const float lightColor[3] = {0.6039216F, 0.6039216F, 0.6039216F};
    const bool scalarBinding =
        shader->setParameter("DiffuseColorRGB", material.diffuseColor.data()) &&
        shader->setParameter("DiffuseColorA", material.diffuseAlpha) &&
        shader->setParameter("Opacity", 1.0F) &&
        shader->setParameter("SpecularColor", material.specularColor.data()) &&
        shader->setParameter("Shininess", material.specularPower) &&
        shader->setParameter("AmbientColor", material.ambientColor.data()) &&
        shader->setParameter("EdgeColorRGB", material.edgeColor.data()) &&
        shader->setParameter("EdgeColorA", material.edgeAlpha) &&
        shader->setParameter("EdgeSize", material.edgeSize) &&
        shader->setParameter("MainTextureMultiply",
                             material.mainTextureMultiply.data()) &&
        shader->setParameter("MainTextureAdd", material.mainTextureAdd.data()) &&
        shader->setParameter("SphereTextureMultiply",
                             material.sphereTextureMultiply.data()) &&
        shader->setParameter("SphereTextureAdd",
                             material.sphereTextureAdd.data()) &&
        shader->setParameter("ToonTextureMultiply",
                             material.toonTextureMultiply.data()) &&
        shader->setParameter("ToonTextureAdd", material.toonTextureAdd.data()) &&
        shader->setParameter("SphereMode", material.sphereMode) &&
        shader->setParameter("HasMainTexture", 0) &&
        shader->setParameter("HasSphereTexture", 0) &&
        shader->setParameter("HasToonTexture", 0) &&
        shader->setParameter("NativeCasterProbe", 0) &&
        shader->setParameter("NativeCasterHardShadow", 0) &&
        shader->setParameter(
            "NativeCasterShadowBias",
            MmdNativeCasterRenderOverride::kDefaultHardShadowBias) &&
        shader->setParameter("UseShadows", false) &&
        shader->setParameter("ShadowStrength", 1.0F) &&
        shader->setParameter("ToonCoordinateOffset", 0.55F) &&
        shader->setParameter("NativeSrgbOutput", 1) &&
        shader->setParameter("MMDLightDirection", lightDirection) &&
        shader->setParameter("MMDLightColor", lightColor);
    if (diagnostic) {
        diagnostic->scalarParameterBindingSuccess = scalarBinding;
    }
    if (!scalarBinding) {
        return false;
    }

    // A requested-but-unavailable texture is a visible diagnostic failure but
    // remains non-fatal, matching the existing fallback that draws without
    // that optional texture.  A failed assignment to an acquired handle is
    // still fatal as before.
    bool mainTextureBinding = material.mainTexturePath.empty() || mainTexture;
    if (mainTexture) {
        MHWRender::MTextureAssignment assignment{mainTexture};
        mainTextureBinding = shader->setParameter("MainTexture", assignment);
    }
    if (diagnostic) {
        diagnostic->mainTextureBindingSuccess = mainTextureBinding;
    }
    if (!mainTextureBinding && mainTexture) {
        return false;
    }

    bool sphereTextureBinding =
        material.sphereTexturePath.empty() || sphereTexture;
    if (sphereTexture) {
        MHWRender::MTextureAssignment assignment{sphereTexture};
        sphereTextureBinding =
            shader->setParameter("SphereTexture", assignment);
    }
    if (diagnostic) {
        diagnostic->sphereTextureBindingSuccess = sphereTextureBinding;
    }
    if (!sphereTextureBinding && sphereTexture) {
        return false;
    }

    bool toonTextureBinding = !toonTextureRequested || toonTexture;
    if (toonTexture) {
        MHWRender::MTextureAssignment assignment{toonTexture};
        toonTextureBinding = shader->setParameter("ToonTexture", assignment);
    }
    if (diagnostic) {
        diagnostic->toonTextureBindingSuccess = toonTextureBinding;
    }
    if (!toonTextureBinding && toonTexture) {
        return false;
    }

    const bool switchBinding =
        shader->setParameter("HasMainTexture", mainTexture ? 1 : 0) &&
        shader->setParameter("HasSphereTexture", sphereTexture ? 1 : 0) &&
        shader->setParameter("HasToonTexture", toonTexture ? 1 : 0);
    if (diagnostic) {
        diagnostic->switchParameterBindingSuccess = switchBinding;
        diagnostic->parameterBindingSuccess = switchBinding;
    }
    return switchBinding;
}

}  // namespace mmd
