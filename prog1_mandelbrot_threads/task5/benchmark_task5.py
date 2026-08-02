#!/usr/bin/env python3
"""Compare Program 1 Task 5 performance with 8 and 16 workers."""

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


THREAD_COUNTS = (8, 16)
VIEWS = (1, 2)
IMAGE_HEIGHT = 1200
SYSTEM_SCHEDULING_MESSAGE = (
    "Workers -> Windows scheduling within the 8 myth4 SMT contexts"
)
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
            "Compare 8 and 16 interleaved-row workers on both Mandelbrot "
            "views while restricted to the same myth4 CPU contexts."
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
            f"Expected exactly one {label} in output, found {len(matches)}."
        )
    return float(matches[0])


def validate_affinity(output: str, threads: int) -> None:
    if "Selected myth4 physical P-cores: 4" not in output:
        raise RuntimeError("Program did not report four selected P-cores.")
    if "Selected myth4 SMT contexts: 8" not in output:
        raise RuntimeError("Program did not report eight selected SMT contexts.")

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
        raise RuntimeError("The myth4 context list contains duplicate CPU Sets.")
    core_indices = [item[2] for item in contexts]
    if len(set(core_indices)) != 4:
        raise RuntimeError("The myth4 context list does not use four cores.")
    if any(core_indices.count(core) != 2 for core in set(core_indices)):
        raise RuntimeError("Each selected P-core must contribute two contexts.")
    if len({item[3] for item in contexts}) != 1:
        raise RuntimeError("Selected contexts do not share one P-core class.")

    serial_targets = SERIAL_TARGET_PATTERN.findall(output)
    if len(serial_targets) != 1 or int(serial_targets[0][0]) != contexts[0][1]:
        raise RuntimeError("Serial reference was not assigned to context 0.")

    worker_mappings = sorted(
        (int(worker), int(cpu_set))
        for worker, cpu_set, _group, _logical, _core
        in AFFINITY_WORKER_PATTERN.findall(output)
    )
    if threads == 8:
        if SYSTEM_SCHEDULING_MESSAGE in output:
            raise RuntimeError("Eight workers unexpectedly used system scheduling.")
        if len(worker_mappings) != 8:
            raise RuntimeError("Eight workers were not individually mapped.")
        for worker, cpu_set in worker_mappings:
            if worker >= 8 or cpu_set != contexts[worker][1]:
                raise RuntimeError("Eight-worker mapping has the wrong context.")
    else:
        if SYSTEM_SCHEDULING_MESSAGE not in output:
            raise RuntimeError(
                "Sixteen workers were not reported as Windows-scheduled."
            )
        if worker_mappings:
            raise RuntimeError(
                "Sixteen workers should not have individual hard-affinity targets."
            )


def validate_worker_rows(workers: tuple[WorkerTiming, ...], threads: int) -> None:
    if len(workers) != threads or [worker.worker for worker in workers] != list(
        range(threads)
    ):
        raise RuntimeError(f"Expected ordered timings for {threads} workers.")

    assigned_rows: list[int] = []
    for worker in workers:
        rows = list(range(worker.worker, IMAGE_HEIGHT, threads))
        if worker.start_row != worker.worker or worker.stride != threads:
            raise RuntimeError("Worker does not use the cyclic row mapping.")
        if worker.row_count != len(rows):
            raise RuntimeError("Worker reported an incorrect row count.")
        if worker.elapsed_ms <= 0.0:
            raise RuntimeError("Worker elapsed times must be positive.")
        assigned_rows.extend(rows)
    if sorted(assigned_rows) != list(range(IMAGE_HEIGHT)):
        raise RuntimeError("Cyclic assignment has missing or duplicate rows.")


