from __future__ import annotations

import argparse
import ast
import contextlib
import ctypes
import hashlib
import os
import pathlib
import platform
import re
import shlex
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass
from importlib import resources
from typing import Iterator, Optional, Sequence, Union

import cpp_simple_interface


PathLike = Union[str, os.PathLike]
PdInput = Union[str, Sequence[Sequence[int]]]
PdManyInput = Union[str, Sequence[PdInput]]
UNKNOT_RESULT = "q^-1*t^0*Z[0] + q^1*t^0*Z[0]"


class QuickCppkhInterfaceError(RuntimeError):
    """Raised when the C++ executables cannot be built or run."""


CppkhInterfaceError = QuickCppkhInterfaceError


@dataclass(frozen=True)
class ExecutableBundle:
    quick_cppkh: pathlib.Path
    cppkh: pathlib.Path
    pd_simplify: pathlib.Path


def _format_pd(crossings: Sequence[Sequence[int]]) -> str:
    parts = []
    for crossing in crossings:
        values = list(crossing)
        if len(values) != 4:
            raise ValueError(f"PD crossing must have four entries: {crossing!r}")
        parts.append("X[{},{},{},{}]".format(*(int(value) for value in values)))
    return "PD[" + ",".join(parts) + "]"


def _parse_x_crossings(text: str) -> Optional[list[list[int]]]:
    pattern = r"X\s*\[\s*(-?\d+)\s*,\s*(-?\d+)\s*,\s*(-?\d+)\s*,\s*(-?\d+)\s*\]"
    crossings = []
    for match in re.finditer(pattern, text):
        crossings.append([int(match.group(i)) for i in range(1, 5)])
    return crossings if crossings else None


def _as_crossings(pd_code: PdInput) -> list[list[int]]:
    if isinstance(pd_code, str):
        body = pd_code.strip()
        if ":" in body:
            body = body.split(":", 1)[1].strip()
        if body.replace(" ", "") in ("PD[]", "[]"):
            return []

        parsed = _parse_x_crossings(body)
        if parsed is not None:
            return parsed

        try:
            value = ast.literal_eval(body)
        except (SyntaxError, ValueError) as exc:
            raise ValueError(f"unsupported PD-code string format: {pd_code!r}") from exc
    else:
        value = pd_code

    crossings = []
    for crossing in value:
        values = list(crossing)
        if len(values) != 4:
            raise ValueError(f"PD crossing must have four entries: {crossing!r}")
        crossings.append([int(item) for item in values])
    return crossings


def _check_sanity(crossings: list[list[int]]) -> None:
    counts = {}
    for crossing in crossings:
        for label in crossing:
            counts[label] = counts.get(label, 0) + 1
    if any(count != 2 for count in counts.values()):
        raise TypeError("each PD label must occur exactly twice")


def normalize_pd_code(pd_code: PdInput) -> str:
    """Normalize a supported PD-code value into standard ``PD[X[...],...]`` text."""

    return _format_pd(_as_crossings(pd_code))


def normalize_pd_codes(pd_codes: PdManyInput) -> str:
    """Normalize one or more PD codes into a newline-separated PD document."""

    if isinstance(pd_codes, str):
        return pd_codes.strip()
    return "\n".join(normalize_pd_code(pd_code) for pd_code in pd_codes)


DATA_RELATIVE_PATHS = {
    "quick_cppkh": pathlib.PurePosixPath("data/src/quick_cppkh/main.cpp"),
    "cppkh": pathlib.PurePosixPath("data/src/cppkh/main.cpp"),
    "pd_simplify_main": pathlib.PurePosixPath("data/src/pd_simplify/src/main.cpp"),
    "pd_simplify_include": pathlib.PurePosixPath("data/src/pd_simplify/include"),
}


def _repo_source_paths() -> Optional[dict[str, pathlib.Path]]:
    current = pathlib.Path(__file__).resolve()
    for parent in current.parents:
        candidates = {
            "quick_cppkh": parent / "src" / "main.cpp",
            "cppkh": parent / "external" / "cppkh" / "src" / "main.cpp",
            "pd_simplify_main": parent / "external" / "cpp-pd-code-simplify" / "src" / "main.cpp",
            "pd_simplify_include": parent / "external" / "cpp-pd-code-simplify" / "include",
        }
        if all(path.exists() for path in candidates.values()):
            return candidates
    return None


