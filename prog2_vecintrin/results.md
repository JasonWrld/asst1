# Program 2 Results

The vectorized `clampedExpVector` implementation was tested with an input size of
10,000 while sweeping `VECTOR_WIDTH` through 2, 4, 8, and 16. For each run, the
width was changed in `CS149intrin.h`, followed by a clean rebuild and execution:

```sh
make clean
make
./myexp -s 10000
```

| Vector width | Total vector instructions | Vector utilization | Utilized lanes | Total lanes | Verification |
| ---: | ---: | ---: | ---: | ---: | :--- |
| 2 | 172727 | 81.2% | 280369 | 345454 | Passed |
| 4 | 99575 | 73.7% | 293585 | 398300 | Passed |
| 8 | 54127 | 69.8% | 302273 | 433016 | Passed |
| 16 | 28217 | 68.0% | 306905 | 451472 | Passed |

Vector utilization decreases as the vector width increases. Each input exponent
determines how many multiply iterations its lane needs. A vector loop must keep
running until the lane with the largest exponent finishes, while lanes with
smaller exponents become inactive under the loop mask. Wider vectors are more
likely to contain lanes with different exponents and a larger gap between their
iteration counts, so more lanes remain masked during later iterations. This
control-flow divergence lowers average lane utilization even though wider
vectors reduce the total number of vector instructions.

The implementation was also tested at the default `VECTOR_WIDTH=4` with `N=3`
and `N=17`. Both non-multiple sizes passed, confirming that the final partial
vector is handled by the valid-lane mask without changing out-of-range output.
The final `VECTOR_WIDTH` setting was restored to 4.
