#include "MmdAuthoringMaterialValueCommand.h"

#include <maya/MArgDatabase.h>
#include <maya/MDagPath.h>
#include <maya/MFnDependencyNode.h>
#include <maya/MFnNumericAttribute.h>
#include <maya/MFnTypedAttribute.h>
#include <maya/MGlobal.h>
#include <maya/MPlugArray.h>
#include <maya/MSelectionList.h>
#include <maya/MString.h>

#include <cmath>
#include <climits>
#include <cstdint>
#include <exception>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <utility>
#include <vector>

#include "third_party/json.hpp"

namespace {
using json = nlohmann::json;
using Command = MmdAuthoringSetMaterialValuesCommand;
constexpr int kProtocolVersion = 1;
constexpr const char* kCommand = "mmdAuthoringSetMaterialValues";
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

bool resolveNode(const std::string& requested, MObject& node, std::string& canonical)
{
    MSelectionList selection;
    MStatus status = selection.add(MString(requested.c_str()));
    if (!status || selection.length() != 1U || !selection.getDependNode(0U, node)) return false;
    canonical = canonicalNodeName(node);
    return !canonical.empty() && canonical == requested;
}

bool registryOwnsMaterial(const MObject& root, const MObject& shader)
{
    MStatus status;
    MFnDependencyNode rootFn(root, &status);
    if (!status) return false;
    MPlug registryPlug = rootFn.findPlug("mmd_model_registry", false, &status);
    if (!status) return false;
    MPlugArray registries;
    if (!registryPlug.connectedTo(registries, true, false) || registries.length() != 1U) return false;
    MFnDependencyNode registryFn(registries[0].node(), &status);
    if (!status || utf8(registryFn.typeName()) != "network") return false;
    MPlug schema = registryFn.findPlug("mmd_model_registry_schema", false, &status);
    if (!status || utf8(schema.asString(&status)) != "1" || !status) return false;
    MPlug modelRoot = registryFn.findPlug("modelRoot", false, &status);
    if (!status) return false;
    MPlugArray roots;
    if (!modelRoot.connectedTo(roots, true, false) || roots.length() != 1U ||
        roots[0].node() != root) return false;
    MPlug members = registryFn.findPlug("materialMembers", false, &status);
    if (!status || !members.isArray()) return false;
    const unsigned int count = members.numElements(&status);
    if (!status) return false;
    unsigned int matches = 0U;
    for (unsigned int physical = 0; physical < count; ++physical) {
        MPlug element = members.elementByPhysicalIndex(physical, &status);
        if (!status) return false;
        MPlugArray sources;
        element.connectedTo(sources, true, false, &status);
        if (!status) return false;
        // Maya retains disconnected sparse array elements. They carry no
        // ownership authority and must not invalidate the registry.
        if (sources.length() == 0U) continue;
        if (sources.length() > 1U) return false;
        if (sources[0].node() == shader) ++matches;
    }
    return matches == 1U;
}

bool exactMaterialIdentity(const MObject& shader, int materialIndex)
{
    MStatus status;
    MFnDependencyNode fn(shader, &status);
    if (!status) return false;
    MPlug marker = fn.findPlug("mmd_material", false, &status);
    if (!status || !marker.asBool(&status) || !status) return false;
    MPlug index = fn.findPlug("mmd_material_index", false, &status);
    return status && index.asInt(&status) == materialIndex && status;
}

struct FieldSpec { const char* attribute; enum Kind { String, Bool, Int, Scalar, Vector3 } kind; };

const std::unordered_map<std::string, FieldSpec>& fixedFields()
{
    static const std::unordered_map<std::string, FieldSpec> fields = {
        {"name", {"mmd_material_name", FieldSpec::String}},
        {"name_english", {"mmd_material_name_en", FieldSpec::String}},
        {"diffuse_color", {"diffuse_color", FieldSpec::Vector3}},
        {"diffuse_alpha", {"mmd_diffuse_alpha", FieldSpec::Scalar}},
        {"specular", {"specular_color", FieldSpec::Vector3}},
        {"specular_coefficient", {"shininess", FieldSpec::Scalar}},
        {"ambient", {"ambient_color", FieldSpec::Vector3}},
        {"draw_flags", {"mmd_draw_flags", FieldSpec::Int}},
        {"edge_flag", {"edge_flag", FieldSpec::Bool}},
        {"edge_color", {"mmd_edge_color", FieldSpec::Vector3}},
        {"edge_alpha", {"mmd_edge_alpha", FieldSpec::Scalar}},
        {"edge_size", {"mmd_edge_size", FieldSpec::Scalar}},
        {"memo", {"mmd_memo", FieldSpec::String}},
    };
    return fields;
}

bool viewportField(const MFnDependencyNode& shaderFn, FieldSpec& spec)
{
    const std::string type = utf8(shaderFn.typeName());
    if (type == "standardSurface") spec = {"baseColor", FieldSpec::Vector3};
    else if (type == "dx11Shader" || type == "GLSLShader") spec = {"DiffuseColorRGB", FieldSpec::Vector3};
    else if (type == "lambert") spec = {"color", FieldSpec::Vector3};
    else return false;
    return true;
}

bool numericStorage(const MPlug& plug, FieldSpec::Kind kind, Command::Storage& storage)
{
    MStatus status;
    MFnNumericAttribute attr(plug.attribute(), &status);
    if (!status) return false;
    const MFnNumericData::Type type = attr.unitType(&status);
    if (!status) return false;
    if (kind == FieldSpec::Bool && type == MFnNumericData::kBoolean) storage = Command::Storage::Bool;
    else if (kind == FieldSpec::Int && (type == MFnNumericData::kInt || type == MFnNumericData::kLong || type == MFnNumericData::kShort)) storage = Command::Storage::Int;
    else if (kind == FieldSpec::Scalar && type == MFnNumericData::kFloat) storage = Command::Storage::Float;
    else if (kind == FieldSpec::Scalar && type == MFnNumericData::kDouble) storage = Command::Storage::Double;
    else if (kind == FieldSpec::Vector3 && type == MFnNumericData::k3Float) storage = Command::Storage::Float3;
    else if (kind == FieldSpec::Vector3 && type == MFnNumericData::k3Double) storage = Command::Storage::Double3;
    else return false;
    return true;
}

bool readValue(const MPlug& plug, Command::Storage storage, Command::Value& value)
{
    MStatus status;
    switch (storage) {
    case Command::Storage::String: value = utf8(plug.asString(&status)); return status;
    case Command::Storage::Bool: value = plug.asBool(&status); return status;
    case Command::Storage::Int: value = plug.asInt(&status); return status;
    case Command::Storage::Float: {
        const double result = static_cast<double>(plug.asFloat(&status));
        if (!status || !std::isfinite(result)) return false;
        value = result;
        return true;
    }
    case Command::Storage::Double: {
        const double result = plug.asDouble(&status);
        if (!status || !std::isfinite(result)) return false;
        value = result;
        return true;
    }
    case Command::Storage::Float3:
    case Command::Storage::Double3: {
        if (!plug.isCompound() || plug.numChildren() != 3U) return false;
        std::array<double, 3> result{};
        for (unsigned int index = 0; index < 3U; ++index) {
            MPlug child = plug.child(index, &status);
            if (!status) return false;
            result[index] = storage == Command::Storage::Float3
                ? static_cast<double>(child.asFloat(&status)) : child.asDouble(&status);
            if (!status || !std::isfinite(result[index])) return false;
        }
        value = result;
        return true;
    }
    }
    return false;
}

bool setValue(MPlug& plug, Command::Storage storage, const Command::Value& value)
{
    MStatus status;
    switch (storage) {
    case Command::Storage::String: return plug.setString(MString(std::get<std::string>(value).c_str()));
    case Command::Storage::Bool: return plug.setBool(std::get<bool>(value));
    case Command::Storage::Int: return plug.setInt(std::get<int>(value));
    case Command::Storage::Float: return plug.setFloat(static_cast<float>(std::get<double>(value)));
    case Command::Storage::Double: return plug.setDouble(std::get<double>(value));
    case Command::Storage::Float3:
    case Command::Storage::Double3: {
        const auto& components = std::get<std::array<double, 3>>(value);
        unsigned int applied = 0U;
        for (; applied < 3U; ++applied) {
            MPlug child = plug.child(applied, &status);
            if (!status) break;
            status = storage == Command::Storage::Float3
                ? child.setFloat(static_cast<float>(components[applied]))
                : child.setDouble(components[applied]);
            if (!status) break;
        }
        return applied == 3U;
    }
    }
    return false;
}

bool equalValue(const Command::Value& left, const Command::Value& right) { return left == right; }

bool plugIsSettable(const MPlug& plug)
{
    if (plug.isLocked() || plug.isFreeToChange(false, true) != MPlug::kFreeToChange) return false;
    if (!plug.isCompound()) return true;
    for (unsigned int index = 0; index < plug.numChildren(); ++index) {
        MStatus status;
        MPlug child = plug.child(index, &status);
        if (!status || child.isLocked() ||
            child.isFreeToChange(false, true) != MPlug::kFreeToChange) return false;
    }
    return true;
}

json jsonValue(const Command::Value& value)
{
    if (const auto* item = std::get_if<std::string>(&value)) return *item;
    if (const auto* item = std::get_if<bool>(&value)) return *item;
    if (const auto* item = std::get_if<int>(&value)) return *item;
    if (const auto* item = std::get_if<double>(&value)) return *item;
    const auto& item = std::get<std::array<double, 3>>(value);
    return json::array({item[0], item[1], item[2]});
}
}  // namespace

