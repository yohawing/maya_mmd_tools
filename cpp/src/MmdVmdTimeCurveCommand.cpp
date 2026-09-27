#include "MmdVmdTimeCurveCommand.h"

#include <maya/MArgDatabase.h>
#include <maya/MFnAnimCurve.h>
#include <maya/MGlobal.h>
#include <maya/MSelectionList.h>
#include <maya/MTime.h>

#include <cmath>
#include <exception>
#include <vector>

#include "third_party/json.hpp"

void* MmdVmdTimeCurveCommand::creator() { return new MmdVmdTimeCurveCommand(); }

MSyntax MmdVmdTimeCurveCommand::newSyntax()
{
    MSyntax syntax;
    syntax.addFlag("-p", "-payload", MSyntax::kString);
    syntax.enableEdit(false);
    syntax.enableQuery(false);
    return syntax;
}

MStatus MmdVmdTimeCurveCommand::doIt(const MArgList& args)
{
    MStatus status;
    MArgDatabase arguments(newSyntax(), args, &status);
    if (!status) return status;
    MString payload;
    status = arguments.getFlagArgument("-payload", 0, payload);
    if (!status) return status;

    // Validate every input before editing. TT addKeys is unsupported by Maya;
    // addKey with time-valued input/output must retain an explicit undo cache.
    MObject node;
    std::vector<double> times;
    try {
        const auto request = nlohmann::json::parse(payload.asUTF8());
        const auto curve = request.at("curve").get<std::string>();
        times = request.at("times").get<std::vector<double>>();
        for (std::size_t i = 0; i < times.size(); ++i) {
            if (!std::isfinite(times[i]) || (i && times[i] < times[i - 1])) {
                MGlobal::displayError("VMD time curve requires finite, ordered times.");
                return MS::kFailure;
            }
        }
        MSelectionList selection;
        MString name;
        name.setUTF8(curve.c_str());
        status = selection.add(name);
        if (!status) return status;
        status = selection.getDependNode(0, node);
        if (!status) return status;
    } catch (const std::exception& error) {
        MGlobal::displayError(MString("Invalid VMD time curve payload: ") + error.what());
        return MS::kFailure;
    }
    MFnAnimCurve curve(node, &status);
    if (!status) return status;
    if (curve.animCurveType() != MFnAnimCurve::kAnimCurveTT) {
        MGlobal::displayError("VMD time curve key insertion requires an animCurveTT.");
        return MS::kFailure;
    }
    changes_.setInteractive(false);
    for (unsigned int count = curve.numKeys(); count > 0; --count) {
        status = curve.remove(count - 1, &changes_);
        if (!status) {
            changes_.undoIt();
            return status;
        }
    }
    const auto unit = MTime::uiUnit();
    for (std::size_t i = 0; i < times.size(); ++i) {
        if (i && times[i] == times[i - 1]) continue;
        const MTime time(times[i], unit);
        curve.addKey(time, time, MFnAnimCurve::kTangentGlobal,
                     MFnAnimCurve::kTangentGlobal, &changes_, &status);
        if (!status) {
            status.perror("VMD TT addKey");
            changes_.undoIt();
            return status;
        }
    }
    applied_ = true;
    return MS::kSuccess;
}

MStatus MmdVmdTimeCurveCommand::undoIt() { return changes_.undoIt(); }
MStatus MmdVmdTimeCurveCommand::redoIt() { return changes_.redoIt(); }
