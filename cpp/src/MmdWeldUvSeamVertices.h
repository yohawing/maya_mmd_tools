/**
 * MmdWeldUvSeamVertices.h
 *
 * MPxCommand used by the Python importer to collapse PMX vertices that were
 * duplicated only to carry different primary UV coordinates.  The command
 * rebuilds the mesh before skinCluster and blendShape creation, so UVs stay
 * attached per face corner while Maya receives one geometric vertex.
 */

#pragma once

#include <maya/MArgList.h>
#include <maya/MPxCommand.h>
#include <maya/MSyntax.h>

class MmdWeldUvSeamVertices : public MPxCommand {
public:
    MmdWeldUvSeamVertices() = default;
    ~MmdWeldUvSeamVertices() override = default;

    MStatus doIt(const MArgList& args) override;
    bool isUndoable() const override;

    static void* creator();
    static MSyntax newSyntax();
};
