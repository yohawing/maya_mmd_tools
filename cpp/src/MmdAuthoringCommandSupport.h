#pragma once

#include <maya/MArgList.h>
#include <maya/MObjectHandle.h>
#include <maya/MPxCommand.h>
#include <maya/MPlug.h>
#include <maya/MSyntax.h>

#include <string>
#include <vector>

/**
 * Narrow undoable mutation witness for the native Authoring command contract.
 *
 * This command deliberately owns only four test attributes.  Feature commands
 * must define their own read/write sets instead of turning this into an
 * arbitrary scene transaction surface.
 */
class MmdAuthoringSetAttrsCommand : public MPxCommand {
public:
    static void* creator();
    static MSyntax newSyntax();

    MStatus doIt(const MArgList& args) override;
    MStatus redoIt() override;
    MStatus undoIt() override;
    bool isUndoable() const override;

public:
    enum class ValueType { Bool, Int, Double, String };

    struct Value {
        ValueType type = ValueType::Bool;
        bool boolValue = false;
        int intValue = 0;
        double doubleValue = 0.0;
        std::string stringValue;
    };

private:
    struct Mutation {
        MObjectHandle node;
        MPlug plug;
        std::string canonicalPlug;
        Value before;
        Value after;
    };

    MStatus apply(bool useAfter);
    MStatus finishError(const char* phase, const std::string& code, const std::string& message);
    MStatus finishSuccess(const char* phase);

    std::vector<Mutation> mutations_;
    bool prepared_ = false;
    bool initialExecution_ = false;
};
