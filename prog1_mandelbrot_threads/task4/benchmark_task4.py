#!/usr/bin/env python3
"""Benchmark Program 1 Task 4 and generate dependency-free reports."""

from __future__ import annotations

import argparse
import csv
import math
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


THREAD_COUNTS = tuple(range(2, 9))
VIEWS = (1, 2)
IMAGE_HEIGHT = 1200
SERIAL_TIME_PATTERN = re.compile(
    r"\[mandelbrot serial\]:\s*\[([0-9]+(?:\.[0-9]+)?)\]\s*ms"
)
THREADED_TIME_PATTERN = re.compile(
    r"\[mandelbrot thread\]:\s*\[([0-9]+(?:\.[0-9]+)?)\]\s*ms"
)
SPEEDUP_PATTERN = re.compile(
    r"\(([0-9]+(?:\.[0-9]+)?)x speedup from ([0-9]+) threads\)"
)
PROFILE_TRIAL_PATTERN = re.compile(
    r"\[worker timing trial\]:\s*\[([0-9]+(?:\.[0-9]+)?)\]\s*ms"
)
WORKER_TIMING_PATTERN = re.compile(
    r"\[worker ([0-9]+)\]: interleaved rows start ([0-9]+), "
    r"stride ([0-9]+), count ([0-9]+), "
    r"\[([0-9]+(?:\.[0-9]+)?)\] ms"
)
CONTEXT_PATTERN = re.compile(
    r"Context ([0-7]) -> CPU Set ([0-9]+), Group ([0-9]+), "
    r"Logical CPU ([0-9]+), CoreIndex ([0-9]+), EfficiencyClass ([0-9]+)"
)
AFFINITY_WORKER_PATTERN = re.compile(
    r"Worker ([0-9]+) -> CPU Set ([0-9]+), Group ([0-9]+), "
    r"Logical CPU ([0-9]+), CoreIndex ([0-9]+)"
)
SERIAL_TARGET_PATTERN = re.compile(
    r"Serial reference -> CPU Set ([0-9]+), Group ([0-9]+), "
    r"Logical CPU ([0-9]+), CoreIndex ([0-9]+)"
)


@dataclass(frozen=True)
class WorkerTiming:
    worker: int
    start_row: int
    stride: int
    row_count: int
    elapsed_ms: float


@dataclass(frozen=True)
class CaseResult:
    view: int
    threads: int
    serial_ms: float
    threaded_ms: float
    workers: tuple[WorkerTiming, ...]

    @property
    def speedup(self) -> float:
        return self.serial_ms / self.threaded_ms

    @property
    def minimum_worker_ms(self) -> float:
        return min(worker.elapsed_ms for worker in self.workers)

    @property
    def maximum_worker_ms(self) -> float:
        return max(worker.elapsed_ms for worker in self.workers)

    @property
    def imbalance_ratio(self) -> float:
        return self.maximum_worker_ms / self.minimum_worker_ms


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Benchmark Mandelbrot Views 1 and 2 with 2-8 threads using the "
            "static interleaved-row decomposition and myth4 affinity."
        )
    )
    parser.add_argument(
        "--executable",
        required=True,
        type=Path,
        help="Path to the native Windows Release mandelbrot executable.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).resolve().parent,
        help="Output directory (default: directory containing this script).",
    )
    return parser.parse_args()


def single_float(pattern: re.Pattern[str], output: str, label: str) -> float:
    matches = pattern.findall(output)
    if len(matches) != 1:
        raise RuntimeError(
            f"Expected exactly one {label} in program output, found {len(matches)}."
        )
    return float(matches[0])


