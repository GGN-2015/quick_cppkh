#!/usr/bin/env python3
"""Compare quick_cppkh and cppkh runtime and peak RSS on a PD-code file."""

from __future__ import annotations

import argparse
import csv
import json
import os
import platform
import re
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional, Sequence


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / "benchmarks" / "pd_codes.txt"


def host_platform() -> str:
    system = platform.system().lower()
    if os.name == "nt" or system.startswith("windows"):
        return "windows"
    if system == "darwin":
        return "macos"
    return "linux"


def exe_suffix() -> str:
    return ".exe" if host_platform() == "windows" else ""


def default_dist() -> Path:
    return ROOT / "dist" / host_platform()


def parse_results(text: str) -> list[str]:
    results: list[str] = []
    for line in text.splitlines():
        match = re.search(r'"([^"]*)"\s*$', line)
        if match:
            results.append(match.group(1))
    return results


def peak_rss_bytes(process: object) -> int:
    import psutil  # type: ignore

    try:
        processes = [process, *process.children(recursive=True)]  # type: ignore[attr-defined]
    except psutil.Error:
        processes = [process]
    total = 0
    for item in processes:
        try:
            total += int(item.memory_info().rss)
        except psutil.Error:
            continue
    return total


def terminate_process_tree(process: object) -> None:
    import psutil  # type: ignore

    try:
        children = process.children(recursive=True)  # type: ignore[attr-defined]
    except psutil.Error:
        children = []
    targets = children + [process]
    for item in targets:
        try:
            item.terminate()
        except psutil.Error:
            pass
    gone, alive = psutil.wait_procs(targets, timeout=2.0)
    del gone
    for item in alive:
        try:
            item.kill()
        except psutil.Error:
            pass


def run_once(
    name: str,
    command: list[str],
    timeout_sec: int,
    measure_memory: bool,
    sample_interval: float,
) -> dict[str, object]:
    psutil_process = None
    peak_rss = 0
    timed_out = False
    start = time.perf_counter()
    with tempfile.TemporaryFile(mode="w+b") as stdout_file, tempfile.TemporaryFile(mode="w+b") as stderr_file:
        proc = subprocess.Popen(
            command,
            cwd=str(ROOT),
            stdout=stdout_file,
            stderr=stderr_file,
        )
        if measure_memory:
            import psutil  # type: ignore

            psutil_process = psutil.Process(proc.pid)
        deadline = start + timeout_sec if timeout_sec > 0 else None
        while True:
            if measure_memory and psutil_process is not None:
                peak_rss = max(peak_rss, peak_rss_bytes(psutil_process))
            exit_code = proc.poll()
            now = time.perf_counter()
            if exit_code is not None:
                break
            if deadline is not None and now >= deadline:
                timed_out = True
                if psutil_process is not None:
                    terminate_process_tree(psutil_process)
                else:
                    proc.terminate()
                break
            time.sleep(max(0.001, sample_interval))

        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            if psutil_process is not None:
                terminate_process_tree(psutil_process)
            else:
                proc.kill()
            proc.wait()
        if measure_memory and psutil_process is not None:
            peak_rss = max(peak_rss, peak_rss_bytes(psutil_process))
        seconds = time.perf_counter() - start
        stdout_file.seek(0)
        stderr_file.seek(0)
        stdout = stdout_file.read().decode("utf-8", errors="replace")
        stderr = stderr_file.read().decode("utf-8", errors="replace")
    if timed_out:
        stderr += f"\n{name} timed out after {timeout_sec} seconds\n"
    return {
        "name": name,
        "seconds": seconds,
        "exit_code": 124 if timed_out else proc.returncode,
        "stdout": stdout,
        "stderr": stderr,
        "results": parse_results(stdout),
        "command": command,
        "peak_rss_bytes": peak_rss if measure_memory else None,
        "peak_rss_mib": (peak_rss / (1024.0 * 1024.0)) if measure_memory else None,
    }


def median(values: list[float]) -> float:
    return statistics.median(values) if values else 0.0


def write_csv(path: Path, runs: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["engine", "repeat", "seconds", "peak_rss_mib", "exit_code", "result_count"],
        )
        writer.writeheader()
        counters: dict[str, int] = {}
        for run in runs:
            name = str(run["name"])
            counters[name] = counters.get(name, 0) + 1
            writer.writerow(
                {
                    "engine": name,
                    "repeat": counters[name],
                    "seconds": f"{float(run['seconds']):.9f}",
                    "peak_rss_mib": (
                        "" if run["peak_rss_mib"] is None else f"{float(run['peak_rss_mib']):.6f}"
                    ),
                    "exit_code": run["exit_code"],
                    "result_count": len(run["results"]),
                }
            )


