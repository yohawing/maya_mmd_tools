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
set(MAYA_VERSION "2024" CACHE STRING "Target Maya version (2024/2025/2026/2027)")
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
    elseif(APPLE)
        set(_cand "/Applications/Autodesk/maya${MAYA_VERSION}/Maya.app/Contents")
        if(EXISTS "${_cand}/include/maya/MFn.h")
            set(MAYA_DEVKIT_ROOT ${_cand})
        endif()
        if(NOT MAYA_DEVKIT_ROOT)
            set(_cand "$ENV{HOME}/devkitBase")
            if(EXISTS "${_cand}/include/maya/MFn.h")
                set(MAYA_DEVKIT_ROOT ${_cand})
            endif()
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
    mmdFastLoad.cpp
    MmdRenderQueue.cpp
    MmdRenderShape.cpp
    MmdRenderGeometryOverride.cpp
    MmdWeldUvSeamVertices.cpp
    MmdAppendNode.cpp
    MmdCcdIkNode.cpp
    MmdPhysicsBoneDriverNode.cpp
)

set(HEADERS
    mmdRuntimeBridge.h
    mmdRuntimeNode.h
    mmdFastLoad.h
    MmdRenderQueue.h
    MmdRenderShape.h
    MmdRenderGeometryOverride.h
    MmdWeldUvSeamVertices.h
    MmdAppendNode.h
    MmdCcdIkNode.h
    MmdCoordinateUtils.h
    MmdPhysicsBoneDriverNode.h
)

add_library(mmd_tools_cpp SHARED ${SOURCES} ${HEADERS})

# nlohmann/json (vendored, header-only) requires C++17.
target_compile_features(mmd_tools_cpp PRIVATE cxx_std_17)

target_include_directories(mmd_tools_cpp PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}
    ${MAYA_INCLUDE_DIR}
)

if(EXISTS "${MMD_ANIM_FFI_INCLUDE_DIR}/mmd_runtime.h")
    target_include_directories(mmd_tools_cpp PRIVATE ${MMD_ANIM_FFI_INCLUDE_DIR})
    target_sources(mmd_tools_cpp PRIVATE "${MMD_ANIM_FFI_INCLUDE_DIR}/mmd_runtime.h")
    message(STATUS "Found mmd-anim-ffi header at ${MMD_ANIM_FFI_INCLUDE_DIR}")
else()
    message(WARNING "mmd_runtime.h not found at ${MMD_ANIM_FFI_INCLUDE_DIR}. FFI bridge will be stub.")
endif()

set(MMD_ANIM_FFI_LIB_DIR "${REPO_ROOT}/external/mmd-anim/target/release" CACHE PATH "Path to mmd-anim-ffi import library")
if(WIN32)
    find_library(MMD_ANIM_FFI_LIB
        NAMES mmd_runtime_ffi mmd_runtime_ffi.dll mmd_anim_ffi mmd_anim_ffi.dll
        PATHS "${MMD_ANIM_FFI_LIB_DIR}"
        NO_DEFAULT_PATH
    )
elseif(APPLE)
    find_library(MMD_ANIM_FFI_LIB
        NAMES mmd_runtime_ffi libmmd_runtime_ffi mmd_anim_ffi libmmd_anim_ffi
        PATHS "${MMD_ANIM_FFI_LIB_DIR}"
        NO_DEFAULT_PATH
    )
endif()

set(MMD_ANIM_FFI_RUNTIME_LIB "")
if(WIN32)
    foreach(_mmd_ffi_dll IN ITEMS "mmd_runtime_ffi.dll" "mmd_anim_ffi.dll")
        if(EXISTS "${MMD_ANIM_FFI_LIB_DIR}/${_mmd_ffi_dll}" AND NOT MMD_ANIM_FFI_RUNTIME_LIB)
            set(MMD_ANIM_FFI_RUNTIME_LIB "${MMD_ANIM_FFI_LIB_DIR}/${_mmd_ffi_dll}")
        endif()
    endforeach()
