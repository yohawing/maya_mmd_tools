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
#include <maya/MFnEnumAttribute.h>
#include <maya/MFnMessageAttribute.h>
#include <maya/MFnMatrixAttribute.h>
#include <maya/MFnDoubleArrayData.h>
#include <maya/MFnDependencyNode.h>
#include <maya/MFnStringData.h>
#include <maya/MFnDagNode.h>
#include <maya/MDataBlock.h>
#include <maya/MDataHandle.h>
#include <maya/MArrayDataHandle.h>
#include <maya/MPlug.h>
#include <maya/MPlugArray.h>
#include <maya/MTime.h>
#include <maya/MGlobal.h>
#include <maya/MDoubleArray.h>
#include <maya/MMatrix.h>
#include <maya/MSelectionList.h>
#include <maya/MDagPath.h>
#include <maya/MItDag.h>

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

    MString getNodeLeafName(const MString& fullPath) {
        int idx = fullPath.rindexW('|');
        if (idx >= 0)
            return fullPath.substring(idx + 1, fullPath.length() - 1);
        idx = fullPath.rindexW(':');
        if (idx >= 0)
            return fullPath.substring(idx + 1, fullPath.length() - 1);
        return fullPath;
    }
}

const MTypeId MmdPhysicsSolverNode::id(0x00128008);

