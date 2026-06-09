/**
 * mmdRuntimeNode.cpp
 *
 * MmdRuntimeNode の実装 (Phase 2 初期版)。
 *
 * 現在の実装はスケルトン:
 * - 登録可能
 * - time 入力で compute が呼ばれる
 * - 内部 bridge を使って evaluate (データ未接続時は何もしない)
 * - 出力アトリビュートはプレースホルダー
 *
 * TODO (後続):
 * - PMX/VMD データの受け渡し (MFnByteArrayData や string path + ロード)
 * - 実際の行列出力 (MFnMatrixData の配列 or フラット float)
 * - キャッシュ & dirty 管理
 * - IK on/off, morph など
 */

#include "mmdRuntimeNode.h"

#include <maya/MFnNumericAttribute.h>
#include <maya/MFnTypedAttribute.h>
#include <maya/MFnMatrixAttribute.h>
#include <maya/MDataHandle.h>
#include <maya/MArrayDataHandle.h>
#include <maya/MGlobal.h>
#include <maya/MTime.h>

#include <vector>
#include <cstring>

const MTypeId MmdRuntimeNode::id(0x00123456); // TODO: 正式な ID を取得して置き換え

MObject MmdRuntimeNode::aTime;
MObject MmdRuntimeNode::aPmxData;
MObject MmdRuntimeNode::aVmdData;
MObject MmdRuntimeNode::aFrame;
MObject MmdRuntimeNode::aWorldMatrices;
MObject MmdRuntimeNode::aMorphWeights;
MObject MmdRuntimeNode::aIkEnabled;

MmdRuntimeNode::MmdRuntimeNode() = default;

MmdRuntimeNode::~MmdRuntimeNode() = default;

void* MmdRuntimeNode::creator() {
    return new MmdRuntimeNode();
}

MStatus MmdRuntimeNode::initialize() {
    MStatus status;

    // time input
    MFnNumericAttribute nAttr;
    aTime = nAttr.create("time", "tm", MFnNumericData::kTime, 0.0, &status);
    nAttr.setWritable(true);
    nAttr.setReadable(false);
    nAttr.setKeyable(true);
    addAttribute(aTime);

    // placeholder for data (later: byte array or path)
    MFnTypedAttribute tAttr;
    aPmxData = tAttr.create("pmxData", "pmx", MFnData::kString, MObject::kNullObj, &status);
    tAttr.setWritable(true);
    addAttribute(aPmxData);

    aVmdData = tAttr.create("vmdData", "vmd", MFnData::kString, MObject::kNullObj, &status);
    tAttr.setWritable(true);
    addAttribute(aVmdData);

    aFrame = nAttr.create("frame", "fr", MFnNumericData::kFloat, 0.0f, &status);
    nAttr.setWritable(true);
    addAttribute(aFrame);

    // outputs (simplified as numeric arrays for prototype)
    aWorldMatrices = nAttr.create("worldMatrices", "wm", MFnNumericData::kFloat, 0.0f, &status);
    nAttr.setArray(true);
    nAttr.setUsesArrayDataBuilder(true);
    nAttr.setWritable(false);
    nAttr.setReadable(true);
    addAttribute(aWorldMatrices);

    aMorphWeights = nAttr.create("morphWeights", "mw", MFnNumericData::kFloat, 0.0f, &status);
    nAttr.setArray(true);
    nAttr.setUsesArrayDataBuilder(true);
    nAttr.setWritable(false);
    nAttr.setReadable(true);
    addAttribute(aMorphWeights);

    aIkEnabled = nAttr.create("ikEnabled", "ik", MFnNumericData::kBoolean, false, &status);
    nAttr.setArray(true);
    nAttr.setUsesArrayDataBuilder(true);
    nAttr.setWritable(false);
    nAttr.setReadable(true);
    addAttribute(aIkEnabled);

    // attribute affects
    attributeAffects(aTime, aWorldMatrices);
    attributeAffects(aTime, aMorphWeights);
    attributeAffects(aTime, aIkEnabled);
    attributeAffects(aFrame, aWorldMatrices);
    // ... 同様に他の出力にも

    return MS::kSuccess;
}

