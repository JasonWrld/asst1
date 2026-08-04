#!/usr/bin/env python3
"""Benchmark Program 3 Part 1 and generate reproducible result files."""

from __future__ import annotations

import argparse
import csv
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path


SERIAL_TIME_PATTERN = re.compile(
    r"\[mandelbrot serial\]:\s*\[([0-9]+(?:\.[0-9]+)?)\]\s*ms"
)
ISPC_TIME_PATTERN = re.compile(
    r"\[mandelbrot ispc\]:\s*\[([0-9]+(?:\.[0-9]+)?)\]\s*ms"
)
SPEEDUP_PATTERN = re.compile(
    r"\(([0-9]+(?:\.[0-9]+)?)x speedup from ISPC\)"
)
VIEWS = (1, 2)


@dataclass(frozen=True)
class Result:
    view: int
    run: int
    serial_ms: float
    ispc_ms: float

    @property
    def speedup(self) -> float:
        return self.serial_ms / self.ispc_ms


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run both Mandelbrot views repeatedly on one logical CPU and "
            "generate raw logs, CSV, and Markdown results."
        )
    )
    parser.add_argument(
        "--executable",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "mandelbrot_ispc",
        help="Path to mandelbrot_ispc (default: repository executable).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).resolve().parent,
        help="Output directory (default: directory containing this script).",
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=5,
        help="Independent invocations per view (default: 5).",
    )
    parser.add_argument(
        "--cpu",
        type=int,
        default=0,
        help="Logical CPU on which to pin the child process (default: 0).",
    )
    return parser.parse_args()


def require_single_match(
    pattern: re.Pattern[str], output: str, label: str
) -> float:
    matches = pattern.findall(output)
    if len(matches) != 1:
        raise RuntimeError(
            f"Expected exactly one {label} in output, found {len(matches)}."
        )
    return float(matches[0])


def parse_result(output: str, view: int, run: int) -> Result:
    serial_ms = require_single_match(
        SERIAL_TIME_PATTERN, output, "serial timing"
    )
    ispc_ms = require_single_match(ISPC_TIME_PATTERN, output, "ISPC timing")
    printed_speedup = require_single_match(
        SPEEDUP_PATTERN, output, "ISPC speedup"
    )
    if serial_ms <= 0.0 or ispc_ms <= 0.0:
        raise RuntimeError("Measured times must be positive.")

    result = Result(view, run, serial_ms, ispc_ms)
    if abs(result.speedup - printed_speedup) > 0.02:
        raise RuntimeError(
            "Printed speedup does not agree with the reported timings: "
            f"printed={printed_speedup:.2f}, computed={result.speedup:.4f}."
        )
    return result


def affinity_setter(cpu: int):
    def set_affinity() -> None:
        os.sched_setaffinity(0, {cpu})

    return set_affinity


def run_case(
    executable: Path,
    output_dir: Path,
    view: int,
    run: int,
    cpu: int,
) -> Result:
    command = [str(executable), "--view", str(view)]
    with tempfile.TemporaryDirectory(
        prefix=f"mandelbrot-part1-v{view}-r{run}-"
    ) as work_dir:
        completed = subprocess.run(
            command,
            cwd=work_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            preexec_fn=affinity_setter(cpu),
        )

    raw_path = output_dir / "raw" / f"view{view}_run{run}.txt"
    raw_path.write_text(completed.stdout, encoding="utf-8", newline="\n")
    if completed.returncode != 0:
        raise RuntimeError(
            f"View {view}, run {run} exited with code "
            f"{completed.returncode}; see {raw_path}."
        )
    if "Error : ISPC output differs from sequential output" in completed.stdout:
        raise RuntimeError(f"View {view}, run {run} failed verification.")
    return parse_result(completed.stdout, view, run)


def write_csv(results: list[Result], destination: Path) -> None:
    with destination.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(["view", "run", "serial_ms", "ispc_ms", "speedup"])
        for result in results:
            writer.writerow(
                [
                    result.view,
                    result.run,
                    f"{result.serial_ms:.3f}",
                    f"{result.ispc_ms:.3f}",
                    f"{result.speedup:.4f}",
                ]
            )


def best_for_view(results: list[Result], view: int) -> tuple[float, float]:
    view_results = [result for result in results if result.view == view]
    return (
        min(result.serial_ms for result in view_results),
        min(result.ispc_ms for result in view_results),
    )


def write_markdown(
    results: list[Result], destination: Path, runs: int, cpu: int
) -> None:
    lines = [
        "# Program 3 Part 1 Results",
        "",
        f"Local WSL benchmark pinned to logical CPU {cpu}. Each row is the "
        "minimum of three internal trials reported by one executable "
        "invocation.",
        f"The summary selects the minimum serial and ISPC times from {runs} "
        "independent invocations.",
        "",
        "## Summary",
        "",
        "| View | Serial (ms) | ISPC (ms) | Speedup |",
        "| ---: | ---: | ---: | ---: |",
    ]
    for view in VIEWS:
        serial_ms, ispc_ms = best_for_view(results, view)
        lines.append(
            f"| {view} | {serial_ms:.3f} | {ispc_ms:.3f} | "
            f"{serial_ms / ispc_ms:.2f}x |"
        )

    lines.extend(
        [
            "",
            "## All runs",
            "",
            "| View | Run | Serial (ms) | ISPC (ms) | Speedup |",
            "| ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for result in results:
        lines.append(
            f"| {result.view} | {result.run} | {result.serial_ms:.3f} | "
            f"{result.ispc_ms:.3f} | {result.speedup:.2f}x |"
        )
    lines.extend(
        [
            "",
            "Speedups are recomputed from the three-decimal timing values; "
            "complete program output is preserved in `raw/`.",
            "",
        ]
    )
    destination.write_text("\n".join(lines), encoding="utf-8", newline="\n")


def main() -> int:
    args = parse_args()
    if args.runs <= 0:
        raise ValueError("--runs must be positive.")

    executable = args.executable.expanduser().resolve()
    if not executable.is_file():
        raise FileNotFoundError(f"Executable not found: {executable}")

    allowed_cpus = os.sched_getaffinity(0)
    if args.cpu not in allowed_cpus:
        allowed = ", ".join(str(cpu) for cpu in sorted(allowed_cpus))
        raise ValueError(f"CPU {args.cpu} is unavailable; allowed CPUs: {allowed}")

    output_dir = args.output_dir.expanduser().resolve()
    (output_dir / "raw").mkdir(parents=True, exist_ok=True)

    results: list[Result] = []
    for view in VIEWS:
        for run in range(1, args.runs + 1):
            print(f"Running view {view}, trial {run}/{args.runs}...", flush=True)
            results.append(
                run_case(executable, output_dir, view, run, args.cpu)
            )

    write_csv(results, output_dir / "results.csv")
    write_markdown(results, output_dir / "results.md", args.runs, args.cpu)
    print(f"Wrote results to {output_dir}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, RuntimeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        sys.exit(1)
