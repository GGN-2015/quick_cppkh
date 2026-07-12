# Benchmarks

`quick_cppkh` compares both wall-clock runtime and peak resident memory. Memory
is measured as peak RSS over the full process tree, so the `quick_cppkh` row
includes the wrapper and any live child processes from both racing routes.

## Local Run

Machine-local run on Windows, 2026-07-12:

- Compiler: WinLibs GCC 16.1.0 x86_64 UCRT POSIX SEH.
- `cppkh` upstream: `GGN-2015/cppkh` main at `37b3cc3`.
- Input: `benchmarks/zip_random_selected.txt`, 5 selected zip-random PD codes
  from the `cpp-pd-code-simplify` benchmark fixture.
- Repeats: 5.
- Memory sampler: `psutil`, process-tree RSS sampled every 0.01 seconds.
- Command:

```sh
python -m pip install matplotlib psutil
python tools/benchmark.py --input benchmarks/zip_random_selected.txt --repeat 5 --out-dir benchmark/quick-vs-cppkh-zip-selected
```

| Engine | Median time | Best time | Median peak RSS | Max peak RSS | Results | Compare |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| `cppkh` | 2.056645s | 2.027057s | 86.87 MiB | 86.90 MiB | 5 | OK |
| `quick_cppkh` | 0.577455s | 0.572184s | 33.41 MiB | 33.51 MiB | 5 | OK |

Runtime ratio `cppkh / quick_cppkh = 3.561567x`, lower runtime is better.
Peak RSS ratio `quick_cppkh / cppkh = 0.384567x`, lower memory is better.

![quick_cppkh vs cppkh runtime and memory chart](assets/quick_vs_cppkh_zip_selected.png)

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