MStatus MmdRuntimeNode::compute(const MPlug& plug, MDataBlock& data) {
    MStatus status;

    if (plug == aWorldMatrices || plug == aMorphWeights || plug == aIkEnabled) {
        // 時間取得
        MTime timeVal = data.inputValue(aTime, &status).asTime();
        float frame = static_cast<float>(timeVal.as(MTime::kFilm));  // 例: film (24fps) 単位。必要に応じて調整

        // データロード (簡易版: string をパスとして扱う)
        MString pmxStr = data.inputValue(aPmxData, &status).asString();
        MString vmdStr = data.inputValue(aVmdData, &status).asString();

        if (!modelLoaded_ && pmxStr.length() > 0) {
            std::string p = pmxStr.asChar();
            MGlobal::displayInfo(MString("[mmdRuntimeNode] Loading PMX from: ") + pmxStr);
            modelLoaded_ = bridge_.createModelFromPmxFile(p);
            if (!modelLoaded_) {
                MGlobal::displayError("[mmdRuntimeNode] Failed to load PMX model from file.");
            }
        }

        if (!clipLoaded_ && vmdStr.length() > 0 && modelLoaded_) {
            std::string v = vmdStr.asChar();
            MGlobal::displayInfo(MString("[mmdRuntimeNode] Loading VMD from: ") + vmdStr);
            clipLoaded_ = bridge_.createClipFromVmdFile(v);
            if (!clipLoaded_) {
                MGlobal::displayError("[mmdRuntimeNode] Failed to load VMD clip.");
            }
        }

        if (modelLoaded_ && clipLoaded_) {
            // 評価
            bool evalOk = bridge_.evaluateFrame(frame);
            if (!evalOk) {
                MGlobal::displayWarning("[mmdRuntimeNode] Evaluate failed (check model/clip).");
            }

            // 出力: World Matrices (flat float array: boneCount * 16)
            MArrayDataHandle outWorld = data.outputArrayValue(aWorldMatrices, &status);
            CHECK_MSTATUS(status);

            std::vector<float> mats = bridge_.getWorldMatrices();
            MArrayDataBuilder builder(&data, aWorldMatrices, static_cast<unsigned int>(mats.size()), &status);

            for (size_t i = 0; i < mats.size(); ++i) {
                MDataHandle h = builder.addElement(static_cast<unsigned int>(i));
                h.setFloat(mats[i]);
            }
            outWorld.set(builder);
            outWorld.setAllClean();

            // Morph Weights
            MArrayDataHandle outMorph = data.outputArrayValue(aMorphWeights, &status);
            std::vector<float> weights = bridge_.getMorphWeights();
            MArrayDataBuilder morphBuilder(&data, aMorphWeights, static_cast<unsigned int>(weights.size()), &status);
            for (size_t i = 0; i < weights.size(); ++i) {
                MDataHandle h = morphBuilder.addElement(static_cast<unsigned int>(i));
                h.setFloat(weights[i]);
            }
            outMorph.set(morphBuilder);
            outMorph.setAllClean();

            // IK Enabled (as int for simplicity)
            MArrayDataHandle outIk = data.outputArrayValue(aIkEnabled, &status);
            std::vector<uint8_t> iks = bridge_.getIkEnabled();
            MArrayDataBuilder ikBuilder(&data, aIkEnabled, static_cast<unsigned int>(iks.size()), &status);
            for (size_t i = 0; i < iks.size(); ++i) {
                MDataHandle h = ikBuilder.addElement(static_cast<unsigned int>(i));
                h.setBool(iks[i] != 0);
            }
            outIk.set(ikBuilder);
            outIk.setAllClean();
        } else {
            // データ未ロード時はクリーンに
            data.outputArrayValue(aWorldMatrices).setAllClean();
            data.outputArrayValue(aMorphWeights).setAllClean();
            data.outputArrayValue(aIkEnabled).setAllClean();
        }

        data.setClean(plug);
        return MS::kSuccess;
    }

    return MS::kUnknownParameter;
}