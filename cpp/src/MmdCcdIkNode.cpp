/**
 * MmdCcdIkNode の実装
 *
 * Phase A (1-link) + Phase B (multi-link 2D CCD)。
 * - 既存の 1-link 解析解は既存属性の挙動と互換。
 * - inputChain が有効な場合のみ multi-link 2D CCD を実行し、
 *   outputLinkAngles / outputLinkRotates を更新。
 */

#include "MmdCcdIkNode.h"

#include <maya/MFnAttribute.h>
#include <maya/MFnNumericAttribute.h>
#include <maya/MFnCompoundAttribute.h>
#include <maya/MFnTypedAttribute.h>
#include <maya/MFnStringData.h>
#include <maya/MFnUnitAttribute.h>
#include <maya/MFnMatrixAttribute.h>
#include <maya/MFnDoubleArrayData.h>
#include <maya/MDoubleArray.h>
#include <maya/MDataHandle.h>
#include <maya/MArrayDataBuilder.h>
#include <maya/MArrayDataHandle.h>
#include <maya/MDagPath.h>
#include <maya/MFnDagNode.h>
#include <maya/MPlug.h>
#include <maya/MPlugArray.h>
#include <maya/MGlobal.h>
#include <maya/MAngle.h>
#include <maya/MEulerRotation.h>
#include <maya/MMatrix.h>
#include <maya/MQuaternion.h>
#include <maya/MTransformationMatrix.h>
#include <maya/MVector.h>

#include "mmd_runtime.h"
#include "third_party/json.hpp"

#include <array>
#include <cmath>
#include <cstdint>
#include <utility>
#include <string>
#include <vector>

#ifdef _WIN32
#include <windows.h>
#elif defined(__APPLE__)
#include <dlfcn.h>
#endif