def write_svg_chart(path: Path, summary: dict[str, object]) -> None:
    cpp = float(summary["cppkh_median_seconds"])
    quick = float(summary["quick_cppkh_median_seconds"])
    cpp_mem = float(summary.get("cppkh_median_peak_rss_mib") or 0.0)
    quick_mem = float(summary.get("quick_cppkh_median_peak_rss_mib") or 0.0)
    max_time = max(cpp, quick, 1e-9)
    max_mem = max(cpp_mem, quick_mem, 1e-9)
    width = 820
    height = 470
    left = 180
    bar_width = 560
    time_rows = [("cppkh", cpp, "#456990"), ("quick_cppkh", quick, "#49a078")]
    mem_rows = [("cppkh", cpp_mem, "#456990"), ("quick_cppkh", quick_mem, "#49a078")]
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        '<text x="32" y="42" font-family="Arial, sans-serif" font-size="24" font-weight="700">quick_cppkh vs cppkh benchmark</text>',
        '<text x="32" y="72" font-family="Arial, sans-serif" font-size="14" fill="#555">Median runtime and process-tree peak RSS, lower is better</text>',
        '<text x="32" y="112" font-family="Arial, sans-serif" font-size="18" font-weight="700">Runtime</text>',
    ]
    for index, (label, value, color) in enumerate(time_rows):
        y = 132 + index * 54
        w = max(2, int(bar_width * value / max_time))
        parts.append(f'<text x="32" y="{y + 24}" font-family="Arial, sans-serif" font-size="18">{label}</text>')
        parts.append(f'<rect x="{left}" y="{y}" width="{w}" height="30" rx="3" fill="{color}"/>')
        parts.append(
            f'<text x="{left + w + 12}" y="{y + 21}" font-family="Arial, sans-serif" font-size="15">{value:.6f}s</text>'
        )
    parts.append('<text x="32" y="268" font-family="Arial, sans-serif" font-size="18" font-weight="700">Peak RSS</text>')
    for index, (label, value, color) in enumerate(mem_rows):
        y = 288 + index * 54
        w = max(2, int(bar_width * value / max_mem))
        parts.append(f'<text x="32" y="{y + 24}" font-family="Arial, sans-serif" font-size="18">{label}</text>')
        parts.append(f'<rect x="{left}" y="{y}" width="{w}" height="30" rx="3" fill="{color}"/>')
        parts.append(
            f'<text x="{left + w + 12}" y="{y + 21}" font-family="Arial, sans-serif" font-size="15">{value:.3f} MiB</text>'
        )
    speedup = float(summary["cppkh_over_quick_speed_ratio"])
    parts.append(
        f'<text x="32" y="438" font-family="Arial, sans-serif" font-size="16" fill="#222">runtime speed ratio: cppkh / quick_cppkh = {speedup:.3f}x</text>'
    )
    parts.append("</svg>")
    path.write_text("\n".join(parts) + "\n", encoding="utf-8")


def write_chart(path: Path, summary: dict[str, object]) -> None:
    try:
        import matplotlib.pyplot as plt  # type: ignore
    except Exception:
        write_svg_chart(path.with_suffix(".svg"), summary)
        return

    labels = ["cppkh", "quick_cppkh"]
    time_values = [float(summary["cppkh_median_seconds"]), float(summary["quick_cppkh_median_seconds"])]
    mem_values = [
        float(summary.get("cppkh_median_peak_rss_mib") or 0.0),
        float(summary.get("quick_cppkh_median_peak_rss_mib") or 0.0),
    ]
    colors = ["#456990", "#49a078"]
    fig, axes = plt.subplots(1, 2, figsize=(10.8, 4.4))
    for ax, values, ylabel, title, suffix, precision in [
        (axes[0], time_values, "median seconds", "Runtime", "s", 4),
        (axes[1], mem_values, "median peak RSS (MiB)", "Memory", " MiB", 2),
    ]:
        bars = ax.bar(labels, values, color=colors)
        ax.set_ylabel(ylabel)
        ax.set_title(title)
        ax.grid(axis="y", color="#dddddd", linewidth=0.8)
        ax.set_axisbelow(True)
        for bar, value in zip(bars, values):
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                value,
                f"{value:.{precision}f}{suffix}",
                ha="center",
                va="bottom",
            )
    fig.suptitle("quick_cppkh vs cppkh benchmark")
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=160)
    plt.close(fig)