@contextlib.contextmanager
def _packaged_source_paths() -> Iterator[dict[str, pathlib.Path]]:
    package_root = resources.files("quick_cppkh_interface")
    with contextlib.ExitStack() as stack:
        paths: dict[str, pathlib.Path] = {}
        try:
            for key, rel_path in DATA_RELATIVE_PATHS.items():
                resource = package_root.joinpath(*rel_path.parts)
                path = pathlib.Path(stack.enter_context(resources.as_file(resource)))
                if not path.exists():
                    raise FileNotFoundError(str(path))
                paths[key] = path
            yield paths
            return
        except FileNotFoundError:
            repo_paths = _repo_source_paths()
            if repo_paths is None:
                raise QuickCppkhInterfaceError(
                    "quick_cppkh_interface C++ sources were not found. Built wheels include them "
                    "under quick_cppkh_interface/data/src; editable checkouts require external/cppkh "
                    "and external/cpp-pd-code-simplify."
                )
            yield repo_paths


def _cache_dir() -> pathlib.Path:
    env_value = os.environ.get("QUICK_CPPKH_INTERFACE_CACHE_DIR")
    if env_value:
        root = pathlib.Path(env_value)
    elif sys.platform == "win32":
        root = pathlib.Path(os.environ.get("LOCALAPPDATA", pathlib.Path.home())) / "quick-cppkh-interface"
    elif sys.platform == "darwin":
        root = pathlib.Path.home() / "Library" / "Caches" / "quick-cppkh-interface"
    else:
        root = pathlib.Path(os.environ.get("XDG_CACHE_HOME", pathlib.Path.home() / ".cache")) / "quick-cppkh-interface"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _exe_suffix() -> str:
    return ".exe" if platform.system() == "Windows" else ""


def _shared_suffix() -> str:
    if platform.system() == "Windows":
        return ".dll"
    return ".dylib" if platform.system() == "Darwin" else ".so"


def _native_enabled() -> bool:
    value = os.environ.get("QUICK_CPPKH_INTERFACE_NATIVE", "1").strip().lower()
    return value not in ("0", "false", "no", "off")


def _base_flags(cxx_standard: str) -> list[str]:
    flags = [f"-std={cxx_standard}", "-O3", "-DNDEBUG"]
    if _native_enabled():
        flags.append("-march=native")
    if platform.system() != "Windows":
        flags.append("-pthread")
    extra = os.environ.get("QUICK_CPPKH_INTERFACE_CXXFLAGS", "").strip()
    if extra:
        flags.extend(shlex.split(extra))
    return flags


def _compiler_runtime_path_entries() -> list[str]:
    compiler = cpp_simple_interface.get_gpp_filepath().strip()
    if not compiler:
        return []

    candidates = []
    unquoted = compiler
    if len(unquoted) >= 2 and unquoted[0] == unquoted[-1] and unquoted[0] in ("'", '"'):
        unquoted = unquoted[1:-1]
    candidates.append(unquoted)

    try:
        candidates.extend(shlex.split(compiler, posix=True))
    except ValueError:
        pass

    paths = []
    for candidate in candidates:
        path = pathlib.Path(candidate)
        if path.exists() and path.is_file():
            parent = str(path.resolve().parent)
            if parent not in paths:
                paths.append(parent)
    return paths


def _cache_key(source_paths: dict[str, pathlib.Path], flags_by_target: dict[str, Sequence[str]]) -> str:
    digest = hashlib.sha256()
    for key in sorted(source_paths):
        path = source_paths[key]
        if path.is_file():
            digest.update(key.encode("utf-8"))
            digest.update(path.read_bytes())
    for key in sorted(flags_by_target):
        digest.update(key.encode("utf-8"))
        digest.update("\0".join(flags_by_target[key]).encode("utf-8"))
    digest.update(cpp_simple_interface.get_gpp_filepath().encode("utf-8"))
    digest.update(platform.platform().encode("utf-8"))
    return digest.hexdigest()[:20]


def _compile_one(
    source: pathlib.Path,
    output: pathlib.Path,
    flags: Sequence[str],
    *,
    fallback_without_native: bool = True,
) -> None:
    tmp_exe = output.with_name(f"{output.name}.tmp-{os.getpid()}{_exe_suffix()}")
    if tmp_exe.exists():
        tmp_exe.unlink()

    success, message = cpp_simple_interface.compile_cpp_files(
        [str(source)],
        str(tmp_exe),
        other_flags=list(flags),
    )
    if not success and fallback_without_native and "-march=native" in flags:
        fallback_flags = [flag for flag in flags if flag != "-march=native"]
        success, message = cpp_simple_interface.compile_cpp_files(
            [str(source)],
            str(tmp_exe),
            other_flags=fallback_flags,
        )
    if not success:
        raise QuickCppkhInterfaceError(message)
    if not tmp_exe.exists():
        raise QuickCppkhInterfaceError(f"compiled executable was not created: {tmp_exe}")
    os.replace(tmp_exe, output)
    try:
        output.chmod(output.stat().st_mode | 0o755)
    except OSError:
        pass


