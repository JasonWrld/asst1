# Program 1 Task 2 Results

View 1, native Windows Release build, `--simulate-myth4`.
Each executable invocation reports the minimum of five internal trials.

| Threads | Serial (ms) | Threaded (ms) | Speedup |
| ---: | ---: | ---: | ---: |
| 2 | 289.008 | 146.240 | 1.98x |
| 3 | 284.787 | 176.444 | 1.61x |
| 4 | 301.223 | 116.230 | 2.59x |
| 5 | 285.711 | 113.288 | 2.52x |
| 6 | 287.598 | 87.327 | 3.29x |
| 7 | 279.332 | 82.352 | 3.39x |
| 8 | 277.219 | 71.873 | 3.86x |

The speedup column is recomputed from the reported three-decimal timing values in `results.csv`.
