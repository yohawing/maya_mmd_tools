#include "MmdVmdRotationCommand.h"

#include <maya/MArgDatabase.h>
#include <maya/MDoubleArray.h>
#include <maya/MEulerRotation.h>
#include <maya/MGlobal.h>
#include <maya/MMatrix.h>
#include <maya/MQuaternion.h>
#include <maya/MTransformationMatrix.h>
#include <maya/MVector.h>

#include <algorithm>
#include <cmath>
#include <stdexcept>
#include <vector>
#include "third_party/json.hpp"

namespace {
using Json = nlohmann::json;

std::vector<double> numbers(const Json& value, std::size_t count)
{
    auto result = value.get<std::vector<double>>();
    if (result.size() != count || !std::all_of(result.begin(), result.end(),
        [](double v) { return std::isfinite(v); }))
        throw std::runtime_error("Invalid finite rotation array size.");
    return result;
}

MQuaternion quaternion(const Json& value)
{
    const auto q = numbers(value, 4);
    return MQuaternion(q[0], q[1], q[2], q[3]);
}

MMatrix matrix(const Json& value)
{
    const auto values = numbers(value, 16);
    MMatrix result;
    for (unsigned int row = 0; row < 4; ++row)
        for (unsigned int column = 0; column < 4; ++column)
            result[row][column] = values[row * 4 + column];
    return result;
}

MEulerRotation::RotationOrder order(int value)
{
    if (value < 0 || value > 5) throw std::runtime_error("Invalid rotation order.");
    return static_cast<MEulerRotation::RotationOrder>(value);
}

// Match the canonical Hamilton operations used by mmd_control_rig_basis.
MQuaternion canonical(MQuaternion q)
{
    const double norm = std::sqrt(q.x*q.x + q.y*q.y + q.z*q.z + q.w*q.w);
    if (!std::isfinite(norm) || norm <= 1e-10)
        throw std::runtime_error("Degenerate control quaternion.");
    q.x /= norm; q.y /= norm; q.z /= norm; q.w /= norm;
    double sign = q.w < -1e-10 ? -1.0 : 1.0;
    if (std::abs(q.w) <= 1e-10) {
        for (double v : {q.x, q.y, q.z}) {
            if (std::abs(v) > 1e-10) { sign = v < 0 ? -1.0 : 1.0; break; }
        }
    }
    q.x *= sign; q.y *= sign; q.z *= sign; q.w *= sign;
    if (std::abs(q.w) <= 1e-10) q.w = 0;
    return q;
}

MQuaternion multiply(MQuaternion a, MQuaternion b)
{
    a = canonical(a); b = canonical(b);
    return canonical(MQuaternion(a.w*b.x + a.x*b.w + a.y*b.z - a.z*b.y,
                       a.w*b.y - a.x*b.z + a.y*b.w + a.z*b.x,
                       a.w*b.z + a.x*b.y - a.y*b.x + a.z*b.w,
                       a.w*b.w - a.x*b.x - a.y*b.y - a.z*b.z));
}
}

void* MmdVmdRotationCommand::creator() { return new MmdVmdRotationCommand(); }

MSyntax MmdVmdRotationCommand::newSyntax()
{
    MSyntax syntax;
    syntax.addFlag("-p", "-payload", MSyntax::kString);
    return syntax;
}

MStatus MmdVmdRotationCommand::doIt(const MArgList& args)
{
    MStatus status;
    MArgDatabase arguments(newSyntax(), args, &status);
    if (!status) return status;
    MString payload;
    status = arguments.getFlagArgument("-payload", 0, payload);
    if (!status) return status;
    try {
        const auto request = Json::parse(payload.asUTF8());
        const auto rotations = request.at("quaternions").get<std::vector<double>>();
        if (rotations.size() % 4 || !std::all_of(rotations.begin(), rotations.end(),
            [](double v) { return std::isfinite(v); }))
            throw std::runtime_error("Invalid quaternion array.");
        const auto jointOrder = order(request.at("rotate_order").get<int>());
        const auto orient = quaternion(request.at("joint_orient"));
        const auto orientInverse = orient.inverse();
        const bool hasBind = request.contains("bind");
        MVector translation;
        MMatrix correction, parent, inverseParent;
        if (hasBind) {
            const auto& bind = request.at("bind");
            const auto t = numbers(bind.at("translation"), 3);
            translation = MVector(t[0], t[1], t[2]);
            correction = matrix(bind.at("correction"));
            parent = matrix(bind.at("parent"));
            inverseParent = matrix(bind.at("inverse_parent"));
        }
        const bool hasBasis = request.contains("basis");
        MQuaternion basis;
        auto controlOrder = jointOrder;
        if (hasBasis) {
            basis = quaternion(request.at("basis"));
            const double norm = basis.x*basis.x + basis.y*basis.y + basis.z*basis.z + basis.w*basis.w;
            if (norm < 1e-20) throw std::runtime_error("Degenerate control basis.");
            basis = canonical(basis);
            controlOrder = order(request.at("control_order").get<int>());
        }
        MDoubleArray result;
        constexpr double degrees = 57.2957795130823208768;
        for (std::size_t i = 0; i < rotations.size(); i += 4) {
            const MQuaternion maya(-rotations[i], -rotations[i+1], rotations[i+2], rotations[i+3]);
            MQuaternion rotation;
            if (hasBind) {
                MTransformationMatrix local;
                local.setTranslation(translation, MSpace::kTransform);
                local.setRotationQuaternion(maya.x, maya.y, maya.z, maya.w);
                const MMatrix total = correction * local.asMatrix() * parent * inverseParent;
                rotation = MTransformationMatrix(total).rotation() * orientInverse;
            } else {
                rotation = orient * maya * orientInverse;
            }
            auto euler = rotation.asEulerRotation();
            euler.reorderIt(jointOrder);
            if (hasBasis) {
                // Preserve the reference Euler round-trip, including rotate order.
                auto bone = euler.asQuaternion();
                bone.normalizeIt();
                rotation = multiply(multiply(basis.inverse(), bone), basis);
                euler = rotation.asEulerRotation();
                euler.reorderIt(controlOrder);
            }
            if (!std::isfinite(euler.x) || !std::isfinite(euler.y) || !std::isfinite(euler.z))
                throw std::runtime_error("Non-finite converted rotation.");
            result.append(euler.x * degrees);
            result.append(euler.y * degrees);
            result.append(euler.z * degrees);
        }
        setResult(result);
        return MS::kSuccess;
    } catch (const std::exception& error) {
        MGlobal::displayError(MString("VMD rotation batch failed: ") + error.what());
        return MS::kFailure;
    }
}