def parse_case(output: str, view: int, threads: int) -> CaseResult:
    if "[row decomposition]:\t\t[interleaved]" not in output:
        raise RuntimeError("Program did not report interleaved decomposition.")
    serial_ms = single_float(SERIAL_TIME_PATTERN, output, "serial timing")
    threaded_ms = single_float(THREADED_TIME_PATTERN, output, "threaded timing")
    profiled_ms = single_float(
        PROFILE_TRIAL_PATTERN, output, "profiled trial timing"
    )
    if not math.isclose(threaded_ms, profiled_ms, abs_tol=0.0005):
        raise RuntimeError("Worker data are not from the fastest threaded trial.")
    speedup_matches = SPEEDUP_PATTERN.findall(output)
    if len(speedup_matches) != 1 or int(speedup_matches[0][1]) != threads:
        raise RuntimeError("Program did not reach its verified speedup report.")

    workers = tuple(
        WorkerTiming(
            int(worker), int(start), int(stride), int(count), float(elapsed)
        )
        for worker, start, stride, count, elapsed
        in WORKER_TIMING_PATTERN.findall(output)
    )
    validate_worker_rows(workers, threads)
    if serial_ms <= 0.0 or threaded_ms <= 0.0:
        raise RuntimeError("Measured times must be positive.")
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
        prefix=f"mandelbrot-task5-v{view}-t{threads}-"
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


def results_by_view(results: Iterable[CaseResult]) -> dict[int, dict[int, CaseResult]]:
    grouped: dict[int, dict[int, CaseResult]] = {view: {} for view in VIEWS}
    for result in results:
        grouped[result.view][result.threads] = result
    if any(set(grouped[view]) != set(THREAD_COUNTS) for view in VIEWS):
        raise RuntimeError("The result set is incomplete.")
    return grouped


def write_results_csv(results: Iterable[CaseResult], destination: Path) -> None:
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
                "hardware_contexts",
                "worker_mapping",
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
                    8,
                    "individual" if result.threads == 8 else "windows_scheduler",
                ]
            )


def write_worker_csv(results: Iterable[CaseResult], destination: Path) -> None:
    with destination.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(
            [
                "view",
                "threads",
                "worker",
                "start_row",
                "stride",
                "row_count",
                "worker_ms",
            ]
        )
        for result in results:
            for worker in result.workers:
                writer.writerow(
                    [
                        result.view,
                        result.threads,
                        worker.worker,
                        worker.start_row,
                        worker.stride,
                        worker.row_count,
                        f"{worker.elapsed_ms:.3f}",
                    ]
                )