namespace {
constexpr double kPi = 3.14159265358979323846;
using nlohmann::json;

struct CcdIkChainConfig;

using IkChainCreateV2Fn = mmd_runtime_ik_chain_t* (*)(
    const mmd_runtime_ffi_rig_bone_t*,
    size_t,
    const mmd_runtime_ffi_rig_bone_local_axis_v2_t*,
    uint32_t,
    const mmd_runtime_ffi_rig_ik_link_t*,
    size_t,
    uint32_t,
    float);

// CcdIkChainConfig に対応する native chain を一度だけ生成する。
// chain の所有権は MmdCcdIkNode::ChainCache に移され、solve ごとには解放しない。
mmd_runtime_ik_chain_t* createNativeIkChain(const CcdIkChainConfig& cfg);

IkChainCreateV2Fn resolveIkChainCreateV2()
{
#ifdef _WIN32
    HMODULE ownerModule = nullptr;
    if (!GetModuleHandleExA(
            GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
            reinterpret_cast<LPCSTR>(&mmd_runtime_ik_chain_create),
            &ownerModule) ||
        !ownerModule) {
        return nullptr;
    }
    return reinterpret_cast<IkChainCreateV2Fn>(
        GetProcAddress(ownerModule, "mmd_runtime_ik_chain_create_v2"));
#elif defined(__APPLE__)
    Dl_info ownerInfo{};
    if (dladdr(reinterpret_cast<const void*>(&mmd_runtime_ik_chain_create), &ownerInfo) == 0 ||
        !ownerInfo.dli_fname) {
        return nullptr;
    }
    void* ownerModule = dlopen(ownerInfo.dli_fname, RTLD_LAZY | RTLD_NOLOAD);
    if (!ownerModule) {
        return nullptr;
    }
    auto fn = reinterpret_cast<IkChainCreateV2Fn>(
        dlsym(ownerModule, "mmd_runtime_ik_chain_create_v2"));
    dlclose(ownerModule);
    return fn;
#endif
    return nullptr;
}

struct CcdIkChainConfig {
    std::vector<mmd_runtime_ffi_rig_bone_t> bones;
    std::vector<mmd_runtime_ffi_rig_bone_local_axis_v2_t> localAxes;
    std::vector<mmd_runtime_ffi_rig_ik_link_t> links;
    std::vector<std::array<double, 3>> restPositions;
    std::vector<std::array<double, 3>> mayaRestTranslates;
    std::vector<int32_t> parentSlots;
    std::vector<MQuaternion> jointOrients;
    std::vector<bool> hasJointOrient;
    std::vector<MMatrix> mayaBindWorldMatrices;
    std::vector<MMatrix> noOrientBindWorldMatrices;
    std::vector<uint32_t> linkSlots;
    bool hasBindMatrices = false;
    bool hasLocalAxes = false;
    int32_t controllerBoneSlot = -1;
    uint32_t targetBoneSlot = 0;
    uint32_t iterationCount = 40;
    float limitAngle = 0.0628f;
};

mmd_runtime_ik_chain_t* createNativeIkChain(const CcdIkChainConfig& cfg)
{
    const IkChainCreateV2Fn createV2 = cfg.hasLocalAxes ? resolveIkChainCreateV2() : nullptr;
    return createV2
        ? createV2(
              cfg.bones.data(),
              cfg.bones.size(),
              cfg.localAxes.data(),
              cfg.targetBoneSlot,
              cfg.links.data(),
              cfg.links.size(),
              cfg.iterationCount,
              cfg.limitAngle)
        : mmd_runtime_ik_chain_create(
              cfg.bones.data(),
              cfg.bones.size(),
              cfg.targetBoneSlot,
              cfg.links.data(),
              cfg.links.size(),
              cfg.iterationCount,
              cfg.limitAngle);
}

bool readJsonVec3(const json& obj, const char* key, std::array<float, 3>& out, const std::array<float, 3>& fallback)
{
    out = fallback;
    auto it = obj.find(key);
    if (it == obj.end() || !it->is_array() || it->size() < 3) {
        return true;
    }
    for (size_t i = 0; i < 3; ++i) {
        if (!(*it)[i].is_number()) {
            return false;
        }
        out[i] = (*it)[i].get<float>();
    }
    return true;
}

bool readJsonVec3Double(const json& obj, const char* key, std::array<double, 3>& out, const std::array<double, 3>& fallback)
{
    out = fallback;
    auto it = obj.find(key);
    if (it == obj.end() || !it->is_array() || it->size() < 3) {
        return true;
    }
    for (size_t i = 0; i < 3; ++i) {
        if (!(*it)[i].is_number()) {
            return false;
        }
        out[i] = (*it)[i].get<double>();
    }
    return true;
}

bool readJsonMatrix(const json& obj, const char* key, MMatrix& out)
{
    auto it = obj.find(key);
    if (it == obj.end() || it->is_null()) {
        return false;
    }
    if (!it->is_array() || it->size() < 16) {
        return false;
    }
    double values[4][4]{};
    for (size_t row = 0; row < 4; ++row) {
        for (size_t col = 0; col < 4; ++col) {
            const json& value = (*it)[row * 4 + col];
            if (!value.is_number()) {
                return false;
            }
            values[row][col] = value.get<double>();
        }
    }
    out = MMatrix(values);
    return true;
}

MQuaternion jointOrientFromDegrees(const std::array<double, 3>& degrees)
{
    return MEulerRotation(
        degrees[0] * kPi / 180.0,
        degrees[1] * kPi / 180.0,
        degrees[2] * kPi / 180.0).asQuaternion();
}

MMatrix mmdWorldToMaya(const MMatrix& matrix)
{
    const double signs[3] = {1.0, 1.0, -1.0};
    MMatrix result(matrix);
    for (unsigned int row = 0; row < 3; ++row) {
        for (unsigned int col = 0; col < 3; ++col) {
            result(row, col) = matrix(row, col) * signs[row] * signs[col];
        }
    }
    for (unsigned int col = 0; col < 3; ++col) {
        result(3, col) = matrix(3, col) * signs[col];
    }
    return result;
}

MMatrix mayaWorldToMmd(const MMatrix& matrix)
{
    return mmdWorldToMaya(matrix);
}

bool connectedGoalModelRootWorldMatrix(
    const MObject& node,
    const MObject& goalWorldMatrixAttr,
    MMatrix& outRootWorld)
{
    MPlug goalPlug(node, goalWorldMatrixAttr);
    MPlugArray sources;
    if (!goalPlug.connectedTo(sources, true, false) || sources.length() == 0) {
        return false;
    }

    for (unsigned int sourceIndex = 0; sourceIndex < sources.length(); ++sourceIndex) {
        MDagPath path;
        MStatus status = MDagPath::getAPathTo(sources[sourceIndex].node(), path);
        if (status != MS::kSuccess) {
            continue;
        }

        // The solver inputs are root-relative, while goalWorldMatrix is a
        // world-space controller value.  Strip only the imported model root
        // transform (matching the Python prototype); arbitrary top-level
        // locators deliberately remain in world space.
        for (unsigned int depth = path.length(); depth > 1; --depth) {
            if (path.pop() != MS::kSuccess) {
                break;
            }
            MFnDagNode dagNode(path, &status);
            if (status != MS::kSuccess) {
                continue;
            }
            std::string leaf = dagNode.name(&status).asChar();
            if (status != MS::kSuccess) {
                continue;
            }
            for (char& value : leaf) {
                if (value >= 'A' && value <= 'Z') {
                    value = static_cast<char>(value - 'A' + 'a');
                }
            }
            if (leaf.size() >= 4 &&
                leaf.compare(leaf.size() - 4, 4, "root") == 0) {
                outRootWorld = path.inclusiveMatrix();
                return true;
            }
        }
    }
    return false;
}

bool parseCcdIkChainJson(const MString& chainJson, CcdIkChainConfig& cfg)
{
    const std::string text = chainJson.asChar();
    if (text.empty()) {
        return false;
    }

    json root;
    try {
        root = json::parse(text);
    } catch (const json::exception&) {
        return false;
    }

    if (!root.is_object()) {
        return false;
    }
    const auto bonesIt = root.find("bones");
    const auto linksIt = root.find("links");
    if (bonesIt == root.end() || linksIt == root.end() || !bonesIt->is_array() || !linksIt->is_array()) {
        return false;
    }
    if (bonesIt->empty() || linksIt->empty()) {
        return false;
    }

    cfg = CcdIkChainConfig{};
    cfg.bones.reserve(bonesIt->size());
    cfg.localAxes.reserve(bonesIt->size());
    cfg.restPositions.reserve(bonesIt->size());
    cfg.mayaRestTranslates.reserve(bonesIt->size());
    cfg.parentSlots.reserve(bonesIt->size());
    cfg.jointOrients.reserve(bonesIt->size());
    cfg.hasJointOrient.reserve(bonesIt->size());
    cfg.mayaBindWorldMatrices.reserve(bonesIt->size());
    cfg.noOrientBindWorldMatrices.reserve(bonesIt->size());
    bool allBindMatrices = true;

    for (const json& boneJson : *bonesIt) {
        if (!boneJson.is_object()) {
            return false;
        }
        mmd_runtime_ffi_rig_bone_t bone{};
        bone.parent_slot = boneJson.value("parent_slot", -1);
        bone.flags = boneJson.value("flags", 0u);

        std::array<float, 3> rest{0.0f, 0.0f, 0.0f};
        if (!readJsonVec3(boneJson, "rest_position", rest, rest)) {
            return false;
        }
        for (size_t axis = 0; axis < 3; ++axis) {
            bone.rest_position_xyz[axis] = rest[axis];
        }
        cfg.restPositions.push_back({
            static_cast<double>(rest[0]),
            static_cast<double>(rest[1]),
            static_cast<double>(rest[2]),
        });

        std::array<float, 3> fixedAxis{0.0f, 0.0f, 0.0f};
        if (!readJsonVec3(boneJson, "fixed_axis", fixedAxis, fixedAxis)) {
            return false;
        }
        for (size_t axis = 0; axis < 3; ++axis) {
            bone.fixed_axis_xyz[axis] = fixedAxis[axis];
        }

        mmd_runtime_ffi_rig_bone_local_axis_v2_t localAxis{};
        const auto localAxisIt = boneJson.find("local_axis");
        if (localAxisIt != boneJson.end() && !localAxisIt->is_null()) {
            if (!localAxisIt->is_object() || localAxisIt->size() != 2 ||
                !localAxisIt->contains("x") || !localAxisIt->contains("z")) {
                return false;
            }
            const json& axisXJson = localAxisIt->at("x");
            const json& axisZJson = localAxisIt->at("z");
            if (!axisXJson.is_array() || axisXJson.size() != 3 ||
                !axisZJson.is_array() || axisZJson.size() != 3) {
                return false;
            }
            for (size_t axis = 0; axis < 3; ++axis) {
                if (!axisXJson[axis].is_number() || !axisZJson[axis].is_number()) {
                    return false;
                }
                const float axisX = axisXJson[axis].get<float>();
                const float axisZ = axisZJson[axis].get<float>();
                if (!std::isfinite(axisX) || !std::isfinite(axisZ)) {
                    return false;
                }
                localAxis.local_axis_x_xyz[axis] = axisX;
                localAxis.local_axis_z_xyz[axis] = axisZ;
            }
            localAxis.has_local_axis = true;
            cfg.hasLocalAxes = true;
        }
        cfg.localAxes.push_back(localAxis);

        std::array<double, 3> mayaRest{
            static_cast<double>(rest[0]),
            static_cast<double>(rest[1]),
            static_cast<double>(rest[2]),
        };
        if (!readJsonVec3Double(boneJson, "maya_rest_translate", mayaRest, mayaRest)) {
            return false;
        }

        cfg.bones.push_back(bone);
        cfg.mayaRestTranslates.push_back(mayaRest);
        cfg.parentSlots.push_back(bone.parent_slot);

        std::array<double, 3> joDegrees{0.0, 0.0, 0.0};
        if (!readJsonVec3Double(boneJson, "joint_orient_deg", joDegrees, joDegrees)) {
            return false;
        }
        const bool hasJo = std::abs(joDegrees[0]) > 1.0e-8 ||
                           std::abs(joDegrees[1]) > 1.0e-8 ||
                           std::abs(joDegrees[2]) > 1.0e-8;
        cfg.jointOrients.push_back(jointOrientFromDegrees(joDegrees));
        cfg.hasJointOrient.push_back(hasJo);

        MMatrix bindWorld;
        MMatrix noOrientBindWorld;
        const bool hasBindWorld = readJsonMatrix(boneJson, "maya_bind_world_matrix", bindWorld);
        const bool hasNoOrientBindWorld = readJsonMatrix(boneJson, "no_orient_bind_world_matrix", noOrientBindWorld);
        if (hasBindWorld && hasNoOrientBindWorld) {
            cfg.mayaBindWorldMatrices.push_back(bindWorld);
            cfg.noOrientBindWorldMatrices.push_back(noOrientBindWorld);
        } else {
            cfg.mayaBindWorldMatrices.push_back(MMatrix::identity);
            cfg.noOrientBindWorldMatrices.push_back(MMatrix::identity);
            allBindMatrices = false;
        }
    }
    cfg.hasBindMatrices = allBindMatrices && cfg.mayaBindWorldMatrices.size() == cfg.bones.size() &&
                          cfg.noOrientBindWorldMatrices.size() == cfg.bones.size();

    cfg.links.reserve(linksIt->size());
    cfg.linkSlots.reserve(linksIt->size());
    for (const json& linkJson : *linksIt) {
        if (!linkJson.is_object() || !linkJson.contains("bone_slot")) {
            return false;
        }
        mmd_runtime_ffi_rig_ik_link_t link{};
        link.bone_slot = linkJson.at("bone_slot").get<uint32_t>();
        if (link.bone_slot >= cfg.bones.size()) {
            return false;
        }
        link.has_angle_limit = linkJson.value("has_angle_limit", false);

        std::array<float, 3> limitMin{0.0f, 0.0f, 0.0f};
        std::array<float, 3> limitMax{0.0f, 0.0f, 0.0f};
        if (!readJsonVec3(linkJson, "angle_limit_min", limitMin, limitMin) ||
            !readJsonVec3(linkJson, "angle_limit_max", limitMax, limitMax)) {
            return false;
        }
        for (size_t axis = 0; axis < 3; ++axis) {
            link.angle_limit_min_xyz[axis] = limitMin[axis];
            link.angle_limit_max_xyz[axis] = limitMax[axis];
        }

        cfg.links.push_back(link);
        cfg.linkSlots.push_back(link.bone_slot);
    }

    cfg.targetBoneSlot = root.value("targetBoneSlot", 0u);
    if (cfg.targetBoneSlot >= cfg.bones.size()) {
        return false;
    }
    cfg.controllerBoneSlot = root.value("controllerBoneSlot", -1);
    cfg.iterationCount = root.value("iterationCount", 40u);
    cfg.limitAngle = root.value("limitAngle", 0.0628f);
    return true;
}

bool plugOrChildrenHasInputConnection(const MObject& node, const MObject& attr)
{
    MPlug plug(node, attr);
    MPlugArray connections;
    if (plug.connectedTo(connections, true, false) && connections.length() > 0) {
        return true;
    }
    const unsigned int childCount = plug.numChildren();
    for (unsigned int i = 0; i < childCount; ++i) {
        MPlug child = plug.child(i);
        connections.clear();
        if (child.connectedTo(connections, true, false) && connections.length() > 0) {
            return true;
        }
    }
    return false;
}

bool plugIsOutputRotate(const MPlug& plug, const MObject& arrayAttr)
{
    MPlug current = plug;
    for (int depth = 0; depth < 4; ++depth) {
        if (current.attribute() == arrayAttr) {
            return true;
        }
        if (current.isElement()) {
            current = current.array();
            continue;
        }
        if (current.isChild()) {
            current = current.parent();
            continue;
        }
        break;
    }
    return false;
}

void setOutputRotateElementZero(
    MDataBlock& data,
    const MObject& arrayAttr,
    const MObject& childX,
    const MObject& childY,
    const MObject& childZ,
    double outXDeg,
    double outYDeg,
    double outZDeg)
{
    MStatus status;
    MArrayDataHandle outArray = data.outputArrayValue(arrayAttr, &status);
    // 新しい builder で置き換え、chainJson のリンク数が減った際に旧要素を
    // 残さない。builder() は既存要素を引き継ぐため、output shape の stale
    // を許してしまう。
    MArrayDataBuilder builder(&data, arrayAttr, 1, &status);
    MDataHandle elem = builder.addElement(0, &status);
    elem.child(childX).setMAngle(MAngle(outXDeg * kPi / 180.0, MAngle::kRadians));
    elem.child(childY).setMAngle(MAngle(outYDeg * kPi / 180.0, MAngle::kRadians));
    elem.child(childZ).setMAngle(MAngle(outZDeg * kPi / 180.0, MAngle::kRadians));
    outArray.set(builder);
    outArray.setAllClean();
}

void setOutputRotateElements(
    MDataBlock& data,
    const MObject& arrayAttr,
    const MObject& childX,
    const MObject& childY,
    const MObject& childZ,
    const std::vector<std::array<double, 3>>& radians)
{
    MStatus status;
    MArrayDataHandle outArray = data.outputArrayValue(arrayAttr, &status);
    MArrayDataBuilder builder(
        &data,
        arrayAttr,
        static_cast<unsigned int>(radians.size()),
        &status);
    for (size_t i = 0; i < radians.size(); ++i) {
        MDataHandle elem = builder.addElement(static_cast<unsigned int>(i), &status);
        elem.child(childX).setMAngle(MAngle(radians[i][0], MAngle::kRadians));
        elem.child(childY).setMAngle(MAngle(radians[i][1], MAngle::kRadians));
        elem.child(childZ).setMAngle(MAngle(radians[i][2], MAngle::kRadians));
    }
    outArray.set(builder);
    outArray.setAllClean();
}

bool readInputTranslateElement(
    const MArrayDataHandle& inputArray,
    unsigned int logicalIndex,
    const MObject& childX,
    const MObject& childY,
    const MObject& childZ,
    std::array<double, 3>& out)
{
    MStatus status;
    MArrayDataHandle array = inputArray;
    status = array.jumpToElement(logicalIndex);
    if (status != MS::kSuccess) {
        return false;
    }
    MDataHandle elem = array.inputValue(&status);
    if (status != MS::kSuccess) {
        return false;
    }
    out = {
        elem.child(childX).asDouble(),
        elem.child(childY).asDouble(),
        elem.child(childZ).asDouble(),
    };
    return true;
}

bool readInputRotateElement(
    const MArrayDataHandle& inputArray,
    unsigned int logicalIndex,
    const MObject& childX,
    const MObject& childY,
    const MObject& childZ,
    std::array<double, 3>& outRadians)
{
    MStatus status;
    MArrayDataHandle array = inputArray;
    status = array.jumpToElement(logicalIndex);
    if (status != MS::kSuccess) {
        return false;
    }
    MDataHandle elem = array.inputValue(&status);
    if (status != MS::kSuccess) {
        return false;
    }
    outRadians = {
        elem.child(childX).asAngle().asRadians(),
        elem.child(childY).asAngle().asRadians(),
        elem.child(childZ).asAngle().asRadians(),
    };
    return true;
}

// FK target と goal の一致判定 (MMD units)。VMD bake の Euler/animCurve
// 丸め誤差より十分大きく、目視で分かる足のズレより十分小さい値。
constexpr float kGoalMatchEpsilon = 1.0e-3f;

// input pose だけから指定 slot の FK world 位置 (MMD space) を得る。
std::array<float, 3> computeFkWorldPosition(
    const CcdIkChainConfig& cfg,
    const std::vector<float>& positions,
    const std::vector<float>& rotations,
    int32_t slot)
{
    std::array<float, 3> goal{0.0f, 0.0f, 0.0f};
    if (slot < 0 || static_cast<size_t>(slot) >= cfg.bones.size()) {
        return goal;
    }

    std::vector<MMatrix> worldMats(cfg.bones.size());
    for (size_t boneIndex = 0; boneIndex < cfg.bones.size(); ++boneIndex) {
        const auto& rest = cfg.restPositions[boneIndex];
        MTransformationMatrix localTfm;
        localTfm.setTranslation(
            MVector(
                rest[0] + static_cast<double>(positions[boneIndex * 3]),
                rest[1] + static_cast<double>(positions[boneIndex * 3 + 1]),
                rest[2] + static_cast<double>(positions[boneIndex * 3 + 2])),
            MSpace::kTransform);
        const size_t qOffset = boneIndex * 4;
        localTfm.setRotationQuaternion(
            static_cast<double>(rotations[qOffset]),
            static_cast<double>(rotations[qOffset + 1]),
            static_cast<double>(rotations[qOffset + 2]),
            static_cast<double>(rotations[qOffset + 3]));
        MMatrix localMatrix = localTfm.asMatrix();
        const int32_t parent = cfg.parentSlots[boneIndex];
        worldMats[boneIndex] = localMatrix;
        if (parent >= 0 && static_cast<size_t>(parent) < boneIndex) {
            worldMats[boneIndex] = localMatrix * worldMats[static_cast<size_t>(parent)];
        }
    }

    MVector point = MTransformationMatrix(worldMats[static_cast<size_t>(slot)])
                        .getTranslation(MSpace::kWorld);
    goal[0] = static_cast<float>(point.x);
    goal[1] = static_cast<float>(point.y);
    goal[2] = static_cast<float>(point.z);
    return goal;
}

// Pass-through: link slot の inputRotate 値をそのまま出力へコピーする。
void copyInputRotateLinksToOutput(
    const CcdIkChainConfig& cfg,
    MDataBlock& data,
    std::vector<std::array<double, 3>>& outEulerRadians)
{
    MStatus status;
    MArrayDataHandle rotateArray = data.inputArrayValue(MmdCcdIkNode::aInputRotateArray, &status);
    outEulerRadians.clear();
    outEulerRadians.reserve(cfg.linkSlots.size());
    for (uint32_t slot : cfg.linkSlots) {
        std::array<double, 3> eulerRadians{0.0, 0.0, 0.0};
        readInputRotateElement(
            rotateArray,
            slot,
            MmdCcdIkNode::aInputRotateArrayX,
            MmdCcdIkNode::aInputRotateArrayY,
            MmdCcdIkNode::aInputRotateArrayZ,
            eulerRadians);
        outEulerRadians.push_back(eulerRadians);
    }
}

std::array<float, 3> readGoalPositionMmd(
    const CcdIkChainConfig& cfg,
    const MObject& node,
    MDataBlock& data,
    bool useGoalWorldMatrix)
{
    MMatrix goalMatrix;
    if (useGoalWorldMatrix) {
        goalMatrix = data.inputValue(MmdCcdIkNode::aGoalWorldMatrix).asMatrix();
    } else {
        const double* goal = data.inputValue(MmdCcdIkNode::aGoal).asDouble3();
        MTransformationMatrix goalTfm;
        goalTfm.setTranslation(MVector(goal[0], goal[1], goal[2]), MSpace::kTransform);
        goalMatrix = goalTfm.asMatrix();
    }

    if (useGoalWorldMatrix) {
        MMatrix rootWorld;
        if (connectedGoalModelRootWorldMatrix(
                node,
                MmdCcdIkNode::aGoalWorldMatrix,
                rootWorld)) {
            goalMatrix = goalMatrix * rootWorld.inverse();
        }
    }

    if (cfg.hasBindMatrices &&
        cfg.controllerBoneSlot >= 0 &&
        static_cast<size_t>(cfg.controllerBoneSlot) < cfg.bones.size()) {
        const size_t slot = static_cast<size_t>(cfg.controllerBoneSlot);
        goalMatrix = cfg.noOrientBindWorldMatrices[slot] *
                     cfg.mayaBindWorldMatrices[slot].inverse() *
                     goalMatrix;
    }

    MTransformationMatrix mmdGoalTfm(mayaWorldToMmd(goalMatrix));
    MVector goal = mmdGoalTfm.getTranslation(MSpace::kWorld);
    return {
        static_cast<float>(goal.x),
        static_cast<float>(goal.y),
        static_cast<float>(goal.z),
    };
}

bool solveChainJsonIk(
    const CcdIkChainConfig& cfg,
    mmd_runtime_ik_chain_t* chain,
    const MObject& node,
    MDataBlock& data,
    bool useGoalWorldMatrix,
    bool goalHasInputConnection,
    bool& outSolved,
    std::vector<std::array<double, 3>>& outEulerRadians)
{
    if (!chain) {
        return false;
    }

    const size_t boneCount = cfg.bones.size();
    std::vector<float> positions(boneCount * 3, 0.0f);
    std::vector<float> rotations(boneCount * 4, 0.0f);
    std::vector<std::array<double, 3>> mayaTranslates(boneCount);
    std::vector<MEulerRotation> mayaRotateEulers(boneCount);

    MStatus status;
    MArrayDataHandle translateArray = data.inputArrayValue(MmdCcdIkNode::aInputTranslateArray, &status);
    MArrayDataHandle rotateArray = data.inputArrayValue(MmdCcdIkNode::aInputRotateArray, &status);

    for (size_t boneIndex = 0; boneIndex < boneCount; ++boneIndex) {
        std::array<double, 3> translate = cfg.mayaRestTranslates[boneIndex];
        readInputTranslateElement(
            translateArray,
            static_cast<unsigned int>(boneIndex),
            MmdCcdIkNode::aInputTranslateArrayX,
            MmdCcdIkNode::aInputTranslateArrayY,
            MmdCcdIkNode::aInputTranslateArrayZ,
            translate);
        mayaTranslates[boneIndex] = translate;

        std::array<double, 3> eulerRadians{0.0, 0.0, 0.0};
        readInputRotateElement(
            rotateArray,
            static_cast<unsigned int>(boneIndex),
            MmdCcdIkNode::aInputRotateArrayX,
            MmdCcdIkNode::aInputRotateArrayY,
            MmdCcdIkNode::aInputRotateArrayZ,
            eulerRadians);
        mayaRotateEulers[boneIndex] = MEulerRotation(eulerRadians[0], eulerRadians[1], eulerRadians[2]);
    }

    if (cfg.hasBindMatrices) {
        std::vector<MMatrix> mayaWorlds(boneCount);
        std::vector<MMatrix> mmdWorlds(boneCount);

        for (size_t boneIndex = 0; boneIndex < boneCount; ++boneIndex) {
            MTransformationMatrix localTfm;
            const auto& translate = mayaTranslates[boneIndex];
            localTfm.setTranslation(MVector(translate[0], translate[1], translate[2]), MSpace::kTransform);
            MQuaternion qTotal = mayaRotateEulers[boneIndex].asQuaternion();
            if (boneIndex < cfg.jointOrients.size() && cfg.hasJointOrient[boneIndex]) {
                qTotal = qTotal * cfg.jointOrients[boneIndex];
                qTotal.normalizeIt();
            }
            localTfm.setRotationQuaternion(qTotal.x, qTotal.y, qTotal.z, qTotal.w);
            MMatrix localMaya = localTfm.asMatrix();

            const int32_t parent = cfg.parentSlots[boneIndex];
            MMatrix mayaWorld = localMaya;
            if (parent >= 0 && static_cast<size_t>(parent) < boneIndex) {
                mayaWorld = localMaya * mayaWorlds[static_cast<size_t>(parent)];
            }
            mayaWorlds[boneIndex] = mayaWorld;

            MMatrix runtimeWorld = cfg.noOrientBindWorldMatrices[boneIndex] *
                                   cfg.mayaBindWorldMatrices[boneIndex].inverse() *
                                   mayaWorld;
            mmdWorlds[boneIndex] = mayaWorldToMmd(runtimeWorld);
        }

        for (size_t boneIndex = 0; boneIndex < boneCount; ++boneIndex) {
            const int32_t parent = cfg.parentSlots[boneIndex];
            MMatrix localMmd = mmdWorlds[boneIndex];
            if (parent >= 0 && static_cast<size_t>(parent) < boneIndex) {
                localMmd = mmdWorlds[boneIndex] * mmdWorlds[static_cast<size_t>(parent)].inverse();
            }
            MTransformationMatrix localTfm(localMmd);
            MVector localTranslation = localTfm.getTranslation(MSpace::kTransform);
            const auto& rest = cfg.restPositions[boneIndex];
            positions[boneIndex * 3] = static_cast<float>(localTranslation.x - rest[0]);
            positions[boneIndex * 3 + 1] = static_cast<float>(localTranslation.y - rest[1]);
            positions[boneIndex * 3 + 2] = static_cast<float>(localTranslation.z - rest[2]);

            MQuaternion q = localTfm.rotation();
            rotations[boneIndex * 4] = static_cast<float>(q.x);
            rotations[boneIndex * 4 + 1] = static_cast<float>(q.y);
            rotations[boneIndex * 4 + 2] = static_cast<float>(q.z);
            rotations[boneIndex * 4 + 3] = static_cast<float>(q.w);
        }
    } else {
        for (size_t boneIndex = 0; boneIndex < boneCount; ++boneIndex) {
            const auto& translate = mayaTranslates[boneIndex];
            const auto& rest = cfg.mayaRestTranslates[boneIndex];
            positions[boneIndex * 3] = static_cast<float>(translate[0] - rest[0]);
            positions[boneIndex * 3 + 1] = static_cast<float>(translate[1] - rest[1]);
            positions[boneIndex * 3 + 2] = static_cast<float>(-(translate[2] - rest[2]));

            MQuaternion q = mayaRotateEulers[boneIndex].asQuaternion();
            rotations[boneIndex * 4] = static_cast<float>(-q.x);
            rotations[boneIndex * 4 + 1] = static_cast<float>(-q.y);
            rotations[boneIndex * 4 + 2] = static_cast<float>(q.z);
            rotations[boneIndex * 4 + 3] = static_cast<float>(q.w);
        }
    }

    const bool useControllerGoal = cfg.controllerBoneSlot >= 0 &&
                                   static_cast<size_t>(cfg.controllerBoneSlot) < boneCount &&
                                   !goalHasInputConnection;
    const std::array<float, 3> goal = useControllerGoal
        ? computeFkWorldPosition(cfg, positions, rotations, cfg.controllerBoneSlot)
        : readGoalPositionMmd(cfg, node, data, useGoalWorldMatrix);

    // Pass-through gate: FK input pose が target を既に goal 上に置いている
    // なら solve しない（VMD bake 済み final pose の二重 solve 防止）。
    // ズレていれば solve する — 腰などチェーン祖先の移動は target だけを
    // 動かすので solve が走り、全ての親は target と goal を一緒に動かす
    // ので pass-through のまま。
    if (static_cast<size_t>(cfg.targetBoneSlot) < boneCount) {
        const std::array<float, 3> fkTarget = computeFkWorldPosition(
            cfg, positions, rotations, static_cast<int32_t>(cfg.targetBoneSlot));
        if (std::abs(fkTarget[0] - goal[0]) <= kGoalMatchEpsilon &&
            std::abs(fkTarget[1] - goal[1]) <= kGoalMatchEpsilon &&
            std::abs(fkTarget[2] - goal[2]) <= kGoalMatchEpsilon) {
            copyInputRotateLinksToOutput(cfg, data, outEulerRadians);
            outSolved = false;
            return true;
        }
    }
    float goalPosition[3] = {goal[0], goal[1], goal[2]};
    std::vector<float> outQuats(cfg.links.size() * 4, 0.0f);
    mmd_runtime_ffi_ik_solve_stats_t stats{};
    bool ok = mmd_runtime_ik_chain_solve(
        chain,
        nullptr,
        positions.data(),
        rotations.data(),
        goalPosition,
        1.0e-5f,
        0u,
        outQuats.data(),
        outQuats.size(),
        &stats);
    if (!ok) {
        return false;
    }
    outSolved = true;

    outEulerRadians.clear();
    outEulerRadians.reserve(cfg.links.size());
    if (cfg.hasBindMatrices) {
        std::vector<float> solvedRotations = rotations;
        for (size_t linkIndex = 0; linkIndex < cfg.links.size(); ++linkIndex) {
            const uint32_t slot = cfg.linkSlots[linkIndex];
            if (slot < boneCount) {
                const size_t src = linkIndex * 4;
                const size_t dst = static_cast<size_t>(slot) * 4;
                solvedRotations[dst] = outQuats[src];
                solvedRotations[dst + 1] = outQuats[src + 1];
                solvedRotations[dst + 2] = outQuats[src + 2];
                solvedRotations[dst + 3] = outQuats[src + 3];
            }
        }

        std::vector<MMatrix> worldMmd(boneCount);
        std::vector<MMatrix> mayaWorlds(boneCount);
        for (size_t boneIndex = 0; boneIndex < boneCount; ++boneIndex) {
            const auto& rest = cfg.restPositions[boneIndex];
            MTransformationMatrix localTfm;
            localTfm.setTranslation(
                MVector(
                    rest[0] + static_cast<double>(positions[boneIndex * 3]),
                    rest[1] + static_cast<double>(positions[boneIndex * 3 + 1]),
                    rest[2] + static_cast<double>(positions[boneIndex * 3 + 2])),
                MSpace::kTransform);
            const size_t qOffset = boneIndex * 4;
            localTfm.setRotationQuaternion(
                static_cast<double>(solvedRotations[qOffset]),
                static_cast<double>(solvedRotations[qOffset + 1]),
                static_cast<double>(solvedRotations[qOffset + 2]),
                static_cast<double>(solvedRotations[qOffset + 3]));
            MMatrix localMmd = localTfm.asMatrix();
            const int32_t parent = cfg.parentSlots[boneIndex];
            worldMmd[boneIndex] = localMmd;
            if (parent >= 0 && static_cast<size_t>(parent) < boneIndex) {
                worldMmd[boneIndex] = localMmd * worldMmd[static_cast<size_t>(parent)];
            }

            MMatrix runtimeWorld = mmdWorldToMaya(worldMmd[boneIndex]);
            mayaWorlds[boneIndex] = cfg.mayaBindWorldMatrices[boneIndex] *
                                    cfg.noOrientBindWorldMatrices[boneIndex].inverse() *
                                    runtimeWorld;
        }

        for (size_t linkIndex = 0; linkIndex < cfg.links.size(); ++linkIndex) {
            const uint32_t slot = cfg.linkSlots[linkIndex];
            if (slot >= boneCount) {
                outEulerRadians.push_back({0.0, 0.0, 0.0});
                continue;
            }
            MMatrix local = mayaWorlds[slot];
            const int32_t parent = cfg.parentSlots[slot];
            if (parent >= 0 && static_cast<size_t>(parent) < boneCount) {
                local = mayaWorlds[slot] * mayaWorlds[static_cast<size_t>(parent)].inverse();
            }
            MQuaternion q = MTransformationMatrix(local).rotation();
            if (slot < cfg.jointOrients.size() && cfg.hasJointOrient[slot]) {
                q = q * cfg.jointOrients[slot].inverse();
                q.normalizeIt();
            }
            MEulerRotation euler = q.asEulerRotation();
            outEulerRadians.push_back({euler.x, euler.y, euler.z});
        }
    } else {
        for (size_t linkIndex = 0; linkIndex < cfg.links.size(); ++linkIndex) {
            const size_t offset = linkIndex * 4;
            MQuaternion outQuat(
                -static_cast<double>(outQuats[offset]),
                -static_cast<double>(outQuats[offset + 1]),
                static_cast<double>(outQuats[offset + 2]),
                static_cast<double>(outQuats[offset + 3]));
            MEulerRotation euler = outQuat.asEulerRotation();
            outEulerRadians.push_back({euler.x, euler.y, euler.z});
        }
    }
    return true;
}
}