MObject MmdPhysicsSolverNode::aEnable;
MObject MmdPhysicsSolverNode::aInputMode;
MObject MmdPhysicsSolverNode::aInTime;
MObject MmdPhysicsSolverNode::aModelRoot;
MObject MmdPhysicsSolverNode::aInWorldSettings;
MObject MmdPhysicsSolverNode::aInKinematicWorldMatrix;
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
    MFnEnumAttribute    eAttr;
    MFnMessageAttribute msgAttr;
    MFnMatrixAttribute  mAttr;

    aEnable = nAttr.create("enable", "en", MFnNumericData::kBoolean, true);
    nAttr.setStorable(true);
    nAttr.setKeyable(true);
    addAttribute(aEnable);

    aInputMode = eAttr.create("inputMode", "im", kInputModeMayaPose);
    eAttr.addField("rest-only", kInputModeRest);
    eAttr.addField("maya-pose", kInputModeMayaPose);
    eAttr.setStorable(true);
    eAttr.setKeyable(false);
    addAttribute(aInputMode);

    aInTime = uAttr.create("inTime", "it", MFnUnitAttribute::kTime, 0.0);
    uAttr.setStorable(false);
    addAttribute(aInTime);

    aModelRoot = msgAttr.create("modelRoot", "mr");
    addAttribute(aModelRoot);

    aInWorldSettings = msgAttr.create("inWorldSettings", "iws");
    addAttribute(aInWorldSettings);

    aInKinematicWorldMatrix = mAttr.create(
        "inKinematicWorldMatrix", "ikwm", MFnMatrixAttribute::kDouble);
    mAttr.setStorable(false);
    mAttr.setArray(true);
    mAttr.setUsesArrayDataBuilder(true);
    mAttr.setDisconnectBehavior(MFnAttribute::kDelete);
    addAttribute(aInKinematicWorldMatrix);

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

    MObject inputs[] = { aEnable, aInTime, aInputMode, aInKinematicWorldMatrix };
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

    short inputMode = data.inputValue(aInputMode).asShort();
    double currentTime = data.inputValue(aInTime).asTime().as(MTime::kSeconds);

    if (!initialized_) {
        tryInitialize(data);
    }

    if (!hasPhysicsData_) {
        writeNoDataOutputs(data);
        return MS::kSuccess;
    }

    // World settings (enable / reset generation)
    bool worldEnable = true;
    int resetGen = lastResetGeneration_;
    readWorldSettings(worldEnable, resetGen);
    if (!worldEnable) {
        writeDisabledOutputs(data);
        return MS::kSuccess;
    }

    bool forceReset = false;
    if (resetGen != lastResetGeneration_) {
        lastResetGeneration_ = resetGen;
        forceReset = true;
    }

    bool sameTime = !forceReset && lastTime_ > -1e20 &&
                    std::abs(currentTime - lastTime_) < kTimeEpsilon;

    if (sameTime && inputMode != kInputModeMayaPose) {
        writeOutputs(data, "cached", true);
        return MS::kSuccess;
    }

    std::string status;
    if (sameTime) {
        resetWorld(inputMode, data);
        status = "pose-updated";
    } else {
        double dt = currentTime - lastTime_;
        if (!forceReset && lastTime_ > -1e20 && dt > 0.0 && dt < kMaxForwardDt) {
            forwardStep(static_cast<float>(dt), inputMode, data);
            status = "stepped";
        } else {
            resetWorld(inputMode, data);
            status = "reset";
        }
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

void MmdPhysicsSolverNode::forwardStep(float dt, short inputMode, MDataBlock& data) {
    bridge_.evaluateRestPose();
    if (inputMode == kInputModeMayaPose && !kinematicCorrections_.empty()) {
        injectKinematicPoses(data);
        bridge_.evaluateCurrentPoseBeforePhysics();
    }
    bridge_.stepPhysicsWorldRuntime(dt);
    bridge_.evaluateCurrentPoseAfterPhysics();
}

void MmdPhysicsSolverNode::resetWorld(short inputMode, MDataBlock& data) {
    bridge_.setPhysicsMode(MMD_RUNTIME_PHYSICS_MODE_LIVE);
    bridge_.evaluateRestPose();
    if (inputMode == kInputModeMayaPose && !kinematicCorrections_.empty()) {
        injectKinematicPoses(data);
        bridge_.evaluateCurrentPoseBeforePhysics();
    }
    bridge_.resetPhysicsWorld();
}

void MmdPhysicsSolverNode::injectKinematicPoses(MDataBlock& data) {
    if (boneCount_ == 0) return;

    std::vector<float> flat(boneCount_ * 16, 0.0f);
    std::vector<uint8_t> mask(boneCount_, 0);

    MArrayDataHandle arrayHandle = data.inputArrayValue(aInKinematicWorldMatrix);

    for (auto& [boneIdx, correction] : kinematicCorrections_) {
        MMatrix mayaMat;
        MStatus st;
        st = arrayHandle.jumpToElement(static_cast<unsigned>(boneIdx));
        if (st == MS::kSuccess) {
            mayaMat = arrayHandle.inputValue().asMatrix();
        } else {
            // Fallback: only safe for kinematic (physicsMode=0) bones;
            // dynamic bones would read solver output → cycle.
            if (kinematicOnlyBoneIndices_.count(boneIdx) &&
                boneIdx >= 0 && boneIdx < static_cast<int>(boneJoints_.size()) &&
                !boneJoints_[boneIdx].empty()) {
                MSelectionList jSel;
                MDagPath jDag;
                if (jSel.add(boneJoints_[boneIdx].c_str()) == MS::kSuccess &&
                    jSel.getDagPath(0, jDag) == MS::kSuccess) {
                    mayaMat = jDag.inclusiveMatrix();
                } else {
                    continue;
                }
            } else {
                continue;
            }
        }

        MMatrix corrected = correction * mayaMat;
        float mmdFlat[16];
        double mayaFlat[16];
        for (int r = 0; r < 4; ++r)
            for (int c = 0; c < 4; ++c)
                mayaFlat[r * 4 + c] = corrected[r][c];

        mayaMatrixToMmd(mayaFlat, mmdFlat);
        size_t offset = static_cast<size_t>(boneIdx) * 16;
        std::memcpy(&flat[offset], mmdFlat, 16 * sizeof(float));
        mask[static_cast<size_t>(boneIdx)] = 1;
    }

    bool anySet = false;
    for (auto m : mask) { if (m) { anySet = true; break; } }
    if (anySet) {
        bridge_.applyPhysicsWorldMatrices(flat.data(), flat.size(),
                                           mask.data(), mask.size());
    }
}

void MmdPhysicsSolverNode::buildKinematicPoseData() {
    kinematicCorrections_.clear();
    kinematicOnlyBoneIndices_.clear();

    MFnDependencyNode fnThis(thisMObject());
    MPlug rootPlug = fnThis.findPlug("modelRoot", false);
    MPlugArray conns;
    rootPlug.connectedTo(conns, true, false);
    if (conns.length() == 0) return;

    MObject rootNode = conns[0].node();
    if (!rootNode.hasFn(MFn::kDagNode)) return;
    MFnDagNode fnRootDag(rootNode);
    MString rootName = fnRootDag.fullPathName();
    MFnDependencyNode fnRoot(rootNode);
    MStatus st;

    // Walk Physics/RigidBodies to find all physics-driven bones
    MString physicsPath = rootName + "|Physics";
    MString rbPath = physicsPath + "|RigidBodies";

    MSelectionList sel;
    if (sel.add(rbPath) != MS::kSuccess) return;

    MDagPath rbDagPath;
    if (sel.getDagPath(0, rbDagPath) != MS::kSuccess) return;

    std::unordered_set<int> physicsBoneIndices;
    unsigned childCount = rbDagPath.childCount();
    for (unsigned i = 0; i < childCount; ++i) {
        MObject child = rbDagPath.child(i);
        if (!child.hasFn(MFn::kTransform)) continue;
        MFnDagNode fnXform(child);
        for (unsigned s = 0; s < fnXform.childCount(); ++s) {
            MObject shapeObj = fnXform.child(s);
            if (!shapeObj.hasFn(MFn::kPluginShape)) continue;
            MFnDependencyNode fnShape(shapeObj);
            if (fnShape.typeName() != "mmdRigidBodyShape") continue;

            MPlug biPlug = fnShape.findPlug("relatedBoneIndex", false, &st);
            if (st != MS::kSuccess) continue;
            int boneIndex = biPlug.asInt();
            if (boneIndex < 0) continue;

            physicsBoneIndices.insert(boneIndex);

            MPlug pmPlug = fnShape.findPlug("physicsMode", false, &st);
            if (st == MS::kSuccess && pmPlug.asShort() == 0) {
                kinematicOnlyBoneIndices_.insert(boneIndex);
            }
        }
    }

    if (physicsBoneIndices.empty()) return;

    // Get rest pose matrices
    bridge_.evaluateRestPose();
    std::vector<float> restMats = bridge_.getWorldMatrices();
    if (restMats.empty()) return;
    size_t matCount = restMats.size() / 16;

    // Collect bone joints
    boneJoints_.clear();
    boneJoints_.resize(boneCount_);
    // Walk Armature group for joints with mmd_bone_index
    MString armPath = rootName + "|Armature";
    MSelectionList armSel;
    if (armSel.add(armPath) != MS::kSuccess) return;
    MDagPath armDag;
    if (armSel.getDagPath(0, armDag) != MS::kSuccess) return;

    MItDag dagIt(MItDag::kDepthFirst, MFn::kJoint);
    dagIt.reset(armDag, MItDag::kDepthFirst, MFn::kJoint);
    for (; !dagIt.isDone(); dagIt.next()) {
        MObject jobj = dagIt.currentItem();
        MFnDependencyNode fnJ(jobj);
        MPlug idxPlug = fnJ.findPlug("mmd_bone_index", false, &st);
        if (st != MS::kSuccess) continue;
        int idx = idxPlug.asInt();
        if (idx >= 0 && idx < static_cast<int>(boneCount_)) {
            MDagPath jPath;
            dagIt.getPath(jPath);
            boneJoints_[idx] = jPath.fullPathName().asChar();
        }
    }

    // Compute corrections
    for (int boneIdx : physicsBoneIndices) {
        if (boneIdx >= static_cast<int>(matCount) ||
            boneIdx >= static_cast<int>(boneJoints_.size()))
            continue;
        const std::string& jointPath = boneJoints_[boneIdx];
        if (jointPath.empty()) continue;

        double restMaya[4][4];
        mmdMatrixToMaya(&restMats[boneIdx * 16], &restMaya[0][0]);
        MMatrix mmdRestMayaMat(restMaya);

        // Read Maya bind world matrix
        MSelectionList jSel;
        if (jSel.add(jointPath.c_str()) != MS::kSuccess) continue;
        MDagPath jDag;
        if (jSel.getDagPath(0, jDag) != MS::kSuccess) continue;
        MMatrix bindMat = jDag.inclusiveMatrix();

        kinematicCorrections_[boneIdx] = mmdRestMayaMat * bindMat.inverse();
    }
}

bool MmdPhysicsSolverNode::readWorldSettings(bool& outEnable, int& outResetGen) {
    MFnDependencyNode fnThis(thisMObject());
    MStatus st;
    MPlug wsPlug = fnThis.findPlug("inWorldSettings", false, &st);
    if (st != MS::kSuccess || wsPlug.isNull()) return false;

    MPlugArray wsConns;
    wsPlug.connectedTo(wsConns, true, false);
    if (wsConns.length() == 0) return false;

    MFnDependencyNode fnWorld(wsConns[0].node());
    MPlug enPlug = fnWorld.findPlug("enable", false, &st);
    if (st == MS::kSuccess) outEnable = enPlug.asBool();
    MPlug rgPlug = fnWorld.findPlug("resetGeneration", false, &st);
    if (st == MS::kSuccess) outResetGen = rgPlug.asInt();
    return true;
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

    buildKinematicPoseData();

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
    lastResetGeneration_ = -1;
    cachedFlat_.clear();
    boneJoints_.clear();
    kinematicCorrections_.clear();
    kinematicOnlyBoneIndices_.clear();
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
    // P·M·P conjugation where P = diag(1,1,-1,1): negate when exactly one index is 2
    for (int r = 0; r < 4; ++r) {
        for (int c = 0; c < 4; ++c) {
            double v = static_cast<double>(src16[c * 4 + r]); // col-major → row-major
            if ((r == 2) != (c == 2)) v = -v;
            dst16[r * 4 + c] = v;
        }
    }
}

void MmdPhysicsSolverNode::mayaMatrixToMmd(const double* src16, float* dst16) {
    // Inverse: row-major Maya → column-major MMD, same P·M·P Z-flip
    for (int r = 0; r < 4; ++r) {
        for (int c = 0; c < 4; ++c) {
            double v = src16[r * 4 + c];
            if ((r == 2) != (c == 2)) v = -v;
            dst16[c * 4 + r] = static_cast<float>(v); // row-major → col-major
        }
    }
}
