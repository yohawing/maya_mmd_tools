/**
 * mmdFastLoad.cpp
 *
 * Implementation of the mmdFastLoad command.
 *
 * Reads a PMX file and builds Maya mesh(es) using the mmd-anim-ffi
 * typed-buffer ABI:
 *   - Geometry  : mmd_runtime_parse_pmx_positions/uvs/indices_buffer
 *   - Normals   : mmd_runtime_parse_pmx_normals_buffer (authored, optional)
 *   - Materials : split via mmd_runtime_pmx_material_split_* (one mesh / material)
 *   - Morphs    : mmd_runtime_parse_pmx_non_geometry_json -> morphs[].vertexOffsets
 *
 * Coordinate conversions match the Python importer:
 *   - Position: (x, y, -z) * scale
 *   - UV:       V flipped (1.0 - v)
 *   - Winding:  reversed (PMX CCW -> Maya CW)
 */

#include "mmdFastLoad.h"
#include "MmdRenderQueue.h"
#include "MmdRenderShape.h"

#include <maya/MArgDatabase.h>
#include <maya/MDagModifier.h>
#include <maya/MDagPath.h>
#include <maya/MFloatArray.h>
#include <maya/MFnDependencyNode.h>
#include <maya/MFnDagNode.h>
#include <maya/MFnMesh.h>
#include <maya/MFnTransform.h>
#include <maya/MGlobal.h>
#include <maya/MIntArray.h>
#include <maya/MPointArray.h>
#include <maya/MSelectionList.h>
#include <maya/MStringArray.h>

// mmd-anim-ffi C ABI header (path set by CMake)
#include "mmd_runtime.h"

// Header-only JSON parser (vendored). Used to read the non-geometry JSON
// (vertex morphs) and the material-split manifest.
#include "third_party/json.hpp"

#include <algorithm>
#include <array>
#include <cctype>
#include <cmath>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <set>
#include <string>
#include <unordered_map>
#include <utility>
#include <vector>

using nlohmann::json;

// -----------------------------------------------------------------------
// Construction / destruction
// -----------------------------------------------------------------------

MmdFastLoad::MmdFastLoad()  = default;
MmdFastLoad::~MmdFastLoad() = default;

// -----------------------------------------------------------------------
// Creator / syntax
// -----------------------------------------------------------------------

void* MmdFastLoad::creator()
{
    return new MmdFastLoad();
}

MSyntax MmdFastLoad::newSyntax()
{
    MSyntax syntax;

    // -file / -f  <path>   (required)
    syntax.addFlag("-f", "-file", MSyntax::kString);

    // -name / -n  <string> (optional)
    syntax.addFlag("-n", "-name", MSyntax::kString);

    // -scale / -s <double> (optional, default 1.0)
    syntax.addFlag("-s", "-scale", MSyntax::kDouble);

    // -morphs / -mo <bool> (optional, default false)
    syntax.addFlag("-mo", "-morphs", MSyntax::kBoolean);

    // -split / -sp <bool> (optional, default false)
    syntax.addFlag("-sp", "-split", MSyntax::kBoolean);

    // -vp2Ownership / -vo <bool> (optional, default false)
    // Explicit opt-in to the custom DAG shape and VP2 geometry override.
    syntax.addFlag("-vo", "-vp2Ownership", MSyntax::kBoolean);

    syntax.enableEdit(false);

    return syntax;
}

// -----------------------------------------------------------------------
// Argument parsing
// -----------------------------------------------------------------------

bool MmdFastLoad::parseArgs(const MArgList& args)
{
    MArgDatabase argData(newSyntax(), args);

    if (!argData.isFlagSet("-f")) {
        MGlobal::displayError("[mmdFastLoad] Required flag missing: -file/-f <path>");
        return false;
    }

    filePath_ = argData.flagArgumentString("-f", 0).asChar();

    if (argData.isFlagSet("-n")) {
        baseName_ = argData.flagArgumentString("-n", 0).asChar();
    }

    if (argData.isFlagSet("-s")) {
        scale_ = argData.flagArgumentDouble("-s", 0);
    }

    if (argData.isFlagSet("-mo")) {
        enableMorphs_ = argData.flagArgumentBool("-mo", 0);
    }

    if (argData.isFlagSet("-sp")) {
        enableSplit_ = argData.flagArgumentBool("-sp", 0);
    }

    if (argData.isFlagSet("-vo")) {
        enableVp2Ownership_ = argData.flagArgumentBool("-vo", 0);
    }

    if (scale_ <= 0.0) {
        MGlobal::displayError("[mmdFastLoad] -scale must be > 0.0");
        return false;
    }

    return true;
}

// -----------------------------------------------------------------------
// Helpers
// -----------------------------------------------------------------------

