# Program 5, Task 1 — SAXPY Task Parallelism

## English write-up

### Method

I built Program 5 as a native Windows x64 Release executable with MSVC and
ISPC's `avx2-i32x8` target. I ran it on an Intel Core i5-13500HX with
`--simulate-myth4`, which restricted the process to four physical P-cores and
their eight SMT contexts. The non-task ISPC reference was pinned to one of
those hardware contexts. The task implementation kept the starter code's 64
ISPC tasks, which the Windows Concurrency Runtime scheduled within all eight
selected contexts.

The reported time for each implementation is the minimum of the three trials
performed internally by the program during the same validated execution. This
is a local topology simulation, not a measurement from a Stanford `myth`
machine.

### Results

| Implementation | Execution resources | Time (ms) | Bandwidth (GB/s) | Throughput (GFLOPS) |
| --- | --- | ---: | ---: | ---: |
| ISPC, no tasks | 1 hardware context, AVX2 SIMD | 10.112 | 29.472 | 3.956 |
| ISPC with tasks | 4 P-cores / 8 SMT contexts, AVX2 SIMD | 5.634 | 52.894 | 7.099 |

The speedup from using ISPC tasks is

```text
Tispc / Ttask = 10.112 / 5.634 = 1.7948x, or 1.79x.
```

The bandwidth and floating-point throughput increase by essentially the same
factor, which is expected because both versions perform the same arithmetic
and memory operations on the same number of elements.

### Performance analysis

SAXPY is trivially parallel, but it has very low arithmetic intensity. For
each element it performs only one multiplication and one addition, while it
loads `X[i]` and `Y[i]` and stores `result[i]`. With an ordinary write-allocate
cache, a cold output cache line also causes a read-for-ownership before the
modified line is eventually written back. Amortized per element, the traffic
model used by the program is therefore approximately

```text
4 bytes (X load) + 4 bytes (Y load) +
4 bytes (write allocation) + 4 bytes (writeback) = 16 bytes.
```

Its arithmetic intensity is consequently only

```text
2 FLOPs / 16 bytes = 0.125 FLOP/byte.
```

There is too little computation to hide the cost of moving the three large
vectors through the memory hierarchy. The single-context ISPC version already
uses 29.472 GB/s. Adding cores raises the observed bandwidth to 52.894 GB/s,
but all four physical cores share caches, memory controllers, and DRAM
bandwidth. They therefore contend for a resource that is already heavily used
instead of supplying four independent streams of memory bandwidth. The two SMT
contexts on each P-core also share that core's execution and cache resources;
SMT does not multiply memory bandwidth. Task scheduling and synchronization add
some overhead as well, although they are not the dominant limitation for this
large input.

### Can it be substantially improved?

**No, not while preserving the same SAXPY interface and memory semantics.** A
different task decomposition or a more aggressively unrolled arithmetic loop
cannot overcome the shared memory-bandwidth limit, so a near-linear 4x speedup
on four physical cores is not realistic for this workload once bandwidth
saturates.

Non-temporal stores could avoid the read-for-ownership traffic when the output
will not be read immediately. In the ideal traffic model this reduces four
float-sized transfers per element to three, so it may provide a useful but
bounded improvement rather than a 4x scaling result. A much larger gain would
require changing the surrounding workload—for example, fusing SAXPY with its
consumer so the intermediate result is not written to and reread from memory,
or arranging for the vectors to remain in cache. Those approaches reduce data
movement, but they are no longer just a rewrite of the same standalone SAXPY
operation.

## 中文分析

### 实验方法

本实验使用 MSVC 和 ISPC 的 `avx2-i32x8` 目标，将 Program 5 构建为 Windows
x64 原生 Release 可执行文件。程序运行在 Intel Core i5-13500HX 上，并传入
`--simulate-myth4`，将进程限制在 4 个物理 P 核及其 8 个 SMT 上下文内。不使用
tasks 的 ISPC 参考实现固定在其中一个硬件上下文；task 实现保持 starter code
原有的 64 个 ISPC tasks，由 Windows Concurrency Runtime 在所选的 8 个上下文
中调度。

每项时间均取程序在同一次已验证运行中内部执行三次所得的最小值。本实验只是本机
拓扑模拟，并非 Stanford `myth` 机器上的实测结果。

### 实验结果

| 实现 | 执行资源 | 时间（ms） | 带宽（GB/s） | 吞吐量（GFLOPS） |
| --- | --- | ---: | ---: | ---: |
| ISPC，不使用 tasks | 1 个硬件上下文，AVX2 SIMD | 10.112 | 29.472 | 3.956 |
| ISPC，使用 tasks | 4 个 P 核 / 8 个 SMT 上下文，AVX2 SIMD | 5.634 | 52.894 | 7.099 |

使用 ISPC tasks 带来的加速比为：

```text
Tispc / Ttask = 10.112 / 5.634 = 1.7948x，约为 1.79x。
```

内存带宽和浮点吞吐量几乎以相同比例提升，这是合理的，因为两个版本对相同数量的
元素执行完全相同的算术和内存操作。

### 性能分析

SAXPY 虽然很容易并行，但它的算术强度很低。每处理一个元素只进行一次乘法和一次
加法，同时需要读取 `X[i]`、`Y[i]` 并写入 `result[i]`。对于普通的
write-allocate cache，首次写入输出 cache line 前还会发生 read-for-ownership，
修改后的 cache line 最终也需要写回。按每个元素平均计算，程序采用的流量模型约为：

```text
4 字节（读取 X）+ 4 字节（读取 Y）+
4 字节（write allocation）+ 4 字节（写回）= 16 字节。
```

因此算术强度只有：

```text
2 FLOPs / 16 字节 = 0.125 FLOP/byte。
```

计算量太少，无法掩盖三个大向量在内存层次中移动的成本。单硬件上下文 ISPC 版本
已经使用了 29.472 GB/s；增加核心后，实测带宽提升到 52.894 GB/s，但 4 个物理
核心共享 cache、内存控制器和 DRAM 带宽。它们是在竞争一个已经被大量使用的共享
资源，而不是各自获得一份独立的内存带宽。同一 P 核上的两个 SMT 上下文也共享该
核心的执行和 cache 资源，SMT 不会成倍增加内存带宽。task 调度和同步还会产生
一定开销，但对于如此大的输入，它们不是最主要的限制。

### 能否大幅改进？

**不能，前提是保持相同的 SAXPY 接口和内存语义。** 改变 task 划分或进一步展开
算术循环都无法突破共享内存带宽上限，因此在带宽饱和后，4 个物理核心实现接近
4 倍的线性加速并不现实。

如果输出不会马上被读取，可以使用 non-temporal stores 避免
read-for-ownership。理想情况下，这会把每个元素的 4 次 float 大小的数据传输
降为 3 次，因而可能获得有用但有限的提升，而不是 4 倍加速。若要取得更大的收益，
必须改变外围工作负载，例如把 SAXPY 与后续消费者融合，避免中间结果写入内存后
又被读回，或者让向量保持在 cache 中。这些方法减少了数据移动，但已经不再只是对
同一个独立 SAXPY 操作的等价重写。
