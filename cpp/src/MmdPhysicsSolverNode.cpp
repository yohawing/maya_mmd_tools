/**
 * MmdPhysicsSolverNode.cpp
 *
 * C++ mmdPhysicsSolver — Python prototype の C++ ポート。
 * 同一 TypeId (0x00128008)、同一属性契約。
 */

#include "MmdPhysicsSolverNode.h"

#include <maya/MFnNumericAttribute.h>
#include <maya/MFnTypedAttribute.h>
#include <maya/MFnUnitAttribute.h>
#include <maya/MFnMessageAttribute.h>
#include <maya/MFnDoubleArrayData.h>
#include <maya/MFnDependencyNode.h>
#include <maya/MFnStringData.h>
#include <maya/MDataBlock.h>
#include <maya/MDataHandle.h>
#include <maya/MPlug.h>
#include <maya/MTime.h>
#include <maya/MGlobal.h>
#include <maya/MDoubleArray.h>

#include <cmath>
#include <cstring>

namespace {
    constexpr double kTimeEpsilon = 1e-6;
    constexpr double kMaxForwardDt = 0.2;

    // Minimal base64 decoder for PMX source payload
    static const int kBase64Table[256] = {
        -1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,
        -1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,
        -1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,62,-1,-1,-1,63,
        52,53,54,55,56,57,58,59,60,61,-1,-1,-1,-1,-1,-1,
        -1, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9,10,11,12,13,14,
        15,16,17,18,19,20,21,22,23,24,25,-1,-1,-1,-1,-1,
        -1,26,27,28,29,30,31,32,33,34,35,36,37,38,39,40,
        41,42,43,44,45,46,47,48,49,50,51,-1,-1,-1,-1,-1,
        -1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,
        -1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,
        -1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,
        -1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,
        -1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,
        -1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,
        -1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,
        -1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1
    };

    std::vector<uint8_t> base64Decode(const char* str, size_t len) {
        std::vector<uint8_t> out;
        out.reserve(len * 3 / 4);

        int val = 0;
        int bits = -8;
        for (size_t i = 0; i < len; ++i) {
            int c = kBase64Table[static_cast<unsigned char>(str[i])];
            if (c == -1) continue;
            val = (val << 6) | c;
            bits += 6;
            if (bits >= 0) {
                out.push_back(static_cast<uint8_t>((val >> bits) & 0xFF));
                bits -= 8;
            }
        }
        return out;
    }
}

const MTypeId MmdPhysicsSolverNode::id(0x00128008);

MObject MmdPhysicsSolverNode::aEnable;
MObject MmdPhysicsSolverNode::aInTime;
MObject MmdPhysicsSolverNode::aModelRoot;
MObject MmdPhysicsSolverNode::aOutBoneMatrices;
MObject MmdPhysicsSolverNode::aOutBoneCount;
MObject MmdPhysicsSolverNode::aOutStatus;
MObject MmdPhysicsSolverNode::aOutSolved;

MmdPhysicsSolverNode::MmdPhysicsSolverNode() = default;

MmdPhysicsSolverNode::~MmdPhysicsSolverNode() {
    freeHandles();
}

void* MmdPhysicsSolverNode::creator() {
    return new MmdPhysicsSolverNode();
}