def validate_affinity(output: str, threads: int) -> None:
    if "Selected myth4 physical P-cores: 4" not in output:
        raise RuntimeError("Program did not report exactly four selected P-cores.")
    if "Selected myth4 SMT contexts: 8" not in output:
        raise RuntimeError("Program did not report exactly eight SMT contexts.")

    contexts = sorted(
        (
            int(context),
            int(cpu_set),
            int(core_index),
            int(efficiency_class),
        )
        for context, cpu_set, _group, _logical, core_index, efficiency_class
        in CONTEXT_PATTERN.findall(output)
    )
    if len(contexts) != 8 or [item[0] for item in contexts] != list(range(8)):
        raise RuntimeError("Affinity output did not describe contexts 0 through 7.")
    if len({item[1] for item in contexts}) != 8:
        raise RuntimeError("The myth4 context list contains duplicate CPU Set IDs.")
    core_indices = [item[2] for item in contexts]
    if len(set(core_indices)) != 4:
        raise RuntimeError("The myth4 context list does not use exactly four cores.")
    if any(core_indices.count(core) != 2 for core in set(core_indices)):
        raise RuntimeError("Each selected P-core must contribute two SMT contexts.")
    if len({item[3] for item in contexts}) != 1:
        raise RuntimeError("Selected contexts do not share one P-core efficiency class.")

    serial_targets = SERIAL_TARGET_PATTERN.findall(output)
    if len(serial_targets) != 1 or int(serial_targets[0][0]) != contexts[0][1]:
        raise RuntimeError("Serial reference was not assigned to myth4 context 0.")

    workers = sorted(
        (int(worker), int(cpu_set))
        for worker, cpu_set, _group, _logical, _core
        in AFFINITY_WORKER_PATTERN.findall(output)
    )
    if len(workers) != threads:
        raise RuntimeError(
            f"Expected {threads} deterministic worker mappings, found {len(workers)}."
        )
    for worker, cpu_set in workers:
        if worker >= threads or cpu_set != contexts[worker][1]:
            raise RuntimeError("Worker affinity does not follow myth4 context order.")


def validate_worker_rows(workers: tuple[WorkerTiming, ...], threads: int) -> None:
    if len(workers) != threads or [item.worker for item in workers] != list(
        range(threads)
    ):
        raise RuntimeError(f"Expected ordered timings for {threads} workers.")

    assigned_rows: list[int] = []
    for worker in workers:
        expected_rows = list(range(worker.worker, IMAGE_HEIGHT, threads))
        if worker.start_row != worker.worker or worker.stride != threads:
            raise RuntimeError("Worker does not use the required cyclic row mapping.")
        if worker.row_count != len(expected_rows):
            raise RuntimeError("Worker reported an incorrect interleaved row count.")
        if any(row % threads != worker.worker for row in expected_rows):
            raise RuntimeError("An interleaved row has the wrong worker residue.")
        if worker.elapsed_ms <= 0.0:
            raise RuntimeError("Worker elapsed times must be positive.")
        assigned_rows.extend(expected_rows)

    if sorted(assigned_rows) != list(range(IMAGE_HEIGHT)):
        raise RuntimeError("Interleaved decomposition has missing or duplicate rows.")


def parse_case(output: str, view: int, threads: int) -> CaseResult:
    if "[row decomposition]:\t\t[interleaved]" not in output:
        raise RuntimeError("Program did not report the interleaved decomposition.")

    serial_ms = single_float(SERIAL_TIME_PATTERN, output, "serial timing")
    threaded_ms = single_float(THREADED_TIME_PATTERN, output, "threaded timing")
    profiled_trial_ms = single_float(
        PROFILE_TRIAL_PATTERN, output, "profiled trial timing"
    )
    if not math.isclose(threaded_ms, profiled_trial_ms, abs_tol=0.0005):
        raise RuntimeError("Worker timings are not from the fastest threaded trial.")

    speedup_matches = SPEEDUP_PATTERN.findall(output)
    if len(speedup_matches) != 1 or int(speedup_matches[0][1]) != threads:
        raise RuntimeError("Program did not reach the verified speedup report.")

    workers = tuple(
        WorkerTiming(
            int(worker), int(start), int(stride), int(count), float(elapsed)
        )
        for worker, start, stride, count, elapsed
        in WORKER_TIMING_PATTERN.findall(output)
    )
    validate_worker_rows(workers, threads)
    if serial_ms <= 0.0 or threaded_ms <= 0.0:
        raise RuntimeError("Measured total times must be positive.")
    if any(worker.elapsed_ms > threaded_ms + 0.001 for worker in workers):
        raise RuntimeError("A worker time exceeds its enclosing threaded trial.")
    return CaseResult(view, threads, serial_ms, threaded_ms, workers)


