#!/usr/bin/env python3
"""Compare the tracked quick_cppkh CLI bundle with direct cppkh."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys
from typing import Sequence


ROOT = Path(__file__).resolve().parents[1]
PLATFORM = "windows" if os.name == "nt" else ("macos" if sys.platform == "darwin" else "linux")
SUFFIX = ".exe" if os.name == "nt" else ""
DIST = ROOT / "dist" / PLATFORM
QUICK = DIST / f"quick_cppkh{SUFFIX}"
CPPKH = DIST / f"cppkh{SUFFIX}"
PD_SIMPLIFY = DIST / f"pd_simplify{SUFFIX}"
TREFOIL = "PD[X[1,5,2,4],X[3,1,4,6],X[5,3,6,2]]"


def run(executable: Path, args: Sequence[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(executable), *args],
        cwd=ROOT,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=check,
    )


def normalized(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n").strip()


def build(cxx: str, rebuild: bool) -> None:
    if not rebuild and all(path.is_file() for path in (QUICK, CPPKH, PD_SIMPLIFY)):
        return
    command = [sys.executable, str(ROOT / "tools" / "build.py"), "--portable", "--clean"]
    if cxx:
        command.extend(["--cxx", cxx])
    subprocess.run(command, cwd=ROOT, check=True)


def assert_same(args: Sequence[str]) -> None:
    expected = run(CPPKH, args)
    actual = run(QUICK, args)
    if normalized(actual.stdout) != normalized(expected.stdout):
        raise AssertionError(
            "quick_cppkh output differs from cppkh\n"
            f"arguments: {list(args)!r}\n"
            f"cppkh: {expected.stdout!r}\n"
            f"quick: {actual.stdout!r}"
        )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cxx", default="", help="C++ compiler passed to tools/build.py")
    parser.add_argument("--rebuild", action="store_true", help="rebuild the CLI bundle first")
    args = parser.parse_args(argv)

    build(args.cxx, args.rebuild)

    for simplify_r1 in (False, True):
        for simplify_nugatory in (False, True):
            assert_same(
                [
                    "--pd-code",
                    TREFOIL,
                    "--quiet",
                    "--simplify-r1" if simplify_r1 else "--no-simplify-r1",
                    "--simplify-nugatory" if simplify_nugatory else "--no-simplify-nugatory",
                ]
            )

    assert_same(["--pd-code", TREFOIL, "--quiet", "--print-crossing-signs"])
    assert_same(["--pd-file", str(ROOT / "benchmarks" / "zip_random_100.txt"), "--quiet"])

    invalid = "PD[X[1,2,3,4]]"
    quick_failure = run(QUICK, ["--pd-code", invalid, "--quiet"], check=False)
    direct_failure = run(CPPKH, ["--pd-code", invalid, "--quiet"], check=False)
    if quick_failure.returncode == 0 or direct_failure.returncode == 0:
        raise AssertionError("invalid PD code was accepted")

    print("quick_cppkh CLI compatibility: OK (4 switch combinations, 100-diagram corpus, errors)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