struct MmdCcdIkNode::ChainCache {
    CcdIkChainConfig config;
    std::string chainJson;
    mmd_runtime_ik_chain_t* nativeChain = nullptr;
    bool valid = false;

    ~ChainCache()
    {
        if (nativeChain) {
            mmd_runtime_ik_chain_free(nativeChain);
            nativeChain = nullptr;
        }
    }
};

const MTypeId MmdCcdIkNode::id(0x00128002);

// --- 入力: inputRoot ---
MObject MmdCcdIkNode::aInputRoot;
MObject MmdCcdIkNode::aInputRootX;
MObject MmdCcdIkNode::aInputRootY;
MObject MmdCcdIkNode::aInputRootZ;

// --- 入力: inputEffector ---
MObject MmdCcdIkNode::aInputEffector;
MObject MmdCcdIkNode::aInputEffectorX;
MObject MmdCcdIkNode::aInputEffectorY;
MObject MmdCcdIkNode::aInputEffectorZ;

// --- 入力: target ---
MObject MmdCcdIkNode::aTarget;
MObject MmdCcdIkNode::aTargetX;
MObject MmdCcdIkNode::aTargetY;
MObject MmdCcdIkNode::aTargetZ;

// --- 入力: enabled ---
MObject MmdCcdIkNode::aEnabled;

