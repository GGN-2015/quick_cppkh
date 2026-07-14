#!/usr/bin/env python3
"""Build quick_cppkh and its two tracked command-line dependencies."""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Optional, Sequence


ROOT = Path(__file__).resolve().parents[1]

CPPKH_REVISION = "ff0489e7763f727a798bcd3fac808534ab4d35f5"
SIMPLIFY_REVISION = "0869536e85ae018ef5b8fb1cd2e150f5560969e3"
CPPKH_SOURCE = ROOT / "third_party" / "cppkh"
SIMPLIFY_SOURCE = ROOT / "third_party" / "cpp-pd-code-simplify"


def host_platform() -> str:
    system = platform.system().lower()
    if os.name == "nt" or system.startswith("windows"):
        return "windows"
    if system == "darwin":
        return "macos"
    return "linux"


def exe_suffix() -> str:
    return ".exe" if host_platform() == "windows" else ""


def default_cxx() -> str:
    env = os.environ.get("CXX")
    if env:
        return env
    for candidate in ("c++", "g++", "clang++"):
        found = shutil.which(candidate)
        if found:
            return found
    if host_platform() == "windows":
        for candidate in ("g++", "clang++"):
            found = shutil.which(candidate)
            if found:
                return found
    return "c++"


def run(command: Sequence[str], cwd: Path = ROOT) -> None:
    print("+ " + " ".join(str(part) for part in command), flush=True)
    subprocess.run([str(part) for part in command], cwd=str(cwd), check=True)


def ensure_clean_dir(path: Path, clean: bool) -> None:
    if clean and path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def require_vendored_source(source: Path, required: Sequence[str], revision: str) -> Path:
    missing = [rel for rel in required if not (source / rel).is_file()]
    if missing:
        details = "\n".join(f"  - {source / rel}" for rel in missing)
        raise SystemExit(
            f"Tracked dependency snapshot {revision[:7]} is incomplete. Missing:\n{details}"
        )
    return source


def common_flags(cxx_standard: str, debug: bool, warnings: bool = False) -> list[str]:
    flags = [f"-std={cxx_standard}"]
    if warnings:
        flags.extend(["-Wall", "-Wextra", "-Wpedantic"])
    flags.extend(["-O0", "-g"] if debug else ["-O3", "-DNDEBUG"])
    return flags


def maybe_native_flags(portable: bool) -> list[str]:
    if portable:
        return []
    return ["-march=native"]


def thread_libs() -> list[str]:
    return [] if host_platform() == "windows" else ["-pthread"]


def compiler_dir(cxx: str) -> Optional[Path]:
    found = shutil.which(cxx)
    if found:
        return Path(found).resolve().parent
    path = Path(cxx)
    if path.exists():
        return path.resolve().parent
    return None


def copy_windows_runtime_dlls(cxx: str, out_dir: Path) -> None:
    if host_platform() != "windows":
        return
    directory = compiler_dir(cxx)
    if directory is None:
        return
    candidates = [
        "libstdc++-6.dll",
        "libwinpthread-1.dll",
        "libgcc_s_seh-1.dll",
        "libgcc_s_dw2-1.dll",
        "libgcc_s_sjlj-1.dll",
    ]
    for name in candidates:
        source = directory / name
        if source.exists():
            shutil.copy2(source, out_dir / name)


def compile_cppkh(cxx: str, source: Path, out_dir: Path, debug: bool, portable: bool) -> Path:
    output = out_dir / ("cppkh" + exe_suffix())
    flags = common_flags("c++14", debug) + maybe_native_flags(portable)
    command = [cxx, *flags, str(source / "src" / "main.cpp"), "-o", str(output), *thread_libs()]
    run(command)
    return output


def compile_simplify(cxx: str, source: Path, out_dir: Path, debug: bool, portable: bool) -> Path:
    output = out_dir / ("pd_simplify" + exe_suffix())
    flags = common_flags("c++17", debug, warnings=True) + ["-I", str(source / "include")] + maybe_native_flags(portable)
    command = [cxx, *flags, str(source / "src" / "main.cpp"), "-o", str(output), *thread_libs()]
    run(command)
    return output


def compile_quick(cxx: str, out_dir: Path, debug: bool, portable: bool) -> Path:
    output = out_dir / ("quick_cppkh" + exe_suffix())
    flags = common_flags("c++17", debug, warnings=True) + maybe_native_flags(portable)
    command = [cxx, *flags, str(ROOT / "src" / "main.cpp"), "-o", str(output), *thread_libs()]
    run(command)
    return output


def build(args: argparse.Namespace) -> None:
    out_dir = Path(args.out_dir) if args.out_dir else ROOT / "dist" / host_platform()
    ensure_clean_dir(out_dir, args.clean)

    cppkh_source = require_vendored_source(
        CPPKH_SOURCE,
        ["src/main.cpp", "LICENSE"],
        CPPKH_REVISION,
    )
    simplify_source = require_vendored_source(
        SIMPLIFY_SOURCE,
        ["src/main.cpp", "include/pdcode_simplify/pdcode_simplify.hpp", "LICENSE"],
        SIMPLIFY_REVISION,
    )

    cppkh = compile_cppkh(args.cxx, cppkh_source, out_dir, args.debug, args.portable)
    simplify = compile_simplify(args.cxx, simplify_source, out_dir, args.debug, args.portable)
    quick = compile_quick(args.cxx, out_dir, args.debug, args.portable)
    copy_windows_runtime_dlls(args.cxx, out_dir)

    print("\nBuilt:")
    print(f"  quick_cppkh : {quick}")
    print(f"  cppkh       : {cppkh}")
    print(f"  pd_simplify : {simplify}")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cxx", default=default_cxx(), help="C++ compiler command")
    parser.add_argument("--out-dir", default="", help="output directory; default dist/<platform>")
    parser.add_argument("--debug", action="store_true", help="build with -O0 -g")
    parser.add_argument("--portable", action="store_true", help="do not add -march=native")
    parser.add_argument("--clean", action="store_true", help="remove the output directory before building")
    args = parser.parse_args(argv)
    build(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
