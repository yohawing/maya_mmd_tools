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
#include "MmdRenderOverride.h"
#include "MmdRenderShape.h"
#include "MmdAuthoringCommandSupport.h"

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
static bool sCppRegisteredMmdRenderQueueUpdateCommand = false;
static bool sCppRegisteredMmdRenderQueueReindexCommand = false;
static bool sCppRegisteredMmdNativeCasterOverride = false;
static bool sCppRegisteredMmdNativeCasterWitnessCommand = false;
static bool sCppRegisteredMmdAuthoringSetAttrsCommand = false;
static MmdNativeCasterRenderOverride* sMmdNativeCasterOverride = nullptr;

static bool isNodeTypeRegistered(const MTypeId& expectedId)
{
    MNodeClass cls(expectedId);
    return cls.typeName().length() > 0;
}

MStatus initializePlugin(MObject obj)
{
    MStatus status;
    MFnPlugin plugin(obj, "yohawing", "0.6.2", "Any");
    MmdRenderGeometryOverride::setPluginLoadPath(plugin.loadPath());
    MmdNativeCasterRenderOverride::setPluginLoadPath(plugin.loadPath());

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
        bool cleanupSucceeded = true;
        if (sCppRegisteredMmdRenderQueueReindexCommand) {
            cleanupStatus = plugin.deregisterCommand("mmdRenderQueueReindex");
            if (!cleanupStatus) {
                MGlobal::displayWarning(
                    "Failed to roll back mmdRenderQueueReindex command.");
                cleanupSucceeded = false;
            } else {
                sCppRegisteredMmdRenderQueueReindexCommand = false;
            }
        }
        if (sCppRegisteredMmdRenderQueueUpdateCommand) {
            cleanupStatus = plugin.deregisterCommand("mmdRenderQueueUpdate");
            if (!cleanupStatus) {
                MGlobal::displayWarning(
                    "Failed to roll back mmdRenderQueueUpdate command.");
                cleanupSucceeded = false;
            } else {
                sCppRegisteredMmdRenderQueueUpdateCommand = false;
            }
        }
        if (sCppRegisteredMmdRenderWitnessCommand) {
            cleanupStatus = plugin.deregisterCommand("mmdRenderWitness");
            if (!cleanupStatus) {
                MGlobal::displayWarning(
                    "Failed to roll back mmdRenderWitness command.");
                cleanupSucceeded = false;
            } else {
                sCppRegisteredMmdRenderWitnessCommand = false;
            }
        }
        if (sCppRegisteredMmdRenderOverride) {
            cleanupStatus = MHWRender::MDrawRegistry::deregisterGeometryOverrideCreator(
                MmdRenderShape::drawDbClassification,
                MmdRenderShape::drawRegistrantId);
            if (!cleanupStatus) {
                MGlobal::displayWarning(
                    "Failed to roll back mmdRenderShape geometry override.");
                cleanupSucceeded = false;
            } else {
                sCppRegisteredMmdRenderOverride = false;
            }
        }
        if (sCppRegisteredMmdRenderShape) {
            cleanupStatus = plugin.deregisterNode(MmdRenderShape::id);
            if (!cleanupStatus) {
                MGlobal::displayWarning(
                    "Failed to roll back mmdRenderShape node.");
                cleanupSucceeded = false;
            } else {
                sCppRegisteredMmdRenderShape = false;
            }
        }
        return cleanupSucceeded;
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

    status = plugin.registerCommand("mmdRenderQueueUpdate",
                                    MmdRenderQueueUpdateCommand::creator,
                                    MmdRenderQueueUpdateCommand::newSyntax);
    if (!status) {
        cleanupMmdRenderWitness();
        return status;
    }
    sCppRegisteredMmdRenderQueueUpdateCommand = true;

    status = plugin.registerCommand("mmdRenderQueueReindex",
                                    MmdRenderQueueReindexCommand::creator,
                                    MmdRenderQueueReindexCommand::newSyntax);
    if (!status) {
        cleanupMmdRenderWitness();
        return status;
    }
    sCppRegisteredMmdRenderQueueReindexCommand = true;

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

    // Register the opt-in caster after the existing nodes and commands have
    // initialized.  The E2E explicitly clears/rebinds modelEditor panels so
    // Maya instantiates this late-added operation provider.
    MHWRender::MRenderer* renderer = MHWRender::MRenderer::theRenderer(false);
    if (renderer) {
        status = plugin.registerCommand(
            "mmdNativeCasterWitness", MmdNativeCasterWitnessCommand::creator,
            MmdNativeCasterWitnessCommand::newSyntax);
        if (!status) {
            MGlobal::displayWarning(
                "mmdNativeCasterWitness command registration failed; capability skipped.");
        } else {
            sCppRegisteredMmdNativeCasterWitnessCommand = true;
            sMmdNativeCasterOverride = new MmdNativeCasterRenderOverride();
            status = renderer->registerOverride(sMmdNativeCasterOverride);
            if (!status) {
                MGlobal::displayWarning(
                    "mmdNativeCaster override registration failed; capability skipped.");
                delete sMmdNativeCasterOverride;
                sMmdNativeCasterOverride = nullptr;
            } else {
                sCppRegisteredMmdNativeCasterOverride = true;
                MmdNativeCasterRenderOverride::markRegistered(true);
            }
        }
    } else {
        MGlobal::displayWarning(
            "MHWRender::MRenderer unavailable; native caster override skipped.");
    }

    status = plugin.registerCommand("mmdAuthoringSetAttrs",
                                    MmdAuthoringSetAttrsCommand::creator,
                                    MmdAuthoringSetAttrsCommand::newSyntax);
    if (!status) {
        // This is the final registration step.  Roll back every capability
        // installed above so a command-name collision cannot leave a partial
        // plug-in surface in Maya.
        bool cleanupSucceeded = true;
        MStatus cleanupStatus;
        if (sCppRegisteredMmdNativeCasterOverride && renderer) {
            cleanupStatus = renderer->deregisterOverride(sMmdNativeCasterOverride);
            if (!cleanupStatus) {
                MGlobal::displayError(
                    "Failed to roll back mmdNativeCaster override; keeping its pointer and registration state.");
                cleanupSucceeded = false;
            } else {
                sCppRegisteredMmdNativeCasterOverride = false;
                MmdNativeCasterRenderOverride::markRegistered(false);
                delete sMmdNativeCasterOverride;
                sMmdNativeCasterOverride = nullptr;
            }
        }
        if (sCppRegisteredMmdNativeCasterWitnessCommand) {
            cleanupStatus = plugin.deregisterCommand("mmdNativeCasterWitness");
            if (!cleanupStatus) {
                MGlobal::displayWarning(
                    "Failed to roll back mmdNativeCasterWitness command; registration remains tracked.");
                cleanupSucceeded = false;
            } else {
                sCppRegisteredMmdNativeCasterWitnessCommand = false;
            }
        }
        if (sCppRegisteredPhysicsBoneDriver) {
            cleanupStatus = plugin.deregisterNode(MmdPhysicsBoneDriverNode::id);
            if (!cleanupStatus) {
                MGlobal::displayWarning(
                    "Failed to roll back mmdPhysicsBoneDriver node; registration remains tracked.");
                cleanupSucceeded = false;
            } else {
                sCppRegisteredPhysicsBoneDriver = false;
            }
        }
        if (sCppRegisteredCcdIk) {
            cleanupStatus = plugin.deregisterNode(MmdCcdIkNode::id);
            if (!cleanupStatus) {
                MGlobal::displayWarning(
                    "Failed to roll back mmdCcdIk node; registration remains tracked.");
                cleanupSucceeded = false;
            } else {
                sCppRegisteredCcdIk = false;
            }
        }
        if (sCppRegisteredAppend) {
            cleanupStatus = plugin.deregisterNode(MmdAppendNode::id);
            if (!cleanupStatus) {
                MGlobal::displayWarning(
                    "Failed to roll back mmdAppend node; registration remains tracked.");
                cleanupSucceeded = false;
            } else {
                sCppRegisteredAppend = false;
            }
        }
        if (!cleanupMmdRenderWitness()) {
            cleanupSucceeded = false;
        }
        cleanupStatus = plugin.deregisterCommand("mmdWeldUvSeamVertices");
        if (!cleanupStatus) {
            MGlobal::displayWarning("Failed to roll back mmdWeldUvSeamVertices command.");
            cleanupSucceeded = false;
        }
        cleanupStatus = plugin.deregisterCommand("mmdFastLoad");
        if (!cleanupStatus) {
            MGlobal::displayWarning("Failed to roll back mmdFastLoad command.");
            cleanupSucceeded = false;
        }
        cleanupStatus = plugin.deregisterNode(MmdRuntimeNode::id);
        if (!cleanupStatus) {
            MGlobal::displayWarning("Failed to roll back mmdRuntimeInstance node.");
            cleanupSucceeded = false;
        }
        if (!cleanupSucceeded) {
            MGlobal::displayError(
                "mmdAuthoringSetAttrs registration failed and rollback was incomplete; "
                "remaining registrations were kept alive and tracked.");
        }
        return status;
    }
    sCppRegisteredMmdAuthoringSetAttrsCommand = true;

    return MS::kSuccess;
}

