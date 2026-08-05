/**
 * @file pluginMain.cpp
 *
 * maya_mmd_tools C++ プラグインのエントリポイント (スケルトン)。
 *
 * Phase 0 時点では最小限の initialize / uninitialize のみを提供します。
 * 将来的にはここで MMD ランタイム関連のカスタムノード (例: MmdRuntimePoseDriver,
 * MmdAnimDeformer など) を登録します。
 *
 * mmd-anim-ffi との連携もこのレイヤまたは別ブリッジで実装予定。
 *
 * ビルド方法については同ディレクトリの CMakeLists.txt を参照してください。
 */

#include <maya/MFnPlugin.h>
#include <maya/MGlobal.h>
#include <maya/MNodeClass.h>
#include <maya/MStatus.h>
#include <maya/MDrawRegistry.h>

#include <string>

#include "mmdRuntimeBridge.h"
#include "mmdRuntimeNode.h"
#include "mmdFastLoad.h"
#include "MmdWeldUvSeamVertices.h"
#include "MmdAppendNode.h"
#include "MmdCcdIkNode.h"
#include "MmdPhysicsBoneDriverNode.h"
#include "MmdRenderGeometryOverride.h"
#include "MmdRenderShape.h"

// 将来のノード登録例 (コメントアウト)
// #include "MmdAnimSkinDeformer.h"

// Track whether C++ actually registered mmdAppend / mmdCcdIk (may be
// skipped when the Python plugin already registered them with the same typeId).
static bool sCppRegisteredAppend = false;
static bool sCppRegisteredCcdIk = false;
static bool sCppRegisteredPhysicsBoneDriver = false;
static bool sCppRegisteredMmdRenderShape = false;
static bool sCppRegisteredMmdRenderOverride = false;
static bool sCppRegisteredMmdRenderWitnessCommand = false;

static bool isNodeTypeRegistered(const MTypeId& expectedId)
{
    MNodeClass cls(expectedId);
    return cls.typeName().length() > 0;
}

MStatus initializePlugin(MObject obj)
{
    MStatus status;
    MFnPlugin plugin(obj, "yohawing", "0.6.2", "Any");

    const uint32_t runtimeAbi = mmd::RuntimeBridge::runtimeAbiVersion();
    if (runtimeAbi != MMD_RUNTIME_ABI_VERSION) {
        const std::string message =
            "mmd-anim runtime ABI mismatch: got=" + std::to_string(runtimeAbi) +
            ", expected=" + std::to_string(MMD_RUNTIME_ABI_VERSION);
        if (!mmd::RuntimeBridge::allowRuntimeAbiMismatch()) {
            MGlobal::displayError((message + ". Refusing to initialize maya_mmd_tools_cpp.").c_str());
            return MS::kFailure;
        }
        MGlobal::displayWarning(
            (message + "; continuing because " + mmd::RuntimeBridge::runtimeAbiMismatchEnvName() + " is set.").c_str());
    } else {
        MGlobal::displayInfo(
            ("mmd-anim runtime ABI verified: " + std::to_string(runtimeAbi)).c_str());
    }

    // mmdRuntimeInstance ノード登録 (Phase 2)
    status = plugin.registerNode(
        "mmdRuntimeInstance",
        MmdRuntimeNode::id,
        MmdRuntimeNode::creator,
        MmdRuntimeNode::initialize);
    CHECK_MSTATUS_AND_RETURN_IT(status);

    MGlobal::displayInfo("maya_mmd_tools_cpp plugin loaded (Phase 2 - mmd-anim runtime).");
    MGlobal::displayInfo("mmdRuntimeInstance node registered. Live MMD evaluation is under development.");

    // mmdFastLoad command (Phase 3 - fast PMX mesh loading)
    status = plugin.registerCommand("mmdFastLoad",
                                    MmdFastLoad::creator,
                                    MmdFastLoad::newSyntax);
    CHECK_MSTATUS_AND_RETURN_IT(status);

    // Native topology normalization used by the Python PMX mesh importer.
    status = plugin.registerCommand("mmdWeldUvSeamVertices",
                                    MmdWeldUvSeamVertices::creator,
                                    MmdWeldUvSeamVertices::newSyntax);
    CHECK_MSTATUS_AND_RETURN_IT(status);

    auto cleanupMmdRenderWitness = [&plugin]() {
        MStatus cleanupStatus;
        if (sCppRegisteredMmdRenderWitnessCommand) {
            cleanupStatus = plugin.deregisterCommand("mmdRenderWitness");
            if (!cleanupStatus) {
                MGlobal::displayWarning(
                    "Failed to roll back mmdRenderWitness command.");
            }
            sCppRegisteredMmdRenderWitnessCommand = false;
        }
        if (sCppRegisteredMmdRenderOverride) {
            cleanupStatus = MHWRender::MDrawRegistry::deregisterGeometryOverrideCreator(
                MmdRenderShape::drawDbClassification,
                MmdRenderShape::drawRegistrantId);
            if (!cleanupStatus) {
                MGlobal::displayWarning(
                    "Failed to roll back mmdRenderShape geometry override.");
            }
            sCppRegisteredMmdRenderOverride = false;
        }
        if (sCppRegisteredMmdRenderShape) {
            cleanupStatus = plugin.deregisterNode(MmdRenderShape::id);
            if (!cleanupStatus) {
                MGlobal::displayWarning(
                    "Failed to roll back mmdRenderShape node.");
            }
            sCppRegisteredMmdRenderShape = false;
        }
    };

    // Opt-in VP2 ownership witness.  This is a custom surface shape
    // classification; it never registers against Maya's built-in
    // drawdb/geometry/mesh path used by ordinary MFnMesh imports.
    status = plugin.registerShape(
        "mmdRenderShape",
        MmdRenderShape::id,
        MmdRenderShape::creator,
        MmdRenderShape::initialize,
        &MmdRenderShape::drawDbClassification);
    CHECK_MSTATUS_AND_RETURN_IT(status);
    sCppRegisteredMmdRenderShape = true;

    status = MHWRender::MDrawRegistry::registerGeometryOverrideCreator(
        MmdRenderShape::drawDbClassification,
        MmdRenderShape::drawRegistrantId,
        MmdRenderGeometryOverride::creator);
    if (!status) {
        cleanupMmdRenderWitness();
        return status;
    }
    sCppRegisteredMmdRenderOverride = true;

    status = plugin.registerCommand("mmdRenderWitness",
                                    MmdRenderWitnessCommand::creator,
                                    MmdRenderWitnessCommand::newSyntax);
    if (!status) {
        cleanupMmdRenderWitness();
        return status;
    }
    sCppRegisteredMmdRenderWitnessCommand = true;

    // mmdAppend 登録 (Python 版と統一した typeName)
    // Python 版が同じ typeId で登録済みの場合はスキップ
    if (isNodeTypeRegistered(MmdAppendNode::id)) {
        MGlobal::displayInfo("mmdAppend already registered by Python plugin; skipping C++ registration.");
        sCppRegisteredAppend = false;
    } else {
        status = plugin.registerNode(
            "mmdAppend",
            MmdAppendNode::id,
            MmdAppendNode::creator,
            MmdAppendNode::initialize);
        if (!status) {
            cleanupMmdRenderWitness();
            return status;
        }
        sCppRegisteredAppend = true;
        MGlobal::displayInfo("mmdAppend node registered.");
    }

    // mmdCcdIk 登録 (Python 版と統一した typeName)
    // Python 版が同じ typeId で登録済みの場合はスキップ
    if (isNodeTypeRegistered(MmdCcdIkNode::id)) {
        MGlobal::displayInfo("mmdCcdIk already registered by Python plugin; skipping C++ registration.");
        sCppRegisteredCcdIk = false;
    } else {
        status = plugin.registerNode(
            "mmdCcdIk",
            MmdCcdIkNode::id,
            MmdCcdIkNode::creator,
            MmdCcdIkNode::initialize);
        if (!status) {
            cleanupMmdRenderWitness();
            return status;
        }
        sCppRegisteredCcdIk = true;
        MGlobal::displayInfo("mmdCcdIk node registered.");
    }

    // mmdPhysicsBoneDriver 登録
    if (isNodeTypeRegistered(MmdPhysicsBoneDriverNode::id)) {
        MGlobal::displayInfo("mmdPhysicsBoneDriver already registered by Python plugin; skipping C++ registration.");
        sCppRegisteredPhysicsBoneDriver = false;
    } else {
        status = plugin.registerNode(
            "mmdPhysicsBoneDriver",
            MmdPhysicsBoneDriverNode::id,
            MmdPhysicsBoneDriverNode::creator,
            MmdPhysicsBoneDriverNode::initialize);
        if (!status) {
            cleanupMmdRenderWitness();
            return status;
        }
        sCppRegisteredPhysicsBoneDriver = true;
        MGlobal::displayInfo("mmdPhysicsBoneDriver node registered (C++).");
    }

    return MS::kSuccess;
}

