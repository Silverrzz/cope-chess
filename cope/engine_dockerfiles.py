from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path, PurePosixPath


MAX_DOCKERFILE_BYTES = 100_000


class EngineDockerfileError(ValueError):
    pass


def engine_dockerfile_root() -> Path:
    configured = os.environ.get("COPE_ENGINE_DOCKERFILES_DIR", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return Path(__file__).resolve().parent.parent / "data" / "engines"


def list_engine_dockerfiles() -> tuple[dict[str, int | str], ...]:
    root = engine_dockerfile_root()
    if not root.is_dir():
        return ()
    files: list[dict[str, int | str]] = []
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if any(part.startswith(".") for part in relative.parts):
            continue
        if not path.is_file() or path.is_symlink():
            continue
        files.append({"path": relative.as_posix(), "size": path.stat().st_size})
    return tuple(sorted(files, key=lambda item: str(item["path"]).casefold()))


def read_engine_dockerfile(selected_path: str) -> str:
    value = selected_path.strip()
    relative = PurePosixPath(value)
    if not value or relative.is_absolute() or "\\" in value:
        raise EngineDockerfileError("Choose a Dockerfile from data/engines.")
    if any(part in {"", ".", ".."} or part.startswith(".") for part in relative.parts):
        raise EngineDockerfileError("Invalid engine Dockerfile path.")
    root = engine_dockerfile_root().resolve()
    candidate = root.joinpath(*relative.parts)
    target = candidate.resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise EngineDockerfileError("Invalid engine Dockerfile path.") from exc
    if not candidate.is_file() or candidate.is_symlink():
        raise FileNotFoundError(value)
    with candidate.open("rb") as source:
        data = source.read(MAX_DOCKERFILE_BYTES + 1)
    if len(data) > MAX_DOCKERFILE_BYTES:
        raise EngineDockerfileError("Engine Dockerfiles cannot exceed 100,000 bytes.")
    try:
        content = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise EngineDockerfileError("Engine Dockerfiles must be UTF-8 text.") from exc
    if "\0" in content:
        raise EngineDockerfileError("Engine Dockerfiles cannot contain null bytes.")
    if not re.search(r"(?im)^\s*FROM(?:\s|$)", content):
        raise EngineDockerfileError("The selected file does not contain a FROM instruction.")
    return content if content.endswith("\n") else content + "\n"


def engine_build_hash(repository_url: str, source_ref: str, dockerfile: str) -> str:
    value = "\0".join((repository_url, source_ref, dockerfile)).encode("utf-8")
    return hashlib.sha256(value).hexdigest()
