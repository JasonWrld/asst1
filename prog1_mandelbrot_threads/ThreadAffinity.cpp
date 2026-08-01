#include "ThreadAffinity.h"

#include <algorithm>
#include <cstdio>
#include <limits>
#include <map>
#include <set>
#include <sstream>
#include <utility>
#include <vector>

#ifdef _WIN32
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>

namespace {

std::string windowsErrorMessage(const char* operation, DWORD errorCode) {
    char* message = nullptr;
    const DWORD flags = FORMAT_MESSAGE_ALLOCATE_BUFFER |
                        FORMAT_MESSAGE_FROM_SYSTEM |
                        FORMAT_MESSAGE_IGNORE_INSERTS;
    const DWORD length = FormatMessageA(
        flags,
        nullptr,
        errorCode,
        0,
        reinterpret_cast<char*>(&message),
        0,
        nullptr);

    std::ostringstream output;
    output << operation << " failed with Windows error " << errorCode;
    if (length != 0 && message != nullptr) {
        std::string text(message, length);
        while (!text.empty() &&
               (text.back() == '\r' || text.back() == '\n' || text.back() == ' ')) {
            text.pop_back();
        }
        output << ": " << text;
    }
    if (message != nullptr) {
        LocalFree(message);
    }
    return output.str();
}

bool isUsableForCurrentProcess(const SYSTEM_CPU_SET_INFORMATION& information) {
    return !information.CpuSet.Allocated ||
           information.CpuSet.AllocatedToTargetProcess;
}

bool queryUsableCpuSets(
    std::vector<ThreadAffinityTarget>& available,
    std::uint8_t& minimumEfficiency,
    std::uint8_t& maximumEfficiency,
    std::string& error) {
    available.clear();
    minimumEfficiency = std::numeric_limits<std::uint8_t>::max();
    maximumEfficiency = 0;

    ULONG requiredLength = 0;
    if (GetSystemCpuSetInformation(
            nullptr, 0, &requiredLength, GetCurrentProcess(), 0) ||
        GetLastError() != ERROR_INSUFFICIENT_BUFFER || requiredLength == 0) {
        error = windowsErrorMessage(
            "GetSystemCpuSetInformation(size query)", GetLastError());
        return false;
    }

    std::vector<unsigned char> buffer(requiredLength);
    ULONG returnedLength = 0;
    auto* first = reinterpret_cast<PSYSTEM_CPU_SET_INFORMATION>(buffer.data());
    if (!GetSystemCpuSetInformation(
            first,
            requiredLength,
            &returnedLength,
            GetCurrentProcess(),
            0)) {
        error = windowsErrorMessage(
            "GetSystemCpuSetInformation", GetLastError());
        return false;
    }

    ULONG offset = 0;
    while (offset < returnedLength) {
        auto* information = reinterpret_cast<PSYSTEM_CPU_SET_INFORMATION>(
            buffer.data() + offset);
        if (information->Size == 0 || information->Size > returnedLength - offset) {
            error = "Windows returned malformed CPU Set information.";
            return false;
        }

        if (information->Type == CpuSetInformation &&
            isUsableForCurrentProcess(*information)) {
            ThreadAffinityTarget target{};
            target.cpuSetId = information->CpuSet.Id;
            target.group = information->CpuSet.Group;
            target.logicalProcessorIndex = information->CpuSet.LogicalProcessorIndex;
            target.coreIndex = information->CpuSet.CoreIndex;
            target.efficiencyClass = information->CpuSet.EfficiencyClass;
            available.push_back(target);
            minimumEfficiency = std::min(minimumEfficiency, target.efficiencyClass);
            maximumEfficiency = std::max(maximumEfficiency, target.efficiencyClass);
        }
        offset += information->Size;
    }

    if (available.empty()) {
        error = "Windows did not report any usable CPU Sets.";
        return false;
    }
    return true;
}

bool queryProcessDefaultCpuSetIds(
    std::vector<std::uint32_t>& ids,
    std::string& error) {
    ids.clear();
    ULONG requiredCount = 0;
    if (!GetProcessDefaultCpuSets(
            GetCurrentProcess(), nullptr, 0, &requiredCount)) {
        if (GetLastError() != ERROR_INSUFFICIENT_BUFFER) {
            error = windowsErrorMessage(
                "GetProcessDefaultCpuSets(size query)", GetLastError());
            return false;
        }
    }
    if (requiredCount == 0) {
        return true;
    }

    std::vector<ULONG> windowsIds(requiredCount);
    if (!GetProcessDefaultCpuSets(
            GetCurrentProcess(),
            windowsIds.data(),
            static_cast<ULONG>(windowsIds.size()),
            &requiredCount)) {
        error = windowsErrorMessage("GetProcessDefaultCpuSets", GetLastError());
        return false;
    }
    windowsIds.resize(requiredCount);
    ids.assign(windowsIds.begin(), windowsIds.end());
    return true;
}

bool queryCurrentThreadCpuSetIds(
    std::vector<std::uint32_t>& ids,
    std::string& error) {
    ids.clear();
    ULONG requiredCount = 0;
    if (!GetThreadSelectedCpuSets(
            GetCurrentThread(), nullptr, 0, &requiredCount)) {
        if (GetLastError() != ERROR_INSUFFICIENT_BUFFER) {
            error = windowsErrorMessage(
                "GetThreadSelectedCpuSets(size query)", GetLastError());
            return false;
        }
    }
    if (requiredCount == 0) {
        return true;
    }

    std::vector<ULONG> windowsIds(requiredCount);
    if (!GetThreadSelectedCpuSets(
            GetCurrentThread(),
            windowsIds.data(),
            static_cast<ULONG>(windowsIds.size()),
            &requiredCount)) {
        error = windowsErrorMessage("GetThreadSelectedCpuSets", GetLastError());
        return false;
    }
    windowsIds.resize(requiredCount);
    ids.assign(windowsIds.begin(), windowsIds.end());
    return true;
}

std::vector<ULONG> toWindowsCpuSetIds(
    const std::vector<std::uint32_t>& ids) {
    return std::vector<ULONG>(ids.begin(), ids.end());
}

bool setProcessDefaultCpuSetIds(
    const std::vector<std::uint32_t>& ids,
    std::string& error) {
    const std::vector<ULONG> windowsIds = toWindowsCpuSetIds(ids);
    if (!SetProcessDefaultCpuSets(
            GetCurrentProcess(),
            windowsIds.empty() ? nullptr : windowsIds.data(),
            static_cast<ULONG>(windowsIds.size()))) {
        error = windowsErrorMessage("SetProcessDefaultCpuSets", GetLastError());
        return false;
    }
    return true;
}

bool setCurrentThreadCpuSetIds(
    const std::vector<std::uint32_t>& ids,
    std::string& error) {
    const std::vector<ULONG> windowsIds = toWindowsCpuSetIds(ids);
    if (!SetThreadSelectedCpuSets(
            GetCurrentThread(),
            windowsIds.empty() ? nullptr : windowsIds.data(),
            static_cast<ULONG>(windowsIds.size()))) {
        error = windowsErrorMessage("SetThreadSelectedCpuSets", GetLastError());
        return false;
    }
    return true;
}

bool cpuSetIdsEqual(
    std::vector<std::uint32_t> left,
    std::vector<std::uint32_t> right) {
    std::sort(left.begin(), left.end());
    std::sort(right.begin(), right.end());
    return left == right;
}

}  // namespace
#endif

