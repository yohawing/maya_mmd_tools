/**
 * MmdPhysicsBoneDriverNode.cpp
 *
 * C++ mmdPhysicsBoneDriver — Python prototype の C++ ポート。
 * 同一 TypeId (0x00128009)、同一属性契約。
 */

#include "MmdPhysicsBoneDriverNode.h"

#include <maya/MFnNumericAttribute.h>
#include <maya/MFnTypedAttribute.h>
#include <maya/MFnUnitAttribute.h>
#include <maya/MFnMatrixAttribute.h>
#include <maya/MFnEnumAttribute.h>
#include <maya/MFnCompoundAttribute.h>
#include <maya/MFnDoubleArrayData.h>
#include <maya/MDataBlock.h>
#include <maya/MDataHandle.h>
#include <maya/MPlug.h>
#include <maya/MAngle.h>
#include <maya/MEulerRotation.h>
#include <maya/MQuaternion.h>
#include <maya/MTransformationMatrix.h>
#include <maya/MVector.h>
#include <maya/MMatrix.h>

#include <cmath>

namespace {
    constexpr double kRotateAxisEpsilon = 1e-8;

    const MEulerRotation::RotationOrder kRotateOrders[6] = {
        MEulerRotation::kXYZ,
        MEulerRotation::kYZX,
        MEulerRotation::kZXY,
        MEulerRotation::kXZY,
        MEulerRotation::kYXZ,
        MEulerRotation::kZYX,
    };
}

const MTypeId MmdPhysicsBoneDriverNode::id(0x00128009);

MObject MmdPhysicsBoneDriverNode::aInSolverBoneMatrices;
MObject MmdPhysicsBoneDriverNode::aInSolverBoneCount;
MObject MmdPhysicsBoneDriverNode::aInBoneIndex;
MObject MmdPhysicsBoneDriverNode::aInParentBoneIndex;
MObject MmdPhysicsBoneDriverNode::aInParentInverseMatrix;
MObject MmdPhysicsBoneDriverNode::aInJointOrient;
MObject MmdPhysicsBoneDriverNode::aInJointOrientX;
MObject MmdPhysicsBoneDriverNode::aInJointOrientY;
MObject MmdPhysicsBoneDriverNode::aInJointOrientZ;
MObject MmdPhysicsBoneDriverNode::aInRotateAxis;
MObject MmdPhysicsBoneDriverNode::aInRotateAxisX;
MObject MmdPhysicsBoneDriverNode::aInRotateAxisY;
MObject MmdPhysicsBoneDriverNode::aInRotateAxisZ;
MObject MmdPhysicsBoneDriverNode::aInRotateOrder;
MObject MmdPhysicsBoneDriverNode::aInSolved;
MObject MmdPhysicsBoneDriverNode::aEnable;
MObject MmdPhysicsBoneDriverNode::aOutTranslate;
MObject MmdPhysicsBoneDriverNode::aOutTranslateX;
MObject MmdPhysicsBoneDriverNode::aOutTranslateY;
MObject MmdPhysicsBoneDriverNode::aOutTranslateZ;
MObject MmdPhysicsBoneDriverNode::aOutRotate;
MObject MmdPhysicsBoneDriverNode::aOutRotateX;
MObject MmdPhysicsBoneDriverNode::aOutRotateY;
MObject MmdPhysicsBoneDriverNode::aOutRotateZ;

MmdPhysicsBoneDriverNode::MmdPhysicsBoneDriverNode() = default;
MmdPhysicsBoneDriverNode::~MmdPhysicsBoneDriverNode() = default;

void* MmdPhysicsBoneDriverNode::creator() {
    return new MmdPhysicsBoneDriverNode();
}

MObject MmdPhysicsBoneDriverNode::createDouble3(
    const char* longName, const char* shortName,
    MObject& childX, MObject& childY, MObject& childZ)
{
    MFnNumericAttribute nAttr;
    MFnCompoundAttribute cAttr;

    std::string lx = std::string(longName) + "X";
    std::string ly = std::string(longName) + "Y";
    std::string lz = std::string(longName) + "Z";
    std::string sx = std::string(shortName) + "x";
    std::string sy = std::string(shortName) + "y";
    std::string sz = std::string(shortName) + "z";

    childX = nAttr.create(lx.c_str(), sx.c_str(), MFnNumericData::kDouble, 0.0);
    childY = nAttr.create(ly.c_str(), sy.c_str(), MFnNumericData::kDouble, 0.0);
    childZ = nAttr.create(lz.c_str(), sz.c_str(), MFnNumericData::kDouble, 0.0);

    MObject compound = cAttr.create(longName, shortName);
    cAttr.addChild(childX);
    cAttr.addChild(childY);
    cAttr.addChild(childZ);
    return compound;
}

