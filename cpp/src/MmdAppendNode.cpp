/**
 * MmdAppendNode.cpp
 *
 * MmdAppendNode の実装 (Phase B)。
 *
 * Phase B では compute を MMD 付与に近い単体評価へ変更する:
 *   outputTranslate = inputTranslate + parentTranslate * grantRate
 *   outputRotate    = slerp(identity, parentQuat, grantRate) * inputQuat
 *
 * enableTranslate/enableRotate が false の場合は input 値をそのまま出力する。
 */

#include "MmdAppendNode.h"

#include <maya/MFnAttribute.h>
#include <maya/MFnNumericAttribute.h>
#include <maya/MFnCompoundAttribute.h>
#include <maya/MDataHandle.h>
#include <maya/MPlug.h>
#include <maya/MGlobal.h>
#include <maya/MAngle.h>
#include <maya/MEulerRotation.h>
#include <maya/MQuaternion.h>

#include "mmd_runtime.h"

#include <cmath>

namespace {
constexpr double kPi = 3.14159265358979323846;
constexpr short kSchemaModeAuto = 0;
constexpr short kSchemaModeLegacy = 1;
constexpr short kSchemaModeCompat = 2;
}

// --- Quaternion helper struct ---
struct Quat {
    double w, x, y, z;

    Quat() : w(1.0), x(0.0), y(0.0), z(0.0) {}
    Quat(double w_, double x_, double y_, double z_) : w(w_), x(x_), y(y_), z(z_) {}

    static Quat fromEulerXYZ(double axDeg, double ayDeg, double azDeg) {
        double hx = axDeg * kPi / 360.0;
        double hy = ayDeg * kPi / 360.0;
        double hz = azDeg * kPi / 360.0;
        double cx = std::cos(hx), sx = std::sin(hx);
        double cy = std::cos(hy), sy = std::sin(hy);
        double cz = std::cos(hz), sz = std::sin(hz);
        return Quat(
            cx*cy*cz + sx*sy*sz,
            sx*cy*cz - cx*sy*sz,
            cx*sy*cz + sx*cy*sz,
            cx*cy*sz - sx*sy*cz
        );
    }

    void toEulerXYZ(double& axDeg, double& ayDeg, double& azDeg) const {
        double sinY = 2.0 * (w * y - z * x);
        if (std::abs(sinY) >= 1.0) {
            ayDeg = std::copysign(90.0, sinY);
            axDeg = std::atan2(2.0 * (w * x + y * z), 1.0 - 2.0 * (x * x + y * y)) * 180.0 / kPi;
            azDeg = 0.0;
        } else {
            ayDeg = std::asin(sinY) * 180.0 / kPi;
            axDeg = std::atan2(2.0 * (w * x + y * z), 1.0 - 2.0 * (x * x + y * y)) * 180.0 / kPi;
            azDeg = std::atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z)) * 180.0 / kPi;
        }
    }

    Quat operator*(const Quat& rhs) const {
        return Quat(
            w*rhs.w - x*rhs.x - y*rhs.y - z*rhs.z,
            w*rhs.x + x*rhs.w + y*rhs.z - z*rhs.y,
            w*rhs.y - x*rhs.z + y*rhs.w + z*rhs.x,
            w*rhs.z + x*rhs.y - y*rhs.x + z*rhs.w
        );
    }

    Quat inverse() const {
        double norm = w*w + x*x + y*y + z*z;
        if (norm <= 1e-12) {
            return Quat();
        }
        return Quat(w / norm, -x / norm, -y / norm, -z / norm);
    }

    Quat normalized() const {
        double norm = std::sqrt(w*w + x*x + y*y + z*z);
        if (norm <= 1e-12) {
            return Quat();
        }
        return Quat(w / norm, x / norm, y / norm, z / norm);
    }

    static Quat slerp(const Quat& from, const Quat& to, double t) {
        double dot = from.w*to.w + from.x*to.x + from.y*to.y + from.z*to.z;
        Quat toAdj = to;
        if (dot < 0.0) {
            toAdj.w = -to.w;
            toAdj.x = -to.x;
            toAdj.y = -to.y;
            toAdj.z = -to.z;
            dot = -dot;
        }
        const double eps = 1e-7;
        if (dot > 1.0 - eps) {
            double w = from.w + t * (toAdj.w - from.w);
            double x = from.x + t * (toAdj.x - from.x);
            double y = from.y + t * (toAdj.y - from.y);
            double z = from.z + t * (toAdj.z - from.z);
            double mag = std::sqrt(w*w + x*x + y*y + z*z);
            if (mag > eps) {
                return Quat(w/mag, x/mag, y/mag, z/mag);
            }
            return Quat(1.0, 0.0, 0.0, 0.0);
        }
        double theta = std::acos(dot);
        double sinTheta = std::sin(theta);
        double scale0 = std::sin((1.0 - t) * theta) / sinTheta;
        double scale1 = std::sin(t * theta) / sinTheta;
        return Quat(
            scale0*from.w + scale1*toAdj.w,
            scale0*from.x + scale1*toAdj.x,
            scale0*from.y + scale1*toAdj.y,
            scale0*from.z + scale1*toAdj.z
        );
    }
};