bool buildPerformanceCorePlan(
    int threadCount,
    ThreadAffinityPlan& plan,
    std::string& error) {
    plan = ThreadAffinityPlan{};
    error.clear();

    if (threadCount < 1) {
        error = "Thread count must be positive.";
        return false;
    }

#ifndef _WIN32
    error = "--pin-p-cores is supported only by the native Windows build; "
            "WSL exposes virtual CPUs rather than reliable host P-core topology.";
    return false;
#else
    std::vector<ThreadAffinityTarget> available;
    std::uint8_t minimumEfficiency = 0;
    std::uint8_t maximumEfficiency = 0;
    if (!queryUsableCpuSets(
            available, minimumEfficiency, maximumEfficiency, error)) {
        return false;
    }
    if (minimumEfficiency == maximumEfficiency) {
        error = "Windows did not expose distinct efficiency classes, so P-cores "
                "cannot be identified safely.";
        return false;
    }

    std::sort(
        available.begin(),
        available.end(),
        [](const ThreadAffinityTarget& left, const ThreadAffinityTarget& right) {
            if (left.group != right.group) {
                return left.group < right.group;
            }
            if (left.coreIndex != right.coreIndex) {
                return left.coreIndex < right.coreIndex;
            }
            return left.logicalProcessorIndex < right.logicalProcessorIndex;
        });

    std::set<std::pair<std::uint16_t, std::uint8_t>> selectedPhysicalCores;
    for (const ThreadAffinityTarget& target : available) {
        if (target.efficiencyClass != maximumEfficiency) {
            continue;
        }
        const auto physicalCore = std::make_pair(target.group, target.coreIndex);
        if (selectedPhysicalCores.insert(physicalCore).second) {
            plan.targets.push_back(target);
        }
    }

    const std::size_t detectedPerformanceCores = plan.targets.size();
    plan.detectedPerformanceCoreCount = detectedPerformanceCores;
    if (static_cast<std::size_t>(threadCount) > detectedPerformanceCores) {
        std::ostringstream output;
        output << "Requested " << threadCount
               << " P-core-bound threads, but Windows exposed only "
               << detectedPerformanceCores << " physical P-cores.";
        error = output.str();
        plan.targets.clear();
        return false;
    }

    plan.targets.resize(static_cast<std::size_t>(threadCount));
    plan.logicalProcessors = plan.targets;
    plan.selectedPhysicalCoreCount = plan.targets.size();
    return true;
#endif
}

