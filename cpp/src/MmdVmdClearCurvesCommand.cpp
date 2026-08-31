#include "MmdVmdClearCurvesCommand.h"

#include <maya/MAnimUtil.h>
#include <maya/MArgDatabase.h>
#include <maya/MFnAnimCurve.h>
#include <maya/MFnDependencyNode.h>
#include <maya/MGlobal.h>
#include <maya/MIntArray.h>
#include <maya/MObjectArray.h>
#include <maya/MObjectHandle.h>
#include <maya/MPlug.h>
#include <maya/MSelectionList.h>
#include <maya/MString.h>

#include <cstdint>
#include <exception>
#include <string>
#include <unordered_set>
#include <vector>

#include "third_party/json.hpp"

namespace {
using json = nlohmann::json;

constexpr int kProtocolVersion = 1;
constexpr const char* kCommand = "mmdVmdClearCurves";
constexpr const char* kPayloadFlag = "-payload";

struct PlugRecord {
    std::string requested;
    std::string canonical;
    std::vector<std::size_t> curves;
};

struct CurveRecord {
    MObject object;
    MObjectHandle handle;
    std::string name;
    unsigned int keyCount = 0U;
    std::size_t ownerPlug = 0U;
    unsigned int removedCount = 0U;
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

json errorResult(const char* phase,
                 const std::string& code,
                 const std::string& message,
                 bool mutated,
                 const std::vector<std::string>& requestedPlugs,
                 std::size_t curveCount = 0U,
                 std::size_t removedCount = 0U)
{
    json plugResults = json::array();
    for (const std::string& plug : requestedPlugs) {
        plugResults.push_back({{"plug", plug}, {"removed_count", 0U}});
    }
    return {
        {"version", kProtocolVersion},
        {"command", kCommand},
        {"ok", false},
        {"phase", phase},
        {"mutated", mutated},
        {"plugs", plugResults},
        {"reason", code + ": " + message},
        {"curve_count", curveCount},
        {"removed_count", removedCount},
    };
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

bool parsePayload(const MString& raw, json& payload, std::string& error)
{
    bool duplicateKey = false;
    std::vector<std::unordered_set<std::string>> objectKeys;
    const auto rejectDuplicateKeys = [&duplicateKey, &objectKeys](
        int, json::parse_event_t event, json& parsed) {
        if (event == json::parse_event_t::object_start) {
            objectKeys.emplace_back();
        } else if (event == json::parse_event_t::key && !objectKeys.empty()) {
            duplicateKey = !objectKeys.back().insert(parsed.get<std::string>()).second ||
                duplicateKey;
        } else if (event == json::parse_event_t::object_end && !objectKeys.empty()) {
            objectKeys.pop_back();
        }
        return true;
    };

    try {
        payload = json::parse(utf8(raw), rejectDuplicateKeys);
    } catch (const std::exception& errorValue) {
        error = errorValue.what();
        return false;
    }
    if (duplicateKey) {
        error = "payload contains a duplicate object key";
        return false;
    }
    return true;
}

bool canonicalizePlug(const std::string& requested, MPlug& plug, std::string& canonical)
{
    if (requested.empty()) return false;
    MSelectionList selection;
    MStatus status = selection.add(fromUtf8(requested));
    if (!status || selection.length() != 1U || !selection.getPlug(0U, plug)) return false;
    canonical = utf8(plug.name(&status));
    return status && !canonical.empty() && !plug.isNull();
}

bool appendExpandedPlug(MPlug plug,
                        std::vector<MPlug>& leaves,
                        std::string& error)
{
    if (plug.isNull()) {
        error = "plug resolved to a null object";
        return false;
    }

    MStatus status;
    const bool isArray = plug.isArray(&status);
    if (!status) {
        error = "could not inspect plug array state";
        return false;
    }
    if (isArray && !plug.isElement(&status)) {
        if (!status) {
            error = "could not inspect plug element state";
            return false;
        }
        MIntArray logicalIndices;
        const unsigned int count = plug.getExistingArrayAttributeIndices(logicalIndices, &status);
        if (!status) {
            error = "could not enumerate existing array elements";
            return false;
        }
        for (unsigned int index = 0U; index < count; ++index) {
            MPlug element = plug.elementByLogicalIndex(logicalIndices[index], &status);
            if (!status || element.isNull()) {
                error = "could not resolve an existing array element";
                return false;
            }
            if (!appendExpandedPlug(element, leaves, error)) return false;
        }
        return true;
    }

    const bool isCompound = plug.isCompound(&status);
    if (!status) {
        error = "could not inspect plug compound state";
        return false;
    }
    if (isCompound) {
        const unsigned int childCount = plug.numChildren(&status);
        if (!status || childCount == 0U) {
            error = "compound plug has no children";
            return false;
        }
        for (unsigned int index = 0U; index < childCount; ++index) {
            MPlug child = plug.child(index, &status);
            if (!status || child.isNull()) {
                error = "could not resolve a compound child plug";
                return false;
            }
            if (!appendExpandedPlug(child, leaves, error)) return false;
        }
        return true;
    }

    leaves.push_back(plug);
    return true;
}

bool validAnimCurveType(MFnAnimCurve::AnimCurveType type)
{
    return type == MFnAnimCurve::kAnimCurveTA || type == MFnAnimCurve::kAnimCurveTL ||
        type == MFnAnimCurve::kAnimCurveTT || type == MFnAnimCurve::kAnimCurveTU ||
        type == MFnAnimCurve::kAnimCurveUA || type == MFnAnimCurve::kAnimCurveUL ||
        type == MFnAnimCurve::kAnimCurveUT || type == MFnAnimCurve::kAnimCurveUU;
}

bool inspectCurve(const MObject& object, CurveRecord& record, std::string& code, std::string& message)
{
    MObjectHandle handle(object);
    if (!handle.isValid() || !handle.isAlive()) {
        code = "invalid_curve";
        message = "animation curve is no longer valid";
        return false;
    }
    if (!object.hasFn(MFn::kAnimCurve)) {
        code = "unsupported_animation";
        message = "MAnimUtil returned a non-animCurve object";
        return false;
    }

    MStatus status;
    MFnAnimCurve curve(object, &status);
    if (!status) {
        code = "invalid_curve";
        message = "MFnAnimCurve could not bind to the animation curve";
        return false;
    }
    const MFnAnimCurve::AnimCurveType curveType = curve.animCurveType(&status);
    if (!status || !validAnimCurveType(curveType)) {
        code = "unsupported_curve_type";
        message = "animation curve has an unknown type";
        return false;
    }

    MFnDependencyNode node(object, &status);
    if (!status) {
        code = "invalid_curve";
        message = "animation curve is not a valid dependency node";
        return false;
    }
    const bool referenced = node.isFromReferencedFile(&status);
    if (!status) {
        code = "curve_inspection_failed";
        message = "could not inspect animation curve reference state";
        return false;
    }
    if (referenced) {
        code = "referenced_curve";
        message = "animation curve belongs to a referenced file";
        return false;
    }
    const bool locked = node.isLocked(&status);
    if (!status) {
        code = "curve_inspection_failed";
        message = "could not inspect animation curve lock state";
        return false;
    }
    if (locked) {
        code = "locked_curve";
        message = "animation curve node is locked";
        return false;
    }

    MPlug input = node.findPlug("input", false, &status);
    MPlug output = node.findPlug("output", false, &status);
    if (!status || input.isNull() || output.isNull()) {
        code = "invalid_curve";
        message = "animation curve has no usable input/output plugs";
        return false;
    }
    if (input.isLocked(&status) || !status || output.isLocked(&status) || !status) {
        code = "locked_curve";
        message = "animation curve input/output is locked";
        return false;
    }

    const unsigned int keyCount = curve.numKeys(&status);
    if (!status) {
        code = "curve_inspection_failed";
        message = "could not read animation curve key count";
        return false;
    }

    MString curveName = node.name(&status);
    if (!status || curveName.length() == 0U) {
        code = "invalid_curve";
        message = "animation curve has no stable Maya name";
        return false;
    }
    record.object = object;
    record.handle = handle;
    record.name = utf8(curveName);
    record.keyCount = keyCount;
    return true;
}

std::size_t findCurve(const std::vector<CurveRecord>& curves, const MObject& object)
{
    const MObjectHandle handle(object);
    for (std::size_t index = 0U; index < curves.size(); ++index) {
        if (curves[index].handle == handle) return index;
    }
    return curves.size();
}

}  // namespace

void* MmdVmdClearCurvesCommand::creator()
{
    return new MmdVmdClearCurvesCommand();
}

MSyntax MmdVmdClearCurvesCommand::newSyntax()
{
    MSyntax syntax;
    syntax.addFlag("-p", kPayloadFlag, MSyntax::kString);
    syntax.enableEdit(false);
    syntax.enableQuery(false);
    return syntax;
}

MStatus MmdVmdClearCurvesCommand::doIt(const MArgList& args)
{
    std::vector<std::string> requestedPlugs;
    const auto finishError = [this, &requestedPlugs](const char* phase,
                                    const std::string& code,
                                    const std::string& message,
                                    bool mutated,
                                    std::size_t curveCount = 0U,
                                    std::size_t removedCount = 0U) {
        setResult(fromUtf8(
            errorResult(
                phase, code, message, mutated, requestedPlugs, curveCount, removedCount)
                .dump()));
        return MS::kSuccess;
    };

    MStatus status;
    MArgDatabase database(newSyntax(), args, &status);
    if (!status || !database.isFlagSet(kPayloadFlag)) {
        return finishError("prepare", "invalid_arguments", "-payload is required", false);
    }
    MString rawPayload;
    status = database.getFlagArgument(kPayloadFlag, 0U, rawPayload);
    if (!status) {
        return finishError("prepare", "invalid_arguments", "-payload must be one JSON string", false);
    }

    json payload;
    std::string parseError;
    if (!parsePayload(rawPayload, payload, parseError)) {
        return finishError("prepare", "invalid_json", parseError, false);
    }
    if (!payload.is_object() || payload.size() != 2U || !payload.contains("version") ||
        !payload.contains("plugs") || !exactVersion(payload["version"]) ||
        !payload["plugs"].is_array()) {
        return finishError(
            "prepare", "invalid_payload", "payload must contain only version=1 and plugs", false);
    }

    for (const json& value : payload["plugs"]) {
        if (!value.is_string() || value.get<std::string>().empty()) {
            return finishError("prepare", "invalid_plug", "each plugs entry must be a non-empty string", false);
        }
        requestedPlugs.push_back(value.get<std::string>());
    }

    std::vector<PlugRecord> plugs;
    std::vector<CurveRecord> curves;
    std::unordered_set<std::string> canonicalPlugs;
    for (const std::string& requested : requestedPlugs) {
        MPlug resolved;
        std::string canonical;
        if (!canonicalizePlug(requested, resolved, canonical)) {
            return finishError("prepare", "ambiguous_or_missing_plug", requested, false);
        }
        if (!canonicalPlugs.insert(canonical).second) {
            return finishError("prepare", "duplicate_plug", canonical, false);
        }

        std::vector<MPlug> leaves;
        std::string expansionError;
        if (!appendExpandedPlug(resolved, leaves, expansionError)) {
            return finishError("prepare", "plug_expansion_failed", canonical + ": " + expansionError, false);
        }

        PlugRecord plugRecord;
        plugRecord.requested = requested;
        plugRecord.canonical = canonical;
        std::unordered_set<std::string> leafNames;
        std::unordered_set<std::size_t> localCurves;
        for (const MPlug& leaf : leaves) {
            MStatus leafStatus;
            const std::string leafName = utf8(leaf.name(&leafStatus));
            if (!leafStatus || leafName.empty() || !leafNames.insert(leafName).second) continue;
            if (leaf.isLocked(&leafStatus) || !leafStatus || leaf.isFromReferencedFile(&leafStatus) ||
                !leafStatus) {
                return finishError("prepare", "plug_not_mutable", leafName, false);
            }

            MObjectArray animation;
            MStatus animationStatus;
            const bool animated = MAnimUtil::findAnimation(leaf, animation, &animationStatus);
            if (!animationStatus) {
                return finishError("prepare", "animation_lookup_failed", leafName, false);
            }
            if (!animated || animation.length() == 0U) continue;

            for (unsigned int animationIndex = 0U; animationIndex < animation.length(); ++animationIndex) {
                CurveRecord candidate;
                std::string curveCode;
                std::string curveMessage;
                if (!inspectCurve(animation[animationIndex], candidate, curveCode, curveMessage)) {
                    return finishError("prepare", curveCode, leafName + ": " + curveMessage, false);
                }
                const std::size_t curveIndex = findCurve(curves, candidate.object);
                if (curveIndex != curves.size()) {
                    if (curves[curveIndex].ownerPlug != plugs.size()) {
                        return finishError(
                            "prepare", "ambiguous_curve", candidate.name + " is attached to multiple plugs", false);
                    }
                    if (localCurves.insert(curveIndex).second) plugRecord.curves.push_back(curveIndex);
                    continue;
                }
                candidate.ownerPlug = plugs.size();
                curves.push_back(std::move(candidate));
                const std::size_t newIndex = curves.size() - 1U;
                localCurves.insert(newIndex);
                plugRecord.curves.push_back(newIndex);
            }
        }
        plugs.push_back(std::move(plugRecord));
    }

    std::size_t totalRemoved = 0U;
    for (CurveRecord& record : curves) {
        MStatus curveStatus;
        MFnAnimCurve curve(record.object, &curveStatus);
        if (!curveStatus) {
            return finishError("mutation", "curve_bind_failed", record.name, true, curves.size(), totalRemoved);
        }
        unsigned int index = record.keyCount;
        while (index > 0U) {
            --index;
            curveStatus = curve.remove(index);
            if (!curveStatus) {
                return finishError(
                    "mutation", "curve_remove_failed", record.name, true, curves.size(), totalRemoved);
            }
            ++record.removedCount;
            ++totalRemoved;
        }
    }

    json plugResults = json::array();
    for (const PlugRecord& plug : plugs) {
        unsigned int removed = 0U;
        for (const std::size_t curveIndex : plug.curves) removed += curves[curveIndex].removedCount;
        plugResults.push_back({
            {"plug", plug.requested},
            {"removed_count", removed},
        });
    }
    setResult(fromUtf8(json({
        {"version", kProtocolVersion},
        {"command", kCommand},
        {"ok", true},
        {"phase", "complete"},
        {"mutated", totalRemoved != 0U},
        {"plugs", plugResults},
        {"curve_count", curves.size()},
        {"removed_count", totalRemoved},
        {"reason", ""},
    }).dump()));
    return MS::kSuccess;
}

bool MmdVmdClearCurvesCommand::isUndoable() const
{
    return false;
}
