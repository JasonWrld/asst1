# Program 4, Task 1 — SIMD and Multi-Core Speedup

## English write-up

### Method

I built Program 4 as a native Windows x64 Release executable with MSVC and
ISPC's `avx2-i32x8` target. I ran the executable with `--simulate-myth4` on an
Intel Core i5-13500HX. This mode restricts the process to four physical
P-cores and their eight SMT contexts in order to approximate the CPU topology
available on a Stanford `myth` machine. These are local topology-simulation
results, not measurements collected on an actual `myth` host.

The serial implementation and the non-task ISPC implementation were both
pinned to the first selected hardware context. The non-task ISPC version
therefore used one CPU core and one hardware thread; its eight-wide AVX2 program
instances are SIMD lanes, not eight CPU threads. The task ISPC implementation
kept the starter code's 64-task decomposition, and ConCRT scheduled those tasks
within all eight selected SMT contexts. Each time below is the minimum of the
three trials performed internally by the program during the same validated
execution.

### Results

| Implementation | Execution resources | Time (ms) | Speedup vs. serial |
| --- | --- | ---: | ---: |
| Serial | 1 hardware context | 667.848 | 1.00x |
| ISPC, no tasks | 1 hardware context, AVX2 SIMD | 175.844 | 3.80x |
| ISPC with tasks | 4 P-cores / 8 SMT contexts, AVX2 SIMD | 45.927 | 14.54x |

The speedup attributable to SIMD parallelization is

```text
Tserial / Tispc = 667.848 / 175.844 = 3.80x.
```

To isolate the additional benefit of multi-core task parallelism, the task
version must be compared with the already-vectorized non-task version:

```text
Tispc / Ttask = 175.844 / 45.927 = 3.83x.
```

The combined SIMD and multi-core speedup relative to the serial implementation
is

```text
Tserial / Ttask = 667.848 / 45.927 = 14.54x.
```

The decomposition is consistent because `3.80 x 3.83` is approximately
`14.54`. Thus, **3.83x is the incremental multi-core speedup asked for in this
task**, whereas **14.54x is the end-to-end speedup from applying both SIMD and
task parallelism**.

### Analysis

The SIMD speedup is lower than AVX2's ideal eight-wide speedup. Each input value
can require a different number of Newton iterations. ISPC executes the varying
`while` loop under a lane mask, so lanes whose values have already converged
become inactive while the remaining lanes continue. The gang still runs until
its slowest lane finishes. Loop control, mask handling, loads and stores, and
other scalar or instruction overhead also remain, so eight SIMD lanes do not
translate into an 8x application speedup.

The task version adds 3.83x over the single-core ISPC implementation, which is
close to the scaling expected from four physical cores. Although eight logical
contexts are available, the two SMT contexts on each core share that core's
execution resources and are not equivalent to two independent physical cores.
SMT can improve utilization when one hardware thread is stalled or leaves
resources idle, but it does not double the machine's arithmetic throughput.
Task scheduling overhead, shared caches and memory bandwidth, and normal timing
variation also keep scaling from being ideal.

## 中文分析

### 实验方法

本实验使用 MSVC 和 ISPC 的 `avx2-i32x8` 目标，将 Program 4 构建为 Windows
x64 原生 Release 可执行文件。程序运行在 Intel Core i5-13500HX 上，并传入
`--simulate-myth4`，把进程限制在 4 个物理 P 核及其 8 个 SMT 上下文内，以近似
Stanford `myth` 机器的 CPU 拓扑。这里记录的是本机拓扑模拟结果，并非真实
`myth` 主机上的测量数据。

串行实现和不使用 task 的 ISPC 实现都固定在第一个硬件上下文。不使用 task 的
ISPC 版本因此只占用一个 CPU 核上的一个硬件线程；AVX2 的 8-wide program
instances 是 SIMD lanes，并不代表 8 个 CPU 线程。task ISPC 版本保持 starter
code 原有的 64-task 划分，由 ConCRT 在所选的全部 8 个 SMT 上下文内调度。下表
三个时间来自同一次已经验证的程序运行，且每项都是程序内部三次试验的最小值。

### 实验结果

| 实现 | 执行资源 | 时间（ms） | 相对串行加速比 |
| --- | --- | ---: | ---: |
| Serial | 1 个硬件上下文 | 667.848 | 1.00x |
| ISPC，不使用 tasks | 1 个硬件上下文，AVX2 SIMD | 175.844 | 3.80x |
| ISPC，使用 tasks | 4 个 P 核 / 8 个 SMT 上下文，AVX2 SIMD | 45.927 | 14.54x |

SIMD 并行带来的加速为：

```text
Tserial / Tispc = 667.848 / 175.844 = 3.80x。
```

为了单独计算多核 task 并行的额外收益，应当用已经向量化的普通 ISPC 版本与
task ISPC 版本比较：

```text
Tispc / Ttask = 175.844 / 45.927 = 3.83x。
```

SIMD 与多核并行相对串行实现的组合总加速为：

```text
Tserial / Ttask = 667.848 / 45.927 = 14.54x。
```

由于 `3.80 x 3.83` 约等于 `14.54`，这三项结果彼此一致。因此，**Task 1
所问的增量多核加速是 3.83x**；**14.54x 是同时应用 SIMD 与 task 并行后相对
串行实现的端到端总加速**，两者不能混用。

### 结果分析

SIMD 加速没有达到 AVX2 理论上的 8 倍。每个输入值需要的 Newton 迭代次数可能
不同。ISPC 使用 lane mask 执行这个具有 varying 条件的 `while` 循环：已经收敛
的 lanes 会变为 inactive，尚未收敛的 lanes 则继续执行，整个 gang 必须等待
最慢的 lane 完成。此外，循环控制、掩码处理、读写内存以及其他标量或指令开销
仍然存在，所以 8 个 SIMD lanes 不会直接转化为程序的 8 倍加速。

task 版本在单核 ISPC 版本之上又获得了 3.83 倍加速，接近 4 个物理核心能够提供
的扩展能力。虽然系统提供了 8 个逻辑上下文，但同一物理核上的两个 SMT 上下文
共享执行资源，不能视为两个独立的物理核心。SMT 可以在线程停顿或部分执行资源
空闲时提高利用率，却不会让机器的算术吞吐量直接翻倍。task 调度开销、共享缓存、
内存带宽以及正常的计时波动也会使实际扩展低于理想情况。
