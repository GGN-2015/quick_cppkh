# Benchmarks

`quick_cppkh` compares both wall-clock runtime and peak resident memory against
the upstream `cppkh` executable and the Python `quick_cppkh_interface` package.
Memory is measured as peak RSS over the full process tree, so the wrapper rows
include their launcher process and any live child processes from racing routes.

## Local Run

Machine-local run on Windows, 2026-07-12:

- Compiler: WinLibs GCC 16.1.0 x86_64 UCRT POSIX SEH.
- `cppkh` upstream: `GGN-2015/cppkh` main at `37b3cc3`.
- Python interface: local `python_project/quick_cppkh-interface` source tree.
- Input: `benchmarks/zip_random_100.txt`, the complete deterministic 100-sample
  zip-random fixture from `cpp-pd-code-simplify` (seed `20260708`, source
  diagrams limited to at most 150 crossings).
- Repeats: 5.
- Memory sampler: `psutil`, process-tree RSS sampled every 0.01 seconds.
- Command:

```sh
python -m pip install matplotlib psutil quick-cppkh-interface
python tools/benchmark.py --input benchmarks/zip_random_100.txt --repeat 5 --out-dir benchmark/quick-vs-cppkh-zip-random-100
```

| Engine | Median time | Best time | Median peak RSS | Max peak RSS | Results | Compare |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| `cppkh` | 14.831043s | 14.748593s | 88.23 MiB | 88.25 MiB | 100 | OK |
| `quick_cppkh` | 14.904007s | 14.771155s | 117.89 MiB | 117.93 MiB | 100 | OK |
| `quick_cppkh_interface` | 15.065402s | 15.025070s | 150.79 MiB | 152.34 MiB | 100 | OK |

Runtime ratios: `cppkh / quick_cppkh = 0.995104x`,
`cppkh / quick_cppkh_interface = 0.984444x`; lower runtime is better.
Peak RSS ratios: `quick_cppkh / cppkh = 1.336182x`,
`quick_cppkh_interface / cppkh = 1.709112x`; lower memory is better.

![quick_cppkh runtime and memory chart](assets/quick_vs_cppkh_zip_random_100.png)

Raw files:

- [summary JSON](assets/quick_vs_cppkh_zip_random_100_summary.json)
- [per-run CSV](assets/quick_vs_cppkh_zip_random_100_runs.csv)

## Notes

The small smoke dataset in `benchmarks/pd_codes.txt` is useful for checking
correctness but is not a good speedup demonstration: each `cppkh` computation
is already so short that the extra process scheduling overhead dominates.

The 100-sample zip-random fixture measures the broad corpus rather than the
former five-case optimization subset. On this dataset the race is approximately
runtime-neutral: many diagrams finish on the direct route before external
simplification can provide a useful lead, while running both routes increases
peak RSS. `quick_cppkh_interface` exercises the same racing computation through
the Python package, so its timing and memory include Python startup and API-layer
overhead.