def write_markdown(results: list[CaseResult], destination: Path) -> None:
    grouped = results_by_view(results)
    lines = [
        "# Program 1 Task 5 Results",
        "",
        "Native Windows Release build with `--simulate-myth4`, "
        "`--decomposition interleaved`, and `--profile-workers`.",
        "Both thread counts use exactly the same four P-cores and eight SMT "
        "hardware contexts.",
        "For 16 threads, worker wall-clock timings include time descheduled by "
        "Windows and therefore do not measure row cost alone.",
        "",
        "| View | Threads | Serial (ms) | Threaded (ms) | Speedup | "
        "Worker imbalance | Mapping |",
        "| ---: | ---: | ---: | ---: | ---: | ---: | :--- |",
    ]
    for result in results:
        mapping = "individual contexts" if result.threads == 8 else "Windows scheduler"
        lines.append(
            f"| {result.view} | {result.threads} | {result.serial_ms:.3f} | "
            f"{result.threaded_ms:.3f} | {result.speedup:.2f}x | "
            f"{result.imbalance_ratio:.2f}x | {mapping} |"
        )

    lines.extend(
        [
            "",
            "## 16-thread change relative to 8 threads",
            "",
            "| View | 8-thread time (ms) | 16-thread time (ms) | "
            "Relative performance | Time change |",
            "| ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for view in VIEWS:
        eight = grouped[view][8]
        sixteen = grouped[view][16]
        relative = eight.threaded_ms / sixteen.threaded_ms
        time_change = (sixteen.threaded_ms / eight.threaded_ms - 1.0) * 100.0
        lines.append(
            f"| {view} | {eight.threaded_ms:.3f} | {sixteen.threaded_ms:.3f} | "
            f"{relative:.3f}x | {time_change:+.1f}% |"
        )
    lines.append("")
    destination.write_text("\n".join(lines), encoding="utf-8", newline="\n")


def write_svg(results: list[CaseResult], destination: Path) -> None:
    grouped = results_by_view(results)
    width, height = 880, 600
    left, right, top, bottom = 90, 40, 75, 85
    plot_width = width - left - right
    plot_height = height - top - bottom
    y_max = max(8.0, math.ceil(max(item.speedup for item in results)))

    def y_position(value: float) -> float:
        return top + (y_max - value) * plot_height / y_max

    centers = {1: left + plot_width * 0.28, 2: left + plot_width * 0.72}
    colors = {8: "#1769aa", 16: "#d96b20"}
    bar_width = 115.0
    gap = 18.0
    elements = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
            f'height="{height}" viewBox="0 0 {width} {height}" '
            'role="img" aria-labelledby="title description">'
        ),
        '<title id="title">Task 5 speedup with 8 and 16 threads</title>',
        (
            '<desc id="description">Grouped bars compare Mandelbrot speedup '
            'with eight and sixteen software threads on the same eight myth4 '
            'hardware contexts for Views 1 and 2.</desc>'
        ),
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        (
            '<text x="440" y="38" text-anchor="middle" '
            'font-family="sans-serif" font-size="23" font-weight="700">'
            'Program 1 Task 5 — 8 vs 16 Threads</text>'
        ),
    ]
    for tick in range(int(y_max) + 1):
        y = y_position(float(tick))
        elements.extend(
            [
                f'<line x1="{left}" y1="{y:.2f}" x2="{width - right}" '
                f'y2="{y:.2f}" stroke="#d9dee7"/>',
                f'<text x="{left - 12}" y="{y + 5:.2f}" text-anchor="end" '
                f'font-family="sans-serif" font-size="14">{tick}</text>',
            ]
        )
    for view in VIEWS:
        for offset, threads in ((-1, 8), (1, 16)):
            result = grouped[view][threads]
            x = centers[view] + offset * (bar_width + gap) / 2 - bar_width / 2
            y = y_position(result.speedup)
            bar_height = height - bottom - y
            elements.extend(
                [
                    f'<rect x="{x:.2f}" y="{y:.2f}" width="{bar_width}" '
                    f'height="{bar_height:.2f}" rx="3" fill="{colors[threads]}"/>',
                    f'<text x="{x + bar_width / 2:.2f}" y="{y - 10:.2f}" '
                    'text-anchor="middle" font-family="sans-serif" '
                    f'font-size="15" font-weight="700">{result.speedup:.2f}x</text>',
                ]
            )
        elements.append(
            f'<text x="{centers[view]:.2f}" y="{height - bottom + 32}" '
            f'text-anchor="middle" font-family="sans-serif" font-size="17">'
            f'View {view}</text>'
        )
    elements.extend(
        [
            f'<line x1="{left}" y1="{top}" x2="{left}" '
            f'y2="{height - bottom}" stroke="#1f2937" stroke-width="2"/>',
            f'<line x1="{left}" y1="{height - bottom}" '
            f'x2="{width - right}" y2="{height - bottom}" '
            'stroke="#1f2937" stroke-width="2"/>',
            f'<text x="25" y="{top + plot_height / 2:.2f}" '
            'text-anchor="middle" font-family="sans-serif" font-size="17" '
            f'transform="rotate(-90 25 {top + plot_height / 2:.2f})">'
            'Speedup over serial</text>',
            '<rect x="590" y="52" width="18" height="18" fill="#1769aa"/>',
            '<text x="617" y="67" font-family="sans-serif" '
            'font-size="14">8 threads</text>',
            '<rect x="700" y="52" width="18" height="18" fill="#d96b20"/>',
            '<text x="727" y="67" font-family="sans-serif" '
            'font-size="14">16 threads</text>',
            '<text x="440" y="575" text-anchor="middle" '
            'font-family="sans-serif" font-size="14" fill="#4b5563">'
            'Both configurations restricted to 4 P-cores / 8 SMT contexts</text>',
            '</svg>',
            '',
        ]
    )
    destination.write_text("\n".join(elements), encoding="utf-8", newline="\n")


