#!/usr/bin/env python3
"""Exercise quick_cppkh against the cppkh 0.2.1 CLI and Python API surface."""

from __future__ import annotations

import argparse
import ast
import os
import pathlib
import re
import subprocess
import sys
from typing import Sequence


ROOT = pathlib.Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / "python_project" / "quick_cppkh-interface"
PD_CODE = [[1, 5, 2, 4], [3, 1, 4, 6], [5, 3, 6, 2]]


def run_cppkh(executable: pathlib.Path, args: Sequence[str], cxx: str) -> str:
    env = os.environ.copy()
    compiler = pathlib.Path(cxx) if cxx else None
    if compiler is not None and compiler.is_file():
        env["PATH"] = os.pathsep.join([str(compiler.resolve().parent), env.get("PATH", "")])
    result = subprocess.run(
        [str(executable), *args],
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return result.stdout


def quoted_result(stdout: str) -> str:
    match = re.search(r'"([^"]*)"', stdout)
    if not match:
        raise AssertionError(f"homology result not found in output: {stdout!r}")
    return match.group(1)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cxx", default="", help="C++ compiler used for package executables")
    parser.add_argument("--force", action="store_true", help="force recompilation")
    args = parser.parse_args(argv)

    sys.path.insert(0, str(PACKAGE_ROOT))
    import quick_cppkh_interface

    bundle = quick_cppkh_interface.compile_executables(force=args.force, cxx=args.cxx or None)
    normalized = quick_cppkh_interface.normalize_pd_code(PD_CODE)
    combinations = [(False, False), (True, False), (False, True), (True, True)]

    for de_r1, de_k8 in combinations:
        flags = [
            "--pd-code",
            normalized,
            "--quiet",
            "--simplify-r1" if de_r1 else "--no-simplify-r1",
            "--simplify-nugatory" if de_k8 else "--no-simplify-nugatory",
        ]
        expected = quoted_result(run_cppkh(bundle.cppkh, flags, args.cxx))
        actual = quick_cppkh_interface.compute_pd(PD_CODE, de_r1=de_r1, de_k8=de_k8)
        if actual != expected:
            raise AssertionError(f"single result mismatch for de_r1={de_r1}, de_k8={de_k8}")
        batch = quick_cppkh_interface.compute_many_pd(
            [PD_CODE, PD_CODE],
            de_r1=de_r1,
            de_k8=de_k8,
        )
        if batch != [expected, expected]:
            raise AssertionError(f"batch result mismatch for de_r1={de_r1}, de_k8={de_k8}")

    signs_text = run_cppkh(
        bundle.cppkh,
        ["--pd-code", normalized, "--quiet", "--print-crossing-signs"],
        args.cxx,
    ).strip()
    signs = ast.literal_eval(signs_text)
    raw_expected = quoted_result(
        run_cppkh(bundle.cppkh, ["--pd-code", normalized, "--quiet", "--no-simplify-pd"], args.cxx)
    )
    shared = quick_cppkh_interface.compile_cppkh_shared(force=args.force)
    signed = quick_cppkh_interface.compute_signed_variants(PD_CODE, [signs])
    if signed != [raw_expected]:
        raise AssertionError("compute_signed_variants does not match raw cppkh")
    try:
        quick_cppkh_interface.compute_signed_variants(PD_CODE, [])
    except ValueError:
        pass
    else:
        raise AssertionError("empty signed-variant input must raise ValueError")

    print(f"cppkh executable: {bundle.cppkh}")
    print(f"cppkh shared library: {shared}")
    print("all four simplification combinations and signed variants: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
