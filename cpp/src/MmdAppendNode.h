/**
 * MmdAppendNode.h
 *
 * Maya カスタムノード: mmdAppendNode
 *
 * Phase B:
 * - 登録可能な MPxNode
 * - 属性: inputTranslate(double3), inputRotate(double3),
 *          parentTranslate(double3), parentRotate(double3),
 *          grantRate(double), enableTranslate(bool), enableRotate(bool),
 *          outputTranslate(double3), outputRotate(double3)
 * - compute: parent contribution を grantRate で input に加える
 */

#pragma once

#include <maya/MPxNode.h>
#include <maya/MFnNumericAttribute.h>
#include <maya/MFnCompoundAttribute.h>
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

private:
    // double3 compound 作成のヘルパー
    static MObject createDouble3Attribute(
        const MString& longName,
        const MString& shortName,
        MObject& childX,
        MObject& childY,
        MObject& childZ,
        double defaultVal = 0.0);
};
