#include "MmdVmdBatchSamplerCommand.h"

#include <maya/MAngle.h>
#include <maya/MAnimControl.h>
#include <maya/MArgDatabase.h>
#include <maya/MComputation.h>
#include <maya/MDistance.h>
#include <maya/MDGContext.h>
#include <maya/MDGContextGuard.h>
#include <maya/MDoubleArray.h>
#include <maya/MFnAnimCurve.h>
#include <maya/MFnAttribute.h>
#include <maya/MFnDependencyNode.h>
#include <maya/MFnNumericAttribute.h>
#include <maya/MFnUnitAttribute.h>
#include <maya/MGlobal.h>
#include <maya/MPlug.h>
#include <maya/MPlugArray.h>
#include <maya/MSelectionList.h>
#include <maya/MString.h>

#include <cmath>
#include <cstdint>
#include <limits>
#include <memory>
#include <string>
#include <unordered_set>
#include <vector>

#include "third_party/json.hpp"

namespace {
using json = nlohmann::json;

constexpr int kProtocolVersion = 1;
constexpr std::size_t kHeaderSize = 6U;
constexpr std::size_t kMaxSamples = 4'194'304U;
constexpr double kMaxFrame = 1.0e9;
constexpr const char* kCommand = "mmdVmdBatchSample";
constexpr const char* kPayloadFlag = "-payload";

enum class UnitKind { Angle, Distance, Scalar };
enum class Strategy { DirectCurve, Static, TimedMPlug };
enum class EvaluationMode { Context, TimelineProbe };

struct Channel {
    MPlug plug;
    MPlug directOutput;
    std::string canonicalPlug;
    UnitKind unit = UnitKind::Scalar;
    Strategy strategy = Strategy::TimedMPlug;
    double staticValue = 0.0;
};

struct Request {
    std::vector<double> frames;
    std::vector<Channel> channels;
    EvaluationMode evaluationMode = EvaluationMode::Context;
};

class ComputationGuard final {
public:
    ComputationGuard() { computation_.beginComputation(false, true, false); }
    ~ComputationGuard() { computation_.endComputation(); }
    bool interrupted() { return computation_.isInterruptRequested(); }

private:
    MComputation computation_;
};

class CurrentTimeGuard final {
public:
    CurrentTimeGuard() : entryTime_(MAnimControl::currentTime()) {}

    MStatus restore()
    {
        if (restored_) return restoreStatus_;
        restoreStatus_ = MAnimControl::setCurrentTime(entryTime_);
        restored_ = true;
        return restoreStatus_;
    }