namespace {

/**
 * Sanitize a string for use as a Maya node name:
 * keep alphanumeric + underscore, replace everything else with '_',
 * ensure it does not start with a digit.
 */
std::string sanitizeName(const std::string& raw)
{
    std::string out;
    out.reserve(raw.size());
    for (unsigned char c : raw) {
        if (c < 128 && (std::isalnum(c) || c == '_')) {
            out += static_cast<char>(c);
        } else {
            out += '_';
        }
    }
    if (out.empty()) {
        out = "MMDModel";
    }
    if (std::isdigit(static_cast<unsigned char>(out[0]))) {
        out.insert(0U, "M_");
    }
    return out;
}

std::string uniqueName(const std::string& base, std::set<std::string>& used)
{
    std::string candidate = sanitizeName(base);
    if (candidate.empty()) {
        candidate = "MMDNode";
    }

    std::string unique = candidate;
    unsigned int counter = 1;
    while (used.find(unique) != used.end()) {
        unique = candidate + "_" + std::to_string(counter++);
    }
    used.insert(unique);
    return unique;
}

std::string quoteMelName(const MString& name)
{
    return "\"" + std::string(name.asChar()) + "\"";
}

std::string materialTransparencyMode(const json& material)
{
    for (const char* key : {"transparencyMode", "alphaMode", "transparency"}) {
        if (!material.is_object() || !material.contains(key) ||
            !material[key].is_string()) {
            continue;
        }
        return material[key].get<std::string>();
    }
    return std::string();
}

float materialDiffuseAlpha(const json& material)
{
    if (!material.is_object() || !material.contains("diffuse") ||
        !material["diffuse"].is_array() || material["diffuse"].size() < 4 ||
        !material["diffuse"][3].is_number()) {
        return 1.0f;
    }
    return material["diffuse"][3].get<float>();
}

std::array<float, 3> materialDiffuseColor(const json& material)
{
    std::array<float, 3> color = {1.0F, 1.0F, 1.0F};
    if (!material.is_object() || !material.contains("diffuse") ||
        !material["diffuse"].is_array() || material["diffuse"].size() < 3U) {
        return color;
    }

    for (std::size_t component = 0; component < color.size(); ++component) {
        const json& value = material["diffuse"][component];
        if (!value.is_number()) {
            return {1.0F, 1.0F, 1.0F};
        }
        const double numericValue = value.get<double>();
        if (!std::isfinite(numericValue)) {
            return {1.0F, 1.0F, 1.0F};
        }
        color[component] = static_cast<float>(numericValue);
    }
    return color;
}

std::array<float, 3> materialColorProperty(
    const json& material,
    const char* key,
    const std::array<float, 3>& fallback)
{
    std::array<float, 3> color = fallback;
    if (!material.is_object() || !material.contains(key) ||
        !material[key].is_array() || material[key].size() < 3U) {
        return color;
    }
    for (std::size_t component = 0; component < color.size(); ++component) {
        const json& value = material[key][component];
        if (!value.is_number()) {
            return fallback;
        }
        const double numericValue = value.get<double>();
        if (!std::isfinite(numericValue)) {
            return fallback;
        }
        color[component] = static_cast<float>(numericValue);
    }
    return color;
}

float materialScalarProperty(const json& material,
                             const char* key,
                             float fallback)
{
    if (!material.is_object() || !material.contains(key) ||
        !material[key].is_number()) {
        return fallback;
    }
    const double numericValue = material[key].get<double>();
    return std::isfinite(numericValue) ? static_cast<float>(numericValue)
                                       : fallback;
}

float materialColorAlphaProperty(const json& material,
                                 const char* key,
                                 float fallback)
{
    if (!material.is_object() || !material.contains(key) ||
        !material[key].is_array() || material[key].size() < 4U ||
        !material[key][3].is_number()) {
        return fallback;
    }
    const double numericValue = material[key][3].get<double>();
    return std::isfinite(numericValue) ? static_cast<float>(numericValue)
                                       : fallback;
}

int materialIntegerProperty(const json& material, const char* key, int fallback)
{
    if (!material.is_object() || !material.contains(key)) {
        return fallback;
    }
    if (material[key].is_number_integer()) {
        return material[key].get<int>();
    }
    if (material[key].is_string()) {
        const std::string mode = material[key].get<std::string>();
        if (mode == "multiply") {
            return 1;
        }
        if (mode == "add") {
            return 2;
        }
        if (mode == "subtexture") {
            return 3;
        }
    }
    return fallback;
}

std::string materialTexturePath(const json& material,
                                const char* key,
                                const std::filesystem::path& modelDirectory)
{
    if (!material.is_object() || !material.contains(key) ||
        !material[key].is_string()) {
        return {};
    }

    const std::string raw = material[key].get<std::string>();
    if (raw.empty()) {
        return {};
    }

    try {
        const std::filesystem::path path = std::filesystem::u8path(raw);
        const std::filesystem::path resolved =
            path.is_absolute() ? path : modelDirectory / path;
        return resolved.lexically_normal().u8string();
    } catch (const std::filesystem::filesystem_error&) {
        return {};
    }
}

int materialSharedToonIndex(const json& material)
{
    if (!material.is_object() || !material.contains("sharedToonIndex") ||
        !material["sharedToonIndex"].is_number_integer()) {
        return -1;
    }
    const int index = material["sharedToonIndex"].get<int>();
    return index >= 0 && index <= 9 ? index : -1;
}

bool materialDoubleSided(const json& material)
{
    if (!material.is_object() || !material.contains("flags") ||
        !material["flags"].is_object()) {
        return false;
    }
    return material["flags"].value("doubleSided", false);
}

bool materialEdgeDrawing(const json& material)
{
    if (!material.is_object() || !material.contains("flags") ||
        !material["flags"].is_object()) {
        return false;
    }
    return material["flags"].value("edge", false);
}

void populateNativeMaterial(const json& material,
                            const std::filesystem::path& modelDirectory,
                            mmd::MmdRenderQueueInput& input)
{
    input.diffuseColor = materialDiffuseColor(material);
    input.diffuseAlpha = materialDiffuseAlpha(material);
    input.specularColor = materialColorProperty(
        material, "specular", {0.0F, 0.0F, 0.0F});
    input.specularPower = materialScalarProperty(material, "specularPower", 0.0F);
    input.ambientColor = materialColorProperty(
        material, "ambient", {0.3F, 0.3F, 0.3F});
    input.edgeColor = materialColorProperty(
        material, "edgeColor", {0.0F, 0.0F, 0.0F});
    input.edgeAlpha = materialColorAlphaProperty(material, "edgeColor", 1.0F);
    input.edgeSize = materialScalarProperty(material, "edgeSize", 0.0F);
    input.edgeDrawing = materialEdgeDrawing(material);
    input.sphereMode = materialIntegerProperty(material, "sphereMode", 0);
    input.mainTexturePath =
        materialTexturePath(material, "texturePath", modelDirectory);
    input.sphereTexturePath =
        materialTexturePath(material, "sphereTexturePath", modelDirectory);
    input.toonTexturePath =
        materialTexturePath(material, "toonTexturePath", modelDirectory);
    input.sharedToonIndex = materialSharedToonIndex(material);
    input.doubleSided = materialDoubleSided(material);
}

std::filesystem::path nativeModelDirectory(const std::string& modelPath)
{
    try {
        return std::filesystem::u8path(modelPath).parent_path();
    } catch (const std::filesystem::filesystem_error&) {
        return {};
    }
}

std::string quoteMelName(const std::string& name)
{
    return "\"" + name + "\"";
}

/**
 * Read a binary file into a byte vector.  Returns empty on failure.
 */
std::vector<uint8_t> readBinaryFile(const std::string& path)
{
    std::ifstream ifs(path, std::ios::binary | std::ios::ate);
    if (!ifs) {
        return {};
    }
    std::streamsize size = ifs.tellg();
    if (size <= 0) {
        return {};
    }
    ifs.seekg(0, std::ios::beg);

    std::vector<uint8_t> buf(static_cast<size_t>(size));
    if (!ifs.read(reinterpret_cast<char*>(buf.data()), size)) {
        return {};
    }
    return buf;
}

// --- Byte-buffer adapters (own the buffer; free before returning) ---------

std::vector<float> bufferToFloatsAndFree(mmd_runtime_ffi_byte_buffer_t buffer)
{
    std::vector<float> out;
    if (buffer.data && buffer.len >= sizeof(float)) {
        const size_t count = buffer.len / sizeof(float);
        out.resize(count);
        std::memcpy(out.data(), buffer.data, count * sizeof(float));
    }
    mmd_runtime_byte_buffer_free(buffer);
    return out;
}

std::vector<uint32_t> bufferToU32AndFree(mmd_runtime_ffi_byte_buffer_t buffer)
{
    std::vector<uint32_t> out;
    if (buffer.data && buffer.len >= sizeof(uint32_t)) {
        const size_t count = buffer.len / sizeof(uint32_t);
        out.resize(count);
        std::memcpy(out.data(), buffer.data, count * sizeof(uint32_t));
    }
    mmd_runtime_byte_buffer_free(buffer);
    return out;
}

json parseJsonBufferAndFree(mmd_runtime_ffi_byte_buffer_t buffer)
{
    json result;
    if (buffer.data && buffer.len > 0) {
        const char* begin = reinterpret_cast<const char*>(buffer.data);
        result = json::parse(begin, begin + buffer.len, nullptr, /*allow_exceptions=*/false);
    }
    mmd_runtime_byte_buffer_free(buffer);
    return result;
}

// --- Mesh construction ----------------------------------------------------

struct BuiltMesh {
    bool         ok = false;
    MString      transformName;
    MString      meshName;
    MPointArray  points;           // Maya-space base points (for morph targets)
    MIntArray    polygonCounts;
    MIntArray    polygonConnects;
};

/**
 * Build a single Maya mesh from flat PMX geometry buffers.
 *   positions : flat f32, length = vertCount * 3   (PMX space)
 *   uvs       : flat f32, length = vertCount * 2   (may be empty)
 *   indices   : flat u32, triangle list            (PMX CCW winding)
 * The new transform is renamed to desiredTransformName (uniqued by Maya).
 */
BuiltMesh buildMesh(const std::vector<float>&    positions,
                    const std::vector<float>&    normals,
                    const std::vector<float>&    uvs,
                    const std::vector<uint32_t>& indices,
                    double                       scale,
                    const MString&               desiredTransformName)
{
    BuiltMesh result;

    const size_t vertCount  = positions.size() / 3;
    const size_t indexCount = indices.size();
    if (vertCount == 0 || indexCount == 0) {
        return result;
    }

    // ---- Points: (x, y, -z) * scale ----
    result.points.setLength(static_cast<unsigned int>(vertCount));
    for (size_t i = 0; i < vertCount; ++i) {
        result.points[static_cast<unsigned int>(i)] = MPoint(
            positions[i * 3]     * scale,
            positions[i * 3 + 1] * scale,
           -positions[i * 3 + 2] * scale);
    }

    // ---- Triangles with reversed winding (PMX CCW -> Maya CW) ----
    const unsigned int triCount = static_cast<unsigned int>(indexCount / 3);
    result.polygonCounts = MIntArray(triCount, 3);
    result.polygonConnects.setLength(static_cast<unsigned int>(triCount * 3));
    for (unsigned int t = 0; t < triCount; ++t) {
        const unsigned int base = t * 3;
        result.polygonConnects[base]     = static_cast<int>(indices[base + 2]);
        result.polygonConnects[base + 1] = static_cast<int>(indices[base + 1]);
        result.polygonConnects[base + 2] = static_cast<int>(indices[base]);
    }

    MFnMesh meshFn;
    MStatus status;
    MObject meshObj = meshFn.create(
        static_cast<int>(vertCount),
        static_cast<int>(triCount),
        result.points,
        result.polygonCounts,
        result.polygonConnects,
        MObject::kNullObj,
        &status);
    if (!status) {
        return result;
    }

    // ---- Authored normals: (x, y, z) -> (x, y, -z), no position scale ----
    // Invalid or missing entries are intentionally omitted so Maya keeps its
    // geometric fallback for those vertices without poisoning valid entries.
    MVectorArray authoredNormals;
    MIntArray    authoredVertexIds;
    const size_t normalCount = normals.size() / 3U;
    const size_t assignCount = std::min(vertCount, normalCount);
    for (size_t i = 0; i < assignCount; ++i) {
        const double x = static_cast<double>(normals[i * 3]);
        const double y = static_cast<double>(normals[i * 3 + 1]);
        const double z = static_cast<double>(normals[i * 3 + 2]);
        const double length = std::sqrt(x * x + y * y + z * z);
        if (!std::isfinite(x) || !std::isfinite(y) || !std::isfinite(z) ||
            !std::isfinite(length) || length <= 0.0) {
            continue;
        }
        authoredNormals.append(MVector(x / length, y / length, -z / length));
        authoredVertexIds.append(static_cast<int>(i));
    }
    if (authoredVertexIds.length() > 0) {
        meshFn.setVertexNormals(authoredNormals, authoredVertexIds, MSpace::kObject);
    }

    MDagPath dagPath;
    MDagPath::getAPathTo(meshObj, dagPath);
    MFnTransform transformFn(dagPath.transform());
    transformFn.setName(desiredTransformName);

    // ---- UVs (V-flip) ----
    if (uvs.size() >= vertCount * 2) {
        MString uvSetName("map1");
        // MFnMesh::create() already creates an empty map1 on a fresh mesh.
        // Recreating it makes Maya silently allocate map11, leaving the
        // hardware shader sampling the empty current map1 instead.
        MStringArray uvSetNames;
        MStatus uvStatus = meshFn.getUVSetNames(uvSetNames);
        if (!uvStatus) {
            return result;
        }
        bool hasUvSet = false;
        for (unsigned int i = 0; i < uvSetNames.length(); ++i) {
            if (uvSetNames[i] == uvSetName) {
                hasUvSet = true;
                break;
            }
        }
        if (!hasUvSet && !meshFn.createUVSet(uvSetName)) {
            return result;
        }
        meshFn.setCurrentUVSetName(uvSetName);

        MFloatArray uArr(static_cast<unsigned int>(vertCount));
        MFloatArray vArr(static_cast<unsigned int>(vertCount));
        for (size_t i = 0; i < vertCount; ++i) {
            uArr[static_cast<unsigned int>(i)] = uvs[i * 2];
            vArr[static_cast<unsigned int>(i)] = 1.0f - uvs[i * 2 + 1];
        }
        meshFn.setUVs(uArr, vArr, &uvSetName);

        MIntArray uvCounts(triCount, 3);
        MIntArray uvConnects;
        uvConnects.setLength(static_cast<unsigned int>(triCount * 3));
        for (unsigned int t = 0; t < triCount; ++t) {
            const unsigned int base = t * 3;
            uvConnects[base]     = static_cast<int>(indices[base + 2]);
            uvConnects[base + 1] = static_cast<int>(indices[base + 1]);
            uvConnects[base + 2] = static_cast<int>(indices[base]);
        }
        meshFn.assignUVs(uvCounts, uvConnects, &uvSetName);
    }

    result.ok            = true;
    result.transformName = transformFn.name();
    result.meshName      = meshFn.name();
    return result;
}

/**
 * Create vertex-morph blendShape targets on baseTransformName from the parsed
 * morph list (non-geometry JSON `morphs` array).
 *
 * globalToLocal: when non-null, a morph offset's PMX `vertexIndex` is looked up
 * in this map and remapped to a local mesh vertex (material-split case). Offsets
 * not present in the map are ignored, so each submesh only gets the morphs that
 * actually move its vertices. When null, vertexIndex is used directly.
 */
unsigned int buildVertexMorphBlendShapes(
    const json&                                       morphs,
    const MString&                                    baseTransformName,
    const MPointArray&                                basePoints,
    const MIntArray&                                  polygonCounts,
    const MIntArray&                                  polygonConnects,
    double                                            scale,
    const std::unordered_map<uint32_t, uint32_t>*     globalToLocal)
{
    if (!morphs.is_array() || morphs.empty()) {
        return 0;
    }

    const std::string baseName(baseTransformName.asChar());
    std::set<std::string> usedNames;

    std::string blendShapeNode;   // created lazily on first attached target
    unsigned int created = 0;

    for (const json& morph : morphs) {
        if (!morph.is_object()) {
            continue;
        }
        if (morph.value("type", std::string()) != "vertex") {
            continue;
        }
        auto offsetsIt = morph.find("vertexOffsets");
        if (offsetsIt == morph.end() || !offsetsIt->is_array() || offsetsIt->empty()) {
            continue;
        }

        // Apply offsets onto a copy of the base points.
        MPointArray targetPoints(basePoints);
        bool touched = false;
        for (const json& off : *offsetsIt) {
            if (!off.is_object()) {
                continue;
            }
            const uint32_t pmxVertex = off.value("vertexIndex", 0u);
            uint32_t localVertex = pmxVertex;
            if (globalToLocal) {
                auto it = globalToLocal->find(pmxVertex);
                if (it == globalToLocal->end()) {
                    continue;
                }
                localVertex = it->second;
            }
            if (localVertex >= targetPoints.length()) {
                continue;
            }
            auto posIt = off.find("position");
            if (posIt == off.end() || !posIt->is_array() || posIt->size() < 3) {
                continue;
            }
            const double dx = (*posIt)[0].get<double>();
            const double dy = (*posIt)[1].get<double>();
            const double dz = (*posIt)[2].get<double>();
            targetPoints[localVertex].x += dx * scale;
            targetPoints[localVertex].y += dy * scale;
            targetPoints[localVertex].z += -dz * scale;
            touched = true;
        }
        if (!touched) {
            continue;   // morph does not affect this mesh
        }

        // Lazily create the blendShape deformer on the first real target.
        if (blendShapeNode.empty()) {
            const std::string blendShapeName =
                uniqueName(baseName + "_vertexMorphs", usedNames);
            MStringArray cmdResult;
            MStatus status = MGlobal::executeCommand(
                MString(("blendShape -name " + quoteMelName(blendShapeName) + " " +
                         quoteMelName(baseTransformName)).c_str()),
                cmdResult, false, false);
            if (!status || cmdResult.length() == 0) {
                MGlobal::displayWarning(
                    "[mmdFastLoad] Failed to create vertex morph blendShape.");
                return created;
            }
            blendShapeNode = cmdResult[0].asChar();
        }

        std::string rawName = morph.value("name", std::string());
        if (rawName.empty()) {
            rawName = "morph_" + std::to_string(created);
        }
        const std::string morphName = uniqueName(rawName, usedNames);
        const std::string targetTransformName =
            uniqueName(baseName + "_" + morphName + "_target", usedNames);

        MFnMesh  targetMeshFn;
        MStatus  createStatus;
        MObject  targetMeshObj = targetMeshFn.create(
            static_cast<int>(targetPoints.length()),
            static_cast<int>(polygonCounts.length()),
            targetPoints,
            polygonCounts,
            polygonConnects,
            MObject::kNullObj,
            &createStatus);
        if (!createStatus) {
            MGlobal::displayWarning(
                MString("[mmdFastLoad] Failed to create morph target: ") +
                morphName.c_str());
            continue;
        }

        MDagPath targetDag;
        MDagPath::getAPathTo(targetMeshObj, targetDag);
        MFnTransform targetTransformFn(targetDag.transform());
        targetTransformFn.setName(MString(targetTransformName.c_str()));

        const std::string targetName(targetTransformFn.name().asChar());
        MGlobal::executeCommand(
            MString(("setAttr " + quoteMelName(targetName + ".visibility") + " 0").c_str()),
            false, false);
        MGlobal::executeCommand(
            MString(("parent " + quoteMelName(targetName) + " " +
                     quoteMelName(baseTransformName)).c_str()),
            false, false);

        const std::string editCmd =
            "blendShape -edit -target " + quoteMelName(baseTransformName) + " " +
            std::to_string(created) + " " + quoteMelName(targetName) + " 1.0 " +
            quoteMelName(blendShapeNode);
        MStatus status = MGlobal::executeCommand(MString(editCmd.c_str()), false, false);
        if (!status) {
            MGlobal::displayWarning(
                MString("[mmdFastLoad] Failed to attach morph target: ") +
                morphName.c_str());
            continue;
        }

        MGlobal::executeCommand(
            MString(("aliasAttr " + quoteMelName(morphName) + " " +
                     quoteMelName(blendShapeNode + ".w[" + std::to_string(created) + "]"))
                        .c_str()),
            false, false);
        ++created;
    }

    return created;
}

} // anonymous namespace

