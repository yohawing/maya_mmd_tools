#include "MmdVmdTimeCurveCommand.h"

#include <maya/MArgDatabase.h>
#include <maya/MAngle.h>
#include <maya/MFnAnimCurve.h>
#include <maya/MGlobal.h>
#include <maya/MSelectionList.h>
#include <maya/MTime.h>

#include <algorithm>
#include <array>
#include <cmath>
#include <exception>
#include <vector>

#include "third_party/json.hpp"

void* MmdVmdTimeCurveCommand::creator() { return new MmdVmdTimeCurveCommand(); }

MSyntax MmdVmdTimeCurveCommand::newSyntax()
{
    MSyntax syntax;
    syntax.addFlag("-p", "-payload", MSyntax::kString);
    syntax.addFlag("-v", "-version");
    syntax.enableEdit(false);
    syntax.enableQuery(false);
    return syntax;
}

MStatus MmdVmdTimeCurveCommand::doIt(const MArgList& args)
{
    MStatus status;
    MArgDatabase arguments(newSyntax(), args, &status);
    if (!status) return status;
    if (arguments.isFlagSet("-version")) {
        setResult(2);
        return MS::kSuccess;
    }
    MString payload;
    status = arguments.getFlagArgument("-payload", 0, payload);
    if (!status) return status;

    // Validate every input before editing. TT addKeys is unsupported by Maya;
    // addKey with time-valued input/output must retain an explicit undo cache.
    MObject node;
    std::vector<double> times;
    std::vector<std::array<double, 8>> segments;
    bool authorTangents = false;
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
        authorTangents = request.contains("tangent_runs");
        if (authorTangents) {
            segments = request.at("tangent_runs").get<std::vector<std::array<double, 8>>>();
            for (const auto& segment : segments) {
                if (!std::all_of(segment.begin(), segment.end(), [](double v) { return std::isfinite(v); }) ||
                    segment[0] > segment[1] || segment[2] > segment[3] ||
                    segment[0] >= segment[2] || segment[1] >= segment[3] ||
                    !std::all_of(segment.begin(), segment.begin() + 4, [&times](double t) {
                        return std::binary_search(times.begin(), times.end(), t);
                    })) {
                    MGlobal::displayError("Invalid VMD time curve tangent segment.");
                    return MS::kFailure;
                }
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
    if (authorTangents) {
        status = curve.setIsWeighted(true, &changes_);
        if (!status) { changes_.undoIt(); return status; }
        for (const auto& segment : segments) {
            for (unsigned int side = 0; side < 2; ++side) {
                unsigned int first = 0, last = 0;
                if (!curve.find(MTime(segment[side * 2], unit), first, &status) || !status ||
                    !curve.find(MTime(segment[side * 2 + 1], unit), last, &status) || !status) {
                    changes_.undoIt();
                    return MS::kFailure;
                }
                const double dx = segment[4 + side * 2], dy = segment[5 + side * 2];
                for (unsigned int index = first; index <= last; ++index) {
                    status = curve.setTangentsLocked(index, false, &changes_);
                    if (status) status = curve.setWeightsLocked(index, false, &changes_);
                    if (status) status = curve.setTangent(index, MAngle(std::atan2(dy, dx)),
                        std::hypot(dx, dy), side == 1, &changes_, true);
                    if (!status) { changes_.undoIt(); return status; }
                }
            }
        }
    }
    applied_ = true;
    return MS::kSuccess;
}

MStatus MmdVmdTimeCurveCommand::undoIt() { return changes_.undoIt(); }
MStatus MmdVmdTimeCurveCommand::redoIt() { return changes_.redoIt(); }