def compile_executables(
    *,
    force: bool = False,
    cxx: Optional[str] = None,
    extra_flags: Optional[Sequence[str]] = None,
) -> ExecutableBundle:
    """Compile and cache quick_cppkh, cppkh, and pd_simplify executables."""

    if cxx:
        cpp_simple_interface.set_gpp_filepath(cxx)

    with _packaged_source_paths() as source_paths:
        quick_flags = _base_flags("c++17")
        cppkh_flags = _base_flags("c++14")
        pd_flags = _base_flags("c++17") + [f"-I{source_paths['pd_simplify_include']}"]
        if extra_flags:
            extra = [str(flag) for flag in extra_flags]
            quick_flags.extend(extra)
            cppkh_flags.extend(extra)
            pd_flags.extend(extra)

        flags_by_target = {
            "quick_cppkh": quick_flags,
            "cppkh": cppkh_flags,
            "pd_simplify": pd_flags,
        }
        cache = _cache_dir() / _cache_key(source_paths, flags_by_target)
        cache.mkdir(parents=True, exist_ok=True)
        bundle = ExecutableBundle(
            quick_cppkh=cache / f"quick_cppkh{_exe_suffix()}",
            cppkh=cache / f"cppkh{_exe_suffix()}",
            pd_simplify=cache / f"pd_simplify{_exe_suffix()}",
        )
        if (
            not force
            and bundle.quick_cppkh.exists()
            and bundle.cppkh.exists()
            and bundle.pd_simplify.exists()
        ):
            return bundle

        _compile_one(source_paths["cppkh"], bundle.cppkh, cppkh_flags)
        _compile_one(source_paths["pd_simplify_main"], bundle.pd_simplify, pd_flags)
        _compile_one(source_paths["quick_cppkh"], bundle.quick_cppkh, quick_flags)
        return bundle


def compile_quick_cppkh(
    *,
    force: bool = False,
    cxx: Optional[str] = None,
    extra_flags: Optional[Sequence[str]] = None,
) -> pathlib.Path:
    """Compile all required executables and return the cached quick_cppkh path."""

    return compile_executables(force=force, cxx=cxx, extra_flags=extra_flags).quick_cppkh


def compile_cppkh(
    *,
    force: bool = False,
    cxx: Optional[str] = None,
    extra_flags: Optional[Sequence[str]] = None,
) -> pathlib.Path:
    """Compile the quick_cppkh executable bundle and return the quick_cppkh path.

    This keeps the public helper name used by ``cppkh_interface`` while making
    the cached executable returned by this package the quick wrapper.
    """

    return compile_quick_cppkh(force=force, cxx=cxx, extra_flags=extra_flags)


def compile_cppkh_shared(*, force: bool = False) -> pathlib.Path:
    """Compile and cache the raw cppkh C API shared library."""

    with _packaged_source_paths() as source_paths:
        flags = _base_flags("c++14") + ["-shared", "-DCPPKH_SHARED_LIBRARY"]
        cache = _cache_dir() / _cache_key(source_paths, {"cppkh_shared": flags})
        cache.mkdir(parents=True, exist_ok=True)
        library = cache / f"cppkh{_shared_suffix()}"
        if library.exists() and not force:
            return library
        _compile_one(source_paths["cppkh"], library, flags)
        return library


def get_quick_cppkh_executable() -> pathlib.Path:
    """Return the cached quick_cppkh path, compiling first when necessary."""

    return compile_quick_cppkh()


def get_cppkh_executable() -> pathlib.Path:
    """Return the cached quick_cppkh path, matching cppkh_interface's helper name."""

    return get_quick_cppkh_executable()


def get_raw_cppkh_executable() -> pathlib.Path:
    """Return the cached raw cppkh executable path."""

    return compile_executables().cppkh


def get_pd_simplify_executable() -> pathlib.Path:
    """Return the cached pd_simplify path, compiling first when necessary."""

    return compile_executables().pd_simplify


_shared_lock = threading.Lock()
_shared_library = None
_dll_directory_handles = []


