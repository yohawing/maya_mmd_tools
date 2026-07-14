/**
 * MmdPhysicsSolverNode.h
 *
 * C++ mmdPhysicsSolver (TypeId 0x00128008).
 * Python prototype と同一 TypeId — mutual-exclusion で一方だけ登録される。
 */

#pragma once

#include <maya/MPxNode.h>
#include <maya/MTypeId.h>
#include <maya/MDoubleArray.h>
#include <maya/MString.h>
#include <maya/MMatrix.h>

#include "mmdRuntimeBridge.h"

#include <vector>
#include <string>
#include <unordered_map>
#include <unordered_set>

class MmdPhysicsSolverNode : public MPxNode {
public:
    MmdPhysicsSolverNode();
    ~MmdPhysicsSolverNode() override;

    MStatus compute(const MPlug& plug, MDataBlock& data) override;

    // kGloballySerial — physics world は thread-safe でない
    SchedulingType schedulingType() const override {
        return SchedulingType::kGloballySerial;
    }

    static void* creator();
    static MStatus initialize();

    static const MTypeId id;

    // Inputs
    static MObject aEnable;
    static MObject aInputMode;
    static MObject aInTime;
    static MObject aModelRoot;
    static MObject aInWorldSettings;
    static MObject aInKinematicWorldMatrix;

    // Outputs
    static MObject aOutBoneMatrices;
    static MObject aOutBoneCount;
    static MObject aOutStatus;
    static MObject aOutSolved;

    static constexpr short kInputModeRest = 0;
    static constexpr short kInputModeMayaPose = 1;

private:
    bool tryInitialize(MDataBlock& data);
    void freeHandles();
    void writeDisabledOutputs(MDataBlock& data);
    void writeNoDataOutputs(MDataBlock& data);
    void writeOutputs(MDataBlock& data, const std::string& status, bool solved);
    bool isOutputPlug(const MPlug& plug) const;

    void buildKinematicPoseData();
    void injectKinematicPoses(MDataBlock& data);
    void forwardStep(float dt, short inputMode, MDataBlock& data);
    void resetWorld(short inputMode, MDataBlock& data);
    bool readWorldSettings(bool& outEnable, int& outResetGen);

    // Z-flip handedness conversion (MMD → Maya)
    static void mmdMatrixToMaya(const float* src16, double* dst16);
    // Maya row-major → MMD column-major with Z-flip
    static void mayaMatrixToMmd(const double* src16, float* dst16);

    mmd::RuntimeBridge bridge_;
    bool initialized_ = false;
    bool hasPhysicsData_ = false;
    size_t boneCount_ = 0;
    double lastTime_ = -1e30;
    int lastResetGeneration_ = -1;
    std::vector<double> cachedFlat_;

    // Physics bone data
    std::vector<std::string> boneJoints_;
    std::unordered_map<int, MMatrix> kinematicCorrections_;
    std::unordered_set<int> kinematicOnlyBoneIndices_;
};
