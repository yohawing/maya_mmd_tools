/**
 * MmdCcdIkNode.h
 *
 * Maya カスタムノード: mmdCcdIkNode
 *
 * Phase A:
 * - 登録可能な MPxNode, TypeId 0x00123458
 * - 属性: inputRoot(double3), inputEffector(double3), target(double3),
 *          enabled(bool default true),
 *          iterations(int default 1), angleLimit(double default 180.0),
 *          outputRotate(double3 degrees), outputAngle(double degrees),
 *          solved(bool)
 * - compute: 1-link analytic CCDIK (Z 軸回転のみ)
 *   root->effector vector を root->target vector へ近づける Z 軸角度を
 *   atan2/cross/dot で計算し、iterations * angleLimit でクランプして
 *   outputRotate=(0,0,clampedAngleDeg), outputAngle=clampedAngleDeg,
 *   solved=true を出力。
 *   enabled=false またはゼロ長 vector 時は (0,0,0), 0, solved=false。
 */

#pragma once

#include <maya/MPxNode.h>
#include <maya/MFnNumericAttribute.h>
#include <maya/MFnCompoundAttribute.h>
#include <maya/MFnTypedAttribute.h>
#include <maya/MFnUnitAttribute.h>
#include <maya/MFnMatrixAttribute.h>
#include <maya/MTypeId.h>

class MmdCcdIkNode : public MPxNode {
public:
    MmdCcdIkNode();
    ~MmdCcdIkNode() override;

    MStatus compute(const MPlug& plug, MDataBlock& data) override;

    static void* creator();
    static MStatus initialize();

    static const MTypeId id;

    // --- 入力 ---
    // inputRoot(double3)
    static MObject aInputRoot;
    static MObject aInputRootX;
    static MObject aInputRootY;
    static MObject aInputRootZ;

    // inputEffector(double3)
    static MObject aInputEffector;
    static MObject aInputEffectorX;
    static MObject aInputEffectorY;
    static MObject aInputEffectorZ;

    // target(double3)
    static MObject aTarget;
    static MObject aTargetX;
    static MObject aTargetY;
    static MObject aTargetZ;

    // enabled(bool)
    static MObject aEnabled;

    // iterations(int, default 1, min 1)
    static MObject aIterations;

    // angleLimit(double degrees, default 180.0, min 0)
    static MObject aAngleLimit;

    // inputChain(doubleArray)
    static MObject aInputChain;

    // Python mmdCcdIk name-compatible inputs. outputRotate is now an array
    // angle compound; solver parity is still a later slice.
    static MObject aChainJson;

    static MObject aGoal;
    static MObject aGoalX;
    static MObject aGoalY;
    static MObject aGoalZ;

    static MObject aGoalWorldMatrix;

    static MObject aInputRotateArray;
    static MObject aInputRotateArrayX;
    static MObject aInputRotateArrayY;
    static MObject aInputRotateArrayZ;

    static MObject aInputTranslateArray;
    static MObject aInputTranslateArrayX;
    static MObject aInputTranslateArrayY;
    static MObject aInputTranslateArrayZ;

    // --- 出力 ---
    // outputRotate(double3)
    static MObject aOutputRotate;
    static MObject aOutputRotateX;
    static MObject aOutputRotateY;
    static MObject aOutputRotateZ;

    // outputAngle(double)
    static MObject aOutputAngle;

    // solved(bool)
    static MObject aSolved;

    // outputLinkAngles(doubleArray)
    static MObject aOutputLinkAngles;

    // outputLinkRotates(doubleArray)
    static MObject aOutputLinkRotates;

private:
    static MObject createDouble3Attribute(
        const MString& longName,
        const MString& shortName,
        MObject& childX,
        MObject& childY,
        MObject& childZ,
        double defaultVal = 0.0);

    static MObject createAngle3ArrayAttribute(
        const MString& longName,
        const MString& shortName,
        MObject& childX,
        MObject& childY,
        MObject& childZ);

    static MObject createAngle3ArrayOutputAttribute(
        const MString& longName,
        const MString& shortName,
        MObject& childX,
        MObject& childY,
        MObject& childZ);

    static MObject createDouble3ArrayAttribute(
        const MString& longName,
        const MString& shortName,
        MObject& childX,
        MObject& childY,
        MObject& childZ);
};
