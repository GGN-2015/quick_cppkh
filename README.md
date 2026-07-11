# quick_cppkh

`quick_cppkh` is a small C++17 command-line accelerator for
[`cppkh`](https://github.com/GGN-2015/cppkh). It keeps the `cppkh` user
interface and races two computation routes:

1. Run `cppkh` directly on the original PD code.
2. Run [`pd_simplify`](https://github.com/GGN-2015/cpp-pd-code-simplify), then
   run `cppkh --no-simplify-pd` on the simplified PD code.

Whichever route returns a successful Khovanov homology result first wins; the
other route is terminated and the winning stdout/stderr is returned.

## Build

Use the Python build script. It downloads or reuses the two upstream projects,
builds `cppkh`, `pd_simplify`, and `quick_cppkh`, then stages them together in
`dist/<platform>`.

```sh
python tools/build.py
```

Useful options:

```sh
python tools/build.py --cxx /path/to/g++
python tools/build.py --portable
python tools/build.py --clean --clean-deps
```

The output executable is:

- Windows: `dist/windows/quick_cppkh.exe`
- Linux: `dist/linux/quick_cppkh`
- macOS: `dist/macos/quick_cppkh`

## Usage

Use the same input style as `cppkh`:

```sh
quick_cppkh --pd-code "PD[X[1,5,2,4],X[3,1,4,6],X[5,3,6,2]]"
quick_cppkh --pd-file benchmarks/pd_codes.txt --quiet
quick_cppkh --pd-dir samples
```

If the dependency executables are not beside `quick_cppkh`, pass them directly
or set environment variables:

```sh
quick_cppkh --cppkh-exe /path/to/cppkh --pd-simplify-exe /path/to/pd_simplify --pd-file input.pd
```

```sh
QUICK_CPPKH_CPPKH=/path/to/cppkh \
QUICK_CPPKH_PD_SIMPLIFY=/path/to/pd_simplify \
quick_cppkh --pd-file input.pd
```

## Benchmarks

The default smoke benchmark is intentionally tiny and mostly measures process
overhead. The optimization benchmark uses selected zip-random PD codes from the
`cpp-pd-code-simplify` benchmark corpus where external simplification reduces
the Khovanov workload.

```sh
python tools/benchmark.py --input benchmarks/zip_random_selected.txt --repeat 5
```

Local Windows result from this repository:

- `cppkh` median: `3.059245s`
- `quick_cppkh` median: `0.771843s`
- Speed ratio: `3.964x`
- Output comparison: OK

See [docs/BENCHMARKS.md](docs/BENCHMARKS.md) for the chart, raw timing files,
and reproduction notes.