// --- 入力: iterations ---
MObject MmdCcdIkNode::aIterations;

// --- 入力: angleLimit ---
MObject MmdCcdIkNode::aAngleLimit;

// --- 入力: inputChain ---
MObject MmdCcdIkNode::aInputChain;

// --- Python-compatible schema inputs ---
MObject MmdCcdIkNode::aChainJson;

MObject MmdCcdIkNode::aGoal;
MObject MmdCcdIkNode::aGoalX;
MObject MmdCcdIkNode::aGoalY;
MObject MmdCcdIkNode::aGoalZ;

MObject MmdCcdIkNode::aGoalWorldMatrix;

MObject MmdCcdIkNode::aInputRotateArray;
MObject MmdCcdIkNode::aInputRotateArrayX;
MObject MmdCcdIkNode::aInputRotateArrayY;
MObject MmdCcdIkNode::aInputRotateArrayZ;

MObject MmdCcdIkNode::aInputTranslateArray;
MObject MmdCcdIkNode::aInputTranslateArrayX;
MObject MmdCcdIkNode::aInputTranslateArrayY;
MObject MmdCcdIkNode::aInputTranslateArrayZ;

// --- 出力: outputRotate ---
MObject MmdCcdIkNode::aOutputRotate;
MObject MmdCcdIkNode::aOutputRotateX;
MObject MmdCcdIkNode::aOutputRotateY;
MObject MmdCcdIkNode::aOutputRotateZ;

