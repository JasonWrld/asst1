# Program 3, Part 1 — ISPC SIMD Mandelbrot

The numeric measurements are in [results.md](results.md) and
[results.csv](results.csv). Complete output from all ten executable
invocations is preserved in [`raw/`](raw/).

## English write-up

### Method

I built the starter implementation with ISPC 1.28.1 using the assignment's
8-wide AVX2 target:

```text
-O3 --target=avx2-i32x8 --arch=x86-64 --opt=disable-fma --pic
```

The experiment ran under WSL2 on an Intel Core i5-13500HX. WSL exposed 20
logical CPUs; each child process was pinned to WSL logical CPU 0 so that the
serial and ISPC phases used the same scheduling context and did not migrate
between WSL CPUs. This does not guarantee a fixed host P-core because WSL runs
under the Microsoft hypervisor. These are local-machine measurements, not
results from a Stanford `myth` host.

I ran each view in five independent executable invocations. Within each
invocation, the starter program reports the minimum of three serial trials and
the minimum of three ISPC trials. The summary below takes the lowest reported
serial and ISPC time across the five invocations. Every invocation reached the
speedup report and exited successfully, which means the built-in element-wise
comparison between the serial and ISPC output passed.

### Results

| View | Serial (ms) | ISPC (ms) | Speedup |
| ---: | ---: | ---: | ---: |
| 1 | 146.511 | 40.851 | 3.59x |
| 2 | 82.003 | 28.567 | 2.87x |

The ideal speedup is approximately **8x**. The configured ISPC target executes
a gang of eight program instances with 8-wide AVX2 instructions on one core,
so in the ideal case each vector instruction performs the work of eight scalar
instructions. Part 1 does not launch ISPC tasks and therefore does not combine
this SIMD parallelism with multiple CPU cores.

The measured speedup is below 8x primarily because Mandelbrot has data-dependent
control flow. A pixel leaves the iteration loop as soon as its complex value
escapes, while pixels inside the set run all 256 iterations. When the eight
pixels in one ISPC gang need different iteration counts, completed lanes become
masked off, but the vector loop must continue until the slowest active lane
finishes. Instructions issued during those later iterations therefore use only
part of the vector width. Pixels far outside the set often escape together, and
pixels well inside it often remain active together; gangs crossing the fractal's
irregular boundary have the greatest divergence.

View 2 zooms into a detailed boundary region and reaches only 2.87x, compared
with 3.59x for View 1. The lower relative SIMD benefit is consistent with more
gangs containing neighboring pixels with substantially different escape times.
This comparison supports the control-flow-divergence hypothesis. Mask handling,
loop/index overhead, cache behavior, dynamic clock frequency, and wide-vector
frequency effects can contribute additional differences from the ideal.

### Reproduce

From `prog3_mandelbrot_ispc/`, download and unpack the compiler version named
in the assignment README, then build with its absolute path:

```bash
wget -O /tmp/ispc-v1.28.1-linux.tar.gz \
  https://github.com/ispc/ispc/releases/download/v1.28.1/ispc-v1.28.1-linux.tar.gz
tar -xzf /tmp/ispc-v1.28.1-linux.tar.gz -C /tmp
make clean
make ISPC=/tmp/ispc-v1.28.1-linux/bin/ispc
python3 part1/benchmark_part1.py \
  --executable ./mandelbrot_ispc --runs 5 --cpu 0
```

The recorded environment used Linux
`6.18.33.2-microsoft-standard-WSL2`, GCC 15.2.0, GNU Make 4.4.1, Python
3.13.13, and ISPC 1.28.1. The benchmark writes PPM images in temporary
directories, so only the raw text and tabular results are retained.

## 中文分析

### 实验方法

实验使用 ISPC 1.28.1，并保持作业提供的 8-wide AVX2 编译参数不变。运行环境为
Intel Core i5-13500HX 上的 WSL2；WSL 暴露 20 个逻辑 CPU。每个被测进程都固定到
WSL 逻辑 CPU 0，使同一进程内的串行阶段和 ISPC 阶段使用相同调度上下文，并避免在
WSL CPU 之间迁移。由于 WSL 位于 Microsoft hypervisor 之上，这并不保证进程始终
映射到某个固定的宿主 P 核。本报告记录的是本机数据，并非 Stanford `myth` 实机结果。

两个 view 各执行 5 次独立程序调用。每次调用内部又分别运行 3 次串行实现和 3 次
ISPC 实现，并报告各自最小值；汇总表再从 5 次调用中分别选取最低串行和 ISPC
时间。所有 10 次调用都正常到达 speedup 输出并以状态 0 退出，因此程序内置的串行与
ISPC 逐像素结果比较全部通过。

### 结果与解释

| View | 串行时间 (ms) | ISPC 时间 (ms) | 加速比 |
| ---: | ---: | ---: | ---: |
| 1 | 146.511 | 40.851 | 3.59x |
| 2 | 82.003 | 28.567 | 2.87x |

理论最大加速比约为 **8x**。当前 ISPC target 会在单个核心上使用 8-wide AVX2
指令同时执行一个 gang 中的 8 个 program instances；理想情况下，一条向量指令可以
完成 8 条标量指令的工作。Part 1 没有启动 ISPC tasks，因此这里不会叠加多核加速。

实际加速低于 8x 的主要原因是 Mandelbrot 循环存在数据相关的控制流分歧。像素一旦
逃逸就会退出循环，集合内部的像素则会执行满 256 次。当同一 gang 的 8 个像素需要
不同迭代次数时，已经完成的 lane 会被 mask 掉，但向量循环仍必须等待最慢 lane
结束；后续指令只能利用部分向量宽度。远离集合的像素通常一起快速逃逸，集合内部像素
通常一起保持活跃，而穿过不规则分形边界的 gang 最容易出现严重 divergence。

View 2 放大了细节丰富的边界区域，加速比只有 2.87x，低于 View 1 的 3.59x。这说明
View 2 中更多相邻像素具有明显不同的逃逸时间，与 SIMD 控制流 divergence 假设一致。
mask 管理、循环与索引开销、缓存行为、动态频率以及宽向量指令引起的频率变化也会造成
额外损失。完整的逐次数据和原始输出可用于复核表中结论。
