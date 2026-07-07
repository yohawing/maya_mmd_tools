/**
 * mmdRuntimeBridge.cpp
 *
 * mmd-anim-ffi の C++ ラッパー実装。
 * ポインタ管理と主要フローをカプセル化。
 */

#include "mmdRuntimeBridge.h"

#include <cstdlib>
#include <cstring>
#include <iostream>
#include <string>

#ifdef _WIN32
#include <windows.h>
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

    bool isTruthyEnvValue(const char* value) {
        if (!value) return false;
        return std::strcmp(value, "1") == 0 ||
               std::strcmp(value, "true") == 0 ||
               std::strcmp(value, "TRUE") == 0 ||
               std::strcmp(value, "yes") == 0 ||
               std::strcmp(value, "YES") == 0 ||
               std::strcmp(value, "on") == 0 ||
               std::strcmp(value, "ON") == 0;
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
        if (!allowRuntimeAbiMismatch()) {
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
        }
        std::cerr << "[mmd] Using mmd-anim runtime despite ABI mismatch because "
                  << kAllowAbiMismatchEnv << " is set: got=" << abi
                  << ", expected=" << MMD_RUNTIME_ABI_VERSION << "\n";
    } else {
        std::cerr << "[mmd] Loaded mmd-anim runtime ABI " << abi;
#ifdef _WIN32
        if (!g_ffiPath.empty()) {
            std::cerr << " from " << g_ffiPath;
        }
#endif
        std::cerr << "\n";
    }

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

uint32_t RuntimeBridge::runtimeAbiVersion() {
    return mmd_runtime_abi_version();
}

bool RuntimeBridge::isRuntimeAbiCompatible() {
    return runtimeAbiVersion() == MMD_RUNTIME_ABI_VERSION || allowRuntimeAbiMismatch();
}

bool RuntimeBridge::allowRuntimeAbiMismatch() {
    return isTruthyEnvValue(std::getenv(kAllowAbiMismatchEnv));
}

const char* RuntimeBridge::runtimeAbiMismatchEnvName() {
    return kAllowAbiMismatchEnv;
}

} // namespace mmd
