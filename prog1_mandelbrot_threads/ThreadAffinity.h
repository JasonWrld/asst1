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
    std::vector<ThreadAffinityTarget> logicalProcessors;
    std::size_t detectedPerformanceCoreCount = 0;
    std::size_t selectedPhysicalCoreCount = 0;
    bool workersUseSystemScheduling = false;
};

struct ThreadAffinityState {
    std::uint64_t previousMask = 0;
    std::uint16_t previousGroup = 0;
    bool active = false;
};

struct ProcessCpuSetState {
    std::vector<std::uint32_t> previousProcessCpuSetIds;
    std::vector<std::uint32_t> previousThreadCpuSetIds;
    bool processAssignmentActive = false;
    bool threadAssignmentActive = false;
};

// Builds a one-thread-per-physical-P-core plan. On non-Windows platforms this
// returns false with an explanatory error because WSL cannot identify host
// P-cores reliably.
bool buildPerformanceCorePlan(
    int threadCount,
    ThreadAffinityPlan& plan,
    std::string& error);

// Builds a Stanford myth-like topology from exactly four physical P-cores and
// both SMT contexts on each core. For up to eight workers, targets contains a
// deterministic one-worker-per-context mapping. With more workers, targets is
// empty and the workers are scheduled within logicalProcessors by Windows.
bool buildMyth4AffinityPlan(
    int threadCount,
    ThreadAffinityPlan& plan,
    std::string& error);

void printThreadAffinityPlan(const ThreadAffinityPlan& plan);
void printMyth4AffinityPlan(const ThreadAffinityPlan& plan);

// Restricts the process default CPU Sets and the calling thread to the given
// logical processors. The previous assignments are saved for restoration.
bool applyProcessCpuSetRestriction(
    const std::vector<ThreadAffinityTarget>& logicalProcessors,
    ProcessCpuSetState& state,
    std::string& error);

bool restoreProcessCpuSetRestriction(
    ProcessCpuSetState& state,
    std::string& error);

bool bindCurrentThread(
    const ThreadAffinityTarget& target,
    ThreadAffinityState& state,
    std::string& error);

bool restoreCurrentThreadAffinity(
    ThreadAffinityState& state,
    std::string& error);

#endif  // THREAD_AFFINITY_H_
