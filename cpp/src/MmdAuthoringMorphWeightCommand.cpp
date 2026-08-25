#include "MmdAuthoringMorphWeightCommand.h"

#include <maya/MArgDatabase.h>
#include <maya/MDagPath.h>
#include <maya/MFnAttribute.h>
#include <maya/MFnDependencyNode.h>
#include <maya/MFnNumericAttribute.h>
#include <maya/MGlobal.h>
#include <maya/MSelectionList.h>
#include <maya/MString.h>

#include <cmath>
#include <cstdint>
#include <exception>
#include <string>
#include <unordered_set>
#include <vector>

#include "third_party/json.hpp"

namespace {
using json = nlohmann::json;
constexpr int kProtocolVersion = 1;
constexpr const char* kCommand = "mmdAuthoringSetMorphWeights";
constexpr const char* kPayloadFlag = "-payload";

std::string utf8(const MString& value) { return value.asUTF8(); }

std::string canonicalNodeName(const MObject& node)
{
    if (node.hasFn(MFn::kDagNode)) {
        MDagPath path;
        if (MDagPath::getAPathTo(node, path)) return utf8(path.fullPathName());
    }
    MStatus status;
    MFnDependencyNode fn(node, &status);
    return status ? utf8(fn.name()) : std::string();
}

bool isAllowedWeightPlug(const MPlug& plug, std::string& canonical)
{
    MStatus status;
    if (!plug.isElement()) return false;
    const MPlug array = plug.array(&status);
    if (!status || array.isNull()) return false;
    MFnAttribute attribute(array.attribute(), &status);
    if (!status) return false;
    MFnDependencyNode node(plug.node(), &status);
    if (!status) return false;
    const std::string nodeType = utf8(node.typeName());
    const std::string attrName = utf8(attribute.name());
    const bool controllerWeight = nodeType == "mmdMorphController" && attrName == "inputWeight";
    const bool blendShapeWeight = nodeType == "blendShape" && attrName == "weight";
    if (!(controllerWeight || blendShapeWeight)) {
        return false;
    }
    MFnNumericAttribute numeric(array.attribute(), &status);
    const MFnNumericData::Type numericType = status ? numeric.unitType(&status) : MFnNumericData::kInvalid;
    if (!status || (controllerWeight && numericType != MFnNumericData::kDouble) ||
        (blendShapeWeight && numericType != MFnNumericData::kFloat)) {
        return false;
    }
    const unsigned int logicalIndex = plug.logicalIndex(&status);
    if (!status) return false;
    canonical = canonicalNodeName(plug.node()) + "." + attrName + "[" +
        std::to_string(logicalIndex) + "]";
    return true;
}
}  // namespace

void* MmdAuthoringSetMorphWeightsCommand::creator()
{
    return new MmdAuthoringSetMorphWeightsCommand();
}

MSyntax MmdAuthoringSetMorphWeightsCommand::newSyntax()
{
    MSyntax syntax;
    syntax.addFlag("-p", kPayloadFlag, MSyntax::kString);
    syntax.enableEdit(false);
    syntax.enableQuery(false);
    return syntax;
}

MStatus MmdAuthoringSetMorphWeightsCommand::finishError(
    const char* phase, const std::string& code, const std::string& message)
{
    setResult(MString(json({
        {"version", kProtocolVersion}, {"command", kCommand}, {"ok", false},
        {"phase", phase}, {"error", {{"code", code}, {"message", message}}},
    }).dump().c_str()));
    return MS::kSuccess;
}

MStatus MmdAuthoringSetMorphWeightsCommand::finishSuccess(const char* phase)
{
    json plugs = json::array();
    json values = json::array();
    for (const Mutation& mutation : mutations_) {
        plugs.push_back(mutation.canonicalPlug);
        values.push_back(phase == std::string("undo") ? mutation.before : mutation.after);
    }
    setResult(MString(json({
        {"version", kProtocolVersion}, {"command", kCommand}, {"ok", true},
        {"phase", phase}, {"plugs", plugs}, {"values", values},
    }).dump().c_str()));
    return MS::kSuccess;
}

