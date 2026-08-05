# Program 4, Task 3 — Minimizing ISPC Speedup

Numeric measurements are available in [results.md](results.md) and
[results.csv](results.csv). Complete validated output for both distributions is
preserved in [`raw/`](raw/).

## English write-up

### Input construction

I added `--input worst` and repeated the following pattern across all 20
million elements:

```text
[heavy, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0]
```

Here `heavy` is `std::nextafter(3.0f, 0.0f)`, the largest representable
`float` below 3.0, printed as `2.99999976`. It requires many Newton iterations
from the initial guess 1.0. In contrast, an input of 1.0 already satisfies the
termination condition and performs no loop iterations. The complete array
therefore contains exactly 2,500,000 heavy values and 17,500,000 light values.

ISPC's AVX2 target uses gangs of eight program instances. In every full gang,
the seven light lanes immediately become inactive while the one heavy lane
continues through the varying loop. The vector instructions must keep
executing until that lane converges, so useful lane utilization in the loop
body is approximately one eighth. The serial implementation only executes the
expensive loop for one out of every eight elements. This construction removes
the normal advantage of performing eight useful Newton updates per vector
instruction while retaining ISPC's mask and loop-control overhead.

The random distribution remains the default, and Task 2's uniform best input
remains available through `--input best`.

### Method and results

I used the native Windows x64 Release executable on an Intel Core i5-13500HX
with `--simulate-myth4`. Serial and non-task ISPC were pinned to one hardware
context. The task implementation was restricted to four physical P-cores and
their eight SMT contexts, and ConCRT was configured for eight concurrency
resources. Each value is the minimum of three internal trials. The Task 3
benchmark ran random and worst once each and validated the topology, input
metadata, reported speedups, and numerical results.

| Input | Serial (ms) | ISPC (ms) | Task ISPC (ms) | SIMD speedup | Multi-core speedup | Total speedup |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Random | 720.994 | 182.825 | 47.516 | 3.94x | 3.85x | 15.17x |
| Worst | 568.940 | 678.372 | 165.115 | 0.84x | 4.11x | 3.45x |

SIMD speedup is `Tserial / Tispc`, incremental multi-core speedup is
`Tispc / Ttask`, and total speedup is `Tserial / Ttask`. For the constructed
input, the requested non-task ISPC result is therefore **0.84x**: the ISPC
implementation is slower than serial. Its 678.372 ms time is 1.19x the serial
time. With tasks, the same ISPC computation reaches **3.45x total speedup**;
moving from non-task ISPC to task ISPC contributes **4.11x**.

Relative to the paired random measurement, the worst input retains only 0.21x
of the SIMD speedup and 0.23x of the total speedup. The incremental multi-core
factor instead rises slightly from 3.85x to 4.11x.

### Analysis

The loss is caused by SIMD control-flow divergence, not by insufficient total
work. During almost all Newton iterations, seven of the eight lanes are
inactive. Hardware still issues a 256-bit vector operation under the active
mask, but only the heavy lane produces useful progress. Mask updates, loop
tests, branches, loads, stores, and vector execution overhead remain. As a
result, the vectorized implementation takes 19% longer than scalar execution
instead of approaching the ideal eight-wide speedup.

The task result behaves differently because the heavy elements are uniformly
spaced. Each task processes a span of 312,500 elements. Since this span is four
modulo eight, alternating tasks contain 39,063 and 39,062 heavy values—a
difference of only one. All 64 tasks therefore have essentially identical
cost, so task-level load balance remains excellent even though SIMD efficiency
inside every gang is poor. Four physical P-cores reduce the 678.372 ms
non-task ISPC time to 165.115 ms, producing the measured 4.11x incremental
multi-core gain. The eight SMT contexts share four cores and should not be
interpreted as eight independent physical processors.

These measurements simulate the `myth` 4P/8SMT topology on a local
i5-13500HX; they are not results from a Stanford `myth` host.

## 中文分析

### 输入构造

程序新增了 `--input worst`，并在全部 2000 万个元素中重复以下模式：

```text
[heavy, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0]
```