def run_case(
    executable: Path, view: int, threads: int, raw_dir: Path
) -> CaseResult:
    command = [
        str(executable),
        "-t",
        str(threads),
        "--simulate-myth4",
        "--decomposition",
        "interleaved",
        "--profile-workers",
        "-v",
        str(view),
    ]
    with tempfile.TemporaryDirectory(
        prefix=f"mandelbrot-task4-v{view}-t{threads}-"
    ) as work:
        completed = subprocess.run(
            command,
            cwd=work,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    output = completed.stdout
    raw_path = raw_dir / f"view{view}_threads_{threads}.txt"
    raw_path.write_text(output, encoding="utf-8", newline="\n")
    if completed.returncode != 0:
        raise RuntimeError(
            f"View {view}, {threads} threads exited with code "
            f"{completed.returncode}; see raw/{raw_path.name}."
        )
    validate_affinity(output, threads)
    return parse_case(output, view, threads)


def write_results_csv(
    results: Iterable[CaseResult], destination: Path
) -> None:
    with destination.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(
            [
                "view",
                "threads",
                "serial_ms",
                "threaded_ms",
                "speedup",
                "min_worker_ms",
                "max_worker_ms",
                "imbalance_ratio",
            ]
        )
        for result in results:
            writer.writerow(
                [
                    result.view,
                    result.threads,
                    f"{result.serial_ms:.3f}",
                    f"{result.threaded_ms:.3f}",
                    f"{result.speedup:.4f}",
                    f"{result.minimum_worker_ms:.3f}",
                    f"{result.maximum_worker_ms:.3f}",
                    f"{result.imbalance_ratio:.4f}",
                ]
            )


def write_worker_csv(results: Iterable[CaseResult], destination: Path) -> None:
    with destination.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(
            [
                "view",
                "worker",
                "start_row",
                "stride",
                "row_count",
                "worker_ms",
            ]
        )
        for result in results:
            if result.threads != 8:
                continue
            for worker in result.workers:
                writer.writerow(
                    [
                        result.view,
                        worker.worker,
                        worker.start_row,
                        worker.stride,
                        worker.row_count,
                        f"{worker.elapsed_ms:.3f}",
                    ]
                )


def write_markdown(results: list[CaseResult], destination: Path) -> None:
    lines = [
        "# Program 1 Task 4 Results",
        "",
        "Native Windows Release build with `--simulate-myth4`, "
        "`--decomposition interleaved`, and `--profile-workers`.",
        "Each row uses the minimum of five internal serial and threaded trials; "
        "worker times come from the fastest threaded trial.",
    ]
    for view in VIEWS:
        lines.extend(
            [
                "",
                f"## View {view}",
                "",
                "| Threads | Serial (ms) | Threaded (ms) | Speedup | "
                "Min worker (ms) | Max worker (ms) | Imbalance |",
                "| ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
            ]
        )
        for result in results:
            if result.view == view:
                lines.append(
                    f"| {result.threads} | {result.serial_ms:.3f} | "
                    f"{result.threaded_ms:.3f} | {result.speedup:.2f}x | "
                    f"{result.minimum_worker_ms:.3f} | "
                    f"{result.maximum_worker_ms:.3f} | "
                    f"{result.imbalance_ratio:.2f}x |"
                )

    lines.extend(
        [
            "",
            "## Final 8-thread result",
            "",
            "| View | Speedup | Worker imbalance |",
            "| ---: | ---: | ---: |",
        ]
    )
    for result in results:
        if result.threads == 8:
            lines.append(
                f"| {result.view} | {result.speedup:.2f}x | "
                f"{result.imbalance_ratio:.2f}x |"
            )
    lines.append("")
    destination.write_text("\n".join(lines), encoding="utf-8", newline="\n")


def write_svg(results: list[CaseResult], destination: Path) -> None:
    width, height = 940, 620
    left, right, top, bottom = 90, 40, 75, 85
    plot_width = width - left - right
    plot_height = height - top - bottom
    y_max = 8.5

    def x_position(threads: int) -> float:
        return left + (threads - 2) * plot_width / 6

    def y_position(speedup: float) -> float:
        return top + (y_max - speedup) * plot_height / y_max

    colors = {1: "#1769aa", 2: "#d96b20"}
    elements = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
            f'height="{height}" viewBox="0 0 {width} {height}" '
            'role="img" aria-labelledby="title description">'
        ),
        '<title id="title">Task 4 speedup for Mandelbrot Views 1 and 2</title>',
        (
            '<desc id="description">Measured static interleaved-row speedup '
            'for two through eight threads on both views, compared with ideal '
            'linear speedup.</desc>'
        ),
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        (
            '<text x="470" y="38" text-anchor="middle" '
            'font-family="sans-serif" font-size="23" font-weight="700">'
            'Program 1 Task 4 — Interleaved-Row Speedup</text>'
        ),
    ]
    for tick in range(0, 9):
        y = y_position(float(tick))
        elements.extend(
            [
                f'<line x1="{left}" y1="{y:.2f}" x2="{width - right}" '
                f'y2="{y:.2f}" stroke="#d9dee7"/>',
                f'<text x="{left - 12}" y="{y + 5:.2f}" text-anchor="end" '
                f'font-family="sans-serif" font-size="14">{tick}</text>',
            ]
        )
    for threads in THREAD_COUNTS:
        x = x_position(threads)
        elements.extend(
            [
                f'<line x1="{x:.2f}" y1="{top}" x2="{x:.2f}" '
                f'y2="{height - bottom}" stroke="#edf0f5"/>',
                f'<text x="{x:.2f}" y="{height - bottom + 28}" '
                f'text-anchor="middle" font-family="sans-serif" '
                f'font-size="14">{threads}</text>',
            ]
        )
    ideal_points = " ".join(
        f"{x_position(threads):.2f},{y_position(float(threads)):.2f}"
        for threads in THREAD_COUNTS
    )
    elements.extend(
        [
            f'<line x1="{left}" y1="{top}" x2="{left}" '
            f'y2="{height - bottom}" stroke="#1f2937" stroke-width="2"/>',
            f'<line x1="{left}" y1="{height - bottom}" '
            f'x2="{width - right}" y2="{height - bottom}" '
            'stroke="#1f2937" stroke-width="2"/>',
            f'<polyline points="{ideal_points}" fill="none" stroke="#8b95a5" '
            'stroke-width="3" stroke-dasharray="9 7"/>',
        ]
    )
    for view in VIEWS:
        view_results = [result for result in results if result.view == view]
        points = " ".join(
            f"{x_position(result.threads):.2f},{y_position(result.speedup):.2f}"
            for result in view_results
        )
        elements.append(
            f'<polyline points="{points}" fill="none" stroke="{colors[view]}" '
            'stroke-width="4" stroke-linejoin="round"/>'
        )
        for result in view_results:
            x = x_position(result.threads)
            y = y_position(result.speedup)
            elements.append(
                f'<circle cx="{x:.2f}" cy="{y:.2f}" r="5" '
                f'fill="{colors[view]}" stroke="#ffffff" stroke-width="2"/>'
            )
    elements.extend(
        [
            f'<text x="{left + plot_width / 2:.2f}" y="{height - 22}" '
            'text-anchor="middle" font-family="sans-serif" font-size="17">'
            'Number of threads</text>',
            f'<text x="25" y="{top + plot_height / 2:.2f}" '
            'text-anchor="middle" font-family="sans-serif" font-size="17" '
            f'transform="rotate(-90 25 {top + plot_height / 2:.2f})">'
            'Speedup over serial</text>',
            '<line x1="610" y1="54" x2="650" y2="54" '
            'stroke="#1769aa" stroke-width="4"/>',
            '<text x="658" y="59" font-family="sans-serif" '
            'font-size="14">View 1</text>',
            '<line x1="715" y1="54" x2="755" y2="54" '
            'stroke="#d96b20" stroke-width="4"/>',
            '<text x="763" y="59" font-family="sans-serif" '
            'font-size="14">View 2</text>',
            '<line x1="610" y1="76" x2="650" y2="76" '
            'stroke="#8b95a5" stroke-width="3" stroke-dasharray="9 7"/>',
            '<text x="658" y="81" font-family="sans-serif" '
            'font-size="14">Ideal linear</text>',
            '</svg>',
            '',
        ]
    )
    destination.write_text("\n".join(elements), encoding="utf-8", newline="\n")