elseif(APPLE)
    foreach(_mmd_ffi_dylib IN ITEMS "libmmd_runtime_ffi.dylib" "mmd_runtime_ffi.dylib" "libmmd_anim_ffi.dylib" "mmd_anim_ffi.dylib")
        if(EXISTS "${MMD_ANIM_FFI_LIB_DIR}/${_mmd_ffi_dylib}" AND NOT MMD_ANIM_FFI_RUNTIME_LIB)
            set(MMD_ANIM_FFI_RUNTIME_LIB "${MMD_ANIM_FFI_LIB_DIR}/${_mmd_ffi_dylib}")
        endif()
    endforeach()
endif()

# ------------------------------------------------------------------
# コンパイル定義 & リンク (Maya plugin 標準)
# ------------------------------------------------------------------
target_compile_definitions(mmd_tools_cpp PRIVATE
    REQUIRE_IOSTREAM
    _BOOL
)

if(MSVC)
    target_compile_options(mmd_tools_cpp PRIVATE /utf-8)
    set_property(TARGET mmd_tools_cpp PROPERTY
        MSVC_RUNTIME_LIBRARY "MultiThreadedDLL"
    )
endif()

if(WIN32)
    target_compile_definitions(mmd_tools_cpp PRIVATE NT_PLUGIN)
    target_link_directories(mmd_tools_cpp PRIVATE ${MAYA_LIB_DIR})
    target_link_libraries(mmd_tools_cpp PRIVATE
        OpenMaya.lib
        OpenMayaRender.lib
        OpenMayaUI.lib
        Foundation.lib
    )
    if(MMD_ANIM_FFI_LIB)
        target_link_libraries(mmd_tools_cpp PRIVATE ${MMD_ANIM_FFI_LIB})
        if(MMD_ANIM_FFI_RUNTIME_LIB)
            add_custom_command(TARGET mmd_tools_cpp POST_BUILD
                COMMAND ${CMAKE_COMMAND} -E copy_if_different
                    "${MMD_ANIM_FFI_RUNTIME_LIB}"
                    "$<TARGET_FILE_DIR:mmd_tools_cpp>"
            )
        endif()
        message(STATUS "Found mmd-anim-ffi import library: ${MMD_ANIM_FFI_LIB}")
    else()
        message(WARNING "mmd-anim-ffi import library not found. Build mmd-anim-ffi first.")
    endif()
    set_target_properties(mmd_tools_cpp PROPERTIES
        SUFFIX ".mll"
        RUNTIME_OUTPUT_DIRECTORY "${REPO_ROOT}/plug-ins/${MAYA_VERSION}/$<CONFIG>"
    )
endif()

if(APPLE)
    target_compile_definitions(mmd_tools_cpp PRIVATE MAC_PLUGIN OSMac_)
    target_link_directories(mmd_tools_cpp PRIVATE ${MAYA_LIB_DIR})
    target_link_libraries(mmd_tools_cpp PRIVATE
        OpenMaya
        OpenMayaRender
        OpenMayaUI
        Foundation
    )
    if(MMD_ANIM_FFI_LIB)
        target_link_libraries(mmd_tools_cpp PRIVATE ${MMD_ANIM_FFI_LIB})
        if(MMD_ANIM_FFI_RUNTIME_LIB)
            add_custom_command(TARGET mmd_tools_cpp POST_BUILD
                COMMAND ${CMAKE_COMMAND} -E copy_if_different
                    "${MMD_ANIM_FFI_RUNTIME_LIB}"
                    "$<TARGET_FILE_DIR:mmd_tools_cpp>"
            )
        endif()
        message(STATUS "Found mmd-anim-ffi library: ${MMD_ANIM_FFI_LIB}")
    else()
        message(WARNING "mmd-anim-ffi library not found. Build mmd-anim-ffi first.")
    endif()
    set_target_properties(mmd_tools_cpp PROPERTIES
        SUFFIX ".bundle"
        PREFIX ""
        LIBRARY_OUTPUT_DIRECTORY "${REPO_ROOT}/plug-ins/${MAYA_VERSION}/$<CONFIG>"
    )
endif()

# ------------------------------------------------------------------
# インストール
# ------------------------------------------------------------------
install(TARGETS mmd_tools_cpp
    RUNTIME DESTINATION "plug-ins/${MAYA_VERSION}"
)

