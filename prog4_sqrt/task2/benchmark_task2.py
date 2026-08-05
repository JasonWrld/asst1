#!/usr/bin/env python3
"""Benchmark Program 4 Task 2 and generate dependency-free reports."""

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


MODES = ("random", "best")
BEST_INPUT_VALUE = 2.999999761581421
INPUT_MODE_PATTERN = re.compile(r"\[input mode\]:\s*\[(random|best)\]")
UNIFORM_VALUE_PATTERN = re.compile(
    r"\[uniform input value\]:\s*\[([0-9]+(?:\.[0-9]+)?)\]"
)
SERIAL_TIME_PATTERN = re.compile(
    r"\[sqrt serial\]:\s*\[([0-9]+(?:\.[0-9]+)?)\]\s*ms"
)
ISPC_TIME_PATTERN = re.compile(
    r"\[sqrt ispc\]:\s*\[([0-9]+(?:\.[0-9]+)?)\]\s*ms"
)
TASK_TIME_PATTERN = re.compile(
    r"\[sqrt task ispc\]:\s*\[([0-9]+(?:\.[0-9]+)?)\]\s*ms"
)
ISPC_SPEEDUP_PATTERN = re.compile(
    r"\(([0-9]+(?:\.[0-9]+)?)x speedup from ISPC\)"
)
TASK_SPEEDUP_PATTERN = re.compile(
    r"\(([0-9]+(?:\.[0-9]+)?)x speedup from task ISPC\)"
)
CONTEXT_PATTERN = re.compile(
    r"Context ([0-7]) -> CPU Set ([0-9]+), Group ([0-9]+), "
    r"Logical CPU ([0-9]+), CoreIndex ([0-9]+), EfficiencyClass ([0-9]+)"
)
REFERENCE_PATTERN = re.compile(
    r"Serial/ISPC reference -> CPU Set ([0-9]+), Group ([0-9]+), "
    r"Logical CPU ([0-9]+), CoreIndex ([0-9]+)"
)


@dataclass(frozen=True)
class CaseResult:
    mode: str
    input_value: float | None
    serial_ms: float
    ispc_ms: float
    task_ms: float

    @property
    def simd_speedup(self) -> float:
        return self.serial_ms / self.ispc_ms

    @property
    def multicore_speedup(self) -> float:
        return self.ispc_ms / self.task_ms

    @property
    def total_speedup(self) -> float:
        return self.serial_ms / self.task_ms


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Compare Program 4's random and uniform best-case inputs using "
            "the native Windows Release executable and myth4 affinity."
        )
    )
    parser.add_argument(
        "--executable",
        required=True,
        type=Path,
        help="Path to the native Windows Release sqrt executable.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).resolve().parent,
        help="Output directory (default: directory containing this script).",
    )
    return parser.parse_args()


def single_match(pattern: re.Pattern[str], output: str, label: str) -> str:
    matches = pattern.findall(output)
    if len(matches) != 1:
        raise RuntimeError(
            f"Expected exactly one {label} in program output, found {len(matches)}."
        )
    match = matches[0]
    if isinstance(match, tuple):
        raise RuntimeError(f"Internal parser error for {label}.")
    return match


def single_float(pattern: re.Pattern[str], output: str, label: str) -> float:
    return float(single_match(pattern, output, label))


def validate_affinity(output: str) -> None:
    if "Selected myth4 physical P-cores: 4" not in output:
        raise RuntimeError("Program did not report exactly four selected P-cores.")
    if "Selected myth4 SMT contexts: 8" not in output:
        raise RuntimeError("Program did not report exactly eight SMT contexts.")
    if "Configured ConCRT concurrency resources: 8" not in output:
        raise RuntimeError("ConCRT was not configured for eight resources.")

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
    if len({item[1] for item in contexts}) != 8:
        raise RuntimeError("The myth4 context list contains duplicate CPU Set IDs.")
    physical_cores = [(item[2], item[4]) for item in contexts]
    if len(set(physical_cores)) != 4:
        raise RuntimeError("The myth4 context list does not use exactly four cores.")
    if any(physical_cores.count(core) != 2 for core in set(physical_cores)):
        raise RuntimeError("Each selected P-core must contribute two SMT contexts.")
    if len({item[5] for item in contexts}) != 1:
        raise RuntimeError("Selected contexts do not share one P-core efficiency class.")

    references = REFERENCE_PATTERN.findall(output)
    if len(references) != 1 or int(references[0][0]) != contexts[0][1]:
        raise RuntimeError("Serial/ISPC reference was not assigned to context 0.")


