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

#include <maya/MFnNumericAttribute.h>
#include <maya/MFnCompoundAttribute.h>
#include <maya/MDataHandle.h>
#include <maya/MPlug.h>
#include <maya/MGlobal.h>

#include <cmath>

namespace {
constexpr double kPi = 3.14159265358979323846;
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


const MTypeId MmdAppendNode::id(0x00123457); // 仮 ID (0x00123456 の次)

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

MStatus MmdAppendNode::initialize() {
    MStatus status;
    MFnNumericAttribute nAttr;

    // --- 入力: inputTranslate(double3) ---
    aInputTranslate = createDouble3Attribute(
        "inputTranslate", "it",
        aInputTranslateX, aInputTranslateY, aInputTranslateZ, 0.0);
    addAttribute(aInputTranslate);

    // --- 入力: inputRotate(double3) ---
    aInputRotate = createDouble3Attribute(
        "inputRotate", "ir",
        aInputRotateX, aInputRotateY, aInputRotateZ, 0.0);
    addAttribute(aInputRotate);

    // --- 入力: parentTranslate(double3) ---
    aParentTranslate = createDouble3Attribute(
        "parentTranslate", "pt",
        aParentTranslateX, aParentTranslateY, aParentTranslateZ, 0.0);
    addAttribute(aParentTranslate);

    // --- 入力: parentRotate(double3) ---
    aParentRotate = createDouble3Attribute(
        "parentRotate", "pr",
        aParentRotateX, aParentRotateY, aParentRotateZ, 0.0);
    addAttribute(aParentRotate);

    // --- 入力: grantRate(double) ---
    aGrantRate = nAttr.create("grantRate", "gr", MFnNumericData::kDouble, 0.0, &status);
    nAttr.setStorable(true);
    nAttr.setKeyable(true);
    nAttr.setWritable(true);
    nAttr.setReadable(false);
    addAttribute(aGrantRate);

    // --- 入力: enableTranslate(bool) ---
    aEnableTranslate = nAttr.create("enableTranslate", "et", MFnNumericData::kBoolean, true, &status);
    nAttr.setStorable(true);
    nAttr.setKeyable(true);
    nAttr.setWritable(true);
    nAttr.setReadable(false);
    addAttribute(aEnableTranslate);

    // --- 入力: enableRotate(bool) ---
    aEnableRotate = nAttr.create("enableRotate", "er", MFnNumericData::kBoolean, true, &status);
    nAttr.setStorable(true);
    nAttr.setKeyable(true);
    nAttr.setWritable(true);
    nAttr.setReadable(false);
    addAttribute(aEnableRotate);

    // --- 出力: outputTranslate(double3) ---
    aOutputTranslate = createDouble3Attribute(
        "outputTranslate", "ot",
        aOutputTranslateX, aOutputTranslateY, aOutputTranslateZ, 0.0);
    // 出力属性: writable=false, readable=true
    {
        MFnCompoundAttribute cAttr(aOutputTranslate, &status);
        cAttr.setWritable(false);
        cAttr.setReadable(true);
        cAttr.setStorable(false);
        cAttr.setKeyable(false);
        MFnNumericAttribute nChild;
        nChild.setObject(aOutputTranslateX);
        nChild.setWritable(false);
        nChild.setKeyable(false);
        nChild.setObject(aOutputTranslateY);
        nChild.setWritable(false);
        nChild.setKeyable(false);
        nChild.setObject(aOutputTranslateZ);
        nChild.setWritable(false);
        nChild.setKeyable(false);
    }
    addAttribute(aOutputTranslate);

    // --- 出力: outputRotate(double3) ---
    aOutputRotate = createDouble3Attribute(
        "outputRotate", "or",
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

    if (!isTranslate && !isRotate) {
        return MS::kUnknownParameter;
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

        MDataHandle outHandle;
        if (plug == aOutputRotate || plug.isCompound()) {
            outHandle = data.outputValue(aOutputRotate, &status);
            outHandle.set(outX, outY, outZ);
            outHandle.setClean();
        }
        if (plug == aOutputRotateX || plug.parent() == aOutputRotate) {
            MDataHandle hX = data.outputValue(aOutputRotateX, &status);
            hX.set(outX);
            hX.setClean();
        }
        if (plug == aOutputRotateY || plug.parent() == aOutputRotate) {
            MDataHandle hY = data.outputValue(aOutputRotateY, &status);
            hY.set(outY);
            hY.setClean();
        }
        if (plug == aOutputRotateZ || plug.parent() == aOutputRotate) {
            MDataHandle hZ = data.outputValue(aOutputRotateZ, &status);
            hZ.set(outZ);
            hZ.setClean();
        }

        data.setClean(plug);
        return MS::kSuccess;
    }

    return MS::kUnknownParameter;
}
