from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from urllib.parse import urlsplit


LOG = logging.getLogger("cope.worker.update")
COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")


def install_worker_release(
    *,
    target_commit: str,
    repository_url: str,
) -> Path:
    return install_client_release(
        client_name="worker",
        target_commit=target_commit,
        repository_url=repository_url,
    )


def install_client_release(
    *,
    client_name: str,
    target_commit: str,
    repository_url: str,
) -> Path:
    if client_name not in {"worker", "benchmarker"}:
        raise ValueError("unsupported update client")
    if COMMIT_PATTERN.fullmatch(target_commit) is None:
        raise ValueError(f"{client_name} update target is not a full Git commit")
    root = _update_root(client_name)
    repository = root / "repository"
    root.mkdir(parents=True, exist_ok=True, mode=0o755)
    if not (repository / ".git").exists():
        clone_url = _repository_clone_url(repository_url, client_name)
        _run(["git", "clone", "--origin", "origin", clone_url, str(repository)])
    configured_url = _git_output(repository, "remote", "get-url", "origin")
    if _normalise_repository_url(configured_url) != _normalise_repository_url(repository_url):
        raise RuntimeError(
            f"{client_name} update repository does not match the server deployment source"
        )
    _run(["git", "-C", str(repository), "fetch", "--prune", "origin"])
    resolved = _git_output(repository, "rev-parse", "--verify", f"{target_commit}^{{commit}}")
    if resolved != target_commit:
        raise RuntimeError(f"worker repository resolved {resolved}, expected {target_commit}")
    releases = root / "releases"
    releases.mkdir(parents=True, exist_ok=True, mode=0o755)
    release = releases / target_commit
    if not _release_ready(release, target_commit):
        if release.exists():
            _remove_release(repository, release)
        source = release / "source"
        venv = release / "venv"
        try:
            release.mkdir(parents=True, mode=0o755)
            _run(
                [
                    "git",
                    "-C",
                    str(repository),
                    "worktree",
                    "add",
                    "--detach",
                    str(source),
                    target_commit,
                ]
            )
            (source / "cope" / "BUILD_VERSION").write_text(
                target_commit + "\n",
                encoding="utf-8",
            )
            _run([sys.executable, "-m", "venv", str(venv)])
            _run(
                [
                    str(_venv_executable(venv, "python")),
                    "-m",
                    "pip",
                    "install",
                    "--no-cache-dir",
                    f"{source}[worker]",
                ]
            )
            reported = _run(
                [str(_venv_executable(venv, "cope")), "version"],
                capture=True,
            )
            if f"version={target_commit}" not in reported:
                raise RuntimeError(
                    f"installed {client_name} release reported the wrong version"
                )
        except Exception:
            if source.exists():
                _run(
                    [
                        "git",
                        "-C",
                        str(repository),
                        "worktree",
                        "remove",
                        "--force",
                        str(source),
                    ],
                    check=False,
                )
            shutil.rmtree(release, ignore_errors=True)
            raise
    link = root / "current"
    temporary_link = root / f".current.{uuid.uuid4().hex}"
    temporary_link.symlink_to(release, target_is_directory=True)
    os.replace(temporary_link, link)
    _prune_releases(repository, releases, keep={target_commit}, limit=3)
    return _venv_executable(release / "venv", "cope")


def _update_root(client_name: str) -> Path:
    configured = os.environ.get("COPE_UPDATE_ROOT", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    state_home = os.environ.get("XDG_STATE_HOME", "").strip()
    base = Path(state_home).expanduser() if state_home else Path.home() / ".local" / "state"
    return (base / f"cope-{client_name}").resolve()


def _repository_clone_url(repository_url: str, client_name: str) -> str:
    configured = os.environ.get("COPE_UPDATE_REPOSITORY_URL", "").strip()
    if configured:
        if _normalise_repository_url(configured) != _normalise_repository_url(repository_url):
            raise RuntimeError(
                f"configured {client_name} repository does not match the deployment source"
            )
        return configured
    if "://" in repository_url or repository_url.startswith(("/", ".")):
        return repository_url
    return f"https://{repository_url}.git"


def _release_ready(release: Path, target_commit: str) -> bool:
    executable = _venv_executable(release / "venv", "cope")
    if not executable.is_file():
        return False
    result = _run([str(executable), "version"], check=False, capture=True)
    return f"version={target_commit}" in result


def _prune_releases(
    repository: Path,
    releases: Path,
    *,
    keep: set[str],
    limit: int,
) -> None:
    candidates = sorted(
        (
            path
            for path in releases.iterdir()
            if path.is_dir() and COMMIT_PATTERN.fullmatch(path.name) and path.name not in keep
        ),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for release in candidates[max(limit - len(keep), 0) :]:
        _remove_release(repository, release)


def _remove_release(repository: Path, release: Path) -> None:
    source = release / "source"
    if source.exists():
        _run(
            [
                "git",
                "-C",
                str(repository),
                "worktree",
                "remove",
                "--force",
                str(source),
            ],
            check=False,
        )
    shutil.rmtree(release, ignore_errors=True)


def _venv_executable(venv: Path, name: str) -> Path:
    if os.name == "nt":
        return venv / "Scripts" / f"{name}.exe"
    return venv / "bin" / name


def _git_output(repository: Path, *arguments: str) -> str:
    return _run(
        ["git", "-C", str(repository), *arguments],
        capture=True,
    ).strip()


def _normalise_repository_url(value: str) -> str:
    cleaned = value.strip().rstrip("/").removesuffix(".git")
    if "://" in cleaned:
        parsed = urlsplit(cleaned)
        host = parsed.hostname or ""
        if parsed.port is not None:
            host = f"{host}:{parsed.port}"
        return f"{host}/{parsed.path.lstrip('/')}".lower()
    if ":" in cleaned and not cleaned.startswith(("/", ".")):
        authority, path = cleaned.split(":", 1)
        host = authority.rsplit("@", 1)[-1]
        return f"{host}/{path.lstrip('/')}".lower()
    return cleaned.lower()


def _run(
    command: list[str],
    *,
    check: bool = True,
    capture: bool = False,
) -> str:
    LOG.info("running command=%s", " ".join(command))
    result = subprocess.run(
        command,
        capture_output=capture,
        check=False,
        text=True,
    )
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(
            f"{command[0]} exited with status {result.returncode}: {detail[-3000:]}"
        )
    return (result.stdout or "").strip()
