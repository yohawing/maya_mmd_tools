/**
 * mmdRuntimeNode.h
 *
 * Maya カスタムノード: mmdRuntimeInstance
 *
 * 目的 (Phase 2):
 * - mmd-anim runtime のインスタンスを Maya DG 内に保持
 * - time 入力を受け取り、内部で evaluate
 * - 出力: worldMatrices (配列), morphWeights, ikStates など
 *
 * 将来的にこのノードの出力をジョイントの matrix や blendShape weight に接続、
 * または deformer から参照してライブ変形を実現。
 */

#pragma once

#include <maya/MPxNode.h>
#include <maya/MFnNumericAttribute.h>
#include <maya/MFnMatrixAttribute.h>
#include <maya/MFnTypedAttribute.h>
#include <maya/MTypeId.h>

#include "mmdRuntimeBridge.h"

class MmdRuntimeNode : public MPxNode {
public:
    MmdRuntimeNode();
    ~MmdRuntimeNode() override;

    MStatus compute(const MPlug& plug, MDataBlock& data) override;

    static void* creator();
    static MStatus initialize();

    // Type ID (ユニークに)
    static const MTypeId id;

    // Attributes
    static MObject aTime;
    static MObject aPmxData;      // 生 PMX データ (string or byte array 将来)
    static MObject aVmdData;      // 生 VMD データ
    static MObject aFrame;        // 評価フレーム (time から来る)

    // 出力
    static MObject aWorldMatrices;   // 出力: ボーン数 x matrix (または float 配列)
    static MObject aMorphWeights;    // float 配列
    static MObject aIkEnabled;       // bool/int 配列

private:
    mmd::RuntimeBridge bridge_;
    bool modelLoaded_ = false;
    bool clipLoaded_ = false;
};