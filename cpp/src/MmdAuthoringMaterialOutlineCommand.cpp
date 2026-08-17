#include "MmdAuthoringMaterialOutlineCommand.h"

#include <maya/MArgDatabase.h>
#include <maya/MDagPath.h>
#include <maya/MFnDependencyNode.h>
#include <maya/MFnNumericAttribute.h>
#include <maya/MFnTypedAttribute.h>
#include <maya/MGlobal.h>
#include <maya/MPlugArray.h>
#include <maya/MSelectionList.h>

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
using Command = MmdAuthoringSetMaterialOutlineCommand;
constexpr int kProtocolVersion = 1;
constexpr const char* kCommand = "mmdAuthoringSetMaterialOutline";
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
    registryPlug.connectedTo(registries, true, false, &status);
    if (!status || registries.length() != 1U) return false;
    MFnDependencyNode registryFn(registries[0].node(), &status);
    if (!status || utf8(registryFn.typeName()) != "network") return false;
    MPlug schema = registryFn.findPlug("mmd_model_registry_schema", false, &status);
    if (!status || utf8(schema.asString(&status)) != "1" || !status) return false;
    MPlug modelRoot = registryFn.findPlug("modelRoot", false, &status);
    if (!status) return false;
    MPlugArray roots;
    modelRoot.connectedTo(roots, true, false, &status);
    if (!status || roots.length() != 1U || roots[0].node() != root) return false;
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
    if (!status || utf8(fn.typeName()) != "dx11Shader") return false;
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
        {"viewport_diffuse", {"DiffuseColorRGB", FieldSpec::Vector3}},
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
        value = result; return true;
    }
    case Command::Storage::Double: {
        const double result = plug.asDouble(&status);
        if (!status || !std::isfinite(result)) return false;
        value = result; return true;
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
        value = result; return true;
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
        const auto& values = std::get<std::array<double, 3>>(value);
        unsigned int applied = 0U;
        for (; applied < 3U; ++applied) {
            MPlug child = plug.child(applied, &status);
            if (!status) break;
            status = storage == Command::Storage::Float3
                ? child.setFloat(static_cast<float>(values[applied]))
                : child.setDouble(values[applied]);
            if (!status) break;
        }
        return applied == 3U;
    }
    }
    return false;
}

bool plugIsSettable(const MPlug& plug)
{
    if (plug.isLocked() || plug.isFreeToChange(false, true) != MPlug::kFreeToChange) return false;
    if (!plug.isCompound()) return true;
    for (unsigned int index = 0; index < plug.numChildren(); ++index) {
        MStatus status;
        MPlug child = plug.child(index, &status);
        if (!status || child.isLocked() || child.isFreeToChange(false, true) != MPlug::kFreeToChange)
            return false;
    }
    return true;
}

bool parseValue(const json& input, FieldSpec::Kind kind, Command::Storage storage, Command::Value& value)
{
    try {
        if (kind == FieldSpec::String) {
            if (!input.is_string()) return false;
            value = input.get<std::string>(); return true;
        }
        if (kind == FieldSpec::Bool) {
            if (!input.is_boolean()) return false;
            value = input.get<bool>(); return true;
        }
        if (kind == FieldSpec::Int) {
            if (!input.is_number_integer() || input.is_boolean()) return false;
            const std::int64_t item = input.get<std::int64_t>();
            if (item < INT_MIN || item > INT_MAX) return false;
            value = static_cast<int>(item); return true;
        }
        if (kind == FieldSpec::Scalar) {
            if (!input.is_number() || input.is_boolean()) return false;
            double item = input.get<double>();
            if (storage == Command::Storage::Float) item = static_cast<double>(static_cast<float>(item));
            if (!std::isfinite(item)) return false;
            value = item; return true;
        }
        if (!input.is_array() || input.size() != 3U) return false;
        std::array<double, 3> result{};
        for (unsigned int index = 0; index < 3U; ++index) {
            if (!input[index].is_number() || input[index].is_boolean()) return false;
            result[index] = input[index].get<double>();
            if (storage == Command::Storage::Float3)
                result[index] = static_cast<double>(static_cast<float>(result[index]));
            if (!std::isfinite(result[index])) return false;
        }
        value = result; return true;
    } catch (const std::exception&) { return false; }
}