bool isNearlyZero(double value) {
    return std::abs(value) < 1e-12;
}

bool isVectorNonZero(const double* values) {
    return !isNearlyZero(values[0]) || !isNearlyZero(values[1]) || !isNearlyZero(values[2]);
}

void setDouble3Outputs(
    MDataBlock& data,
    const MPlug& plug,
    const MObject& compound,
    const MObject& childX,
    const MObject& childY,
    const MObject& childZ,
    double outX,
    double outY,
    double outZ)
{
    MStatus status;
    if (plug == compound || plug.isCompound()) {
        MDataHandle outHandle = data.outputValue(compound, &status);
        outHandle.set(outX, outY, outZ);
        outHandle.setClean();
    }
    if (plug == childX || plug.parent() == compound) {
        MDataHandle hX = data.outputValue(childX, &status);
        hX.set(outX);
        hX.setClean();
    }
    if (plug == childY || plug.parent() == compound) {
        MDataHandle hY = data.outputValue(childY, &status);
        hY.set(outY);
        hY.setClean();
    }
    if (plug == childZ || plug.parent() == compound) {
        MDataHandle hZ = data.outputValue(childZ, &status);
        hZ.set(outZ);
        hZ.setClean();
    }
}

void setAngle3OutputsDegrees(
    MDataBlock& data,
    const MPlug& plug,
    const MObject& compound,
    const MObject& childX,
    const MObject& childY,
    const MObject& childZ,
    double outXDeg,
    double outYDeg,
    double outZDeg)
{
    MStatus status;
    const double outXRad = outXDeg * kPi / 180.0;
    const double outYRad = outYDeg * kPi / 180.0;
    const double outZRad = outZDeg * kPi / 180.0;
    if (plug == childX || plug == compound || plug.isCompound() || plug.parent() == compound) {
        MDataHandle hX = data.outputValue(childX, &status);
        hX.setMAngle(MAngle(outXRad, MAngle::kRadians));
        hX.setClean();
    }
    if (plug == childY || plug == compound || plug.isCompound() || plug.parent() == compound) {
        MDataHandle hY = data.outputValue(childY, &status);
        hY.setMAngle(MAngle(outYRad, MAngle::kRadians));
        hY.setClean();
    }
    if (plug == childZ || plug == compound || plug.isCompound() || plug.parent() == compound) {
        MDataHandle hZ = data.outputValue(childZ, &status);
        hZ.setMAngle(MAngle(outZRad, MAngle::kRadians));
        hZ.setClean();
    }
}


const MTypeId MmdAppendNode::id(0x00128001);

// --- 入力: inputTranslate ---
MObject MmdAppendNode::aInputTranslate;
MObject MmdAppendNode::aInputTranslateX;
MObject MmdAppendNode::aInputTranslateY;
MObject MmdAppendNode::aInputTranslateZ;

// --- 入力: inputRotate ---
MObject MmdAppendNode::aInputRotate;
MObject MmdAppendNode::aInputRotateX;
MObject MmdAppendNode::aInputRotateY;
MObject MmdAppendNode::aInputRotateZ;

// --- 入力: parentTranslate ---
MObject MmdAppendNode::aParentTranslate;
MObject MmdAppendNode::aParentTranslateX;
MObject MmdAppendNode::aParentTranslateY;
MObject MmdAppendNode::aParentTranslateZ;

// --- 入力: parentRotate ---
MObject MmdAppendNode::aParentRotate;
MObject MmdAppendNode::aParentRotateX;
MObject MmdAppendNode::aParentRotateY;
MObject MmdAppendNode::aParentRotateZ;

// --- 入力: grantRate, enableTranslate, enableRotate ---
MObject MmdAppendNode::aGrantRate;
MObject MmdAppendNode::aEnableTranslate;
MObject MmdAppendNode::aEnableRotate;

