#!/usr/bin/env python3
"""Run the Program 1 Task 2 sweep and generate dependency-free reports."""

from __future__ import annotations

import argparse
import csv
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


THREAD_COUNTS = tuple(range(2, 9))
SERIAL_TIME_PATTERN = re.compile(
    r"\[mandelbrot serial\]:\s*\[([0-9]+(?:\.[0-9]+)?)\]\s*ms"
)
THREADED_TIME_PATTERN = re.compile(
    r"\[mandelbrot thread\]:\s*\[([0-9]+(?:\.[0-9]+)?)\]\s*ms"
)
SPEEDUP_PATTERN = re.compile(
    r"\(([0-9]+(?:\.[0-9]+)?)x speedup from ([0-9]+) threads\)"
)
CONTEXT_PATTERN = re.compile(
    r"Context ([0-7]) -> CPU Set ([0-9]+), Group ([0-9]+), "
    r"Logical CPU ([0-9]+), CoreIndex ([0-9]+), EfficiencyClass ([0-9]+)"
)
WORKER_PATTERN = re.compile(
    r"Worker ([0-9]+) -> CPU Set ([0-9]+), Group ([0-9]+), "
    r"Logical CPU ([0-9]+), CoreIndex ([0-9]+)"
)
SERIAL_TARGET_PATTERN = re.compile(
    r"Serial reference -> CPU Set ([0-9]+), Group ([0-9]+), "
    r"Logical CPU ([0-9]+), CoreIndex ([0-9]+)"
)


@dataclass(frozen=True)
class Result:
    threads: int
    serial_ms: float
    threaded_ms: float

    @property
    def speedup(self) -> float:
        return self.serial_ms / self.threaded_ms


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Benchmark Mandelbrot View 1 with 2-8 threads under "
            "--simulate-myth4 and generate CSV, Markdown, raw logs, and SVG."
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


def require_single_match(pattern: re.Pattern[str], output: str, label: str) -> str:
    matches = pattern.findall(output)
    if len(matches) != 1:
        raise RuntimeError(
            f"Expected exactly one {label} in program output, found {len(matches)}."
        )
    match = matches[0]
    if isinstance(match, tuple):
        raise TypeError(f"Internal parser error: {label} has multiple capture groups.")
    return match


def validate_affinity(output: str, threads: int) -> None:
    if "Selected myth4 physical P-cores: 4" not in output:
        raise RuntimeError("Program did not report exactly four selected P-cores.")
    if "Selected myth4 SMT contexts: 8" not in output:
        raise RuntimeError("Program did not report exactly eight SMT contexts.")

    contexts = sorted(
        (
            int(context),
            int(cpu_set),
            int(group),
            int(logical_cpu),
            int(core_index),
            int(efficiency_class),
        )
        for context, cpu_set, group, logical_cpu, core_index, efficiency_class
        in CONTEXT_PATTERN.findall(output)
    )
    if len(contexts) != 8 or [item[0] for item in contexts] != list(range(8)):
        raise RuntimeError("Affinity output did not describe contexts 0 through 7.")

    cpu_set_ids = [item[1] for item in contexts]
    core_indices = [item[4] for item in contexts]
    if len(set(cpu_set_ids)) != 8:
        raise RuntimeError("The myth4 context list contains duplicate CPU Set IDs.")
    if len(set(core_indices)) != 4:
        raise RuntimeError("The myth4 context list does not use exactly four cores.")
    if any(core_indices.count(core) != 2 for core in set(core_indices)):
        raise RuntimeError("Each selected P-core must contribute two SMT contexts.")

    serial_targets = SERIAL_TARGET_PATTERN.findall(output)
    if len(serial_targets) != 1 or int(serial_targets[0][0]) != contexts[0][1]:
        raise RuntimeError("Serial reference was not assigned to myth4 context 0.")

    workers = sorted(
        (
            int(worker),
            int(cpu_set),
            int(group),
            int(logical_cpu),
            int(core_index),
        )
        for worker, cpu_set, group, logical_cpu, core_index
        in WORKER_PATTERN.findall(output)
    )
    if len(workers) != threads:
        raise RuntimeError(
            f"Expected {threads} deterministic worker mappings, found {len(workers)}."
        )
    for worker in workers:
        worker_index, cpu_set = worker[0], worker[1]
        if worker_index >= threads or cpu_set != contexts[worker_index][1]:
            raise RuntimeError("Worker mapping does not follow myth4 context order.")