bool addMutation(
    MFnDependencyNode& shaderFn,
    const std::string& shaderName,
    const std::string& field,
    const FieldSpec& spec,
    const json& after,
    std::unordered_set<std::string>& plugs,
    std::vector<Command::Mutation>& mutations,
    std::string& error)
{
    MStatus status;
    MPlug plug = shaderFn.findPlug(spec.attribute, false, &status);
    const std::string canonical = shaderName + "." + spec.attribute;
    if (!status) { error = "missing_plug:" + canonical; return false; }
    if (!plugs.insert(canonical).second || !plugIsSettable(plug)) {
        error = "plug_not_settable:" + canonical; return false;
    }
    Command::Mutation mutation;
    mutation.node = MObjectHandle(shaderFn.object());
    mutation.plug = plug;
    mutation.field = field;
    mutation.canonicalPlug = canonical;
    if (spec.kind == FieldSpec::String) {
        MFnTypedAttribute attr(plug.attribute(), &status);
        if (!status || attr.attrType(&status) != MFnData::kString || !status) {
            error = "type_mismatch:" + canonical; return false;
        }
        mutation.storage = Command::Storage::String;
    } else if (!numericStorage(plug, spec.kind, mutation.storage)) {
        error = "type_mismatch:" + canonical; return false;
    }
    if (!parseValue(after, spec.kind, mutation.storage, mutation.after)) {
        error = "invalid_value:" + field; return false;
    }
    if (!readValue(plug, mutation.storage, mutation.before)) {
        error = "read_failed:" + canonical; return false;
    }
    mutations.push_back(std::move(mutation));
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

void* MmdAuthoringSetMaterialOutlineCommand::creator()
{
    return new MmdAuthoringSetMaterialOutlineCommand();
}

MSyntax MmdAuthoringSetMaterialOutlineCommand::newSyntax()
{
    MSyntax syntax;
    syntax.addFlag("-p", kPayloadFlag, MSyntax::kString);
    syntax.enableEdit(false);
    syntax.enableQuery(false);
    return syntax;
}

MStatus MmdAuthoringSetMaterialOutlineCommand::finishError(
    const char* phase, const std::string& code, const std::string& message)
{
    setResult(MString(json({{"version", kProtocolVersion}, {"command", kCommand}, {"ok", false},
        {"phase", phase}, {"error", {{"code", code}, {"message", message}}}}).dump().c_str()));
    return MS::kSuccess;
}

MStatus MmdAuthoringSetMaterialOutlineCommand::finishSuccess(const char* phase)
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

MStatus MmdAuthoringSetMaterialOutlineCommand::doIt(const MArgList& args)
{
    mutations_.clear(); prepared_ = false;
    MStatus status;
    MArgDatabase argData(newSyntax(), args, &status);
    if (!status || !argData.isFlagSet(kPayloadFlag))
        return finishError("prepare", "invalid_arguments", "-payload is required");
    json payload; bool duplicateKey = false; std::vector<std::unordered_set<std::string>> keys;
    const auto rejectDuplicates = [&duplicateKey, &keys](int, json::parse_event_t event, json& parsed) {
        if (event == json::parse_event_t::object_start) keys.emplace_back();
        else if (event == json::parse_event_t::key && !keys.empty())
            duplicateKey = !keys.back().insert(parsed.get<std::string>()).second || duplicateKey;
        else if (event == json::parse_event_t::object_end && !keys.empty()) keys.pop_back();
        return true;
    };
    try { payload = json::parse(utf8(argData.flagArgumentString(kPayloadFlag, 0)), rejectDuplicates); }
    catch (const std::exception& error) { return finishError("prepare", "invalid_json", error.what()); }
    if (duplicateKey) return finishError("prepare", "duplicate_json_key", "payload contains a duplicate object key");
    bool version = false;
    if (payload.is_object() && payload.contains("version") &&
        !payload["version"].is_boolean() && payload["version"].is_number_integer()) {
        try { version = payload["version"].get<std::int64_t>() == kProtocolVersion; }
        catch (const std::exception&) { version = false; }
    }
    if (!version || payload.size() != 7U || !payload.contains("root") || !payload["root"].is_string() ||
        !payload.contains("shader") || !payload["shader"].is_string() ||
        !payload.contains("material_index") || !payload["material_index"].is_number_integer() ||
        payload["material_index"].is_boolean() || !payload.contains("updates") ||
        !payload["updates"].is_array() || !payload.contains("outline_preimage") ||
        !payload["outline_preimage"].is_object() || !payload.contains("outline_target") ||
        !payload["outline_target"].is_object())
        return finishError("prepare", "invalid_payload", "exact outline payload fields are required");
    std::int64_t index64 = -1;
    try { index64 = payload["material_index"].get<std::int64_t>(); }
    catch (const std::exception&) { return finishError("prepare", "invalid_material_index", "out of range"); }
    if (index64 < 0 || index64 > INT_MAX)
        return finishError("prepare", "invalid_material_index", "out of range");
    MObject root, shader; std::string rootName, shaderName;
    if (!resolveNode(payload["root"].get<std::string>(), root, rootName) || !root.hasFn(MFn::kTransform))
        return finishError("prepare", "invalid_root_identity", payload["root"].get<std::string>());
    if (!resolveNode(payload["shader"].get<std::string>(), shader, shaderName))
        return finishError("prepare", "invalid_shader_identity", payload["shader"].get<std::string>());
    if (!exactMaterialIdentity(shader, static_cast<int>(index64)) || !registryOwnsMaterial(root, shader))
        return finishError("prepare", "material_not_owned", shaderName);
    MFnDependencyNode shaderFn(shader, &status);
    if (!status) return finishError("prepare", "invalid_shader", shaderName);

    const std::vector<std::pair<std::string, FieldSpec>> outlineSpecs = {
        {"technique", {"technique", FieldSpec::String}},
        {"EdgeSize", {"EdgeSize", FieldSpec::Scalar}},
        {"mmd_shader_outline_enabled", {"mmd_shader_outline_enabled", FieldSpec::Bool}},
        {"mmdDoubleSided", {"mmdDoubleSided", FieldSpec::Bool}},
        {"mmdTransparencyMode", {"mmdTransparencyMode", FieldSpec::String}},
    };
    const json& preimage = payload["outline_preimage"];
    if (preimage.size() != outlineSpecs.size())
        return finishError("prepare", "invalid_outline_preimage", "outline preimage fields mismatch");
    bool edgeSizeExists = false;
    for (const auto& item : outlineSpecs) {
        if (!preimage.contains(item.first))
            return finishError("prepare", "invalid_outline_preimage", item.first);
        const json& expected = preimage[item.first];
        if (!expected.is_object() || expected.size() != 2U || !expected.contains("exists") ||
            !expected["exists"].is_boolean() || !expected.contains("value"))
            return finishError("prepare", "invalid_outline_preimage", item.first);
        MPlug plug = shaderFn.findPlug(item.second.attribute, false, &status);
        const bool actualExists = status == MS::kSuccess;
        const bool expectedExists = expected["exists"].get<bool>();
        if (actualExists != expectedExists)
            return finishError("prepare", "outline_preimage_mismatch", item.first);
        if (item.first == "EdgeSize") edgeSizeExists = actualExists;
        if (!actualExists) {
            if (!expected["value"].is_null())
                return finishError("prepare", "invalid_outline_preimage", item.first);
            continue;
        }
        Command::Storage storage = Command::Storage::Double;
        if (item.second.kind == FieldSpec::String) {
            MFnTypedAttribute attr(plug.attribute(), &status);
            if (!status || attr.attrType(&status) != MFnData::kString || !status)
                return finishError("prepare", "type_mismatch", item.first);
            storage = Command::Storage::String;
        } else if (!numericStorage(plug, item.second.kind, storage))
            return finishError("prepare", "type_mismatch", item.first);
        Command::Value actual, expectedValue;
        if (!readValue(plug, storage, actual) ||
            !parseValue(expected["value"], item.second.kind, storage, expectedValue) ||
            actual != expectedValue)
            return finishError("prepare", "outline_preimage_mismatch", item.first);
    }

    std::unordered_set<std::string> fields, plugs;
    std::string error;
    for (const json& update : payload["updates"]) {
        if (!update.is_object() || update.size() != 2U || !update.contains("field") ||
            !update["field"].is_string() || !update.contains("value"))
            return finishError("prepare", "invalid_update", "each update requires field and value");
        const std::string field = update["field"].get<std::string>();
        if (!fields.insert(field).second)
            return finishError("prepare", "duplicate_field", field);
        const auto found = fixedFields().find(field);
        if (found == fixedFields().end()) return finishError("prepare", "field_not_allowed", field);
        if (!addMutation(shaderFn, shaderName, field, found->second, update["value"], plugs, mutations_, error)) {
            mutations_.clear();
            const std::size_t split = error.find(':');
            return finishError("prepare", error.substr(0, split), error.substr(split + 1U));
        }
    }

    const json& target = payload["outline_target"];
    const std::size_t expectedTargetSize = edgeSizeExists ? 4U : 3U;
    if (target.size() != expectedTargetSize || !target.contains("technique") ||
        !target.contains("mmdDoubleSided") || !target.contains("mmd_shader_outline_enabled") ||
        edgeSizeExists != target.contains("EdgeSize")) {
        mutations_.clear();
        return finishError("prepare", "invalid_outline_target", "outline target fields mismatch");
    }
    static const std::unordered_set<std::string> allowedTechniques = {
        "MMDTechnique", "MMDTechniqueTranslucent", "MMDTechniqueDoubleSided",
        "MMDTechniqueTranslucentDoubleSided",
    };
    if (!target["technique"].is_string() ||
        allowedTechniques.find(target["technique"].get<std::string>()) == allowedTechniques.end()) {
        mutations_.clear();
        return finishError("prepare", "invalid_outline_target", "technique is outside the fixed DX11 domain");
    }
    if (target.contains("EdgeSize") &&
        (!target["EdgeSize"].is_number() || target["EdgeSize"].is_boolean() ||
         !std::isfinite(target["EdgeSize"].get<double>()) || target["EdgeSize"].get<double>() < 0.0 ||
         target["EdgeSize"].get<double>() > 2.0)) {
        mutations_.clear();
        return finishError("prepare", "invalid_outline_target", "EdgeSize is outside the fixed DX11 domain");
    }
    const json& transparency = preimage["mmdTransparencyMode"];
    static const std::unordered_set<std::string> allowedTransparencyModes = {"opaque", "cutout", "blend"};
    if (transparency["exists"].get<bool>() &&
        (!transparency["value"].is_string() || allowedTransparencyModes.find(
            transparency["value"].get<std::string>()) == allowedTransparencyModes.end())) {
        mutations_.clear();
        return finishError("prepare", "invalid_outline_preimage",
            "mmdTransparencyMode is outside the fixed DX11 policy-input domain");
    }
    const std::vector<std::pair<std::string, FieldSpec>> targetSpecs = {
        {"technique", {"technique", FieldSpec::String}},
        {"mmdDoubleSided", {"mmdDoubleSided", FieldSpec::Bool}},
        {"mmd_shader_outline_enabled", {"mmd_shader_outline_enabled", FieldSpec::Bool}},
        {"EdgeSize", {"EdgeSize", FieldSpec::Scalar}},
    };
    for (const auto& item : targetSpecs) {
        if (!target.contains(item.first)) continue;
        if (!fields.insert(item.first).second ||
            !addMutation(shaderFn, shaderName, item.first, item.second, target[item.first], plugs, mutations_, error)) {
            mutations_.clear();
            if (error.empty()) return finishError("prepare", "duplicate_field", item.first);
            const std::size_t split = error.find(':');
            return finishError("prepare", error.substr(0, split), error.substr(split + 1U));
        }
    }
    if (mutations_.empty()) return finishError("prepare", "empty_write_set", "outline target is required");
    prepared_ = true; initialExecution_ = true; status = redoIt(); initialExecution_ = false; return status;
}

MStatus MmdAuthoringSetMaterialOutlineCommand::apply(bool useAfter)
{
    const char* phase = useAfter ? "redo" : "undo";
    if (!prepared_) return finishError(phase, "command_not_prepared", "command has no validated write set");
    std::size_t applied = 0U, restoreCount = 0U;
    for (Mutation& mutation : mutations_) {
        if (!mutation.node.isValid() || !mutation.node.isAlive()) break;
        const Value& value = useAfter ? mutation.after : mutation.before;
        const bool written = setValue(mutation.plug, mutation.storage, value);
        const bool compound = mutation.storage == Storage::Float3 || mutation.storage == Storage::Double3;
        restoreCount = written || compound ? applied + 1U : applied;
        if (!written) break;
        Value actual;
        if (!readValue(mutation.plug, mutation.storage, actual) || actual != value) break;
        ++applied;
    }
    if (applied == mutations_.size()) return finishSuccess(phase);
    bool restored = true;
    for (std::size_t index = restoreCount; index > 0U; --index) {
        Mutation& mutation = mutations_[index - 1U];
        const Value& restore = useAfter ? mutation.before : mutation.after;
        Value actual;
        restored = setValue(mutation.plug, mutation.storage, restore) &&
            readValue(mutation.plug, mutation.storage, actual) && actual == restore && restored;
    }
    const std::string message = restored ? "write set was restored after verification failed" :
        "rollback could not be verified";
    if (initialExecution_) {
        prepared_ = false;
        return finishError(phase, restored ? "write_or_verify_failed" : "rollback_failed", message);
    }
    finishError(phase, restored ? "write_or_verify_failed" : "rollback_failed", message);
    MGlobal::displayError(MString("[mmdAuthoringSetMaterialOutline] ") + message.c_str());
    return MS::kFailure;
}

MStatus MmdAuthoringSetMaterialOutlineCommand::redoIt() { return apply(true); }
MStatus MmdAuthoringSetMaterialOutlineCommand::undoIt() { return apply(false); }
bool MmdAuthoringSetMaterialOutlineCommand::isUndoable() const { return prepared_; }
