#ifndef MANDELBROT_THREAD_H_
#define MANDELBROT_THREAD_H_

#include <string>

struct ThreadAffinityPlan;

enum class RowDecomposition {
    Block,
    Interleaved,
};

bool mandelbrotThread(
    int numThreads,
    float x0, float y0, float x1, float y1,
    int width, int height,
    int maxIterations, int output[],
    RowDecomposition decomposition,
    double workerElapsedSeconds[],
    const ThreadAffinityPlan* affinityPlan,
    std::string& error);

#endif  // MANDELBROT_THREAD_H_
