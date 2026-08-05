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

struct Myth4AffinityPlan {
    std::vector<ThreadAffinityTarget> logicalProcessors;
    std::size_t detectedPerformanceCoreCount = 0;
    std::size_t selectedPhysicalCoreCount = 0;
};

struct ProcessCpuSetState {
    std::vector<std::uint32_t> previousProcessCpuSetIds;
    std::vector<std::uint32_t> previousThreadCpuSetIds;
    bool processAssignmentActive = false;
    bool threadAssignmentActive = false;
};

struct ThreadAffinityState {
    std::uint64_t previousMask = 0;
    std::uint16_t previousGroup = 0;
    bool active = false;
};

// Select exactly four Windows performance cores and both SMT contexts from
// each core. This is unavailable on non-Windows platforms because WSL does
// not expose the host's hybrid-core topology reliably.
bool buildMyth4AffinityPlan(
    Myth4AffinityPlan& plan,
    std::string& error);

void printMyth4AffinityPlan(const Myth4AffinityPlan& plan);

// Set the process default CPU Sets and the calling thread's selected CPU Sets.
// Threads subsequently created by the Windows task runtime inherit the process
// default assignment.
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

// Limit the default Windows Concurrency Runtime scheduler to the eight
// execution contexts exposed by the myth4 affinity plan. This must be called
// before the first ISPC task launch creates the default scheduler.
bool configureMyth4TaskRuntime(std::string& error);

#endif  // THREAD_AFFINITY_H_
