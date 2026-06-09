# CMakeLists.txt for maya_mmd_tools C++ plugin (Maya native extension)
#
# mmd-anim runtime を利用したライブ評価ノード (Phase 2 以降)。
# 
# ビルド例 (Windows, Maya 2024):
#   cd build
#   cmake .. -G "Visual Studio 17 2022" -DMAYA_VERSION=2024 -DMAYA_DEVKIT_ROOT="C:/Program Files/Autodesk/Maya2024/devkit"
#   cmake --build . --config Release
#
# 成果物: plug-ins/2024/Release/mmd_tools_cpp.mll (など)
#
# mmd-anim-ffi (C ABI) をリンク/遅延ロードして使用。
# 事前ビルド DLL は mmd_tools/native/win64/ などに配置想定。

cmake_minimum_required(VERSION 3.20)

project(mmd_tools_cpp LANGUAGES CXX)

# ------------------------------------------------------------------
# オプション
# ------------------------------------------------------------------
set(MAYA_VERSION "2024" CACHE STRING "Target Maya version (2024/2025/2026)")
set(MAYA_DEVKIT_ROOT "" CACHE PATH "Maya devkit root (include/lib)")

# mmd-anim-ffi ヘッダのヒント (submodule から)
# CMAKE_SOURCE_DIR は cpp/src から呼ばれる場合 cpp/src になるので、リポジトリルートを明示
set(REPO_ROOT "${CMAKE_SOURCE_DIR}/../.." CACHE PATH "Repository root")
set(MMD_ANIM_FFI_INCLUDE_DIR "${REPO_ROOT}/external/mmd-anim/crates/mmd-anim-ffi/include" CACHE PATH "Path to mmd_runtime.h")

# ------------------------------------------------------------------
# Maya devkit 検出
# ------------------------------------------------------------------
if(NOT MAYA_DEVKIT_ROOT)
    if(WIN32)
        set(_cand "C:/Program Files/Autodesk/Maya${MAYA_VERSION}/devkit")
        if(EXISTS ${_cand})
            set(MAYA_DEVKIT_ROOT ${_cand})
        endif()
    endif()
endif()

if(NOT MAYA_DEVKIT_ROOT OR NOT EXISTS "${MAYA_DEVKIT_ROOT}")
    message(WARNING "MAYA_DEVKIT_ROOT not found or not set. Maya plugin build will likely fail.")
endif()

set(MAYA_INCLUDE_DIR "${MAYA_DEVKIT_ROOT}/include")
set(MAYA_LIB_DIR "${MAYA_DEVKIT_ROOT}/lib")

# ------------------------------------------------------------------
# ソース
# ------------------------------------------------------------------
set(SOURCES
    pluginMain.cpp
    mmdRuntimeBridge.cpp
    mmdRuntimeNode.cpp
)

set(HEADERS
    mmdRuntimeBridge.h
    mmdRuntimeNode.h
    mmd_runtime.h   # 可能ならコピー or include
)

add_library(mmd_tools_cpp SHARED ${SOURCES} ${HEADERS})

target_include_directories(mmd_tools_cpp PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}
    ${MAYA_INCLUDE_DIR}
)

if(EXISTS "${MMD_ANIM_FFI_INCLUDE_DIR}/mmd_runtime.h")
    target_include_directories(mmd_tools_cpp PRIVATE ${MMD_ANIM_FFI_INCLUDE_DIR})
    message(STATUS "Found mmd-anim-ffi header at ${MMD_ANIM_FFI_INCLUDE_DIR}")
else()
    message(WARNING "mmd_runtime.h not found at ${MMD_ANIM_FFI_INCLUDE_DIR}. FFI bridge will be stub.")
endif()

# ------------------------------------------------------------------
# コンパイル定義 & リンク (Maya plugin 標準)
# ------------------------------------------------------------------
target_compile_definitions(mmd_tools_cpp PRIVATE
    REQUIRE_IOSTREAM
    _BOOL
)

if(WIN32)
    target_compile_definitions(mmd_tools_cpp PRIVATE NT_PLUGIN)
    target_link_directories(mmd_tools_cpp PRIVATE ${MAYA_LIB_DIR})
    target_link_libraries(mmd_tools_cpp PRIVATE
        OpenMaya.lib
        Foundation.lib
        # mmd_anim_ffi.lib が必要ならここに (遅延ロード推奨)
    )
    set_target_properties(mmd_tools_cpp PROPERTIES
        SUFFIX ".mll"
        RUNTIME_OUTPUT_DIRECTORY "${CMAKE_SOURCE_DIR}/plug-ins/${MAYA_VERSION}/$<CONFIG>"
    )
endif()

if(APPLE)
    target_compile_definitions(mmd_tools_cpp PRIVATE MAC_PLUGIN)
    # .bundle 設定など追加必要
endif()

# ------------------------------------------------------------------
# インストール
# ------------------------------------------------------------------
install(TARGETS mmd_tools_cpp
    RUNTIME DESTINATION "plug-ins/${MAYA_VERSION}"
)

message(STATUS "maya_mmd_tools C++ configured for Maya ${MAYA_VERSION}")
message(STATUS "  Devkit: ${MAYA_DEVKIT_ROOT}")