def write_readme(results: list[CaseResult], destination: Path) -> None:
    grouped = results_by_view(results)

    def metrics(view: int) -> tuple[CaseResult, CaseResult, float, float]:
        eight = grouped[view][8]
        sixteen = grouped[view][16]
        relative = eight.threaded_ms / sixteen.threaded_ms
        change = (sixteen.threaded_ms / eight.threaded_ms - 1.0) * 100.0
        return eight, sixteen, relative, change

    view1 = metrics(1)
    view2 = metrics(2)
    lines = [
        "# Program 1, Task 5 — 8 vs 16 Threads",
        "",
        "![Eight- and sixteen-thread speedup](speedup_8_vs_16.svg)",
        "",
        "See [results.md](results.md), [results.csv](results.csv), "
        "[worker_times.csv](worker_times.csv), and the complete [`raw/`](raw/) "
        "program output.",
        "",
        "## English write-up",
        "",
        "### Method",
        "",
        "I ran the final static interleaved-row implementation with 8 and 16 "
        "software threads on both views. The native Windows Release executable "
        "used `--simulate-myth4 --decomposition interleaved --profile-workers`. "
        "Both configurations were restricted to exactly the same four physical "
        "P-cores and eight SMT hardware contexts. With eight threads, each worker "
        "was pinned to one context. With sixteen threads, workers were not bound "
        "round-robin; Windows scheduled them within the process's eight-context "
        "CPU Set restriction. Each timing is the minimum of five internal trials.",
        "",
        "### Results and explanation",
        "",
        f"For View 1, eight threads took {view1[0].threaded_ms:.3f} ms "
        f"({view1[0].speedup:.2f}x), while sixteen took "
        f"{view1[1].threaded_ms:.3f} ms ({view1[1].speedup:.2f}x). Thus the "
        f"sixteen-thread run had {view1[2]:.3f}x the performance of the "
        f"eight-thread run, with a {view1[3]:+.1f}% elapsed-time change. For "
        f"View 2, the corresponding values were {view2[0].threaded_ms:.3f} ms "
        f"({view2[0].speedup:.2f}x) and {view2[1].threaded_ms:.3f} ms "
        f"({view2[1].speedup:.2f}x), or {view2[2]:.3f}x relative performance "
        f"and a {view2[3]:+.1f}% elapsed-time change.",
        "",
        "Performance is not greater with sixteen threads because the "
        "simulated machine still exposes only eight hardware contexts. Eight "
        "workers already occupy all four cores and both SMT contexts per core. "
        "Sixteen workers therefore oversubscribe those same contexts: at most eight "
        "can execute at once, while the operating system time-slices the rest. The "
        "extra threads add creation, join, scheduling, and context-switch costs and "
        "can increase cache pressure. In this run the regression is substantial, "
        "not merely noise. All sixteen workers still receive exactly 75 rows, but "
        f"their wall-clock time ratios grow to {view1[1].imbalance_ratio:.2f}x "
        f"and {view2[1].imbalance_ratio:.2f}x. Those timers include time spent "
        "descheduled, so this spread is evidence of scheduler time-slicing rather "
        "than unequal Mandelbrot work. Static interleaving balances row work, but "
        "it cannot create additional execution resources; the exact regression is "
        "platform- and scheduler-dependent.",
        "",
        "These are i5-13500HX measurements under a 4P/8SMT topology restriction, "
        "not results from a Stanford `myth` host.",
        "",
        "## 中文分析",
        "",
        "### 实验方法",
        "",
        "最终静态交错行实现分别使用 8 和 16 个软件线程运行两个视图。原生 Windows "
        "Release 程序使用 `--simulate-myth4 --decomposition interleaved "
        "--profile-workers`，两种线程数都被限制在完全相同的 4 个物理 P 核和 8 个 "
        "SMT 硬件上下文中。8 线程时每个 worker 固定到一个上下文；16 线程时不进行"
        "循环硬绑定，而由 Windows 在进程的 8-context CPU Set 限制内调度。每个时间"
        "都是程序内部五次 trial 的最小值。",
        "",
        "### 结果与解释",
        "",
        f"View 1 的 8 线程时间为 {view1[0].threaded_ms:.3f} ms，"
        f"加速比 {view1[0].speedup:.2f}x；16 线程时间为 "
        f"{view1[1].threaded_ms:.3f} ms，加速比 {view1[1].speedup:.2f}x。"
        f"16 线程相对性能为 {view1[2]:.3f}x，耗时变化 {view1[3]:+.1f}%。"
        f"View 2 的 8/16 线程时间分别为 {view2[0].threaded_ms:.3f} ms 和 "
        f"{view2[1].threaded_ms:.3f} ms，加速比分别为 "
        f"{view2[0].speedup:.2f}x 和 {view2[1].speedup:.2f}x；16 线程相对"
        f"性能为 {view2[2]:.3f}x，耗时变化 {view2[3]:+.1f}%。",
        "",
        "16 线程并没有更快，因为模拟机器仍然只有 8 个硬件上下文。8 个 worker "
        "已经占满四个核心的两个 SMT 上下文；16 个 worker 只能过量订阅同一批上下文，"
        "任意时刻最多仍有 8 个执行，其余线程由操作系统分时调度。额外线程增加了创建、"
        "`join`、调度和上下文切换开销，也可能增加缓存压力。本次退化幅度明显，不只是"
        "普通噪声。16 个 worker 都恰好负责 75 行，但墙钟耗时比分别增长到 "
        f"{view1[1].imbalance_ratio:.2f}x 和 {view2[1].imbalance_ratio:.2f}x；worker "
        "计时包含被操作系统暂停的时间，因此这是分时调度的证据，而不是 Mandelbrot "
        "计算量重新失衡。静态交错能平衡行工作量，却无法产生新的执行资源；具体退化"
        "幅度取决于平台和调度器。",
        "",
        "这些数据来自 i5-13500HX 上的 4P/8SMT 拓扑限制，并非 Stanford `myth` "
        "实机结果。",
        "",
        "## Reproduce / 复现",
        "",
        "```bash",
        "python3 task5/benchmark_task5.py \\",
        "  --executable /mnt/c/path/to/build/Release/mandelbrot.exe",
        "```",
        "",
        "```powershell",
        "py task5\\benchmark_task5.py `",
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
    if len(rows) != 4:
        raise RuntimeError("results.csv does not contain exactly four rows.")
    expected = {(view, threads) for view in VIEWS for threads in THREAD_COUNTS}
    actual = {(int(row["view"]), int(row["threads"])) for row in rows}
    if actual != expected:
        raise RuntimeError("results.csv is missing an 8/16-thread case.")
    for row in rows:
        serial_ms = float(row["serial_ms"])
        threaded_ms = float(row["threaded_ms"])
        speedup = float(row["speedup"])
        if not math.isclose(
            speedup, serial_ms / threaded_ms, rel_tol=0.0, abs_tol=0.0001
        ):
            raise RuntimeError("results.csv contains an inconsistent speedup.")
        if int(row["hardware_contexts"]) != 8:
            raise RuntimeError("A result escaped the eight-context restriction.")

    with (output_dir / "worker_times.csv").open(
        "r", encoding="utf-8", newline=""
    ) as stream:
        worker_rows = list(csv.DictReader(stream))
    if len(worker_rows) != 48:
        raise RuntimeError("worker_times.csv does not contain exactly 48 rows.")
    ET.parse(output_dir / "speedup_8_vs_16.svg")


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
                    f"  {result.threaded_ms:.3f} ms, {result.speedup:.2f}x, "
                    f"worker ratio {result.imbalance_ratio:.2f}x",
                    flush=True,
                )

        write_results_csv(results, output_dir / "results.csv")
        write_worker_csv(results, output_dir / "worker_times.csv")
        write_markdown(results, output_dir / "results.md")
        write_svg(results, output_dir / "speedup_8_vs_16.svg")
        write_readme(results, output_dir / "README.md")
        validate_generated_files(output_dir)
    except (OSError, RuntimeError, ValueError, ET.ParseError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    print(f"Task 5 artifacts written to {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
