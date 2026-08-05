#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <string>
#include <vector>

#include "CycleTimer.h"
#include "ThreadAffinity.h"
#include "sqrt_ispc.h"

using namespace ispc;

extern void sqrtSerial(
    int N,
    float startGuess,
    float values[],
    float output[]);

namespace {

enum class InputMode {
    Random,
    Best,
    Worst,
};

void usage(const char* programName) {
    std::printf("Usage: %s [options]\n", programName);
    std::printf("Program Options:\n");
    std::printf(
        "      --simulate-myth4  Restrict Windows execution to 4 P-cores/8 SMT contexts\n");
    std::printf(
        "      --input <MODE>    Input distribution: random, best, or worst (default: random)\n");
    std::printf("  -?  --help            Show this message\n");
}

bool parseInputMode(const std::string& value, InputMode& mode) {
    if (value == "random") {
        mode = InputMode::Random;
        return true;
    }
    if (value == "best") {
        mode = InputMode::Best;
        return true;
    }
    if (value == "worst") {
        mode = InputMode::Worst;
        return true;
    }
    return false;
}

const char* inputModeName(InputMode mode) {
    if (mode == InputMode::Best) {
        return "best";
    }
    if (mode == InputMode::Worst) {
        return "worst";
    }
    return "random";
}

bool verifyResult(int count, const float* result, const float* gold) {
    for (int i = 0; i < count; ++i) {
        if (std::fabs(result[i] - gold[i]) > 1e-4f) {
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
    InputMode inputMode = InputMode::Random;
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
        if (argument == "--input") {
            if (++i >= argc) {
                std::fprintf(stderr, "Missing value for --input\n");
                return 1;
            }
            const std::string value(argv[i]);
            if (!parseInputMode(value, inputMode)) {
                std::fprintf(
                    stderr,
                    "Invalid input mode: %s (expected random, best, or worst)\n",
                    value.c_str());
                return 1;
            }
            continue;
        }
        if (argument.rfind("--input=", 0) == 0) {
            const std::string value = argument.substr(8);
            if (!parseInputMode(value, inputMode)) {
                std::fprintf(
                    stderr,
                    "Invalid input mode: %s (expected random, best, or worst)\n",
                    value.c_str());
                return 1;
            }
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
    constexpr float initialGuess = 1.0f;

    std::vector<float> values(static_cast<std::size_t>(N));
    std::vector<float> output(static_cast<std::size_t>(N));
    std::vector<float> gold(static_cast<std::size_t>(N));

    std::printf("[input mode]:\t\t[%s]\n", inputModeName(inputMode));
    const float highWorkInputValue = std::nextafter(3.0f, 0.0f);
    if (inputMode == InputMode::Best) {
        std::fill(values.begin(), values.end(), highWorkInputValue);
        std::printf(
            "[uniform input value]:\t[%.9g]\n",
            static_cast<double>(highWorkInputValue));
    } else if (inputMode == InputMode::Worst) {
        constexpr int worstInputPeriod = 8;
        constexpr int heavyInputCount = N / worstInputPeriod;
        constexpr int lightInputCount = N - heavyInputCount;
        constexpr float lightInputValue = 1.0f;
        for (int i = 0; i < N; ++i) {
            values[static_cast<std::size_t>(i)] =
                i % worstInputPeriod == 0
                    ? highWorkInputValue
                    : lightInputValue;
        }
        std::printf(
            "[worst input pattern]:\t[1 heavy + 7 light per 8 values]\n");
        std::printf("[worst input period]:\t[%d]\n", worstInputPeriod);
        std::printf(
            "[heavy input value]:\t[%.9g]\n",
            static_cast<double>(highWorkInputValue));
        std::printf(
            "[light input value]:\t[%.9g]\n",
            static_cast<double>(lightInputValue));
        std::printf("[heavy input count]:\t[%d]\n", heavyInputCount);
        std::printf("[light input count]:\t[%d]\n", lightInputCount);
    } else {
        for (int i = 0; i < N; ++i) {
            values[static_cast<std::size_t>(i)] =
                .001f + 2.998f * static_cast<float>(std::rand()) / RAND_MAX;
        }
    }

    for (int i = 0; i < N; ++i) {
        gold[static_cast<std::size_t>(i)] =
            std::sqrt(values[static_cast<std::size_t>(i)]);
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
        sqrtSerial(N, initialGuess, values.data(), output.data());
        const double endTime = CycleTimer::currentSeconds();
        minSerial = std::min(minSerial, endTime - startTime);
    }

    std::printf("[sqrt serial]:\t\t[%.3f] ms\n", minSerial * 1000);
    if (!verifyResult(N, output.data(), gold.data())) {
        return 1;
    }

    double minISPC = 1e30;
    for (int i = 0; i < 3; ++i) {
        const double startTime = CycleTimer::currentSeconds();
        sqrt_ispc(N, initialGuess, values.data(), output.data());
        const double endTime = CycleTimer::currentSeconds();
        minISPC = std::min(minISPC, endTime - startTime);
    }

    std::printf("[sqrt ispc]:\t\t[%.3f] ms\n", minISPC * 1000);
    if (!verifyResult(N, output.data(), gold.data())) {
        return 1;
    }

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

    std::fill(output.begin(), output.end(), 0.0f);

    double minTaskISPC = 1e30;
    for (int i = 0; i < 3; ++i) {
        const double startTime = CycleTimer::currentSeconds();
        sqrt_ispc_withtasks(N, initialGuess, values.data(), output.data());
        const double endTime = CycleTimer::currentSeconds();
        minTaskISPC = std::min(minTaskISPC, endTime - startTime);
    }

    std::printf("[sqrt task ispc]:\t[%.3f] ms\n", minTaskISPC * 1000);
    if (!verifyResult(N, output.data(), gold.data())) {
        return 1;
    }

    std::printf("\t\t\t\t(%.2fx speedup from ISPC)\n",
                minSerial / minISPC);
    std::printf("\t\t\t\t(%.2fx speedup from task ISPC)\n",
                minSerial / minTaskISPC);

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
