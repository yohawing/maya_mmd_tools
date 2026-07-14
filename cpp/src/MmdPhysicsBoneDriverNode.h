/**
 * MmdPhysicsBoneDriverNode.h
 *
 * C++ mmdPhysicsBoneDriver (TypeId 0x00128009).
 * Python prototype と同一 TypeId — mutual-exclusion で一方だけ登録される。
 * mmdPhysicsSolver の flat world matrix 配列から一本のボーンを取り出し、
 * parent inverse / jointOrient / rotateAxis / rotateOrder を反映した
 * Maya local translate/rotate に変換する。FFI 依存なし・純粋な行列計算。
 */

#pragma once

#include <maya/MPxNode.h>
#include <maya/MTypeId.h>
#include <maya/MDoubleArray.h>
#include <maya/MMatrix.h>

class MmdPhysicsBoneDriverNode : public MPxNode {
public:
    MmdPhysicsBoneDriverNode();
    ~MmdPhysicsBoneDriverNode() override;

    MStatus compute(const MPlug& plug, MDataBlock& data) override;

    // kParallel — 状態を持たない純粋計算
    SchedulingType schedulingType() const override {
        return SchedulingType::kParallel;
    }

    static void* creator();
    static MStatus initialize();

    static const MTypeId id;

    // Inputs
    static MObject aInSolverBoneMatrices;
    static MObject aInSolverBoneCount;
    static MObject aInBoneIndex;
    static MObject aInParentBoneIndex;
    static MObject aInParentInverseMatrix;

    static MObject aInJointOrient;
    static MObject aInJointOrientX;
    static MObject aInJointOrientY;
    static MObject aInJointOrientZ;

    static MObject aInRotateAxis;
    static MObject aInRotateAxisX;
    static MObject aInRotateAxisY;
    static MObject aInRotateAxisZ;

    static MObject aInRotateOrder;
    static MObject aInSolved;
    static MObject aEnable;

    // Outputs
    static MObject aOutTranslate;
    static MObject aOutTranslateX;
    static MObject aOutTranslateY;
    static MObject aOutTranslateZ;

    static MObject aOutRotate;
    static MObject aOutRotateX;
    static MObject aOutRotateY;
    static MObject aOutRotateZ;

private:
    bool isOutputPlug(const MPlug& plug) const;
    void writeIdentity(MDataBlock& data) const;
    static MMatrix extractMatrix(const MDoubleArray& arr, int boneIndex);

    static MObject createDouble3(
        const char* longName, const char* shortName,
        MObject& childX, MObject& childY, MObject& childZ);

    static MObject createAngle3(
        const char* longName, const char* shortName,
        MObject& childX, MObject& childY, MObject& childZ);
};