MObject MmdPhysicsBoneDriverNode::createAngle3(
    const char* longName, const char* shortName,
    MObject& childX, MObject& childY, MObject& childZ)
{
    MFnUnitAttribute uAttr;
    MFnCompoundAttribute cAttr;

    std::string lx = std::string(longName) + "X";
    std::string ly = std::string(longName) + "Y";
    std::string lz = std::string(longName) + "Z";
    std::string sx = std::string(shortName) + "x";
    std::string sy = std::string(shortName) + "y";
    std::string sz = std::string(shortName) + "z";

    childX = uAttr.create(lx.c_str(), sx.c_str(), MFnUnitAttribute::kAngle, 0.0);
    childY = uAttr.create(ly.c_str(), sy.c_str(), MFnUnitAttribute::kAngle, 0.0);
    childZ = uAttr.create(lz.c_str(), sz.c_str(), MFnUnitAttribute::kAngle, 0.0);

    MObject compound = cAttr.create(longName, shortName);
    cAttr.addChild(childX);
    cAttr.addChild(childY);
    cAttr.addChild(childZ);
    return compound;
}

MStatus MmdPhysicsBoneDriverNode::initialize() {
    MFnNumericAttribute nAttr;
    MFnTypedAttribute   tAttr;
    MFnMatrixAttribute  mAttr;
    MFnEnumAttribute    eAttr;
    MFnCompoundAttribute cAttr;

    // --- Inputs ---

    aInSolverBoneMatrices = tAttr.create(
        "inSolverBoneMatrices", "isbm", MFnData::kDoubleArray);
    tAttr.setStorable(false);
    addAttribute(aInSolverBoneMatrices);

    aInSolverBoneCount = nAttr.create(
        "inSolverBoneCount", "isbc", MFnNumericData::kInt, 0);
    nAttr.setStorable(false);
    addAttribute(aInSolverBoneCount);

    aInBoneIndex = nAttr.create(
        "inBoneIndex", "ibi", MFnNumericData::kInt, -1);
    nAttr.setStorable(true);
    addAttribute(aInBoneIndex);

    aInParentBoneIndex = nAttr.create(
        "inParentBoneIndex", "ipbi", MFnNumericData::kInt, -1);
    nAttr.setStorable(true);
    addAttribute(aInParentBoneIndex);

    aInParentInverseMatrix = mAttr.create("inParentInverseMatrix", "ipim");
    mAttr.setStorable(false);
    addAttribute(aInParentInverseMatrix);

    aInJointOrient = createAngle3(
        "inJointOrient", "ijo", aInJointOrientX, aInJointOrientY, aInJointOrientZ);
    addAttribute(aInJointOrient);

    aInRotateAxis = createAngle3(
        "inRotateAxis", "ira", aInRotateAxisX, aInRotateAxisY, aInRotateAxisZ);
    addAttribute(aInRotateAxis);

    aInRotateOrder = eAttr.create("inRotateOrder", "iro", 0);
    eAttr.addField("xyz", 0);
    eAttr.addField("yzx", 1);
    eAttr.addField("zxy", 2);
    eAttr.addField("xzy", 3);
    eAttr.addField("yxz", 4);
    eAttr.addField("zyx", 5);
    addAttribute(aInRotateOrder);

    aInSolved = nAttr.create("inSolved", "isv", MFnNumericData::kBoolean, false);
    nAttr.setStorable(false);
    addAttribute(aInSolved);

    aEnable = nAttr.create("enable", "en", MFnNumericData::kBoolean, true);
    nAttr.setStorable(true);
    nAttr.setKeyable(true);
    addAttribute(aEnable);

    // --- Outputs ---

    aOutTranslate = createDouble3(
        "outTranslate", "ot", aOutTranslateX, aOutTranslateY, aOutTranslateZ);
    {
        MFnCompoundAttribute outCAttr(aOutTranslate);
        outCAttr.setWritable(false);
        outCAttr.setStorable(false);
        MFnNumericAttribute cx(aOutTranslateX);
        cx.setWritable(false);
        cx.setStorable(false);
        MFnNumericAttribute cy(aOutTranslateY);
        cy.setWritable(false);
        cy.setStorable(false);
        MFnNumericAttribute cz(aOutTranslateZ);
        cz.setWritable(false);
        cz.setStorable(false);
    }
    addAttribute(aOutTranslate);

    aOutRotate = createAngle3(
        "outRotate", "or", aOutRotateX, aOutRotateY, aOutRotateZ);
    {
        MFnCompoundAttribute outCAttr(aOutRotate);
        outCAttr.setWritable(false);
        outCAttr.setStorable(false);
        MFnUnitAttribute cx(aOutRotateX);
        cx.setWritable(false);
        cx.setStorable(false);
        MFnUnitAttribute cy(aOutRotateY);
        cy.setWritable(false);
        cy.setStorable(false);
        MFnUnitAttribute cz(aOutRotateZ);
        cz.setWritable(false);
        cz.setStorable(false);
    }
    addAttribute(aOutRotate);

    // --- Affects ---
    MObject inputs[] = {
        aInSolverBoneMatrices,
        aInSolverBoneCount,
        aInBoneIndex,
        aInParentBoneIndex,
        aInParentInverseMatrix,
        aInJointOrient,
        aInRotateAxis,
        aInRotateOrder,
        aInSolved,
        aEnable,
    };
    MObject outputs[] = { aOutTranslate, aOutRotate };
    for (auto& in : inputs) {
        for (auto& out : outputs) {
            attributeAffects(in, out);
        }
    }

    return MS::kSuccess;
}

