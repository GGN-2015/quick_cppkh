# Algorithm Manual

`quick_cppkh` does not implement a new Khovanov homology algorithm. It is a
process-level scheduler around two existing C++ executables:

- `cppkh`: computes integral Khovanov homology from PD code.
- `pd_simplify`: simplifies PD code before homology computation.

The goal is to reduce wall-clock time when external PD simplification produces
a much smaller diagram, while preserving `cppkh` as the compatibility baseline.

## Inputs And CLI Contract

The public interface follows `cppkh`:

```sh
quick_cppkh --pd-code "PD[...]"
quick_cppkh --pd-file input.pd
quick_cppkh --pd-dir samples
```

The wrapper forwards normal `cppkh` options to the direct route. Input options
are also forwarded to `pd_simplify` so the simplify route sees the same source
PD codes.

cppkh `0.2.1` simplification controls are supported directly:

```sh
--simplify-r1 / --no-simplify-r1
--simplify-nugatory / --no-simplify-nugatory
```

They configure the direct route. The simplify-then-compute route removes these
flags before its final cppkh call and appends `--no-simplify-pd`, because that
route has already completed a stronger external diagram simplification.

Tool-location options are wrapper-specific:

```sh
quick_cppkh --cppkh-exe /path/to/cppkh --pd-simplify-exe /path/to/pd_simplify --pd-file input.pd
```

Environment equivalents:

```sh
QUICK_CPPKH_CPPKH=/path/to/cppkh
QUICK_CPPKH_PD_SIMPLIFY=/path/to/pd_simplify
```

## Computation Routes

For Khovanov homology output, `quick_cppkh` starts two routes concurrently.

### Route 1: Direct

```text
input PD code -> cppkh -> homology output
```

This route is the compatibility baseline. It receives the original command-line
arguments, except for wrapper-only tool-location options.

### Route 2: Simplify Then Compute

```text
input PD code -> pd_simplify --json -> final_pd_code -> cppkh --no-simplify-pd -> homology output
```

`pd_simplify` emits JSON. `quick_cppkh` extracts every `final_pd_code`, writes
those codes to a temporary `.pd` file, then asks `cppkh` to compute homology on
that already-simplified file with `--no-simplify-pd`.

Passing `--no-simplify-pd` on the second `cppkh` call is intentional: the
external simplifier has already normalized the diagram, and avoiding duplicate
internal simplification keeps the simplified route closer to the measured
algorithmic branch.

## Winner Selection

The first route that exits successfully with code `0` wins.

When a winner is selected:

1. `quick_cppkh` marks the other route as canceled.
2. The losing child process is terminated.
3. The winner's captured stdout and stderr are replayed to the caller.
4. The wrapper exits with the winner's exit code.

If both routes fail, `quick_cppkh` prints diagnostics for both branches and
returns a nonzero exit code.

## Direct-Only Output Modes

Some `cppkh` options do not request homology:

- `--print-simplified-pd`
- `--print-crossing-signs`
- `--help`

These modes are passed directly to `cppkh`. They are not raced through
`pd_simplify`, because simplifying externally first would change the object
being printed.

## Correctness Assumption

For homology output, the scheduler relies on the invariant property that both
routes compute Khovanov homology of equivalent diagrams:

- Direct route: original diagram handled by `cppkh`.
- Simplified route: diagram reduced by `pd_simplify`, then computed by `cppkh`.

The benchmark script checks output equality between `cppkh`, `quick_cppkh`, and
`quick_cppkh_interface` for the selected benchmark dataset. A mismatch is
treated as a benchmark failure signal, not as a tolerated performance artifact.

The Python package also tracks the cppkh-interface `0.2.1` API additions. Mixed
`de_r1`/`de_k8` settings use raw cppkh with the independent native switches,
while the default `(True, True)` setting keeps the two-route quick race.
`compute_signed_variants` uses cppkh's shared-library C API and bypasses diagram
simplification, matching upstream semantics.

## Process And Platform Notes

The wrapper is implemented in C++17 with platform APIs:

- Windows: `CreateProcessW`, anonymous pipes, and `TerminateProcess`.
- Linux/macOS: `fork`, `execvp`, pipes, process groups, `SIGTERM`, then
  `SIGKILL` if needed.

Windows process creation is serialized inside the wrapper. This avoids a pipe
handle inheritance race where one concurrent branch could inherit the other
branch's pipe writer and prevent EOF detection.

## Performance Characteristics

The direct route is best when the input diagram is already small or when
external simplification does not reduce Khovanov workload enough to pay for
process overhead.

The simplify route is best when:

- `pd_simplify` quickly removes many crossings.
- The simplified diagram has much lower Khovanov complexity.
- The direct `cppkh` route would spend most time on the unreduced diagram.

Because both routes start at the same time, `quick_cppkh` pays extra process
overhead and may briefly use more total process resources. The benchmark
therefore measures peak RSS over the full process tree, not just the wrapper
process.