def write_readme(results: list[CaseResult], destination: Path) -> None:
    final = {result.view: result for result in results if result.threads == 8}
    reached_target = all(result.speedup >= 7.0 for result in final.values())
    english_target = (
        "Both local 8-thread measurements exceed the assignment's 7x target."
        if reached_target
        else (
            "At least one local 8-thread measurement is below the assignment's "
            "7x target. These are retained as measured rather than presented as "
            "Stanford myth results."
        )
    )
    chinese_target = (
        "两个视图的本机 8 线程结果都超过了题目要求的 7x。"
        if reached_target
        else (
            "至少一个视图的本机 8 线程结果低于题目给出的 7x 目标；报告保留真实数据，"
            "不将其冒充为 Stanford myth 实机结果。"
        )
    )
    lines = [
        "# Program 1, Task 4 — Static Interleaved Rows",
        "",
        "![Views 1 and 2 speedup for 2–8 threads](speedup_both_views.svg)",
        "",
        "Numeric results are in [results.md](results.md) and "
        "[results.csv](results.csv). The 8-thread worker data are in "
        "[worker_times_8.csv](worker_times_8.csv), and complete output is "
        "preserved in [`raw/`](raw/).",
        "",
        "## English write-up",
        "",
        "### Approach",
        "",
        "I replaced the default contiguous block assignment with one static cyclic "
        "rule. For `N` threads, worker `i` computes rows `i`, `i + N`, `i + 2N`, "
        "and so on. The same rule works for every thread count and both views; it "
        "contains no special cases based on the image or thread count. Every row "
        "has exactly one residue modulo `N`, so workers write disjoint output rows "
        "and require no locks, atomics, barriers, or work queues.",
        "",
        "This assignment spreads each worker's rows throughout the image. Expensive "
        "Mandelbrot regions that were concentrated in the middle block in Tasks 2 "
        "and 3 are therefore shared across all workers. The old policy remains "
        "available as `--decomposition block` solely to reproduce those experiments; "
        "the program now defaults to `interleaved`.",
        "",
        "### Method and results",
        "",
        "I used a native Windows Release build on an Intel Core i5-13500HX with "
        "`--simulate-myth4 --decomposition interleaved --profile-workers`. The "
        "process was restricted to four P-cores and their eight SMT contexts, and "
        "the serial reference was pinned to the first selected P-core. Each serial "
        "and threaded value is the minimum of five internal trials.",
        "",
        f"At eight threads, View 1 reached **{final[1].speedup:.2f}x** speedup "
        f"with a {final[1].imbalance_ratio:.2f}x maximum-to-minimum worker-time "
        f"ratio. View 2 reached **{final[2].speedup:.2f}x** speedup with a "
        f"{final[2].imbalance_ratio:.2f}x worker-time ratio. {english_target}",
        "",
        "The much smaller worker-time spread compared with the 13.00x ratio from "
        "Task 3 View 1 confirms that cyclic rows fix the dominant block imbalance. "
        "Remaining differences from ideal linear speedup come from SMT siblings "
        "sharing execution resources on four physical cores, thread overhead, cache "
        "effects, and dynamic clock frequency. This i5-13500HX experiment simulates "
        "the requested 4P/8SMT topology; it is not a Stanford `myth` measurement.",
        "",
        "## 中文分析",
        "",
        "### 实现方法",
        "",
        "默认连续块划分被替换为一条静态循环规则：使用 `N` 个线程时，worker `i` "
        "负责第 `i`、`i + N`、`i + 2N`……行。同一规则适用于所有线程数和两个视图，"
        "没有针对图像或线程数硬编码。每一行对 `N` 的余数唯一，因此每个 worker 写入"
        "互不重叠的输出行，不需要锁、原子操作、屏障或动态任务队列。",
        "",
        "这种划分让每个线程的行分散在整个图像中，将 Task 2/3 中集中于中央连续块的"
        "高计算量区域平均分给所有线程。旧策略仍可通过 `--decomposition block` 复现，"
        "但程序默认使用 `interleaved`。",
        "",
        "### 实验与结果",
        "",
        "实验使用 Intel Core i5-13500HX 的原生 Windows Release 版本，参数为 "
        "`--simulate-myth4 --decomposition interleaved --profile-workers`。进程被"
        "限制在 4 个 P 核及其 8 个 SMT 上下文中，串行参考固定在第一个选中 P 核；"
        "串行和并行时间均取程序内部五次 trial 的最小值。",
        "",
        f"8 线程时，View 1 的加速比为 **{final[1].speedup:.2f}x**，worker 最大/"
        f"最小耗时比为 {final[1].imbalance_ratio:.2f}x；View 2 的加速比为 "
        f"**{final[2].speedup:.2f}x**，耗时比为 {final[2].imbalance_ratio:.2f}x。"
        f"{chinese_target}",
        "",
        "与 Task 3 View 1 的 13.00x 失衡比相比，现在的 worker 耗时差距显著缩小，"
        "证明循环行划分解决了主要的连续块负载不均衡。与理想线性加速的剩余差距来自"
        "四个物理核上的 SMT 资源共享、线程开销、缓存行为和动态频率。本实验只是在 "
        "i5-13500HX 上模拟指定的 4P/8SMT 拓扑，并非 Stanford `myth` 实机数据。",
        "",
        "## Reproduce / 复现",
        "",
        "```bash",
        "python3 task4/benchmark_task4.py \\",
        "  --executable /mnt/c/path/to/build/Release/mandelbrot.exe",
        "```",
        "",
        "```powershell",
        "py task4\\benchmark_task4.py `",
        "  --executable build\\Release\\mandelbrot.exe",
        "```",
        "",
    ]
    destination.write_text("\n".join(lines), encoding="utf-8", newline="\n")


