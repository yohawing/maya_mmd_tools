#pragma once

#include <maya/MAnimCurveChange.h>
#include <maya/MPxCommand.h>
#include <maya/MSyntax.h>

// Replace identity time keys in a TT curve as one undoable operation.
class MmdVmdTimeCurveCommand final : public MPxCommand {
public:
    static void* creator();
    static MSyntax newSyntax();
    MStatus doIt(const MArgList& args) override;
    MStatus undoIt() override;
    MStatus redoIt() override;
    bool isUndoable() const override { return applied_; }

private:
    MAnimCurveChange changes_;
    bool applied_ = false;
};