其中 `heavy` 为 `std::nextafter(3.0f, 0.0f)`，即严格小于 3.0 的最大可表示
`float`，输出为 `2.99999976`。从初始猜测 1.0 开始时，该值需要执行很多次
Newton 迭代；输入 1.0 则一开始就满足终止条件，不进入循环。完整数组因此恰好包含
250 万个 heavy 和 1750 万个 light。

ISPC 的 AVX2 目标使用包含 8 个 program instances 的 gang。在每个完整 gang 中，
7 个 light lanes 会立即变为 inactive，唯一的 heavy lane 则继续执行 varying 循环。
向量指令必须一直执行到该 lane 收敛，所以循环体内有用的 lane 利用率约为八分之一。
串行实现则只为每 8 个元素中的 1 个执行高成本循环。这个构造消除了“一条向量指令
同时完成 8 个有效 Newton 更新”的正常优势，却保留了 ISPC 的 mask 和循环控制开销。

随机分布仍为默认输入，Task 2 的统一最佳输入仍可通过 `--input best` 使用。

### 实验方法与结果

实验在 Intel Core i5-13500HX 上使用 Windows x64 原生 Release 可执行文件，并
传入 `--simulate-myth4`。串行和普通 ISPC 固定在一个硬件上下文；task ISPC 被
限制在 4 个物理 P 核及其 8 个 SMT 上下文内，ConCRT 并发资源数设为 8。每项时间
是程序内部三次 trial 的最小值。Task 3 脚本分别正式运行 random 和 worst 一次，
并验证了拓扑、输入元数据、加速比和数值结果。

| 输入 | Serial（ms） | ISPC（ms） | Task ISPC（ms） | SIMD 加速 | 增量多核加速 | 总加速 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 随机输入 | 720.994 | 182.825 | 47.516 | 3.94x | 3.85x | 15.17x |
| 最差输入 | 568.940 | 678.372 | 165.115 | 0.84x | 4.11x | 3.45x |

SIMD 加速为 `Tserial / Tispc`，增量多核加速为 `Tispc / Ttask`，总加速为
`Tserial / Ttask`。因此该构造下题目要求的普通 ISPC 结果是 **0.84x**，即 ISPC
反而比串行更慢；其 678.372 ms 耗时是串行时间的 1.19 倍。使用 tasks 后，总加速
为 **3.45x**；从普通 ISPC 切换到 task ISPC 带来 **4.11x** 增量多核加速。

与同一轮随机输入相比，最差输入只保留了 0.21 倍的 SIMD 加速比和 0.23 倍的总
加速比；增量多核因子则从 3.85x 小幅提高到 4.11x。

### 结果分析

效率损失来自 SIMD 控制流 divergence，而不是总计算量不足。在绝大多数 Newton
迭代中，8 个 lanes 中有 7 个处于 inactive 状态。硬件仍然要在 active mask 下发射
256-bit 向量运算，但只有 heavy lane 产生有效进展；mask 更新、循环判断、分支、
读写内存和向量执行开销仍然存在。因此向量实现没有接近理论 8-wide 加速，反而比
标量执行慢了约 19%。

task 结果表现不同，因为 heavy 元素在数组中均匀分布。每个 task 处理 312500 个
元素；该长度除以 8 余 4，所以相邻 tasks 分别包含 39063 和 39062 个 heavy，差异
只有 1。64 个 tasks 的计算量因此几乎完全相同：虽然每个 gang 内部的 SIMD 效率
很差，task 层面的负载仍然均衡。4 个物理 P 核把普通 ISPC 的 678.372 ms 降到
165.115 ms，得到实测 4.11x 增量多核收益。8 个 SMT contexts 共享 4 个物理核，
不能被理解为 8 个相互独立的处理器。

这些数据来自 i5-13500HX 上对 `myth` 4P/8SMT 拓扑的本地模拟，并非 Stanford
`myth` 实机结果。

## Reproduce / 复现

Build the native Windows x64 Release executable, then run from WSL:

```bash
python3 task3/benchmark_task3.py \
  --executable /mnt/c/path/to/build/Release/sqrt.exe
```

Or run the dependency-free script from a Windows terminal with Python:

```powershell
py task3\benchmark_task3.py `
  --executable build\Release\sqrt.exe
```

The script updates each raw log as its case runs, then replaces `results.csv`
and `results.md` after both cases pass validation.
