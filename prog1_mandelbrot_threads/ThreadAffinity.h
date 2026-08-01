#ifndef THREAD_AFFINITY_H_
#define THREAD_AFFINITY_H_

#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

struct ThreadAffinityTarget {
    std::uint32_t cpuSetId;
    std::uint16_t group;
    std::uint8_t logicalProcessorIndex;
    std::uint8_t coreIndex;
    std::uint8_t efficiencyClass;
};

struct ThreadAffinityPlan {
    std::vector<ThreadAffinityTarget> targets;
    std::size_t detectedPerformanceCoreCount = 0;
};

struct ThreadAffinityState {
    std::uint64_t previousMask = 0;
    std::uint16_t previousGroup = 0;
    bool active = false;
};

// Builds a one-thread-per-physical-P-core plan. On non-Windows platforms this
// returns false with an explanatory error because WSL cannot identify host
// P-cores reliably.
bool buildPerformanceCorePlan(
    int threadCount,
    ThreadAffinityPlan& plan,
    std::string& error);

void printThreadAffinityPlan(const ThreadAffinityPlan& plan);

bool bindCurrentThread(
    const ThreadAffinityTarget& target,
    ThreadAffinityState& state,
    std::string& error);

bool restoreCurrentThreadAffinity(
    ThreadAffinityState& state,
    std::string& error);

#endif  // THREAD_AFFINITY_H_