MStatus MmdAuthoringSetMorphWeightsCommand::doIt(const MArgList& args)
{
    mutations_.clear();
    prepared_ = false;
    MStatus status;
    MArgDatabase argData(newSyntax(), args, &status);
    if (!status || !argData.isFlagSet(kPayloadFlag)) {
        return finishError("prepare", "invalid_arguments", "-payload is required");
    }

    json payload;
    bool duplicateKey = false;
    std::vector<std::unordered_set<std::string>> objectKeys;
    const auto rejectDuplicateKeys = [&duplicateKey, &objectKeys](
        int, json::parse_event_t event, json& parsed) {
        if (event == json::parse_event_t::object_start) objectKeys.emplace_back();
        else if (event == json::parse_event_t::key && !objectKeys.empty())
            duplicateKey = !objectKeys.back().insert(parsed.get<std::string>()).second || duplicateKey;
        else if (event == json::parse_event_t::object_end && !objectKeys.empty()) objectKeys.pop_back();
        return true;
    };
    try {
        payload = json::parse(utf8(argData.flagArgumentString(kPayloadFlag, 0)), rejectDuplicateKeys);
    } catch (const std::exception& error) {
        return finishError("prepare", "invalid_json", error.what());
    }
    if (duplicateKey) return finishError("prepare", "duplicate_json_key", "payload contains a duplicate object key");
    const bool validVersion = payload.is_object() && payload.contains("version") &&
        !payload["version"].is_boolean() &&
        ((payload["version"].is_number_unsigned() &&
          payload["version"].get<std::uint64_t>() == static_cast<std::uint64_t>(kProtocolVersion)) ||
         (payload["version"].is_number_integer() && !payload["version"].is_number_unsigned() &&
          payload["version"].get<std::int64_t>() == kProtocolVersion));
    if (!validVersion || payload.size() != 2U || !payload.contains("updates") ||
        !payload["updates"].is_array() || payload["updates"].empty()) {
        return finishError("prepare", "invalid_payload", "version=1 and a non-empty updates array are required");
    }

    std::unordered_set<std::string> canonicalPlugs;
    for (const json& update : payload["updates"]) {
        if (!update.is_object() || update.size() != 2U || !update.contains("plug") ||
            !update["plug"].is_string() || update["plug"].get<std::string>().empty() ||
            !update.contains("value") || !update["value"].is_number() || update["value"].is_boolean()) {
            mutations_.clear();
            return finishError("prepare", "invalid_update", "each update requires only plug and a finite numeric value");
        }
        double value = 0.0;
        try {
            value = update["value"].get<double>();
        } catch (const std::exception& error) {
            mutations_.clear();
            return finishError("prepare", "invalid_value", error.what());
        }
        if (!std::isfinite(value)) {
            mutations_.clear();
            return finishError("prepare", "invalid_value", "morph weight must be finite");
        }
        MSelectionList selection;
        status = selection.add(MString(update["plug"].get<std::string>().c_str()));
        MPlug plug;
        if (!status || selection.length() != 1U || !selection.getPlug(0U, plug)) {
            mutations_.clear();
            return finishError("prepare", "ambiguous_or_missing_plug", update["plug"].get<std::string>());
        }
        std::string canonical;
        if (!isAllowedWeightPlug(plug, canonical)) {
            mutations_.clear();
            return finishError("prepare", "plug_not_allowed", update["plug"].get<std::string>());
        }
        if (!canonicalPlugs.insert(canonical).second) {
            mutations_.clear();
            return finishError("prepare", "duplicate_plug", canonical);
        }
        MPlug parentArray = plug.array(&status);
        if (!status || parentArray.isNull() || parentArray.isLocked() || plug.isLocked() ||
            plug.isFreeToChange(false, true) != MPlug::kFreeToChange) {
            mutations_.clear();
            return finishError("prepare", "plug_not_settable", canonical);
        }
        Mutation mutation;
        mutation.node = MObjectHandle(plug.node());
        mutation.plug = plug;
        mutation.canonicalPlug = canonical;
        mutation.floatStorage = utf8(MFnDependencyNode(plug.node()).typeName()) == "blendShape";
        mutation.before = mutation.floatStorage
            ? static_cast<double>(plug.asFloat(&status))
            : plug.asDouble(&status);
        mutation.after = mutation.floatStorage
            ? static_cast<double>(static_cast<float>(value))
            : value;
        if (!std::isfinite(mutation.after)) {
            mutations_.clear();
            return finishError("prepare", "invalid_value", canonical + ": value overflows storage");
        }
        if (!status || !std::isfinite(mutation.before)) {
            mutations_.clear();
            return finishError("prepare", "read_failed", canonical);
        }
        mutations_.push_back(std::move(mutation));
    }

    prepared_ = true;
    initialExecution_ = true;
    status = redoIt();
    initialExecution_ = false;
    return status;
}

MStatus MmdAuthoringSetMorphWeightsCommand::apply(bool useAfter)
{
    if (!prepared_) return finishError(useAfter ? "redo" : "undo", "command_not_prepared", "command has no validated write set");
    std::size_t applied = 0U;
    for (Mutation& mutation : mutations_) {
        if (!mutation.node.isValid() || !mutation.node.isAlive()) break;
        const double value = useAfter ? mutation.after : mutation.before;
        const MStatus writeStatus = mutation.floatStorage
            ? mutation.plug.setFloat(static_cast<float>(value))
            : mutation.plug.setDouble(value);
        if (!writeStatus) break;
        ++applied;
        MStatus readStatus;
        const double actual = mutation.floatStorage
            ? static_cast<double>(mutation.plug.asFloat(&readStatus))
            : mutation.plug.asDouble(&readStatus);
        if (!readStatus || actual != value) break;
    }
    if (applied == mutations_.size()) return finishSuccess(useAfter ? "redo" : "undo");

    bool restored = true;
    for (std::size_t index = applied; index > 0U; --index) {
        Mutation& mutation = mutations_[index - 1U];
        const double restore = useAfter ? mutation.before : mutation.after;
        MStatus writeStatus = mutation.floatStorage
            ? mutation.plug.setFloat(static_cast<float>(restore))
            : mutation.plug.setDouble(restore);
        MStatus readStatus;
        const double actual = mutation.floatStorage
            ? static_cast<double>(mutation.plug.asFloat(&readStatus))
            : mutation.plug.asDouble(&readStatus);
        restored = restored && writeStatus && readStatus && actual == restore;
    }
    const char* phase = useAfter ? "redo" : "undo";
    const std::string code = restored ? "write_or_verify_failed" : "rollback_failed";
    const std::string message = restored ? "write set was restored after verification failed" : "rollback could not be verified";
    if (initialExecution_) {
        prepared_ = false;
        return finishError(phase, code, message);
    }
    finishError(phase, code, message);
    MGlobal::displayError(MString("[mmdAuthoringSetMorphWeights] ") + message.c_str());
    return MS::kFailure;
}

MStatus MmdAuthoringSetMorphWeightsCommand::redoIt() { return apply(true); }
MStatus MmdAuthoringSetMorphWeightsCommand::undoIt() { return apply(false); }
bool MmdAuthoringSetMorphWeightsCommand::isUndoable() const { return prepared_; }