def _load_cppkh_shared():
    global _shared_library
    if _shared_library is not None:
        return _shared_library

    runtime_paths = _compiler_runtime_path_entries()
    if runtime_paths:
        os.environ["PATH"] = os.pathsep.join(runtime_paths + [os.environ.get("PATH", "")])
    library_path = compile_cppkh_shared()
    if platform.system() == "Windows" and hasattr(os, "add_dll_directory"):
        for directory in [str(library_path.parent), *runtime_paths]:
            _dll_directory_handles.append(os.add_dll_directory(directory))

    library = ctypes.CDLL(str(library_path))
    library.cppkh_compute_pd_signed_variants_ex.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int]
    library.cppkh_compute_pd_signed_variants_ex.restype = ctypes.c_void_p
    library.cppkh_last_error.restype = ctypes.c_char_p
    library.cppkh_free.argtypes = [ctypes.c_void_p]
    _shared_library = library
    return library


def compute_signed_variants(pd_code: PdInput, signs: Sequence[Sequence[int]]) -> list[str]:
    """Compute explicit crossing-sign variants through cppkh's native C API.

    This additive API does not simplify the PD code because every sign row
    corresponds positionally to the original crossing list.
    """

    crossings = _as_crossings(pd_code)
    _check_sanity(crossings)
    rows = [list(row) for row in signs]
    if not rows:
        raise ValueError("at least one crossing-sign row is required")
    if any(
        len(row) != len(crossings) or any(sign not in (-1, 1) for sign in row)
        for row in rows
    ):
        raise ValueError("each sign row must contain one +1/-1 value per crossing")

    pd_text = _format_pd(crossings).encode("utf-8")
    signs_text = "\n".join(" ".join(map(str, row)) for row in rows).encode("ascii")
    with _shared_lock:
        library = _load_cppkh_shared()
        pointer = library.cppkh_compute_pd_signed_variants_ex(pd_text, signs_text, 1)
        if not pointer:
            error = library.cppkh_last_error()
            detail = error.decode("utf-8", "replace") if error else "signed computation failed"
            raise QuickCppkhInterfaceError(detail)
        try:
            result = ctypes.string_at(pointer).decode("utf-8")
        finally:
            library.cppkh_free(pointer)
    return result.splitlines()


def _subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    runtime_paths = _compiler_runtime_path_entries()
    if runtime_paths:
        env["PATH"] = os.pathsep.join(runtime_paths + [env.get("PATH", "")])
    return env


def _run_command(command: list[str], *, encoding: Optional[str] = None) -> subprocess.CompletedProcess[str]:
    kwargs = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "encoding": encoding or "utf-8",
        "errors": "replace",
        "env": _subprocess_env(),
    }
    if platform.system() == "Windows":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    return subprocess.run(command, **kwargs)


def _run_document(
    pd_text: str,
    *,
    encoding: Optional[str] = None,
    threads: Union[str, int] = "1",
    use_quick: bool = True,
    de_r1: bool = True,
    de_k8: bool = True,
    print_simplified_pd: bool = False,
) -> list[str]:
    bundle = compile_executables()
    with tempfile.NamedTemporaryFile("w", suffix=".pd", encoding="utf-8", delete=False) as handle:
        handle.write(pd_text)
        if pd_text and not pd_text.endswith("\n"):
            handle.write("\n")
        pd_file = handle.name

    if use_quick:
        command = [
            str(bundle.quick_cppkh),
            "--cppkh-exe",
            str(bundle.cppkh),
            "--pd-simplify-exe",
            str(bundle.pd_simplify),
            "--pd-file",
            pd_file,
            "--quiet",
            "--threads",
            str(threads),
        ]
    else:
        command = [
            str(bundle.cppkh),
            "--pd-file",
            pd_file,
            "--quiet",
            "--threads",
            str(threads),
        ]
    command.append("--simplify-r1" if de_r1 else "--no-simplify-r1")
    command.append("--simplify-nugatory" if de_k8 else "--no-simplify-nugatory")
    if print_simplified_pd:
        command.append("--print-simplified-pd")

    try:
        result = _run_command(command, encoding=encoding)
    finally:
        try:
            os.unlink(pd_file)
        except OSError:
            pass

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise QuickCppkhInterfaceError(detail or f"command exited with code {result.returncode}")

    if print_simplified_pd:
        lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        return [line.split("\t", 1)[-1] for line in lines]

    matches = re.findall(r'"([^"]*)"', result.stdout)
    if not matches:
        raise QuickCppkhInterfaceError(f"result not found in output: {result.stdout!r}")
    return matches


def _run_single(pd_text: str, **kwargs: object) -> str:
    results = _run_document(pd_text, **kwargs)
    if len(results) != 1:
        raise QuickCppkhInterfaceError(f"expected exactly one result, got {len(results)}")
    return results[0]


def _prepare_many_for_compute(
    pd_codes: PdManyInput,
) -> str:
    if isinstance(pd_codes, str):
        return pd_codes.strip()

    prepared = []
    for pd_code in pd_codes:
        crossings = _as_crossings(pd_code)
        _check_sanity(crossings)
        prepared.append(_format_pd(crossings))
    return "\n".join(prepared)