// --- 出力: outputAngle ---
MObject MmdCcdIkNode::aOutputAngle;

// --- 出力: solved(bool) ---
MObject MmdCcdIkNode::aSolved;

// --- 出力: outputLinkAngles ---
MObject MmdCcdIkNode::aOutputLinkAngles;

// --- 出力: outputLinkRotates ---
MObject MmdCcdIkNode::aOutputLinkRotates;


MmdCcdIkNode::MmdCcdIkNode() = default;
MmdCcdIkNode::~MmdCcdIkNode() = default;

bool MmdCcdIkNode::ensureChainCache(const MString& chainJson)
{
    const std::string text = chainJson.asChar();
    if (!chainCache_) {
        chainCache_ = std::make_unique<ChainCache>();
    }

    // 内容が同一なら、成功した config/native chain も、失敗した malformed
    // 結果も再利用する。失敗結果を記録することで output plug ごとの再parse
    // を防ぎ、直前の有効 config が stale のまま残ることも避ける。
    if (chainCache_->chainJson == text) {
        return chainCache_->valid;
    }

    CcdIkChainConfig parsed;
    const bool parsedOk = parseCcdIkChainJson(chainJson, parsed);

    // 置換前に旧 native chain を解放し、parse/create 失敗時は必ず無効化する。
    // この順序により malformed config が直前の有効 chain を再利用しない。
    if (chainCache_->nativeChain) {
        mmd_runtime_ik_chain_free(chainCache_->nativeChain);
        chainCache_->nativeChain = nullptr;
    }
    chainCache_->config = CcdIkChainConfig{};
    chainCache_->chainJson = text;
    chainCache_->valid = false;

    if (!parsedOk) {
        return false;
    }

    mmd_runtime_ik_chain_t* nativeChain = createNativeIkChain(parsed);
    if (!nativeChain) {
        return false;
    }

    chainCache_->config = std::move(parsed);
    chainCache_->nativeChain = nativeChain;
    chainCache_->valid = true;
    return true;
}

void* MmdCcdIkNode::creator() {
    return new MmdCcdIkNode();
}

MObject MmdCcdIkNode::createDouble3Attribute(
    const MString& longName,
    const MString& shortName,
    MObject& childX,
    MObject& childY,
    MObject& childZ,
    double defaultVal)
{
    MStatus status;
    MFnNumericAttribute nAttr;
    MFnCompoundAttribute cAttr;

    childX = nAttr.create(longName + "X", shortName + "x", MFnNumericData::kDouble, defaultVal, &status);
    nAttr.setStorable(true);
    nAttr.setKeyable(true);
    nAttr.setWritable(true);
    nAttr.setReadable(true);

    childY = nAttr.create(longName + "Y", shortName + "y", MFnNumericData::kDouble, defaultVal, &status);
    nAttr.setStorable(true);
    nAttr.setKeyable(true);
    nAttr.setWritable(true);
    nAttr.setReadable(true);

    childZ = nAttr.create(longName + "Z", shortName + "z", MFnNumericData::kDouble, defaultVal, &status);
    nAttr.setStorable(true);
    nAttr.setKeyable(true);
    nAttr.setWritable(true);
    nAttr.setReadable(true);

    MObject compound = cAttr.create(longName, shortName, &status);
    cAttr.addChild(childX);
    cAttr.addChild(childY);
    cAttr.addChild(childZ);
    cAttr.setStorable(true);
    cAttr.setKeyable(true);
    cAttr.setWritable(true);
    cAttr.setReadable(true);

    return compound;
}

MObject MmdCcdIkNode::createAngle3ArrayAttribute(
    const MString& longName,
    const MString& shortName,
    MObject& childX,
    MObject& childY,
    MObject& childZ,
    const MString& childShortPrefix)
{
    MStatus status;
    MFnUnitAttribute uAttr;
    MFnCompoundAttribute cAttr;

    const MString csp = childShortPrefix.length() > 0 ? childShortPrefix : shortName;

    childX = uAttr.create(longName + "ElementX", csp + "x", MFnUnitAttribute::kAngle, 0.0, &status);
    uAttr.setStorable(true);
    uAttr.setKeyable(true);
    uAttr.setWritable(true);
    uAttr.setReadable(true);

    childY = uAttr.create(longName + "ElementY", csp + "y", MFnUnitAttribute::kAngle, 0.0, &status);
    uAttr.setStorable(true);
    uAttr.setKeyable(true);
    uAttr.setWritable(true);
    uAttr.setReadable(true);

    childZ = uAttr.create(longName + "ElementZ", csp + "z", MFnUnitAttribute::kAngle, 0.0, &status);
    uAttr.setStorable(true);
    uAttr.setKeyable(true);
    uAttr.setWritable(true);
    uAttr.setReadable(true);

    MObject compound = cAttr.create(longName, shortName, &status);
    cAttr.addChild(childX);
    cAttr.addChild(childY);
    cAttr.addChild(childZ);
    cAttr.setArray(true);
    cAttr.setUsesArrayDataBuilder(true);
    cAttr.setStorable(true);
    cAttr.setWritable(true);
    cAttr.setReadable(true);
    return compound;
}