MStatus uninitializePlugin(MObject obj)
{
    MStatus status;
    MFnPlugin plugin(obj);

    // Receiver body shaders keep a supported MRenderTargetAssignment to the
    // caster target for their whole lifetime.  Refuse a partial plug-in
    // teardown until every geometry override has released those shaders;
    // deleting the native override first would invalidate a live assignment.
    if (sCppRegisteredMmdNativeCasterOverride &&
        !MmdNativeCasterRenderOverride::shutdownReady()) {
        MGlobal::displayError(
            "Cannot unload mmd_tools_cpp while native receiver shaders are active; "
            "close or replace the scene first.");
        return MS::kFailure;
    }

    if (sCppRegisteredMmdAuthoringSetAttrsCommand) {
        status = plugin.deregisterCommand("mmdAuthoringSetAttrs");
        CHECK_MSTATUS_AND_RETURN_IT(status);
        sCppRegisteredMmdAuthoringSetAttrsCommand = false;
    }

    if (sCppRegisteredMmdRenderWitnessCommand) {
        status = plugin.deregisterCommand("mmdRenderWitness");
        if (!status) {
            MGlobal::displayWarning(
                "Failed to deregister mmdRenderWitness command.");
        }
        sCppRegisteredMmdRenderWitnessCommand = false;
    }

    if (sCppRegisteredMmdRenderQueueUpdateCommand) {
        status = plugin.deregisterCommand("mmdRenderQueueUpdate");
        if (!status) {
            MGlobal::displayWarning(
                "Failed to deregister mmdRenderQueueUpdate command.");
        }
        sCppRegisteredMmdRenderQueueUpdateCommand = false;
    }

    if (sCppRegisteredMmdRenderQueueReindexCommand) {
        status = plugin.deregisterCommand("mmdRenderQueueReindex");
        if (!status) {
            MGlobal::displayWarning(
                "Failed to deregister mmdRenderQueueReindex command.");
        }
        sCppRegisteredMmdRenderQueueReindexCommand = false;
    }

    if (sCppRegisteredMmdNativeCasterWitnessCommand) {
        status = plugin.deregisterCommand("mmdNativeCasterWitness");
        CHECK_MSTATUS_AND_RETURN_IT(status);
        sCppRegisteredMmdNativeCasterWitnessCommand = false;
    }

    if (sCppRegisteredMmdNativeCasterOverride) {
        MHWRender::MRenderer* renderer = MHWRender::MRenderer::theRenderer();
        status = renderer ? renderer->deregisterOverride(sMmdNativeCasterOverride)
                          : MS::kFailure;
        if (!status) {
            MGlobal::displayError(
                "Failed to deregister mmdNativeCaster override; plugin remains loaded.");
            return status;
        }
        sCppRegisteredMmdNativeCasterOverride = false;
        MmdNativeCasterRenderOverride::markRegistered(false);
        delete sMmdNativeCasterOverride;
        sMmdNativeCasterOverride = nullptr;
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
