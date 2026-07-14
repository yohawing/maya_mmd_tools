/**
 * mmdRuntimeBridge.h
 *
 * mmd-anim-ffi (C ABI) を C++ から安全に呼び出すための薄いブリッジ。
 * Phase 2 のライブ評価ノードで使用。
 *
 * 役割:
 * - 生ポインタ (model/clip/instance) のライフサイクル管理のヘルパー
 * - 主要 API の C++ ラッパー (from_pmx_bytes, evaluate, get matrices など)
 * - エラー処理の簡素化
 *
 * 注意: 事前ビルドの mmd_runtime_ffi.dll / mmd_anim_ffi.dll をロード/リンクする必要あり。
 * 現在はヘッダインクルード + 遅延ロード想定。
 */

#pragma once

#include <cstdint>
#include <string>
#include <vector>

// mmd-anim-ffi ヘッダ (CMake でパス設定)
#include "mmd_runtime.h"

namespace mmd {

class RuntimeBridge {
public:
    RuntimeBridge();
    ~RuntimeBridge();

    // モデル作成 (PMX バイトから)
    bool createModelFromPmx(const uint8_t* data, size_t len);

    // 便利: ファイルパスからロード (内部で fread)
    bool createModelFromPmxFile(const std::string& path);

    void freeModel();

    // クリップ作成 (VMD バイト、モデル対応)
    bool createClipFromVmd(const uint8_t* data, size_t len);

    // 便利: ファイルから
    bool createClipFromVmdFile(const std::string& path);

    void freeClip();

    // インスタンス作成
    bool createInstance();
    void freeInstance();

    // 評価
    bool evaluateFrame(float frame, float ikTolerance = 0.001f, uint32_t maxIters = 0);

    // 出力取得
    std::vector<float> getWorldMatrices() const;
    std::vector<float> getMorphWeights() const;
    std::vector<uint8_t> getIkEnabled() const;

    // 状態
    bool isModelValid() const { return model_ != nullptr; }
    bool isClipValid() const { return clip_ != nullptr; }
    bool isInstanceValid() const { return instance_ != nullptr; }
    bool isPhysicsWorldValid() const { return physicsWorld_ != nullptr; }

    size_t boneCount() const;
    size_t morphCount() const;

    // Physics world
    bool createPhysicsWorldFromPmx(const uint8_t* data, size_t len);
    void freePhysicsWorld();
    bool resetPhysicsWorld();
    bool stepPhysicsWorldRuntime(float dtSeconds,
                                mmd_runtime_ffi_physics_world_step_report_t* outReport = nullptr);
    size_t physicsWorldRigidbodyCount() const;
    std::vector<float> copyRigidbodyStates() const;

    // Instance physics helpers
    bool setPhysicsMode(mmd_runtime_physics_mode_t mode);
    bool evaluateCurrentPoseBeforePhysics();
    bool evaluateCurrentPoseAfterPhysics();

    static uint32_t runtimeAbiVersion();
    static bool isRuntimeAbiCompatible();
    static bool allowRuntimeAbiMismatch();
    static const char* runtimeAbiMismatchEnvName();

private:
    mmd_runtime_model_t*   model_ = nullptr;
    mmd_runtime_clip_t*    clip_ = nullptr;
    mmd_runtime_instance_t* instance_ = nullptr;
    mmd_runtime_physics_world_t* physicsWorld_ = nullptr;

    // 内部ユーティリティ
    bool loadFfiIfNeeded();
};

} // namespace mmd
