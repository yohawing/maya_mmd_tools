#include "MmdAuthoringCommandSupport.h"

#include <maya/MArgDatabase.h>
#include <maya/MDagPath.h>
#include <maya/MFnAttribute.h>
#include <maya/MFnDependencyNode.h>
#include <maya/MFnNumericAttribute.h>
#include <maya/MFnTypedAttribute.h>
#include <maya/MGlobal.h>
#include <maya/MSelectionList.h>
#include <maya/MString.h>
#include <maya/MStringArray.h>

#include <array>
#include <cmath>
#include <cstdint>
#include <exception>
#include <limits>
#include <unordered_set>
#include <unordered_map>

#include "third_party/json.hpp"

namespace {
using json = nlohmann::json;

constexpr int kProtocolVersion = 1;
constexpr const char* kPayloadFlag = "-payload";

const std::unordered_map<std::string, MmdAuthoringSetAttrsCommand::ValueType>& whitelist()
{
    using Type = MmdAuthoringSetAttrsCommand::ValueType;
    static const std::unordered_map<std::string, Type> values = {
        {"mmdAuthoringWitnessBool", Type::Bool},
        {"mmdAuthoringWitnessInt", Type::Int},
        {"mmdAuthoringWitnessDouble", Type::Double},
        {"mmdAuthoringWitnessString", Type::String},
    };
    return values;
}

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

std::string canonicalNodeName(const MObject& node)
{
    if (node.hasFn(MFn::kDagNode)) {
        MDagPath path;
        if (MDagPath::getAPathTo(node, path)) {
            return utf8(path.fullPathName());
        }
    }
    MStatus status;
    MFnDependencyNode fn(node, &status);
    return status ? utf8(fn.absoluteName()) : std::string();
}

bool typeMatches(const MPlug& plug, MmdAuthoringSetAttrsCommand::ValueType expected)
{
    MStatus status;
    MFnAttribute attr(plug.attribute(), &status);
    if (!status) {
        return false;
    }
    if (expected == MmdAuthoringSetAttrsCommand::ValueType::String) {
        MFnTypedAttribute typed(plug.attribute(), &status);
        return status && typed.attrType() == MFnData::kString;
    }
    MFnNumericAttribute numeric(plug.attribute(), &status);
    if (!status) {
        return false;
    }
    const auto actual = numeric.unitType();
    if (expected == MmdAuthoringSetAttrsCommand::ValueType::Bool) {
        return actual == MFnNumericData::kBoolean;
    }
    if (expected == MmdAuthoringSetAttrsCommand::ValueType::Int) {
        return actual == MFnNumericData::kInt || actual == MFnNumericData::kLong ||
               actual == MFnNumericData::kShort || actual == MFnNumericData::kByte;
    }
    return actual == MFnNumericData::kDouble || actual == MFnNumericData::kFloat;
}

MStatus setValue(MPlug& plug, const MmdAuthoringSetAttrsCommand::Value& value)
{
    using Type = MmdAuthoringSetAttrsCommand::ValueType;
    switch (value.type) {
    case Type::Bool: return plug.setBool(value.boolValue);
    case Type::Int: return plug.setInt(value.intValue);
    case Type::Double: return plug.setDouble(value.doubleValue);
    case Type::String: return plug.setString(fromUtf8(value.stringValue));
    }
    return MS::kFailure;
}

bool valueMatches(const MPlug& plug, const MmdAuthoringSetAttrsCommand::Value& value)
{
    using Type = MmdAuthoringSetAttrsCommand::ValueType;
    MStatus status;
    switch (value.type) {
    case Type::Bool: {
        const bool actual = plug.asBool(&status);
        return status && actual == value.boolValue;
    }
    case Type::Int: {
        const int actual = plug.asInt(&status);
        return status && actual == value.intValue;
    }
    case Type::Double: {
        const double actual = plug.asDouble(&status);
        return status && actual == value.doubleValue;
    }
    case Type::String: {
        const MString actual = plug.asString(&status);
        return status && utf8(actual) == value.stringValue;
    }
    }
    return false;
}
}  // namespace

void* MmdAuthoringSetAttrsCommand::creator()
{
    return new MmdAuthoringSetAttrsCommand();
}

MSyntax MmdAuthoringSetAttrsCommand::newSyntax()
{
    MSyntax syntax;
    syntax.addFlag("-p", kPayloadFlag, MSyntax::kString);
    syntax.enableEdit(false);
    syntax.enableQuery(false);
    return syntax;
}