MObject MmdCcdIkNode::createAngle3ArrayOutputAttribute(
    const MString& longName,
    const MString& shortName,
    MObject& childX,
    MObject& childY,
    MObject& childZ,
    const MString& childShortPrefix)
{
    MStatus status;
    MObject compound = createAngle3ArrayAttribute(longName, shortName, childX, childY, childZ, childShortPrefix);
    MFnCompoundAttribute cAttr(compound, &status);
    cAttr.setWritable(false);
    cAttr.setReadable(true);
    cAttr.setStorable(false);
    cAttr.setKeyable(false);

    MFnUnitAttribute uChild;
    uChild.setObject(childX);
    uChild.setWritable(false);
    uChild.setStorable(false);
    uChild.setKeyable(false);
    uChild.setObject(childY);
    uChild.setWritable(false);
    uChild.setStorable(false);
    uChild.setKeyable(false);
    uChild.setObject(childZ);
    uChild.setWritable(false);
    uChild.setStorable(false);
    uChild.setKeyable(false);
    return compound;
}

MObject MmdCcdIkNode::createDouble3ArrayAttribute(
    const MString& longName,
    const MString& shortName,
    MObject& childX,
    MObject& childY,
    MObject& childZ,
    const MString& childShortPrefix)
{
    MStatus status;
    MFnNumericAttribute nAttr;
    MFnCompoundAttribute cAttr;

    const MString csp = childShortPrefix.length() > 0 ? childShortPrefix : shortName;

    childX = nAttr.create(longName + "ElementX", csp + "x", MFnNumericData::kDouble, 0.0, &status);
    nAttr.setStorable(true);
    nAttr.setKeyable(true);
    nAttr.setWritable(true);
    nAttr.setReadable(true);

    childY = nAttr.create(longName + "ElementY", csp + "y", MFnNumericData::kDouble, 0.0, &status);
    nAttr.setStorable(true);
    nAttr.setKeyable(true);
    nAttr.setWritable(true);
    nAttr.setReadable(true);

    childZ = nAttr.create(longName + "ElementZ", csp + "z", MFnNumericData::kDouble, 0.0, &status);
    nAttr.setStorable(true);
    nAttr.setKeyable(true);
    nAttr.setWritable(true);
    nAttr.setReadable(true);

    MObject compound = cAttr.create(longName, shortName, &status);
    cAttr.addChild(childX);
    cAttr.addChild(childY);
    cAttr.addChild(childZ);
    cAttr.setArray(true);
    cAttr.setUsesArrayDataBuilder(true);
    cAttr.setStorable(true);
    cAttr.setWritable(true);
    cAttr.setReadable(true);
    return compound;
}

