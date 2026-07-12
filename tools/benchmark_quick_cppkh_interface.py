#!/usr/bin/env python3
"""Run quick_cppkh_interface for tools/benchmark.py."""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
from typing import Sequence


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="PD-code input file")
    parser.add_argument("--package-path", default="", help="local quick-cppkh-interface project path")
    parser.add_argument("--cache-dir", default="", help="quick_cppkh_interface executable cache directory")
    parser.add_argument("--cxx", default="", help="C++ compiler path")
    parser.add_argument("--threads", default="1", help="thread count forwarded to compute_many_pd")
    parser.add_argument("--raw-pd", action="store_true", help="disable interface simplification flags")
    parser.add_argument("--compile-only", action="store_true", help="compile cached executables and exit")
    args = parser.parse_args(argv)

    if args.package_path:
        sys.path.insert(0, str(pathlib.Path(args.package_path).resolve()))
    if args.cache_dir:
        os.environ["QUICK_CPPKH_INTERFACE_CACHE_DIR"] = str(pathlib.Path(args.cache_dir).resolve())

    import quick_cppkh_interface

    if args.cxx:
        quick_cppkh_interface.compile_executables(cxx=args.cxx)
    else:
        quick_cppkh_interface.compile_executables()

    if args.compile_only:
        return 0

    pd_text = pathlib.Path(args.input).read_text(encoding="utf-8")
    results = quick_cppkh_interface.compute_many_pd(
        pd_text,
        de_r1=not args.raw_pd,
        de_k8=not args.raw_pd,
        threads=args.threads,
    )
    for index, result in enumerate(results, 1):
        print(f"{index}: {json.dumps(result)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
