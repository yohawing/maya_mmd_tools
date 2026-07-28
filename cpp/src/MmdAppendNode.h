/**
 * MmdAppendNode.h
 *
 * Maya カスタムノード: mmdAppend (C++ 実装)
 *
 * TypeId 0x00128001 (Python 版と統一)
 *
 * MMD 付与ボーン演算ノード。
 * - compute: parent contribution を grantRate で input に加える
 */

#pragma once

#include <maya/MPxNode.h>
#include <maya/MFnNumericAttribute.h>
#include <maya/MFnCompoundAttribute.h>
#include <maya/MFnUnitAttribute.h>
#include <maya/MFnTypedAttribute.h>
#include <maya/MTypeId.h>

class MmdAppendNode : public MPxNode {
public:
    MmdAppendNode();
    ~MmdAppendNode() override;

    MStatus compute(const MPlug& plug, MDataBlock& data) override;

    static void* creator();
    static MStatus initialize();

    static const MTypeId id;

    // --- 入力 ---
    // inputTranslate(double3)
    static MObject aInputTranslate;
    static MObject aInputTranslateX;
    static MObject aInputTranslateY;
    static MObject aInputTranslateZ;

    // inputRotate(double3)
    static MObject aInputRotate;
    static MObject aInputRotateX;
    static MObject aInputRotateY;
    static MObject aInputRotateZ;

    // parentTranslate(double3)
    static MObject aParentTranslate;
    static MObject aParentTranslateX;
    static MObject aParentTranslateY;
    static MObject aParentTranslateZ;

    // parentRotate(double3)
    static MObject aParentRotate;
    static MObject aParentRotateX;
    static MObject aParentRotateY;
    static MObject aParentRotateZ;

    // grantRate(double)
    static MObject aGrantRate;

    // enableTranslate(bool)
    static MObject aEnableTranslate;

    // enableRotate(bool)
    static MObject aEnableRotate;

    // Python mmdAppend name-compatible schema inputs. These are kept alongside
    // the prototype Phase B attrs until angle-unit and compute parity are ready.
    static MObject aBaseTranslate;
    static MObject aBaseTranslateX;
    static MObject aBaseTranslateY;
    static MObject aBaseTranslateZ;

    static MObject aBaseRotate;
    static MObject aBaseRotateX;
    static MObject aBaseRotateY;
    static MObject aBaseRotateZ;

    static MObject aSourceTranslate;
    static MObject aSourceTranslateX;
    static MObject aSourceTranslateY;
    static MObject aSourceTranslateZ;

    static MObject aSourceRotate;
    static MObject aSourceRotateX;
    static MObject aSourceRotateY;
    static MObject aSourceRotateZ;

    static MObject aSourceJointOrient;
    static MObject aSourceJointOrientX;
    static MObject aSourceJointOrientY;
    static MObject aSourceJointOrientZ;

    // Optional flat [x,y,z,w] array supplied by mmdCcdIk. An empty array or
    // negative index falls back to sourceRotate + sourceJointOrient.
    static MObject aSourceMmdLinkQuaternions;
    static MObject aSourceMmdLinkIndex;

    // Optional bind-space matrices for exact native MMD -> Maya append
    // conversion.  These are populated once at rig creation time and only
    // used when sourceMmdLinkQuaternions is connected.
    static MObject aUseTargetBindMatrices;
    static MObject aTargetMayaBindWorldMatrix;
    static MObject aTargetNoOrientBindWorldMatrix;
    static MObject aParentMayaBindWorldMatrix;
    static MObject aParentNoOrientBindWorldMatrix;

    static MObject aTargetJointOrient;
    static MObject aTargetJointOrientX;
    static MObject aTargetJointOrientY;
    static MObject aTargetJointOrientZ;

    static MObject aRatio;
    static MObject aAffectRotation;
    static MObject aAffectTranslation;
    static MObject aLocalAppend;
    static MObject aSchemaMode;

    // --- 出力 ---
    // outputTranslate(double3)
    static MObject aOutputTranslate;
    static MObject aOutputTranslateX;
    static MObject aOutputTranslateY;
    static MObject aOutputTranslateZ;

    // outputRotate(double3)
    static MObject aOutputRotate;
    static MObject aOutputRotateX;
    static MObject aOutputRotateY;
    static MObject aOutputRotateZ;

    static MObject aAppendTranslate;
    static MObject aAppendTranslateX;
    static MObject aAppendTranslateY;
    static MObject aAppendTranslateZ;

    static MObject aAppendRotate;
    static MObject aAppendRotateX;
    static MObject aAppendRotateY;
    static MObject aAppendRotateZ;

private:
    // double3 compound 作成のヘルパー
    static MObject createDouble3Attribute(
        const MString& longName,
        const MString& shortName,
        MObject& childX,
        MObject& childY,
        MObject& childZ,
        double defaultVal = 0.0);

    static MObject createAngle3Attribute(
        const MString& longName,
        const MString& shortName,
        MObject& childX,
        MObject& childY,
        MObject& childZ,
        double defaultVal = 0.0);

    static void markDouble3Output(
        MObject& compound,
        MObject& childX,
        MObject& childY,
        MObject& childZ);

    static void markAngle3Output(
        MObject& compound,
        MObject& childX,
        MObject& childY,
        MObject& childZ);
};
