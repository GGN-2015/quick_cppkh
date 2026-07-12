from __future__ import annotations

import base64
import hashlib
import io
import shutil
import tarfile
import tempfile
import zipfile
from pathlib import Path

from poetry.core.masonry import api as poetry_api


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PROJECT_ROOT.parents[1]

DATA_FILES = [
    (
        REPO_ROOT / "src" / "main.cpp",
        PROJECT_ROOT / "quick_cppkh_interface" / "data" / "src" / "quick_cppkh" / "main.cpp",
        "quick_cppkh_interface/data/src/quick_cppkh/main.cpp",
    ),
    (
        REPO_ROOT / "external" / "cppkh" / "src" / "main.cpp",
        PROJECT_ROOT / "quick_cppkh_interface" / "data" / "src" / "cppkh" / "main.cpp",
        "quick_cppkh_interface/data/src/cppkh/main.cpp",
    ),
    (
        REPO_ROOT / "external" / "cpp-pd-code-simplify" / "src" / "main.cpp",
        PROJECT_ROOT / "quick_cppkh_interface" / "data" / "src" / "pd_simplify" / "src" / "main.cpp",
        "quick_cppkh_interface/data/src/pd_simplify/src/main.cpp",
    ),
    (
        REPO_ROOT
        / "external"
        / "cpp-pd-code-simplify"
        / "include"
        / "pdcode_simplify"
        / "pdcode_simplify.hpp",
        PROJECT_ROOT
        / "quick_cppkh_interface"
        / "data"
        / "src"
        / "pd_simplify"
        / "include"
        / "pdcode_simplify"
        / "pdcode_simplify.hpp",
        "quick_cppkh_interface/data/src/pd_simplify/include/pdcode_simplify/pdcode_simplify.hpp",
    ),
]


def _sync_sources() -> None:
    missing = []
    for source, target, _wheel_name in DATA_FILES:
        if source.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        elif not target.exists():
            missing.append(str(source))
    if missing:
        raise FileNotFoundError(
            "quick_cppkh_interface package sources were not available. Missing:\n"
            + "\n".join(missing)
            + "\nRun `python tools/build.py` from the repository root first, or build from an sdist."
        )


def _clean_sources() -> None:
    for source, target, _wheel_name in DATA_FILES:
        if source.exists() and target.exists():
            target.unlink()


def _source_bytes_by_name() -> dict[str, bytes]:
    result = {}
    for source, target, wheel_name in DATA_FILES:
        if target.exists():
            result[wheel_name] = target.read_bytes()
        elif source.exists():
            result[wheel_name] = source.read_bytes()
        else:
            raise FileNotFoundError(f"source file was not available for packaging: {wheel_name}")
    return result


def _wheel_hash_and_size(data: bytes) -> tuple[str, str]:
    digest = hashlib.sha256(data).digest()
    encoded = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return f"sha256={encoded}", str(len(data))


def _make_universal_wheel_metadata(data: bytes) -> bytes:
    text = data.decode("utf-8")
    lines = []
    tag_written = False
    root_written = False
    for line in text.splitlines():
        if line.startswith("Root-Is-Purelib:"):
            lines.append("Root-Is-Purelib: true")
            root_written = True
        elif line.startswith("Tag:"):
            if not tag_written:
                lines.append("Tag: py3-none-any")
                tag_written = True
        else:
            lines.append(line)
    if not root_written:
        lines.append("Root-Is-Purelib: true")
    if not tag_written:
        lines.append("Tag: py3-none-any")
    return ("\n".join(lines) + "\n").encode("utf-8")


def _universal_wheel_path(wheel_path: Path, record_name: str) -> Path:
    dist_info = record_name.split("/", 1)[0]
    base = dist_info.removesuffix(".dist-info")
    return wheel_path.with_name(f"{base}-py3-none-any.whl")