def parse_result(output: str, threads: int) -> Result:
    serial_ms = float(
        require_single_match(SERIAL_TIME_PATTERN, output, "serial timing")
    )
    threaded_ms = float(
        require_single_match(THREADED_TIME_PATTERN, output, "threaded timing")
    )
    speedup_matches = SPEEDUP_PATTERN.findall(output)
    if len(speedup_matches) != 1 or int(speedup_matches[0][1]) != threads:
        raise RuntimeError("Program did not reach the verified speedup report.")
    if serial_ms <= 0.0 or threaded_ms <= 0.0:
        raise RuntimeError("Measured times must be positive.")
    return Result(threads, serial_ms, threaded_ms)


def run_case(executable: Path, threads: int, raw_dir: Path) -> Result:
    command = [
        str(executable),
        "-t",
        str(threads),
        "--simulate-myth4",
        "--decomposition",
        "block",
        "-v",
        "1",
    ]
    with tempfile.TemporaryDirectory(prefix=f"mandelbrot-task2-t{threads}-") as work:
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
    return parse_result(output, threads)


def write_csv(results: Iterable[Result], destination: Path) -> None:
    with destination.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(["threads", "serial_ms", "threaded_ms", "speedup"])
        for result in results:
            writer.writerow(
                [
                    result.threads,
                    f"{result.serial_ms:.3f}",
                    f"{result.threaded_ms:.3f}",
                    f"{result.speedup:.4f}",
                ]
            )


def write_markdown(results: Iterable[Result], destination: Path) -> None:
    lines = [
        "# Program 1 Task 2 Results",
        "",
        "View 1, native Windows Release build, `--simulate-myth4` and "
        "`--decomposition block`.",
        "Each executable invocation reports the minimum of five internal trials.",
        "",
        "| Threads | Serial (ms) | Threaded (ms) | Speedup |",
        "| ---: | ---: | ---: | ---: |",
    ]
    for result in results:
        lines.append(
            f"| {result.threads} | {result.serial_ms:.3f} | "
            f"{result.threaded_ms:.3f} | {result.speedup:.2f}x |"
        )
    lines.extend(
        [
            "",
            "The speedup column is recomputed from the reported three-decimal "
            "timing values in `results.csv`.",
            "",
        ]
    )
    destination.write_text("\n".join(lines), encoding="utf-8", newline="\n")


