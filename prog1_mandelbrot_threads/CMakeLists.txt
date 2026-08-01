cmake_minimum_required(VERSION 3.20)

project(mandelbrot_threads LANGUAGES CXX)

find_package(Threads REQUIRED)

add_executable(mandelbrot
    main.cpp
    mandelbrotSerial.cpp
    mandelbrotThread.cpp
    ThreadAffinity.cpp
    ../common/ppm.cpp)

target_compile_features(mandelbrot PRIVATE cxx_std_17)
target_include_directories(mandelbrot PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}
    ${CMAKE_CURRENT_SOURCE_DIR}/../common)
target_link_libraries(mandelbrot PRIVATE Threads::Threads)

if(MSVC)
    target_compile_options(mandelbrot PRIVATE /W4 /permissive-)
    target_compile_definitions(mandelbrot PRIVATE
        _WIN32_WINNT=0x0A00
        WIN32_LEAN_AND_MEAN
        NOMINMAX)
else()
    target_compile_options(mandelbrot PRIVATE -Wall -Wextra -Wpedantic)
endif()
