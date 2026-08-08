/**
 * @file MmdRenderGeometryOverride.cpp
 * @brief VP2 render-item and geometry ownership for the opt-in witness shape.
 */

#include "MmdRenderGeometryOverride.h"

#include "MmdRenderShape.h"
#include "MmdRenderOverride.h"

#include <maya/MHWGeometry.h>
#include <maya/MGlobal.h>
#include <maya/MShaderManager.h>
#include <maya/MTextureManager.h>
#include <maya/MViewport2Renderer.h>

#include <algorithm>
#include <cstddef>
#include <cstdlib>
#include <filesystem>
#include <limits>
#include <sstream>
#include <string>
#include <unordered_map>
#include <vector>

namespace {

using namespace MHWRender;

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
                directory / "mmd_tools" / "shaders" / "MMDShader.fx";
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
        // Keep the legacy relative fallback.  The caller reports the shader
        // lookup failure with the selected path.
    }
    return {};
}

MString renderItemName(const MmdRenderShape::QueueGeometry& geometry,
                       std::size_t queueIndex,
                       bool outline = false)
{
    const std::string name =
        "mmdRenderQueue_" +
        std::string(mmd::mmdDrawPassName(geometry.entry.pass)) + "_m" +
        std::to_string(geometry.entry.materialIndex) + "_s" +
        std::to_string(geometry.entry.submeshIndex) + "_q" +
        std::to_string(queueIndex) + (outline ? "_edge" : "");
    return MString(name.c_str());
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

const char* nativeShaderTechnique(mmd::MmdDrawPass pass,
                                  bool doubleSided,
                                  bool textureAlphaBlend)
{
    if (pass == mmd::MmdDrawPass::Transparent || textureAlphaBlend) {
        return doubleSided ? "MMDNativeTranslucentDoubleSided"
                           : "MMDNativeTranslucent";
    }
    return doubleSided ? "MMDNativeOpaqueDoubleSided" : "MMDNativeOpaque";
}

const char* nativeOutlineShaderTechnique(mmd::MmdDrawPass pass,
                                          bool doubleSided,
                                          bool textureAlphaBlend)
{
    return pass == mmd::MmdDrawPass::Transparent || textureAlphaBlend
               ? (doubleSided ? "MMDNativeOutlineTranslucentDoubleSided"
                              : "MMDNativeOutlineTranslucent")
               : (doubleSided ? "MMDNativeOutlineDoubleSided"
                              : "MMDNativeOutline");
}

std::string nativeShaderPath()
{
    const char* configured = std::getenv("MMD_TOOLS_NATIVE_SHADER_PATH");
    if (configured && *configured) {
        return configured;
    }

    if (!gBundledNativeShaderPath.empty()) {
        return gBundledNativeShaderPath.u8string();
    }

    // Keep the relative fallback for direct command-line/plugin consumers
    // that do not initialize the plug-in entry point through Maya.
    return "mmd_tools/shaders/MMDShader.fx";
}

std::string nativeShaderCacheKey(
    const MmdRenderShape::QueueGeometry& geometry,
    bool outline,
    bool textureAlphaBlend)
{
    const char* technique = outline
                                ? nativeOutlineShaderTechnique(
                                      geometry.entry.pass,
                                      geometry.material.doubleSided,
                                      textureAlphaBlend)
                                : nativeShaderTechnique(geometry.entry.pass,
                                                         geometry.material.doubleSided,
                                                         textureAlphaBlend);
    return std::string(technique) +
           ":m" +
           std::to_string(geometry.material.materialIndex) +
           (outline ? ":edge" : ":body");
}

std::string nativeSharedToonPath(int sharedToonIndex)
{
    if (sharedToonIndex < 0 || sharedToonIndex > 9) {
        return {};
    }

    std::filesystem::path toonDirectory;
    const char* configured = std::getenv("MMD_TOOLS_NATIVE_TOON_DIR");
    if (configured && *configured) {
        toonDirectory = std::filesystem::u8path(configured);
    } else {
        const std::filesystem::path shaderPath =
            std::filesystem::u8path(nativeShaderPath());
        toonDirectory = shaderPath.parent_path() / "toon_textures";
    }
    const std::string fileName =
        std::string("toon") + (sharedToonIndex < 9 ? "0" : "") +
        std::to_string(sharedToonIndex + 1) + ".bmp";
    return (toonDirectory / fileName).lexically_normal().u8string();
}

}  // namespace

