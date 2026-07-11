#!/usr/bin/env python3
"""Compare quick_cppkh and cppkh wall-clock runtime on a PD-code file."""

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


def run_once(name: str, command: list[str], timeout_sec: int) -> dict[str, object]:
    start = time.perf_counter()
    proc = subprocess.run(
        command,
        cwd=str(ROOT),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout_sec if timeout_sec > 0 else None,
        check=False,
    )
    seconds = time.perf_counter() - start
    return {
        "name": name,
        "seconds": seconds,
        "exit_code": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "results": parse_results(proc.stdout),
        "command": command,
    }


def median(values: list[float]) -> float:
    return statistics.median(values) if values else 0.0


def write_csv(path: Path, runs: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["engine", "repeat", "seconds", "exit_code", "result_count"])
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
                    "exit_code": run["exit_code"],
                    "result_count": len(run["results"]),
                }
            )


def write_svg_chart(path: Path, summary: dict[str, object]) -> None:
    cpp = float(summary["cppkh_median_seconds"])
    quick = float(summary["quick_cppkh_median_seconds"])
    max_value = max(cpp, quick, 1e-9)
    width = 820
    height = 310
    left = 180
    bar_width = 560
    rows = [("cppkh", cpp, "#456990"), ("quick_cppkh", quick, "#49a078")]
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        '<text x="32" y="42" font-family="Arial, sans-serif" font-size="24" font-weight="700">Runtime comparison</text>',
        '<text x="32" y="72" font-family="Arial, sans-serif" font-size="14" fill="#555">Median wall-clock seconds, lower is better</text>',
    ]
    for index, (label, value, color) in enumerate(rows):
        y = 120 + index * 72
        w = max(2, int(bar_width * value / max_value))
        parts.append(f'<text x="32" y="{y + 24}" font-family="Arial, sans-serif" font-size="18">{label}</text>')
        parts.append(f'<rect x="{left}" y="{y}" width="{w}" height="34" rx="3" fill="{color}"/>')
        parts.append(
            f'<text x="{left + w + 12}" y="{y + 23}" font-family="Arial, sans-serif" font-size="16">{value:.6f}s</text>'
        )
    speedup = float(summary["cppkh_over_quick_speed_ratio"])
    parts.append(
        f'<text x="32" y="278" font-family="Arial, sans-serif" font-size="16" fill="#222">cppkh / quick_cppkh = {speedup:.3f}x</text>'
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
    values = [float(summary["cppkh_median_seconds"]), float(summary["quick_cppkh_median_seconds"])]
    colors = ["#456990", "#49a078"]
    fig, ax = plt.subplots(figsize=(8.2, 4.2))
    bars = ax.bar(labels, values, color=colors)
    ax.set_ylabel("median seconds")
    ax.set_title("quick_cppkh vs cppkh runtime")
    ax.grid(axis="y", color="#dddddd", linewidth=0.8)
    ax.set_axisbelow(True)
    for bar, value in zip(bars, values):
        ax.text(bar.get_x() + bar.get_width() / 2, value, f"{value:.4f}s", ha="center", va="bottom")
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

    runs: list[dict[str, object]] = []
    for repeat in range(args.repeat):
        print(f"run {repeat + 1}/{args.repeat}: cppkh", flush=True)
        runs.append(run_once("cppkh", [str(cppkh_exe), *base_args], args.timeout_sec))
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
            )
        )

    cpp_runs = [run for run in runs if run["name"] == "cppkh"]
    quick_runs = [run for run in runs if run["name"] == "quick_cppkh"]
    cpp_times = [float(run["seconds"]) for run in cpp_runs]
    quick_times = [float(run["seconds"]) for run in quick_runs]
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
        "cppkh_over_quick_speed_ratio": (median(cpp_times) / median(quick_times)) if median(quick_times) > 0 else 0,
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
    args = parser.parse_args(argv)
    benchmark(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
