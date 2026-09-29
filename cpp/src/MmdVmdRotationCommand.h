#pragma once

#include <maya/MPxCommand.h>
#include <maya/MSyntax.h>

// Pure numerical conversion of one bone's authored quaternion keys.
class MmdVmdRotationCommand final : public MPxCommand {
public:
    static void* creator();
    static MSyntax newSyntax();
    MStatus doIt(const MArgList& args) override;
};
