/**
 * mmdFastLoad.h
 *
 * MPxCommand: mmdFastLoad
 *
 * Fast PMX mesh loading using the mmd-anim-ffi typed-buffer ABI
 * (mmd_runtime_parse_pmx_* / mmd_runtime_pmx_material_split_*).
 *
 * Flags:
 *   -file/-f  <path>   Required. Path to .pmx file.
 *   -name/-n  <string> Optional. Base name for created transform/mesh.
 *                      Default: derived from filename.
 *   -scale/-s <double> Optional. Uniform scale factor (default 1.0).
 *   -morphs/-mo <bool> Optional. Create vertex morph blendShape targets.
 *   -split/-sp <bool>  Optional. Split into one mesh per material
 *                      (mmd_runtime_pmx_material_split_* ABI) grouped under a
 *                      single transform. Default: false (single merged mesh).
 *   -vp2Ownership/-vo <bool> Optional. Use the opt-in custom mmdRenderShape
 *                              and its MPxGeometryOverride instead of regular
 *                              MFnMesh nodes. This is a native draw witness,
 *                              not a visual parity claim.
 *
 * Geometry is read from typed byte buffers (positions/uvs/indices); vertex
 * morphs are read from the non-geometry JSON (morphs[].vertexOffsets[]).
 * Coordinate conversion: x→x, y→y, z→-z (MMD → Maya), V-flip on UV.
 * Polygon winding is reversed (PMX → Maya).
 *
 * Returns [transformName, meshName] (single mesh), [groupName] (split), or
 * [transformName, sourceMeshName, renderShapeName] (VP2 ownership) as a string
 * array result. Supports undo by deleting the created root node(s).
 * Depends on mmd-anim-ffi (mmd_runtime.h) for the PMX parse ABI.
 */

#pragma once

#include <maya/MPxCommand.h>
#include <maya/MSyntax.h>
#include <maya/MArgList.h>
#include <maya/MString.h>
#include <maya/MStringArray.h>

#include <cstddef>
#include <cstdint>
#include <string>

class MmdFastLoad : public MPxCommand {
public:
    MmdFastLoad();
    ~MmdFastLoad() override;

    MStatus doIt(const MArgList& args) override;
    MStatus redoIt() override;
    MStatus undoIt() override;
    bool isUndoable() const override;

    static void* creator();
    static MSyntax newSyntax();

private:
    bool parseArgs(const MArgList& args);

    // Build a single merged mesh from the whole PMX.
    MStatus loadSingle(const std::string& safeName, const uint8_t* data, size_t len);
    // Build one mesh per material, grouped under a single transform.
    MStatus loadSplit(const std::string& safeName, const uint8_t* data, size_t len);
    // Build the opt-in custom DAG shape from material-split geometry.
    MStatus loadVp2Ownership(const std::string& safeName,
                             const uint8_t* data,
                             size_t len);

    // Parsed from command flags
    std::string filePath_;
    std::string baseName_;
    double scale_ = 1.0;
    bool enableMorphs_ = false;
    bool enableSplit_ = false;
    // Explicit opt-in: custom DAG shape plus MPxGeometryOverride ownership.
    bool enableVp2Ownership_ = false;

    // Created node names (for undo / result)
    MString transformName_;
    MString meshName_;
    // Top-level root transform(s) to delete on undo (covers split groups too).
    MStringArray createdRoots_;
};