MStatus uninitializePlugin(MObject obj)
{
    MStatus status;
    MFnPlugin plugin(obj);

    if (sCppRegisteredMmdRenderWitnessCommand) {
        status = plugin.deregisterCommand("mmdRenderWitness");
        if (!status) {
            MGlobal::displayWarning(
                "Failed to deregister mmdRenderWitness command.");
        }
        sCppRegisteredMmdRenderWitnessCommand = false;
    }

    if (sCppRegisteredMmdRenderOverride) {
        status = MHWRender::MDrawRegistry::deregisterGeometryOverrideCreator(
            MmdRenderShape::drawDbClassification,
            MmdRenderShape::drawRegistrantId);
        if (!status) {
            MGlobal::displayWarning(
                "Failed to deregister mmdRenderShape geometry override.");
        }
        sCppRegisteredMmdRenderOverride = false;
    }

    if (sCppRegisteredMmdRenderShape) {
        status = plugin.deregisterNode(MmdRenderShape::id);
        if (!status) {
            MGlobal::displayWarning("Failed to deregister mmdRenderShape node.");
        }
        sCppRegisteredMmdRenderShape = false;
    }

    // 登録ノード解除
    status = plugin.deregisterNode(MmdRuntimeNode::id);
    CHECK_MSTATUS_AND_RETURN_IT(status);

    status = plugin.deregisterCommand("mmdFastLoad");
    CHECK_MSTATUS_AND_RETURN_IT(status);

    status = plugin.deregisterCommand("mmdWeldUvSeamVertices");
    CHECK_MSTATUS_AND_RETURN_IT(status);

    // C++ が登録したノードのみ deregister
    if (sCppRegisteredAppend) {
        status = plugin.deregisterNode(MmdAppendNode::id);
        CHECK_MSTATUS_AND_RETURN_IT(status);
        sCppRegisteredAppend = false;
    }

    if (sCppRegisteredCcdIk) {
        status = plugin.deregisterNode(MmdCcdIkNode::id);
        CHECK_MSTATUS_AND_RETURN_IT(status);
        sCppRegisteredCcdIk = false;
    }

    if (sCppRegisteredPhysicsBoneDriver) {
        status = plugin.deregisterNode(MmdPhysicsBoneDriverNode::id);
        CHECK_MSTATUS_AND_RETURN_IT(status);
        sCppRegisteredPhysicsBoneDriver = false;
    }

    MGlobal::displayInfo("maya_mmd_tools_cpp plugin unloaded.");
    return MS::kSuccess;
}