// -----------------------------------------------------------------------
// doIt / redoIt / undoIt
// -----------------------------------------------------------------------

MStatus MmdFastLoad::doIt(const MArgList& args)
{
    if (!parseArgs(args)) {
        return MS::kFailure;
    }

    // Default base name from filename (strip extension)
    if (baseName_.empty()) {
        size_t lastSep = filePath_.find_last_of("/\\");
        std::string fname = (lastSep == std::string::npos)
                                ? filePath_
                                : filePath_.substr(lastSep + 1);
        size_t dot = fname.rfind('.');
        baseName_ = (dot == std::string::npos) ? fname : fname.substr(0, dot);
    }

    return redoIt();
}

MStatus MmdFastLoad::redoIt()
{
    // Clear any stale undo state
    transformName_.clear();
    meshName_.clear();
    createdRoots_.clear();

    const std::string safeName = sanitizeName(baseName_);

    // ---- Read PMX file ----
    std::vector<uint8_t> pmxBytes = readBinaryFile(filePath_);
    if (pmxBytes.empty()) {
        MGlobal::displayError(
            MString("[mmdFastLoad] Could not read file: ") + filePath_.c_str());
        return MS::kFailure;
    }
    const uint8_t* data = pmxBytes.data();
    const size_t   len  = pmxBytes.size();

    if (enableVp2Ownership_) {
        return loadVp2Ownership(safeName, data, len);
    }
    return enableSplit_ ? loadSplit(safeName, data, len)
                        : loadSingle(safeName, data, len);
}

