#include <stdio.h>

#include <algorithm>
#include <cerrno>
#include <cstdlib>
#include <cstring>
#include <limits>
#include <string>
#include <vector>

#include "CycleTimer.h"
#include "ThreadAffinity.h"
#include "mandelbrotThread.h"

extern void mandelbrotSerial(
    float x0, float y0, float x1, float y1,
    int width, int height,
    int startRow, int numRows,
    int maxIterations,
    int output[]);

extern void writePPMImage(
    int* data,
    int width, int height,
    const char *filename,
    int maxIterations);

void scaleAndShift(
    float& x0, float& x1, float& y0, float& y1,
    float scale,
    float shiftX, float shiftY) {
    x0 *= scale;
    x1 *= scale;
    y0 *= scale;
    y1 *= scale;
    x0 += shiftX;
    x1 += shiftX;
    y0 += shiftY;
    y1 += shiftY;
}

void usage(const char* progname) {
    printf("Usage: %s [options]\n", progname);
    printf("Program Options:\n");
    printf("  -t  --threads <N>  Use N threads\n");
    printf("  -v  --view <INT>   Use specified view settings\n");
    printf("      --pin-p-cores  Pin each worker to a distinct Windows P-core\n");
    printf("      --simulate-myth4  Restrict execution to 4 Windows P-cores/8 SMT contexts\n");
    printf("      --decomposition <MODE>  Row assignment: block or interleaved (default)\n");
    printf("      --profile-workers  Report per-worker compute time from the fastest trial\n");
    printf("  -?  --help         This message\n");
}

bool parseInteger(const std::string& text, int& value) {
    if (text.empty()) {
        return false;
    }

    errno = 0;
    char* end = nullptr;
    const long parsed = std::strtol(text.c_str(), &end, 10);
    if (errno == ERANGE || end == text.c_str() || *end != '\0' ||
        parsed < std::numeric_limits<int>::min() ||
        parsed > std::numeric_limits<int>::max()) {
        return false;
    }
    value = static_cast<int>(parsed);
    return true;
}

bool verifyResult(int* gold, int* result, int width, int height) {
    for (int i = 0; i < height; i++) {
        for (int j = 0; j < width; j++) {
            if (gold[i * width + j] != result[i * width + j]) {
                printf("Mismatch : [%d][%d], Expected : %d, Actual : %d\n",
                       i, j,
                       gold[i * width + j],
                       result[i * width + j]);
                return false;
            }
        }
    }
    return true;
}

class ScopedProcessCpuSetRestriction {
public:
    ~ScopedProcessCpuSetRestriction() {
        std::string ignored;
        restore(ignored);
    }

    bool apply(
        const std::vector<ThreadAffinityTarget>& logicalProcessors,
        std::string& error) {
        return applyProcessCpuSetRestriction(logicalProcessors, state_, error);
    }

    bool restore(std::string& error) {
        return restoreProcessCpuSetRestriction(state_, error);
    }

private:
    ProcessCpuSetState state_;
};

