/**
 * mmdRuntimeBridge.cpp
 *
 * mmd-anim-ffi の C++ ラッパー実装。
 * ポインタ管理と主要フローをカプセル化。
 */

#include "mmdRuntimeBridge.h"

#include <cmath>
#include <iostream>
#include <string>

#ifdef _WIN32
#include <windows.h>
#else
#include <dlfcn.h>
#endif

namespace mmd {

namespace {
    // FFI ライブラリの遅延ロード (Windows 例)
    // 本番では CMake でリンク or LoadLibrary + GetProcAddress がより堅牢
    bool g_ffiLoaded = false;
    constexpr const char* kAllowAbiMismatchEnv = "MMD_ANIM_FFI_ALLOW_ABI_MISMATCH";

#ifdef _WIN32
    HMODULE g_ffiModule = nullptr;
    std::string g_ffiPath;
#endif

    using EvaluateHostFrameFn = mmd_runtime_status_t (*)(
        mmd_runtime_instance_t*, mmd_runtime_physics_world_t*,
        const mmd_runtime_ffi_host_pose_view_t*, mmd_runtime_physics_frame_action_t,
        float, float, uint32_t, mmd_runtime_ffi_physics_world_step_report_t*);
    using GetGravityFn = mmd_runtime_status_t (*)(const mmd_runtime_physics_world_t*, float*);
    using SetGravityFn = mmd_runtime_status_t (*)(mmd_runtime_physics_world_t*, const float*);
    using CopyBindingsFn = mmd_runtime_status_t (*)(
        const mmd_runtime_physics_world_t*, mmd_runtime_ffi_physics_rigidbody_binding_t*,
        size_t, size_t*);
    using DrivenBoneMaskFn = mmd_runtime_status_t (*)(
        const mmd_runtime_physics_world_t*, uint8_t*, size_t);

    EvaluateHostFrameFn g_evaluateHostFrame = nullptr;
    GetGravityFn g_getGravity = nullptr;
    SetGravityFn g_setGravity = nullptr;
    CopyBindingsFn g_copyBindings = nullptr;
    DrivenBoneMaskFn g_drivenBoneMask = nullptr;

    template <typename Function>
    Function resolveOptionalSymbol(const char* name) {
#ifdef _WIN32
        return g_ffiModule
            ? reinterpret_cast<Function>(GetProcAddress(g_ffiModule, name))
            : nullptr;
#else
        return reinterpret_cast<Function>(dlsym(RTLD_DEFAULT, name));
#endif
    }