def parse_case(output: str, expected_mode: str) -> CaseResult:
    if "Error:" in output:
        raise RuntimeError("Program reported a result-verification error.")
    actual_mode = single_match(INPUT_MODE_PATTERN, output, "input mode")
    if actual_mode != expected_mode:
        raise RuntimeError(
            f"Expected input mode {expected_mode}, program reported {actual_mode}."
        )

    uniform_values = UNIFORM_VALUE_PATTERN.findall(output)
    input_value: float | None = None
    if expected_mode == "best":
        if len(uniform_values) != 1:
            raise RuntimeError("Best mode did not report one uniform input value.")
        input_value = float(uniform_values[0])
        if not math.isclose(input_value, BEST_INPUT_VALUE, abs_tol=5e-9):
            raise RuntimeError(
                "Best mode did not use the largest representable float below 3.0."
            )
        if not input_value < 3.0:
            raise RuntimeError("Best input must remain strictly below 3.0.")
    elif uniform_values:
        raise RuntimeError("Random mode unexpectedly reported a uniform input value.")

    serial_ms = single_float(SERIAL_TIME_PATTERN, output, "serial timing")
    ispc_ms = single_float(ISPC_TIME_PATTERN, output, "ISPC timing")
    task_ms = single_float(TASK_TIME_PATTERN, output, "task ISPC timing")
    if min(serial_ms, ispc_ms, task_ms) <= 0.0:
        raise RuntimeError("All measured times must be positive.")

    result = CaseResult(
        expected_mode, input_value, serial_ms, ispc_ms, task_ms
    )
    reported_ispc = single_float(
        ISPC_SPEEDUP_PATTERN, output, "reported ISPC speedup"
    )
    reported_task = single_float(
        TASK_SPEEDUP_PATTERN, output, "reported task ISPC speedup"
    )
    if not math.isclose(reported_ispc, result.simd_speedup, abs_tol=0.02):
        raise RuntimeError("Reported ISPC speedup is inconsistent with timings.")
    if not math.isclose(reported_task, result.total_speedup, abs_tol=0.02):
        raise RuntimeError("Reported task ISPC speedup is inconsistent with timings.")
    if not math.isclose(
        result.simd_speedup * result.multicore_speedup,
        result.total_speedup,
        rel_tol=1e-12,
    ):
        raise RuntimeError("SIMD and multi-core speedups do not compose.")
    return result


def run_case(executable: Path, mode: str, raw_dir: Path) -> CaseResult:
    command = [
        str(executable),
        "--simulate-myth4",
        "--input",
        mode,
    ]
    with tempfile.TemporaryDirectory(prefix=f"sqrt-task2-{mode}-") as work:
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
    raw_path = raw_dir / f"{mode}.txt"
    raw_path.write_text(output, encoding="utf-8", newline="\n")
    if completed.returncode != 0:
        raise RuntimeError(
            f"Input mode {mode} exited with code {completed.returncode}; "
            f"see raw/{raw_path.name}."
        )
    validate_affinity(output)
    return parse_case(output, mode)


def write_results_csv(results: list[CaseResult], destination: Path) -> None:
    with destination.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(
            [
                "input_mode",
                "uniform_input_value",
                "serial_ms",
                "ispc_ms",
                "task_ispc_ms",
                "simd_speedup",
                "multicore_speedup",
                "total_speedup",
            ]
        )
        for result in results:
            writer.writerow(
                [
                    result.mode,
                    "" if result.input_value is None else f"{result.input_value:.9g}",
                    f"{result.serial_ms:.3f}",
                    f"{result.ispc_ms:.3f}",
                    f"{result.task_ms:.3f}",
                    f"{result.simd_speedup:.4f}",
                    f"{result.multicore_speedup:.4f}",
                    f"{result.total_speedup:.4f}",
                ]
            )


def write_results_markdown(
    results: list[CaseResult], destination: Path
) -> None:
    by_mode = {result.mode: result for result in results}
    random = by_mode["random"]
    best = by_mode["best"]
    lines = [
        "# Program 4 Task 2 results",
        "",
        "| Input | Serial (ms) | ISPC (ms) | Task ISPC (ms) | SIMD speedup | Multi-core speedup | Total speedup |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for result in results:
        lines.append(
            f"| {result.mode} | {result.serial_ms:.3f} | {result.ispc_ms:.3f} | "
            f"{result.task_ms:.3f} | {result.simd_speedup:.2f}x | "
            f"{result.multicore_speedup:.2f}x | {result.total_speedup:.2f}x |"
        )
    lines.extend(
        [
            "",
            "## Best/random improvement",
            "",
            "| Metric | Improvement factor |",
            "| --- | ---: |",
            f"| SIMD speedup | {best.simd_speedup / random.simd_speedup:.2f}x |",
            f"| Multi-core speedup | {best.multicore_speedup / random.multicore_speedup:.2f}x |",
            f"| Total speedup | {best.total_speedup / random.total_speedup:.2f}x |",
            "",
            "Each timing is the minimum of three trials performed internally by the executable.",
        ]
    )
    destination.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def main() -> int:
    args = parse_args()
    executable = args.executable.expanduser().resolve()
    if not executable.is_file():
        raise RuntimeError(f"Executable does not exist: {executable}")

    output_dir = args.output_dir.expanduser().resolve()
    raw_dir = output_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    results: list[CaseResult] = []
    for mode in MODES:
        print(f"Running {mode} input...", flush=True)
        results.append(run_case(executable, mode, raw_dir))

    write_results_csv(results, output_dir / "results.csv")
    write_results_markdown(results, output_dir / "results.md")
    print(f"Wrote Task 2 results to {output_dir}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)
