#pragma once

#include <maya/MArgList.h>
#include <maya/MObjectHandle.h>
#include <maya/MPxCommand.h>
#include <maya/MPlug.h>
#include <maya/MSyntax.h>

#include <string>
#include <vector>

/** Atomically write an already-discovered set of Authoring morph weights. */
class MmdAuthoringSetMorphWeightsCommand : public MPxCommand {
public:
    static void* creator();
    static MSyntax newSyntax();

    MStatus doIt(const MArgList& args) override;
    MStatus redoIt() override;
    MStatus undoIt() override;
    bool isUndoable() const override;

private:
    struct Mutation {
        MObjectHandle node;
        MPlug plug;
        std::string canonicalPlug;
        bool floatStorage = false;
        double before = 0.0;
        double after = 0.0;
    };

    MStatus apply(bool useAfter);
    MStatus finishError(const char* phase, const std::string& code, const std::string& message);
    MStatus finishSuccess(const char* phase);

    std::vector<Mutation> mutations_;
    bool prepared_ = false;
    bool initialExecution_ = false;
};