MStatus MmdAuthoringSetAttrsCommand::finishError(
    const char* phase,
    const std::string& code,
    const std::string& message)
{
    setResult(MString(json({
        {"version", kProtocolVersion},
        {"command", "mmdAuthoringSetAttrs"},
        {"ok", false},
        {"phase", phase},
        {"error", {{"code", code}, {"message", message}}},
    }).dump().c_str()));
    return MS::kSuccess;
}

MStatus MmdAuthoringSetAttrsCommand::finishSuccess(const char* phase)
{
    json plugs = json::array();
    for (const Mutation& mutation : mutations_) {
        plugs.push_back(mutation.canonicalPlug);
    }
    setResult(MString(json({
        {"version", kProtocolVersion},
        {"command", "mmdAuthoringSetAttrs"},
        {"ok", true},
        {"phase", phase},
        {"plugs", plugs},
    }).dump().c_str()));
    return MS::kSuccess;
}

MStatus MmdAuthoringSetAttrsCommand::doIt(const MArgList& args)
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
                                         int,
                                         json::parse_event_t event,
                                         json& parsed) {
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
        payload = json::parse(
            utf8(argData.flagArgumentString(kPayloadFlag, 0)),
            rejectDuplicateKeys);
    } catch (const std::exception& error) {
        return finishError("prepare", "invalid_json", error.what());
    }
    if (duplicateKey) {
        return finishError("prepare", "duplicate_json_key", "payload contains a duplicate object key");
    }
    const bool validVersion = payload.is_object() && payload.contains("version") &&
        !payload["version"].is_boolean() &&
        ((payload["version"].is_number_unsigned() &&
          payload["version"].get<std::uint64_t>() == static_cast<std::uint64_t>(kProtocolVersion)) ||
         (payload["version"].is_number_integer() && !payload["version"].is_number_unsigned() &&
          payload["version"].get<std::int64_t>() == kProtocolVersion));
    if (!validVersion ||
        !payload.contains("updates") || !payload["updates"].is_array() ||
        payload["updates"].empty()) {
        return finishError("prepare", "invalid_payload", "version=1 and a non-empty updates array are required");
    }

    std::unordered_set<std::string> canonicalPlugs;
    for (const json& update : payload["updates"]) {
        if (!update.is_object() || !update.contains("plug") || !update["plug"].is_string() ||
            !update.contains("type") || !update["type"].is_string() || !update.contains("value")) {
            mutations_.clear();
            return finishError("prepare", "invalid_update", "each update requires plug, type, and value");
        }
        const std::string plugName = update["plug"].get<std::string>();
        const std::size_t separator = plugName.rfind('.');
        if (separator == std::string::npos || separator == 0U || separator + 1U >= plugName.size()) {
            mutations_.clear();
            return finishError("prepare", "invalid_plug", "plug must be node.attribute");
        }
        const std::string nodeName = plugName.substr(0U, separator);
        const std::string attrName = plugName.substr(separator + 1U);
        const auto allowed = whitelist().find(attrName);
        if (allowed == whitelist().end()) {
            mutations_.clear();
            return finishError("prepare", "plug_not_allowed", plugName);
        }

        MSelectionList selection;
        status = MGlobal::getSelectionListByName(MString(nodeName.c_str()), selection);
        if (!status || selection.length() != 1U) {
            mutations_.clear();
            return finishError("prepare", "ambiguous_or_missing_node", nodeName);
        }
        MObject node;
        status = selection.getDependNode(0U, node);
        if (!status) {
            mutations_.clear();
            return finishError("prepare", "invalid_node", nodeName);
        }

        MFnDependencyNode fn(node, &status);
        MPlug plug = fn.findPlug(MString(attrName.c_str()), false, &status);
        if (!status || plug.isNull()) {
            mutations_.clear();
            return finishError("prepare", "missing_plug", plugName);
        }
        if (!typeMatches(plug, allowed->second)) {
            mutations_.clear();
            return finishError("prepare", "type_mismatch", plugName);
        }
        if (plug.isLocked() || plug.isFreeToChange(false, true) != MPlug::kFreeToChange) {
            mutations_.clear();
            return finishError("prepare", "plug_not_settable", plugName);
        }

        Mutation mutation;
        mutation.node = MObjectHandle(node);
        mutation.plug = plug;
        mutation.canonicalPlug = canonicalNodeName(node) + "." + attrName;
        if (!canonicalPlugs.insert(mutation.canonicalPlug).second) {
            mutations_.clear();
            return finishError("prepare", "duplicate_plug", mutation.canonicalPlug);
        }
        mutation.before.type = allowed->second;
        mutation.after.type = allowed->second;
        try {
            const std::string typeName = update["type"].get<std::string>();
            switch (allowed->second) {
            case ValueType::Bool:
                if (typeName != "bool" || !update["value"].is_boolean()) throw std::runtime_error("bool required");
                mutation.before.boolValue = plug.asBool();
                mutation.after.boolValue = update["value"].get<bool>();
                break;
            case ValueType::Int:
                if (typeName != "int" || !update["value"].is_number_integer()) throw std::runtime_error("int required");
                {
                    int requested = 0;
                    if (update["value"].is_number_unsigned()) {
                        const std::uint64_t unsignedValue = update["value"].get<std::uint64_t>();
                        if (unsignedValue > static_cast<std::uint64_t>(std::numeric_limits<int>::max())) {
                            throw std::runtime_error("int out of range");
                        }
                        requested = static_cast<int>(unsignedValue);
                    } else {
                        const std::int64_t signedValue = update["value"].get<std::int64_t>();
                        if (signedValue < std::numeric_limits<int>::min() ||
                            signedValue > std::numeric_limits<int>::max()) {
                            throw std::runtime_error("int out of range");
                        }
                        requested = static_cast<int>(signedValue);
                    }
                    mutation.before.intValue = plug.asInt();
                    mutation.after.intValue = requested;
                }
                break;
            case ValueType::Double:
                if (typeName != "double" || !update["value"].is_number_float()) throw std::runtime_error("double required");
                mutation.before.doubleValue = plug.asDouble();
                mutation.after.doubleValue = update["value"].get<double>();
                break;
            case ValueType::String:
                if (typeName != "string" || !update["value"].is_string()) throw std::runtime_error("string required");
                mutation.before.stringValue = utf8(plug.asString());
                mutation.after.stringValue = update["value"].get<std::string>();
                break;
            }
        } catch (const std::exception& error) {
            mutations_.clear();
            return finishError("prepare", "value_type_mismatch", plugName + ": " + error.what());
        }
        mutations_.push_back(std::move(mutation));
    }

    prepared_ = true;
    initialExecution_ = true;
    status = redoIt();
    initialExecution_ = false;
    return status;
}