# ------------------------------------------------------------------
# Standalone CLI smoke (C++ only, no Maya). Built together with the plugin.
# Reuses mmdRuntimeBridge (compiled again for exe; small). Links FFI only.
# Output placed under build/cpp/maya<ver>/<config>/ so no mayapy needed.
# FFI DLL is copied next to exe (LoadLibrary candidates will find it from app dir).
# ------------------------------------------------------------------
add_executable(mmd_runtime_smoke
    mmdRuntimeSmoke.cpp
    mmdRuntimeBridge.cpp
)

target_include_directories(mmd_runtime_smoke PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}
)
target_compile_features(mmd_runtime_smoke PRIVATE cxx_std_17)

if(EXISTS "${MMD_ANIM_FFI_INCLUDE_DIR}/mmd_runtime.h")
    target_include_directories(mmd_runtime_smoke PRIVATE ${MMD_ANIM_FFI_INCLUDE_DIR})
    message(STATUS "mmd_runtime_smoke: using mmd-anim-ffi header at ${MMD_ANIM_FFI_INCLUDE_DIR}")
else()
    message(FATAL_ERROR "mmd_runtime.h not found; required for mmd_runtime_smoke. Run ffi_build first.")
endif()

# Standalone native render-order contract smoke.  This target deliberately
# has no Maya or FFI dependency so the ordering rule remains testable even
# when a full plugin build is unavailable.
add_executable(mmd_render_queue_smoke
    MmdRenderQueueSmoke.cpp
    MmdRenderQueue.cpp
)

target_include_directories(mmd_render_queue_smoke PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}
)
target_compile_features(mmd_render_queue_smoke PRIVATE cxx_std_17)

if(WIN32)
    set_target_properties(mmd_render_queue_smoke PROPERTIES
        RUNTIME_OUTPUT_DIRECTORY "${REPO_ROOT}/build/cpp/maya${MAYA_VERSION}/$<CONFIG>"
    )
endif()

if(MSVC)
    target_compile_options(mmd_render_queue_smoke PRIVATE /utf-8)
    set_property(TARGET mmd_render_queue_smoke PROPERTY
        MSVC_RUNTIME_LIBRARY "MultiThreadedDLL"
    )
endif()

if(WIN32)
    if(MMD_ANIM_FFI_LIB)
        target_link_libraries(mmd_runtime_smoke PRIVATE ${MMD_ANIM_FFI_LIB})
        if(MMD_ANIM_FFI_RUNTIME_LIB)
            add_custom_command(TARGET mmd_runtime_smoke POST_BUILD
                COMMAND ${CMAKE_COMMAND} -E copy_if_different
                    "${MMD_ANIM_FFI_RUNTIME_LIB}"
                    "$<TARGET_FILE_DIR:mmd_runtime_smoke>"
            )
            message(STATUS "mmd_runtime_smoke: will copy ${MMD_ANIM_FFI_RUNTIME_LIB} next to exe")
        endif()
    else()
        message(WARNING "mmd-anim-ffi import library not found for mmd_runtime_smoke.")
    endif()
    set_target_properties(mmd_runtime_smoke PROPERTIES
        RUNTIME_OUTPUT_DIRECTORY "${REPO_ROOT}/build/cpp/maya${MAYA_VERSION}/$<CONFIG>"
    )
endif()

if(MSVC)
    target_compile_options(mmd_runtime_smoke PRIVATE /utf-8)
    set_property(TARGET mmd_runtime_smoke PROPERTY
        MSVC_RUNTIME_LIBRARY "MultiThreadedDLL"
    )
endif()

if(APPLE)
    if(MMD_ANIM_FFI_LIB)
        target_link_libraries(mmd_runtime_smoke PRIVATE ${MMD_ANIM_FFI_LIB})
    endif()
    set_target_properties(mmd_runtime_smoke PROPERTIES
        RUNTIME_OUTPUT_DIRECTORY "${REPO_ROOT}/build/cpp/maya${MAYA_VERSION}/$<CONFIG>"
    )
endif()

message(STATUS "maya_mmd_tools C++ configured for Maya ${MAYA_VERSION}")
message(STATUS "  Devkit: ${MAYA_DEVKIT_ROOT}")