bool buildMyth4AffinityPlan(
    int threadCount,
    ThreadAffinityPlan& plan,
    std::string& error) {
    plan = ThreadAffinityPlan{};
    error.clear();

    if (threadCount < 1) {
        error = "Thread count must be positive.";
        return false;
    }

#ifndef _WIN32
    error = "--simulate-myth4 is supported only by the native Windows build; "
            "WSL exposes virtual CPUs rather than reliable host P-core topology.";
    return false;
#else
    static constexpr std::size_t MYTH_PHYSICAL_CORES = 4;
    static constexpr std::size_t SMT_CONTEXTS_PER_CORE = 2;

    std::vector<ThreadAffinityTarget> available;
    std::uint8_t minimumEfficiency = 0;
    std::uint8_t maximumEfficiency = 0;
    if (!queryUsableCpuSets(
            available, minimumEfficiency, maximumEfficiency, error)) {
        return false;
    }
    if (minimumEfficiency == maximumEfficiency) {
        error = "Windows did not expose distinct efficiency classes, so P-cores "
                "cannot be identified safely.";
        return false;
    }

    using PhysicalCore = std::pair<std::uint16_t, std::uint8_t>;
    std::map<PhysicalCore, std::vector<ThreadAffinityTarget>> performanceCores;
    for (const ThreadAffinityTarget& target : available) {
        if (target.efficiencyClass == maximumEfficiency) {
            performanceCores[std::make_pair(target.group, target.coreIndex)]
                .push_back(target);
        }
    }

    plan.detectedPerformanceCoreCount = performanceCores.size();
    if (performanceCores.size() < MYTH_PHYSICAL_CORES) {
        std::ostringstream output;
        output << "Myth4 simulation requires four physical P-cores, but Windows "
               << "exposed only " << performanceCores.size() << ".";
        error = output.str();
        return false;
    }

    std::vector<std::vector<ThreadAffinityTarget>> selectedCores;
    selectedCores.reserve(MYTH_PHYSICAL_CORES);
    for (auto& entry : performanceCores) {
        std::vector<ThreadAffinityTarget>& contexts = entry.second;
        std::sort(
            contexts.begin(),
            contexts.end(),
            [](const ThreadAffinityTarget& left,
               const ThreadAffinityTarget& right) {
                return left.logicalProcessorIndex < right.logicalProcessorIndex;
            });
        if (contexts.size() < SMT_CONTEXTS_PER_CORE) {
            std::ostringstream output;
            output << "P-core Group " << entry.first.first
                   << ", CoreIndex " << static_cast<unsigned int>(entry.first.second)
                   << " exposes only " << contexts.size()
                   << " logical processor(s); myth4 requires two SMT contexts "
                      "per selected core.";
            error = output.str();
            return false;
        }
        selectedCores.push_back(contexts);
        if (selectedCores.size() == MYTH_PHYSICAL_CORES) {
            break;
        }
    }

    // Put the first SMT context from each core first, followed by the second
    // context from each core. This spreads workers 0-3 across physical cores.
    for (std::size_t sibling = 0; sibling < SMT_CONTEXTS_PER_CORE; ++sibling) {
        for (const std::vector<ThreadAffinityTarget>& core : selectedCores) {
            plan.logicalProcessors.push_back(core[sibling]);
        }
    }

    plan.selectedPhysicalCoreCount = MYTH_PHYSICAL_CORES;
    const std::size_t workerCount = static_cast<std::size_t>(threadCount);
    if (workerCount <= plan.logicalProcessors.size()) {
        plan.targets.assign(
            plan.logicalProcessors.begin(),
            plan.logicalProcessors.begin() + workerCount);
    } else {
        plan.workersUseSystemScheduling = true;
    }
    return true;
#endif
}