    void resolveHostPhysicsSymbols() {
        g_evaluateHostFrame = resolveOptionalSymbol<EvaluateHostFrameFn>("mmd_runtime_evaluate_host_frame");
        g_getGravity = resolveOptionalSymbol<GetGravityFn>("mmd_runtime_physics_world_get_gravity");
        g_setGravity = resolveOptionalSymbol<SetGravityFn>("mmd_runtime_physics_world_set_gravity");
        g_copyBindings = resolveOptionalSymbol<CopyBindingsFn>("mmd_runtime_physics_world_copy_rigidbody_bindings");
        g_drivenBoneMask = resolveOptionalSymbol<DrivenBoneMaskFn>("mmd_runtime_physics_world_physics_driven_bone_mask");
    }

#ifdef _WIN32
    std::string modulePath(HMODULE module) {
        char buffer[MAX_PATH] = {};
        const DWORD length = GetModuleFileNameA(module, buffer, MAX_PATH);
        if (length == 0 || length >= MAX_PATH) {
            return "";
        }
        return std::string(buffer, length);
    }
#endif
}

RuntimeBridge::RuntimeBridge() {
    loadFfiIfNeeded();
}

RuntimeBridge::~RuntimeBridge() {
    freePhysicsWorld();
    freeInstance();
    freeClip();
    freeModel();
}

bool RuntimeBridge::loadFfiIfNeeded() {
    if (g_ffiLoaded) return true;

#ifdef _WIN32
    // 事前ビルド DLL を探す (native/win64 などからコピー想定)
    // 簡易: カレント or プラグイン隣
    const char* candidates[] = {
        "mmd_runtime_ffi.dll",
        "..\\..\\mmd_tools\\native\\win64\\mmd_runtime_ffi.dll",
        "plug-ins\\mmd_runtime_ffi.dll",
        "mmd_anim_ffi.dll",
        "..\\..\\mmd_tools\\native\\win64\\mmd_anim_ffi.dll",
        "plug-ins\\mmd_anim_ffi.dll"
    };
    for (auto cand : candidates) {
        g_ffiModule = LoadLibraryA(cand);
        if (g_ffiModule) {
            g_ffiPath = modulePath(g_ffiModule);
            break;
        }
    }
    if (!g_ffiModule) {
        std::cerr << "[mmd] Failed to load mmd_runtime_ffi.dll or mmd_anim_ffi.dll\n";
        return false;
    }
#endif

    const uint32_t abi = runtimeAbiVersion();
    if (abi != MMD_RUNTIME_ABI_VERSION) {
        std::cerr << "[mmd] Rejected mmd-anim runtime ABI mismatch: got="
                  << abi << ", expected=" << MMD_RUNTIME_ABI_VERSION;
#ifdef _WIN32
        if (!g_ffiPath.empty()) {
            std::cerr << ", path=" << g_ffiPath;
        }
#endif
        std::cerr << "\n";
#ifdef _WIN32
        FreeLibrary(g_ffiModule);
        g_ffiModule = nullptr;
        g_ffiPath.clear();
#endif
        return false;
    } else {
        std::cerr << "[mmd] Loaded mmd-anim runtime ABI " << abi;
#ifdef _WIN32
        if (!g_ffiPath.empty()) {
            std::cerr << " from " << g_ffiPath;
        }
#endif
        std::cerr << "\n";
    }

    resolveHostPhysicsSymbols();
    g_ffiLoaded = true;
    return true;
}

bool RuntimeBridge::createModelFromPmx(const uint8_t* data, size_t len) {
    freeModel();
    if (!data || len == 0 || !loadFfiIfNeeded()) return false;

    model_ = mmd_runtime_model_create_from_pmx_bytes(data, len);
    return model_ != nullptr;
}

bool RuntimeBridge::createModelFromPmxFile(const std::string& path) {
    freeModel();
    if (path.empty() || !loadFfiIfNeeded()) return false;

    // 簡易ファイル読み込み (本番はメモリマップや大きなファイル対応を)
    FILE* f = fopen(path.c_str(), "rb");
    if (!f) return false;

    fseek(f, 0, SEEK_END);
    long size = ftell(f);
    fseek(f, 0, SEEK_SET);

    if (size <= 0) {
        fclose(f);
        return false;
    }

    std::vector<uint8_t> buf(size);
    size_t read = fread(buf.data(), 1, size, f);
    fclose(f);

    if (read != static_cast<size_t>(size)) return false;

    return createModelFromPmx(buf.data(), buf.size());
}

void RuntimeBridge::freeModel() {
    if (model_) {
        mmd_runtime_model_free(model_);
        model_ = nullptr;
    }
}

bool RuntimeBridge::createClipFromVmd(const uint8_t* data, size_t len) {
    freeClip();
    if (!model_ || !data || len == 0) return false;

    clip_ = mmd_runtime_clip_create_from_vmd_bytes_for_model(model_, data, len);
    return clip_ != nullptr;
}

bool RuntimeBridge::createClipFromVmdFile(const std::string& path) {
    freeClip();
    if (path.empty() || !model_) return false;

    FILE* f = fopen(path.c_str(), "rb");
    if (!f) return false;

    fseek(f, 0, SEEK_END);
    long size = ftell(f);
    fseek(f, 0, SEEK_SET);

    if (size <= 0) {
        fclose(f);
        return false;
    }

    std::vector<uint8_t> buf(size);
    size_t read = fread(buf.data(), 1, size, f);
    fclose(f);

    if (read != static_cast<size_t>(size)) return false;

    return createClipFromVmd(buf.data(), buf.size());
}

void RuntimeBridge::freeClip() {
    if (clip_) {
        mmd_runtime_clip_free(clip_);
        clip_ = nullptr;
    }
}

bool RuntimeBridge::createInstance() {
    freeInstance();
    if (!model_) return false;

    instance_ = mmd_runtime_instance_create_for_model(model_);
    return instance_ != nullptr;
}

void RuntimeBridge::freeInstance() {
    if (instance_) {
        mmd_runtime_instance_free(instance_);
        instance_ = nullptr;
    }
}

bool RuntimeBridge::evaluateFrame(float frame, float ikTolerance, uint32_t maxIters) {
    if (!instance_ || !clip_) return false;

    if (maxIters > 0) {
        return mmd_runtime_instance_evaluate_clip_frame_with_ik_options(
            instance_, clip_, frame, ikTolerance, maxIters);
    }
    return mmd_runtime_instance_evaluate_clip_frame(instance_, clip_, frame);
}

std::vector<float> RuntimeBridge::getWorldMatrices() const {
    if (!instance_) return {};
    size_t n = mmd_runtime_instance_world_matrix_f32_len(instance_);
    if (n == 0) return {};

    std::vector<float> out(n);
    if (!mmd_runtime_instance_copy_world_matrices(instance_, out.data(), n)) {
        return {};
    }
    return out;
}

std::vector<float> RuntimeBridge::getMorphWeights() const {
    if (!instance_) return {};
    size_t n = mmd_runtime_instance_morph_weight_len(instance_);
    if (n == 0) return {};

    std::vector<float> out(n);
    if (!mmd_runtime_instance_copy_morph_weights(instance_, out.data(), n)) {
        return {};
    }
    return out;
}

std::vector<uint8_t> RuntimeBridge::getIkEnabled() const {
    if (!instance_) return {};
    size_t n = mmd_runtime_instance_ik_enabled_len(instance_);
    if (n == 0) return {};

    std::vector<uint8_t> out(n);
    if (!mmd_runtime_instance_copy_ik_enabled(instance_, out.data(), n)) {
        return {};
    }
    return out;
}

size_t RuntimeBridge::boneCount() const {
    if (!model_) return 0;
    return mmd_runtime_model_bone_count(model_);
}

size_t RuntimeBridge::morphCount() const {
    if (!model_) return 0;
    return mmd_runtime_model_morph_count(model_);
}

bool RuntimeBridge::createPhysicsWorldFromPmx(const uint8_t* data, size_t len) {
    freePhysicsWorld();
    if (!data || len == 0 || !loadFfiIfNeeded() || !supportsHostPhysics()) return false;

    mmd_runtime_status_t st = mmd_runtime_physics_world_create_from_pmx_bytes(
        data, len, &physicsWorld_);
    return st == 0 && physicsWorld_ != nullptr;
}

void RuntimeBridge::freePhysicsWorld() {
    if (physicsWorld_) {
        mmd_runtime_physics_world_free(physicsWorld_);
        physicsWorld_ = nullptr;
    }
}

bool RuntimeBridge::resetPhysicsWorld() {
    if (!physicsWorld_ || !instance_) return false;

    size_t seeded = 0;
    mmd_runtime_status_t st = mmd_runtime_physics_world_reset(
        physicsWorld_, instance_, &seeded);
    return st == 0;
}

bool RuntimeBridge::stepPhysicsWorldRuntime(
    float dtSeconds,
    mmd_runtime_ffi_physics_world_step_report_t* outReport)
{
    if (!physicsWorld_ || !instance_) return false;

    mmd_runtime_ffi_physics_world_step_report_t localReport{};
    mmd_runtime_status_t st = mmd_runtime_physics_world_step_runtime(
        physicsWorld_, instance_, dtSeconds,
        outReport ? outReport : &localReport);
    return st == 0;
}

size_t RuntimeBridge::physicsWorldRigidbodyCount() const {
    if (!physicsWorld_) return 0;

    size_t count = 0;
    mmd_runtime_status_t st = mmd_runtime_physics_world_rigidbody_count(
        physicsWorld_, &count);
    return st == 0 ? count : 0;
}

std::vector<float> RuntimeBridge::copyRigidbodyStates() const {
    if (!physicsWorld_) return {};

    size_t rbCount = physicsWorldRigidbodyCount();
    if (rbCount == 0) return {};

    // 7 floats per rigidbody: pos(3) + rot_quat(4)
    const size_t len = rbCount * 7;
    std::vector<float> out(len);
    mmd_runtime_status_t st = mmd_runtime_physics_world_copy_rigidbody_states(
        physicsWorld_, out.data(), len);
    return st == 0 ? out : std::vector<float>{};
}

bool RuntimeBridge::getPhysicsGravity(float outGravity[3]) const {
    if (!physicsWorld_ || !outGravity || !supportsHostPhysics()) return false;
    const auto status = g_getGravity(physicsWorld_, outGravity);
    return status == MMD_RUNTIME_STATUS_OK && std::isfinite(outGravity[0]) &&
           std::isfinite(outGravity[1]) && std::isfinite(outGravity[2]);
}

bool RuntimeBridge::setPhysicsGravity(const float gravity[3]) {
    if (!physicsWorld_ || !gravity || !supportsHostPhysics() ||
        !std::isfinite(gravity[0]) || !std::isfinite(gravity[1]) || !std::isfinite(gravity[2])) {
        return false;
    }
    return g_setGravity(physicsWorld_, gravity) == MMD_RUNTIME_STATUS_OK;
}

std::vector<mmd_runtime_ffi_physics_rigidbody_binding_t> RuntimeBridge::copyRigidbodyBindings() const {
    const size_t count = physicsWorldRigidbodyCount();
    if (!physicsWorld_ || !supportsHostPhysics() || count == 0) return {};
    std::vector<mmd_runtime_ffi_physics_rigidbody_binding_t> bindings(count);
    size_t written = 0;
    const auto status = g_copyBindings(
        physicsWorld_, bindings.data(), bindings.size(), &written);
    if (status != MMD_RUNTIME_STATUS_OK || written != count) return {};
    for (const auto& binding : bindings) {
        if (binding.bone_index < -1) return {};
    }
    return bindings;
}

std::vector<uint8_t> RuntimeBridge::physicsDrivenBoneMask(size_t boneCount) const {
    if (!physicsWorld_ || !supportsHostPhysics() || boneCount == 0) return {};
    std::vector<uint8_t> mask(boneCount, 0);
    const auto status = g_drivenBoneMask(
        physicsWorld_, mask.data(), mask.size());
    if (status != MMD_RUNTIME_STATUS_OK) return {};
    for (const auto value : mask) {
        if (value > 1) return {};
    }
    return mask;
}

bool RuntimeBridge::evaluateHostFrame(
    const mmd_runtime_ffi_host_pose_view_t& pose,
    mmd_runtime_physics_frame_action_t action,
    float dtSeconds,
    float ikTolerance,
    uint32_t maxIters,
    mmd_runtime_ffi_physics_world_step_report_t* outReport)
{
    if (!instance_ || !physicsWorld_ || !supportsHostPhysics() ||
        (action != MMD_RUNTIME_PHYSICS_FRAME_ACTION_SEED &&
         action != MMD_RUNTIME_PHYSICS_FRAME_ACTION_STEP) ||
        !std::isfinite(dtSeconds) || !std::isfinite(ikTolerance) || ikTolerance < 0.0f) {
        return false;
    }
    if (pose.bone_count != boneCount() || pose.morph_count != morphCount() ||
        pose.ik_count != mmd_runtime_instance_ik_enabled_len(instance_)) return false;
    if (pose.bone_count > 0 && (!pose.local_position_offsets_xyz ||
        !pose.local_rotation_xyzw || !pose.local_scales_xyz)) return false;
    if (pose.morph_count > 0 && !pose.morph_weights) return false;
    if (pose.ik_count > 0 && !pose.ik_enabled) return false;
    for (size_t i = 0; i < pose.bone_count * 3; ++i) {
        if (!std::isfinite(pose.local_position_offsets_xyz[i]) ||
            !std::isfinite(pose.local_scales_xyz[i])) return false;
    }
    for (size_t bone = 0; bone < pose.bone_count; ++bone) {
        float normSq = 0.0f;
        for (size_t component = 0; component < 4; ++component) {
            const float value = pose.local_rotation_xyzw[bone * 4 + component];
            if (!std::isfinite(value)) return false;
            normSq += value * value;
        }
        if (std::fabs(normSq - 1.0f) > 2.0e-3f) return false;
    }
    for (size_t i = 0; i < pose.morph_count; ++i) {
        if (!std::isfinite(pose.morph_weights[i])) return false;
    }
    for (size_t i = 0; i < pose.ik_count; ++i) {
        if (pose.ik_enabled[i] > 1) return false;
    }
    mmd_runtime_ffi_physics_world_step_report_t localReport{};
    return g_evaluateHostFrame(
        instance_, physicsWorld_, &pose, action, dtSeconds, ikTolerance, maxIters,
        outReport ? outReport : &localReport) == MMD_RUNTIME_STATUS_OK;
}

bool RuntimeBridge::setPhysicsMode(mmd_runtime_physics_mode_t mode) {
    if (!instance_) return false;
    return mmd_runtime_instance_set_physics_mode(instance_, mode) == 0;
}

bool RuntimeBridge::evaluateRestPose() {
    if (!instance_) return false;
    return mmd_runtime_instance_evaluate_rest_pose(instance_);
}

bool RuntimeBridge::evaluateCurrentPoseBeforePhysics() {
    if (!instance_) return false;
    return mmd_runtime_instance_evaluate_current_pose_before_physics(instance_) == 0;
}

bool RuntimeBridge::applyPhysicsWorldMatrices(const float* matrices, size_t matricesLen,
                                               const uint8_t* mask, size_t maskLen,
                                               size_t* outUpdatedCount) {
    if (!instance_) return false;
    size_t updated = 0;
    mmd_runtime_status_t st = mmd_runtime_instance_apply_physics_world_matrices(
        instance_, matrices, matricesLen, mask, maskLen, &updated);
    if (outUpdatedCount) *outUpdatedCount = updated;
    return st == 0;
}

bool RuntimeBridge::evaluateCurrentPoseAfterPhysics() {
    if (!instance_) return false;
    return mmd_runtime_instance_evaluate_current_pose_after_physics(instance_) == 0;
}

uint32_t RuntimeBridge::runtimeAbiVersion() {
    return mmd_runtime_abi_version();
}

uint32_t RuntimeBridge::runtimeFeatureFlags() {
    return mmd_runtime_feature_flags();
}

bool RuntimeBridge::supportsHostPhysics() {
    constexpr uint32_t required = MMD_RUNTIME_FEATURE_SPLIT_PHYSICS_EVALUATION |
                                  MMD_RUNTIME_FEATURE_PHYSICS_BULLET_NATIVE;
    return isRuntimeAbiCompatible() && (runtimeFeatureFlags() & required) == required &&
           g_evaluateHostFrame && g_getGravity && g_setGravity && g_copyBindings &&
           g_drivenBoneMask;
}

bool RuntimeBridge::isRuntimeAbiCompatible() {
    return runtimeAbiVersion() == MMD_RUNTIME_ABI_VERSION;
}

bool RuntimeBridge::allowRuntimeAbiMismatch() {
    return false;
}

const char* RuntimeBridge::runtimeAbiMismatchEnvName() {
    return kAllowAbiMismatchEnv;
}

} // namespace mmd