def _compute_one(
    pd_code: PdInput,
    *,
    encoding: Optional[str],
    de_r1: bool,
    de_k8: bool,
    show_real_pdcode: bool,
    threads: Union[str, int],
) -> str:
    crossings = _as_crossings(pd_code)
    _check_sanity(crossings)
    if crossings == []:
        return UNKNOT_RESULT

    document = _format_pd(crossings)
    if show_real_pdcode:
        simplified = _run_document(
            document,
            encoding=encoding,
            threads=threads,
            use_quick=False,
            de_r1=de_r1,
            de_k8=de_k8,
            print_simplified_pd=True,
        )
        print(f"Real PD code after de_r1 and de_k8: {simplified[0] if simplified else ''}")

    return _run_single(
        document,
        encoding=encoding,
        threads=threads,
        use_quick=bool(de_r1 and de_k8),
        de_r1=de_r1,
        de_k8=de_k8,
    )


def solve_khovanov(
    pd_code: PdInput,
    encoding: Optional[str] = None,
    de_r1: bool = True,
    de_k8: bool = True,
    show_real_pdcode: bool = False,
) -> str:
    """Compute Khovanov homology with a javakh-interface compatible signature."""

    return _compute_one(
        pd_code,
        encoding=encoding,
        de_r1=de_r1,
        de_k8=de_k8,
        show_real_pdcode=show_real_pdcode,
        threads="1",
    )


def solve_many_khovanov(
    pd_codes: PdManyInput,
    encoding: Optional[str] = None,
    de_r1: bool = True,
    de_k8: bool = True,
    show_real_pdcode: bool = False,
    threads: Union[str, int] = "1",
) -> list[str]:
    """Compute many PD codes using quick_cppkh when both simplification flags are enabled."""

    document = _prepare_many_for_compute(pd_codes)
    if not document:
        return []
    if show_real_pdcode:
        simplified = _run_document(
            document,
            encoding=encoding,
            threads=threads,
            use_quick=False,
            de_r1=de_r1,
            de_k8=de_k8,
            print_simplified_pd=True,
        )
        print(f"Real PD code after de_r1 and de_k8: {simplified}")
    return _run_document(
        document,
        encoding=encoding,
        threads=threads,
        use_quick=bool(de_r1 and de_k8),
        de_r1=de_r1,
        de_k8=de_k8,
    )


def compute_pd(
    pd_code: PdInput,
    *,
    encoding: Optional[str] = None,
    de_r1: bool = True,
    de_k8: bool = True,
    show_real_pdcode: bool = False,
    threads: Union[str, int] = "1",
) -> str:
    """Compute Khovanov homology using the same defaults as solve_khovanov."""

    return _compute_one(
        pd_code,
        encoding=encoding,
        de_r1=de_r1,
        de_k8=de_k8,
        show_real_pdcode=show_real_pdcode,
        threads=threads,
    )


def compute_many_pd(
    pd_codes: PdManyInput,
    *,
    encoding: Optional[str] = None,
    de_r1: bool = True,
    de_k8: bool = True,
    show_real_pdcode: bool = False,
    threads: Union[str, int] = "1",
) -> list[str]:
    """Compute many PD codes using one cached executable invocation."""

    return solve_many_khovanov(
        pd_codes,
        encoding=encoding,
        de_r1=de_r1,
        de_k8=de_k8,
        show_real_pdcode=show_real_pdcode,
        threads=threads,
    )


def simplify_pd(pd_code: PdInput, *, de_r1: bool = True, de_k8: bool = True) -> str:
    """Return the normalized PD string after optional native R1 and nugatory simplification."""

    crossings = _as_crossings(pd_code)
    _check_sanity(crossings)
    return _run_single(
        _format_pd(crossings),
        use_quick=False,
        de_r1=de_r1,
        de_k8=de_k8,
        print_simplified_pd=True,
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Compute Khovanov homology with quick-cppkh-interface.")
    parser.add_argument("pd_code", help="PD code as PD[...] text or a Python-style list of crossings.")
    parser.add_argument("--no-de-r1", action="store_true", help="Disable R1-move removal.")
    parser.add_argument("--no-de-k8", action="store_true", help="Disable nugatory-crossing removal.")
    parser.add_argument("--threads", default="1", help="quick_cppkh/cppkh --threads value.")
    args = parser.parse_args(argv)
    print(
        compute_pd(
            args.pd_code,
            de_r1=not args.no_de_r1,
            de_k8=not args.no_de_k8,
            threads=args.threads,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
