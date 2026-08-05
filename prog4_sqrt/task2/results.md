# Program 4 Task 2 results

| Input | Serial (ms) | ISPC (ms) | Task ISPC (ms) | SIMD speedup | Multi-core speedup | Total speedup |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| random | 677.752 | 174.201 | 46.828 | 3.89x | 3.72x | 14.47x |
| best | 3329.612 | 695.005 | 169.671 | 4.79x | 4.10x | 19.62x |

## Best/random improvement

| Metric | Improvement factor |
| --- | ---: |
| SIMD speedup | 1.23x |
| Multi-core speedup | 1.10x |
| Total speedup | 1.36x |

Each timing is the minimum of three trials performed internally by the executable.
