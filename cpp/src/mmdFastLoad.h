/**
 * mmdFastLoad.h
 *
 * MPxCommand: mmdFastLoad
 *
 * Fast PMX mesh loading using the mmd-anim-ffi parsed-model ABI.
 *
 * Flags:
 *   -file/-f  <path>   Required. Path to .pmx file.
 *   -name/-n  <string> Optional. Base name for created transform/mesh.
 *                      Default: derived from filename.
 *   -scale/-s <double> Optional. Uniform scale factor (default 1.0).
 *
 * Creates one Maya transform + mesh from PMX geometry (positions, indices, UVs).
 * Coordinate conversion: x→x, y→y, z→-z (MMD → Maya), V-flip on UV.
 * Polygon winding is reversed (PMX → Maya).
 *
 * Returns [transformName, meshName] as string array result.
 * Supports undo by deleting the created transform node.
 * Depends on mmd-anim-ffi DLL (mmd_runtime.h) for parsed-model ABI.
 */

#pragma once

#include <maya/MPxCommand.h>
#include <maya/MSyntax.h>
#include <maya/MArgList.h>
#include <maya/MString.h>
#include <maya/MStringArray.h>

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

    // Parsed from command flags
    std::string filePath_;
    std::string baseName_;
    double scale_ = 1.0;

    // Created node names (for undo)
    MString transformName_;
    MString meshName_;
};
