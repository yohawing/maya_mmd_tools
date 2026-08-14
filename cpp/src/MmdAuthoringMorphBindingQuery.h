#pragma once

#include <maya/MArgList.h>
#include <maya/MPxCommand.h>
#include <maya/MSyntax.h>

/** Read the Maya observations required by the Python morph binding policy. */
class MmdAuthoringMorphBindingQueryCommand : public MPxCommand {
public:
    static void* creator();
    static MSyntax newSyntax();
    MStatus doIt(const MArgList& args) override;
    bool isUndoable() const override { return false; }
};
