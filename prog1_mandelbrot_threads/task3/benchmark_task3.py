#!/usr/bin/env python3
"""Collect and visualize Program 1 Task 3 per-worker timing data."""

from __future__ import annotations

import argparse
import csv
import math
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


THREAD_COUNTS = tuple(range(2, 9))
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
    r"\[worker ([0-9]+)\]: rows \[([0-9]+), ([0-9]+)\), "
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


@dataclass(frozen=True)
class WorkerTiming:
    worker: int
    start_row: int
    end_row: int
    elapsed_ms: float


@dataclass(frozen=True)
class CaseResult:
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
    def spread_ms(self) -> float:
        return self.maximum_worker_ms - self.minimum_worker_ms

    @property
    def imbalance_ratio(self) -> float:
        return self.maximum_worker_ms / self.minimum_worker_ms

    @property
    def slowest_worker(self) -> int:
        return max(self.workers, key=lambda worker: worker.elapsed_ms).worker


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Profile Mandelbrot View 1 with 2-8 threads under --simulate-myth4 "
            "and generate Task 3 worker-timing artifacts."
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
        )
        for context, cpu_set, _group, _logical_cpu, core_index, _efficiency
        in CONTEXT_PATTERN.findall(output)
    )
    if len(contexts) != 8 or [context[0] for context in contexts] != list(range(8)):
        raise RuntimeError("Affinity output did not describe contexts 0 through 7.")
    if len({context[1] for context in contexts}) != 8:
        raise RuntimeError("The myth4 context list contains duplicate CPU Set IDs.")
    core_indices = [context[2] for context in contexts]
    if len(set(core_indices)) != 4:
        raise RuntimeError("The myth4 context list does not use exactly four cores.")
    if any(core_indices.count(core) != 2 for core in set(core_indices)):
        raise RuntimeError("Each selected P-core must contribute two SMT contexts.")

    workers = sorted(
        (int(worker), int(cpu_set))
        for worker, cpu_set, _group, _logical_cpu, _core_index
        in AFFINITY_WORKER_PATTERN.findall(output)
    )
    if len(workers) != threads:
        raise RuntimeError(
            f"Expected {threads} affinity worker mappings, found {len(workers)}."
        )
    for worker, cpu_set in workers:
        if worker >= threads or cpu_set != contexts[worker][1]:
            raise RuntimeError("Worker affinity does not follow myth4 context order.")


def expected_row_range(threads: int, worker: int) -> tuple[int, int]:
    rows_per_thread = IMAGE_HEIGHT // threads
    start_row = worker * rows_per_thread
    end_row = (
        IMAGE_HEIGHT if worker == threads - 1 else start_row + rows_per_thread
    )
    return start_row, end_row


def parse_case(output: str, threads: int) -> CaseResult:
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
        WorkerTiming(int(worker), int(start), int(end), float(elapsed))
        for worker, start, end, elapsed in WORKER_TIMING_PATTERN.findall(output)
    )
    if len(workers) != threads or [worker.worker for worker in workers] != list(
        range(threads)
    ):
        raise RuntimeError(f"Expected ordered timings for {threads} workers.")
    for worker in workers:
        if (worker.start_row, worker.end_row) != expected_row_range(
            threads, worker.worker
        ):
            raise RuntimeError("Worker row ranges do not match block decomposition.")
        if worker.elapsed_ms <= 0.0:
            raise RuntimeError("Worker elapsed times must be positive.")
        if worker.elapsed_ms > threaded_ms + 0.001:
            raise RuntimeError("A worker time exceeds its enclosing threaded trial.")

    if serial_ms <= 0.0 or threaded_ms <= 0.0:
        raise RuntimeError("Measured total times must be positive.")
    return CaseResult(threads, serial_ms, threaded_ms, workers)


def run_case(executable: Path, threads: int, raw_dir: Path) -> CaseResult:
    command = [
        str(executable),
        "-t",
        str(threads),
        "--simulate-myth4",
        "--decomposition",
        "block",
        "--profile-workers",
        "-v",
        "1",
    ]
    with tempfile.TemporaryDirectory(prefix=f"mandelbrot-task3-t{threads}-") as work:
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
    (raw_dir / f"view1_threads_{threads}.txt").write_text(
        output, encoding="utf-8", newline="\n"
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"Thread count {threads} exited with code {completed.returncode}; "
            f"see raw/view1_threads_{threads}.txt."
        )
    validate_affinity(output, threads)
    return parse_case(output, threads)


def write_worker_csv(results: Iterable[CaseResult], destination: Path) -> None:
    with destination.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(
            [
                "threads",
                "worker",
                "start_row",
                "end_row",
                "worker_ms",
                "is_slowest",
                "threaded_trial_ms",
                "serial_ms",
                "speedup",
            ]
        )
        for result in results:
            for worker in result.workers:
                writer.writerow(
                    [
                        result.threads,
                        worker.worker,
                        worker.start_row,
                        worker.end_row,
                        f"{worker.elapsed_ms:.3f}",
                        "true" if worker.worker == result.slowest_worker else "false",
                        f"{result.threaded_ms:.3f}",
                        f"{result.serial_ms:.3f}",
                        f"{result.speedup:.4f}",
                    ]
                )