def _rewrite_wheel_with_sources(wheel_path: Path) -> Path:
    source_by_name = _source_bytes_by_name()
    source_names = set(source_by_name)
    rows: list[tuple[str, str, str]] = []

    with zipfile.ZipFile(wheel_path, "r") as zin:
        record_name = next(
            (name for name in zin.namelist() if name.endswith(".dist-info/RECORD")),
            None,
        )
        if record_name is None:
            raise RuntimeError(f"wheel RECORD file not found in {wheel_path}")
        output_path = _universal_wheel_path(wheel_path, record_name)
        temp_path = output_path.with_name(output_path.name + ".tmp")

        with zipfile.ZipFile(temp_path, "w", compression=zipfile.ZIP_DEFLATED) as zout:
            wheel_metadata_name = record_name.removesuffix("RECORD") + "WHEEL"

            for item in zin.infolist():
                if item.filename in source_names or item.filename == record_name:
                    continue
                data = zin.read(item.filename)
                if item.filename == wheel_metadata_name:
                    data = _make_universal_wheel_metadata(data)
                zout.writestr(item, data)
                if not item.is_dir():
                    digest, size = _wheel_hash_and_size(data)
                    rows.append((item.filename, digest, size))

            for source_name, source_data in sorted(source_by_name.items()):
                source_info = zipfile.ZipInfo(source_name, date_time=(2016, 1, 1, 0, 0, 0))
                source_info.compress_type = zipfile.ZIP_DEFLATED
                zout.writestr(source_info, source_data)
                digest, size = _wheel_hash_and_size(source_data)
                rows.append((source_name, digest, size))

            rows.append((record_name, "", ""))
            record_text = "".join(f"{path},{digest},{size}\n" for path, digest, size in rows)
            record_info = zipfile.ZipInfo(record_name, date_time=(2016, 1, 1, 0, 0, 0))
            record_info.compress_type = zipfile.ZIP_DEFLATED
            zout.writestr(record_info, record_text.encode("utf-8"))

    temp_path.replace(output_path)
    if output_path != wheel_path and wheel_path.exists():
        wheel_path.unlink()
    return output_path


def _rewrite_sdist_with_sources(sdist_path: Path) -> None:
    source_by_name = _source_bytes_by_name()
    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".tar.gz")
    temp_path = Path(temp_file.name)
    temp_file.close()

    try:
        with tarfile.open(sdist_path, "r:gz") as tin, tarfile.open(temp_path, "w:gz") as tout:
            names = tin.getnames()
            if not names:
                raise RuntimeError(f"sdist is empty: {sdist_path}")
            root = names[0].split("/", 1)[0]
            source_names = {f"{root}/{name}" for name in source_by_name}

            for member in tin.getmembers():
                if member.name in source_names:
                    continue
                if member.isfile():
                    extracted = tin.extractfile(member)
                    if extracted is None:
                        raise RuntimeError(f"could not read {member.name} from {sdist_path}")
                    with extracted:
                        tout.addfile(member, extracted)
                else:
                    tout.addfile(member)

            for source_name, source_data in sorted(source_by_name.items()):
                source_info = tarfile.TarInfo(f"{root}/{source_name}")
                source_info.size = len(source_data)
                source_info.mtime = 0
                source_info.mode = 0o644
                tout.addfile(source_info, io.BytesIO(source_data))

        temp_path.replace(sdist_path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def get_requires_for_build_wheel(config_settings=None):
    return poetry_api.get_requires_for_build_wheel(config_settings)


def get_requires_for_build_sdist(config_settings=None):
    return poetry_api.get_requires_for_build_sdist(config_settings)


def prepare_metadata_for_build_wheel(metadata_directory, config_settings=None):
    _sync_sources()
    try:
        return poetry_api.prepare_metadata_for_build_wheel(metadata_directory, config_settings)
    finally:
        _clean_sources()


def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
    _sync_sources()
    try:
        wheel_name = poetry_api.build_wheel(wheel_directory, config_settings, metadata_directory)
        final_path = _rewrite_wheel_with_sources(Path(wheel_directory) / wheel_name)
        return final_path.name
    finally:
        _clean_sources()


def build_sdist(sdist_directory, config_settings=None):
    _sync_sources()
    try:
        sdist_name = poetry_api.build_sdist(sdist_directory, config_settings)
        _rewrite_sdist_with_sources(Path(sdist_directory) / sdist_name)
        return sdist_name
    finally:
        _clean_sources()
