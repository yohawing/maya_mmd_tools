#pragma once

#include <maya/MArgList.h>
#include <maya/MPxCommand.h>
#include <maya/MSyntax.h>

/**
 * Destructively removes every key from animation curves attached to a set of
 * canonical plugs.
 *
 * The command intentionally has no Maya undo contract.  The caller must
 * treat a mutation-phase failure as fatal because already removed keys are not
 * restored.
 */
class MmdVmdClearCurvesCommand final : public MPxCommand {
public:
    static void* creator();
    static MSyntax newSyntax();

    MStatus doIt(const MArgList& args) override;
    bool isUndoable() const override;
};