def write_summary_csv(results: Iterable[CaseResult], destination: Path) -> None:
    with destination.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(
            [
                "threads",
                "serial_ms",
                "threaded_ms",
                "speedup",
                "min_worker_ms",
                "max_worker_ms",
                "spread_ms",
                "imbalance_ratio",
                "slowest_worker",
            ]
        )
        for result in results:
            writer.writerow(
                [
                    result.threads,
                    f"{result.serial_ms:.3f}",
                    f"{result.threaded_ms:.3f}",
                    f"{result.speedup:.4f}",
                    f"{result.minimum_worker_ms:.3f}",
                    f"{result.maximum_worker_ms:.3f}",
                    f"{result.spread_ms:.3f}",
                    f"{result.imbalance_ratio:.4f}",
                    result.slowest_worker,
                ]
            )


def write_markdown(results: Iterable[CaseResult], destination: Path) -> None:
    results = list(results)
    lines = [
        "# Program 1 Task 3 Results",
        "",
        "View 1, native Windows Release build, `--simulate-myth4`, "
        "`--decomposition block`, and `--profile-workers`.",
        "Worker timings come from the same fastest threaded trial used for the "
        "reported speedup.",
        "",
        "| Threads | Threaded (ms) | Speedup | Min worker (ms) | Max worker (ms) | Imbalance | Slowest |",
        "| ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for result in results:
        lines.append(
            f"| {result.threads} | {result.threaded_ms:.3f} | "
            f"{result.speedup:.2f}x | {result.minimum_worker_ms:.3f} | "
            f"{result.maximum_worker_ms:.3f} | {result.imbalance_ratio:.2f}x | "
            f"{result.slowest_worker} |"
        )

    for result in results:
        lines.extend(
            [
                "",
                f"## {result.threads} threads",
                "",
                "| Worker | Rows | Compute time (ms) |",
                "| ---: | :--- | ---: |",
            ]
        )
        for worker in result.workers:
            marker = " **(slowest)**" if worker.worker == result.slowest_worker else ""
            lines.append(
                f"| {worker.worker} | [{worker.start_row}, {worker.end_row}) | "
                f"{worker.elapsed_ms:.3f}{marker} |"
            )
    lines.append("")
    destination.write_text("\n".join(lines), encoding="utf-8", newline="\n")


def write_svg(results: list[CaseResult], destination: Path) -> None:
    width, height = 1000, 1160
    panel_width, panel_height = 430, 235
    column_gap, row_gap = 60, 28
    origin_x, origin_y = 70, 105
    plot_left, plot_top, plot_width, plot_height = 42, 32, 370, 165
    maximum = max(result.maximum_worker_ms for result in results)
    y_max = max(20.0, math.ceil(maximum / 20.0) * 20.0)

    elements = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
            f'height="{height}" viewBox="0 0 {width} {height}" '
            'role="img" aria-labelledby="title description">'
        ),
        '<title id="title">Mandelbrot View 1 per-worker compute times</title>',
        (
            '<desc id="description">Seven panels compare per-worker compute '
            'times for two through eight threads. The slowest worker in each '
            'panel is highlighted.</desc>'
        ),
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        (
            '<text x="500" y="38" text-anchor="middle" font-family="sans-serif" '
            'font-size="24" font-weight="700">Program 1 Task 3 — View 1 Worker Times</text>'
        ),
        (
            '<rect x="340" y="57" width="16" height="16" fill="#1769aa"/>'
            '<text x="364" y="70" font-family="sans-serif" font-size="14">Worker</text>'
        ),
        (
            '<rect x="455" y="57" width="16" height="16" fill="#c43c35"/>'
            '<text x="479" y="70" font-family="sans-serif" font-size="14">Slowest worker</text>'
        ),
        (
            f'<text x="655" y="70" font-family="sans-serif" font-size="14" '
            f'fill="#4b5563">Common scale: 0–{y_max:.0f} ms</text>'
        ),
    ]

    for index, result in enumerate(results):
        column = index % 2
        row = index // 2
        panel_x = origin_x + column * (panel_width + column_gap)
        panel_y = origin_y + row * (panel_height + row_gap)
        chart_x = panel_x + plot_left
        chart_y = panel_y + plot_top

        elements.append(
            f'<rect x="{panel_x}" y="{panel_y}" width="{panel_width}" '
            f'height="{panel_height}" rx="8" fill="#fbfcfe" stroke="#cfd6e2"/>'
        )
        elements.append(
            f'<text x="{panel_x + panel_width / 2:.2f}" y="{panel_y + 23}" '
            f'text-anchor="middle" font-family="sans-serif" font-size="17" '
            f'font-weight="700">{result.threads} threads</text>'
        )

        for fraction in (0.0, 0.5, 1.0):
            value = y_max * fraction
            y = chart_y + plot_height * (1.0 - fraction)
            elements.append(
                f'<line x1="{chart_x}" y1="{y:.2f}" '
                f'x2="{chart_x + plot_width}" y2="{y:.2f}" '
                'stroke="#dde3ec" stroke-width="1"/>'
            )
            elements.append(
                f'<text x="{chart_x - 7}" y="{y + 4:.2f}" text-anchor="end" '
                f'font-family="sans-serif" font-size="11" fill="#596273">{value:.0f}</text>'
            )

        gap = 7.0
        bar_width = (plot_width - gap * (result.threads + 1)) / result.threads
        for worker in result.workers:
            bar_height = worker.elapsed_ms * plot_height / y_max
            x = chart_x + gap + worker.worker * (bar_width + gap)
            y = chart_y + plot_height - bar_height
            color = "#c43c35" if worker.worker == result.slowest_worker else "#1769aa"
            elements.append(
                f'<rect x="{x:.2f}" y="{y:.2f}" width="{bar_width:.2f}" '
                f'height="{bar_height:.2f}" rx="2" fill="{color}"/>'
            )
            elements.append(
                f'<text x="{x + bar_width / 2:.2f}" y="{max(chart_y + 11, y - 4):.2f}" '
                f'text-anchor="middle" font-family="sans-serif" font-size="10" '
                f'font-weight="700" fill="#26313f">{worker.elapsed_ms:.1f}</text>'
            )
            elements.append(
                f'<text x="{x + bar_width / 2:.2f}" y="{chart_y + plot_height + 15}" '
                f'text-anchor="middle" font-family="sans-serif" font-size="11">'
                f'W{worker.worker}</text>'
            )

        elements.append(
            f'<text x="{panel_x + panel_width / 2:.2f}" y="{panel_y + panel_height - 8}" '
            f'text-anchor="middle" font-family="sans-serif" font-size="12" '
            f'fill="#4b5563">max/min = {result.imbalance_ratio:.2f}x</text>'
        )

    two_threads = results[0]
    three_threads = results[1]
    note_x = origin_x + panel_width + column_gap
    note_y = origin_y + 3 * (panel_height + row_gap)
    elements.extend(
        [
            f'<rect x="{note_x}" y="{note_y}" width="{panel_width}" '
            f'height="{panel_height}" rx="8" fill="#f5f8fc" stroke="#cfd6e2"/>',
            f'<text x="{note_x + panel_width / 2:.2f}" y="{note_y + 32}" '
            'text-anchor="middle" font-family="sans-serif" font-size="19" '
            'font-weight="700">Key observation</text>',
            f'<text x="{note_x + 32}" y="{note_y + 75}" font-family="sans-serif" '
            f'font-size="16">2 threads: max/min = {two_threads.imbalance_ratio:.2f}x</text>',
            f'<text x="{note_x + 32}" y="{note_y + 108}" font-family="sans-serif" '
            f'font-size="16">3 threads: max/min = {three_threads.imbalance_ratio:.2f}x</text>',
            f'<text x="{note_x + 32}" y="{note_y + 148}" font-family="sans-serif" '
            'font-size="15" fill="#374151">The central 3-thread block dominates,</text>',
            f'<text x="{note_x + 32}" y="{note_y + 173}" font-family="sans-serif" '
            'font-size="15" fill="#374151">and total time follows the slowest worker.</text>',
            f'<text x="{note_x + panel_width / 2:.2f}" y="{note_y + 211}" '
            'text-anchor="middle" font-family="sans-serif" font-size="15" '
            'font-weight="700" fill="#c43c35">Confirms block load imbalance</text>',
        ]
    )

    elements.extend(
        [
            '<text x="500" y="1140" text-anchor="middle" font-family="sans-serif" '
            'font-size="14" fill="#4b5563">Compute-only timing; affinity setup, thread creation, and join excluded</text>',
            '</svg>',
            '',
        ]
    )
    destination.write_text("\n".join(elements), encoding="utf-8", newline="\n")


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
        for threads in THREAD_COUNTS:
            print(f"Profiling View 1 with {threads} threads...", flush=True)
            result = run_case(executable, threads, raw_dir)
            results.append(result)
            print(
                f"  threaded={result.threaded_ms:.3f} ms, "
                f"slowest=W{result.slowest_worker} {result.maximum_worker_ms:.3f} ms, "
                f"imbalance={result.imbalance_ratio:.2f}x",
                flush=True,
            )
    except (OSError, RuntimeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    write_worker_csv(results, output_dir / "worker_times.csv")
    write_summary_csv(results, output_dir / "summary.csv")
    write_markdown(results, output_dir / "results.md")
    write_svg(results, output_dir / "worker_times_view1.svg")
    print(f"Wrote Task 3 results to {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