// --- Python-compatible schema inputs ---
MObject MmdAppendNode::aBaseTranslate;
MObject MmdAppendNode::aBaseTranslateX;
MObject MmdAppendNode::aBaseTranslateY;
MObject MmdAppendNode::aBaseTranslateZ;

MObject MmdAppendNode::aBaseRotate;
MObject MmdAppendNode::aBaseRotateX;
MObject MmdAppendNode::aBaseRotateY;
MObject MmdAppendNode::aBaseRotateZ;

MObject MmdAppendNode::aSourceTranslate;
MObject MmdAppendNode::aSourceTranslateX;
MObject MmdAppendNode::aSourceTranslateY;
MObject MmdAppendNode::aSourceTranslateZ;

MObject MmdAppendNode::aSourceRotate;
MObject MmdAppendNode::aSourceRotateX;
MObject MmdAppendNode::aSourceRotateY;
MObject MmdAppendNode::aSourceRotateZ;

MObject MmdAppendNode::aSourceJointOrient;
MObject MmdAppendNode::aSourceJointOrientX;
MObject MmdAppendNode::aSourceJointOrientY;
MObject MmdAppendNode::aSourceJointOrientZ;

MObject MmdAppendNode::aTargetJointOrient;
MObject MmdAppendNode::aTargetJointOrientX;
MObject MmdAppendNode::aTargetJointOrientY;
MObject MmdAppendNode::aTargetJointOrientZ;

MObject MmdAppendNode::aRatio;
MObject MmdAppendNode::aAffectRotation;
MObject MmdAppendNode::aAffectTranslation;
MObject MmdAppendNode::aLocalAppend;
MObject MmdAppendNode::aSchemaMode;

// --- 出力: outputTranslate ---
MObject MmdAppendNode::aOutputTranslate;
MObject MmdAppendNode::aOutputTranslateX;
MObject MmdAppendNode::aOutputTranslateY;
MObject MmdAppendNode::aOutputTranslateZ;

// --- 出力: outputRotate ---
MObject MmdAppendNode::aOutputRotate;
MObject MmdAppendNode::aOutputRotateX;
MObject MmdAppendNode::aOutputRotateY;
MObject MmdAppendNode::aOutputRotateZ;

MObject MmdAppendNode::aAppendTranslate;
MObject MmdAppendNode::aAppendTranslateX;
MObject MmdAppendNode::aAppendTranslateY;
MObject MmdAppendNode::aAppendTranslateZ;

MObject MmdAppendNode::aAppendRotate;
MObject MmdAppendNode::aAppendRotateX;
MObject MmdAppendNode::aAppendRotateY;
MObject MmdAppendNode::aAppendRotateZ;

MmdAppendNode::MmdAppendNode() = default;
MmdAppendNode::~MmdAppendNode() = default;

void* MmdAppendNode::creator() {
    return new MmdAppendNode();
}

