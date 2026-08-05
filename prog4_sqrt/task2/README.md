# Program 4, Task 2 — Maximizing ISPC Speedup

Numeric measurements are available in [results.md](results.md) and
[results.csv](results.csv). Complete validated output for both distributions is
preserved in [`raw/`](raw/).

## English write-up

### Input construction

I added an explicit `--input best` mode and filled all 20 million array
elements with

```cpp
std::nextafter(3.0f, 0.0f)
```

which is `2.99999976` when printed with nine significant digits. This is the
largest representable `float` strictly below 3.0. Values closer to 3 require
many Newton iterations from the initial guess 1.0, so this choice makes the
iterative computation dominate fixed loop, memory, and task-scheduling costs.
Every element is identical, so all eight AVX2 lanes in each full ISPC gang take
the same control-flow path and remain active for the same number of iterations.
The only partially active lanes are the fixed tail gangs at task boundaries,
not input-dependent divergence. The 64 ISPC tasks also receive exactly the
same amount of work.

I did not use 3.0 itself because the implementation does not converge there.
With `x = 3` and `guess = 1`, the first update produces zero; subsequent
updates remain zero while the error remains one.

The starter random distribution remains the default and is available
explicitly through `--input random`. This preserves the Task 1 experiment while
making both Task 2 cases reproducible from the same executable.

### Method and results

I used the native Windows x64 Release executable on an Intel Core i5-13500HX
with `--simulate-myth4`. The serial and non-task ISPC implementations were
pinned to one hardware context. The task implementation was restricted to four
physical P-cores and their eight SMT contexts, with ConCRT configured for eight
concurrency resources. Each reported time is the minimum of three internal
trials. Both cases were run once by the Task 2 benchmark script and passed the
existing result check.

| Input | Serial (ms) | ISPC (ms) | Task ISPC (ms) | SIMD speedup | Multi-core speedup | Total speedup |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Random | 677.752 | 174.201 | 46.828 | 3.89x | 3.72x | 14.47x |
| Uniform best | 3329.612 | 695.005 | 169.671 | 4.79x | 4.10x | 19.62x |

For each row, SIMD speedup is `Tserial / Tispc`, incremental multi-core
speedup is `Tispc / Ttask`, and total speedup is `Tserial / Ttask`. Therefore,
the requested best-input results are **4.79x without tasks** and **19.62x with
tasks**. Moving from non-task ISPC to task ISPC contributes an additional
**4.10x**.

The input improves SIMD speedup from 3.89x to 4.79x, a 1.23x improvement in
the speedup ratio. It also improves incremental multi-core speedup from 3.72x
to 4.10x, a 1.10x improvement. Together these raise total speedup by 1.36x,
from 14.47x to 19.62x.

### Analysis

The best-input times are longer than the random-input times because the input
deliberately requires more work. Task 2 asks for the largest *relative
speedup*, not the smallest absolute runtime. The serial implementation pays
for every iteration of every element independently. ISPC executes eight
same-length iterations together, avoiding the inactive lanes caused by the
random distribution's different convergence counts. The long, uniform loop
also makes fixed overhead a smaller fraction of runtime. These effects explain
the improved SIMD ratio.

At task granularity, every contiguous span contains the same value, so all 64
tasks have equal cost. The longer computation amortizes task dispatch and gives
the scheduler enough uniform work to keep all selected contexts busy. SMT does
not create additional physical cores: sibling contexts share a core's
execution resources. It can nevertheless interleave independent instruction
streams while one Newton dependency chain is waiting, which helps utilization.
On this simulated topology the resulting multi-core gain rises modestly to
4.10x rather than approaching an ideal 8x.

Removing divergence alone does not guarantee the theoretical AVX2 speedup of
8x. The Newton recurrence has loop-control and data-dependency overhead, and
vector arithmetic does not necessarily have eight times the scalar throughput
for every instruction. Loads, stores, masking, cache behavior, and dynamic CPU
frequency also remain. The measured 4.79x is therefore consistent with fully
uniform lanes but non-ideal hardware throughput.

These measurements simulate the `myth` 4P/8SMT topology on a local
i5-13500HX; they are not results from a Stanford `myth` host.

## 中文分析

### 输入构造

程序新增了显式的 `--input best` 模式，将 2000 万个数组元素全部设为：

```cpp
std::nextafter(3.0f, 0.0f)
```