MStatus MmdPhysicsSolverNode::initialize() {
    MFnNumericAttribute nAttr;
    MFnTypedAttribute   tAttr;
    MFnUnitAttribute    uAttr;
    MFnMessageAttribute msgAttr;

    aEnable = nAttr.create("enable", "en", MFnNumericData::kBoolean, true);
    nAttr.setStorable(true);
    nAttr.setKeyable(true);
    addAttribute(aEnable);

    aInTime = uAttr.create("inTime", "it", MFnUnitAttribute::kTime, 0.0);
    uAttr.setStorable(false);
    addAttribute(aInTime);

    aModelRoot = msgAttr.create("modelRoot", "mr");
    addAttribute(aModelRoot);

    aOutBoneMatrices = tAttr.create("outBoneMatrices", "obm", MFnData::kDoubleArray);
    tAttr.setWritable(false);
    tAttr.setStorable(false);
    addAttribute(aOutBoneMatrices);

    aOutBoneCount = nAttr.create("outBoneCount", "obc", MFnNumericData::kInt, 0);
    nAttr.setWritable(false);
    nAttr.setStorable(false);
    addAttribute(aOutBoneCount);

    aOutStatus = tAttr.create("outStatus", "ost", MFnData::kString);
    tAttr.setWritable(false);
    tAttr.setStorable(false);
    addAttribute(aOutStatus);

    aOutSolved = nAttr.create("outSolved", "osv", MFnNumericData::kBoolean, false);
    nAttr.setWritable(false);
    nAttr.setStorable(false);
    addAttribute(aOutSolved);

    MObject inputs[] = { aEnable, aInTime };
    MObject outputs[] = { aOutBoneMatrices, aOutBoneCount, aOutStatus, aOutSolved };
    for (auto& in : inputs) {
        for (auto& out : outputs) {
            attributeAffects(in, out);
        }
    }

    return MS::kSuccess;
}

bool MmdPhysicsSolverNode::isOutputPlug(const MPlug& plug) const {
    MObject attr = plug.attribute();
    return attr == aOutBoneMatrices || attr == aOutBoneCount ||
           attr == aOutStatus || attr == aOutSolved;
}

MStatus MmdPhysicsSolverNode::compute(const MPlug& plug, MDataBlock& data) {
    if (!isOutputPlug(plug))
        return MS::kUnknownParameter;

    bool enable = data.inputValue(aEnable).asBool();
    if (!enable) {
        writeDisabledOutputs(data);
        return MS::kSuccess;
    }

    double currentTime = data.inputValue(aInTime).asTime().as(MTime::kSeconds);

    if (!initialized_) {
        tryInitialize(data);
    }

    if (!hasPhysicsData_) {
        writeNoDataOutputs(data);
        return MS::kSuccess;
    }

    // Same-time idempotence
    if (std::abs(currentTime - lastTime_) < kTimeEpsilon) {
        writeOutputs(data, "cached", true);
        return MS::kSuccess;
    }

    double dt = currentTime - lastTime_;
    std::string status;

    if (lastTime_ > -1e20 && dt > 0.0 && dt < kMaxForwardDt) {
        // Forward step
        bridge_.evaluateCurrentPoseBeforePhysics();
        bridge_.stepPhysicsWorldRuntime(static_cast<float>(dt));
        bridge_.evaluateCurrentPoseAfterPhysics();
        status = "stepped";
    } else {
        // Jump / backward / first eval → reset
        bridge_.setPhysicsMode(MMD_RUNTIME_PHYSICS_MODE_LIVE);
        bridge_.evaluateCurrentPoseBeforePhysics();
        bridge_.resetPhysicsWorld();
        status = "reset";
    }

    lastTime_ = currentTime;

    // Update cached matrices
    std::vector<float> worldMats = bridge_.getWorldMatrices();
    size_t matCount = worldMats.size() / 16;
    cachedFlat_.resize(matCount * 16);
    for (size_t i = 0; i < matCount; ++i) {
        mmdMatrixToMaya(&worldMats[i * 16], &cachedFlat_[i * 16]);
    }

    writeOutputs(data, status, true);
    return MS::kSuccess;
}