def benchmark(args: argparse.Namespace) -> dict[str, object]:
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    input_path = Path(args.input).resolve()
    if not input_path.exists():
        raise FileNotFoundError(input_path)

    dist = default_dist()
    quick_exe = Path(args.quick_exe) if args.quick_exe else dist / ("quick_cppkh" + exe_suffix())
    cppkh_exe = Path(args.cppkh_exe) if args.cppkh_exe else dist / ("cppkh" + exe_suffix())
    if not quick_exe.exists():
        raise FileNotFoundError(f"quick_cppkh not found: {quick_exe}")
    if not cppkh_exe.exists():
        raise FileNotFoundError(f"cppkh not found: {cppkh_exe}")

    base_args = ["--pd-file", str(input_path), "--quiet"]
    if args.raw_pd:
        base_args.append("--no-simplify-pd")
    if not args.no_memory:
        try:
            import psutil  # noqa: F401  # type: ignore
        except Exception as exc:
            raise RuntimeError(
                "memory benchmarking requires psutil; install it with `python -m pip install psutil` "
                "or pass --no-memory"
            ) from exc

    runs: list[dict[str, object]] = []
    for repeat in range(args.repeat):
        print(f"run {repeat + 1}/{args.repeat}: cppkh", flush=True)
        runs.append(
            run_once(
                "cppkh",
                [str(cppkh_exe), *base_args],
                args.timeout_sec,
                not args.no_memory,
                args.memory_sample_interval,
            )
        )
        print(f"run {repeat + 1}/{args.repeat}: quick_cppkh", flush=True)
        runs.append(
            run_once(
                "quick_cppkh",
                [
                    str(quick_exe),
                    "--cppkh-exe",
                    str(cppkh_exe),
                    *base_args,
                ],
                args.timeout_sec,
                not args.no_memory,
                args.memory_sample_interval,
            )
        )

    cpp_runs = [run for run in runs if run["name"] == "cppkh"]
    quick_runs = [run for run in runs if run["name"] == "quick_cppkh"]
    cpp_times = [float(run["seconds"]) for run in cpp_runs]
    quick_times = [float(run["seconds"]) for run in quick_runs]
    cpp_memory = [float(run["peak_rss_mib"]) for run in cpp_runs if run["peak_rss_mib"] is not None]
    quick_memory = [float(run["peak_rss_mib"]) for run in quick_runs if run["peak_rss_mib"] is not None]
    cpp_results = cpp_runs[-1]["results"] if cpp_runs else []
    quick_results = quick_runs[-1]["results"] if quick_runs else []
    match = cpp_results == quick_results

    summary: dict[str, object] = {
        "input": str(input_path),
        "items": sum(1 for line in input_path.read_text(encoding="utf-8").splitlines() if line.strip()),
        "repeat": args.repeat,
        "cppkh_median_seconds": median(cpp_times),
        "quick_cppkh_median_seconds": median(quick_times),
        "cppkh_best_seconds": min(cpp_times) if cpp_times else None,
        "quick_cppkh_best_seconds": min(quick_times) if quick_times else None,
        "cppkh_median_peak_rss_mib": median(cpp_memory) if cpp_memory else None,
        "quick_cppkh_median_peak_rss_mib": median(quick_memory) if quick_memory else None,
        "cppkh_max_peak_rss_mib": max(cpp_memory) if cpp_memory else None,
        "quick_cppkh_max_peak_rss_mib": max(quick_memory) if quick_memory else None,
        "cppkh_over_quick_speed_ratio": (median(cpp_times) / median(quick_times)) if median(quick_times) > 0 else 0,
        "quick_over_cppkh_peak_rss_ratio": (
            median(quick_memory) / median(cpp_memory)
            if cpp_memory and quick_memory and median(cpp_memory) > 0
            else None
        ),
        "outputs_match": match,
        "cppkh_result_count": len(cpp_results),
        "quick_cppkh_result_count": len(quick_results),
    }

    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    write_csv(out_dir / "runs.csv", runs)
    write_chart(out_dir / "runtime.png", summary)

    for index, run in enumerate(runs, 1):
        stem = f"{run['name']}_{index}"
        (out_dir / f"{stem}.stdout").write_text(str(run["stdout"]), encoding="utf-8", errors="replace")
        (out_dir / f"{stem}.stderr").write_text(str(run["stderr"]), encoding="utf-8", errors="replace")

    print(json.dumps(summary, indent=2), flush=True)
    if not match:
        print("warning: outputs differ; inspect stdout files in " + str(out_dir), file=sys.stderr)
    return summary


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default=str(DEFAULT_INPUT), help="PD-code file")
    parser.add_argument("--quick-exe", default="", help="path to quick_cppkh executable")
    parser.add_argument("--cppkh-exe", default="", help="path to cppkh executable")
    parser.add_argument("--out-dir", default=str(ROOT / "benchmark" / "quick-vs-cppkh"), help="output directory")
    parser.add_argument("--repeat", type=int, default=3, help="number of repeats")
    parser.add_argument("--timeout-sec", type=int, default=0, help="per-run timeout, 0 disables")
    parser.add_argument("--raw-pd", action="store_true", help="pass --no-simplify-pd to cppkh")
    parser.add_argument("--no-memory", action="store_true", help="disable peak RSS measurement")
    parser.add_argument(
        "--memory-sample-interval",
        type=float,
        default=0.01,
        help="seconds between process-tree RSS samples",
    )
    args = parser.parse_args(argv)
    benchmark(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
