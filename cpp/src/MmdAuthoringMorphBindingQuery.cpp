#include "MmdAuthoringMorphBindingQuery.h"

#include <maya/MArgDatabase.h>
#include <maya/MDagPath.h>
#include <maya/MFnDependencyNode.h>
#include <maya/MGlobal.h>
#include <maya/MPlug.h>
#include <maya/MPlugArray.h>
#include <maya/MSelectionList.h>
#include <maya/MString.h>

#include <exception>
#include <limits>
#include <string>
#include <unordered_set>
#include <vector>

#include "third_party/json.hpp"

namespace {
using json = nlohmann::json;

constexpr int kProtocolVersion = 1;
constexpr const char* kCommand = "mmdAuthoringQueryMorphBindings";
constexpr const char* kPayloadFlag = "-payload";
constexpr const char* kRawNameAttribute = "mmd_blendshape_morph_names_json";

std::string utf8(const MString& value) { return value.asUTF8(); }

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
    // maya.cmds.ls(node, long=True) uses the dependency-node name (without a
    // leading root-namespace colon) for non-DAG nodes.
    return status ? utf8(fn.name()) : std::string();
}

json errorResult(const std::string& code, const std::string& message)
{
    return {
        {"version", kProtocolVersion}, {"command", kCommand}, {"ok", false},
        {"error", {{"code", code}, {"message", message}}},
    };
}

bool parsePayload(const MString& raw, json& payload, std::string& error)
{
    bool duplicate = false;
    std::vector<std::unordered_set<std::string>> objectKeys;
    const auto rejectDuplicateKeys = [&duplicate, &objectKeys](
                                         int, json::parse_event_t event, json& parsed) {
        if (event == json::parse_event_t::object_start) {
            objectKeys.emplace_back();
        } else if (event == json::parse_event_t::key && !objectKeys.empty()) {
            duplicate = !objectKeys.back().insert(parsed.get<std::string>()).second || duplicate;
        } else if (event == json::parse_event_t::object_end && !objectKeys.empty()) {
            objectKeys.pop_back();
        }
        return true;
    };
    try {
        payload = json::parse(utf8(raw), rejectDuplicateKeys);
    } catch (const std::exception& exc) {
        error = exc.what();
        return false;
    }
    if (duplicate) {
        error = "payload contains a duplicate object key";
        return false;
    }
    return true;
}

}  // namespace

void* MmdAuthoringMorphBindingQueryCommand::creator()
{
    return new MmdAuthoringMorphBindingQueryCommand();
}

MSyntax MmdAuthoringMorphBindingQueryCommand::newSyntax()
{
    MSyntax syntax;
    syntax.addFlag("-p", kPayloadFlag, MSyntax::kString);
    syntax.enableEdit(false);
    syntax.enableQuery(false);
    return syntax;
}

