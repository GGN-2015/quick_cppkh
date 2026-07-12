#!/usr/bin/env python3
"""Compare cppkh, quick_cppkh, and quick_cppkh_interface on a PD-code file."""

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
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / "benchmarks" / "zip_random_100.txt"
DEFAULT_INTERFACE_PACKAGE_PATH = ROOT / "python_project" / "quick_cppkh-interface"
INTERFACE_RUNNER = ROOT / "tools" / "benchmark_quick_cppkh_interface.py"


@dataclass(frozen=True)
class EngineSpec:
    name: str
    command: list[str]
    env: Optional[dict[str, str]] = None


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
    env: Optional[dict[str, str]] = None,
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
            env=env,
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


def engine_key(name: str) -> str:
    return re.sub(r"[^0-9A-Za-z]+", "_", name).strip("_").lower()


def display_label(name: str) -> str:
    return "quick_cppkh\ninterface" if name == "quick_cppkh_interface" else name


def engine_colors(names: Sequence[str]) -> list[str]:
    palette = {
        "cppkh": "#456990",
        "quick_cppkh": "#49a078",
        "quick_cppkh_interface": "#d9793d",
    }
    fallback = ["#7b6d8d", "#c44536", "#197278", "#6d597a"]
    colors = []
    for index, name in enumerate(names):
        colors.append(palette.get(name, fallback[index % len(fallback)]))
    return colors


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
    engines = summary.get("engines", {})
    if not isinstance(engines, dict):
        raise TypeError("summary['engines'] must be a dict")

    names = [str(name) for name in summary.get("engine_order", engines.keys())]
    colors = engine_colors(names)
    time_rows = [
        (name, float(engines[name]["median_seconds"]), colors[index])  # type: ignore[index]
        for index, name in enumerate(names)
    ]
    mem_rows = [
        (name, float(engines[name].get("median_peak_rss_mib") or 0.0), colors[index])  # type: ignore[index]
        for index, name in enumerate(names)
    ]
    max_time = max((value for _, value, _ in time_rows), default=1e-9)
    max_mem = max((value for _, value, _ in mem_rows), default=1e-9)
    max_time = max(max_time, 1e-9)
    max_mem = max(max_mem, 1e-9)
    width = 980
    height = 560
    left = 230
    bar_width = 640
    row_step = 48
    runtime_start = 134
    memory_start = runtime_start + len(names) * row_step + 72
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        f'<text x="32" y="42" font-family="Arial, sans-serif" font-size="24" font-weight="700">quick_cppkh benchmark ({int(summary.get("items", 0))} PD codes)</text>',
        '<text x="32" y="72" font-family="Arial, sans-serif" font-size="14" fill="#555">Median runtime and process-tree peak RSS, lower is better</text>',
        '<text x="32" y="112" font-family="Arial, sans-serif" font-size="18" font-weight="700">Runtime</text>',
    ]
    for index, (label, value, color) in enumerate(time_rows):
        y = runtime_start + index * row_step
        w = max(2, int(bar_width * value / max_time))
        parts.append(f'<text x="32" y="{y + 24}" font-family="Arial, sans-serif" font-size="18">{label}</text>')
        parts.append(f'<rect x="{left}" y="{y}" width="{w}" height="30" rx="3" fill="{color}"/>')
        parts.append(
            f'<text x="{left + w + 12}" y="{y + 21}" font-family="Arial, sans-serif" font-size="15">{value:.6f}s</text>'
        )
    parts.append(
        f'<text x="32" y="{memory_start - 28}" font-family="Arial, sans-serif" font-size="18" font-weight="700">Peak RSS</text>'
    )
    for index, (label, value, color) in enumerate(mem_rows):
        y = memory_start + index * row_step
        w = max(2, int(bar_width * value / max_mem))
        parts.append(f'<text x="32" y="{y + 24}" font-family="Arial, sans-serif" font-size="18">{label}</text>')
        parts.append(f'<rect x="{left}" y="{y}" width="{w}" height="30" rx="3" fill="{color}"/>')
        parts.append(
            f'<text x="{left + w + 12}" y="{y + 21}" font-family="Arial, sans-serif" font-size="15">{value:.3f} MiB</text>'
        )
    ratios = summary.get("speed_ratios_vs_cppkh", {})
    ratio_text = ", ".join(f"cppkh / {name} = {float(value):.3f}x" for name, value in ratios.items())
    parts.append(f'<text x="32" y="{height - 34}" font-family="Arial, sans-serif" font-size="16" fill="#222">{ratio_text}</text>')
    parts.append("</svg>")
    path.write_text("\n".join(parts) + "\n", encoding="utf-8")


