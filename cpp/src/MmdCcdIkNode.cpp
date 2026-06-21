/**
 * MmdCcdIkNode の実装
 *
 * Phase A (1-link) + Phase B (multi-link 2D CCD)。
 * - 既存の 1-link 解析解は既存属性の挙動と互換。
 * - inputChain が有効な場合のみ multi-link 2D CCD を実行し、
 *   outputLinkAngles / outputLinkRotates を更新。
 */

#include "MmdCcdIkNode.h"

#include <maya/MFnNumericAttribute.h>
#include <maya/MFnCompoundAttribute.h>
#include <maya/MFnTypedAttribute.h>
#include <maya/MFnDoubleArrayData.h>
#include <maya/MDoubleArray.h>
#include <maya/MDataHandle.h>
#include <maya/MPlug.h>
#include <maya/MGlobal.h>

#include <array>
#include <cmath>
#include <vector>

namespace {
constexpr double kPi = 3.14159265358979323846;
}

const MTypeId MmdCcdIkNode::id(0x00123458);

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

MStatus MmdCcdIkNode::initialize() {
    MStatus status;
    MFnNumericAttribute nAttr;
    MFnTypedAttribute tAttr;

    // --- 入力: inputRoot(double3) ---
    aInputRoot = createDouble3Attribute(
        "inputRoot", "irt",
        aInputRootX, aInputRootY, aInputRootZ, 0.0);
    addAttribute(aInputRoot);

    // --- 入力: inputEffector(double3) ---
    aInputEffector = createDouble3Attribute(
        "inputEffector", "ief",
        aInputEffectorX, aInputEffectorY, aInputEffectorZ, 0.0);
    addAttribute(aInputEffector);

    // --- 入力: target(double3) ---
    aTarget = createDouble3Attribute(
        "target", "tgt",
        aTargetX, aTargetY, aTargetZ, 0.0);
    addAttribute(aTarget);

    // --- 入力: enabled(bool, default true) ---
    aEnabled = nAttr.create("enabled", "enb", MFnNumericData::kBoolean, true, &status);
    nAttr.setStorable(true);
    nAttr.setKeyable(true);
    nAttr.setWritable(true);
    nAttr.setReadable(false);
    addAttribute(aEnabled);

    // --- 入力: iterations(int, default 1, min 1) ---
    aIterations = nAttr.create("iterations", "itn", MFnNumericData::kInt, 1, &status);
    nAttr.setStorable(true);
    nAttr.setKeyable(true);
    nAttr.setWritable(true);
    nAttr.setReadable(false);
    nAttr.setMin(1);
    addAttribute(aIterations);

    // --- 入力: angleLimit(double degrees, default 180.0, min 0) ---
    aAngleLimit = nAttr.create("angleLimit", "alm", MFnNumericData::kDouble, 180.0, &status);
    nAttr.setStorable(true);
    nAttr.setKeyable(true);
    nAttr.setWritable(true);
    nAttr.setReadable(false);
    nAttr.setMin(0.0);
    addAttribute(aAngleLimit);

    // --- 入力: inputChain(doubleArray) ---
    aInputChain = tAttr.create("inputChain", "ichn", MFnData::kDoubleArray, MObject::kNullObj, &status);
    tAttr.setStorable(true);
    tAttr.setKeyable(false);
    tAttr.setWritable(true);
    tAttr.setReadable(true);
    addAttribute(aInputChain);

    // --- 出力: outputRotate(double3) ---
    aOutputRotate = createDouble3Attribute(
        "outputRotate", "ort",
        aOutputRotateX, aOutputRotateY, aOutputRotateZ, 0.0);
    {
        MFnCompoundAttribute cAttr(aOutputRotate, &status);
        cAttr.setWritable(false);
        cAttr.setReadable(true);
        cAttr.setStorable(false);
        cAttr.setKeyable(false);
        MFnNumericAttribute nChild;
        nChild.setObject(aOutputRotateX);
        nChild.setWritable(false);
        nChild.setKeyable(false);
        nChild.setObject(aOutputRotateY);
        nChild.setWritable(false);
        nChild.setKeyable(false);
        nChild.setObject(aOutputRotateZ);
        nChild.setWritable(false);
        nChild.setKeyable(false);
    }
    addAttribute(aOutputRotate);

    // --- 出力: outputAngle(double) ---
    aOutputAngle = nAttr.create("outputAngle", "oan", MFnNumericData::kDouble, 0.0, &status);
    nAttr.setWritable(false);
    nAttr.setReadable(true);
    nAttr.setStorable(false);
    nAttr.setKeyable(false);
    addAttribute(aOutputAngle);

    // --- 出力: solved(bool) ---
    aSolved = nAttr.create("solved", "sol", MFnNumericData::kBoolean, false, &status);
    nAttr.setWritable(false);
    nAttr.setReadable(true);
    nAttr.setStorable(false);
    nAttr.setKeyable(false);
    addAttribute(aSolved);

    // --- 出力: outputLinkAngles(doubleArray) ---
    aOutputLinkAngles = tAttr.create("outputLinkAngles", "ola", MFnData::kDoubleArray, MObject::kNullObj, &status);
    tAttr.setWritable(false);
    tAttr.setReadable(true);
    tAttr.setStorable(false);
    tAttr.setKeyable(false);
    addAttribute(aOutputLinkAngles);

    // --- 出力: outputLinkRotates(doubleArray) ---
    aOutputLinkRotates = tAttr.create("outputLinkRotates", "olr", MFnData::kDoubleArray, MObject::kNullObj, &status);
    tAttr.setWritable(false);
    tAttr.setReadable(true);
    tAttr.setStorable(false);
    tAttr.setKeyable(false);
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

    return MS::kSuccess;
}