MStatus MmdFastLoad::loadSingle(const std::string& safeName,
                                const uint8_t* data, size_t len)
{
    // ---- Geometry from typed buffers ----
    std::vector<float>    positions = bufferToFloatsAndFree(
        mmd_runtime_parse_pmx_positions_buffer(data, len));
    std::vector<float>    normals = bufferToFloatsAndFree(
        mmd_runtime_parse_pmx_normals_buffer(data, len));
    std::vector<float>    uvs = bufferToFloatsAndFree(
        mmd_runtime_parse_pmx_uvs_buffer(data, len));
    std::vector<uint32_t> indices = bufferToU32AndFree(
        mmd_runtime_parse_pmx_indices_buffer(data, len));

    if (positions.empty() || indices.empty()) {
        MGlobal::displayError(
            "[mmdFastLoad] PMX parse returned no geometry.\n"
            "  Ensure the mmd-anim FFI library is available and the PMX is valid.");
        return MS::kFailure;
    }

    BuiltMesh mesh = buildMesh(positions, normals, uvs, indices, scale_,
                               MString((safeName + "_fast").c_str()));
    if (!mesh.ok) {
        MGlobal::displayError("[mmdFastLoad] MFnMesh::create failed.");
        return MS::kFailure;
    }

    // ---- Vertex morphs (non-geometry JSON) ----
    if (enableMorphs_) {
        json nonGeo = parseJsonBufferAndFree(
            mmd_runtime_parse_pmx_non_geometry_json(data, len));
        if (nonGeo.is_object() && nonGeo.contains("morphs")) {
            const unsigned int created = buildVertexMorphBlendShapes(
                nonGeo["morphs"], mesh.transformName, mesh.points,
                mesh.polygonCounts, mesh.polygonConnects, scale_, nullptr);
            if (created > 0) {
                MGlobal::displayInfo(
                    MString("[mmdFastLoad] Created vertex morph targets: ") +
                    std::to_string(created).c_str());
            }
        }
    }

    transformName_ = mesh.transformName;
    meshName_      = mesh.meshName;
    createdRoots_.append(mesh.transformName);

    MStringArray result;
    result.append(transformName_);
    result.append(meshName_);
    setResult(result);

    MGlobal::displayInfo(
        MString("[mmdFastLoad] Created mesh: ") + transformName_ +
        " (" + meshName_ + ")");
    return MS::kSuccess;
}

