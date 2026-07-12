# Benchmarks

`quick_cppkh` follows the timing-only style of the upstream `cppkh` benchmark
notes here. Memory is intentionally not compared because the quick route runs
extra processes by design.

## Local Run

Machine-local run on Windows, 2026-07-12:

- Compiler: WinLibs GCC 16.1.0 x86_64 UCRT POSIX SEH.
- `cppkh` upstream: `GGN-2015/cppkh` main at `37b3cc3`.
- Input: `benchmarks/zip_random_selected.txt`, 5 selected zip-random PD codes
  from the `cpp-pd-code-simplify` benchmark fixture.
- Repeats: 5.
- Command:

```sh
python tools/benchmark.py --input benchmarks/zip_random_selected.txt --repeat 5 --out-dir benchmark/quick-vs-cppkh-zip-selected
```

| Engine | Median time | Best time | Results | Compare |
| --- | ---: | ---: | ---: | --- |
| `cppkh` | 2.096003s | 1.911956s | 5 | OK |
| `quick_cppkh` | 0.578577s | 0.572170s | 5 | OK |

`cppkh / quick_cppkh = 3.622687x`, lower runtime is better.

![quick_cppkh vs cppkh runtime chart](assets/quick_vs_cppkh_zip_selected.png)

Raw files:

- [summary JSON](assets/quick_vs_cppkh_zip_selected_summary.json)
- [per-run CSV](assets/quick_vs_cppkh_zip_selected_runs.csv)

## Notes

The small smoke dataset in `benchmarks/pd_codes.txt` is useful for checking
correctness but is not a good speedup demonstration: each `cppkh` computation
is already so short that the extra process scheduling overhead dominates.

The selected zip-random dataset is the intended optimization benchmark. These
PD codes are large enough that the external `pd_simplify` route can reduce the
diagram before Khovanov computation, while the direct `cppkh` route remains the
fallback if simplification does not help.