MStatus MmdCcdIkNode::initialize() {
    MStatus status;
    MFnNumericAttribute nAttr;
    MFnTypedAttribute tAttr;
    MFnStringData sData;
    MFnMatrixAttribute mAttr;

    // --- Legacy 入力 (hidden): inputRoot(double3) ---
    aInputRoot = createDouble3Attribute(
        "inputRoot", "irt",
        aInputRootX, aInputRootY, aInputRootZ, 0.0);
    addAttribute(aInputRoot);
    MFnAttribute(aInputRoot).setHidden(true);

    // --- Legacy 入力 (hidden): inputEffector(double3) ---
    aInputEffector = createDouble3Attribute(
        "inputEffector", "ief",
        aInputEffectorX, aInputEffectorY, aInputEffectorZ, 0.0);
    addAttribute(aInputEffector);
    MFnAttribute(aInputEffector).setHidden(true);

    // --- Legacy 入力 (hidden): target(double3) ---
    aTarget = createDouble3Attribute(
        "target", "tgt",
        aTargetX, aTargetY, aTargetZ, 0.0);
    addAttribute(aTarget);
    MFnAttribute(aTarget).setHidden(true);

    // --- 入力: enabled(bool, default true) ---
    aEnabled = nAttr.create("enabled", "en", MFnNumericData::kBoolean, true, &status);
    nAttr.setStorable(true);
    nAttr.setKeyable(true);
    nAttr.setWritable(true);
    nAttr.setReadable(false);
    addAttribute(aEnabled);

    // --- Legacy 入力 (hidden): iterations(int, default 1, min 1) ---
    aIterations = nAttr.create("iterations", "itn", MFnNumericData::kInt, 1, &status);
    nAttr.setStorable(true);
    nAttr.setKeyable(true);
    nAttr.setWritable(true);
    nAttr.setReadable(false);
    nAttr.setMin(1);
    nAttr.setHidden(true);
    addAttribute(aIterations);

    // --- Legacy 入力 (hidden): angleLimit(double degrees, default 180.0, min 0) ---
    aAngleLimit = nAttr.create("angleLimit", "alm", MFnNumericData::kDouble, 180.0, &status);
    nAttr.setStorable(true);
    nAttr.setKeyable(true);
    nAttr.setWritable(true);
    nAttr.setReadable(false);
    nAttr.setMin(0.0);
    nAttr.setHidden(true);
    addAttribute(aAngleLimit);

    // --- Legacy 入力 (hidden): inputChain(doubleArray) ---
    aInputChain = tAttr.create("inputChain", "ichn", MFnData::kDoubleArray, MObject::kNullObj, &status);
    tAttr.setStorable(true);
    tAttr.setKeyable(false);
    tAttr.setWritable(true);
    tAttr.setReadable(true);
    tAttr.setHidden(true);
    addAttribute(aInputChain);

    aChainJson = tAttr.create("chainJson", "cj", MFnData::kString, sData.create(""), &status);
    tAttr.setStorable(true);
    tAttr.setKeyable(false);
    tAttr.setWritable(true);
    tAttr.setReadable(true);
    addAttribute(aChainJson);

    aGoal = createDouble3Attribute(
        "goal", "g",
        aGoalX, aGoalY, aGoalZ, 0.0);
    addAttribute(aGoal);

    aGoalWorldMatrix = mAttr.create("goalWorldMatrix", "gwm", MFnMatrixAttribute::kDouble, &status);
    mAttr.setStorable(false);
    mAttr.setKeyable(false);
    mAttr.setWritable(true);
    mAttr.setReadable(true);
    addAttribute(aGoalWorldMatrix);

    aInputRotateArray = createAngle3ArrayAttribute(
        "inputRotate", "ir",
        aInputRotateArrayX, aInputRotateArrayY, aInputRotateArrayZ, "ier");
    addAttribute(aInputRotateArray);

    aInputTranslateArray = createDouble3ArrayAttribute(
        "inputTranslate", "it_ik",
        aInputTranslateArrayX, aInputTranslateArrayY, aInputTranslateArrayZ, "iet");
    addAttribute(aInputTranslateArray);

    // --- 出力: outputRotate angle array ---
    aOutputRotate = createAngle3ArrayOutputAttribute(
        "outputRotate", "or_ik",
        aOutputRotateX, aOutputRotateY, aOutputRotateZ, "oer");
    addAttribute(aOutputRotate);

    // --- Legacy 出力 (hidden): outputAngle(double) ---
    aOutputAngle = nAttr.create("outputAngle", "oan", MFnNumericData::kDouble, 0.0, &status);
    nAttr.setWritable(false);
    nAttr.setReadable(true);
    nAttr.setStorable(false);
    nAttr.setKeyable(false);
    nAttr.setHidden(true);
    addAttribute(aOutputAngle);

    // --- Legacy 出力 (hidden): solved(bool) ---
    aSolved = nAttr.create("solved", "sol", MFnNumericData::kBoolean, false, &status);
    nAttr.setWritable(false);
    nAttr.setReadable(true);
    nAttr.setStorable(false);
    nAttr.setKeyable(false);
    nAttr.setHidden(true);
    addAttribute(aSolved);

    // --- Legacy 出力 (hidden): outputLinkAngles(doubleArray) ---
    aOutputLinkAngles = tAttr.create("outputLinkAngles", "ola", MFnData::kDoubleArray, MObject::kNullObj, &status);
    tAttr.setWritable(false);
    tAttr.setReadable(true);
    tAttr.setStorable(false);
    tAttr.setKeyable(false);
    tAttr.setHidden(true);
    addAttribute(aOutputLinkAngles);

    // --- Legacy 出力 (hidden): outputLinkRotates(doubleArray) ---
    aOutputLinkRotates = tAttr.create("outputLinkRotates", "olr", MFnData::kDoubleArray, MObject::kNullObj, &status);
    tAttr.setWritable(false);
    tAttr.setReadable(true);
    tAttr.setStorable(false);
    tAttr.setKeyable(false);
    tAttr.setHidden(true);
    addAttribute(aOutputLinkRotates);

    // --- attributeAffects ---
    // 既存 1-link の既存出力との依存を維持
    attributeAffects(aInputRootX, aOutputRotateX);
    attributeAffects(aInputRootY, aOutputRotateX);
    attributeAffects(aInputRootZ, aOutputRotateX);
    attributeAffects(aInputRootX, aOutputRotateY);
    attributeAffects(aInputRootY, aOutputRotateY);
    attributeAffects(aInputRootZ, aOutputRotateY);
    attributeAffects(aInputRootX, aOutputRotateZ);
    attributeAffects(aInputRootY, aOutputRotateZ);
    attributeAffects(aInputRootZ, aOutputRotateZ);
    attributeAffects(aInputRootX, aOutputAngle);
    attributeAffects(aInputRootY, aOutputAngle);
    attributeAffects(aInputRootZ, aOutputAngle);
    attributeAffects(aInputRootX, aSolved);
    attributeAffects(aInputRootY, aSolved);
    attributeAffects(aInputRootZ, aSolved);

    attributeAffects(aInputEffectorX, aOutputRotateX);
    attributeAffects(aInputEffectorY, aOutputRotateX);
    attributeAffects(aInputEffectorZ, aOutputRotateX);
    attributeAffects(aInputEffectorX, aOutputRotateY);
    attributeAffects(aInputEffectorY, aOutputRotateY);
    attributeAffects(aInputEffectorZ, aOutputRotateY);
    attributeAffects(aInputEffectorX, aOutputRotateZ);
    attributeAffects(aInputEffectorY, aOutputRotateZ);
    attributeAffects(aInputEffectorZ, aOutputRotateZ);
    attributeAffects(aInputEffectorX, aOutputAngle);
    attributeAffects(aInputEffectorY, aOutputAngle);
    attributeAffects(aInputEffectorZ, aOutputAngle);
    attributeAffects(aInputEffectorX, aSolved);
    attributeAffects(aInputEffectorY, aSolved);
    attributeAffects(aInputEffectorZ, aSolved);

    attributeAffects(aTargetX, aOutputRotateX);
    attributeAffects(aTargetY, aOutputRotateX);
    attributeAffects(aTargetZ, aOutputRotateX);
    attributeAffects(aTargetX, aOutputRotateY);
    attributeAffects(aTargetY, aOutputRotateY);
    attributeAffects(aTargetZ, aOutputRotateY);
    attributeAffects(aTargetX, aOutputRotateZ);
    attributeAffects(aTargetY, aOutputRotateZ);
    attributeAffects(aTargetZ, aOutputRotateZ);
    attributeAffects(aTargetX, aOutputAngle);
    attributeAffects(aTargetY, aOutputAngle);
    attributeAffects(aTargetZ, aOutputAngle);
    attributeAffects(aTargetX, aSolved);
    attributeAffects(aTargetY, aSolved);
    attributeAffects(aTargetZ, aSolved);

    attributeAffects(aEnabled, aOutputRotateX);
    attributeAffects(aEnabled, aOutputRotateY);
    attributeAffects(aEnabled, aOutputRotateZ);
    attributeAffects(aEnabled, aOutputAngle);
    attributeAffects(aEnabled, aSolved);

    attributeAffects(aIterations, aOutputRotateX);
    attributeAffects(aIterations, aOutputRotateY);
    attributeAffects(aIterations, aOutputRotateZ);
    attributeAffects(aIterations, aOutputAngle);
    attributeAffects(aIterations, aSolved);

    attributeAffects(aAngleLimit, aOutputRotateX);
    attributeAffects(aAngleLimit, aOutputRotateY);
    attributeAffects(aAngleLimit, aOutputRotateZ);
    attributeAffects(aAngleLimit, aOutputAngle);
    attributeAffects(aAngleLimit, aSolved);

    // multi-link outputs
    attributeAffects(aInputRootX, aOutputLinkAngles);
    attributeAffects(aInputRootY, aOutputLinkAngles);
    attributeAffects(aInputRootZ, aOutputLinkAngles);
    attributeAffects(aInputEffectorX, aOutputLinkAngles);
    attributeAffects(aInputEffectorY, aOutputLinkAngles);
    attributeAffects(aInputEffectorZ, aOutputLinkAngles);
    attributeAffects(aTargetX, aOutputLinkAngles);
    attributeAffects(aTargetY, aOutputLinkAngles);
    attributeAffects(aTargetZ, aOutputLinkAngles);
    attributeAffects(aEnabled, aOutputLinkAngles);
    attributeAffects(aIterations, aOutputLinkAngles);
    attributeAffects(aAngleLimit, aOutputLinkAngles);
    attributeAffects(aInputChain, aOutputLinkAngles);

    attributeAffects(aInputRootX, aOutputLinkRotates);
    attributeAffects(aInputRootY, aOutputLinkRotates);
    attributeAffects(aInputRootZ, aOutputLinkRotates);
    attributeAffects(aInputEffectorX, aOutputLinkRotates);
    attributeAffects(aInputEffectorY, aOutputLinkRotates);
    attributeAffects(aInputEffectorZ, aOutputLinkRotates);
    attributeAffects(aTargetX, aOutputLinkRotates);
    attributeAffects(aTargetY, aOutputLinkRotates);
    attributeAffects(aTargetZ, aOutputLinkRotates);
    attributeAffects(aEnabled, aOutputLinkRotates);
    attributeAffects(aIterations, aOutputLinkRotates);
    attributeAffects(aAngleLimit, aOutputLinkRotates);
    attributeAffects(aInputChain, aOutputLinkRotates);

    attributeAffects(aChainJson, aOutputRotateX);
    attributeAffects(aChainJson, aOutputRotateY);
    attributeAffects(aChainJson, aOutputRotateZ);
    attributeAffects(aChainJson, aOutputAngle);
    attributeAffects(aChainJson, aSolved);
    attributeAffects(aChainJson, aOutputLinkAngles);
    attributeAffects(aChainJson, aOutputLinkRotates);
    // goal is a vector input to the solver.  Every goal component can change
    // every Euler component (and every link) of the result, so do not map a
    // source child to only the same-named output child.  In particular, Maya
    // dirties array output children independently; declaring the complete
    // child-to-child matrix keeps sibling outputRotate elements from staying
    // cached after a single goal-axis edit.
    const MObject goalChildren[] = {aGoalX, aGoalY, aGoalZ};
    const MObject outputRotateChildren[] = {aOutputRotateX, aOutputRotateY, aOutputRotateZ};
    for (const MObject& goalChild : goalChildren) {
        for (const MObject& outputChild : outputRotateChildren) {
            attributeAffects(goalChild, outputChild);
        }
        attributeAffects(goalChild, aSolved);
        attributeAffects(goalChild, aOutputLinkAngles);
        attributeAffects(goalChild, aOutputLinkRotates);
    }
    attributeAffects(aGoalWorldMatrix, aOutputRotateX);
    attributeAffects(aGoalWorldMatrix, aOutputRotateY);
    attributeAffects(aGoalWorldMatrix, aOutputRotateZ);
    attributeAffects(aGoalWorldMatrix, aSolved);
    attributeAffects(aGoalWorldMatrix, aOutputLinkAngles);
    attributeAffects(aGoalWorldMatrix, aOutputLinkRotates);
    attributeAffects(aInputRotateArray, aOutputRotateX);
    attributeAffects(aInputRotateArray, aOutputRotateY);
    attributeAffects(aInputRotateArray, aOutputRotateZ);
    attributeAffects(aInputRotateArray, aSolved);
    attributeAffects(aInputRotateArray, aOutputLinkAngles);
    attributeAffects(aInputRotateArray, aOutputLinkRotates);
    attributeAffects(aInputTranslateArray, aOutputRotateX);
    attributeAffects(aInputTranslateArray, aOutputRotateY);
    attributeAffects(aInputTranslateArray, aOutputRotateZ);
    attributeAffects(aInputTranslateArray, aSolved);
    attributeAffects(aInputTranslateArray, aOutputLinkAngles);
    attributeAffects(aInputTranslateArray, aOutputLinkRotates);

    return MS::kSuccess;
}