MStatus MmdCcdIkNode::compute(const MPlug& plug, MDataBlock& data) {
    MStatus status;

    bool isRotate = (plug == aOutputRotate ||
                     plug == aOutputRotateX ||
                     plug == aOutputRotateY ||
                     plug == aOutputRotateZ);
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

    if (isRotate) {
        if (plug == aOutputRotate || plug.isCompound()) {
            MDataHandle outHandle = data.outputValue(aOutputRotate, &status);
            outHandle.set(outRotX, outRotY, outRotZ);
            outHandle.setClean();
        }
        if (plug == aOutputRotateX || plug.parent() == aOutputRotate) {
            MDataHandle hX = data.outputValue(aOutputRotateX, &status);
            hX.set(outRotX);
            hX.setClean();
        }
        if (plug == aOutputRotateY || plug.parent() == aOutputRotate) {
            MDataHandle hY = data.outputValue(aOutputRotateY, &status);
            hY.set(outRotY);
            hY.setClean();
        }
        if (plug == aOutputRotateZ || plug.parent() == aOutputRotate) {
            MDataHandle hZ = data.outputValue(aOutputRotateZ, &status);
            hZ.set(outRotZ);
            hZ.setClean();
        }
    }

    if (isAngle) {
        MDataHandle hAngle = data.outputValue(aOutputAngle, &status);
        hAngle.set(outAngleDeg);
        hAngle.setClean();
    }

    if (isSolved) {
        MDataHandle hSolved = data.outputValue(aSolved, &status);
        hSolved.set(outSolved);
        hSolved.setClean();
    }

    if (isLinkAngles) {
        MFnDoubleArrayData dataObject;
        MObject linkAnglesObj = dataObject.create(outLinkAngles, &status);
        MDataHandle hLinkAngles = data.outputValue(aOutputLinkAngles, &status);
        hLinkAngles.setMObject(linkAnglesObj);
        hLinkAngles.setClean();
    }

    if (isLinkRotates) {
        MFnDoubleArrayData dataObject;
        MObject linkRotatesObj = dataObject.create(outLinkRotates, &status);
        MDataHandle hLinkRotates = data.outputValue(aOutputLinkRotates, &status);
        hLinkRotates.setMObject(linkRotatesObj);
        hLinkRotates.setClean();
    }

    data.setClean(plug);
    return MS::kSuccess;
}
