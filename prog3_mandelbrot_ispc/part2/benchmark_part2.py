#!/usr/bin/env python3
"""Sweep ISPC task counts and benchmark the selected final configuration."""

from __future__ import annotations

import argparse
import csv
import math
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path


DEFAULT_TASK_COUNTS = (2, 4, 5, 8, 10, 16, 20, 25, 32, 40, 50, 80,
                       100, 160, 200, 400, 800)
ISPC_FLAGS = ("-O3", "--target=avx2-i32x8", "--arch=x86-64",
              "--opt=disable-fma", "--pic")
TASK_COUNT_PATTERN = re.compile(r"uniform int numTasks = ([0-9]+);")
SERIAL_PATTERN = re.compile(
    r"\[mandelbrot serial\]:\s*\[([0-9]+(?:\.[0-9]+)?)\]\s*ms"
)
ISPC_PATTERN = re.compile(
    r"\[mandelbrot ispc\]:\s*\[([0-9]+(?:\.[0-9]+)?)\]\s*ms"
)
TASK_PATTERN = re.compile(
    r"\[mandelbrot multicore ispc\]:\s*\[([0-9]+(?:\.[0-9]+)?)\]\s*ms"
)
ISPC_SPEEDUP_PATTERN = re.compile(
    r"\(([0-9]+(?:\.[0-9]+)?)x speedup from ISPC\)"
)
TASK_SPEEDUP_PATTERN = re.compile(
    r"\(([0-9]+(?:\.[0-9]+)?)x speedup from task ISPC\)"
)


@dataclass(frozen=True)
class Trial:
    task_count: int
    view: int
    run: int
    serial_ms: float
    ispc_ms: float
    task_ms: float

    @property
    def serial_speedup(self) -> float:
        return self.serial_ms / self.task_ms

    @property
    def ispc_speedup(self) -> float:
        return self.ispc_ms / self.task_ms


@dataclass(frozen=True)
class Summary:
    task_count: int
    serial_ms: float
    ispc_ms: float
    task_ms: float

    @property
    def serial_speedup(self) -> float:
        return self.serial_ms / self.task_ms

    @property
    def ispc_speedup(self) -> float:
        return self.ispc_ms / self.task_ms


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("sweep", "final"), default="sweep")
    parser.add_argument(
        "--ispc", type=Path,
        help="ISPC compiler path; required in sweep mode.",
    )
    parser.add_argument(
        "--executable", type=Path,
        help="Built mandelbrot_ispc path; required in final mode.",
    )
    parser.add_argument(
        "--final-task-count", type=int,
        help="Expected source task count; required in final mode.",
    )
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument(
        "--task-counts", type=int, nargs="+", default=DEFAULT_TASK_COUNTS,
    )
    parser.add_argument(
        "--repo-dir", type=Path,
        default=Path(__file__).resolve().parent.parent,
    )
    parser.add_argument(
        "--output-dir", type=Path,
        default=Path(__file__).resolve().parent,
    )
    return parser.parse_args()


def require_one(pattern: re.Pattern[str], output: str, label: str) -> float:
    matches = pattern.findall(output)
    if len(matches) != 1:
        raise RuntimeError(
            f"Expected one {label}, found {len(matches)} in program output."
        )
    return float(matches[0])


def parse_trial(output: str, task_count: int, view: int, run: int) -> Trial:
    trial = Trial(
        task_count,
        view,
        run,
        require_one(SERIAL_PATTERN, output, "serial timing"),
        require_one(ISPC_PATTERN, output, "ISPC timing"),
        require_one(TASK_PATTERN, output, "task timing"),
    )
    printed_ispc = require_one(ISPC_SPEEDUP_PATTERN, output, "ISPC speedup")
    printed_task = require_one(TASK_SPEEDUP_PATTERN, output, "task speedup")
    if min(trial.serial_ms, trial.ispc_ms, trial.task_ms) <= 0.0:
        raise RuntimeError("All measured times must be positive.")
    if abs(trial.serial_ms / trial.ispc_ms - printed_ispc) > 0.02:
        raise RuntimeError("Printed ISPC speedup disagrees with timings.")
    if abs(trial.serial_speedup - printed_task) > 0.02:
        raise RuntimeError("Printed task speedup disagrees with timings.")
    return trial


def run_checked(command: list[str], cwd: Path, label: str) -> None:
    completed = subprocess.run(
        command, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"{label} failed:\n{completed.stdout}")