bool MmdPhysicsBoneDriverNode::isOutputPlug(const MPlug& plug) const {
    MObject attr = plug.attribute();
    if (attr == aOutTranslate || attr == aOutTranslateX || attr == aOutTranslateY || attr == aOutTranslateZ ||
        attr == aOutRotate || attr == aOutRotateX || attr == aOutRotateY || attr == aOutRotateZ) {
        return true;
    }
    if (plug.isChild()) {
        return isOutputPlug(plug.parent());
    }
    return false;
}

void MmdPhysicsBoneDriverNode::writeIdentity(MDataBlock& data) const {
    data.outputValue(aOutTranslate).set3Double(0.0, 0.0, 0.0);
    data.outputValue(aOutRotate).set3Double(0.0, 0.0, 0.0);
    data.setClean(aOutTranslate);
    data.setClean(aOutRotate);
}

MMatrix MmdPhysicsBoneDriverNode::extractMatrix(const MDoubleArray& arr, int boneIndex) {
    int offset = boneIndex * 16;
    double values[4][4];
    for (int r = 0; r < 4; ++r) {
        for (int c = 0; c < 4; ++c) {
            values[r][c] = arr[offset + r * 4 + c];
        }
    }
    return MMatrix(values);
}

MStatus MmdPhysicsBoneDriverNode::compute(const MPlug& plug, MDataBlock& data) {
    if (!isOutputPlug(plug))
        return MS::kUnknownParameter;

    bool enable = data.inputValue(aEnable).asBool();
    bool solved = data.inputValue(aInSolved).asBool();
    if (!enable || !solved) {
        writeIdentity(data);
        return MS::kSuccess;
    }

    int boneCount = data.inputValue(aInSolverBoneCount).asInt();
    int boneIndex = data.inputValue(aInBoneIndex).asInt();
    int parentBoneIndex = data.inputValue(aInParentBoneIndex).asInt();

    if (boneIndex < 0 || boneIndex >= boneCount) {
        writeIdentity(data);
        return MS::kSuccess;
    }

    MObject matData = data.inputValue(aInSolverBoneMatrices).data();
    if (matData.isNull()) {
        writeIdentity(data);
        return MS::kSuccess;
    }

    MFnDoubleArrayData fnArr(matData);
    MDoubleArray arr = fnArr.array();
    unsigned int expectedLen = static_cast<unsigned int>(boneCount) * 16u;
    if (arr.length() < expectedLen) {
        writeIdentity(data);
        return MS::kSuccess;
    }

    MMatrix boneWorld = extractMatrix(arr, boneIndex);

    MMatrix localMat;
    if (parentBoneIndex >= 0 && parentBoneIndex < boneCount) {
        MMatrix parentWorld = extractMatrix(arr, parentBoneIndex);
        localMat = boneWorld * parentWorld.inverse();
    } else {
        MMatrix parentInvMat = data.inputValue(aInParentInverseMatrix).asMatrix();
        localMat = boneWorld * parentInvMat;
    }

    MTransformationMatrix tfm(localMat);
    MVector translate = tfm.getTranslation(MSpace::kTransform);
    MQuaternion totalQuat = tfm.rotation();

    double joX = data.inputValue(aInJointOrientX).asAngle().asRadians();
    double joY = data.inputValue(aInJointOrientY).asAngle().asRadians();
    double joZ = data.inputValue(aInJointOrientZ).asAngle().asRadians();
    MQuaternion qJo = MEulerRotation(joX, joY, joZ).asQuaternion();

    double raX = data.inputValue(aInRotateAxisX).asAngle().asRadians();
    double raY = data.inputValue(aInRotateAxisY).asAngle().asRadians();
    double raZ = data.inputValue(aInRotateAxisZ).asAngle().asRadians();
    bool hasRa = std::abs(raX) > kRotateAxisEpsilon ||
                 std::abs(raY) > kRotateAxisEpsilon ||
                 std::abs(raZ) > kRotateAxisEpsilon;

    MQuaternion rotateQuat;
    if (hasRa) {
        MQuaternion qRa = MEulerRotation(raX, raY, raZ).asQuaternion();
        rotateQuat = qRa.inverse() * totalQuat * qJo.inverse();
    } else {
        rotateQuat = totalQuat * qJo.inverse();
    }

    short roIndex = data.inputValue(aInRotateOrder).asShort();
    MEulerRotation::RotationOrder ro =
        (roIndex >= 0 && roIndex < 6) ? kRotateOrders[roIndex] : MEulerRotation::kXYZ;
    MEulerRotation rotateEuler = rotateQuat.asEulerRotation();
    rotateEuler.reorderIt(ro);

    MDataHandle outT = data.outputValue(aOutTranslate);
    outT.set3Double(translate.x, translate.y, translate.z);
    MDataHandle outR = data.outputValue(aOutRotate);
    outR.set3Double(rotateEuler.x, rotateEuler.y, rotateEuler.z);

    data.setClean(aOutTranslate);
    data.setClean(aOutRotate);

    return MS::kSuccess;
}