void MmdRenderGeometryOverride::setPluginLoadPath(const MString& loadPath)
{
    gBundledNativeShaderPath = findBundledNativeShaderPath(loadPath);
}

MHWRender::MTexture* MmdRenderGeometryOverride::acquireNativeTexture(
    const std::string& path,
    MHWRender::MTextureManager* textureManager)
{
    if (path.empty() || !textureManager) {
        return nullptr;
    }

    const auto cached = materialTextures_.find(path);
    if (cached != materialTextures_.end()) {
        return cached->second;
    }

    MString mayaPath;
    mayaPath.setUTF8(path.c_str());
    MHWRender::MTexture* texture =
        textureManager->acquireTexture(mayaPath, 0, false);
    materialTextures_.emplace(path, texture);
    if (!texture) {
        MGlobal::displayWarning(
            MString("[mmdRenderOverride] Native texture unavailable: ") +
            mayaPath);
    }
    return texture;
}

bool MmdRenderGeometryOverride::setNativeMaterialParameters(
    MHWRender::MShaderInstance* shader,
    const mmd::MmdRenderQueueInput& material,
    MHWRender::MTextureManager* textureManager,
    MmdRenderShape::MaterialBindingDiagnostic* diagnostic)
{
    if (!shader) {
        return false;
    }

    // Native items use the same raw gamma texture inputs as the authored MMD
    // shader.  Exposure control is disabled when the Maya texture handle is
    // acquired because NativeSrgbOutput writes directly to the CM-off sRGB
    // capture target.
    MHWRender::MTexture* mainTexture =
        acquireNativeTexture(material.mainTexturePath, textureManager);
    MHWRender::MTexture* sphereTexture =
        acquireNativeTexture(material.sphereTexturePath, textureManager);
    const std::string toonPath = material.toonTexturePath.empty()
                                     ? nativeSharedToonPath(material.sharedToonIndex)
                                     : material.toonTexturePath;
    MHWRender::MTexture* toonTexture =
        acquireNativeTexture(toonPath, textureManager);

    if (diagnostic) {
        diagnostic->mainTexturePath = material.mainTexturePath;
        diagnostic->sphereTexturePath = material.sphereTexturePath;
        diagnostic->toonTexturePath = toonPath;
        diagnostic->toonTextureSource = material.toonTexturePath.empty()
                                            ? (material.sharedToonIndex >= 0
                                                   ? "shared"
                                                   : "none")
                                            : "explicit";
        diagnostic->mainTextureRequested = !material.mainTexturePath.empty();
        diagnostic->sphereTextureRequested = !material.sphereTexturePath.empty();
        diagnostic->toonTextureRequested = !toonPath.empty();
        diagnostic->mainTextureAcquired = mainTexture != nullptr;
        diagnostic->sphereTextureAcquired = sphereTexture != nullptr;
        diagnostic->toonTextureAcquired = toonTexture != nullptr;
        diagnostic->sphereMode = material.sphereMode;
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
        shader->setParameter("SphereMode", material.sphereMode) &&
        shader->setParameter("HasMainTexture", 0) &&
        shader->setParameter("HasSphereTexture", 0) &&
        shader->setParameter("HasToonTexture", 0) &&
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

    bool sphereTextureBinding = material.sphereTexturePath.empty() || sphereTexture;
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

    bool toonTextureBinding = toonPath.empty() || toonTexture;
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
    MRenderer* renderer = MRenderer::theRenderer();
    const MShaderManager* shaderManager =
        renderer ? renderer->getShaderManager() : nullptr;
    if (shaderManager) {
        for (const auto& shader : materialShaders_) {
            if (shader.second) {
                const bool receiverShader =
                    receiverShaders_.count(shader.second) != 0U;
                if (receiverShader) {
                    // Retire bookkeeping immediately around Maya's own
                    // shader release.  No null target assignment is issued:
                    // the persistent caster target remains valid until every
                    // borrowed body shader has completed this boundary.
                    MmdNativeCasterRenderOverride::beginReceiverShaderRetire(
                        shader.second);
                }
                shaderManager->releaseShader(shader.second);
                if (receiverShader) {
                    MmdNativeCasterRenderOverride::finishReceiverShaderRetire(
                        shader.second);
                }
            }
        }
    }
    materialShaders_.clear();

    MTextureManager* textureManager =
        renderer ? renderer->getTextureManager() : nullptr;
    if (textureManager) {
        for (const auto& texture : materialTextures_) {
            if (texture.second) {
                textureManager->releaseTexture(texture.second);
            }
        }
    }
    materialTextures_.clear();
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
    shape_->clearMaterialBindingDiagnostics();
    disableItems(list);

    MRenderer* renderer = MRenderer::theRenderer();
    const MShaderManager* shaderManager =
        renderer ? renderer->getShaderManager() : nullptr;
    MTextureManager* textureManager =
        renderer ? renderer->getTextureManager() : nullptr;
    if (!shaderManager) {
        MGlobal::displayError(
            "[mmdRenderOverride] Maya shader manager is unavailable.");
        shape_->clearRenderItemWitness();
        return;
    }

    const MmdRenderShape::GeometryData& geometry = shape_->geometry();
    auto configureItem = [&](const MmdRenderShape::QueueGeometry& queueGeometry,
                             std::size_t queueIndex,
                             bool outline) {
        const mmd::MmdDrawPass pass = queueGeometry.entry.pass;
        MTexture* mainTexture =
            acquireNativeTexture(queueGeometry.material.mainTexturePath,
                                 textureManager);
        const bool textureAlphaBlend =
            mainTexture && mainTexture->hasTransparentAlpha();
        const bool effectiveTransparent =
            pass == mmd::MmdDrawPass::Transparent || textureAlphaBlend;
        const char* technique = outline
                                    ? nativeOutlineShaderTechnique(
                                          pass,
                                          queueGeometry.material.doubleSided,
                                          textureAlphaBlend)
                                    : nativeShaderTechnique(
                                          pass,
                                          queueGeometry.material.doubleSided,
                                          textureAlphaBlend);
        MmdRenderShape::MaterialBindingDiagnostic diagnostic;
        diagnostic.queueIndex = queueIndex;
        diagnostic.materialIndex = queueGeometry.entry.materialIndex;
        diagnostic.submeshIndex = queueGeometry.entry.submeshIndex;
        diagnostic.pass = mmd::mmdDrawPassName(pass);
        diagnostic.outline = outline;
        diagnostic.technique = technique;
        diagnostic.uvStreamAvailable = queueGeometry.uvStreamAvailable;
        diagnostic.diffuseAlpha = queueGeometry.material.diffuseAlpha;
        diagnostic.textureAlphaBlend = textureAlphaBlend;
        diagnostic.effectiveTransparent = effectiveTransparent;
        diagnostic.mainTexturePath = queueGeometry.material.mainTexturePath;
        diagnostic.sphereTexturePath = queueGeometry.material.sphereTexturePath;
        diagnostic.toonTexturePath = queueGeometry.material.toonTexturePath.empty()
                                         ? nativeSharedToonPath(
                                               queueGeometry.material.sharedToonIndex)
                                         : queueGeometry.material.toonTexturePath;
        diagnostic.toonTextureSource =
            queueGeometry.material.toonTexturePath.empty()
                ? (queueGeometry.material.sharedToonIndex >= 0 ? "shared"
                                                                 : "none")
                : "explicit";
        diagnostic.mainTextureRequested =
            !queueGeometry.material.mainTexturePath.empty();
        diagnostic.sphereTextureRequested =
            !queueGeometry.material.sphereTexturePath.empty();
        diagnostic.toonTextureRequested = !diagnostic.toonTexturePath.empty();
        diagnostic.mainTextureAcquired = mainTexture != nullptr;
        diagnostic.sphereMode = queueGeometry.material.sphereMode;
        MRenderItem* item = findOrCreateItem(
            list, renderItemName(queueGeometry, queueIndex, outline),
            static_cast<MGeometry::DrawMode>(MGeometry::kShaded |
                                             MGeometry::kTextured));
        if (!item) {
            shape_->recordMaterialBindingDiagnostic(diagnostic);
            return false;
        }
        MHWRender::MShaderInstance* materialShader = nullptr;
        const std::string shaderKey = nativeShaderCacheKey(
            queueGeometry, outline, textureAlphaBlend);
        const std::string shaderPath = nativeShaderPath();
        const auto shaderIt = materialShaders_.find(shaderKey);
        if (shaderIt != materialShaders_.end()) {
            materialShader = shaderIt->second;
        } else {
            materialShader = shaderManager->getEffectsFileShader(
                MString(shaderPath.c_str()),
                MString(technique));
            if (materialShader) {
                materialShaders_.emplace(shaderKey, materialShader);
            }
        }
        if (!materialShader) {
            shape_->recordMaterialBindingDiagnostic(diagnostic);
            MGlobal::displayError(
                MString("[mmdRenderOverride] Native MMD shader is unavailable: ") +
                technique + " path=" + shaderPath.c_str());
            disableItems(list);
            shape_->clearRenderItemWitness();
            return false;
        }
        diagnostic.shaderAvailable = true;
        const bool parameterBindingSuccess = setNativeMaterialParameters(
            materialShader, queueGeometry.material, textureManager, &diagnostic);
        diagnostic.parameterBindingSuccess = parameterBindingSuccess;
        if (!parameterBindingSuccess) {
            shape_->recordMaterialBindingDiagnostic(diagnostic);
            MGlobal::displayError(
                MString("[mmdRenderOverride] Failed to bind material parameters to ") +
                item->name());
            disableItems(list);
            shape_->clearRenderItemWitness();
            return false;
        }
        diagnostic.shaderAssignmentSuccess = item->setShader(materialShader);
        diagnostic.bindingSuccess = diagnostic.shaderAssignmentSuccess;
        shape_->recordMaterialBindingDiagnostic(diagnostic);
        if (!diagnostic.shaderAssignmentSuccess) {
            MGlobal::displayError(
                MString("[mmdRenderOverride] Failed to bind material shader to ") +
                item->name());
            disableItems(list);
            shape_->clearRenderItemWitness();
            return false;
        }
        if (!outline) {
            // Register only after all material parameters and the render-item
            // shader assignment succeed.  This leaves no transient registry
            // entry that could outlive a failed item setup.
            MmdNativeCasterRenderOverride::registerReceiverShader(
                materialShader);
            receiverShaders_.insert(materialShader);
        }
        if (effectiveTransparent) {
            item->setTreatAsTransparent(true);
            item->setSupportsAdvancedTransparency(true);
        }
        // Opaque items draw the outline before the body; alpha-blended items
        // (including opaque-PMX materials with soft texture alpha) reverse
        // that order so the edge depth test can hide only discarded texels.
        // depthPriority is also useful as a diagnostic when a renderer groups
        // items internally; it does not replace the shader's blend/depth state.
        const bool outlineAfterBody = effectiveTransparent;
        const std::size_t priority =
            queueIndex * 2U +
            (outline ? (outlineAfterBody ? 1U : 0U)
                     : (outlineAfterBody ? 0U : 1U));
        item->depthPriority(static_cast<unsigned int>(priority));
        item->castsShadows(!outline && pass != mmd::MmdDrawPass::Transparent);
        item->receivesShadows(!outline && pass != mmd::MmdDrawPass::Transparent);
        return true;
    };

    for (std::size_t queueIndex = 0; queueIndex < geometry.queueGeometry.size();
         ++queueIndex) {
        const MmdRenderShape::QueueGeometry& queueGeometry =
            geometry.queueGeometry[queueIndex];
        MTexture* mainTexture =
            acquireNativeTexture(queueGeometry.material.mainTexturePath,
                                 textureManager);
        const bool textureAlphaBlend =
            mainTexture && mainTexture->hasTransparentAlpha();
        const bool effectiveTransparent =
            queueGeometry.entry.pass == mmd::MmdDrawPass::Transparent ||
            textureAlphaBlend;
        const bool outline = queueGeometry.material.edgeDrawing &&
                             queueGeometry.material.edgeSize > 0.0F &&
                             queueGeometry.material.edgeAlpha > 0.0F;
        if (effectiveTransparent) {
            // The translucent body must establish the depth-tested surface
            // before the inverted hull runs.  Otherwise the read-only edge
            // pass writes its opaque edge color into the white background and
            // the body blends against black across the whole interior.
            if (!configureItem(queueGeometry, queueIndex, false)) {
                return;
            }
            if (outline && !configureItem(queueGeometry, queueIndex, true)) {
                return;
            }
        } else {
            if (outline && !configureItem(queueGeometry, queueIndex, true)) {
                return;
            }
            if (!configureItem(queueGeometry, queueIndex, false)) {
                return;
            }
        }
    }
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
                const std::size_t source = static_cast<std::size_t>(vertex) * 3U;
                const std::size_t target =
                    static_cast<std::size_t>(vertex) * dimension;
                for (unsigned int component = 0;
                     component < dimension && component < 3U; ++component) {
                    destination[target + component] =
                        geometry.normals.size() == vertexCount * 3U
                            ? geometry.normals[source + component]
                            : (component == 1U ? 1.0F : 0.0F);
                }
            }
            break;
        case MHWRender::MGeometry::kTexture:
            for (unsigned int vertex = 0; vertex < vertexCount; ++vertex) {
                const std::size_t source = static_cast<std::size_t>(vertex) * 2U;
                const std::size_t target =
                    static_cast<std::size_t>(vertex) * dimension;
                for (unsigned int component = 0;
                     component < dimension && component < 2U; ++component) {
                    if (geometry.uvs.size() == vertexCount * 2U) {
                        destination[target + component] =
                            geometry.uvs[source + component];
                    }
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
    std::unordered_map<std::string, const MmdRenderShape::QueueGeometry*>
        queueGeometryByName;
    queueGeometryByName.reserve(geometry.queueGeometry.size());
    for (std::size_t queueIndex = 0;
         queueIndex < geometry.queueGeometry.size(); ++queueIndex) {
        const MmdRenderShape::QueueGeometry& candidate =
            geometry.queueGeometry[queueIndex];
        queueGeometryByName.emplace(
            std::string(renderItemName(candidate, queueIndex).asChar()),
            &candidate);
        if (candidate.material.edgeDrawing && candidate.material.edgeSize > 0.0F &&
            candidate.material.edgeAlpha > 0.0F) {
            queueGeometryByName.emplace(
                std::string(renderItemName(candidate, queueIndex, true).asChar()),
                &candidate);
        }
    }
    for (int i = 0; i < renderItems.length(); ++i) {
        const MRenderItem* item = renderItems.itemAt(i);
        if (!item) {
            continue;
        }
        const MString itemName = item->name();
        const auto queueGeometry = queueGeometryByName.find(itemName.asChar());
        if (queueGeometry == queueGeometryByName.end()) {
            continue;
        }

        const std::vector<uint32_t>& indices = queueGeometry->second->indices;
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
        for (std::size_t index = 0; index < indices.size(); ++index) {
            destination[index] =
                queueGeometry->second->vertexOffset + indices[index];
        }
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
    // Do not report a ready witness until VP2 has accepted the vertex and
    // index buffers for every current render item.  updateRenderItems() only
    // prepares item metadata; recording here makes the commandPort evidence
    // fail closed when geometry population is skipped or fails.
    shape_->recordRenderItemWitness(geometry.renderQueue);
}

void MmdRenderGeometryOverride::cleanUp()
{
    // MGeometry owns the transient buffers.  The override owns the per-material
    // stock shaders, released in its destructor while Maya's renderer is alive.
}
