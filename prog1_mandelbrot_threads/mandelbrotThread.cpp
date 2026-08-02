#include <stdio.h>
#include <cstdlib>
#include <string>
#include <thread>

#include "CycleTimer.h"
#include "ThreadAffinity.h"

typedef struct {
    float x0, x1;
    float y0, y1;
    unsigned int width;
    unsigned int height;
    int maxIterations;
    int* output;
    int threadId;
    int numThreads;
    double* elapsedSeconds;
    const ThreadAffinityTarget* affinityTarget;
    std::string* affinityError;
} WorkerArgs;


extern void mandelbrotSerial(
    float x0, float y0, float x1, float y1,
    int width, int height,
    int startRow, int numRows,
    int maxIterations,
    int output[]);


//
// workerThreadStart --
//
// Thread entrypoint.
void workerThreadStart(WorkerArgs * const args) {
    ThreadAffinityState affinityState;
    if (args->affinityTarget != nullptr &&
        !bindCurrentThread(
            *args->affinityTarget, affinityState, *args->affinityError)) {
        return;
    }

    const double startTime = args->elapsedSeconds == nullptr
        ? 0.0
        : CycleTimer::currentSeconds();

    const int rowsPerThread = args->height / args->numThreads;
    const int startRow = args->threadId * rowsPerThread;
    const int numRows = (args->threadId == args->numThreads - 1)
        ? args->height - startRow
        : rowsPerThread;

    mandelbrotSerial(
        args->x0, args->y0, args->x1, args->y1,
        args->width, args->height,
        startRow, numRows,
        args->maxIterations,
        args->output);

    if (args->elapsedSeconds != nullptr) {
        *args->elapsedSeconds = CycleTimer::currentSeconds() - startTime;
    }

    if (args->affinityTarget != nullptr) {
        std::string restoreError;
        if (!restoreCurrentThreadAffinity(affinityState, restoreError)) {
            *args->affinityError = restoreError;
        }
    }
}

//
// MandelbrotThread --
//
// Multi-threaded implementation of mandelbrot set image generation.
// Threads of execution are created by spawning std::threads.
bool mandelbrotThread(
    int numThreads,
    float x0, float y0, float x1, float y1,
    int width, int height,
    int maxIterations, int output[], double workerElapsedSeconds[],
    const ThreadAffinityPlan* affinityPlan,
    std::string& error)
{
    static constexpr int MAX_THREADS = 32;

    error.clear();

    if (numThreads < 1 || numThreads > MAX_THREADS)
    {
        error = "Thread count must be between 1 and 32.";
        return false;
    }
    if (affinityPlan != nullptr &&
        affinityPlan->targets.size() != static_cast<std::size_t>(numThreads)) {
        error = "Affinity plan does not contain exactly one target per thread.";
        return false;
    }

    // Creates thread objects that do not yet represent a thread.
    std::thread workers[MAX_THREADS];
    WorkerArgs args[MAX_THREADS];
    std::string affinityErrors[MAX_THREADS];

    for (int i=0; i<numThreads; i++) {
      
        // TODO FOR CS149 STUDENTS: You may or may not wish to modify
        // the per-thread arguments here.  The code below copies the
        // same arguments for each thread
        args[i].x0 = x0;
        args[i].y0 = y0;
        args[i].x1 = x1;
        args[i].y1 = y1;
        args[i].width = width;
        args[i].height = height;
        args[i].maxIterations = maxIterations;
        args[i].numThreads = numThreads;
        args[i].output = output;
      
        args[i].threadId = i;
        args[i].elapsedSeconds = workerElapsedSeconds == nullptr
            ? nullptr
            : &workerElapsedSeconds[i];
        if (args[i].elapsedSeconds != nullptr) {
            *args[i].elapsedSeconds = 0.0;
        }
        args[i].affinityTarget = affinityPlan == nullptr
            ? nullptr
            : &affinityPlan->targets[static_cast<std::size_t>(i)];
        args[i].affinityError = &affinityErrors[i];
    }

    // Spawn the worker threads.  Note that only numThreads-1 std::threads
    // are created and the main application thread is used as a worker
    // as well.
    for (int i=1; i<numThreads; i++) {
        workers[i] = std::thread(workerThreadStart, &args[i]);
    }
    
    workerThreadStart(&args[0]);

    // join worker threads
    for (int i=1; i<numThreads; i++) {
        workers[i].join();
    }

    for (int i = 0; i < numThreads; ++i) {
        if (!affinityErrors[i].empty()) {
            error = "Worker " + std::to_string(i) + ": " + affinityErrors[i];
            return false;
        }
    }
    return true;
}