MStatus MmdFastLoad::loadSplit(const std::string& safeName,
                               const uint8_t* data, size_t len)
{
    mmd_runtime_pmx_material_split_t* split =
        mmd_runtime_pmx_material_split_create(data, len, /*flags=*/0u);
    if (!split) {
        MGlobal::displayError(
            "[mmdFastLoad] mmd_runtime_pmx_material_split_create returned NULL.");
        return MS::kFailure;
    }

    const size_t meshCount = mmd_runtime_pmx_material_split_mesh_count(split);
    if (meshCount == 0) {
        MGlobal::displayError("[mmdFastLoad] Material split produced no meshes.");
        mmd_runtime_pmx_material_split_free(split);
        return MS::kFailure;
    }

    // Manifest (per-mesh material index + original vertex indices).
    json manifest = parseJsonBufferAndFree(
        mmd_runtime_pmx_material_split_manifest_json(split));

    // Non-geometry JSON: material names (always) + morphs (if requested).
    json nonGeo = parseJsonBufferAndFree(
        mmd_runtime_parse_pmx_non_geometry_json(data, len));
    const json* materials = (nonGeo.is_object() && nonGeo.contains("materials") &&
                             nonGeo["materials"].is_array())
                                ? &nonGeo["materials"] : nullptr;
    const json* morphs = (enableMorphs_ && nonGeo.is_object() &&
                          nonGeo.contains("morphs") && nonGeo["morphs"].is_array())
                             ? &nonGeo["morphs"] : nullptr;
    const json* manifestMeshes = (manifest.is_object() && manifest.contains("meshes") &&
                                  manifest["meshes"].is_array())
                                     ? &manifest["meshes"] : nullptr;

    // The material-split ABI exposes one submesh per material.  Build the
    // native ordering contract before creating Maya nodes so the future VP2
    // render-item owner can consume the same pass/material order.  Creation
    // order alone is not claimed as a VP2 draw-order guarantee.
    std::vector<mmd::MmdRenderQueueInput> queueInputs;
    queueInputs.reserve(meshCount);
    for (size_t i = 0; i < meshCount; ++i) {
        size_t originalMaterialIndex = i;
        if (manifestMeshes && i < manifestMeshes->size()) {
            originalMaterialIndex =
                (*manifestMeshes)[i].value("originalMaterialIndex", i);
        }

        mmd::MmdRenderQueueInput input;
        input.materialIndex = originalMaterialIndex;
        input.submeshIndex = i;
        if (materials && originalMaterialIndex < materials->size()) {
            const json& material = (*materials)[originalMaterialIndex];
            input.transparencyMode = materialTransparencyMode(material);
            input.diffuseAlpha = materialDiffuseAlpha(material);
        }
        queueInputs.push_back(std::move(input));
    }
    const std::vector<mmd::MmdRenderQueueEntry> renderQueue =
        mmd::buildMmdRenderQueue(queueInputs);

    // ---- Root group transform ----
    MStringArray groupResult;
    MStatus status = MGlobal::executeCommand(
        MString(("group -empty -name " + quoteMelName(safeName + "_fast")).c_str()),
        groupResult, false, false);
    if (!status || groupResult.length() == 0) {
        MGlobal::displayError("[mmdFastLoad] Failed to create split group.");
        mmd_runtime_pmx_material_split_free(split);
        return MS::kFailure;
    }
    const MString groupName = groupResult[0];

    std::set<std::string> usedNames;
    usedNames.insert(groupName.asChar());
    unsigned int totalMorphs = 0;

    for (const mmd::MmdRenderQueueEntry& queueEntry : renderQueue) {
        const size_t i = queueEntry.submeshIndex;
        std::vector<float>    positions = bufferToFloatsAndFree(
            mmd_runtime_pmx_material_split_positions_buffer(split, i));
        std::vector<float>    normals = bufferToFloatsAndFree(
            mmd_runtime_pmx_material_split_normals_buffer(split, i));
        std::vector<float>    uvs = bufferToFloatsAndFree(
            mmd_runtime_pmx_material_split_uvs_buffer(split, i));
        std::vector<uint32_t> indices = bufferToU32AndFree(
            mmd_runtime_pmx_material_split_indices_buffer(split, i));
        if (positions.empty() || indices.empty()) {
            continue;
        }

        // Resolve a friendly material name for this submesh.
        std::string matName = "material_" + std::to_string(i);
        size_t originalMaterialIndex = i;
        if (manifestMeshes && i < manifestMeshes->size()) {
            originalMaterialIndex =
                (*manifestMeshes)[i].value("originalMaterialIndex", i);
        }
        if (materials && originalMaterialIndex < materials->size()) {
            const std::string n =
                (*materials)[originalMaterialIndex].value("name", std::string());
            if (!n.empty()) {
                matName = n;
            }
        }
        const std::string meshNodeName =
            uniqueName(safeName + "_" + matName, usedNames);

        BuiltMesh mesh = buildMesh(positions, normals, uvs, indices, scale_,
                                   MString(meshNodeName.c_str()));
        if (!mesh.ok) {
            MGlobal::displayWarning(
                MString("[mmdFastLoad] Failed to build submesh: ") +
                meshNodeName.c_str());
            continue;
        }

        MGlobal::executeCommand(
            MString(("parent " + quoteMelName(mesh.transformName) + " " +
                     quoteMelName(groupName)).c_str()),
            false, false);

        // Per-submesh vertex morphs: remap global PMX vertex -> local index.
        if (morphs && manifestMeshes && i < manifestMeshes->size()) {
            const json& mm = (*manifestMeshes)[i];
            auto ovIt = mm.find("originalVertexIndices");
            if (ovIt != mm.end() && ovIt->is_array()) {
                std::unordered_map<uint32_t, uint32_t> globalToLocal;
                globalToLocal.reserve(ovIt->size());
                uint32_t local = 0;
                for (const json& g : *ovIt) {
                    globalToLocal.emplace(g.get<uint32_t>(), local);
                    ++local;
                }
                totalMorphs += buildVertexMorphBlendShapes(
                    *morphs, mesh.transformName, mesh.points,
                    mesh.polygonCounts, mesh.polygonConnects, scale_,
                    &globalToLocal);
            }
        }
    }

    mmd_runtime_pmx_material_split_free(split);

    transformName_ = groupName;
    meshName_.clear();
    createdRoots_.append(groupName);

    MStringArray result;
    result.append(groupName);
    setResult(result);

    MGlobal::displayInfo(
        MString("[mmdFastLoad] Created material-split group: ") + groupName +
        " (" + std::to_string(meshCount).c_str() + " meshes" +
        (totalMorphs > 0
             ? MString(", ") + std::to_string(totalMorphs).c_str() + " morph targets"
             : MString("")) +
        ")");
    return MS::kSuccess;
}