void* MmdAuthoringSetMaterialValuesCommand::creator() { return new MmdAuthoringSetMaterialValuesCommand(); }

MSyntax MmdAuthoringSetMaterialValuesCommand::newSyntax()
{
    MSyntax syntax;
    syntax.addFlag("-p", kPayloadFlag, MSyntax::kString);
    syntax.enableEdit(false);
    syntax.enableQuery(false);
    return syntax;
}

MStatus MmdAuthoringSetMaterialValuesCommand::finishError(const char* phase, const std::string& code, const std::string& message)
{
    setResult(MString(json({{"version", kProtocolVersion}, {"command", kCommand}, {"ok", false},
        {"phase", phase}, {"error", {{"code", code}, {"message", message}}}}).dump().c_str()));
    return MS::kSuccess;
}

MStatus MmdAuthoringSetMaterialValuesCommand::finishSuccess(const char* phase)
{
    json fields = json::array(), plugs = json::array(), values = json::array();
    const bool undo = std::string(phase) == "undo";
    for (const Mutation& mutation : mutations_) {
        fields.push_back(mutation.field);
        plugs.push_back(mutation.canonicalPlug);
        values.push_back(jsonValue(undo ? mutation.before : mutation.after));
    }
    setResult(MString(json({{"version", kProtocolVersion}, {"command", kCommand}, {"ok", true},
        {"phase", phase}, {"fields", fields}, {"plugs", plugs}, {"values", values}}).dump().c_str()));
    return MS::kSuccess;
}