def current_task_count(source: str) -> int:
    matches = TASK_COUNT_PATTERN.findall(source)
    if len(matches) != 1:
        raise RuntimeError(
            "mandelbrot.ispc must contain exactly one uniform numTasks assignment."
        )
    return int(matches[0])


def validate_task_counts(task_counts: list[int] | tuple[int, ...]) -> list[int]:
    values = sorted(set(task_counts))
    if not values or any(value <= 0 or 800 % value != 0 for value in values):
        raise ValueError("Every task count must be a positive divisor of 800.")
    return values


def build_support_objects(repo_dir: Path, ispc: Path) -> None:
    run_checked(
        ["make", f"ISPC={ispc}"], repo_dir, "support-object build"
    )


def compile_variant(
    repo_dir: Path,
    ispc: Path,
    source_text: str,
    source_default: int,
    task_count: int,
    variant_dir: Path,
) -> Path:
    variant_dir.mkdir(parents=True)
    variant_source = variant_dir / "mandelbrot.ispc"
    replacement = f"uniform int numTasks = {task_count};"
    old_assignment = f"uniform int numTasks = {source_default};"
    replaced = source_text.replace(old_assignment, replacement, 1)
    if replaced == source_text and task_count != source_default:
        raise RuntimeError("Could not replace the task-count assignment.")
    variant_source.write_text(replaced, encoding="utf-8", newline="\n")

    variant_object = variant_dir / "mandelbrot_ispc.o"
    run_checked(
        [str(ispc), *ISPC_FLAGS, str(variant_source), "-o", str(variant_object)],
        repo_dir,
        f"ISPC compilation for {task_count} tasks",
    )

    executable = variant_dir / "mandelbrot_ispc"
    objects = [
        repo_dir / "objs/main.o",
        repo_dir / "objs/mandelbrotSerial.o",
        variant_object,
        repo_dir / "objs/ppm.o",
        repo_dir / "objs/tasksys.o",
    ]
    command = [
        "g++", "-m64", f"-I{repo_dir.parent / 'common'}",
        f"-I{repo_dir / 'objs'}", "-O3", "-Wall", "-fPIC",
        "-ffp-contract=off", "-o", str(executable),
        *(str(path) for path in objects), "-lm", "-lpthread",
    ]
    run_checked(command, repo_dir, f"link for {task_count} tasks")
    return executable


