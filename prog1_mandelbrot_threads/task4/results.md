# Program 1 Task 4 Results

Native Windows Release build with `--simulate-myth4`, `--decomposition interleaved`, and `--profile-workers`.
Each row uses the minimum of five internal serial and threaded trials; worker times come from the fastest threaded trial.

## View 1

| Threads | Serial (ms) | Threaded (ms) | Speedup | Min worker (ms) | Max worker (ms) | Imbalance |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 2 | 284.611 | 145.403 | 1.96x | 144.303 | 145.330 | 1.01x |
| 3 | 281.885 | 100.723 | 2.80x | 94.115 | 100.653 | 1.07x |
| 4 | 286.158 | 73.896 | 3.87x | 70.846 | 73.583 | 1.04x |
| 5 | 288.096 | 65.338 | 4.41x | 56.334 | 65.137 | 1.16x |
| 6 | 287.542 | 55.413 | 5.19x | 52.460 | 55.197 | 1.05x |
| 7 | 287.704 | 46.215 | 6.23x | 42.284 | 45.839 | 1.08x |
| 8 | 293.216 | 41.372 | 7.09x | 39.133 | 40.296 | 1.03x |

## View 2

| Threads | Serial (ms) | Threaded (ms) | Speedup | Min worker (ms) | Max worker (ms) | Imbalance |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 2 | 160.104 | 82.810 | 1.93x | 81.252 | 82.562 | 1.02x |
| 3 | 160.452 | 55.822 | 2.87x | 53.013 | 55.745 | 1.05x |
| 4 | 154.323 | 41.912 | 3.68x | 39.415 | 41.471 | 1.05x |
| 5 | 161.133 | 39.173 | 4.11x | 32.284 | 38.458 | 1.19x |
| 6 | 159.456 | 32.681 | 4.88x | 27.040 | 32.550 | 1.20x |
| 7 | 165.881 | 29.904 | 5.55x | 24.698 | 29.770 | 1.21x |
| 8 | 171.862 | 26.123 | 6.58x | 24.421 | 25.738 | 1.05x |

## Final 8-thread result

| View | Speedup | Worker imbalance |
| ---: | ---: | ---: |
| 1 | 7.09x | 1.03x |
| 2 | 6.58x | 1.05x |
