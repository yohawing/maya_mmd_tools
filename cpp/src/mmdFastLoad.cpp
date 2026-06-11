/**
 * mmdFastLoad.cpp
 *
 * Implementation of mmdFastLoad command.
 *
 * Reads a PMX file, uses mmd-anim-ffi parsed-model ABI to extract
 * vertex positions, UVs, and indices, then creates a Maya mesh with
 * coordinate conversions matching the Python importer:
 *   - Position: (x, y, -z) * scale
 *   - UV: V flipped (1.0 - v)
 *   - Winding: reversed (PMX CCW → Maya CW)
 */

#include "mmdFastLoad.h"

#include <maya/MArgDatabase.h>
#include <maya/MDGModifier.h>
#include <maya/MDagPath.h>
#include <maya/MFloatArray.h>
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

#include <algorithm>
#include <cctype>
#include <fstream>
#include <set>
#include <string>
#include <vector>

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

    syntax.enableEdit(false);

    return syntax;
}

// -----------------------------------------------------------------------
// Argument parsing
// -----------------------------------------------------------------------

bool MmdFastLoad::parseArgs(const MArgList& args)
{
    MArgDatabase argData(newSyntax(), args);
    MStatus      status;

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

std::string byteBufferToStringAndFree(mmd_runtime_ffi_byte_buffer_t buffer)
{
    std::string out;
    if (buffer.data && buffer.len > 0) {
        out.assign(reinterpret_cast<const char*>(buffer.data), buffer.len);
    }
    mmd_runtime_byte_buffer_free(buffer);
    return out;
}

unsigned int createVertexMorphBlendShapes(
    mmd_runtime_parsed_model_t* model,
    const MString& baseTransformName,
    const MPointArray& basePoints,
    const MIntArray& polygonCounts,
    const MIntArray& polygonConnects,
    double scale)
{
    const size_t morphCount = mmd_runtime_parsed_model_vertex_morph_count(model);
    const size_t offsetCount = mmd_runtime_parsed_model_vertex_morph_offset_count(model);
    if (morphCount == 0 || offsetCount == 0) {
        return 0;
    }

    const uint32_t* spans = mmd_runtime_parsed_model_vertex_morph_spans(model);
    const uint32_t* vertexIndices =
        mmd_runtime_parsed_model_vertex_morph_vertex_indices(model);
    const float* offsets = mmd_runtime_parsed_model_vertex_morph_position_offsets(model);
    if (!spans || !vertexIndices || !offsets) {
        MGlobal::displayWarning("[mmdFastLoad] Vertex morph FFI accessors returned null.");
        return 0;
    }

    const std::string baseName(baseTransformName.asChar());
    const std::string blendShapeName = sanitizeName(baseName + "_vertexMorphs");
    MStringArray commandResult;
    MStatus status = MGlobal::executeCommand(
        MString(("blendShape -name " + quoteMelName(blendShapeName) + " " +
                 quoteMelName(baseTransformName)).c_str()),
        commandResult,
        false,
        false);
    if (!status || commandResult.length() == 0) {
        MGlobal::displayWarning("[mmdFastLoad] Failed to create vertex morph blendShape.");
        return 0;
    }
    const std::string blendShapeNode(commandResult[0].asChar());

    std::set<std::string> usedNames;
    usedNames.insert(blendShapeName);
    unsigned int created = 0;

    for (size_t morphIndex = 0; morphIndex < morphCount; ++morphIndex) {
        const size_t spanBase = morphIndex * 3;
        const uint32_t start = spans[spanBase];
        const uint32_t count = spans[spanBase + 1];
        if (count == 0 || static_cast<size_t>(start) + count > offsetCount) {
            continue;
        }

        std::string rawName = byteBufferToStringAndFree(
            mmd_runtime_parsed_model_vertex_morph_name(model, morphIndex));
        if (rawName.empty()) {
            rawName = "morph_" + std::to_string(morphIndex);
        }
        const std::string morphName = uniqueName(rawName, usedNames);
        const std::string targetTransformName =
            uniqueName(baseName + "_" + morphName + "_target", usedNames);

        MPointArray targetPoints(basePoints);
        for (uint32_t i = 0; i < count; ++i) {
            const uint32_t offsetIndex = start + i;
            const uint32_t vertexIndex = vertexIndices[offsetIndex];
            if (vertexIndex >= targetPoints.length()) {
                continue;
            }
            targetPoints[vertexIndex].x += offsets[offsetIndex * 3] * scale;
            targetPoints[vertexIndex].y += offsets[offsetIndex * 3 + 1] * scale;
            targetPoints[vertexIndex].z += -offsets[offsetIndex * 3 + 2] * scale;
        }

        MFnMesh targetMeshFn;
        MStatus createStatus;
        MObject targetMeshObj = targetMeshFn.create(
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
            false,
            false);
        MGlobal::executeCommand(
            MString(("parent " + quoteMelName(targetName) + " " +
                     quoteMelName(baseTransformName)).c_str()),
            false,
            false);

        const std::string editCmd =
            "blendShape -edit -target " + quoteMelName(baseTransformName) + " " +
            std::to_string(created) + " " + quoteMelName(targetName) + " 1.0 " +
            quoteMelName(blendShapeNode);
        status = MGlobal::executeCommand(MString(editCmd.c_str()), false, false);
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
            false,
            false);
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

    // ---- Sanitize base name ----
    std::string safeName = sanitizeName(baseName_);

    // ---- Read PMX file ----
    std::vector<uint8_t> pmxBytes = readBinaryFile(filePath_);
    if (pmxBytes.empty()) {
        MGlobal::displayError(
            MString("[mmdFastLoad] Could not read file: ") + filePath_.c_str());
        return MS::kFailure;
    }

    // ---- Parse via mmd-anim-ffi parsed-model ABI ----
    mmd_runtime_parsed_model_t* model =
        mmd_runtime_parsed_model_create_from_pmx_bytes(pmxBytes.data(),
                                                        pmxBytes.size());
    if (!model) {
        MGlobal::displayError(
            "[mmdFastLoad] mmd_runtime_parsed_model_create_from_pmx_bytes "
            "returned NULL.\n"
            "  Ensure mmd_anim_ffi.dll is available and the PMX file is valid.");
        return MS::kFailure;
    }

    // ---- Extract geometry ----
    const size_t vertCount  = mmd_runtime_parsed_model_vertex_count(model);
    const size_t indexCount = mmd_runtime_parsed_model_index_count(model);

    if (vertCount == 0 || indexCount == 0) {
        MGlobal::displayError(
            "[mmdFastLoad] PMX model has no vertices or no indices.");
        mmd_runtime_parsed_model_free(model);
        return MS::kFailure;
    }

    const float*    posPtr   = mmd_runtime_parsed_model_positions(model);
    const float*    uvPtr    = mmd_runtime_parsed_model_uvs(model);
    const uint32_t* idxPtr   = mmd_runtime_parsed_model_indices(model);

    if (!posPtr || !idxPtr) {
        MGlobal::displayError(
            "[mmdFastLoad] FFI returned null geometry pointers.");
        mmd_runtime_parsed_model_free(model);
        return MS::kFailure;
    }

    // ---- Build MPointArray with Maya coordinate conversion ----
    //   MMD:   (x, y, z)
    //   Maya:  (x, y, -z)   * scale
    MPointArray mayaPoints;
    mayaPoints.setLength(static_cast<unsigned int>(vertCount));
    for (size_t i = 0; i < vertCount; ++i) {
        mayaPoints[static_cast<unsigned int>(i)] = MPoint(
            posPtr[i * 3]     * static_cast<float>(scale_),
            posPtr[i * 3 + 1] * static_cast<float>(scale_),
           -posPtr[i * 3 + 2] * static_cast<float>(scale_));
    }

    // ---- Build triangle polygon counts / connects (reversed winding) ----
    //   PMX winding (CCW)  →  Maya winding (CW)  = reverse order
    const unsigned int triCount = static_cast<unsigned int>(indexCount / 3);

    MIntArray polygonCounts(triCount, 3);  // all triangles

    MIntArray polygonConnects;
    polygonConnects.setLength(static_cast<unsigned int>(indexCount));
    for (unsigned int t = 0; t < triCount; ++t) {
        const unsigned int base = t * 3;
        polygonConnects[base]     = static_cast<int>(idxPtr[base + 2]);
        polygonConnects[base + 1] = static_cast<int>(idxPtr[base + 1]);
        polygonConnects[base + 2] = static_cast<int>(idxPtr[base]);
    }

    // ---- Create mesh ----
    MFnMesh meshFn;
    MStatus status;
    MObject meshObj = meshFn.create(
        static_cast<int>(vertCount),
        static_cast<int>(triCount),
        mayaPoints,
        polygonCounts,
        polygonConnects,
        MObject::kNullObj,
        &status);

    if (!status) {
        MGlobal::displayError("[mmdFastLoad] MFnMesh::create failed.");
        mmd_runtime_parsed_model_free(model);
        return MS::kFailure;
    }

    // ---- Get transform and name it ----
    MDagPath dagPath;
    MDagPath::getAPathTo(meshObj, dagPath);
    MObject  transformObj = dagPath.transform();
    MFnTransform transformFn(transformObj);

    MString tName(safeName.c_str());
    tName += "_fast";
    transformFn.setName(tName);

    // ---- Set UV set (V-flip matching Python importer) ----
    if (uvPtr) {
        MString uvSetName("map1");
        meshFn.createUVSet(uvSetName);

        MFloatArray uArr(static_cast<unsigned int>(vertCount));
        MFloatArray vArr(static_cast<unsigned int>(vertCount));
        for (size_t i = 0; i < vertCount; ++i) {
            uArr[static_cast<unsigned int>(i)] = uvPtr[i * 2];
            vArr[static_cast<unsigned int>(i)] = 1.0f - uvPtr[i * 2 + 1];
        }

        meshFn.setUVs(uArr, vArr, &uvSetName);

        MIntArray uvCounts(triCount, 3);
        MIntArray uvConnects;
        uvConnects.setLength(static_cast<unsigned int>(indexCount));
        for (unsigned int t = 0; t < triCount; ++t) {
            const unsigned int base = t * 3;
            uvConnects[base]     = static_cast<int>(idxPtr[base + 2]);
            uvConnects[base + 1] = static_cast<int>(idxPtr[base + 1]);
            uvConnects[base + 2] = static_cast<int>(idxPtr[base]);
        }

        meshFn.assignUVs(uvCounts, uvConnects, &uvSetName);
    }

    // ---- Free parsed model (no longer needed) ----
    if (enableMorphs_) {
        const unsigned int morphTargets = createVertexMorphBlendShapes(
            model,
            transformFn.name(),
            mayaPoints,
            polygonCounts,
            polygonConnects,
            scale_);
        if (morphTargets > 0) {
            MGlobal::displayInfo(
                MString("[mmdFastLoad] Created vertex morph blendShape targets: ") +
                std::to_string(morphTargets).c_str());
        }
    }

    // ---- Free parsed model (no longer needed) ----
    mmd_runtime_parsed_model_free(model);

    // ---- Record created node names for undo ----
    transformName_ = transformFn.name();
    meshName_      = meshFn.name();

    // ---- Return [transformName, meshName] as result ----
    MStringArray result;
    result.append(transformName_);
    result.append(meshName_);
    setResult(result);

    MGlobal::displayInfo(
        MString("[mmdFastLoad] Created mesh: ") + transformName_ +
        " (" + meshName_ + ")");

    return MS::kSuccess;
}

MStatus MmdFastLoad::undoIt()
{
    if (transformName_.length() == 0) {
        return MS::kSuccess;  // nothing to undo
    }

    MSelectionList sel;
    sel.add(transformName_);
    if (sel.length() == 0) {
        MGlobal::displayWarning(
            MString("[mmdFastLoad] undo: transform not found: ") +
            transformName_);
        transformName_.clear();
        meshName_.clear();
        return MS::kSuccess;
    }

    MObject node;
    sel.getDependNode(0, node);

    MDGModifier dgMod;
    dgMod.deleteNode(node);
    dgMod.doIt();

    transformName_.clear();
    meshName_.clear();

    return MS::kSuccess;
}

bool MmdFastLoad::isUndoable() const
{
    return true;
}
