#!/usr/bin/env python3
"""Benchmark Program 4 Task 3 and generate dependency-free reports."""

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


MODES = ("random", "worst")
TOTAL_VALUES = 20_000_000
WORST_PERIOD = 8
HEAVY_INPUT_VALUE = 2.999999761581421
LIGHT_INPUT_VALUE = 1.0
HEAVY_INPUT_COUNT = TOTAL_VALUES // WORST_PERIOD
LIGHT_INPUT_COUNT = TOTAL_VALUES - HEAVY_INPUT_COUNT
WORST_PATTERN_TEXT = "1 heavy + 7 light per 8 values"

INPUT_MODE_PATTERN = re.compile(r"\[input mode\]:\s*\[(random|worst)\]")
WORST_PATTERN_PATTERN = re.compile(r"\[worst input pattern\]:\s*\[([^\]]+)\]")
WORST_PERIOD_PATTERN = re.compile(r"\[worst input period\]:\s*\[([0-9]+)\]")
HEAVY_VALUE_PATTERN = re.compile(
    r"\[heavy input value\]:\s*\[([0-9]+(?:\.[0-9]+)?)\]"
)
LIGHT_VALUE_PATTERN = re.compile(
    r"\[light input value\]:\s*\[([0-9]+(?:\.[0-9]+)?)\]"
)
HEAVY_COUNT_PATTERN = re.compile(r"\[heavy input count\]:\s*\[([0-9]+)\]")
LIGHT_COUNT_PATTERN = re.compile(r"\[light input count\]:\s*\[([0-9]+)\]")
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
            "Compare Program 4's random and worst-case SIMD inputs using "
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


def single_int(pattern: re.Pattern[str], output: str, label: str) -> int:
    return int(single_match(pattern, output, label))


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


def validate_worst_metadata(output: str, expected_mode: str) -> None:
    patterns: tuple[re.Pattern[str], ...] = (
        WORST_PATTERN_PATTERN,
        WORST_PERIOD_PATTERN,
        HEAVY_VALUE_PATTERN,
        LIGHT_VALUE_PATTERN,
        HEAVY_COUNT_PATTERN,
        LIGHT_COUNT_PATTERN,
    )
    if expected_mode == "random":
        if any(pattern.search(output) for pattern in patterns):
            raise RuntimeError("Random mode unexpectedly reported worst-case metadata.")
        return

    pattern_text = single_match(
        WORST_PATTERN_PATTERN, output, "worst input pattern"
    )
    period = single_int(WORST_PERIOD_PATTERN, output, "worst input period")
    heavy_value = single_float(HEAVY_VALUE_PATTERN, output, "heavy input value")
    light_value = single_float(LIGHT_VALUE_PATTERN, output, "light input value")
    heavy_count = single_int(HEAVY_COUNT_PATTERN, output, "heavy input count")
    light_count = single_int(LIGHT_COUNT_PATTERN, output, "light input count")

    if pattern_text != WORST_PATTERN_TEXT or period != WORST_PERIOD:
        raise RuntimeError("Worst mode did not report the required 1-of-8 pattern.")
    if not math.isclose(heavy_value, HEAVY_INPUT_VALUE, abs_tol=5e-9):
        raise RuntimeError("Worst mode reported an incorrect heavy input value.")
    if not math.isclose(light_value, LIGHT_INPUT_VALUE, abs_tol=1e-12):
        raise RuntimeError("Worst mode reported an incorrect light input value.")
    if heavy_count != HEAVY_INPUT_COUNT or light_count != LIGHT_INPUT_COUNT:
        raise RuntimeError("Worst mode reported incorrect heavy/light counts.")
    if heavy_count + light_count != TOTAL_VALUES:
        raise RuntimeError("Worst input counts do not cover the complete array.")


def parse_case(output: str, expected_mode: str) -> CaseResult:
    if "Error:" in output:
        raise RuntimeError("Program reported a result-verification error.")
    actual_mode = single_match(INPUT_MODE_PATTERN, output, "input mode")
    if actual_mode != expected_mode:
        raise RuntimeError(
            f"Expected input mode {expected_mode}, program reported {actual_mode}."
        )
    validate_worst_metadata(output, expected_mode)

    serial_ms = single_float(SERIAL_TIME_PATTERN, output, "serial timing")
    ispc_ms = single_float(ISPC_TIME_PATTERN, output, "ISPC timing")
    task_ms = single_float(TASK_TIME_PATTERN, output, "task ISPC timing")
    if min(serial_ms, ispc_ms, task_ms) <= 0.0:
        raise RuntimeError("All measured times must be positive.")

    result = CaseResult(expected_mode, serial_ms, ispc_ms, task_ms)
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
    command = [str(executable), "--simulate-myth4", "--input", mode]
    with tempfile.TemporaryDirectory(prefix=f"sqrt-task3-{mode}-") as work:
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
                "heavy_input_value",
                "light_input_value",
                "heavy_count",
                "light_count",
                "serial_ms",
                "ispc_ms",
                "task_ispc_ms",
                "simd_speedup",
                "multicore_speedup",
                "total_speedup",
            ]
        )
        for result in results:
            is_worst = result.mode == "worst"
            writer.writerow(
                [
                    result.mode,
                    f"{HEAVY_INPUT_VALUE:.9g}" if is_worst else "",
                    f"{LIGHT_INPUT_VALUE:.1f}" if is_worst else "",
                    HEAVY_INPUT_COUNT if is_worst else "",
                    LIGHT_INPUT_COUNT if is_worst else "",
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
    worst = by_mode["worst"]
    lines = [
        "# Program 4 Task 3 results",
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
            "## Worst/random speedup ratio",
            "",
            "| Metric | Worst / random |",
            "| --- | ---: |",
            f"| SIMD speedup | {worst.simd_speedup / random.simd_speedup:.2f}x |",
            f"| Multi-core speedup | {worst.multicore_speedup / random.multicore_speedup:.2f}x |",
            f"| Total speedup | {worst.total_speedup / random.total_speedup:.2f}x |",
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

    by_mode = {result.mode: result for result in results}
    if by_mode["worst"].simd_speedup >= 1.0:
        raise RuntimeError(
            "Worst input did not reduce the non-task ISPC speedup below 1.0."
        )

    write_results_csv(results, output_dir / "results.csv")
    write_results_markdown(results, output_dir / "results.md")
    print(f"Wrote Task 3 results to {output_dir}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)
