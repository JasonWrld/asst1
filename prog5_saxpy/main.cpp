#include <algorithm>
#include <cstdio>
#include <string>
#include <vector>

#include "CycleTimer.h"
#include "ThreadAffinity.h"
#include "saxpy_ispc.h"

extern void saxpySerial(
    int N,
    float scale,
    float X[],
    float Y[],
    float result[]);

using namespace ispc;

namespace {

void usage(const char* programName) {
    std::printf("Usage: %s [options]\n", programName);
    std::printf("Program Options:\n");
    std::printf(
        "      --simulate-myth4  Restrict Windows execution to 4 P-cores/8 SMT contexts\n");
    std::printf("  -?  --help            Show this message\n");
}

// Return GB/s.
float toBW(int bytes, float seconds) {
    return static_cast<float>(bytes) /
           (1024.0f * 1024.0f * 1024.0f) /
           seconds;
}

float toGFLOPS(int operations, float seconds) {
    return static_cast<float>(operations) / 1.0e9f / seconds;
}

bool verifyResult(int count, const float* result, const float* gold) {
    for (int i = 0; i < count; ++i) {
        if (result[i] != gold[i]) {
            std::printf(
                "Error: [%d] Got %f expected %f\n",
                i,
                result[i],
                gold[i]);
            return false;
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
        return applyProcessCpuSetRestriction(
            logicalProcessors, state_, error);
    }

    bool restore(std::string& error) {
        return restoreProcessCpuSetRestriction(state_, error);
    }

private:
    ProcessCpuSetState state_;
};

class ScopedThreadAffinity {
public:
    ~ScopedThreadAffinity() {
        std::string ignored;
        restore(ignored);
    }

    bool bind(
        const ThreadAffinityTarget& target,
        std::string& error) {
        return bindCurrentThread(target, state_, error);
    }

    bool restore(std::string& error) {
        return restoreCurrentThreadAffinity(state_, error);
    }

private:
    ThreadAffinityState state_;
};

}  // namespace

int main(int argc, char** argv) {
    bool simulateMyth4 = false;
    for (int i = 1; i < argc; ++i) {
        const std::string argument(argv[i]);
        if (argument == "-?" || argument == "--help") {
            usage(argv[0]);
            return 0;
        }
        if (argument == "--simulate-myth4") {
            simulateMyth4 = true;
            continue;
        }
        std::fprintf(stderr, "Unknown option: %s\n", argument.c_str());
        usage(argv[0]);
        return 1;
    }

    Myth4AffinityPlan affinityPlan;
    ScopedProcessCpuSetRestriction processCpuSetRestriction;
    if (simulateMyth4) {
        std::string affinityError;
        if (!buildMyth4AffinityPlan(affinityPlan, affinityError)) {
            std::fprintf(
                stderr,
                "Unable to prepare myth4 affinity: %s\n",
                affinityError.c_str());
            return 1;
        }
        printMyth4AffinityPlan(affinityPlan);
        if (!processCpuSetRestriction.apply(
                affinityPlan.logicalProcessors, affinityError)) {
            std::fprintf(
                stderr,
                "Unable to apply myth4 CPU Set restriction: %s\n",
                affinityError.c_str());
            return 1;
        }
        if (!configureMyth4TaskRuntime(affinityError)) {
            std::fprintf(
                stderr,
                "Unable to configure myth4 task runtime: %s\n",
                affinityError.c_str());
            return 1;
        }
    }

    constexpr int N = 20 * 1000 * 1000;
    constexpr int totalBytes = 4 * N * static_cast<int>(sizeof(float));
    constexpr int totalFlops = 2 * N;
    constexpr float scale = 2.0f;

    std::vector<float> arrayX(static_cast<std::size_t>(N));
    std::vector<float> arrayY(static_cast<std::size_t>(N));
    std::vector<float> resultSerial(static_cast<std::size_t>(N), 0.0f);
    std::vector<float> resultISPC(static_cast<std::size_t>(N), 0.0f);
    std::vector<float> resultTasks(static_cast<std::size_t>(N), 0.0f);

    for (int i = 0; i < N; ++i) {
        arrayX[static_cast<std::size_t>(i)] = static_cast<float>(i);
        arrayY[static_cast<std::size_t>(i)] = static_cast<float>(i);
    }

    ScopedThreadAffinity referenceAffinity;
    if (simulateMyth4) {
        std::string affinityError;
        if (!referenceAffinity.bind(
                affinityPlan.logicalProcessors.front(), affinityError)) {
            std::fprintf(
                stderr,
                "Unable to pin serial/ISPC reference: %s\n",
                affinityError.c_str());
            return 1;
        }
    }

    double minSerial = 1e30;
    for (int i = 0; i < 3; ++i) {
        const double startTime = CycleTimer::currentSeconds();
        saxpySerial(
            N,
            scale,
            arrayX.data(),
            arrayY.data(),
            resultSerial.data());
        const double endTime = CycleTimer::currentSeconds();
        minSerial = std::min(minSerial, endTime - startTime);
    }

    double minISPC = 1e30;
    for (int i = 0; i < 3; ++i) {
        const double startTime = CycleTimer::currentSeconds();
        saxpy_ispc(
            N,
            scale,
            arrayX.data(),
            arrayY.data(),
            resultISPC.data());
        const double endTime = CycleTimer::currentSeconds();
        minISPC = std::min(minISPC, endTime - startTime);
    }

    if (!verifyResult(N, resultISPC.data(), resultSerial.data())) {
        return 1;
    }

    std::printf(
        "[saxpy ispc]:\t\t[%.3f] ms\t[%.3f] GB/s\t[%.3f] GFLOPS\n",
        minISPC * 1000.0,
        toBW(totalBytes, static_cast<float>(minISPC)),
        toGFLOPS(totalFlops, static_cast<float>(minISPC)));

    if (simulateMyth4) {
        std::string affinityError;
        if (!referenceAffinity.restore(affinityError)) {
            std::fprintf(
                stderr,
                "Unable to restore reference-thread affinity: %s\n",
                affinityError.c_str());
            return 1;
        }
    }

    double minTaskISPC = 1e30;
    for (int i = 0; i < 3; ++i) {
        const double startTime = CycleTimer::currentSeconds();
        saxpy_ispc_withtasks(
            N,
            scale,
            arrayX.data(),
            arrayY.data(),
            resultTasks.data());
        const double endTime = CycleTimer::currentSeconds();
        minTaskISPC = std::min(minTaskISPC, endTime - startTime);
    }

    if (!verifyResult(N, resultTasks.data(), resultSerial.data())) {
        return 1;
    }

    std::printf(
        "[saxpy task ispc]:\t[%.3f] ms\t[%.3f] GB/s\t[%.3f] GFLOPS\n",
        minTaskISPC * 1000.0,
        toBW(totalBytes, static_cast<float>(minTaskISPC)),
        toGFLOPS(totalFlops, static_cast<float>(minTaskISPC)));

    std::printf(
        "\t\t\t\t(%.2fx speedup from use of tasks)\n",
        minISPC / minTaskISPC);

    if (simulateMyth4) {
        std::string affinityError;
        if (!processCpuSetRestriction.restore(affinityError)) {
            std::fprintf(
                stderr,
                "Unable to restore process CPU Sets: %s\n",
                affinityError.c_str());
            return 1;
        }
    }
    return 0;
}