def validate_generated_files(output_dir: Path) -> None:
    with (output_dir / "results.csv").open(
        "r", encoding="utf-8", newline=""
    ) as stream:
        rows = list(csv.DictReader(stream))
    if len(rows) != len(VIEWS) * len(THREAD_COUNTS):
        raise RuntimeError("results.csv does not contain exactly 14 data rows.")
    for row in rows:
        serial_ms = float(row["serial_ms"])
        threaded_ms = float(row["threaded_ms"])
        speedup = float(row["speedup"])
        if serial_ms <= 0.0 or threaded_ms <= 0.0:
            raise RuntimeError("results.csv contains a non-positive time.")
        if not math.isclose(
            speedup, serial_ms / threaded_ms, rel_tol=0.0, abs_tol=0.0001
        ):
            raise RuntimeError("results.csv contains an inconsistent speedup.")

    with (output_dir / "worker_times_8.csv").open(
        "r", encoding="utf-8", newline=""
    ) as stream:
        worker_rows = list(csv.DictReader(stream))
    if len(worker_rows) != 16:
        raise RuntimeError("worker_times_8.csv does not contain exactly 16 rows.")
    for view in VIEWS:
        if sum(int(row["view"]) == view for row in worker_rows) != 8:
            raise RuntimeError(f"View {view} does not have exactly eight workers.")

    ET.parse(output_dir / "speedup_both_views.svg")


def main() -> int:
    args = parse_args()
    executable = args.executable.expanduser().resolve()
    output_dir = args.output_dir.expanduser().resolve()
    if not executable.is_file():
        print(f"error: executable not found: {executable}", file=sys.stderr)
        return 2

    output_dir.mkdir(parents=True, exist_ok=True)
    raw_dir = output_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    results: list[CaseResult] = []
    try:
        for view in VIEWS:
            for threads in THREAD_COUNTS:
                print(
                    f"Running View {view} with {threads} threads...", flush=True
                )
                result = run_case(executable, view, threads, raw_dir)
                results.append(result)
                print(
                    f"  {result.threaded_ms:.3f} ms, "
                    f"{result.speedup:.2f}x, "
                    f"worker ratio {result.imbalance_ratio:.2f}x",
                    flush=True,
                )

        write_results_csv(results, output_dir / "results.csv")
        write_worker_csv(results, output_dir / "worker_times_8.csv")
        write_markdown(results, output_dir / "results.md")
        write_svg(results, output_dir / "speedup_both_views.svg")
        write_readme(results, output_dir / "README.md")
        validate_generated_files(output_dir)
    except (OSError, RuntimeError, ValueError, ET.ParseError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    print(f"Task 4 artifacts written to {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
