#include "MmdVmdBatchSamplerCommand.h"

#include <maya/MAngle.h>
#include <maya/MAnimControl.h>
#include <maya/MArgDatabase.h>
#include <maya/MComputation.h>
#include <maya/MDataHandle.h>
#include <maya/MDistance.h>
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

#include <chrono>
#include <array>
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <fstream>
#include <limits>
#include <memory>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <vector>

#include "third_party/json.hpp"

namespace {
using json = nlohmann::json;

constexpr int kProtocolVersion = 2;
constexpr std::size_t kHeaderSize = 6U;
constexpr std::size_t kTimingHeaderSizeV3 = 9U;
constexpr std::size_t kMaxSessionSamples = 134'217'728U;
constexpr std::size_t kMaxSpoolBytes = 2'147'483'648ULL;
constexpr std::size_t kMaxTraversalNodes = 4096U;
constexpr double kMaxFrame = 1.0e9;
constexpr const char* kCommand = "mmdVmdBatchSample";
constexpr const char* kPayloadFlag = "-payload";
constexpr const char* kEvaluationPolicy = "maya_timeline_bake_v1";
constexpr const char* kTimingProtocolV3 = "wall_v3";

enum class UnitKind { Angle, Distance, Scalar };
enum class Strategy { DirectCurve, Static, TimedMPlug };
// A direct-spool request is one bounded Prepare-scoped sampling session.  The
// command receives the full frame plan once and performs 120-frame internal
// checkpoints, so the canonicalized MPlugs live for the whole session without
// requiring a process-global registry.
struct Channel {
    MPlug plug;
    MPlug directOutput;
    std::string canonicalPlug;
    UnitKind unit = UnitKind::Scalar;
    Strategy strategy = Strategy::TimedMPlug;
    double staticValue = 0.0;
};

struct CompoundGroup {
    MPlug parent;
    std::array<std::size_t, 3U> channelIndices{};
};

struct Request {
    std::vector<double> frames;
    std::vector<Channel> channels;
    std::vector<CompoundGroup> compoundGroups;
    std::string spoolPath;
    std::size_t spoolBytes = 0U;
    std::size_t outputChannelCount = 0U;
    std::vector<std::size_t> outputSlots;
    std::vector<double> outputDefaults;
};


struct TimingTotals {
    double setCurrentTimeWallSec = 0.0;
    double firstTimedMPlugReadWallSec = 0.0;
    double channelLoopWallSec = 0.0;
};

struct DirectCheckpoint {
    TimingTotals timing;
    double wallSec = 0.0;
    std::size_t classifiedCompoundGroupCount = 0U;
    std::size_t classifiedCompoundCoveredChannelCount = 0U;
    std::size_t compoundSuccessGroupCount = 0U;
    std::size_t compoundSuccessCoveredChannelCount = 0U;
    std::size_t compoundFallbackGroupCount = 0U;
    std::size_t compoundFallbackCoveredChannelCount = 0U;
};

constexpr std::size_t kDirectAckVersion = 1U;
constexpr std::size_t kDirectCheckpointRecordSize = 10U;

using WallClock = std::chrono::steady_clock;

double elapsedSeconds(const WallClock::time_point start, const WallClock::time_point end)
{
    return std::chrono::duration<double>(end - start).count();
}

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

bool readNumeric(const MDataHandle& handle, const MPlug& plug, UnitKind unit, double& value)
{
    if (!handle.isNumeric()) return false;
    MStatus status;
    if (unit == UnitKind::Angle) {
        MFnUnitAttribute attribute(plug.attribute(), &status);
        if (status && attribute.unitType(&status) == MFnUnitAttribute::kAngle) {
            value = handle.asAngle().asDegrees();
            return std::isfinite(value);
        }
    } else if (unit == UnitKind::Distance) {
        MFnUnitAttribute attribute(plug.attribute(), &status);
        if (status && attribute.unitType(&status) == MFnUnitAttribute::kDistance) {
            value = handle.asDistance().asUnits(MDistance::uiUnit());
            return std::isfinite(value);
        }
    }

    // As with the scalar plug path, a declared angle/distance route can end
    // at a unitless child.  Its raw numeric value is already in the route's
    // declared UI units and must not be converted a second time.
    MFnNumericAttribute numeric(plug.attribute(), &status);
    if (!status || numeric.unitType(&status) == MFnNumericData::kInvalid) return false;
    value = handle.asDouble();
    return std::isfinite(value);
}

bool compoundChildIndex(const MPlug& plug, MPlug& parent, std::size_t& childIndex)
{
    MStatus status;
    parent = plug.parent(&status);
    if (!status || parent.isNull() || !parent.isCompound()) return false;
    const unsigned int childCount = parent.numChildren(&status);
    if (!status || childCount != 3U) return false;
    for (unsigned int index = 0U; index < childCount; ++index) {
        const MPlug child = parent.child(index, &status);
        if (!status || child.isNull()) return false;
        if (child == plug) {
            childIndex = static_cast<std::size_t>(index);
            return true;
        }
    }
    return false;
}

std::vector<CompoundGroup> classifyCompoundGroups(const std::vector<Channel>& channels)
{
    struct Candidate {
        MPlug parent;
        std::size_t channelIndex = 0U;
        std::size_t childIndex = 0U;
        UnitKind unit = UnitKind::Scalar;
    };
    std::unordered_map<std::string, std::vector<Candidate>> candidates;
    for (std::size_t channelIndex = 0U; channelIndex < channels.size(); ++channelIndex) {
        const Channel& channel = channels[channelIndex];
        if (channel.strategy != Strategy::TimedMPlug) continue;
        MPlug parent;
        std::size_t childIndex = 0U;
        if (!compoundChildIndex(channel.plug, parent, childIndex)) continue;
        MStatus status;
        const std::string parentName = utf8(parent.name(&status));
        if (!status || parentName.empty()) continue;
        candidates[parentName].push_back({parent, channelIndex, childIndex, channel.unit});
    }

    std::vector<CompoundGroup> groups;
    for (const auto& entry : candidates) {
        const std::vector<Candidate>& members = entry.second;
        if (members.size() != 3U) continue;
        std::array<std::size_t, 3U> channelIndices{};
        std::array<bool, 3U> covered{false, false, false};
        const UnitKind unit = members.front().unit;
        bool valid = true;
        for (const Candidate& member : members) {
            if (member.childIndex >= covered.size() || covered[member.childIndex] ||
                member.unit != unit) {
                valid = false;
                break;
            }
            covered[member.childIndex] = true;
            channelIndices[member.childIndex] = member.channelIndex;
        }
        if (!valid || !covered[0] || !covered[1] || !covered[2]) continue;
        CompoundGroup group;
        group.parent = members.front().parent;
        group.channelIndices = channelIndices;
        groups.push_back(std::move(group));
    }
    return groups;
}

bool readCompoundGroup(const CompoundGroup& group,
                       const std::vector<Channel>& channels,
                       std::array<double, 3U>& values)
{
    MStatus status;
    MDataHandle parentHandle = group.parent.asMDataHandle(&status);
    if (!status) return false;
    bool success = true;
    for (std::size_t childIndex = 0U; childIndex < group.channelIndices.size(); ++childIndex) {
        const Channel& channel = channels[group.channelIndices[childIndex]];
        MDataHandle childHandle = parentHandle.child(channel.plug);
        if (!readNumeric(childHandle, channel.plug, channel.unit, values[childIndex])) {
            success = false;
            break;
        }
    }
    group.parent.destructHandle(parentHandle);
    return success;
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

bool physicsNodeType(const MObject& node, std::string& type)
{
    MStatus status;
    MFnDependencyNode dependencyNode(node, &status);
    if (!status) return false;
    const MString typeName = dependencyNode.typeName(&status);
    if (!status || typeName.length() == 0U) return false;
    type = utf8(typeName);
    return !type.empty();
}

bool isPhysicsType(const std::string& type)
{
    std::string lower = type;
    for (char& character : lower) {
        if (character >= 'A' && character <= 'Z') character = static_cast<char>(character - 'A' + 'a');
    }
    return lower.find("physics") != std::string::npos ||
           lower.find("rigidbody") != std::string::npos ||
           lower.find("rigid_body") != std::string::npos;
}

bool collectPlugIncoming(const MPlug& plug, std::vector<MPlug>& sources, std::string& error)
{
    MStatus status;
    MPlugArray connected;
    const bool hasSources = plug.connectedTo(connected, true, false, &status);
    if (!status) {
        error = "could not inspect incoming connections for " + utf8(plug.name(&status));
        return false;
    }
    if (hasSources) {
        for (unsigned int index = 0U; index < connected.length(); ++index) {
            sources.push_back(connected[index]);
        }
    }
    if (!plug.isCompound()) return true;

    const unsigned int childCount = plug.numChildren(&status);
    if (!status) {
        error = "could not inspect compound children for " + utf8(plug.name(&status));
        return false;
    }
    for (unsigned int index = 0U; index < childCount; ++index) {
        const MPlug child = plug.child(index, &status);
        if (!status || child.isNull()) {
            error = "could not inspect compound child for " + utf8(plug.name(&status));
            return false;
        }
        if (!collectPlugIncoming(child, sources, error)) return false;
    }
    return true;
}

bool collectIncoming(const MPlug& plug, std::vector<MPlug>& sources, std::string& error)
{
    if (!collectPlugIncoming(plug, sources, error)) return false;

    MStatus status;
    const MPlug parent = plug.parent(&status);
    if (!status) {
        // Maya reports kFailure for parent() on a top-level plug in some
        // versions.  Use the plug's child state to distinguish that expected
        // no-parent case from an actual failed inspection of a compound child.
        MStatus childStatus;
        const bool isChild = plug.isChild(&childStatus);
        if (!childStatus) {
            error = "could not inspect child state for " + utf8(plug.name(&status));
            return false;
        }
        if (!isChild) return true;
        error = "could not inspect parent plug for " + utf8(plug.name(&status));
        return false;
    }
    if (!parent.isNull() && parent.isCompound()) {
        MPlugArray parentSources;
        const bool parentHasSources = parent.connectedTo(parentSources, true, false, &status);
        if (!status) {
            error = "could not inspect incoming parent connections for " + utf8(plug.name(&status));
            return false;
        }
        if (parentHasSources) {
            for (unsigned int index = 0U; index < parentSources.length(); ++index) {
                sources.push_back(parentSources[index]);
            }
        }
    }
    return true;
}

bool validateUpstream(const Channel& channel, std::string& error)
{
    std::string targetType;
    if (!physicsNodeType(channel.plug.node(), targetType)) {
        error = "could not resolve channel node type: " + channel.canonicalPlug;
        return false;
    }
    const std::string canonical = channel.canonicalPlug;
    const std::size_t separator = canonical.rfind('.');
    const std::string attribute = separator == std::string::npos ? std::string() : canonical.substr(separator + 1U);
    const bool prePhysicsInput = isPhysicsType(targetType) && attribute.rfind("inPre", 0U) == 0U;
    if (isPhysicsType(targetType) && !prePhysicsInput) {
        error = "sampled channel is a physics output: " + channel.canonicalPlug;
        return false;
    }

    std::vector<MPlug> queue;
    if (!collectIncoming(channel.plug, queue, error)) return false;
    std::unordered_set<std::string> visited;
    while (!queue.empty()) {
        if (visited.size() >= kMaxTraversalNodes) {
            error = "upstream dependency traversal exceeded the safety limit";
            return false;
        }
        const MPlug source = queue.back();
        queue.pop_back();
        std::string sourceType;
        if (!physicsNodeType(source.node(), sourceType)) {
            error = "could not resolve upstream node type for " + channel.canonicalPlug;
            return false;
        }
        MStatus sourceStatus;
        const MString sourcePlugName = source.name(&sourceStatus);
        if (!sourceStatus || sourcePlugName.length() == 0U) {
            error = "could not resolve upstream plug identity for " + channel.canonicalPlug;
            return false;
        }
        const std::string key = utf8(sourcePlugName);
        if (!visited.insert(key).second) continue;
        if (isPhysicsType(sourceType)) {
            error = "sampled channel has an upstream physics dependency: " + key;
            return false;
        }
        std::vector<MPlug> upstream;
        if (!collectIncoming(source, upstream, error)) return false;
        queue.insert(queue.end(), upstream.begin(), upstream.end());
    }
    return true;
}

bool exactSize(const json& value, std::size_t& result)
{
    if (value.is_boolean() || !value.is_number_integer()) return false;
    try {
        const std::uint64_t number = value.get<std::uint64_t>();
        if (number > std::numeric_limits<std::size_t>::max()) return false;
        result = static_cast<std::size_t>(number);
        return true;
    } catch (...) {
        return false;
    }
}

bool parseFrames(const json& values, std::vector<double>& frames, std::string& error)
{
    if (!values.is_array() || values.empty()) {
        error = "frames must be a non-empty array";
        return false;
    }
    double previousFrame = 0.0;
    bool firstFrame = true;
    frames.reserve(values.size());
    for (const json& frameValue : values) {
        double frame = 0.0;
        if (!finiteNumber(frameValue, frame) || std::fabs(frame) > kMaxFrame ||
            (!firstFrame && frame <= previousFrame)) {
            error = "frames must be finite, strictly increasing UI-frame values";
            return false;
        }
        frames.push_back(frame);
        previousFrame = frame;
        firstFrame = false;
    }
    return true;
}

bool parseChannels(const json& values, std::vector<Channel>& channels, std::string& error,
                   bool allowEmpty = false)
{
    if (!values.is_array() || (!allowEmpty && values.empty())) {
        error = allowEmpty ? "channels must be an array" : "channels must be a non-empty array";
        return false;
    }
    std::unordered_set<std::string> canonicalPlugs;
    channels.reserve(values.size());
    for (const json& channelValue : values) {
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
        if (!validateUpstream(channel, error)) return false;
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
        channels.push_back(std::move(channel));
    }
    return true;
}

bool parseRequest(const MString& payloadString, Request& request, std::string& error)
{
    json payload;
    bool duplicateKey = false;
    std::vector<std::unordered_set<std::string>> objectKeys;
    const auto callback = [&duplicateKey, &objectKeys](int, json::parse_event_t event, json& parsed) {
        if (event == json::parse_event_t::object_start) objectKeys.emplace_back();
        else if (event == json::parse_event_t::key && !objectKeys.empty()) {
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
    if (duplicateKey || !payload.is_object() || !payload.contains("version") ||
        !exactVersion(payload["version"]) || !payload.contains("evaluation_policy") ||
        !payload["evaluation_policy"].is_string() ||
        payload["evaluation_policy"].get<std::string>() != kEvaluationPolicy) {
        error = duplicateKey ? "duplicate JSON object key" :
                               "payload requires version=2 and evaluation_policy=maya_timeline_bake_v1";
        return false;
    }
    if (!payload.contains("mode") || !payload["mode"].is_string() ||
        payload["mode"].get<std::string>() != "direct_spool") {
        error = "unsupported native sampler request mode";
        return false;
    }

    if (!payload.contains("timing") || !payload["timing"].is_string() ||
        payload["timing"].get<std::string>() != kTimingProtocolV3) {
        error = "payload timing must be wall_v3";
        return false;
    }
    if (!onlyKeys(payload, {"version", "evaluation_policy", "timing", "mode",
                            "frames", "channels", "spool_path", "spool_bytes",
                            "output_channel_count", "output_slots", "output_defaults"}) ||
        !payload.contains("spool_path") || !payload["spool_path"].is_string() ||
        !payload.contains("spool_bytes") || !exactSize(payload["spool_bytes"], request.spoolBytes) ||
        !payload.contains("output_channel_count") ||
        !exactSize(payload["output_channel_count"], request.outputChannelCount) ||
        !payload.contains("output_slots") || !payload["output_slots"].is_array() ||
        !payload.contains("output_defaults") || !payload["output_defaults"].is_array() ||
        !parseFrames(payload["frames"], request.frames, error) ||
        !parseChannels(payload["channels"], request.channels, error, true)) {
        if (error.empty()) error = "invalid native sampler direct_spool payload";
        return false;
    }
    request.spoolPath = payload["spool_path"].get<std::string>();
    if (request.spoolPath.empty() ||
        request.outputChannelCount == 0U || request.outputChannelCount > 1'000'000U) {
        error = "invalid native sampler direct spool identity or output shape";
        return false;
    }
    request.outputSlots.reserve(payload["output_slots"].size());
    for (const json& slot : payload["output_slots"]) {
        std::size_t index = 0U;
        if (!exactSize(slot, index)) {
            error = "native sampler output slot must be an exact non-negative integer";
            return false;
        }
        request.outputSlots.push_back(index);
    }
    request.outputDefaults.reserve(payload["output_defaults"].size());
    for (const json& value : payload["output_defaults"]) {
        double number = 0.0;
        if (!finiteNumber(value, number)) {
            error = "native sampler output default must be finite";
            return false;
        }
        request.outputDefaults.push_back(number);
    }
    if (request.outputSlots.size() != request.channels.size() ||
        request.outputDefaults.size() != request.outputChannelCount) {
        error = "native sampler output shape does not match channels";
        return false;
    }
    std::vector<bool> covered(request.outputChannelCount, false);
    for (std::size_t slot : request.outputSlots) {
        if (slot >= request.outputChannelCount || covered[slot]) {
            error = "native sampler output slots are duplicated or out of range";
            return false;
        }
        covered[slot] = true;
    }
    if (request.frames.size() > std::numeric_limits<std::size_t>::max() / request.outputChannelCount ||
        request.frames.size() * request.outputChannelCount > kMaxSessionSamples ||
        request.frames.size() * request.outputChannelCount > kMaxSpoolBytes / sizeof(double) ||
        request.spoolBytes != request.frames.size() * request.outputChannelCount * sizeof(double)) {
        error = "native sampler direct spool shape is invalid or too large";
        return false;
    }
    request.compoundGroups = classifyCompoundGroups(request.channels);
    return true;
}

bool directValue(const Channel& channel, const MFnAnimCurve& curve, double frame, double& value)
{
    MStatus status;
    const double raw = curve.evaluate(MTime(frame, MTime::uiUnit()), &status);
    if (!status || !std::isfinite(raw)) return false;
    return sourceOutputUnit(channel.directOutput, channel.unit, raw, value);
}

MStatus sampleDirectSpool(const Request& request)
{
    if (MAnimControl::isPlaying()) {
        return fail("maya_timeline_bake_v1 is unavailable during playback");
    }
    std::fstream spool(request.spoolPath, std::ios::in | std::ios::out | std::ios::binary);
    if (!spool.is_open()) return fail("direct spool could not be opened");
    spool.seekg(0, std::ios::end);
    const std::streamoff actualBytes = spool.tellg();
    if (actualBytes < 0 || static_cast<std::size_t>(actualBytes) != request.spoolBytes) {
        spool.close();
        return fail("direct spool has an unexpected byte size");
    }
    spool.seekp(0, std::ios::beg);

    std::vector<int> groupForChannel(request.channels.size(), -1);
    std::vector<int> groupSlot(request.channels.size(), -1);
    for (std::size_t groupIndex = 0U; groupIndex < request.compoundGroups.size(); ++groupIndex) {
        const CompoundGroup& group = request.compoundGroups[groupIndex];
        for (std::size_t slot = 0U; slot < group.channelIndices.size(); ++slot) {
            const std::size_t channelIndex = group.channelIndices[slot];
            groupForChannel[channelIndex] = static_cast<int>(groupIndex);
            groupSlot[channelIndex] = static_cast<int>(slot);
        }
    }
    std::vector<std::unique_ptr<MFnAnimCurve>> directCurves(request.channels.size());
    for (std::size_t channelIndex = 0U; channelIndex < request.channels.size(); ++channelIndex) {
        const Channel& channel = request.channels[channelIndex];
        if (channel.strategy != Strategy::DirectCurve) continue;
        MStatus status;
        directCurves[channelIndex] = std::make_unique<MFnAnimCurve>(channel.directOutput.node(), &status);
        if (!status || !directCurves[channelIndex]) {
            spool.close();
            return fail("direct animation curve became unavailable: " + channel.canonicalPlug);
        }
    }

    const MTime entryTime = MAnimControl::currentTime();
    std::unique_ptr<ComputationGuard> computationGuard;
    TimingTotals timing;
    TimingTotals checkpointTiming;
    std::vector<DirectCheckpoint> checkpoints;
    std::vector<std::array<double, 3U>> compoundValues(request.compoundGroups.size());
    std::vector<bool> compoundReady(request.compoundGroups.size(), false);
    std::vector<bool> compoundSucceeded(request.compoundGroups.size(), false);
    std::vector<bool> compoundFallback(request.compoundGroups.size(), false);
    std::vector<double> outputRow(request.outputChannelCount, 0.0);
    std::string sampleError;
    WallClock::time_point checkpointWallStart;
    bool currentTimeRestored = false;

    auto restoreTime = [&]() -> bool {
        return static_cast<bool>(MAnimControl::setCurrentTime(entryTime));
    };
    const auto checkpointStart = [&](std::size_t frameIndex) {
        if (frameIndex % 120U == 0U) {
            // Match the old command-per-chunk lifecycle.  Maya's interrupt
            // state is scoped to one checkpoint, while the resolved plan and
            // bound function sets remain scoped to this full request.
            computationGuard.reset();
            computationGuard = std::make_unique<ComputationGuard>();
            checkpointTiming = TimingTotals{};
            checkpointWallStart = WallClock::now();
            std::fill(compoundReady.begin(), compoundReady.end(), false);
            std::fill(compoundSucceeded.begin(), compoundSucceeded.end(), false);
            std::fill(compoundFallback.begin(), compoundFallback.end(), false);
        }
    };
    const auto appendCheckpoint = [&]() {
        DirectCheckpoint checkpoint;
        checkpoint.timing = checkpointTiming;
        checkpoint.wallSec = elapsedSeconds(checkpointWallStart, WallClock::now());
        checkpoint.classifiedCompoundGroupCount = request.compoundGroups.size();
        checkpoint.classifiedCompoundCoveredChannelCount = request.compoundGroups.size() * 3U;
        for (std::size_t groupIndex = 0U; groupIndex < request.compoundGroups.size(); ++groupIndex) {
            if (compoundFallback[groupIndex]) ++checkpoint.compoundFallbackGroupCount;
            else if (compoundSucceeded[groupIndex]) ++checkpoint.compoundSuccessGroupCount;
        }
        checkpoint.compoundSuccessCoveredChannelCount = checkpoint.compoundSuccessGroupCount * 3U;
        checkpoint.compoundFallbackCoveredChannelCount = checkpoint.compoundFallbackGroupCount * 3U;
        checkpoints.push_back(checkpoint);
    };

    std::size_t directCount = 0U;
    std::size_t staticCount = 0U;
    std::size_t timedCount = 0U;
    for (const Channel& channel : request.channels) {
        if (channel.strategy == Strategy::DirectCurve) ++directCount;
        else if (channel.strategy == Strategy::Static) ++staticCount;
        else ++timedCount;
    }
    const bool requiresTimelineEvaluation = timedCount != 0U;
    for (std::size_t frameIndex = 0U; frameIndex < request.frames.size(); ++frameIndex) {
        checkpointStart(frameIndex);
        // The parent compound value is frame-local.  Only the runtime
        // success/fallback decision is checkpoint-scoped.
        std::fill(compoundReady.begin(), compoundReady.end(), false);
        // A failed setCurrentTime/read/write/cancellation path must perform a
        // final restore.  A completed checkpoint already restored entryTime,
        // so the successful final checkpoint must not restore it twice.
        currentTimeRestored = !requiresTimelineEvaluation;
        const double frame = request.frames[frameIndex];
        if (requiresTimelineEvaluation) {
            const auto setCurrentTimeStart = WallClock::now();
            const MStatus timeStatus =
                MAnimControl::setCurrentTime(MTime(frame, MTime::uiUnit()));
            const double setCurrentTimeWallSec =
                elapsedSeconds(setCurrentTimeStart, WallClock::now());
            timing.setCurrentTimeWallSec += setCurrentTimeWallSec;
            checkpointTiming.setCurrentTimeWallSec += setCurrentTimeWallSec;
            if (!timeStatus) {
                sampleError = "maya_timeline_bake_v1 could not set frame " +
                              std::to_string(frame);
                break;
            }
        }
        outputRow = request.outputDefaults;
        const auto channelLoopStart = WallClock::now();
        bool firstTimedMPlugReadMeasured = false;
        for (std::size_t channelIndex = 0U; channelIndex < request.channels.size(); ++channelIndex) {
            const Channel& channel = request.channels[channelIndex];
            double value = 0.0;
            bool ok = false;
            if (channel.strategy == Strategy::DirectCurve) {
                ok = directCurves[channelIndex] &&
                     directValue(channel, *directCurves[channelIndex], frame, value);
            } else if (channel.strategy == Strategy::Static) {
                value = channel.staticValue;
                ok = true;
            } else {
                const int groupIndex = groupForChannel[channelIndex];
                if (groupIndex >= 0 && !compoundReady[static_cast<std::size_t>(groupIndex)] &&
                    !compoundFallback[static_cast<std::size_t>(groupIndex)]) {
                    const auto firstTimedReadStart =
                        !firstTimedMPlugReadMeasured ? WallClock::now() : WallClock::time_point();
                    const bool compoundOk = readCompoundGroup(
                        request.compoundGroups[static_cast<std::size_t>(groupIndex)], request.channels,
                        compoundValues[static_cast<std::size_t>(groupIndex)]);
                    if (!firstTimedMPlugReadMeasured) {
                        const double firstTimedReadWallSec =
                            elapsedSeconds(firstTimedReadStart, WallClock::now());
                        timing.firstTimedMPlugReadWallSec += firstTimedReadWallSec;
                        checkpointTiming.firstTimedMPlugReadWallSec += firstTimedReadWallSec;
                        firstTimedMPlugReadMeasured = true;
                    }
                    if (compoundOk) {
                        compoundReady[static_cast<std::size_t>(groupIndex)] = true;
                        compoundSucceeded[static_cast<std::size_t>(groupIndex)] = true;
                    } else {
                        compoundFallback[static_cast<std::size_t>(groupIndex)] = true;
                    }
                }
                if (groupIndex >= 0 && compoundReady[static_cast<std::size_t>(groupIndex)]) {
                    value = compoundValues[static_cast<std::size_t>(groupIndex)]
                        [static_cast<std::size_t>(groupSlot[channelIndex])];
                    ok = std::isfinite(value);
                } else {
                    const auto firstTimedReadStart =
                        !firstTimedMPlugReadMeasured ? WallClock::now() : WallClock::time_point();
                    ok = readNumeric(channel.plug, channel.unit, value);
                    if (!firstTimedMPlugReadMeasured) {
                        const double firstTimedReadWallSec =
                            elapsedSeconds(firstTimedReadStart, WallClock::now());
                        timing.firstTimedMPlugReadWallSec += firstTimedReadWallSec;
                        checkpointTiming.firstTimedMPlugReadWallSec += firstTimedReadWallSec;
                        firstTimedMPlugReadMeasured = true;
                    }
                }
            }
            if (!ok || !std::isfinite(value)) {
                sampleError = "sampling failed for channel " + channel.canonicalPlug +
                              " at frame " + std::to_string(frame);
                break;
            }
            const std::size_t outputIndex = request.outputSlots[channelIndex];
            outputRow[outputIndex] = value;
        }
        const double channelLoopWallSec = elapsedSeconds(channelLoopStart, WallClock::now());
        timing.channelLoopWallSec += channelLoopWallSec;
        checkpointTiming.channelLoopWallSec += channelLoopWallSec;
        if (!sampleError.empty()) break;
        for (double value : outputRow) {
            if (!std::isfinite(value)) {
                sampleError = "direct spool encountered a non-finite value";
                break;
            }
        }
        if (!sampleError.empty()) break;
        const std::streamoff offset = static_cast<std::streamoff>(
            frameIndex * request.outputChannelCount * sizeof(double));
        spool.seekp(offset, std::ios::beg);
        spool.write(reinterpret_cast<const char*>(outputRow.data()),
                    static_cast<std::streamsize>(outputRow.size() * sizeof(double)));
        if (!spool.good()) {
            sampleError = "direct spool write failed";
            break;
        }
        if (computationGuard == nullptr || computationGuard->interrupted()) {
            sampleError = "sampling cancelled";
            break;
        }
        const bool checkpointEnd = ((frameIndex + 1U) % 120U == 0U) ||
                                   frameIndex + 1U == request.frames.size();
        if (checkpointEnd && requiresTimelineEvaluation && !restoreTime()) {
            sampleError = "current time restoration failed";
            break;
        }
        if (checkpointEnd) {
            currentTimeRestored = true;
            appendCheckpoint();
            computationGuard.reset();
        }
    }
    computationGuard.reset();
    const bool restored = currentTimeRestored || restoreTime();
    const bool flushed = static_cast<bool>(spool.flush());
    spool.close();
    const bool closed = !spool.fail();
    if (!restored) return fail("current time restoration failed");
    if (!flushed) return fail("direct spool flush failed");
    if (!closed) return fail("direct spool close failed");
    if (!sampleError.empty()) return fail(sampleError);

    if (checkpoints.size() != (request.frames.size() + 119U) / 120U) {
        return fail("direct spool checkpoint diagnostics are incomplete");
    }
    MDoubleArray result;
    const std::size_t directAckOffset = kHeaderSize + kTimingHeaderSizeV3;
    const std::size_t directAckSize = 2U + checkpoints.size() * kDirectCheckpointRecordSize;
    result.setLength(static_cast<unsigned int>(directAckOffset + directAckSize));
    result[0] = static_cast<double>(kProtocolVersion);
    result[1] = static_cast<double>(request.frames.size());
    result[2] = static_cast<double>(request.outputChannelCount);
    result[3] = static_cast<double>(directCount);
    result[4] = static_cast<double>(staticCount);
    result[5] = static_cast<double>(timedCount);
    result[6] = timing.setCurrentTimeWallSec;
    result[7] = timing.firstTimedMPlugReadWallSec;
    result[8] = timing.channelLoopWallSec;
    const DirectCheckpoint& firstCheckpoint = checkpoints.front();
    result[9] = static_cast<double>(firstCheckpoint.classifiedCompoundGroupCount);
    result[10] = static_cast<double>(firstCheckpoint.classifiedCompoundCoveredChannelCount);
    result[11] = static_cast<double>(firstCheckpoint.compoundSuccessGroupCount);
    result[12] = static_cast<double>(firstCheckpoint.compoundSuccessCoveredChannelCount);
    result[13] = static_cast<double>(firstCheckpoint.compoundFallbackGroupCount);
    result[14] = static_cast<double>(firstCheckpoint.compoundFallbackCoveredChannelCount);
    result[15] = static_cast<double>(kDirectAckVersion);
    result[16] = static_cast<double>(checkpoints.size());
    std::size_t checkpointOffset = 17U;
    for (const DirectCheckpoint& checkpoint : checkpoints) {
        result[checkpointOffset++] = checkpoint.timing.setCurrentTimeWallSec;
        result[checkpointOffset++] = checkpoint.timing.firstTimedMPlugReadWallSec;
        result[checkpointOffset++] = checkpoint.timing.channelLoopWallSec;
        result[checkpointOffset++] = static_cast<double>(checkpoint.classifiedCompoundGroupCount);
        result[checkpointOffset++] = static_cast<double>(checkpoint.classifiedCompoundCoveredChannelCount);
        result[checkpointOffset++] = static_cast<double>(checkpoint.compoundSuccessGroupCount);
        result[checkpointOffset++] = static_cast<double>(checkpoint.compoundSuccessCoveredChannelCount);
        result[checkpointOffset++] = static_cast<double>(checkpoint.compoundFallbackGroupCount);
        result[checkpointOffset++] = static_cast<double>(checkpoint.compoundFallbackCoveredChannelCount);
        result[checkpointOffset++] = checkpoint.wallSec;
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
    return sampleDirectSpool(request);
}

bool MmdVmdBatchSamplerCommand::isUndoable() const
{
    return false;
}
