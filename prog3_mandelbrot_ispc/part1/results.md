# Program 3 Part 1 Results

Local WSL benchmark pinned to logical CPU 0. Each row is the minimum of three internal trials reported by one executable invocation.
The summary selects the minimum serial and ISPC times from 5 independent invocations.

## Summary

| View | Serial (ms) | ISPC (ms) | Speedup |
| ---: | ---: | ---: | ---: |
| 1 | 146.511 | 40.851 | 3.59x |
| 2 | 82.003 | 28.567 | 2.87x |

## All runs

| View | Run | Serial (ms) | ISPC (ms) | Speedup |
| ---: | ---: | ---: | ---: | ---: |
| 1 | 1 | 146.511 | 40.851 | 3.59x |
| 1 | 2 | 147.312 | 41.506 | 3.55x |
| 1 | 3 | 148.690 | 41.235 | 3.61x |
| 1 | 4 | 146.759 | 42.555 | 3.45x |
| 1 | 5 | 147.252 | 41.216 | 3.57x |
| 2 | 1 | 82.535 | 28.567 | 2.89x |
| 2 | 2 | 82.003 | 28.717 | 2.86x |
| 2 | 3 | 83.457 | 29.248 | 2.85x |
| 2 | 4 | 82.545 | 29.091 | 2.84x |
| 2 | 5 | 84.182 | 28.787 | 2.92x |

Speedups are recomputed from the three-decimal timing values; complete program output is preserved in `raw/`.
