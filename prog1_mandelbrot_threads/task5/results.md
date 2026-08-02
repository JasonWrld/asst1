# Program 1 Task 5 Results

Native Windows Release build with `--simulate-myth4`, `--decomposition interleaved`, and `--profile-workers`.
Both thread counts use exactly the same four P-cores and eight SMT hardware contexts.
For 16 threads, worker wall-clock timings include time descheduled by Windows and therefore do not measure row cost alone.

| View | Threads | Serial (ms) | Threaded (ms) | Speedup | Worker imbalance | Mapping |
| ---: | ---: | ---: | ---: | ---: | ---: | :--- |
| 1 | 8 | 303.209 | 40.551 | 7.48x | 1.01x | individual contexts |
| 1 | 16 | 282.949 | 77.209 | 3.66x | 2.93x | Windows scheduler |
| 2 | 8 | 157.145 | 23.846 | 6.59x | 1.01x | individual contexts |
| 2 | 16 | 160.406 | 42.864 | 3.74x | 2.70x | Windows scheduler |

## 16-thread change relative to 8 threads

| View | 8-thread time (ms) | 16-thread time (ms) | Relative performance | Time change |
| ---: | ---: | ---: | ---: | ---: |
| 1 | 40.551 | 77.209 | 0.525x | +90.4% |
| 2 | 23.846 | 42.864 | 0.556x | +79.8% |
