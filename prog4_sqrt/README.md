# Program 4: Iterative sqrt

The original Linux/WSL build remains available through `make`. A native
Windows x64 Release build is provided for measurements on a hybrid Intel CPU,
where Windows can distinguish P-cores from E-cores.

## Native Windows Release build

Requirements:

- Visual Studio 2022 Build Tools with the MSVC x64 toolchain and CMake
- Intel ISPC 1.28.1 for Windows

Configure the project from a Developer Command Prompt. `ispc.exe` may be on
`PATH`, or its location can be supplied explicitly:

```powershell
$buildDir = Join-Path $env:TEMP "sqrt-native-build"
cmake -S . -B $buildDir -G "Visual Studio 17 2022" -A x64 `
  -DISPC_EXECUTABLE=C:/tools/ispc-v1.28.1-windows/bin/ispc.exe
cmake --build $buildDir --config Release
```

The executable is generated at `%TEMP%\sqrt-native-build\Release\sqrt.exe`.

Run without topology restrictions:

```powershell
& "$buildDir/Release/sqrt.exe"
```

Run with the Stanford myth-like 4P/8SMT topology:

```powershell
& "$buildDir/Release/sqrt.exe" --simulate-myth4
```

The starter random distribution remains the default. Program 4 Tasks 2 and 3
add explicit best- and worst-case SIMD distributions:

```powershell
& "$buildDir/Release/sqrt.exe" --simulate-myth4 --input random
& "$buildDir/Release/sqrt.exe" --simulate-myth4 --input best
& "$buildDir/Release/sqrt.exe" --simulate-myth4 --input worst
```

`--input best` fills the array with the largest representable `float` below
3.0. This keeps every SIMD lane and ISPC task equally busy while avoiding the
non-convergent value 3.0.

`--input worst` places one such high-work value and seven `1.0f` values in
every group of eight inputs. The single high-work lane keeps each AVX2 gang in
the varying loop after the other seven lanes have become inactive.

The simulation mode selects four physical Windows P-cores and both SMT
contexts on each core, restricts the process to those eight CPU Sets, pins the
serial and single-core ISPC references to one context, and limits the Windows
Concurrency Runtime to eight concurrency resources. The 64 ISPC tasks remain
work items scheduled within those eight contexts.

These are local measurements under a topology restriction, not results from a
Stanford `myth` host. CPU model, cache hierarchy, memory system, operating
system, and task runtime still differ.

## Linux/WSL build

```bash
make ISPC=/path/to/ispc
./sqrt
./sqrt --input random
./sqrt --input best
./sqrt --input worst
```

`--simulate-myth4` intentionally fails on Linux/WSL because the virtual CPU
topology does not reliably identify the host P-cores.