该值以 9 位有效数字输出时为 `2.99999976`，是严格小于 3.0 的最大可表示
`float`。初始猜测为 1.0 时，越接近 3 的合法输入通常需要越多次 Newton 迭代，
因此这一选择会提高迭代计算在总时间中的占比，摊薄循环、访存和 task 调度等固定
开销。所有元素完全相同，所以同一个 ISPC gang 中的 8 个 AVX2 lanes 具有相同的
控制流和迭代次数，不会因提前收敛而产生 inactive lanes。只有 task 边界处固定的
尾部 gang 可能没有填满全部 lanes，这不是由输入引起的 divergence；64 个 ISPC
tasks 的工作量仍完全一致。

不能直接使用 3.0。对于 `x = 3` 和初始 `guess = 1`，第一次更新会得到 0，之后
guess 始终为 0，而误差始终为 1，算法无法收敛。

starter code 的随机分布仍为默认模式，也可通过 `--input random` 显式选择。因此
Task 1 的实验仍然可以复现，Task 2 的两种输入也能由同一个可执行文件生成。

### 实验方法与结果

实验在 Intel Core i5-13500HX 上使用 Windows x64 原生 Release 可执行文件，并
传入 `--simulate-myth4`。串行与普通 ISPC 实现固定在一个硬件上下文；task ISPC
被限制在 4 个物理 P 核及其 8 个 SMT 上下文内，ConCRT 并发资源数设为 8。每项
时间都是程序内部三次 trial 的最小值。Task 2 基准脚本分别正式运行两种输入一次，
两次都通过了原有结果校验。

| 输入 | Serial（ms） | ISPC（ms） | Task ISPC（ms） | SIMD 加速 | 增量多核加速 | 总加速 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 随机输入 | 677.752 | 174.201 | 46.828 | 3.89x | 3.72x | 14.47x |
| 统一最佳输入 | 3329.612 | 695.005 | 169.671 | 4.79x | 4.10x | 19.62x |

表中 SIMD 加速为 `Tserial / Tispc`，增量多核加速为 `Tispc / Ttask`，总加速为
`Tserial / Ttask`。因此题目要求的最佳输入结果是：**不使用 tasks 时为 4.79x**，
**使用 tasks 时为 19.62x**；从普通 ISPC 切换到 task ISPC 又带来 **4.10x**
增量加速。

最佳输入将 SIMD 加速从 3.89x 提高到 4.79x，即 SIMD 加速比本身提高了
1.23x；增量多核加速从 3.72x 提高到 4.10x，即提高了 1.10x。两者共同使总加速
提高 1.36x，从 14.47x 上升到 19.62x。

### 结果分析

最佳输入的绝对耗时比随机输入更长，因为它刻意增加了计算量。Task 2 要求最大化
的是相对串行实现的**加速比**，而不是最小化绝对运行时间。串行版本必须逐元素执行
全部迭代；ISPC 则能让 8 个迭代次数相同的元素同时执行，消除了随机输入因收敛次数
不同而产生的 inactive lanes。较长而统一的循环也降低了固定开销在总时间中的比例，
因此 SIMD 加速得到提升。

在 task 层面，每个连续 span 中都是相同的值，所以 64 个 tasks 的计算量完全一致。
更长的计算摊薄了 task 分发成本，并让调度器有足够且均衡的工作填满全部选中上下文。
SMT 不会产生新的物理核心，同一核上的两个 contexts 仍共享执行资源；但一个 Newton
依赖链等待时，核心可以交错执行另一个独立指令流，从而提高资源利用率。本机模拟结果
中的增量多核加速因此小幅提高到 4.10x，而不是理想的 8x。

即使完全消除了 divergence，也不能保证达到 AVX2 理论上的 8 倍加速。Newton 递推
仍有循环控制和数据依赖，不是每一种向量指令都能获得相对标量指令 8 倍的实际吞吐量；
读写内存、掩码处理、缓存行为和动态频率同样存在。因此，统一 lanes 下实测 4.79x
仍符合非理想硬件上的执行特征。

这些数据来自 i5-13500HX 上对 `myth` 4P/8SMT 拓扑的本地模拟，并非 Stanford
`myth` 实机结果。

## Reproduce / 复现

Build the native Windows x64 Release executable, then run from WSL:

```bash
python3 task2/benchmark_task2.py \
  --executable /mnt/c/path/to/build/Release/sqrt.exe
```

Or run the same dependency-free script from a Windows terminal with Python:

```powershell
py task2\benchmark_task2.py `
  --executable build\Release\sqrt.exe
```

The script updates each raw log as its case runs, then replaces `results.csv`
and `results.md` after both cases pass validation.