def write_chart(path: Path, summary: dict[str, object]) -> None:
    try:
        import matplotlib.pyplot as plt  # type: ignore
    except Exception:
        write_svg_chart(path.with_suffix(".svg"), summary)
        return

    engines = summary.get("engines", {})
    if not isinstance(engines, dict):
        raise TypeError("summary['engines'] must be a dict")

    names = [str(name) for name in summary.get("engine_order", engines.keys())]
    labels = [display_label(name) for name in names]
    time_values = [float(engines[name]["median_seconds"]) for name in names]  # type: ignore[index]
    mem_values = [
        float(engines[name].get("median_peak_rss_mib") or 0.0)  # type: ignore[index]
        for name in names
    ]
    colors = engine_colors(names)
    fig, axes = plt.subplots(1, 2, figsize=(12.2, 4.8))
    for ax, values, xlabel, title, suffix, precision in [
        (axes[0], time_values, "median seconds", "Runtime", "s", 4),
        (axes[1], mem_values, "median peak RSS (MiB)", "Memory", " MiB", 2),
    ]:
        bars = ax.barh(labels, values, color=colors)
        ax.set_xlabel(xlabel)
        ax.set_title(title)
        ax.grid(axis="x", color="#dddddd", linewidth=0.8)
        ax.set_axisbelow(True)
        max_value = max(values) if values else 0.0
        ax.set_xlim(0, max_value * 1.22 if max_value > 0 else 1.0)
        ax.invert_yaxis()
        for bar, value in zip(bars, values):
            ax.text(
                value,
                bar.get_y() + bar.get_height() / 2,
                f"{value:.{precision}f}{suffix}",
                ha="left",
                va="center",
            )
    fig.suptitle(f"quick_cppkh benchmark ({int(summary.get('items', 0))} PD codes)")
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=160)
    plt.close(fig)


def quick_interface_command(args: argparse.Namespace, input_path: Path, cache_dir: Path) -> list[str]:
    command = [
        str(args.quick_interface_python),
        str(INTERFACE_RUNNER),
        "--input",
        str(input_path),
        "--cache-dir",
        str(cache_dir),
        "--threads",
        str(args.threads),
    ]
    package_path = Path(args.quick_interface_package_path) if args.quick_interface_package_path else None
    if package_path is not None:
        command.extend(["--package-path", str(package_path)])
    if args.quick_interface_cxx:
        command.extend(["--cxx", str(args.quick_interface_cxx)])
    if args.raw_pd:
        command.append("--raw-pd")
    return command