MStatus MmdAuthoringSetMaterialValuesCommand::doIt(const MArgList& args)
{
    mutations_.clear(); prepared_ = false;
    MStatus status;
    MArgDatabase argData(newSyntax(), args, &status);
    if (!status || !argData.isFlagSet(kPayloadFlag)) return finishError("prepare", "invalid_arguments", "-payload is required");
    json payload; bool duplicateKey = false; std::vector<std::unordered_set<std::string>> keys;
    const auto rejectDuplicates = [&duplicateKey, &keys](int, json::parse_event_t event, json& parsed) {
        if (event == json::parse_event_t::object_start) keys.emplace_back();
        else if (event == json::parse_event_t::key && !keys.empty()) duplicateKey = !keys.back().insert(parsed.get<std::string>()).second || duplicateKey;
        else if (event == json::parse_event_t::object_end && !keys.empty()) keys.pop_back();
        return true;
    };
    try { payload = json::parse(utf8(argData.flagArgumentString(kPayloadFlag, 0)), rejectDuplicates); }
    catch (const std::exception& error) { return finishError("prepare", "invalid_json", error.what()); }
    if (duplicateKey) return finishError("prepare", "duplicate_json_key", "payload contains a duplicate object key");
    const bool version = payload.is_object() && payload.contains("version") &&
        !payload["version"].is_boolean() &&
        ((payload["version"].is_number_unsigned() &&
          payload["version"].get<std::uint64_t>() == static_cast<std::uint64_t>(kProtocolVersion)) ||
         (payload["version"].is_number_integer() && !payload["version"].is_number_unsigned() &&
          payload["version"].get<std::int64_t>() == kProtocolVersion));
    if (!version || payload.size() != 5U || !payload.contains("root") || !payload["root"].is_string() ||
        !payload.contains("shader") || !payload["shader"].is_string() || !payload.contains("material_index") ||
        !payload["material_index"].is_number_integer() || payload["material_index"].is_boolean() ||
        !payload.contains("updates") || !payload["updates"].is_array() || payload["updates"].empty())
        return finishError("prepare", "invalid_payload", "version, canonical root/shader, material_index, and updates are required");
    std::int64_t index64 = -1;
    try {
        if (payload["material_index"].is_number_unsigned()) {
            const auto value = payload["material_index"].get<std::uint64_t>();
            if (value > static_cast<std::uint64_t>(INT_MAX))
                return finishError("prepare", "invalid_material_index", "material_index is out of range");
            index64 = static_cast<std::int64_t>(value);
        } else index64 = payload["material_index"].get<std::int64_t>();
    } catch (const std::exception&) {
        return finishError("prepare", "invalid_material_index", "material_index is out of range");
    }
    if (index64 < 0 || index64 > INT_MAX) return finishError("prepare", "invalid_material_index", "material_index is out of range");
    MObject root, shader; std::string rootName, shaderName;
    if (!resolveNode(payload["root"].get<std::string>(), root, rootName) || !root.hasFn(MFn::kTransform))
        return finishError("prepare", "invalid_root_identity", payload["root"].get<std::string>());
    if (!resolveNode(payload["shader"].get<std::string>(), shader, shaderName))
        return finishError("prepare", "invalid_shader_identity", payload["shader"].get<std::string>());
    if (!exactMaterialIdentity(shader, static_cast<int>(index64)) || !registryOwnsMaterial(root, shader))
        return finishError("prepare", "material_not_owned", shaderName);
    MFnDependencyNode shaderFn(shader, &status);
    if (!status) return finishError("prepare", "invalid_shader", shaderName);
    std::unordered_set<std::string> seenFields, seenPlugs;
    for (const json& update : payload["updates"]) {
        if (!update.is_object() || update.size() != 2U || !update.contains("field") || !update["field"].is_string() || !update.contains("value")) {
            mutations_.clear(); return finishError("prepare", "invalid_update", "each update requires only field and value");
        }
        const std::string field = update["field"].get<std::string>();
        if (!seenFields.insert(field).second) { mutations_.clear(); return finishError("prepare", "duplicate_field", field); }
        FieldSpec spec{};
        auto found = fixedFields().find(field);
        if (found != fixedFields().end()) spec = found->second;
        else if (field == "viewport_diffuse" && viewportField(shaderFn, spec)) {}
        else { mutations_.clear(); return finishError("prepare", "field_not_allowed", field); }
        MPlug plug = shaderFn.findPlug(spec.attribute, false, &status);
        if (!status) { mutations_.clear(); return finishError("prepare", "missing_plug", shaderName + "." + spec.attribute); }
        const std::string canonicalPlug = shaderName + "." + spec.attribute;
        if (!seenPlugs.insert(canonicalPlug).second || !plugIsSettable(plug)) {
            mutations_.clear(); return finishError("prepare", "plug_not_settable", canonicalPlug);
        }
        Mutation mutation; mutation.node = MObjectHandle(shader); mutation.plug = plug; mutation.field = field; mutation.canonicalPlug = canonicalPlug;
        if (spec.kind == FieldSpec::String) {
            MFnTypedAttribute attr(plug.attribute(), &status);
            if (!status || attr.attrType(&status) != MFnData::kString || !status ||
                !update["value"].is_string()) {
                mutations_.clear(); return finishError("prepare", "type_mismatch", field);
            }
            mutation.storage = Storage::String; mutation.after = update["value"].get<std::string>();
        } else {
            if (!numericStorage(plug, spec.kind, mutation.storage)) { mutations_.clear(); return finishError("prepare", "type_mismatch", canonicalPlug); }
            if (spec.kind == FieldSpec::Bool) {
                if (!update["value"].is_boolean()) { mutations_.clear(); return finishError("prepare", "type_mismatch", field); }
                mutation.after = update["value"].get<bool>();
            } else if (spec.kind == FieldSpec::Int) {
                if (!update["value"].is_number_integer() || update["value"].is_boolean()) { mutations_.clear(); return finishError("prepare", "type_mismatch", field); }
                std::int64_t value = 0;
                try { value = update["value"].get<std::int64_t>(); }
                catch (const std::exception&) { mutations_.clear(); return finishError("prepare", "invalid_value", field); }
                if (value < INT_MIN || value > INT_MAX) { mutations_.clear(); return finishError("prepare", "invalid_value", field); }
                mutation.after = static_cast<int>(value);
            } else if (spec.kind == FieldSpec::Scalar) {
                if (!update["value"].is_number() || update["value"].is_boolean()) { mutations_.clear(); return finishError("prepare", "type_mismatch", field); }
                double value = 0.0;
                try { value = update["value"].get<double>(); }
                catch (const std::exception&) { mutations_.clear(); return finishError("prepare", "invalid_value", field); }
                if (!std::isfinite(value)) { mutations_.clear(); return finishError("prepare", "invalid_value", field); }
                if (mutation.storage == Storage::Float) value = static_cast<double>(static_cast<float>(value));
                if (!std::isfinite(value)) { mutations_.clear(); return finishError("prepare", "invalid_value", field); }
                mutation.after = value;
            } else {
                if (!update["value"].is_array() || update["value"].size() != 3U) { mutations_.clear(); return finishError("prepare", "type_mismatch", field); }
                std::array<double, 3> value{};
                for (unsigned int component = 0; component < 3U; ++component) {
                    const json& item = update["value"][component];
                    if (!item.is_number() || item.is_boolean()) { mutations_.clear(); return finishError("prepare", "type_mismatch", field); }
                    try { value[component] = item.get<double>(); }
                    catch (const std::exception&) { mutations_.clear(); return finishError("prepare", "invalid_value", field); }
                    if (mutation.storage == Storage::Float3) value[component] = static_cast<double>(static_cast<float>(value[component]));
                    if (!std::isfinite(value[component])) { mutations_.clear(); return finishError("prepare", "invalid_value", field); }
                }
                mutation.after = value;
            }
        }
        if (!readValue(plug, mutation.storage, mutation.before)) { mutations_.clear(); return finishError("prepare", "read_failed", canonicalPlug); }
        mutations_.push_back(std::move(mutation));
    }
    prepared_ = true; initialExecution_ = true; status = redoIt(); initialExecution_ = false; return status;
}