void printThreadAffinityPlan(const ThreadAffinityPlan& plan) {
    std::printf("Detected physical performance cores: %zu\n",
                plan.detectedPerformanceCoreCount);
    std::printf("Selected performance cores: %zu\n",
                plan.selectedPhysicalCoreCount);
    for (std::size_t i = 0; i < plan.targets.size(); ++i) {
        const ThreadAffinityTarget& target = plan.targets[i];
        std::printf(
            "  Worker %zu -> CPU Set %u, Group %u, Logical CPU %u, "
            "CoreIndex %u, EfficiencyClass %u\n",
            i,
            static_cast<unsigned int>(target.cpuSetId),
            static_cast<unsigned int>(target.group),
            static_cast<unsigned int>(target.logicalProcessorIndex),
            static_cast<unsigned int>(target.coreIndex),
            static_cast<unsigned int>(target.efficiencyClass));
    }
}

void printMyth4AffinityPlan(const ThreadAffinityPlan& plan) {
    std::printf("Detected physical performance cores: %zu\n",
                plan.detectedPerformanceCoreCount);
    std::printf("Selected myth4 physical P-cores: %zu\n",
                plan.selectedPhysicalCoreCount);
    std::printf("Selected myth4 SMT contexts: %zu\n",
                plan.logicalProcessors.size());
    for (std::size_t i = 0; i < plan.logicalProcessors.size(); ++i) {
        const ThreadAffinityTarget& target = plan.logicalProcessors[i];
        std::printf(
            "  Context %zu -> CPU Set %u, Group %u, Logical CPU %u, "
            "CoreIndex %u, EfficiencyClass %u\n",
            i,
            static_cast<unsigned int>(target.cpuSetId),
            static_cast<unsigned int>(target.group),
            static_cast<unsigned int>(target.logicalProcessorIndex),
            static_cast<unsigned int>(target.coreIndex),
            static_cast<unsigned int>(target.efficiencyClass));
    }
    if (!plan.logicalProcessors.empty()) {
        const ThreadAffinityTarget& serial = plan.logicalProcessors.front();
        std::printf(
            "  Serial reference -> CPU Set %u, Group %u, Logical CPU %u, "
            "CoreIndex %u\n",
            static_cast<unsigned int>(serial.cpuSetId),
            static_cast<unsigned int>(serial.group),
            static_cast<unsigned int>(serial.logicalProcessorIndex),
            static_cast<unsigned int>(serial.coreIndex));
    }
    if (plan.workersUseSystemScheduling) {
        std::printf(
            "  Workers -> Windows scheduling within the 8 myth4 SMT contexts\n");
    } else {
        for (std::size_t i = 0; i < plan.targets.size(); ++i) {
            const ThreadAffinityTarget& target = plan.targets[i];
            std::printf(
                "  Worker %zu -> CPU Set %u, Group %u, Logical CPU %u, "
                "CoreIndex %u\n",
                i,
                static_cast<unsigned int>(target.cpuSetId),
                static_cast<unsigned int>(target.group),
                static_cast<unsigned int>(target.logicalProcessorIndex),
                static_cast<unsigned int>(target.coreIndex));
        }
    }
}

