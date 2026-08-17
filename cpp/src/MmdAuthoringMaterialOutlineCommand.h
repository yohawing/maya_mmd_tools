#pragma once

#include <maya/MArgList.h>
#include <maya/MObjectHandle.h>
#include <maya/MPxCommand.h>
#include <maya/MPlug.h>
#include <maya/MSyntax.h>

#include <array>
#include <string>
#include <variant>
#include <vector>

/** Atomically apply a Python-owned Material value plus DX11 outline preview. */
class MmdAuthoringSetMaterialOutlineCommand : public MPxCommand {
public:
    static void* creator();
    static MSyntax newSyntax();

    MStatus doIt(const MArgList& args) override;
    MStatus redoIt() override;
    MStatus undoIt() override;
    bool isUndoable() const override;

    using Value = std::variant<std::string, bool, int, double, std::array<double, 3>>;
    enum class Storage { String, Bool, Int, Float, Double, Float3, Double3 };

    struct Mutation {
        MObjectHandle node;
        MPlug plug;
        std::string field;
        std::string canonicalPlug;
        Storage storage = Storage::Double;
        Value before = 0.0;
        Value after = 0.0;
    };

private:
    MStatus apply(bool useAfter);
    MStatus finishError(const char* phase, const std::string& code, const std::string& message);
    MStatus finishSuccess(const char* phase);

    std::vector<Mutation> mutations_;
    bool prepared_ = false;
    bool initialExecution_ = false;
};