MStatus MmdAuthoringSetMaterialValuesCommand::apply(bool useAfter)
{
    const char* phase = useAfter ? "redo" : "undo";
    if (!prepared_) return finishError(phase, "command_not_prepared", "command has no validated write set");
    std::size_t applied = 0U;
    std::size_t restoreCount = 0U;
    for (Mutation& mutation : mutations_) {
        if (!mutation.node.isValid() || !mutation.node.isAlive()) break;
        const Value& value = useAfter ? mutation.after : mutation.before;
        const bool written = setValue(mutation.plug, mutation.storage, value);
        const bool compound = mutation.storage == Storage::Float3 ||
            mutation.storage == Storage::Double3;
        restoreCount = written || compound ? applied + 1U : applied;
        if (!written) break;
        Value actual;
        if (!readValue(mutation.plug, mutation.storage, actual) || !equalValue(actual, value)) break;
        ++applied;
    }
    if (applied == mutations_.size()) return finishSuccess(phase);
    bool restored = true;
    for (std::size_t index = restoreCount; index > 0U; --index) {
        Mutation& mutation = mutations_[index - 1U];
        const Value& restore = useAfter ? mutation.before : mutation.after;
        Value actual;
        restored = setValue(mutation.plug, mutation.storage, restore) && readValue(mutation.plug, mutation.storage, actual) && equalValue(actual, restore) && restored;
    }
    const std::string message = restored ? "write set was restored after verification failed" : "rollback could not be verified";
    if (initialExecution_) { prepared_ = false; return finishError(phase, restored ? "write_or_verify_failed" : "rollback_failed", message); }
    finishError(phase, restored ? "write_or_verify_failed" : "rollback_failed", message);
    MGlobal::displayError(MString("[mmdAuthoringSetMaterialValues] ") + message.c_str());
    return MS::kFailure;
}

MStatus MmdAuthoringSetMaterialValuesCommand::redoIt() { return apply(true); }
MStatus MmdAuthoringSetMaterialValuesCommand::undoIt() { return apply(false); }
bool MmdAuthoringSetMaterialValuesCommand::isUndoable() const { return prepared_; }
