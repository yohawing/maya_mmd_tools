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
#include <maya/MStatus.h>

#include "mmdRuntimeNode.h"

// 将来のノード登録例 (コメントアウト)
// #include "MmdAnimSkinDeformer.h"

MStatus initializePlugin(MObject obj)
{
    MStatus status;
    MFnPlugin plugin(obj, "yohawing", "0.2.0", "Any");

    // mmdRuntimeInstance ノード登録 (Phase 2)
    status = plugin.registerNode(
        "mmdRuntimeInstance",
        MmdRuntimeNode::id,
        MmdRuntimeNode::creator,
        MmdRuntimeNode::initialize);
    CHECK_MSTATUS_AND_RETURN_IT(status);

    MGlobal::displayInfo("maya_mmd_tools_cpp plugin loaded (Phase 2 - mmd-anim runtime).");
    MGlobal::displayInfo("mmdRuntimeInstance node registered. Live MMD evaluation is under development.");

    return MS::kSuccess;
}

MStatus uninitializePlugin(MObject obj)
{
    MStatus status;
    MFnPlugin plugin(obj);

    // 登録ノード解除
    status = plugin.deregisterNode(MmdRuntimeNode::id);
    CHECK_MSTATUS_AND_RETURN_IT(status);

    MGlobal::displayInfo("maya_mmd_tools_cpp plugin unloaded.");
    return MS::kSuccess;
}
