# Program 4 Task 3 results

| Input | Serial (ms) | ISPC (ms) | Task ISPC (ms) | SIMD speedup | Multi-core speedup | Total speedup |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| random | 720.994 | 182.825 | 47.516 | 3.94x | 3.85x | 15.17x |
| worst | 568.940 | 678.372 | 165.115 | 0.84x | 4.11x | 3.45x |

## Worst/random speedup ratio

| Metric | Worst / random |
| --- | ---: |
| SIMD speedup | 0.21x |
| Multi-core speedup | 1.07x |
| Total speedup | 0.23x |

Each timing is the minimum of three trials performed internally by the executable.
