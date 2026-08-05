from __future__ import annotations

import gzip
import hashlib
import json
import os
import posixpath
import shutil
import stat
import tarfile
from pathlib import Path, PurePosixPath
from typing import Any


ARTIFACT_FORMAT = "cope-tar-gzip-v1"
ARTIFACT_PLATFORM = "linux-x86_64"
MANIFEST_NAME = ".cope-manifest.json"
MARKER_NAME = ".cope-artifact"
DEFAULT_MAX_FILES = 20_000
DEFAULT_MAX_UNCOMPRESSED_BYTES = 4 * 1024 * 1024 * 1024


def create_artifact_archive(
    source: Path,
    destination: Path,
    *,
    build_hash: str,
    entrypoint: str = "engine",
) -> tuple[str, int]:
    source = source.resolve()
    _validate_relative_path(entrypoint)
    executable = source / entrypoint
    if not executable.is_file():
        raise ValueError(f"artifact entrypoint is missing: {entrypoint}")
    if not _inside(source, executable.resolve()):
        raise ValueError("artifact entrypoint resolves outside the artifact")
    entries = _source_entries(source)
    files = [_source_metadata(source, path, name) for path, name in entries]
    manifest = {
        "format": ARTIFACT_FORMAT,
        "platform": ARTIFACT_PLATFORM,
        "build_hash": build_hash,
        "entrypoint": entrypoint,
        "files": files,
    }
    manifest_bytes = json.dumps(
        manifest,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("xb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
            with tarfile.open(
                fileobj=compressed,
                mode="w",
                format=tarfile.PAX_FORMAT,
            ) as archive:
                manifest_info = tarfile.TarInfo(MANIFEST_NAME)
                manifest_info.size = len(manifest_bytes)
                manifest_info.mode = 0o644
                manifest_info.mtime = 0
                manifest_info.uid = 0
                manifest_info.gid = 0
                archive.addfile(manifest_info, _BytesReader(manifest_bytes))
                for path, name in entries:
                    _add_source_entry(archive, path, name)
        raw.flush()
        os.fsync(raw.fileno())
    return sha256_file(destination), destination.stat().st_size


def validate_artifact_archive(
    path: Path,
    *,
    expected_build_hash: str | None = None,
    expected_entrypoint: str | None = None,
    max_files: int = DEFAULT_MAX_FILES,
    max_uncompressed_bytes: int = DEFAULT_MAX_UNCOMPRESSED_BYTES,
) -> dict[str, Any]:
    with tarfile.open(path, mode="r:gz") as archive:
        members = archive.getmembers()
        if not members or len(members) > max_files + 1:
            raise ValueError("artifact contains an invalid number of entries")
        by_name: dict[str, tarfile.TarInfo] = {}
        declared_size = 0
        for member in members:
            name = _validate_relative_path(member.name)
            if name == MARKER_NAME:
                raise ValueError("artifact contains a reserved path")
            if name in by_name:
                raise ValueError(f"artifact contains duplicate path: {name}")
            if not (member.isfile() or member.isdir() or member.issym()):
                raise ValueError(f"artifact contains unsupported path type: {name}")
            if member.issym():
                _resolve_link_name(name, member.linkname)
            if member.isfile():
                declared_size += member.size
                if declared_size > max_uncompressed_bytes:
                    raise ValueError("artifact exceeds the uncompressed size limit")
            by_name[name] = member
        manifest_member = by_name.get(MANIFEST_NAME)
        if manifest_member is None or not manifest_member.isfile():
            raise ValueError("artifact manifest is missing")
        manifest_stream = archive.extractfile(manifest_member)
        if manifest_stream is None:
            raise ValueError("artifact manifest cannot be read")
        try:
            manifest = json.loads(manifest_stream.read().decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("artifact manifest is invalid") from exc
        if not isinstance(manifest, dict):
            raise ValueError("artifact manifest is invalid")
        if manifest.get("format") != ARTIFACT_FORMAT:
            raise ValueError("artifact format is unsupported")
        if manifest.get("platform") != ARTIFACT_PLATFORM:
            raise ValueError("artifact platform is unsupported")
        build_hash = manifest.get("build_hash")
        if not isinstance(build_hash, str) or len(build_hash) != 64:
            raise ValueError("artifact build hash is invalid")
        if expected_build_hash is not None and build_hash != expected_build_hash:
            raise ValueError("artifact build hash does not match the requested build")
        entrypoint = manifest.get("entrypoint")
        if not isinstance(entrypoint, str):
            raise ValueError("artifact entrypoint is invalid")
        _validate_relative_path(entrypoint)
        if expected_entrypoint is not None and entrypoint != expected_entrypoint:
            raise ValueError("artifact entrypoint does not match the descriptor")
        expected_files = manifest.get("files")
        if not isinstance(expected_files, list):
            raise ValueError("artifact file manifest is invalid")
        actual_files = []
        for member in members:
            if member.name == MANIFEST_NAME:
                continue
            actual_files.append(_member_metadata(archive, member))
        if actual_files != expected_files:
            raise ValueError("artifact contents do not match the manifest")
        _validate_entrypoint(by_name, entrypoint)
        return manifest


def extract_artifact_archive(
    archive_path: Path,
    destination: Path,
    *,
    expected_build_hash: str,
    expected_entrypoint: str,
) -> None:
    validate_artifact_archive(
        archive_path,
        expected_build_hash=expected_build_hash,
        expected_entrypoint=expected_entrypoint,
    )
    destination.mkdir(parents=True, exist_ok=False)
    links: list[tuple[Path, str]] = []
    directories: list[tuple[Path, int]] = []
    with tarfile.open(archive_path, mode="r:gz") as archive:
        for member in archive.getmembers():
            name = _validate_relative_path(member.name)
            target = destination.joinpath(*PurePosixPath(name).parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                directories.append((target, member.mode & 0o777))
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            if member.isfile():
                stream = archive.extractfile(member)
                if stream is None:
                    raise ValueError(f"artifact file cannot be read: {name}")
                with target.open("xb") as output:
                    shutil.copyfileobj(stream, output, 1024 * 1024)
                target.chmod(member.mode & 0o777)
                continue
            links.append((target, member.linkname))
    for target, linkname in links:
        target.symlink_to(linkname)
    for target, mode in reversed(directories):
        target.chmod(mode)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class _BytesReader:
    def __init__(self, value: bytes):
        self._value = value
        self._offset = 0

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            size = len(self._value) - self._offset
        value = self._value[self._offset : self._offset + size]
        self._offset += len(value)
        return value


def _source_entries(source: Path) -> list[tuple[Path, str]]:
    result: list[tuple[Path, str]] = []

    def visit(directory: Path, prefix: str) -> None:
        with os.scandir(directory) as scan:
            entries = sorted(scan, key=lambda item: item.name)
        for entry in entries:
            name = f"{prefix}/{entry.name}" if prefix else entry.name
            if name in {MANIFEST_NAME, MARKER_NAME}:
                continue
            _validate_relative_path(name)
            path = Path(entry.path)
            result.append((path, name))
            if entry.is_dir(follow_symlinks=False):
                visit(path, name)

    visit(source, "")
    return result


def _source_metadata(source: Path, path: Path, name: str) -> dict[str, Any]:
    value = path.lstat()
    mode = stat.S_IMODE(value.st_mode) & 0o777
    if stat.S_ISDIR(value.st_mode):
        return {"path": name, "type": "directory", "mode": mode}
    if stat.S_ISREG(value.st_mode):
        return {
            "path": name,
            "type": "file",
            "mode": mode,
            "size": value.st_size,
            "sha256": sha256_file(path),
        }
    if stat.S_ISLNK(value.st_mode):
        target = os.readlink(path)
        resolved = _resolve_link_name(name, target)
        if not _inside(source, source.joinpath(*PurePosixPath(resolved).parts).resolve()):
            raise ValueError(f"artifact link resolves outside the artifact: {name}")
        return {"path": name, "type": "symlink", "mode": mode, "target": target}
    raise ValueError(f"artifact contains unsupported path type: {name}")


def _add_source_entry(archive: tarfile.TarFile, path: Path, name: str) -> None:
    value = path.lstat()
    info = tarfile.TarInfo(name)
    info.mode = stat.S_IMODE(value.st_mode) & 0o777
    info.mtime = 0
    info.uid = 0
    info.gid = 0
    if stat.S_ISDIR(value.st_mode):
        info.type = tarfile.DIRTYPE
        archive.addfile(info)
        return
    if stat.S_ISLNK(value.st_mode):
        info.type = tarfile.SYMTYPE
        info.linkname = os.readlink(path)
        archive.addfile(info)
        return
    info.size = value.st_size
    with path.open("rb") as stream:
        archive.addfile(info, stream)


def _member_metadata(archive: tarfile.TarFile, member: tarfile.TarInfo) -> dict[str, Any]:
    name = _validate_relative_path(member.name)
    mode = member.mode & 0o777
    if member.isdir():
        return {"path": name, "type": "directory", "mode": mode}
    if member.issym():
        return {
            "path": name,
            "type": "symlink",
            "mode": mode,
            "target": member.linkname,
        }
    stream = archive.extractfile(member)
    if stream is None:
        raise ValueError(f"artifact file cannot be read: {name}")
    digest = hashlib.sha256()
    size = 0
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
        size += len(chunk)
        digest.update(chunk)
    if size != member.size:
        raise ValueError(f"artifact file size is invalid: {name}")
    return {
        "path": name,
        "type": "file",
        "mode": mode,
        "size": size,
        "sha256": digest.hexdigest(),
    }


def _validate_entrypoint(
    members: dict[str, tarfile.TarInfo],
    entrypoint: str,
) -> None:
    current = entrypoint
    seen: set[str] = set()
    while True:
        if current in seen:
            raise ValueError("artifact entrypoint contains a link cycle")
        seen.add(current)
        member = members.get(current)
        if member is None:
            raise ValueError("artifact entrypoint is missing")
        if member.isfile():
            if member.mode & 0o111 == 0:
                raise ValueError("artifact entrypoint is not executable")
            return
        if not member.issym():
            raise ValueError("artifact entrypoint is not a file")
        current = _resolve_link_name(current, member.linkname)


def _validate_relative_path(value: str) -> str:
    if not value or value.startswith("/") or "\\" in value:
        raise ValueError("artifact path is invalid")
    path = PurePosixPath(value)
    if any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("artifact path is invalid")
    normalized = path.as_posix()
    if normalized != value:
        raise ValueError("artifact path is not normalized")
    return normalized


def _resolve_link_name(name: str, target: str) -> str:
    if not target or target.startswith("/") or "\\" in target:
        raise ValueError(f"artifact link target is invalid: {name}")
    resolved = posixpath.normpath(posixpath.join(posixpath.dirname(name), target))
    return _validate_relative_path(resolved)


def _inside(root: Path, value: Path) -> bool:
    try:
        value.relative_to(root)
        return True
    except ValueError:
        return False
