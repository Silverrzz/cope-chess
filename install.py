from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
import platform
import secrets
import shlex
import shutil
import socket
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parent
RUNTIME = ROOT / ".cope-worker" / "installer"
IMAGE = "cope-chess:local"


@dataclass(frozen=True)
class Target:
    key: str
    label: str
    detail: str


TARGETS = (
    Target("server", "Server platform", "Docker Compose web, database, scheduler, and APIs"),
    Target("worker", "Game worker", "Runs chess games assigned by a COPE server"),
    Target("benchmarker", "Benchmarker", "Builds engines and records hardware benchmarks"),
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python install.py")
    parser.add_argument(
        "--targets",
        help="comma-separated targets: server, worker, benchmarker",
    )
    parser.add_argument(
        "--prepare-only",
        action="store_true",
        help="build and configure selected targets without starting them",
    )
    parser.add_argument(
        "--list-targets",
        action="store_true",
        help="print available targets and exit",
    )
    args = parser.parse_args(argv)
    if args.list_targets:
        for target in TARGETS:
            print(f"{target.key}\t{target.label}\t{target.detail}")
        return 0
    _check_repository()
    selected = _parse_targets(args.targets) if args.targets else _select_targets()
    if not selected:
        print("Nothing selected.")
        return 0
    settings = _load_settings()
    print(f"\nHost: {platform.system()} {platform.release()} ({platform.machine()})")
    print("Selected: " + ", ".join(_target(key).label for key in selected))
    if "server" in selected:
        settings = _prepare_server(settings, start=not args.prepare_only)
    clients = [key for key in selected if key in {"worker", "benchmarker"}]
    if clients:
        _prepare_clients(clients, image_ready="server" in selected)
        settings = _configure_clients(
            clients,
            settings,
            local_server="server" in selected,
        )
        if not args.prepare_only:
            for key in clients:
                _start_client(key, settings)
    _save_settings(settings)
    print("\nCOPE installation complete.")
    if args.prepare_only:
        print("Targets were prepared but not started.")
    if "server" in selected and not args.prepare_only:
        print(f"Server: https://{settings['domain']}")
    for key in clients:
        if not args.prepare_only:
            print(f"{_target(key).label} log: docker logs --follow {_container_name(key)}")
    return 0


def _check_repository() -> None:
    required = (ROOT / "pyproject.toml", ROOT / "compose.yaml", ROOT / "cope")
    if not all(path.exists() for path in required):
        raise SystemExit("Run this script from a cloned cope-chess repository.")
    if sys.version_info < (3, 11):
        raise SystemExit("Python 3.11 or newer is required.")


def _parse_targets(value: str) -> list[str]:
    selected = []
    valid = {target.key for target in TARGETS}
    for raw in value.split(","):
        key = raw.strip().lower()
        if not key:
            continue
        if key not in valid:
            raise SystemExit(
                f"Unknown target {key!r}; choose from {', '.join(sorted(valid))}."
            )
        if key not in selected:
            selected.append(key)
    return selected


def _select_targets() -> list[str]:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        return _select_targets_text()
    selected: set[int] = set()
    cursor = 0
    while True:
        print("\033[2J\033[H", end="")
        print("COPE installer\n")
        print("Choose what to build and run on this machine.")
        print("Use Up/Down to move, Space to tick, and Enter to continue.\n")
        for index, target in enumerate(TARGETS):
            pointer = ">" if index == cursor else " "
            tick = "x" if index in selected else " "
            print(f"{pointer} [{tick}] {target.label}")
            print(f"      {target.detail}")
        print("\nPress q to cancel.")
        key = _read_key()
        if key == "up":
            cursor = (cursor - 1) % len(TARGETS)
        elif key == "down":
            cursor = (cursor + 1) % len(TARGETS)
        elif key == "space":
            if cursor in selected:
                selected.remove(cursor)
            else:
                selected.add(cursor)
        elif key == "enter":
            return [target.key for index, target in enumerate(TARGETS) if index in selected]
        elif key in {"q", "escape"}:
            return []
        elif key == "interrupt":
            raise KeyboardInterrupt


def _select_targets_text() -> list[str]:
    print("COPE installer\n")
    for index, target in enumerate(TARGETS, start=1):
        print(f"  {index}. {target.label} - {target.detail}")
    value = input("\nEnter comma-separated numbers to tick: ").strip()
    if not value:
        return []
    selected = []
    for raw in value.split(","):
        try:
            index = int(raw.strip())
            if index < 1:
                raise ValueError
            target = TARGETS[index - 1]
        except (ValueError, IndexError):
            raise SystemExit(f"Invalid selection: {raw.strip()!r}") from None
        if target.key not in selected:
            selected.append(target.key)
    return selected


def _read_key() -> str:
    if os.name == "nt":
        import msvcrt

        key = msvcrt.getwch()
        if key in {"\x00", "\xe0"}:
            code = msvcrt.getwch()
            return {"H": "up", "P": "down"}.get(code, "")
        return {
            "\r": "enter",
            " ": "space",
            "\x1b": "escape",
            "\x03": "interrupt",
            "q": "q",
            "Q": "q",
        }.get(key, "")
    import termios
    import tty

    descriptor = sys.stdin.fileno()
    previous = termios.tcgetattr(descriptor)
    try:
        tty.setraw(descriptor)
        key = sys.stdin.read(1)
        if key == "\x1b":
            suffix = sys.stdin.read(2)
            return {"[A": "up", "[B": "down"}.get(suffix, "escape")
        return {
            "\r": "enter",
            "\n": "enter",
            " ": "space",
            "\x03": "interrupt",
            "q": "q",
            "Q": "q",
        }.get(key, "")
    finally:
        termios.tcsetattr(descriptor, termios.TCSADRAIN, previous)


def _target(key: str) -> Target:
    return next(target for target in TARGETS if target.key == key)


def _prepare_server(settings: dict[str, str], *, start: bool) -> dict[str, str]:
    _require_command("docker")
    _run(["docker", "compose", "version"])
    domain = _prompt(
        "Public DNS name for this server",
        settings.get("domain")
        or _env_value("COPE_DOMAIN")
        or _example_env_value("COPE_DOMAIN")
        or "localhost",
    )
    if "://" in domain or "/" in domain:
        raise SystemExit("Enter a DNS name without a scheme or path.")
    settings["domain"] = domain
    repository_url = _git_value("remote", "get-url", "origin")
    _ensure_env(domain, repository_url)
    _ensure_secrets()
    version = _build_version()
    print("\nBuilding the server platform...")
    _run(
        [
            "docker",
            "compose",
            "build",
            "--build-arg",
            f"COPE_BUILD_VERSION={version}",
        ]
    )
    if start:
        print("Starting the server platform...")
        _run(
            [
                "docker",
                "compose",
                "up",
                "-d",
                "--no-build",
                "--remove-orphans",
                "--wait",
                "--wait-timeout",
                "180",
            ]
        )
    return settings


def _prepare_clients(clients: list[str], *, image_ready: bool) -> None:
    _require_command("docker")
    _run(["docker", "version", "--format", "{{.Server.Version}}"])
    if not image_ready:
        print(
            "\nBuilding "
            + ", ".join(_target(key).label.lower() for key in clients)
            + " image..."
        )
        _run(
            [
                "docker",
                "build",
                "--build-arg",
                f"COPE_BUILD_VERSION={_build_version()}",
                "--tag",
                IMAGE,
                str(ROOT),
            ]
        )


def _configure_clients(
    clients: list[str],
    settings: dict[str, str],
    *,
    local_server: bool,
) -> dict[str, str]:
    domain = settings.get("domain", "")
    for key in clients:
        setting = f"{key}_server_url"
        path = "worker" if key == "worker" else "benchmarker"
        default = settings.get(setting, "")
        if local_server:
            default = f"wss://{domain}/{path}"
        if not default:
            port = "8702" if key == "worker" else "8703"
            default = f"ws://127.0.0.1:{port}"
        settings[setting] = _prompt(f"{_target(key).label} server URL", default)
    return settings


def _start_client(key: str, settings: dict[str, str]) -> None:
    RUNTIME.mkdir(parents=True, exist_ok=True)
    container = _container_name(key)
    status = _container_status(container)
    if status == "running":
        print(f"{_target(key).label} is already running in {container}.")
        return
    if status:
        _run(["docker", "rm", container])
    state_path = (
        RUNTIME / "worker.json"
        if key == "worker"
        else RUNTIME / "benchmarker.session"
    )
    token = None
    if not state_path.is_file():
        token = _registration_token(key, settings)
        token_path = RUNTIME / f"{key}.token"
        token_path.write_text(token + "\n", encoding="utf-8")
        _restrict_file(token_path)
    command = [
        "docker",
        "run",
        "--detach",
        "--name",
        container,
        "--init",
        "--restart",
        "unless-stopped",
        "--user",
        "0:0",
        "--mount",
        f"type=bind,source={RUNTIME},target=/state",
        "--mount",
        "type=bind,source=/var/run/docker.sock,target=/var/run/docker.sock",
        "--mount",
        f"type=volume,source={_cache_volume()},target=/root/.cope-worker/engines",
        "--env",
        f"COPE_BUILD_VERSION={_build_version()}",
        "--entrypoint",
        "/bin/sh",
        IMAGE,
        "-c",
        'state="$1"; token="$2"; shift 2; if [ -s "$state" ]; then exec cope "$@"; '
        'else exec cope "$@" --token-file "$token"; fi',
        "cope-bootstrap",
        "/state/worker.json" if key == "worker" else "/state/benchmarker.session",
        f"/state/{key}.token",
    ]
    command.extend(
        [
            key,
            "--server-url",
            settings[f"{key}_server_url"],
            "--label-hint",
            f"{socket.gethostname()}-{key}",
        ]
    )
    if key == "worker":
        command.extend(["--state-file", "/state/worker.json"])
    else:
        command.extend(["--session-file", "/state/benchmarker.session"])
    print(f"Starting {_target(key).label.lower()}...")
    _run(command, capture=True)
    print(f"{_target(key).label} started in {container}.")


def _registration_token(key: str, settings: dict[str, str]) -> str:
    domain = settings.get("domain", "")
    expected = f"wss://{domain}/{'worker' if key == 'worker' else 'benchmarker'}"
    if domain and settings.get(f"{key}_server_url") == expected:
        command = "mint-worker-token" if key == "worker" else "mint-benchmarker-token"
        label = f"{socket.gethostname()}-{key}"
        output = _run(
            ["docker", "compose", "exec", "-T", "web", "cope", command, label],
            capture=True,
        )
        for line in output.splitlines():
            if line.startswith("token="):
                return line.removeprefix("token=").strip()
        raise SystemExit(f"The server did not return a {_target(key).label.lower()} token.")
    token = getpass.getpass(f"One-time {_target(key).label.lower()} token: ").strip()
    if not token:
        raise SystemExit(f"A token is required to register the {_target(key).label.lower()}.")
    return token


def _ensure_env(domain: str, repository_url: str) -> None:
    path = ROOT / ".env"
    if path.exists():
        content = path.read_text(encoding="utf-8")
    else:
        example = ROOT / ".env.example"
        content = example.read_text(encoding="utf-8") if example.exists() else ""
    content = _set_env_value(content, "COPE_DOMAIN", domain)
    if repository_url:
        content = _set_env_value(content, "COPE_UPDATE_REPOSITORY_URL", repository_url)
    path.write_text(content, encoding="utf-8")


def _set_env_value(content: str, key: str, value: str) -> str:
    lines = content.splitlines()
    replacement = f"{key}={value}"
    for index, line in enumerate(lines):
        if line.startswith(f"{key}="):
            lines[index] = replacement
            break
    else:
        if lines and lines[-1]:
            lines.append("")
        lines.append(replacement)
    return "\n".join(lines) + "\n"


def _env_value(key: str) -> str:
    path = ROOT / ".env"
    if not path.is_file():
        return ""
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{key}="):
            return line.partition("=")[2].strip()
    return ""


def _example_env_value(key: str) -> str:
    path = ROOT / ".env.example"
    if not path.is_file():
        return ""
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{key}="):
            return line.partition("=")[2].strip()
    return ""


def _ensure_secrets() -> None:
    directory = ROOT / "secrets"
    directory.mkdir(parents=True, exist_ok=True)
    for name in ("admin_token", "event_token", "db_password"):
        path = directory / name
        if path.is_file() and path.stat().st_size:
            continue
        path.write_text(secrets.token_urlsafe(48) + "\n", encoding="utf-8")
        _restrict_file(path)


def _load_settings() -> dict[str, str]:
    path = RUNTIME / "settings.json"
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return {
        str(key): str(value)
        for key, value in payload.items()
        if isinstance(key, str) and isinstance(value, str)
    }


def _save_settings(settings: dict[str, str]) -> None:
    RUNTIME.mkdir(parents=True, exist_ok=True)
    path = RUNTIME / "settings.json"
    path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    _restrict_file(path)


def _prompt(label: str, default: str) -> str:
    suffix = f" [{default}]" if default else ""
    value = input(f"{label}{suffix}: ").strip()
    return value or default


def _require_command(name: str) -> None:
    if shutil.which(name) is None:
        raise SystemExit(
            f"{name} is required for the selected target. Install it and run this script again."
        )


def _build_version() -> str:
    commit = _git_value("rev-parse", "HEAD")
    if len(commit) == 40 and all(character in "0123456789abcdef" for character in commit):
        return commit
    return (ROOT / "cope" / "VERSION").read_text(encoding="utf-8").strip()


def _git_value(*arguments: str) -> str:
    if shutil.which("git") is None:
        return ""
    result = subprocess.run(
        ["git", "-C", str(ROOT), *arguments],
        capture_output=True,
        check=False,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def _run(
    command: list[str],
    *,
    capture: bool = False,
) -> str:
    display = subprocess.list2cmdline(command) if os.name == "nt" else shlex.join(command)
    print(f"  {display}")
    result = subprocess.run(
        command,
        cwd=ROOT,
        capture_output=capture,
        check=False,
        text=True,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise SystemExit(
            f"{command[0]} exited with status {result.returncode}"
            + (f":\n{detail[-4000:]}" if detail else ".")
        )
    return result.stdout.strip() if capture else ""


def _container_name(key: str) -> str:
    digest = hashlib.sha256(str(ROOT).lower().encode("utf-8")).hexdigest()[:10]
    return f"cope-chess-{key}-{digest}"


def _cache_volume() -> str:
    digest = hashlib.sha256(str(ROOT).lower().encode("utf-8")).hexdigest()[:10]
    return f"cope-chess-engine-cache-{digest}"


def _container_status(name: str) -> str:
    result = subprocess.run(
        ["docker", "inspect", "--format", "{{.State.Status}}", name],
        cwd=ROOT,
        capture_output=True,
        check=False,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def _restrict_file(path: Path) -> None:
    if os.name != "nt":
        path.chmod(0o600)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nInstallation cancelled.", file=sys.stderr)
        raise SystemExit(130) from None
