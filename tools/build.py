#!/usr/bin/env python3
"""Build quick_cppkh and its two upstream command-line dependencies."""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path
from typing import Iterable, Optional, Sequence


ROOT = Path(__file__).resolve().parents[1]

CPPKH_REPO = "https://github.com/GGN-2015/cppkh"
CPPKH_REVISION = "ff0489e7763f727a798bcd3fac808534ab4d35f5"
SIMPLIFY_REPO = "https://github.com/GGN-2015/cpp-pd-code-simplify"


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


def request_url(url: str, destination: Path, timeout: int = 120) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": "quick_cppkh-build"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        destination.write_bytes(response.read())


def download_archive(repo: str, destination: Path, revision: str = "main") -> bool:
    archive = destination.with_suffix(".zip")
    url = repo + f"/archive/{revision}.zip"
    try:
        request_url(url, archive)
        with zipfile.ZipFile(archive) as zf:
            tmp = destination.parent / (destination.name + "_archive")
            if tmp.exists():
                shutil.rmtree(tmp)
            zf.extractall(tmp)
            roots = [p for p in tmp.iterdir() if p.is_dir()]
            if not roots:
                raise RuntimeError("archive had no top-level directory")
            if destination.exists():
                shutil.rmtree(destination)
            shutil.move(str(roots[0]), str(destination))
            shutil.rmtree(tmp, ignore_errors=True)
        archive.unlink(missing_ok=True)
        return True
    except Exception as exc:  # noqa: BLE001 - fallback is intentional here.
        print(f"warning: archive download failed for {repo}: {exc}", file=sys.stderr)
        return False


def raw_url(repo: str, rel: str, revision: str = "main") -> str:
    name = repo.rsplit("/", 1)[-1]
    owner = repo.rsplit("/", 2)[-2]
    return f"https://raw.githubusercontent.com/{owner}/{name}/{revision}/{rel}"


def fetch_raw_files(
    repo: str,
    destination: Path,
    files: Iterable[str],
    revision: str = "main",
) -> None:
    for rel in files:
        target = destination / rel
        if target.exists():
            continue
        print(f"download {rel}", flush=True)
        request_url(raw_url(repo, rel, revision), target)


def source_revision(source: Path) -> str:
    marker = source / ".quick_cppkh_revision"
    if marker.exists():
        return marker.read_text(encoding="utf-8").strip()
    if (source / ".git").exists():
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(source),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    return ""


def mark_source_revision(source: Path, revision: str) -> None:
    (source / ".quick_cppkh_revision").write_text(revision + "\n", encoding="utf-8")


def ensure_cppkh_source(deps_dir: Path, clean: bool) -> Path:
    source = deps_dir / "cppkh"
    if clean and source.exists():
        shutil.rmtree(source)
    if (source / "src" / "main.cpp").exists() and source_revision(source) == CPPKH_REVISION:
        return source
    if source.exists():
        print(f"refresh cppkh source to {CPPKH_REVISION[:7]}", flush=True)
        shutil.rmtree(source)
    source.parent.mkdir(parents=True, exist_ok=True)
    if shutil.which("git"):
        try:
            run(["git", "clone", "--depth", "1", CPPKH_REPO + ".git", str(source)], cwd=ROOT)
            run(["git", "fetch", "--depth", "1", "origin", CPPKH_REVISION], cwd=source)
            run(["git", "checkout", "--detach", CPPKH_REVISION], cwd=source)
            return source
        except subprocess.CalledProcessError:
            shutil.rmtree(source, ignore_errors=True)
    if download_archive(CPPKH_REPO, source, CPPKH_REVISION):
        mark_source_revision(source, CPPKH_REVISION)
        return source
    fetch_raw_files(
        CPPKH_REPO,
        source,
        ["src/main.cpp", "README.md", "LICENSE"],
        CPPKH_REVISION,
    )
    mark_source_revision(source, CPPKH_REVISION)
    return source


def ensure_simplify_source(deps_dir: Path, clean: bool) -> Path:
    source = deps_dir / "cpp-pd-code-simplify"
    if clean and source.exists():
        shutil.rmtree(source)
    if (source / "src" / "main.cpp").exists() and (
        source / "include" / "pdcode_simplify" / "pdcode_simplify.hpp"
    ).exists():
        return source
    source.parent.mkdir(parents=True, exist_ok=True)
    if shutil.which("git"):
        try:
            run(["git", "clone", "--depth", "1", SIMPLIFY_REPO + ".git", str(source)], cwd=ROOT)
            return source
        except subprocess.CalledProcessError:
            shutil.rmtree(source, ignore_errors=True)
    if download_archive(SIMPLIFY_REPO, source):
        return source
    fetch_raw_files(
        SIMPLIFY_REPO,
        source,
        [
            "include/pdcode_simplify/pdcode_simplify.hpp",
            "src/main.cpp",
            "src/pdcode_simplify.cpp",
            "README.md",
            "LICENSE",
        ],
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
    deps_dir = Path(args.deps_dir)
    out_dir = Path(args.out_dir) if args.out_dir else ROOT / "dist" / host_platform()
    ensure_clean_dir(out_dir, args.clean)
    deps_dir.mkdir(parents=True, exist_ok=True)

    cppkh_source = ensure_cppkh_source(deps_dir, args.clean_deps)
    simplify_source = ensure_simplify_source(deps_dir, args.clean_deps)

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
    parser.add_argument("--deps-dir", default=str(ROOT / "external"), help="downloaded upstream source directory")
    parser.add_argument("--out-dir", default="", help="output directory; default dist/<platform>")
    parser.add_argument("--debug", action="store_true", help="build with -O0 -g")
    parser.add_argument("--portable", action="store_true", help="do not add -march=native")
    parser.add_argument("--clean", action="store_true", help="remove the output directory before building")
    parser.add_argument("--clean-deps", action="store_true", help="redownload upstream sources")
    args = parser.parse_args(argv)
    build(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