MStatus MmdFastLoad::loadVp2Ownership(const std::string& safeName,
                                      const uint8_t* data,
                                      size_t len)
{
    // This path intentionally uses the same material-split ABI and queue
    // classification as -split, but hands ownership to one opt-in custom DAG
    // shape.  The ordinary MFnMesh path is not changed by this flag.
    mmd_runtime_pmx_material_split_t* split =
        mmd_runtime_pmx_material_split_create(data, len, /*flags=*/0u);
    if (!split) {
        MGlobal::displayError(
            "[mmdFastLoad] VP2 ownership split creation failed.");
        return MS::kFailure;
    }

    const size_t meshCount = mmd_runtime_pmx_material_split_mesh_count(split);
    if (meshCount == 0U) {
        MGlobal::displayError(
            "[mmdFastLoad] VP2 ownership split produced no meshes.");
        mmd_runtime_pmx_material_split_free(split);
        return MS::kFailure;
    }

    json manifest = parseJsonBufferAndFree(
        mmd_runtime_pmx_material_split_manifest_json(split));
    json nonGeo = parseJsonBufferAndFree(
        mmd_runtime_parse_pmx_non_geometry_json(data, len));
    const json* materials = (nonGeo.is_object() && nonGeo.contains("materials") &&
                             nonGeo["materials"].is_array())
                                ? &nonGeo["materials"]
                                : nullptr;
    const json* manifestMeshes =
        (manifest.is_object() && manifest.contains("meshes") &&
         manifest["meshes"].is_array())
                                     ? &manifest["meshes"]
                                     : nullptr;
    const std::filesystem::path modelDirectory = nativeModelDirectory(filePath_);

    std::vector<mmd::MmdRenderQueueInput> queueInputs;
    queueInputs.reserve(meshCount);
    for (size_t i = 0; i < meshCount; ++i) {
        size_t originalMaterialIndex = i;
        if (manifestMeshes && i < manifestMeshes->size()) {
            originalMaterialIndex =
                (*manifestMeshes)[i].value("originalMaterialIndex", i);
        }

        mmd::MmdRenderQueueInput input;
        input.materialIndex = originalMaterialIndex;
        input.submeshIndex = i;
        if (materials && originalMaterialIndex < materials->size()) {
            const json& material = (*materials)[originalMaterialIndex];
            input.transparencyMode = materialTransparencyMode(material);
            populateNativeMaterial(material, modelDirectory, input);
        }
        queueInputs.push_back(std::move(input));
    }
    std::vector<std::vector<float>> submeshPositions(meshCount);
    std::vector<std::vector<float>> submeshNormals(meshCount);
    std::vector<std::vector<float>> submeshUvs(meshCount);
    std::vector<std::vector<uint32_t>> submeshIndices(meshCount);
    for (size_t i = 0; i < meshCount; ++i) {
        submeshPositions[i] = bufferToFloatsAndFree(
            mmd_runtime_pmx_material_split_positions_buffer(split, i));
        submeshNormals[i] = bufferToFloatsAndFree(
            mmd_runtime_pmx_material_split_normals_buffer(split, i));
        submeshUvs[i] = bufferToFloatsAndFree(
            mmd_runtime_pmx_material_split_uvs_buffer(split, i));
        submeshIndices[i] = bufferToU32AndFree(
            mmd_runtime_pmx_material_split_indices_buffer(split, i));
        if (submeshPositions[i].empty() || submeshIndices[i].empty()) {
            MGlobal::displayError(
                MString("[mmdFastLoad] VP2 ownership submesh has no geometry: ") +
                std::to_string(i).c_str());
            mmd_runtime_pmx_material_split_free(split);
            return MS::kFailure;
        }
    }
    mmd_runtime_pmx_material_split_free(split);

    MStatus status;
    MFnDagNode rootFn;
    MObject parent = MObject::kNullObj;
    const MString shapeName((safeName + "_vp2").c_str());
    // With a null parent, MFnDagNode::create returns the automatically
    // created transform for this surface shape.  Resolve the custom shape
    // child explicitly instead of treating that transform as the user node.
    MObject rootObject = rootFn.create(
        MmdRenderShape::id, shapeName, parent, &status);
    if (!status || rootObject.isNull()) {
        MGlobal::displayError(
            "[mmdFastLoad] Failed to create mmdRenderShape.");
        return MS::kFailure;
    }

    MObject shapeObject = MObject::kNullObj;
    const unsigned int childCount = rootFn.childCount(&status);
    if (status) {
        for (unsigned int childIndex = 0; childIndex < childCount;
             ++childIndex) {
            MStatus childStatus;
            const MObject child = rootFn.child(childIndex, &childStatus);
            if (!childStatus || child.isNull()) {
                continue;
            }
            MFnDependencyNode childFn(child, &childStatus);
            if (!childStatus) {
                continue;
            }
            MTypeId childType = childFn.typeId(&childStatus);
            if (childStatus && childType == MmdRenderShape::id) {
                shapeObject = child;
                break;
            }
        }
    }
    if (!status || shapeObject.isNull()) {
        MGlobal::displayError(
            "[mmdFastLoad] Created transform has no mmdRenderShape child.");
        MDagModifier cleanup;
        cleanup.deleteNode(rootObject);
        cleanup.doIt();
        return MS::kFailure;
    }

    MmdRenderShape* shape = MmdRenderShape::fromMObject(shapeObject, &status);
    if (!status) {
        MGlobal::displayError(
            "[mmdFastLoad] mmdRenderShape user-node lookup failed.");
        MDagModifier cleanup;
        cleanup.deleteNode(rootObject);
        cleanup.doIt();
        return MS::kFailure;
    }
    if (!shape) {
        MGlobal::displayError(
            "[mmdFastLoad] mmdRenderShape user-node is null.");
        MDagModifier cleanup;
        cleanup.deleteNode(rootObject);
        cleanup.doIt();
        return MS::kFailure;
    }
    if (!shape->setMaterialSplitGeometry(
            submeshPositions, submeshNormals, submeshUvs, submeshIndices,
            queueInputs, scale_)) {
        MGlobal::displayError(
            "[mmdFastLoad] VP2 ownership geometry rejected by mmdRenderShape.");
        MDagModifier cleanup;
        cleanup.deleteNode(rootObject);
        cleanup.doIt();
        return MS::kFailure;
    }

    MString rootName = rootFn.fullPathName(&status);
    MFnDagNode shapeFn(shapeObject, &status);
    if (!status) {
        MDagModifier cleanup;
        cleanup.deleteNode(rootObject);
        cleanup.doIt();
        return status;
    }
    const MString shapePath = shapeFn.fullPathName(&status);
    if (!status) {
        MDagModifier cleanup;
        cleanup.deleteNode(rootObject);
        cleanup.doIt();
        return status;
    }

    transformName_ = rootName;
    meshName_ = shapePath;
    createdRoots_.append(rootName);

    MStringArray result;
    result.append(transformName_);
    result.append(meshName_);
    setResult(result);

    MGlobal::displayInfo(
        MString("[mmdFastLoad] Created opt-in VP2 ownership shape: ") +
        meshName_ + " (queue entries=" +
        std::to_string(queueInputs.size()).c_str() + ")");
    return MS::kSuccess;
}

MStatus MmdFastLoad::undoIt()
{
    if (createdRoots_.length() == 0) {
        return MS::kSuccess;  // nothing to undo
    }

    MDagModifier dagMod;
    for (unsigned int i = 0; i < createdRoots_.length(); ++i) {
        MSelectionList sel;
        if (!sel.add(createdRoots_[i]) || sel.length() == 0) {
            continue;
        }
        MObject node;
        MStatus status = sel.getDependNode(0, node);
        if (!status || !node.hasFn(MFn::kDagNode)) {
            continue;
        }
        status = dagMod.deleteNode(node);
        if (!status) {
            return status;
        }
    }
    MStatus status = dagMod.doIt();
    if (!status) {
        return status;
    }

    transformName_.clear();
    meshName_.clear();
    createdRoots_.clear();
    return MS::kSuccess;
}

bool MmdFastLoad::isUndoable() const
{
    return true;
}