def run_trial(
    executable: Path,
    raw_path: Path,
    task_count: int,
    view: int,
    run: int,
) -> Trial:
    with tempfile.TemporaryDirectory(
        prefix=f"mandelbrot-t{task_count}-v{view}-r{run}-"
    ) as work:
        completed = subprocess.run(
            [str(executable), "--tasks", "--view", str(view)],
            cwd=work,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    raw_path.write_text(completed.stdout, encoding="utf-8", newline="\n")
    if completed.returncode != 0:
        raise RuntimeError(
            f"Tasks {task_count}, view {view}, run {run} exited with "
            f"{completed.returncode}; see {raw_path}."
        )
    if "differs from sequential output" in completed.stdout:
        raise RuntimeError(f"Correctness verification failed; see {raw_path}.")
    return parse_trial(completed.stdout, task_count, view, run)


def summarize(trials: list[Trial]) -> list[Summary]:
    summaries = []
    for task_count in sorted({trial.task_count for trial in trials}):
        group = [trial for trial in trials if trial.task_count == task_count]
        summaries.append(
            Summary(
                task_count,
                min(trial.serial_ms for trial in group),
                min(trial.ispc_ms for trial in group),
                min(trial.task_ms for trial in group),
            )
        )
    return summaries


def select_task_count(summaries: list[Summary]) -> tuple[int, float]:
    fastest_ms = min(summary.task_ms for summary in summaries)
    eligible = [
        summary.task_count for summary in summaries
        if summary.task_ms <= fastest_ms * 1.01
    ]
    return min(eligible), fastest_ms


def write_trials_csv(trials: list[Trial], destination: Path) -> None:
    with destination.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow([
            "task_count", "view", "run", "serial_ms", "ispc_ms", "task_ms",
            "serial_speedup", "ispc_speedup",
        ])
        for trial in trials:
            writer.writerow([
                trial.task_count, trial.view, trial.run,
                f"{trial.serial_ms:.3f}", f"{trial.ispc_ms:.3f}",
                f"{trial.task_ms:.3f}", f"{trial.serial_speedup:.4f}",
                f"{trial.ispc_speedup:.4f}",
            ])


def write_summary_csv(summaries: list[Summary], destination: Path) -> None:
    with destination.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow([
            "task_count", "serial_ms", "ispc_ms", "task_ms",
            "serial_speedup", "ispc_speedup",
        ])
        for summary in summaries:
            writer.writerow([
                summary.task_count, f"{summary.serial_ms:.3f}",
                f"{summary.ispc_ms:.3f}", f"{summary.task_ms:.3f}",
                f"{summary.serial_speedup:.4f}",
                f"{summary.ispc_speedup:.4f}",
            ])


def write_summary_markdown(
    summaries: list[Summary], selected: int, fastest_ms: float, destination: Path
) -> None:
    lines = [
        "# Program 3 Part 2 Task-Count Sweep",
        "",
        "View 1 on local WSL; each timing is the minimum across five "
        "independent invocations, each of which internally runs three trials.",
        "",
        "| Tasks | Serial (ms) | ISPC (ms) | Task ISPC (ms) | "
        "vs serial | vs ISPC |",
        "| ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for summary in summaries:
        marker = " **(selected)**" if summary.task_count == selected else ""
        lines.append(
            f"| {summary.task_count}{marker} | {summary.serial_ms:.3f} | "
            f"{summary.ispc_ms:.3f} | {summary.task_ms:.3f} | "
            f"{summary.serial_speedup:.2f}x | {summary.ispc_speedup:.2f}x |"
        )
    lines.extend([
        "",
        f"Absolute fastest task time: {fastest_ms:.3f} ms. Selected default: "
        f"{selected} tasks (smallest count within 1% of the fastest time).",
        "",
    ])
    destination.write_text("\n".join(lines), encoding="utf-8", newline="\n")


def write_svg(summaries: list[Summary], selected: int, destination: Path) -> None:
    width, height = 960, 620
    left, right, top, bottom = 92, 34, 58, 92
    plot_width = width - left - right
    plot_height = height - top - bottom
    x_min = math.log2(summaries[0].task_count)
    x_max = math.log2(summaries[-1].task_count)
    y_max = max(34.0, max(item.serial_speedup for item in summaries) * 1.08)

    def x_pos(task_count: int) -> float:
        return left + (math.log2(task_count) - x_min) * plot_width / (x_max - x_min)

    def y_pos(speedup: float) -> float:
        return top + (y_max - speedup) * plot_height / y_max

    points = " ".join(
        f"{x_pos(item.task_count):.2f},{y_pos(item.serial_speedup):.2f}"
        for item in summaries
    )
    elements = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="white"/>',
        '<text x="480" y="32" text-anchor="middle" font-family="sans-serif" font-size="22">View 1 task-count sweep</text>',
    ]
    for tick in range(0, int(y_max) + 1, 5):
        y = y_pos(float(tick))
        elements.append(f'<line x1="{left}" y1="{y:.2f}" x2="{width-right}" y2="{y:.2f}" stroke="#dddddd"/>')
        elements.append(f'<text x="{left-12}" y="{y+5:.2f}" text-anchor="end" font-family="sans-serif" font-size="13">{tick}x</text>')
    target_y = y_pos(32.0)
    elements.append(f'<line x1="{left}" y1="{target_y:.2f}" x2="{width-right}" y2="{target_y:.2f}" stroke="#777" stroke-dasharray="8 6"/>')
    for item in summaries:
        x = x_pos(item.task_count)
        elements.append(f'<text x="{x:.2f}" y="{height-bottom+25}" text-anchor="middle" transform="rotate(45 {x:.2f} {height-bottom+25})" font-family="sans-serif" font-size="12">{item.task_count}</text>')
    elements.append(f'<polyline points="{points}" fill="none" stroke="#1565c0" stroke-width="3"/>')
    for item in summaries:
        color = "#d32f2f" if item.task_count == selected else "#1565c0"
        radius = 6 if item.task_count == selected else 4
        elements.append(f'<circle cx="{x_pos(item.task_count):.2f}" cy="{y_pos(item.serial_speedup):.2f}" r="{radius}" fill="{color}"/>')
    elements.extend([
        f'<line x1="{left}" y1="{top}" x2="{left}" y2="{height-bottom}" stroke="black"/>',
        f'<line x1="{left}" y1="{height-bottom}" x2="{width-right}" y2="{height-bottom}" stroke="black"/>',
        f'<text x="{width/2}" y="{height-20}" text-anchor="middle" font-family="sans-serif" font-size="16">ISPC tasks (log scale)</text>',
        f'<text x="22" y="{height/2}" text-anchor="middle" transform="rotate(-90 22 {height/2})" font-family="sans-serif" font-size="16">Speedup over serial</text>',
        '</svg>',
        '',
    ])
    destination.write_text("\n".join(elements), encoding="utf-8", newline="\n")


def sweep(args: argparse.Namespace, repo_dir: Path, output_dir: Path) -> None:
    if args.ispc is None:
        raise ValueError("--ispc is required in sweep mode.")
    ispc = args.ispc.expanduser().resolve()
    if not ispc.is_file():
        raise FileNotFoundError(f"ISPC compiler not found: {ispc}")
    task_counts = validate_task_counts(args.task_counts)
    source_path = repo_dir / "mandelbrot.ispc"
    source_text = source_path.read_text(encoding="utf-8")
    source_default = current_task_count(source_text)
    build_support_objects(repo_dir, ispc)

    raw_dir = output_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    for stale in raw_dir.glob("sweep_tasks_*_run_*.txt"):
        stale.unlink()

    trials: list[Trial] = []
    with tempfile.TemporaryDirectory(prefix="mandelbrot-part2-sweep-") as temp:
        temp_root = Path(temp)
        for task_count in task_counts:
            executable = compile_variant(
                repo_dir, ispc, source_text, source_default, task_count,
                temp_root / f"tasks_{task_count}",
            )
            for run in range(1, args.runs + 1):
                print(
                    f"Sweep: {task_count} tasks, run {run}/{args.runs}",
                    flush=True,
                )
                trials.append(run_trial(
                    executable,
                    raw_dir / f"sweep_tasks_{task_count}_run_{run}.txt",
                    task_count, 1, run,
                ))

    summaries = summarize(trials)
    selected, fastest_ms = select_task_count(summaries)
    write_trials_csv(trials, output_dir / "sweep_trials.csv")
    write_summary_csv(summaries, output_dir / "sweep_results.csv")
    write_summary_markdown(
        summaries, selected, fastest_ms, output_dir / "sweep_results.md"
    )
    write_svg(summaries, selected, output_dir / "task_count_speedup.svg")
    (output_dir / "selected_task_count.txt").write_text(
        f"{selected}\n", encoding="utf-8", newline="\n"
    )
    print(f"Selected task count: {selected}")


def final_benchmark(
    args: argparse.Namespace, repo_dir: Path, output_dir: Path
) -> None:
    if args.executable is None or args.final_task_count is None:
        raise ValueError(
            "--executable and --final-task-count are required in final mode."
        )
    executable = args.executable.expanduser().resolve()
    if not executable.is_file():
        raise FileNotFoundError(f"Executable not found: {executable}")
    source_count = current_task_count(
        (repo_dir / "mandelbrot.ispc").read_text(encoding="utf-8")
    )
    if source_count != args.final_task_count or 800 % source_count != 0:
        raise RuntimeError(
            "Final source task count does not match the requested valid count."
        )

    raw_dir = output_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    for stale in raw_dir.glob("final_view*_run_*.txt"):
        stale.unlink()
    trials = []
    for view in (1, 2):
        for run in range(1, args.runs + 1):
            print(f"Final: view {view}, run {run}/{args.runs}", flush=True)
            trials.append(run_trial(
                executable,
                raw_dir / f"final_view{view}_run_{run}.txt",
                source_count, view, run,
            ))
    write_trials_csv(trials, output_dir / "final_trials.csv")

    lines = [
        "# Program 3 Part 2 Final Results", "",
        f"Default configuration: {source_count} tasks.", "",
        "| View | Serial (ms) | ISPC (ms) | Task ISPC (ms) | "
        "vs serial | vs ISPC |",
        "| ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for view in (1, 2):
        group = [trial for trial in trials if trial.view == view]
        summary = Summary(
            source_count,
            min(trial.serial_ms for trial in group),
            min(trial.ispc_ms for trial in group),
            min(trial.task_ms for trial in group),
        )
        lines.append(
            f"| {view} | {summary.serial_ms:.3f} | {summary.ispc_ms:.3f} | "
            f"{summary.task_ms:.3f} | {summary.serial_speedup:.2f}x | "
            f"{summary.ispc_speedup:.2f}x |"
        )
    lines.append("")
    (output_dir / "final_results.md").write_text(
        "\n".join(lines), encoding="utf-8", newline="\n"
    )


def main() -> int:
    args = parse_args()
    if args.runs <= 0:
        raise ValueError("--runs must be positive.")
    repo_dir = args.repo_dir.expanduser().resolve()
    output_dir = args.output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    if args.mode == "sweep":
        sweep(args, repo_dir, output_dir)
    else:
        final_benchmark(args, repo_dir, output_dir)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, RuntimeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        sys.exit(1)