bool applyProcessCpuSetRestriction(
    const std::vector<ThreadAffinityTarget>& logicalProcessors,
    ProcessCpuSetState& state,
    std::string& error) {
    state = ProcessCpuSetState{};
    error.clear();

    if (logicalProcessors.empty()) {
        error = "Process CPU Set restriction requires at least one target.";
        return false;
    }

#ifndef _WIN32
    (void)logicalProcessors;
    error = "Process CPU Set restriction is unavailable on this platform.";
    return false;
#else
    std::vector<std::uint32_t> requestedIds;
    requestedIds.reserve(logicalProcessors.size());
    for (const ThreadAffinityTarget& target : logicalProcessors) {
        requestedIds.push_back(target.cpuSetId);
    }
    std::set<std::uint32_t> uniqueIds(requestedIds.begin(), requestedIds.end());
    if (uniqueIds.size() != requestedIds.size()) {
        error = "Process CPU Set restriction contains duplicate CPU Set IDs.";
        return false;
    }

    if (!queryProcessDefaultCpuSetIds(
            state.previousProcessCpuSetIds, error) ||
        !queryCurrentThreadCpuSetIds(
            state.previousThreadCpuSetIds, error)) {
        state = ProcessCpuSetState{};
        return false;
    }

    if (!setProcessDefaultCpuSetIds(requestedIds, error)) {
        state = ProcessCpuSetState{};
        return false;
    }
    state.processAssignmentActive = true;

    if (!setCurrentThreadCpuSetIds(requestedIds, error)) {
        const std::string assignmentError = error;
        std::string rollbackError;
        if (!restoreProcessCpuSetRestriction(state, rollbackError)) {
            error = assignmentError + "; rollback also failed: " + rollbackError;
        } else {
            error = assignmentError;
        }
        return false;
    }
    state.threadAssignmentActive = true;

    std::vector<std::uint32_t> actualProcessIds;
    std::vector<std::uint32_t> actualThreadIds;
    if (!queryProcessDefaultCpuSetIds(actualProcessIds, error) ||
        !queryCurrentThreadCpuSetIds(actualThreadIds, error) ||
        !cpuSetIdsEqual(actualProcessIds, requestedIds) ||
        !cpuSetIdsEqual(actualThreadIds, requestedIds)) {
        const std::string verificationError = error.empty()
            ? "Windows did not apply the requested myth4 CPU Sets."
            : error;
        std::string rollbackError;
        if (!restoreProcessCpuSetRestriction(state, rollbackError)) {
            error = verificationError + "; rollback also failed: " + rollbackError;
        } else {
            error = verificationError;
        }
        return false;
    }
    return true;
#endif
}

