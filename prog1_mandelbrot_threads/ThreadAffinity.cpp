#include "ThreadAffinity.h"

#include <algorithm>
#include <cstdio>
#include <limits>
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

}  // namespace
#endif

bool buildPerformanceCorePlan(
    int threadCount,
    ThreadAffinityPlan& plan,
    std::string& error) {
    plan.targets.clear();
    plan.detectedPerformanceCoreCount = 0;
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
    ULONG requiredLength = 0;
    if (GetSystemCpuSetInformation(
            nullptr, 0, &requiredLength, GetCurrentProcess(), 0) ||
        GetLastError() != ERROR_INSUFFICIENT_BUFFER || requiredLength == 0) {
        const DWORD code = GetLastError();
        error = windowsErrorMessage("GetSystemCpuSetInformation(size query)", code);
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
        error = windowsErrorMessage("GetSystemCpuSetInformation", GetLastError());
        return false;
    }

    std::vector<ThreadAffinityTarget> available;
    std::uint8_t minimumEfficiency = std::numeric_limits<std::uint8_t>::max();
    std::uint8_t maximumEfficiency = 0;

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
    return true;
#endif
}

void printThreadAffinityPlan(const ThreadAffinityPlan& plan) {
    std::printf("Detected physical performance cores: %zu\n",
                plan.detectedPerformanceCoreCount);
    std::printf("Selected performance cores: %zu\n", plan.targets.size());
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