MObject MmdAppendNode::createDouble3Attribute(
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

MObject MmdAppendNode::createAngle3Attribute(
    const MString& longName,
    const MString& shortName,
    MObject& childX,
    MObject& childY,
    MObject& childZ,
    double defaultVal)
{
    MStatus status;
    MFnUnitAttribute uAttr;
    MFnCompoundAttribute cAttr;

    childX = uAttr.create(longName + "X", shortName + "x", MFnUnitAttribute::kAngle, defaultVal, &status);
    uAttr.setStorable(true);
    uAttr.setKeyable(true);
    uAttr.setWritable(true);
    uAttr.setReadable(true);

    childY = uAttr.create(longName + "Y", shortName + "y", MFnUnitAttribute::kAngle, defaultVal, &status);
    uAttr.setStorable(true);
    uAttr.setKeyable(true);
    uAttr.setWritable(true);
    uAttr.setReadable(true);

    childZ = uAttr.create(longName + "Z", shortName + "z", MFnUnitAttribute::kAngle, defaultVal, &status);
    uAttr.setStorable(true);
    uAttr.setKeyable(true);
    uAttr.setWritable(true);
    uAttr.setReadable(true);

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

void MmdAppendNode::markDouble3Output(
    MObject& compound,
    MObject& childX,
    MObject& childY,
    MObject& childZ)
{
    MStatus status;
    MFnCompoundAttribute cAttr(compound, &status);
    cAttr.setWritable(false);
    cAttr.setReadable(true);
    cAttr.setStorable(false);
    cAttr.setKeyable(false);
    MFnNumericAttribute nChild;
    nChild.setObject(childX);
    nChild.setWritable(false);
    nChild.setKeyable(false);
    nChild.setObject(childY);
    nChild.setWritable(false);
    nChild.setKeyable(false);
    nChild.setObject(childZ);
    nChild.setWritable(false);
    nChild.setKeyable(false);
}

void MmdAppendNode::markAngle3Output(
    MObject& compound,
    MObject& childX,
    MObject& childY,
    MObject& childZ)
{
    MStatus status;
    MFnCompoundAttribute cAttr(compound, &status);
    cAttr.setWritable(false);
    cAttr.setReadable(true);
    cAttr.setStorable(false);
    cAttr.setKeyable(false);
    MFnUnitAttribute uChild;
    uChild.setObject(childX);
    uChild.setWritable(false);
    uChild.setKeyable(false);
    uChild.setStorable(false);
    uChild.setObject(childY);
    uChild.setWritable(false);
    uChild.setKeyable(false);
    uChild.setStorable(false);
    uChild.setObject(childZ);
    uChild.setWritable(false);
    uChild.setKeyable(false);
    uChild.setStorable(false);
}

MStatus MmdAppendNode::initialize() {
    MStatus status;
    MFnNumericAttribute nAttr;

    // --- Legacy 入力 (hidden): inputTranslate(double3) ---
    aInputTranslate = createDouble3Attribute(
        "inputTranslate", "it",
        aInputTranslateX, aInputTranslateY, aInputTranslateZ, 0.0);
    addAttribute(aInputTranslate);
    MFnAttribute(aInputTranslate).setHidden(true);

    // --- Legacy 入力 (hidden): inputRotate(double3) ---
    aInputRotate = createDouble3Attribute(
        "inputRotate", "ir",
        aInputRotateX, aInputRotateY, aInputRotateZ, 0.0);
    addAttribute(aInputRotate);
    MFnAttribute(aInputRotate).setHidden(true);

    // --- Legacy 入力 (hidden): parentTranslate(double3) ---
    aParentTranslate = createDouble3Attribute(
        "parentTranslate", "pt",
        aParentTranslateX, aParentTranslateY, aParentTranslateZ, 0.0);
    addAttribute(aParentTranslate);
    MFnAttribute(aParentTranslate).setHidden(true);

    // --- Legacy 入力 (hidden): parentRotate(double3) ---
    aParentRotate = createDouble3Attribute(
        "parentRotate", "pr",
        aParentRotateX, aParentRotateY, aParentRotateZ, 0.0);
    addAttribute(aParentRotate);
    MFnAttribute(aParentRotate).setHidden(true);

    // --- Legacy 入力 (hidden): grantRate(double) ---
    aGrantRate = nAttr.create("grantRate", "gr", MFnNumericData::kDouble, 0.0, &status);
    nAttr.setStorable(true);
    nAttr.setKeyable(true);
    nAttr.setWritable(true);
    nAttr.setReadable(false);
    nAttr.setHidden(true);
    addAttribute(aGrantRate);

    // --- Legacy 入力 (hidden): enableTranslate(bool) ---
    aEnableTranslate = nAttr.create("enableTranslate", "et", MFnNumericData::kBoolean, true, &status);
    nAttr.setStorable(true);
    nAttr.setKeyable(true);
    nAttr.setWritable(true);
    nAttr.setReadable(false);
    nAttr.setHidden(true);
    addAttribute(aEnableTranslate);

    // --- Legacy 入力 (hidden): enableRotate(bool) ---
    aEnableRotate = nAttr.create("enableRotate", "er", MFnNumericData::kBoolean, true, &status);
    nAttr.setStorable(true);
    nAttr.setKeyable(true);
    nAttr.setWritable(true);
    nAttr.setReadable(false);
    nAttr.setHidden(true);
    addAttribute(aEnableRotate);

    aBaseTranslate = createDouble3Attribute(
        "baseTranslate", "bt",
        aBaseTranslateX, aBaseTranslateY, aBaseTranslateZ, 0.0);
    addAttribute(aBaseTranslate);

    aBaseRotate = createAngle3Attribute(
        "baseRotate", "br",
        aBaseRotateX, aBaseRotateY, aBaseRotateZ, 0.0);
    addAttribute(aBaseRotate);

    aSourceTranslate = createDouble3Attribute(
        "sourceTranslate", "st",
        aSourceTranslateX, aSourceTranslateY, aSourceTranslateZ, 0.0);
    addAttribute(aSourceTranslate);

    aSourceRotate = createAngle3Attribute(
        "sourceRotate", "sr",
        aSourceRotateX, aSourceRotateY, aSourceRotateZ, 0.0);
    addAttribute(aSourceRotate);

    aSourceJointOrient = createAngle3Attribute(
        "sourceJointOrient", "sjo",
        aSourceJointOrientX, aSourceJointOrientY, aSourceJointOrientZ, 0.0);
    addAttribute(aSourceJointOrient);

    aTargetJointOrient = createAngle3Attribute(
        "targetJointOrient", "tjo",
        aTargetJointOrientX, aTargetJointOrientY, aTargetJointOrientZ, 0.0);
    addAttribute(aTargetJointOrient);

    aRatio = nAttr.create("ratio", "rat", MFnNumericData::kFloat, 1.0, &status);
    nAttr.setStorable(true);
    nAttr.setKeyable(true);
    nAttr.setWritable(true);
    nAttr.setReadable(false);
    addAttribute(aRatio);

    aAffectRotation = nAttr.create("affectRotation", "afr", MFnNumericData::kBoolean, true, &status);
    nAttr.setStorable(true);
    nAttr.setKeyable(true);
    nAttr.setWritable(true);
    nAttr.setReadable(false);
    addAttribute(aAffectRotation);

    aAffectTranslation = nAttr.create("affectTranslation", "aft", MFnNumericData::kBoolean, false, &status);
    nAttr.setStorable(true);
    nAttr.setKeyable(true);
    nAttr.setWritable(true);
    nAttr.setReadable(false);
    addAttribute(aAffectTranslation);

    aLocalAppend = nAttr.create("localAppend", "lap", MFnNumericData::kBoolean, false, &status);
    nAttr.setStorable(true);
    nAttr.setKeyable(true);
    nAttr.setWritable(true);
    nAttr.setReadable(false);
    addAttribute(aLocalAppend);

    aSchemaMode = nAttr.create("schemaMode", "sm", MFnNumericData::kShort, kSchemaModeAuto, &status);
    nAttr.setStorable(true);
    nAttr.setKeyable(false);
    nAttr.setWritable(true);
    nAttr.setReadable(false);
    nAttr.setMin(kSchemaModeAuto);
    nAttr.setMax(kSchemaModeCompat);
    addAttribute(aSchemaMode);

    // --- 出力: outputTranslate(double3) ---
    aOutputTranslate = createDouble3Attribute(
        "outputTranslate", "ot",
        aOutputTranslateX, aOutputTranslateY, aOutputTranslateZ, 0.0);
    markDouble3Output(aOutputTranslate, aOutputTranslateX, aOutputTranslateY, aOutputTranslateZ);
    addAttribute(aOutputTranslate);

    // --- 出力: outputRotate(double3) ---
    aOutputRotate = createAngle3Attribute(
        "outputRotate", "or",
        aOutputRotateX, aOutputRotateY, aOutputRotateZ, 0.0);
    markAngle3Output(aOutputRotate, aOutputRotateX, aOutputRotateY, aOutputRotateZ);
    addAttribute(aOutputRotate);

    aAppendTranslate = createDouble3Attribute(
        "appendTranslate", "at",
        aAppendTranslateX, aAppendTranslateY, aAppendTranslateZ, 0.0);
    markDouble3Output(aAppendTranslate, aAppendTranslateX, aAppendTranslateY, aAppendTranslateZ);
    addAttribute(aAppendTranslate);

    aAppendRotate = createAngle3Attribute(
        "appendRotate", "ar",
        aAppendRotateX, aAppendRotateY, aAppendRotateZ, 0.0);
    markAngle3Output(aAppendRotate, aAppendRotateX, aAppendRotateY, aAppendRotateZ);
    addAttribute(aAppendRotate);

    // --- attributeAffects ---
    // すべての入力を output に接続
    attributeAffects(aInputTranslateX, aOutputTranslateX);
    attributeAffects(aInputTranslateY, aOutputTranslateY);
    attributeAffects(aInputTranslateZ, aOutputTranslateZ);
    attributeAffects(aInputRotateX, aOutputRotateX);
    attributeAffects(aInputRotateY, aOutputRotateY);
    attributeAffects(aInputRotateZ, aOutputRotateZ);

    attributeAffects(aParentTranslateX, aOutputTranslateX);
    attributeAffects(aParentTranslateY, aOutputTranslateY);
    attributeAffects(aParentTranslateZ, aOutputTranslateZ);
    attributeAffects(aParentRotateX, aOutputRotateX);
    attributeAffects(aParentRotateY, aOutputRotateY);
    attributeAffects(aParentRotateZ, aOutputRotateZ);

    attributeAffects(aGrantRate, aOutputTranslateX);
    attributeAffects(aGrantRate, aOutputTranslateY);
    attributeAffects(aGrantRate, aOutputTranslateZ);
    attributeAffects(aGrantRate, aOutputRotateX);
    attributeAffects(aGrantRate, aOutputRotateY);
    attributeAffects(aGrantRate, aOutputRotateZ);

    attributeAffects(aEnableTranslate, aOutputTranslateX);
    attributeAffects(aEnableTranslate, aOutputTranslateY);
    attributeAffects(aEnableTranslate, aOutputTranslateZ);

    attributeAffects(aEnableRotate, aOutputRotateX);
    attributeAffects(aEnableRotate, aOutputRotateY);
    attributeAffects(aEnableRotate, aOutputRotateZ);

    for (MObject input : {
             aBaseTranslateX, aBaseTranslateY, aBaseTranslateZ,
             aSourceTranslateX, aSourceTranslateY, aSourceTranslateZ,
             aRatio, aAffectTranslation, aLocalAppend, aSchemaMode}) {
        attributeAffects(input, aOutputTranslateX);
        attributeAffects(input, aOutputTranslateY);
        attributeAffects(input, aOutputTranslateZ);
        attributeAffects(input, aAppendTranslateX);
        attributeAffects(input, aAppendTranslateY);
        attributeAffects(input, aAppendTranslateZ);
    }

    for (MObject input : {
             aBaseRotateX, aBaseRotateY, aBaseRotateZ,
             aSourceRotateX, aSourceRotateY, aSourceRotateZ,
             aSourceJointOrientX, aSourceJointOrientY, aSourceJointOrientZ,
             aTargetJointOrientX, aTargetJointOrientY, aTargetJointOrientZ,
             aRatio, aAffectRotation, aLocalAppend, aSchemaMode}) {
        attributeAffects(input, aOutputRotateX);
        attributeAffects(input, aOutputRotateY);
        attributeAffects(input, aOutputRotateZ);
        attributeAffects(input, aAppendRotateX);
        attributeAffects(input, aAppendRotateY);
        attributeAffects(input, aAppendRotateZ);
    }

    return MS::kSuccess;
}

MStatus MmdAppendNode::compute(const MPlug& plug, MDataBlock& data) {
    MStatus status;

    // どの出力プラグか判定
    bool isTranslate = (plug == aOutputTranslate ||
                        plug == aOutputTranslateX ||
                        plug == aOutputTranslateY ||
                        plug == aOutputTranslateZ);
    bool isRotate = (plug == aOutputRotate ||
                     plug == aOutputRotateX ||
                     plug == aOutputRotateY ||
                     plug == aOutputRotateZ);
    bool isAppendTranslate = (plug == aAppendTranslate ||
                              plug == aAppendTranslateX ||
                              plug == aAppendTranslateY ||
                              plug == aAppendTranslateZ);
    bool isAppendRotate = (plug == aAppendRotate ||
                           plug == aAppendRotateX ||
                           plug == aAppendRotateY ||
                           plug == aAppendRotateZ);

    if (!isTranslate && !isRotate && !isAppendTranslate && !isAppendRotate) {
        return MS::kUnknownParameter;
    }

    const double* baseT = data.inputValue(aBaseTranslate).asDouble3();
    const double* sourceT = data.inputValue(aSourceTranslate).asDouble3();
    const double* baseR = data.inputValue(aBaseRotate).asDouble3();
    const double* sourceR = data.inputValue(aSourceRotate).asDouble3();
    const double* sourceJo = data.inputValue(aSourceJointOrient).asDouble3();
    const double* targetJo = data.inputValue(aTargetJointOrient).asDouble3();
    float ratio = data.inputValue(aRatio, &status).asFloat();
    bool affectRot = data.inputValue(aAffectRotation, &status).asBool();
    bool affectTrans = data.inputValue(aAffectTranslation, &status).asBool();
    short schemaMode = data.inputValue(aSchemaMode, &status).asShort();
    bool autoCompatInputsActive = isAppendTranslate || isAppendRotate ||
                                  isVectorNonZero(baseT) || isVectorNonZero(sourceT) ||
                                  isVectorNonZero(baseR) || isVectorNonZero(sourceR) ||
                                  isVectorNonZero(sourceJo) || isVectorNonZero(targetJo) ||
                                  std::abs(ratio - 1.0f) > 1e-6f ||
                                  affectTrans || !affectRot;
    bool useCompatSchema = autoCompatInputsActive;
    if (schemaMode == kSchemaModeLegacy) {
        useCompatSchema = isAppendTranslate || isAppendRotate;
    } else if (schemaMode == kSchemaModeCompat) {
        useCompatSchema = true;
    }

    if (useCompatSchema) {
        const double srcRx = data.inputValue(aSourceRotateX).asAngle().asRadians();
        const double srcRy = data.inputValue(aSourceRotateY).asAngle().asRadians();
        const double srcRz = data.inputValue(aSourceRotateZ).asAngle().asRadians();
        const double srcJoX = data.inputValue(aSourceJointOrientX).asAngle().asRadians();
        const double srcJoY = data.inputValue(aSourceJointOrientY).asAngle().asRadians();
        const double srcJoZ = data.inputValue(aSourceJointOrientZ).asAngle().asRadians();
        const double tgtJoX = data.inputValue(aTargetJointOrientX).asAngle().asRadians();
        const double tgtJoY = data.inputValue(aTargetJointOrientY).asAngle().asRadians();
        const double tgtJoZ = data.inputValue(aTargetJointOrientZ).asAngle().asRadians();
        const double baseRx = data.inputValue(aBaseRotateX).asAngle().asRadians();
        const double baseRy = data.inputValue(aBaseRotateY).asAngle().asRadians();
        const double baseRz = data.inputValue(aBaseRotateZ).asAngle().asRadians();

        MQuaternion sourceQuat = MEulerRotation(srcRx, srcRy, srcRz).asQuaternion();
        MQuaternion sourceJoQuat = MEulerRotation(srcJoX, srcJoY, srcJoZ).asQuaternion();
        MQuaternion sourceMmdQuat = sourceJoQuat.inverse() * sourceQuat * sourceJoQuat;
        sourceMmdQuat.normalizeIt();

        float sourcePosition[3] = {
            static_cast<float>(data.inputValue(aSourceTranslateX).asDouble()),
            static_cast<float>(data.inputValue(aSourceTranslateY).asDouble()),
            static_cast<float>(-data.inputValue(aSourceTranslateZ).asDouble()),
        };
        float sourceRotation[4] = {
            static_cast<float>(sourceMmdQuat.x),
            static_cast<float>(sourceMmdQuat.y),
            static_cast<float>(sourceMmdQuat.z),
            static_cast<float>(sourceMmdQuat.w),
        };
        float outPosition[3] = {0.0f, 0.0f, 0.0f};
        float outRotation[4] = {0.0f, 0.0f, 0.0f, 1.0f};
        mmd_runtime_ffi_append_config_t config{
            ratio,
            affectRot,
            affectTrans,
        };
        mmd_runtime_append_solver_t* solver = mmd_runtime_append_solver_create(&config);
        if (solver == nullptr) {
            return MS::kFailure;
        }
        bool ok = mmd_runtime_append_solver_solve(
            solver,
            sourcePosition,
            sourceRotation,
            outPosition,
            outRotation);
        mmd_runtime_append_solver_free(solver);
        if (!ok) {
            return MS::kFailure;
        }

        const double grantTx = static_cast<double>(outPosition[0]);
        const double grantTy = static_cast<double>(outPosition[1]);
        const double grantTz = -static_cast<double>(outPosition[2]);
        MQuaternion grantQuat(
            static_cast<double>(outRotation[0]),
            static_cast<double>(outRotation[1]),
            static_cast<double>(outRotation[2]),
            static_cast<double>(outRotation[3]));
        MEulerRotation appendEuler = grantQuat.asEulerRotation();
        double appendRx = appendEuler.x * 180.0 / kPi;
        double appendRy = appendEuler.y * 180.0 / kPi;
        double appendRz = appendEuler.z * 180.0 / kPi;

        MQuaternion targetJoQuat = MEulerRotation(tgtJoX, tgtJoY, tgtJoZ).asQuaternion();
        MQuaternion targetGrantQuat = targetJoQuat * grantQuat * targetJoQuat.inverse();
        targetGrantQuat.normalizeIt();
        MQuaternion baseQuat = MEulerRotation(baseRx, baseRy, baseRz).asQuaternion();
        MQuaternion finalQuat = baseQuat * targetGrantQuat;
        finalQuat.normalizeIt();
        MEulerRotation finalEuler = finalQuat.asEulerRotation();
        double outRx = finalEuler.x * 180.0 / kPi;
        double outRy = finalEuler.y * 180.0 / kPi;
        double outRz = finalEuler.z * 180.0 / kPi;

        if (isAppendTranslate) {
            setDouble3Outputs(
                data, plug, aAppendTranslate,
                aAppendTranslateX, aAppendTranslateY, aAppendTranslateZ,
                grantTx, grantTy, grantTz);
            data.setClean(plug);
            return MS::kSuccess;
        }
        if (isAppendRotate) {
            setAngle3OutputsDegrees(
                data, plug, aAppendRotate,
                aAppendRotateX, aAppendRotateY, aAppendRotateZ,
                appendRx, appendRy, appendRz);
            data.setClean(plug);
            return MS::kSuccess;
        }
        if (isTranslate) {
            setDouble3Outputs(
                data, plug, aOutputTranslate,
                aOutputTranslateX, aOutputTranslateY, aOutputTranslateZ,
                data.inputValue(aBaseTranslateX).asDouble() + grantTx,
                data.inputValue(aBaseTranslateY).asDouble() + grantTy,
                data.inputValue(aBaseTranslateZ).asDouble() + grantTz);
            data.setClean(plug);
            return MS::kSuccess;
        }
        if (isRotate) {
            setAngle3OutputsDegrees(
                data, plug, aOutputRotate,
                aOutputRotateX, aOutputRotateY, aOutputRotateZ,
                outRx, outRy, outRz);
            data.setClean(plug);
            return MS::kSuccess;
        }
    }

    // 入力値を取得
    double grantRate = data.inputValue(aGrantRate, &status).asDouble();
    bool enableT = data.inputValue(aEnableTranslate, &status).asBool();
    bool enableR = data.inputValue(aEnableRotate, &status).asBool();

    if (isTranslate) {
        // inputTranslate
        const double* inT = data.inputValue(aInputTranslate).asDouble3();
        // parentTranslate
        const double* parentT = data.inputValue(aParentTranslate).asDouble3();

        double outX, outY, outZ;
        if (enableT) {
            // MMD 付与: outputTranslate = inputTranslate + parentTranslate * grantRate
            outX = inT[0] + parentT[0] * grantRate;
            outY = inT[1] + parentT[1] * grantRate;
            outZ = inT[2] + parentT[2] * grantRate;
        } else {
            // false → input 値をそのまま出力
            outX = inT[0];
            outY = inT[1];
            outZ = inT[2];
        }

        // outputTranslate に書き込み
        MDataHandle outHandle;
        if (plug == aOutputTranslate || plug.isCompound()) {
            outHandle = data.outputValue(aOutputTranslate, &status);
            outHandle.set(outX, outY, outZ);
            outHandle.setClean();
        }
        // 個別要素への要求にも応える
        if (plug == aOutputTranslateX || plug.parent() == aOutputTranslate) {
            MDataHandle hX = data.outputValue(aOutputTranslateX, &status);
            hX.set(outX);
            hX.setClean();
        }
        if (plug == aOutputTranslateY || plug.parent() == aOutputTranslate) {
            MDataHandle hY = data.outputValue(aOutputTranslateY, &status);
            hY.set(outY);
            hY.setClean();
        }
        if (plug == aOutputTranslateZ || plug.parent() == aOutputTranslate) {
            MDataHandle hZ = data.outputValue(aOutputTranslateZ, &status);
            hZ.set(outZ);
            hZ.setClean();
        }

        data.setClean(plug);
        return MS::kSuccess;
    }

    if (isRotate) {
        // inputRotate (deg)
        const double* inR = data.inputValue(aInputRotate).asDouble3();
        // parentRotate (deg)
        const double* parentR = data.inputValue(aParentRotate).asDouble3();

        double outX, outY, outZ;
        if (enableR) {
            // MMD 付与回転:
            //   q_contrib = slerp(identity, parentQuat, grantRate)
            //   q_result  = q_contrib * inputQuat
            Quat qIn  = Quat::fromEulerXYZ(inR[0], inR[1], inR[2]);
            Quat qPar = Quat::fromEulerXYZ(parentR[0], parentR[1], parentR[2]);
            Quat qContrib = Quat::slerp(Quat(), qPar, grantRate);
            Quat qResult  = qContrib * qIn;
            qResult.toEulerXYZ(outX, outY, outZ);
        } else {
            // false → input 値をそのまま出力
            outX = inR[0];
            outY = inR[1];
            outZ = inR[2];
        }

        setAngle3OutputsDegrees(
            data, plug, aOutputRotate,
            aOutputRotateX, aOutputRotateY, aOutputRotateZ,
            outX, outY, outZ);

        data.setClean(plug);
        return MS::kSuccess;
    }

    return MS::kUnknownParameter;
}