bool MmdPhysicsSolverNode::tryInitialize(MDataBlock& /* data */) {
    initialized_ = true;
    hasPhysicsData_ = false;

    // Resolve modelRoot message connection
    MFnDependencyNode fnThis(thisMObject());
    MPlug rootPlug = fnThis.findPlug("modelRoot", false);
    if (rootPlug.isNull()) return false;

    MPlugArray connections;
    rootPlug.connectedTo(connections, true, false);
    if (connections.length() == 0) return false;

    MObject rootNode = connections[0].node();
    MFnDependencyNode fnRoot(rootNode);

    // Read base64-encoded PMX payload
    MPlug payloadPlug;
    MStatus st;
    payloadPlug = fnRoot.findPlug("mmd_source_pmx_payload", false, &st);
    if (st != MS::kSuccess || payloadPlug.isNull()) return false;

    MString payloadStr = payloadPlug.asString();
    if (payloadStr.length() == 0) return false;

    std::vector<uint8_t> pmxBytes = base64Decode(
        payloadStr.asChar(), payloadStr.length());
    if (pmxBytes.empty()) return false;

    // Create model
    if (!bridge_.createModelFromPmx(pmxBytes.data(), pmxBytes.size()))
        return false;

    // Create instance
    if (!bridge_.createInstance()) {
        bridge_.freeModel();
        return false;
    }

    // Create physics world
    if (!bridge_.createPhysicsWorldFromPmx(pmxBytes.data(), pmxBytes.size())) {
        bridge_.freeInstance();
        bridge_.freeModel();
        return false;
    }

    boneCount_ = bridge_.boneCount();
    hasPhysicsData_ = true;
    return true;
}

void MmdPhysicsSolverNode::freeHandles() {
    bridge_.freePhysicsWorld();
    bridge_.freeInstance();
    bridge_.freeClip();
    bridge_.freeModel();
    initialized_ = false;
    hasPhysicsData_ = false;
    lastTime_ = -1e30;
    cachedFlat_.clear();
}

void MmdPhysicsSolverNode::writeDisabledOutputs(MDataBlock& data) {
    data.outputValue(aOutSolved).setBool(false);

    MFnStringData fnStr;
    data.outputValue(aOutStatus).setMObject(fnStr.create("disabled"));

    data.outputValue(aOutBoneCount).setInt(static_cast<int>(boneCount_));

    MFnDoubleArrayData fnArr;
    data.outputValue(aOutBoneMatrices).setMObject(fnArr.create(MDoubleArray()));

    data.setClean(aOutBoneMatrices);
    data.setClean(aOutBoneCount);
    data.setClean(aOutStatus);
    data.setClean(aOutSolved);
}

void MmdPhysicsSolverNode::writeNoDataOutputs(MDataBlock& data) {
    data.outputValue(aOutSolved).setBool(false);

    MFnStringData fnStr;
    data.outputValue(aOutStatus).setMObject(fnStr.create("no physics data"));

    data.outputValue(aOutBoneCount).setInt(0);

    MFnDoubleArrayData fnArr;
    data.outputValue(aOutBoneMatrices).setMObject(fnArr.create(MDoubleArray()));

    data.setClean(aOutBoneMatrices);
    data.setClean(aOutBoneCount);
    data.setClean(aOutStatus);
    data.setClean(aOutSolved);
}

void MmdPhysicsSolverNode::writeOutputs(MDataBlock& data,
                                         const std::string& status, bool solved)
{
    data.outputValue(aOutSolved).setBool(solved);

    MFnStringData fnStr;
    data.outputValue(aOutStatus).setMObject(fnStr.create(status.c_str()));

    data.outputValue(aOutBoneCount).setInt(static_cast<int>(boneCount_));

    MDoubleArray arr(static_cast<unsigned>(cachedFlat_.size()));
    for (unsigned i = 0; i < static_cast<unsigned>(cachedFlat_.size()); ++i) {
        arr[i] = cachedFlat_[i];
    }
    MFnDoubleArrayData fnArr;
    data.outputValue(aOutBoneMatrices).setMObject(fnArr.create(arr));

    data.setClean(aOutBoneMatrices);
    data.setClean(aOutBoneCount);
    data.setClean(aOutStatus);
    data.setClean(aOutSolved);
}

void MmdPhysicsSolverNode::mmdMatrixToMaya(const float* src16, double* dst16) {
    // mmd-anim output: column-major, right-handed (Z+ forward)
    // Maya: row-major, right-handed (Z- forward for imported MMD)
    // Flip Z row and Z column for handedness conversion
    for (int r = 0; r < 4; ++r) {
        for (int c = 0; c < 4; ++c) {
            double v = static_cast<double>(src16[c * 4 + r]); // col-major → row-major
            if (r == 2 || c == 2) v = -v; // Z-flip
            dst16[r * 4 + c] = v;
        }
    }
}