MStatus MmdAuthoringSetAttrsCommand::apply(bool useAfter)
{
    if (!prepared_) {
        return finishError(useAfter ? "redo" : "undo", "command_not_prepared", "command has no validated write set");
    }
    std::size_t applied = 0U;
    for (Mutation& mutation : mutations_) {
        if (!mutation.node.isValid() || !mutation.node.isAlive()) {
            break;
        }
        const Value& value = useAfter ? mutation.after : mutation.before;
        const MStatus status = setValue(mutation.plug, value);
        if (!status) {
            break;
        }
        ++applied;
    }
    bool verified = applied == mutations_.size();
    if (verified) {
        for (const Mutation& mutation : mutations_) {
            const Value& expected = useAfter ? mutation.after : mutation.before;
            if (!valueMatches(mutation.plug, expected)) {
                verified = false;
                break;
            }
        }
    }
    if (!verified) {
        for (std::size_t index = applied; index > 0U; --index) {
            Mutation& mutation = mutations_[index - 1U];
            const Value& value = useAfter ? mutation.before : mutation.after;
            setValue(mutation.plug, value);
        }
        bool restored = true;
        for (std::size_t index = 0U; index < applied; ++index) {
            const Mutation& mutation = mutations_[index];
            const Value& expected = useAfter ? mutation.before : mutation.after;
            restored = restored && valueMatches(mutation.plug, expected);
        }
        const char* phase = useAfter ? "redo" : "undo";
        const std::string code = restored ? "write_or_verify_failed" : "rollback_failed";
        const std::string message =
            restored ? "write set was restored after mutation verification failed"
                     : "mutation verification failed and rollback could not be verified";
        if (initialExecution_) {
            prepared_ = false;
            return finishError(phase, code, message);
        }
        finishError(
            useAfter ? "redo" : "undo",
            code,
            message);
        MGlobal::displayError(MString("[mmdAuthoringSetAttrs] ") + message.c_str());
        return MS::kFailure;
    }
    return finishSuccess(useAfter ? "redo" : "undo");
}

MStatus MmdAuthoringSetAttrsCommand::redoIt()
{
    return apply(true);
}

MStatus MmdAuthoringSetAttrsCommand::undoIt()
{
    return apply(false);
}

bool MmdAuthoringSetAttrsCommand::isUndoable() const
{
    return prepared_;
}