MStatus MmdCcdIkNode::compute(const MPlug& plug, MDataBlock& data) {
    MStatus status;

    bool isRotate = plugIsOutputRotate(plug, aOutputRotate);
    bool isAngle = (plug == aOutputAngle);
    bool isSolved = (plug == aSolved);
    bool isLinkAngles = (plug == aOutputLinkAngles);
    bool isLinkRotates = (plug == aOutputLinkRotates);

    if (!isRotate && !isAngle && !isSolved && !isLinkAngles && !isLinkRotates) {
        return MS::kUnknownParameter;
    }

    bool enabled = data.inputValue(aEnabled, &status).asBool();
    int iterations = data.inputValue(aIterations, &status).asInt();
    double angleLimit = data.inputValue(aAngleLimit, &status).asDouble();
    const double* target = data.inputValue(aTarget).asDouble3();

    double outAngleDeg = 0.0;
    bool outSolved = false;
    double outRotX = 0.0, outRotY = 0.0, outRotZ = 0.0;
    MDoubleArray outLinkAngles;
    MDoubleArray outLinkRotates;
    const double eps = 1e-12;

    // chainJson の parse/native chain create はノードインスタンス内で内容変更時
    // のみ実行する。compute が re-entrant になっても chain の置換と solve が
    // 同時に走らないよう、cache の mutex を solve 完了まで保持する。
    std::unique_lock<std::mutex> chainCacheLock(chainCacheMutex_);
    const MString chainJson = data.inputValue(aChainJson, &status).asString();
    if (ensureChainCache(chainJson)) {
        const CcdIkChainConfig& chainCfg = chainCache_->config;
        std::vector<std::array<double, 3>> chainRotationsRadians;
        bool chainSolved = false;
        bool chainPathHandled = false;

        if (enabled) {
            const MObject thisNode = thisMObject();
            const bool goalWorldConnected = plugOrChildrenHasInputConnection(thisNode, aGoalWorldMatrix);
            const bool goalConnected = goalWorldConnected || plugOrChildrenHasInputConnection(thisNode, aGoal);
            chainPathHandled = solveChainJsonIk(
                chainCfg,
                chainCache_->nativeChain,
                thisNode,
                data,
                goalWorldConnected,
                goalConnected,
                chainSolved,
                chainRotationsRadians);
        } else {
            MArrayDataHandle rotateArray = data.inputArrayValue(aInputRotateArray, &status);
            chainRotationsRadians.reserve(chainCfg.linkSlots.size());
            for (uint32_t slot : chainCfg.linkSlots) {
                std::array<double, 3> eulerRadians{0.0, 0.0, 0.0};
                readInputRotateElement(
                    rotateArray,
                    slot,
                    aInputRotateArrayX,
                    aInputRotateArrayY,
                    aInputRotateArrayZ,
                    eulerRadians);
                chainRotationsRadians.push_back(eulerRadians);
            }
            chainPathHandled = true;
        }

        if (chainPathHandled) {
            // どの output plug が要求された場合でも、一度の solve 結果で関連
            // output を全て書き込み clean にする。これにより sibling plug の
            // 要求ごとに parse/create/solve が再実行されない。
            setOutputRotateElements(
                data,
                aOutputRotate,
                aOutputRotateX,
                aOutputRotateY,
                aOutputRotateZ,
                chainRotationsRadians);

            MDataHandle hAngle = data.outputValue(aOutputAngle, &status);
            hAngle.set(0.0); // chain path の outputAngle は neutral contract
            hAngle.setClean();

            MDataHandle hSolved = data.outputValue(aSolved, &status);
            hSolved.set(chainSolved);
            hSolved.setClean();

            for (const auto& eulerRadians : chainRotationsRadians) {
                outLinkAngles.append(eulerRadians[2] * 180.0 / kPi);
                outLinkRotates.append(eulerRadians[0] * 180.0 / kPi);
                outLinkRotates.append(eulerRadians[1] * 180.0 / kPi);
                outLinkRotates.append(eulerRadians[2] * 180.0 / kPi);
            }

            MFnDoubleArrayData dataObject;
            MObject linkAnglesObj = dataObject.create(outLinkAngles, &status);
            MDataHandle hLinkAngles = data.outputValue(aOutputLinkAngles, &status);
            hLinkAngles.setMObject(linkAnglesObj);
            hLinkAngles.setClean();

            MObject linkRotatesObj = dataObject.create(outLinkRotates, &status);
            MDataHandle hLinkRotates = data.outputValue(aOutputLinkRotates, &status);
            hLinkRotates.setMObject(linkRotatesObj);
            hLinkRotates.setClean();

            data.setClean(plug);
            return MS::kSuccess;
        }
    }

    // malformed/empty chainJson は legacy path へ戻すが、キャッシュは既に
    // invalidated 済みなので、旧 native chain が残ることはない。
    chainCacheLock.unlock();

    if (enabled) {
        MDataHandle chainHandle = data.inputValue(aInputChain, &status);
        MObject chainObj = chainHandle.data();
        MDoubleArray chainVals;
        int linkCount = 0;
        bool useMultiLink = false;

        if (!chainObj.isNull()) {
            MFnDoubleArrayData chainData(chainObj, &status);
            if (status == MS::kSuccess) {
                chainVals = chainData.array();
                if (chainVals.length() >= 6 && (chainVals.length() % 3) == 0) {
                    linkCount = static_cast<int>(chainVals.length() / 3 - 1);
                    useMultiLink = linkCount >= 2;
                }
            }
        }

        if (useMultiLink) {
            std::vector<std::array<double, 3>> positions(static_cast<size_t>(linkCount + 1));
            std::vector<double> rotations(static_cast<size_t>(linkCount), 0.0);
            for (int i = 0; i < linkCount + 1; ++i) {
                int base = i * 3;
                positions[static_cast<size_t>(i)] = {
                    chainVals[base],
                    chainVals[static_cast<size_t>(base + 1)],
                    chainVals[static_cast<size_t>(base + 2)]
                };
            }

            for (int it = 0; it < iterations; ++it) {
                for (int link = linkCount - 1; link >= 0; --link) {
                    const auto& pivot = positions[static_cast<size_t>(link)];
                    const auto& effector = positions[static_cast<size_t>(linkCount)];

                    double ex = effector[0] - pivot[0];
                    double ey = effector[1] - pivot[1];
                    double tx = target[0] - pivot[0];
                    double ty = target[1] - pivot[1];

                    if ((ex * ex + ey * ey) <= eps || (tx * tx + ty * ty) <= eps) {
                        continue;
                    }

                    double crossZ = ex * ty - ey * tx;
                    double dot = ex * tx + ey * ty;
                    double stepAngleDeg = std::atan2(crossZ, dot) * 180.0 / kPi;

                    if (angleLimit >= 0.0) {
                        double maxStep = std::abs(angleLimit);
                        if (std::abs(stepAngleDeg) > maxStep) {
                            stepAngleDeg = (stepAngleDeg >= 0.0 ? 1.0 : -1.0) * maxStep;
                        }
                    }

                    rotations[static_cast<size_t>(link)] += stepAngleDeg;

                    double rad = stepAngleDeg * kPi / 180.0;
                    double cosV = std::cos(rad);
                    double sinV = std::sin(rad);

                    for (int j = link + 1; j <= linkCount; ++j) {
                        auto& point = positions[static_cast<size_t>(j)];
                        double px = point[0] - pivot[0];
                        double py = point[1] - pivot[1];

                        double nx = px * cosV - py * sinV;
                        double ny = px * sinV + py * cosV;
                        point[0] = pivot[0] + nx;
                        point[1] = pivot[1] + ny;
                    }
                }
            }

            outSolved = true;
            for (int link = 0; link < linkCount; ++link) {
                const double v = rotations[static_cast<size_t>(link)];
                outLinkAngles.append(v);
                outLinkRotates.append(0.0);
                outLinkRotates.append(0.0);
                outLinkRotates.append(v);
            }
        } else {
            // 既存 1-link パス (既存 smoke を維持)
            const double* root = data.inputValue(aInputRoot).asDouble3();
            const double* effector = data.inputValue(aInputEffector).asDouble3();

            double ex = effector[0] - root[0];
            double ey = effector[1] - root[1];
            double ez = effector[2] - root[2];

            double tx = target[0] - root[0];
            double ty = target[1] - root[1];
            double tz = target[2] - root[2];

            double lenEff = std::sqrt(ex * ex + ey * ey + ez * ez);
            double lenTgt = std::sqrt(tx * tx + ty * ty + tz * tz);

            if (lenEff > eps && lenTgt > eps) {
                double crossZ = ex * ty - ey * tx;
                double dot = ex * tx + ey * ty;
                double angleRad = std::atan2(crossZ, dot);
                double requestedAngle = angleRad * 180.0 / kPi;

                double maxAllowed = static_cast<double>(iterations) * angleLimit;
                if (std::abs(requestedAngle) > maxAllowed) {
                    outAngleDeg = (requestedAngle >= 0.0 ? 1.0 : -1.0) * maxAllowed;
                } else {
                    outAngleDeg = requestedAngle;
                }
                outRotZ = outAngleDeg;
                outSolved = true;
            }
        }
    }

    // Legacy path でも chain path と同じく、一度の compute で全関連 output を
    // 書き込み clean にする。requested plug ごとの条件分岐を残すと、同一
    // evaluation 内で sibling output が古い値を保持する。
    setOutputRotateElementZero(
        data,
        aOutputRotate,
        aOutputRotateX,
        aOutputRotateY,
        aOutputRotateZ,
        outRotX,
        outRotY,
        outRotZ);

    MDataHandle hAngle = data.outputValue(aOutputAngle, &status);
    hAngle.set(outAngleDeg);
    hAngle.setClean();

    MDataHandle hSolved = data.outputValue(aSolved, &status);
    hSolved.set(outSolved);
    hSolved.setClean();

    MFnDoubleArrayData dataObject;
    MObject linkAnglesObj = dataObject.create(outLinkAngles, &status);
    MDataHandle hLinkAngles = data.outputValue(aOutputLinkAngles, &status);
    hLinkAngles.setMObject(linkAnglesObj);
    hLinkAngles.setClean();

    MObject linkRotatesObj = dataObject.create(outLinkRotates, &status);
    MDataHandle hLinkRotates = data.outputValue(aOutputLinkRotates, &status);
    hLinkRotates.setMObject(linkRotatesObj);
    hLinkRotates.setClean();

    data.setClean(plug);
    return MS::kSuccess;
}