int main(int argc, char** argv) {
    const unsigned int width = 1600;
    const unsigned int height = 1200;
    const int maxIterations = 256;
    int numThreads = 2;
    int viewIndex = 1;
    bool pinPerformanceCores = false;
    bool simulateMyth4 = false;
    bool profileWorkers = false;
    RowDecomposition decomposition = RowDecomposition::Interleaved;

    float x0 = -2;
    float x1 = 1;
    float y0 = -1;
    float y1 = 1;

    // Parse options without GNU getopt so the same source builds with MSVC.
    for (int i = 1; i < argc; ++i) {
        const std::string argument(argv[i]);
        std::string value;

        if (argument == "-?" || argument == "--help") {
            usage(argv[0]);
            return 0;
        } else if (argument == "--pin-p-cores") {
            pinPerformanceCores = true;
        } else if (argument == "--simulate-myth4") {
            simulateMyth4 = true;
        } else if (argument == "--profile-workers") {
            profileWorkers = true;
        } else if (argument == "--decomposition") {
            if (++i >= argc) {
                fprintf(stderr, "Missing value for %s\n", argument.c_str());
                return 1;
            }
            value = argv[i];
            if (value == "block") {
                decomposition = RowDecomposition::Block;
            } else if (value == "interleaved") {
                decomposition = RowDecomposition::Interleaved;
            } else {
                fprintf(stderr,
                        "Invalid decomposition: %s (expected block or interleaved)\n",
                        value.c_str());
                return 1;
            }
        } else if (argument.rfind("--decomposition=", 0) == 0) {
            value = argument.substr(std::strlen("--decomposition="));
            if (value == "block") {
                decomposition = RowDecomposition::Block;
            } else if (value == "interleaved") {
                decomposition = RowDecomposition::Interleaved;
            } else {
                fprintf(stderr,
                        "Invalid decomposition: %s (expected block or interleaved)\n",
                        value.c_str());
                return 1;
            }
        } else if (argument == "-t" || argument == "--threads") {
            if (++i >= argc) {
                fprintf(stderr, "Missing value for %s\n", argument.c_str());
                return 1;
            }
            value = argv[i];
            if (!parseInteger(value, numThreads)) {
                fprintf(stderr, "Invalid thread count: %s\n", value.c_str());
                return 1;
            }
        } else if (argument.rfind("--threads=", 0) == 0) {
            value = argument.substr(std::strlen("--threads="));
            if (!parseInteger(value, numThreads)) {
                fprintf(stderr, "Invalid thread count: %s\n", value.c_str());
                return 1;
            }
        } else if (argument == "-v" || argument == "--view") {
            if (++i >= argc) {
                fprintf(stderr, "Missing value for %s\n", argument.c_str());
                return 1;
            }
            value = argv[i];
            if (!parseInteger(value, viewIndex)) {
                fprintf(stderr, "Invalid view index: %s\n", value.c_str());
                return 1;
            }
        } else if (argument.rfind("--view=", 0) == 0) {
            value = argument.substr(std::strlen("--view="));
            if (!parseInteger(value, viewIndex)) {
                fprintf(stderr, "Invalid view index: %s\n", value.c_str());
                return 1;
            }
        } else {
            fprintf(stderr, "Unknown option: %s\n", argument.c_str());
            usage(argv[0]);
            return 1;
        }
    }

    if (numThreads < 1 || numThreads > 32) {
        fprintf(stderr, "Thread count must be between 1 and 32\n");
        return 1;
    }
    if (pinPerformanceCores && simulateMyth4) {
        fprintf(stderr,
                "--pin-p-cores and --simulate-myth4 cannot be used together\n");
        return 1;
    }
    if (viewIndex == 2) {
        scaleAndShift(x0, x1, y0, y1, .015f, -.986f, .30f);
    } else if (viewIndex != 1) {
        fprintf(stderr, "Invalid view index: %d\n", viewIndex);
        return 1;
    }

    printf("[row decomposition]:\t\t[%s]\n",
           decomposition == RowDecomposition::Block
               ? "block"
               : "interleaved");

    ThreadAffinityPlan affinityPlan;
    const ThreadAffinityPlan* selectedAffinityPlan = nullptr;
    ScopedProcessCpuSetRestriction processCpuSetRestriction;
    if (pinPerformanceCores) {
        std::string affinityError;
        if (!buildPerformanceCorePlan(numThreads, affinityPlan, affinityError)) {
            fprintf(stderr, "Unable to prepare P-core affinity: %s\n",
                    affinityError.c_str());
            return 1;
        }
        printThreadAffinityPlan(affinityPlan);
        selectedAffinityPlan = &affinityPlan;
    } else if (simulateMyth4) {
        std::string affinityError;
        if (!buildMyth4AffinityPlan(
                numThreads, affinityPlan, affinityError)) {
            fprintf(stderr, "Unable to prepare myth4 affinity: %s\n",
                    affinityError.c_str());
            return 1;
        }
        printMyth4AffinityPlan(affinityPlan);
        if (!processCpuSetRestriction.apply(
                affinityPlan.logicalProcessors, affinityError)) {
            fprintf(stderr, "Unable to apply myth4 CPU Set restriction: %s\n",
                    affinityError.c_str());
            return 1;
        }
        if (!affinityPlan.workersUseSystemScheduling) {
            selectedAffinityPlan = &affinityPlan;
        }
    }

    int* outputSerial = new int[width * height];
    int* outputThread = new int[width * height];

    ThreadAffinityState serialAffinityState;
    if (simulateMyth4) {
        std::string affinityError;
        if (!bindCurrentThread(
                affinityPlan.logicalProcessors.front(),
                serialAffinityState,
                affinityError)) {
            fprintf(stderr, "Unable to pin serial reference to a P-core: %s\n",
                    affinityError.c_str());
            delete[] outputSerial;
            delete[] outputThread;
            return 1;
        }
    }

    double minSerial = 1e30;
    for (int i = 0; i < 5; ++i) {
        memset(outputSerial, 0, width * height * sizeof(int));
        const double startTime = CycleTimer::currentSeconds();
        mandelbrotSerial(
            x0, y0, x1, y1,
            width, height,
            0, height,
            maxIterations,
            outputSerial);
        const double endTime = CycleTimer::currentSeconds();
        minSerial = std::min(minSerial, endTime - startTime);
    }

    if (simulateMyth4) {
        std::string affinityError;
        if (!restoreCurrentThreadAffinity(
                serialAffinityState, affinityError)) {
            fprintf(stderr, "Unable to restore serial thread affinity: %s\n",
                    affinityError.c_str());
            delete[] outputSerial;
            delete[] outputThread;
            return 1;
        }
    }

    printf("[mandelbrot serial]:\t\t[%.3f] ms\n", minSerial * 1000);
    writePPMImage(
        outputSerial,
        width, height,
        "mandelbrot-serial.ppm",
        maxIterations);

    double minThread = 1e30;
    std::vector<double> bestWorkerTimes;
    for (int i = 0; i < 5; ++i) {
        memset(outputThread, 0, width * height * sizeof(int));
        std::vector<double> trialWorkerTimes(
            profileWorkers ? static_cast<std::size_t>(numThreads) : 0U);
        const double startTime = CycleTimer::currentSeconds();
        std::string threadError;
        if (!mandelbrotThread(
                numThreads,
                x0, y0, x1, y1,
                width, height,
                maxIterations,
                outputThread,
                decomposition,
                profileWorkers ? trialWorkerTimes.data() : nullptr,
                selectedAffinityPlan,
                threadError)) {
            fprintf(stderr, "Threaded computation failed: %s\n", threadError.c_str());
            delete[] outputSerial;
            delete[] outputThread;
            return 1;
        }
        const double endTime = CycleTimer::currentSeconds();
        const double elapsed = endTime - startTime;
        if (elapsed < minThread) {
            minThread = elapsed;
            if (profileWorkers) {
                bestWorkerTimes = trialWorkerTimes;
            }
        }
    }

    printf("[mandelbrot thread]:\t\t[%.3f] ms\n", minThread * 1000);
    writePPMImage(
        outputThread,
        width, height,
        "mandelbrot-thread.ppm",
        maxIterations);

    if (!verifyResult(outputSerial, outputThread, width, height)) {
        printf("Error : Output from threads does not match serial output\n");
        delete[] outputSerial;
        delete[] outputThread;
        return 1;
    }

    printf("\t\t\t\t(%.2fx speedup from %d threads)\n",
           minSerial / minThread,
           numThreads);

    if (profileWorkers) {
        printf("[worker timing trial]:\t\t[%.3f] ms\n", minThread * 1000);
        if (decomposition == RowDecomposition::Block) {
            const int rowsPerThread = static_cast<int>(height) / numThreads;
            for (int worker = 0; worker < numThreads; ++worker) {
                const int startRow = worker * rowsPerThread;
                const int numRows = worker == numThreads - 1
                    ? static_cast<int>(height) - startRow
                    : rowsPerThread;
                printf("[worker %d]: rows [%d, %d), [%.3f] ms\n",
                       worker,
                       startRow,
                       startRow + numRows,
                       bestWorkerTimes[static_cast<std::size_t>(worker)] * 1000);
            }
        } else {
            for (int worker = 0; worker < numThreads; ++worker) {
                const int rowCount = worker < static_cast<int>(height)
                    ? 1 + (static_cast<int>(height) - 1 - worker) / numThreads
                    : 0;
                printf("[worker %d]: interleaved rows start %d, stride %d, "
                       "count %d, [%.3f] ms\n",
                       worker,
                       worker,
                       numThreads,
                       rowCount,
                       bestWorkerTimes[static_cast<std::size_t>(worker)] * 1000);
            }
        }
    }

    delete[] outputSerial;
    delete[] outputThread;

    if (simulateMyth4) {
        std::string affinityError;
        if (!processCpuSetRestriction.restore(affinityError)) {
            fprintf(stderr, "Unable to restore process CPU Sets: %s\n",
                    affinityError.c_str());
            return 1;
        }
    }
    return 0;
}
