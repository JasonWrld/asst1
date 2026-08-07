cmake_minimum_required(VERSION 3.20)

project(saxpy_windows_native LANGUAGES CXX)

if(NOT WIN32 OR NOT MSVC)
    message(FATAL_ERROR
        "This CMake configuration builds the native Windows/MSVC executable. "
        "Use the existing Makefile on Linux or WSL.")
endif()

set(ISPC_EXECUTABLE "" CACHE FILEPATH "Path to the native Windows ispc.exe")
if(NOT ISPC_EXECUTABLE)
    find_program(ISPC_EXECUTABLE NAMES ispc ispc.exe)
endif()
if(NOT ISPC_EXECUTABLE)
    message(FATAL_ERROR
        "Unable to find Windows ispc.exe. Put it on PATH or configure "
        "-DISPC_EXECUTABLE=C:/path/to/ispc.exe")
endif()

set(ISPC_GENERATED_DIR "${CMAKE_CURRENT_BINARY_DIR}/generated")
set(ISPC_OBJECT "${ISPC_GENERATED_DIR}/saxpy_ispc.obj")
set(ISPC_HEADER "${ISPC_GENERATED_DIR}/saxpy_ispc.h")

add_custom_command(
    OUTPUT "${ISPC_OBJECT}" "${ISPC_HEADER}"
    COMMAND "${CMAKE_COMMAND}" -E make_directory "${ISPC_GENERATED_DIR}"
    COMMAND "${ISPC_EXECUTABLE}"
        -O3
        --target=avx2-i32x8
        --arch=x86-64
        "${CMAKE_CURRENT_SOURCE_DIR}/saxpy.ispc"
        -o "${ISPC_OBJECT}"
        -h "${ISPC_HEADER}"
    DEPENDS "${CMAKE_CURRENT_SOURCE_DIR}/saxpy.ispc"
    COMMENT "Compiling saxpy.ispc for Windows AVX2"
    VERBATIM)

add_custom_target(saxpy_ispc_codegen
    DEPENDS "${ISPC_OBJECT}" "${ISPC_HEADER}")

set_source_files_properties(
    "${ISPC_OBJECT}"
    PROPERTIES GENERATED TRUE EXTERNAL_OBJECT TRUE)
set_source_files_properties(
    "${ISPC_HEADER}"
    PROPERTIES GENERATED TRUE)

add_executable(saxpy
    main.cpp
    saxpySerial.cpp
    ThreadAffinity.cpp
    ThreadAffinity.h
    ../common/tasksys.cpp
    "${ISPC_OBJECT}"
    "${ISPC_HEADER}")
add_dependencies(saxpy saxpy_ispc_codegen)

target_compile_features(saxpy PRIVATE cxx_std_17)
target_include_directories(saxpy PRIVATE
    "${CMAKE_CURRENT_SOURCE_DIR}"
    "${CMAKE_CURRENT_SOURCE_DIR}/../common"
    "${ISPC_GENERATED_DIR}")
target_compile_options(saxpy PRIVATE /W4 /permissive-)
target_compile_definitions(saxpy PRIVATE
    _WIN32_WINNT=0x0A00
    WIN32_LEAN_AND_MEAN)
target_link_libraries(saxpy PRIVATE concrt)