def write_svg(results: list[Result], destination: Path) -> None:
    width, height = 900, 600
    left, right, top, bottom = 90, 35, 65, 80
    plot_width = width - left - right
    plot_height = height - top - bottom
    x_min, x_max = 2.0, 8.0
    y_min, y_max = 0.0, 8.5

    def x_position(value: float) -> float:
        return left + (value - x_min) * plot_width / (x_max - x_min)

    def y_position(value: float) -> float:
        return top + (y_max - value) * plot_height / (y_max - y_min)

    measured_points = " ".join(
        f"{x_position(result.threads):.2f},{y_position(result.speedup):.2f}"
        for result in results
    )
    ideal_points = " ".join(
        f"{x_position(threads):.2f},{y_position(float(threads)):.2f}"
        for threads in THREAD_COUNTS
    )

    elements = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
            f'height="{height}" viewBox="0 0 {width} {height}" '
            'role="img" aria-labelledby="title description">'
        ),
        '<title id="title">Mandelbrot View 1 speedup by thread count</title>',
        (
            '<desc id="description">Measured block-decomposition speedup on a '
            'four-P-core, eight-SMT-context myth4 simulation, compared with ideal '
            'linear speedup.</desc>'
        ),
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        (
            '<text x="450" y="34" text-anchor="middle" '
            'font-family="sans-serif" font-size="22" font-weight="700">'
            'Program 1 Task 2 — View 1 Speedup</text>'
        ),
    ]

    for tick in range(0, 9):
        y = y_position(float(tick))
        elements.append(
            f'<line x1="{left}" y1="{y:.2f}" x2="{width - right}" '
            'y2="{:.2f}" stroke="#d9dee7" stroke-width="1"/>'.format(y)
        )
        elements.append(
            f'<text x="{left - 12}" y="{y + 5:.2f}" text-anchor="end" '
            f'font-family="sans-serif" font-size="14" fill="#303642">{tick}</text>'
        )

    for threads in THREAD_COUNTS:
        x = x_position(float(threads))
        elements.append(
            f'<line x1="{x:.2f}" y1="{top}" x2="{x:.2f}" '
            f'y2="{height - bottom}" stroke="#edf0f5" stroke-width="1"/>'
        )
        elements.append(
            f'<text x="{x:.2f}" y="{height - bottom + 28}" text-anchor="middle" '
            f'font-family="sans-serif" font-size="14" fill="#303642">{threads}</text>'
        )

    elements.extend(
        [
            f'<line x1="{left}" y1="{top}" x2="{left}" y2="{height - bottom}" '
            'stroke="#1f2937" stroke-width="2"/>',
            f'<line x1="{left}" y1="{height - bottom}" x2="{width - right}" '
            f'y2="{height - bottom}" stroke="#1f2937" stroke-width="2"/>',
            (
                f'<polyline points="{ideal_points}" fill="none" stroke="#8b95a5" '
                'stroke-width="3" stroke-dasharray="9 7"/>'
            ),
            (
                f'<polyline points="{measured_points}" fill="none" '
                'stroke="#1769aa" stroke-width="4" stroke-linejoin="round"/>'
            ),
        ]
    )

    for result in results:
        x = x_position(float(result.threads))
        y = y_position(result.speedup)
        elements.append(
            f'<circle cx="{x:.2f}" cy="{y:.2f}" r="6" fill="#1769aa" '
            'stroke="#ffffff" stroke-width="2"/>'
        )
        elements.append(
            f'<text x="{x:.2f}" y="{y - 12:.2f}" text-anchor="middle" '
            f'font-family="sans-serif" font-size="13" font-weight="700" '
            f'fill="#124f80">{result.speedup:.2f}x</text>'
        )

    elements.extend(
        [
            (
                f'<text x="{left + plot_width / 2:.2f}" y="{height - 20}" '
                'text-anchor="middle" font-family="sans-serif" font-size="17">'
                'Number of threads</text>'
            ),
            (
                f'<text x="24" y="{top + plot_height / 2:.2f}" '
                'text-anchor="middle" font-family="sans-serif" font-size="17" '
                f'transform="rotate(-90 24 {top + plot_height / 2:.2f})">'
                'Speedup over serial</text>'
            ),
            '<line x1="610" y1="54" x2="650" y2="54" stroke="#1769aa" '
            'stroke-width="4"/>',
            '<text x="660" y="59" font-family="sans-serif" font-size="14">Measured</text>',
            '<line x1="610" y1="78" x2="650" y2="78" stroke="#8b95a5" '
            'stroke-width="3" stroke-dasharray="9 7"/>',
            '<text x="660" y="83" font-family="sans-serif" font-size="14">Ideal linear</text>',
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

    results: list[Result] = []
    try:
        for threads in THREAD_COUNTS:
            print(f"Running View 1 with {threads} threads...", flush=True)
            result = run_case(executable, threads, raw_dir)
            results.append(result)
            print(
                f"  serial={result.serial_ms:.3f} ms, "
                f"threaded={result.threaded_ms:.3f} ms, "
                f"speedup={result.speedup:.2f}x",
                flush=True,
            )
    except (OSError, RuntimeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    write_csv(results, output_dir / "results.csv")
    write_markdown(results, output_dir / "results.md")
    write_svg(results, output_dir / "speedup_view1.svg")
    print(f"Wrote Task 2 results to {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