    ~CurrentTimeGuard()
    {
        if (!restored_) restore();
    }

private:
    MTime entryTime_;
    bool restored_ = false;
    MStatus restoreStatus_ = MS::kSuccess;
};

std::string utf8(const MString& value)
{
    return value.asUTF8();
}

MString fromUtf8(const std::string& value)
{
    MString result;
    result.setUTF8(value.c_str());
    return result;
}

MStatus fail(const std::string& message)
{
    MGlobal::displayError((std::string("[") + kCommand + "] " + message).c_str());
    return MS::kFailure;
}

bool finiteNumber(const json& value, double& result)
{
    if (!value.is_number() || value.is_boolean()) return false;
    try {
        result = value.get<double>();
    } catch (...) {
        return false;
    }
    return std::isfinite(result);
}

bool exactVersion(const json& value)
{
    if (value.is_boolean() || !value.is_number_integer()) return false;
    try {
        return value.get<std::int64_t>() == kProtocolVersion;
    } catch (...) {
        return false;
    }
}

bool hasIncoming(const MPlug& plug)
{
    MStatus status;
    MPlugArray sources;
    const bool connected = plug.connectedTo(sources, true, false, &status);
    return status && connected && sources.length() != 0U;
}

bool hasParentIncoming(const MPlug& plug)
{
    MStatus status;
    const MPlug parent = plug.parent(&status);
    return status && !parent.isNull() && parent.isCompound() && hasIncoming(parent);
}

bool readNumeric(const MPlug& plug, UnitKind unit, double& value)
{
    MStatus status;
    if (unit == UnitKind::Angle) {
        MFnUnitAttribute attribute(plug.attribute(), &status);
        if (status && attribute.unitType(&status) == MFnUnitAttribute::kAngle) {
            value = plug.asMAngle(&status).asDegrees();
            return status && std::isfinite(value);
        }
    } else if (unit == UnitKind::Distance) {
        MFnUnitAttribute attribute(plug.attribute(), &status);
        if (status && attribute.unitType(&status) == MFnUnitAttribute::kDistance) {
            value = plug.asMDistance(&status).asUnits(MDistance::uiUnit());
            return status && std::isfinite(value);
        }
    }

    // Logical MMD routes can terminate at a unitless physical plug while the
    // route declaration still carries angle/distance semantics.  In that
    // case Maya's raw numeric value is already the route's UI value; applying
    // an angle/cm conversion here would change the authored semantics.
    MFnNumericAttribute numeric(plug.attribute(), &status);
    if (!status || numeric.unitType(&status) == MFnNumericData::kInvalid) return false;
    value = plug.asDouble(&status);
    return status && std::isfinite(value);
}

bool sourceOutputUnit(const MPlug& output, UnitKind declaredUnit, double raw, double& value)
{
    MStatus status;
    MFnUnitAttribute attribute(output.attribute(), &status);
    if (status) {
        const auto type = attribute.unitType(&status);
        if (status && declaredUnit == UnitKind::Angle && type == MFnUnitAttribute::kAngle) {
            value = MAngle(raw, MAngle::kRadians).asDegrees();
            return std::isfinite(value);
        }
        if (status && declaredUnit == UnitKind::Distance && type == MFnUnitAttribute::kDistance) {
            value = MDistance(raw, MDistance::kCentimeters).asUnits(MDistance::uiUnit());
            return std::isfinite(value);
        }
    }
    value = raw;
    return std::isfinite(value);
}

bool timeInputCurveType(MFnAnimCurve::AnimCurveType type)
{
    return type == MFnAnimCurve::kAnimCurveTA || type == MFnAnimCurve::kAnimCurveTL ||
           type == MFnAnimCurve::kAnimCurveTT || type == MFnAnimCurve::kAnimCurveTU;
}

bool findDirectCurve(const MPlug& plug, MPlug& output)
{
    if (hasParentIncoming(plug)) return false;
    MStatus status;
    MPlugArray sources;
    const bool connected = plug.connectedTo(sources, true, false, &status);
    if (!status || !connected || sources.length() != 1U) return false;
    const MPlug source = sources[0];
    MFnAnimCurve curve(source.node(), &status);
    if (!status || !timeInputCurveType(curve.animCurveType(&status)) || !status) return false;
    output = curve.findPlug("output", false, &status);
    if (!status || output.isNull() || !(source.attribute() == output.attribute())) return false;
    // A direct route must have a time-input curve, not a driven animCurveU
    // hidden behind a unit conversion or other topology.
    return true;
}

bool safeNumericInput(const MPlug& plug)
{
    if (hasIncoming(plug) || hasParentIncoming(plug) || plug.isCompound()) return false;
    MStatus status;
    MFnAttribute attribute(plug.attribute(), &status);
    if (!status || !attribute.isWritable() || !attribute.isStorable()) return false;
    MFnUnitAttribute unit(plug.attribute(), &status);
    if (status) {
        const auto type = unit.unitType(&status);
        if (status && (type == MFnUnitAttribute::kAngle || type == MFnUnitAttribute::kDistance)) return true;
    }
    MFnNumericAttribute numeric(plug.attribute(), &status);
    if (!status) return false;
    const auto type = numeric.unitType(&status);
    return status && type != MFnNumericData::kInvalid && type != MFnNumericData::k3Double &&
           type != MFnNumericData::k3Float && type != MFnNumericData::k2Double &&
           type != MFnNumericData::k2Float;
}

bool canonicalizePlug(const std::string& requested, MPlug& plug, std::string& canonical)
{
    if (requested.empty()) return false;
    MSelectionList selection;
    MStatus status = selection.add(fromUtf8(requested));
    if (!status || selection.length() != 1U || !selection.getPlug(0, plug)) return false;
    canonical = utf8(plug.name(&status));
    return status && !canonical.empty() && !plug.isNull();
}

bool onlyKeys(const json& object, const std::unordered_set<std::string>& allowed)
{
    if (!object.is_object()) return false;
    for (auto it = object.begin(); it != object.end(); ++it) {
        if (allowed.find(it.key()) == allowed.end()) return false;
    }
    return true;
}

bool parseRequest(const MString& payloadString, Request& request, std::string& error)
{
    json payload;
    bool duplicateKey = false;
    std::vector<std::unordered_set<std::string>> objectKeys;
    const auto callback = [&duplicateKey, &objectKeys](int, json::parse_event_t event, json& parsed) {
        if (event == json::parse_event_t::object_start) {
            objectKeys.emplace_back();
        } else if (event == json::parse_event_t::key && !objectKeys.empty()) {
            duplicateKey = !objectKeys.back().insert(parsed.get<std::string>()).second || duplicateKey;
        } else if (event == json::parse_event_t::object_end && !objectKeys.empty()) {
            objectKeys.pop_back();
        }
        return true;
    };
    try {
        payload = json::parse(utf8(payloadString), callback);
    } catch (const std::exception& exception) {
        error = std::string("invalid JSON: ") + exception.what();
        return false;
    }
    if (duplicateKey) {
        error = "duplicate JSON object key";
        return false;
    }
    if (!payload.is_object() || (payload.size() != 3U && payload.size() != 4U) ||
        !onlyKeys(payload, {"version", "frames", "channels", "evaluation_mode"}) ||
        !payload.contains("version") || !exactVersion(payload["version"]) ||
        !payload.contains("frames") || !payload["frames"].is_array() || payload["frames"].empty() ||
        !payload.contains("channels") || !payload["channels"].is_array() || payload["channels"].empty()) {
        error = "payload requires version, frames, channels, and optional evaluation_mode (version=1)";
        return false;
    }
    if (payload.contains("evaluation_mode")) {
        if (!payload["evaluation_mode"].is_string() ||
            payload["evaluation_mode"].get<std::string>() != "timeline_probe") {
            error = "evaluation_mode, when present, must be timeline_probe";
            return false;
        }
        request.evaluationMode = EvaluationMode::TimelineProbe;
    }

    double previousFrame = 0.0;
    bool firstFrame = true;
    request.frames.reserve(payload["frames"].size());
    for (const json& frameValue : payload["frames"]) {
        double frame = 0.0;
        if (!finiteNumber(frameValue, frame) || std::fabs(frame) > kMaxFrame ||
            (!firstFrame && frame <= previousFrame)) {
            error = "frames must be finite, strictly increasing UI-frame values";
            return false;
        }
        request.frames.push_back(frame);
        previousFrame = frame;
        firstFrame = false;
    }

    std::unordered_set<std::string> canonicalPlugs;
    request.channels.reserve(payload["channels"].size());
    for (const json& channelValue : payload["channels"]) {
        if (!channelValue.is_object() || channelValue.size() != 3U ||
            !onlyKeys(channelValue, {"plug", "unit", "hint"}) ||
            !channelValue.contains("plug") || !channelValue["plug"].is_string() ||
            !channelValue.contains("unit") || !channelValue["unit"].is_string() ||
            !channelValue.contains("hint") || !channelValue["hint"].is_string()) {
            error = "each channel requires only canonical plug, unit, and hint";
            return false;
        }
        const std::string requestedPlug = channelValue["plug"].get<std::string>();
        const std::string unit = channelValue["unit"].get<std::string>();
        const std::string hint = channelValue["hint"].get<std::string>();
        UnitKind unitKind;
        if (unit == "angle") unitKind = UnitKind::Angle;
        else if (unit == "distance") unitKind = UnitKind::Distance;
        else if (unit == "scalar") unitKind = UnitKind::Scalar;
        else {
            error = "channel unit must be angle, distance, or scalar";
            return false;
        }
        if (hint != "direct_curve" && hint != "static" && hint != "timed_mplug") {
            error = "channel hint must be direct_curve, static, or timed_mplug";
            return false;
        }

        Channel channel;
        if (!canonicalizePlug(requestedPlug, channel.plug, channel.canonicalPlug)) {
            error = "channel plug is missing or ambiguous: " + requestedPlug;
            return false;
        }
        if (!canonicalPlugs.insert(channel.canonicalPlug).second) {
            error = "duplicate canonical channel plug: " + channel.canonicalPlug;
            return false;
        }
        channel.unit = unitKind;
        MPlug directOutput;
        const bool directEligible = findDirectCurve(channel.plug, directOutput);
        const bool staticEligible = safeNumericInput(channel.plug);
        if (hint == "direct_curve" && directEligible) {
            channel.strategy = Strategy::DirectCurve;
            channel.directOutput = directOutput;
        } else if (hint == "static" && staticEligible) {
            channel.strategy = Strategy::Static;
            if (!readNumeric(channel.plug, channel.unit, channel.staticValue)) {
                error = "static numeric plug could not be read: " + channel.canonicalPlug;
                return false;
            }
        } else {
            channel.strategy = Strategy::TimedMPlug;
        }
        request.channels.push_back(std::move(channel));
    }
    if (request.frames.size() > std::numeric_limits<std::size_t>::max() / request.channels.size() ||
        request.frames.size() * request.channels.size() > kMaxSamples) {
        error = "frame/channel sample count exceeds 4,194,304";
        return false;
    }
    return true;
}

bool directValue(const Channel& channel, const MFnAnimCurve& curve, double frame, double& value)
{
    MStatus status;
    const double raw = curve.evaluate(MTime(frame, MTime::uiUnit()), &status);
    if (!status || !std::isfinite(raw)) return false;
    return sourceOutputUnit(channel.directOutput, channel.unit, raw, value);
}

MStatus sample(const Request& request)
{
    MDoubleArray result;
    result.setLength(static_cast<unsigned int>(kHeaderSize));
    result[0] = static_cast<double>(kProtocolVersion);
    result[1] = static_cast<double>(request.frames.size());
    result[2] = static_cast<double>(request.channels.size());
    std::size_t directCount = 0U;
    std::size_t staticCount = 0U;
    std::size_t timedCount = 0U;
    for (const Channel& channel : request.channels) {
        if (channel.strategy == Strategy::DirectCurve) ++directCount;
        else if (channel.strategy == Strategy::Static) ++staticCount;
        else ++timedCount;
    }
    result[3] = static_cast<double>(directCount);
    result[4] = static_cast<double>(staticCount);
    result[5] = static_cast<double>(timedCount);
    result.setLength(static_cast<unsigned int>(kHeaderSize + request.frames.size() * request.channels.size()));

    // Bind each direct curve once per command.  Constructing an MFnAnimCurve
    // inside the frame loop would retain the intended semantics but would
    // re-enter the dependency-node function-set machinery for every sample.
    std::vector<std::unique_ptr<MFnAnimCurve>> directCurves(request.channels.size());
    for (std::size_t channelIndex = 0; channelIndex < request.channels.size(); ++channelIndex) {
        const Channel& channel = request.channels[channelIndex];
        if (channel.strategy != Strategy::DirectCurve) continue;
        MStatus status;
        directCurves[channelIndex] = std::make_unique<MFnAnimCurve>(channel.directOutput.node(), &status);
        if (!status || !directCurves[channelIndex]) {
            return fail("direct animation curve became unavailable: " + channel.canonicalPlug);
        }
    }

    const bool timelineProbe = request.evaluationMode == EvaluationMode::TimelineProbe;
    if (timelineProbe && MAnimControl::isPlaying()) {
        return fail("timeline_probe is unavailable during playback");
    }
    std::unique_ptr<CurrentTimeGuard> currentTimeGuard;
    std::unique_ptr<ComputationGuard> computationGuard;
    if (timelineProbe) {
        currentTimeGuard = std::make_unique<CurrentTimeGuard>();
        computationGuard = std::make_unique<ComputationGuard>();
    }
    std::string sampleError;
    std::size_t offset = kHeaderSize;
    for (double frame : request.frames) {
        // One guard is deliberately shared by every timed fallback channel
        // for this frame.  Direct curves and static values do not require a
        // DG context and therefore do not perturb Maya's current time.
        const bool hasTimed = timedCount != 0U;
        std::unique_ptr<MDGContextGuard> guard;
        if (timelineProbe) {
            const MStatus status = MAnimControl::setCurrentTime(MTime(frame, MTime::uiUnit()));
            if (!status) {
                sampleError = "timeline_probe could not set frame " + std::to_string(frame);
                break;
            }
        } else if (hasTimed) {
            guard = std::make_unique<MDGContextGuard>(MDGContext(MTime(frame, MTime::uiUnit())));
        }
        for (std::size_t channelIndex = 0; channelIndex < request.channels.size(); ++channelIndex) {
            const Channel& channel = request.channels[channelIndex];
            double value = 0.0;
            bool ok = false;
            if (channel.strategy == Strategy::DirectCurve) {
                ok = directCurves[channelIndex] &&
                     directValue(channel, *directCurves[channelIndex], frame, value);
            }
            else if (channel.strategy == Strategy::Static) {
                value = channel.staticValue;
                ok = true;
            } else ok = readNumeric(channel.plug, channel.unit, value);
            if (!ok || !std::isfinite(value)) {
                sampleError = "sampling failed for channel " + channel.canonicalPlug +
                              " at frame " + std::to_string(frame);
                break;
            }
            result[static_cast<unsigned int>(offset++)] = value;
        }
        if (!sampleError.empty()) break;
        if (computationGuard && computationGuard->interrupted()) {
            sampleError = "sampling cancelled";
            break;
        }
    }
    if (currentTimeGuard && !currentTimeGuard->restore()) {
        return fail("current time restoration failed");
    }
    if (!sampleError.empty()) {
        return fail(sampleError);
    }
    MPxCommand::setResult(result);
    return MS::kSuccess;
}
}  // namespace

void* MmdVmdBatchSamplerCommand::creator()
{
    return new MmdVmdBatchSamplerCommand();
}

MSyntax MmdVmdBatchSamplerCommand::newSyntax()
{
    MSyntax syntax;
    syntax.addFlag("-p", kPayloadFlag, MSyntax::kString);
    syntax.enableEdit(false);
    syntax.enableQuery(false);
    return syntax;
}

MStatus MmdVmdBatchSamplerCommand::doIt(const MArgList& args)
{
    MStatus status;
    MArgDatabase database(newSyntax(), args, &status);
    if (!status || !database.isFlagSet(kPayloadFlag)) return fail("-payload is required");
    MString payload;
    status = database.getFlagArgument(kPayloadFlag, 0, payload);
    if (!status) return fail("-payload must be one JSON string");
    Request request;
    std::string error;
    if (!parseRequest(payload, request, error)) return fail(error);
    return sample(request);
}

bool MmdVmdBatchSamplerCommand::isUndoable() const
{
    return false;
}