def prepare_quick_interface(command: list[str]) -> None:
    result = subprocess.run(
        [*command, "--compile-only"],
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        detail = "\n".join(part for part in [result.stdout.strip(), result.stderr.strip()] if part)
        raise RuntimeError(f"quick_cppkh_interface preparation failed:\n{detail}")


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

    base_args = ["--pd-file", str(input_path), "--quiet", "--threads", str(args.threads)]
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

    engines = [
        EngineSpec("cppkh", [str(cppkh_exe), *base_args]),
        EngineSpec(
            "quick_cppkh",
            [
                str(quick_exe),
                "--cppkh-exe",
                str(cppkh_exe),
                *base_args,
            ],
        ),
    ]
    if not args.skip_quick_interface:
        if not INTERFACE_RUNNER.exists():
            raise FileNotFoundError(f"quick_cppkh_interface benchmark runner not found: {INTERFACE_RUNNER}")
        cache_dir = Path(args.quick_interface_cache_dir) if args.quick_interface_cache_dir else out_dir / "quick_cppkh_interface_cache"
        interface_command = quick_interface_command(args, input_path, cache_dir)
        print("prepare: quick_cppkh_interface", flush=True)
        prepare_quick_interface(interface_command)
        engines.append(EngineSpec("quick_cppkh_interface", interface_command))

    runs: list[dict[str, object]] = []
    for repeat in range(args.repeat):
        for engine in engines:
            print(f"run {repeat + 1}/{args.repeat}: {engine.name}", flush=True)
            runs.append(
                run_once(
                    engine.name,
                    engine.command,
                    args.timeout_sec,
                    not args.no_memory,
                    args.memory_sample_interval,
                    engine.env,
                )
            )

    runs_by_engine = {
        engine.name: [run for run in runs if run["name"] == engine.name]
        for engine in engines
    }
    baseline_runs = runs_by_engine["cppkh"]
    engine_summaries: dict[str, dict[str, object]] = {}
    for engine in engines:
        engine_runs = runs_by_engine[engine.name]
        times = [float(run["seconds"]) for run in engine_runs]
        memory = [float(run["peak_rss_mib"]) for run in engine_runs if run["peak_rss_mib"] is not None]
        result_count = len(engine_runs[-1]["results"]) if engine_runs else 0
        if engine.name == "cppkh":
            outputs_match_cppkh = all(int(run["exit_code"]) == 0 for run in engine_runs)
        else:
            outputs_match_cppkh = all(
                int(run["exit_code"]) == 0
                and int(baseline_runs[index]["exit_code"]) == 0
                and run["results"] == baseline_runs[index]["results"]
                for index, run in enumerate(engine_runs)
                if index < len(baseline_runs)
            )
        engine_summaries[engine.name] = {
            "median_seconds": median(times),
            "best_seconds": min(times) if times else None,
            "median_peak_rss_mib": median(memory) if memory else None,
            "max_peak_rss_mib": max(memory) if memory else None,
            "result_count": result_count,
            "exit_codes": [run["exit_code"] for run in engine_runs],
            "outputs_match_cppkh": outputs_match_cppkh,
        }

    baseline_time = float(engine_summaries["cppkh"]["median_seconds"])
    baseline_memory = engine_summaries["cppkh"]["median_peak_rss_mib"]
    speed_ratios = {}
    memory_ratios = {}
    for engine in engines:
        if engine.name == "cppkh":
            continue
        engine_time = float(engine_summaries[engine.name]["median_seconds"])
        engine_memory = engine_summaries[engine.name]["median_peak_rss_mib"]
        speed_ratios[engine.name] = (baseline_time / engine_time) if engine_time > 0 else 0
        memory_ratios[engine.name] = (
            float(engine_memory) / float(baseline_memory)
            if engine_memory is not None and baseline_memory is not None and float(baseline_memory) > 0
            else None
        )
    match = all(bool(summary["outputs_match_cppkh"]) for summary in engine_summaries.values())

    summary: dict[str, object] = {
        "input": str(input_path),
        "items": sum(1 for line in input_path.read_text(encoding="utf-8").splitlines() if line.strip()),
        "repeat": args.repeat,
        "engine_order": [engine.name for engine in engines],
        "engines": engine_summaries,
        "speed_ratios_vs_cppkh": speed_ratios,
        "peak_rss_ratios_vs_cppkh": memory_ratios,
        "outputs_match": match,
    }
    for engine in engines:
        key = engine_key(engine.name)
        engine_summary = engine_summaries[engine.name]
        summary[f"{key}_median_seconds"] = engine_summary["median_seconds"]
        summary[f"{key}_best_seconds"] = engine_summary["best_seconds"]
        summary[f"{key}_median_peak_rss_mib"] = engine_summary["median_peak_rss_mib"]
        summary[f"{key}_max_peak_rss_mib"] = engine_summary["max_peak_rss_mib"]
        summary[f"{key}_result_count"] = engine_summary["result_count"]
    summary["cppkh_over_quick_speed_ratio"] = speed_ratios.get("quick_cppkh", 0)
    summary["quick_over_cppkh_peak_rss_ratio"] = memory_ratios.get("quick_cppkh")

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
    parser.add_argument("--threads", default="1", help="threads value passed to cppkh-compatible engines")
    parser.add_argument("--raw-pd", action="store_true", help="pass --no-simplify-pd to cppkh")
    parser.add_argument("--no-memory", action="store_true", help="disable peak RSS measurement")
    parser.add_argument(
        "--skip-quick-interface",
        action="store_true",
        help="omit quick_cppkh_interface from the benchmark",
    )
    parser.add_argument(
        "--quick-interface-python",
        default=sys.executable,
        help="Python executable used to run quick_cppkh_interface",
    )
    parser.add_argument(
        "--quick-interface-package-path",
        default=str(DEFAULT_INTERFACE_PACKAGE_PATH),
        help="local quick-cppkh-interface project path added to sys.path; pass an empty value to use an installed package",
    )
    parser.add_argument(
        "--quick-interface-cache-dir",
        default="",
        help="cache directory for quick_cppkh_interface compiled executables",
    )
    parser.add_argument(
        "--quick-interface-cxx",
        default=os.environ.get("CXX", ""),
        help="C++ compiler path passed to quick_cppkh_interface compilation",
    )
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