bool restoreProcessCpuSetRestriction(
    ProcessCpuSetState& state,
    std::string& error) {
    error.clear();
    if (!state.threadAssignmentActive && !state.processAssignmentActive) {
        return true;
    }

#ifndef _WIN32
    error = "Process CPU Set restoration is unavailable on this platform.";
    return false;
#else
    bool succeeded = true;
    if (state.threadAssignmentActive) {
        std::string threadError;
        if (setCurrentThreadCpuSetIds(
                state.previousThreadCpuSetIds, threadError)) {
            std::vector<std::uint32_t> actualThreadIds;
            if (queryCurrentThreadCpuSetIds(actualThreadIds, threadError) &&
                cpuSetIdsEqual(
                    actualThreadIds, state.previousThreadCpuSetIds)) {
                state.threadAssignmentActive = false;
            } else {
                if (threadError.empty()) {
                    threadError =
                        "Windows did not restore the calling thread CPU Sets.";
                }
                error = threadError;
                succeeded = false;
            }
        } else {
            error = threadError;
            succeeded = false;
        }
    }
    if (state.processAssignmentActive) {
        std::string processError;
        if (setProcessDefaultCpuSetIds(
                state.previousProcessCpuSetIds, processError)) {
            std::vector<std::uint32_t> actualProcessIds;
            if (queryProcessDefaultCpuSetIds(actualProcessIds, processError) &&
                cpuSetIdsEqual(
                    actualProcessIds, state.previousProcessCpuSetIds)) {
                state.processAssignmentActive = false;
            } else {
                if (processError.empty()) {
                    processError =
                        "Windows did not restore the process default CPU Sets.";
                }
                if (!error.empty()) {
                    error += "; ";
                }
                error += processError;
                succeeded = false;
            }
        } else {
            if (!error.empty()) {
                error += "; ";
            }
            error += processError;
            succeeded = false;
        }
    }
    if (succeeded) {
        state.previousProcessCpuSetIds.clear();
        state.previousThreadCpuSetIds.clear();
    }
    return succeeded;
#endif
}

bool bindCurrentThread(
    const ThreadAffinityTarget& target,
    ThreadAffinityState& state,
    std::string& error) {
    error.clear();
    state = ThreadAffinityState{};

#ifndef _WIN32
    (void)target;
    error = "Thread affinity binding is unavailable on this platform.";
    return false;
#else
    constexpr unsigned int maskBits = sizeof(KAFFINITY) * 8U;
    if (target.logicalProcessorIndex >= maskBits) {
        error = "Logical processor index cannot be represented by GROUP_AFFINITY.";
        return false;
    }

    GROUP_AFFINITY requested{};
    requested.Group = target.group;
    requested.Mask = static_cast<KAFFINITY>(1) << target.logicalProcessorIndex;

    GROUP_AFFINITY previous{};
    if (!SetThreadGroupAffinity(GetCurrentThread(), &requested, &previous)) {
        error = windowsErrorMessage("SetThreadGroupAffinity", GetLastError());
        return false;
    }

    state.previousGroup = previous.Group;
    state.previousMask = static_cast<std::uint64_t>(previous.Mask);
    state.active = true;

    GROUP_AFFINITY actual{};
    if (!GetThreadGroupAffinity(GetCurrentThread(), &actual)) {
        error = windowsErrorMessage("GetThreadGroupAffinity", GetLastError());
        GROUP_AFFINITY ignored{};
        SetThreadGroupAffinity(GetCurrentThread(), &previous, &ignored);
        state.active = false;
        return false;
    }
    if (actual.Group != requested.Group || actual.Mask != requested.Mask) {
        error = "Windows did not apply the requested one-processor affinity mask.";
        GROUP_AFFINITY ignored{};
        SetThreadGroupAffinity(GetCurrentThread(), &previous, &ignored);
        state.active = false;
        return false;
    }
    return true;
#endif
}

bool restoreCurrentThreadAffinity(
    ThreadAffinityState& state,
    std::string& error) {
    error.clear();
    if (!state.active) {
        return true;
    }

#ifndef _WIN32
    error = "Thread affinity restoration is unavailable on this platform.";
    return false;
#else
    GROUP_AFFINITY previous{};
    previous.Group = state.previousGroup;
    previous.Mask = static_cast<KAFFINITY>(state.previousMask);
    GROUP_AFFINITY ignored{};
    if (!SetThreadGroupAffinity(GetCurrentThread(), &previous, &ignored)) {
        error = windowsErrorMessage("SetThreadGroupAffinity(restore)", GetLastError());
        return false;
    }
    state.active = false;
    return true;
#endif
}
