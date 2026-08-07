# Program 5: BLAS saxpy

The original Linux/WSL build remains available through `make`. A native
Windows x64 Release build is provided for measurements on a hybrid Intel CPU,
where Windows can distinguish P-cores from E-cores.

## Native Windows Release build

Requirements:

- Visual Studio 2022 Build Tools with the MSVC x64 toolchain and CMake
- Intel ISPC 1.28.1 for Windows
- An AVX2-capable x86-64 processor

Configure the project from a Developer Command Prompt. `ispc.exe` may be on
`PATH`, or its location can be supplied explicitly:

```powershell
$buildDir = Join-Path $env:TEMP "saxpy-native-build"
cmake -S . -B $buildDir -G "Visual Studio 17 2022" -A x64 `
  -DISPC_EXECUTABLE=C:/tools/ispc-v1.28.1-windows/bin/ispc.exe
cmake --build $buildDir --config Release
```

The executable is generated at
`%TEMP%\saxpy-native-build\Release\saxpy.exe`.

Run without topology restrictions:

```powershell
& "$buildDir/Release/saxpy.exe"
```

Run with the Stanford myth-like 4P/8SMT topology:

```powershell
& "$buildDir/Release/saxpy.exe" --simulate-myth4
```

The simulation mode selects four physical Windows P-cores and both SMT
contexts on each core, restricts the process to those eight CPU Sets, pins the
serial and single-core ISPC references to one context, and limits the Windows
Concurrency Runtime to eight concurrency resources. The 64 ISPC tasks remain
work items scheduled within those eight contexts.

The mode fails instead of silently using other processors if Windows cannot
distinguish performance cores or cannot provide four P-cores with two SMT
contexts each.

These are local measurements under a topology restriction, not results from a
Stanford `myth` host. CPU model, cache hierarchy, memory system, operating
system, and task runtime still differ.

## Linux/WSL build

```bash
make ISPC=/path/to/ispc
./saxpy
```

`--simulate-myth4` intentionally fails on Linux/WSL because the virtual CPU
topology does not reliably identify the host P-cores.