MStatus MmdAuthoringMorphBindingQueryCommand::doIt(const MArgList& args)
{
    MStatus status;
    MArgDatabase database(syntax(), args, &status);
    if (!status || !database.isFlagSet(kPayloadFlag)) {
        setResult(MString(errorResult("invalid_payload", "payload flag is required").dump().c_str()));
        return MS::kSuccess;
    }
    MString rawPayload;
    status = database.getFlagArgument(kPayloadFlag, 0, rawPayload);
    json payload;
    std::string parseError;
    if (!status || !parsePayload(rawPayload, payload, parseError) || !payload.is_object()) {
        setResult(MString(errorResult("invalid_payload", "payload must be strict JSON").dump().c_str()));
        return MS::kSuccess;
    }
    if (!payload.contains("version") || !payload["version"].is_number_integer() ||
        payload["version"].get<long long>() != kProtocolVersion ||
        !payload.contains("controller") || !payload["controller"].is_string() ||
        payload["controller"].get<std::string>().empty() ||
        !payload.contains("slot") || !payload["slot"].is_number_unsigned()) {
        setResult(MString(errorResult("invalid_payload", "version, controller, and slot are required").dump().c_str()));
        return MS::kSuccess;
    }
    const auto slotValue = payload["slot"].get<unsigned long long>();
    if (slotValue > static_cast<unsigned long long>(std::numeric_limits<unsigned int>::max())) {
        setResult(MString(errorResult("invalid_payload", "slot is out of range").dump().c_str()));
        return MS::kSuccess;
    }

    MSelectionList selection;
    const std::string requestedController = payload["controller"].get<std::string>();
    status = selection.add(MString(requestedController.c_str()));
    MObject controllerObject;
    if (!status || selection.length() != 1 || !selection.getDependNode(0, controllerObject)) {
        setResult(MString(errorResult("ambiguous_or_missing_controller", "controller has no unique Maya identity").dump().c_str()));
        return MS::kSuccess;
    }
    MFnDependencyNode controller(controllerObject, &status);
    MPlug outputArray = status ? controller.findPlug("outputWeight", true, &status) : MPlug();
    if (!status || !outputArray.isArray()) {
        setResult(MString(errorResult("missing_output_weight", "controller has no outputWeight array").dump().c_str()));
        return MS::kSuccess;
    }

    MPlugArray destinations;
    MPlugArray controllerConnections;
    status = controller.getConnections(controllerConnections);
    MPlug output;
    bool foundOutput = false;
    for (unsigned int index = 0; status && index < controllerConnections.length(); ++index) {
        MPlug candidate = controllerConnections[index];
        if (candidate.isElement() && candidate.array().attribute() == outputArray.attribute() &&
            candidate.logicalIndex(&status) == static_cast<unsigned int>(slotValue) && status) {
            output = candidate;
            foundOutput = true;
            break;
        }
    }
    if (status && foundOutput && !output.connectedTo(destinations, false, true, &status)) {
        status = MS::kFailure;
    }
    if (!status) {
        setResult(MString(errorResult("maya_query_failed", "could not read outputWeight destinations").dump().c_str()));
        return MS::kSuccess;
    }

    json result = {
        {"version", kProtocolVersion}, {"command", kCommand}, {"ok", true},
        {"requestedController", requestedController},
        {"controller", canonicalNodeName(controllerObject)}, {"slot", slotValue},
        {"destinations", json::array()}, {"blendShapes", json::array()},
    };
    std::unordered_set<std::string> observedBlendShapes;
    for (unsigned int index = 0; index < destinations.length(); ++index) {
        const MPlug destination = destinations[index];
        const MObject nodeObject = destination.node();
        MFnDependencyNode node(nodeObject, &status);
        if (!status) {
            setResult(MString(errorResult("maya_query_failed", "could not inspect destination node").dump().c_str()));
            return MS::kSuccess;
        }
        const std::string nodeName = canonicalNodeName(nodeObject);
        const std::string nodeType = utf8(node.typeName());
        const std::string plugName = nodeName + "." + utf8(destination.partialName(false, true, true, false, true, true));
        result["destinations"].push_back({{"node", nodeName}, {"nodeType", nodeType}, {"plug", plugName}});
        if (nodeType != "blendShape" || !observedBlendShapes.insert(nodeName).second) continue;

        json aliases = json::array();
        MPlug weights = node.findPlug("weight", true, &status);
        if (!status || !weights.isArray()) {
            setResult(MString(errorResult("maya_query_failed", "blendShape has no weight array").dump().c_str()));
            return MS::kSuccess;
        }
        for (unsigned int physical = 0; physical < weights.numElements(); ++physical) {
            MPlug weight = weights.elementByPhysicalIndex(physical, &status);
            const unsigned int logical = status ? weight.logicalIndex(&status) : 0;
            const MString alias = status ? node.plugsAlias(weight, &status) : MString();
            if (!status) {
                setResult(MString(errorResult("maya_query_failed", "could not inspect blendShape aliases").dump().c_str()));
                return MS::kSuccess;
            }
            if (alias.length()) {
                aliases.push_back({
                    {"alias", utf8(alias)},
                    {"plug", nodeName + ".weight[" + std::to_string(logical) + "]"},
                });
            }
        }
        json blendShape = {{"node", nodeName}, {"aliases", aliases}, {"rawNameMappingJson", nullptr}};
        MPlug rawMapping = node.findPlug(kRawNameAttribute, true, &status);
        if (status && !rawMapping.isNull()) {
            MString rawValue = rawMapping.asString(&status);
            if (!status) {
                setResult(MString(errorResult("maya_query_failed", "could not read raw-name mapping").dump().c_str()));
                return MS::kSuccess;
            }
            blendShape["rawNameMappingJson"] = utf8(rawValue);
        } else {
            status = MS::kSuccess;
        }
        result["blendShapes"].push_back(std::move(blendShape));
    }
    setResult(MString(result.dump().c_str()));
    return MS::kSuccess;
}
