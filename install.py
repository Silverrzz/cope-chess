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
INSTALLER_VERSION = "3"


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
    repository_url = _prompt(
        "Git repository URL used for deployments",
        settings.get("repository_url")
        or _env_value("COPE_UPDATE_REPOSITORY_URL")
        or _git_value("remote", "get-url", "origin"),
    )
    if not repository_url:
        raise SystemExit("A Git repository URL is required for safe deployments.")
    configured_origin = _git_value("remote", "get-url", "origin")
    if configured_origin and _normalise_repository_url(repository_url) != _normalise_repository_url(
        configured_origin
    ):
        raise SystemExit("The deployment repository URL must match this checkout's origin.")
    update_ref = _prompt(
        "Git branch or ref used for deployments",
        settings.get("update_ref") or _env_value("COPE_UPDATE_REF") or "main",
    )
    if not update_ref or any(character.isspace() for character in update_ref):
        raise SystemExit("The deployment Git ref cannot be empty or contain whitespace.")
    compose_project = _prompt(
        "Docker Compose project name",
        settings.get("compose_project")
        or _env_value("COPE_COMPOSE_PROJECT")
        or "cope-chess",
    )
    if not _valid_compose_project(compose_project):
        raise SystemExit(
            "The Docker Compose project must start with a letter or digit and contain only letters, digits, hyphens, and underscores."
        )
    settings["repository_url"] = repository_url
    settings["update_ref"] = update_ref
    settings["compose_project"] = compose_project
    _ensure_env(domain, repository_url, update_ref, compose_project)
    _ensure_secrets()
    version = _build_version()
    print("\nBuilding the server platform...")
    _run(
        [
            *_compose_prefix(compose_project),
            "build",
            "--build-arg",
            f"COPE_BUILD_VERSION={version}",
        ]
    )
    if start:
        print("Starting the server platform...")
        try:
            _run(
                [
                    *_compose_prefix(compose_project),
                    "up",
                    "-d",
                    "--no-build",
                    "--remove-orphans",
                    "--wait",
                    "--wait-timeout",
                    "180",
                ]
            )
        except SystemExit:
            _print_compose_logs(compose_project, "migrate")
            raise
        _verify_server_deployment(compose_project, version)
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
            port = "8702" if key == "worker" else "8703"
            service = "worker-server" if key == "worker" else "benchmark-server"
            default = f"ws://{service}:{port}/{path}"
        if not default:
            port = "8702" if key == "worker" else "8703"
            default = f"ws://127.0.0.1:{port}"
        settings[setting] = _prompt(f"{_target(key).label} server URL", default)
    return settings


def _start_client(key: str, settings: dict[str, str]) -> None:
    RUNTIME.mkdir(parents=True, exist_ok=True)
    machine_id = _client_machine_id(key)
    state_path = (
        RUNTIME / "worker.json"
        if key == "worker"
        else RUNTIME / "benchmarker.session"
    )
    container = _container_name(key)
    status = _container_status(container)
    current_installer_version = _container_label(container, "cope-chess.installer-version")
    current_build_version = _container_label(container, "cope-chess.build-version")
    current_machine_id = _container_label(container, "cope-chess.machine-id")
    current_image_id = _container_image_id(container)
    desired_image_id = _image_id(IMAGE)
    build_version = _build_version()
    if (
        status == "running"
        and current_installer_version == INSTALLER_VERSION
        and current_build_version == build_version
        and current_machine_id == machine_id
        and current_image_id == desired_image_id
        and state_path.is_file()
    ):
        print(f"{_target(key).label} is already running in {container}.")
        return
    if status:
        _run(["docker", "rm", "--force", container])
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
        "--label",
        f"cope-chess.installer-version={INSTALLER_VERSION}",
        "--label",
        f"cope-chess.build-version={build_version}",
        "--label",
        f"cope-chess.machine-id={machine_id}",
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
        f"COPE_BUILD_VERSION={build_version}",
    ]
    if settings[f"{key}_server_url"].startswith(
        ("ws://worker-server:", "ws://benchmark-server:")
    ):
        command.extend(["--network", _compose_network()])
    if key == "worker":
        update_root = RUNTIME / "update"
        update_root.mkdir(parents=True, exist_ok=True)
        command.extend(
            [
                "--mount",
                f"type=bind,source={update_root},target=/update",
                "--mount",
                f"type=bind,source={ROOT},target=/update/repository",
                "--env",
                "COPE_UPDATE_ROOT=/update",
            ]
        )
    command.extend(
        [
            "--entrypoint",
            "/bin/sh",
            IMAGE,
            "-c",
            'binary=cope; if [ -x /update/current/venv/bin/cope ]; then '
            'binary=/update/current/venv/bin/cope; fi; state="$1"; token="$2"; '
            'shift 2; if [ -s "$state" ]; then exec "$binary" "$@"; '
            'else exec "$binary" "$@" --token-file "$token"; fi',
            "cope-bootstrap",
            "/state/worker.json" if key == "worker" else "/state/benchmarker.session",
            f"/state/{key}.token",
        ]
    )
    command.extend(
        [
            key,
            "--server-url",
            settings[f"{key}_server_url"],
            "--label-hint",
            f"{socket.gethostname()}-{key}",
            "--machine-id",
            machine_id,
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
    path = "worker" if key == "worker" else "benchmarker"
    service = "worker-server" if key == "worker" else "benchmark-server"
    port = "8702" if key == "worker" else "8703"
    expected = {
        f"wss://{domain}/{path}",
        f"ws://{service}:{port}/{path}",
    }
    if domain and settings.get(f"{key}_server_url") in expected:
        command = "mint-worker-token" if key == "worker" else "mint-benchmarker-token"
        label = f"{socket.gethostname()}-{key}"
        output = _run(
            [
                *_compose_prefix(settings.get("compose_project", "cope-chess")),
                "exec",
                "-T",
                "web",
                "cope",
                command,
                label,
            ],
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


def _ensure_env(
    domain: str,
    repository_url: str,
    update_ref: str,
    compose_project: str,
) -> None:
    path = ROOT / ".env"
    if path.exists():
        content = path.read_text(encoding="utf-8")
    else:
        example = ROOT / ".env.example"
        content = example.read_text(encoding="utf-8") if example.exists() else ""
    content = _set_env_value(content, "COPE_DOMAIN", domain)
    content = _set_env_value(content, "COPE_UPDATE_REPOSITORY_URL", repository_url)
    content = _set_env_value(content, "COPE_UPDATE_REF", update_ref)
    content = _set_env_value(content, "COPE_COMPOSE_PROJECT", compose_project)
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
        if not path.is_file() or not path.stat().st_size:
            path.write_text(secrets.token_urlsafe(48) + "\n", encoding="utf-8")
        _make_container_readable(path)


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


def _compose_prefix(project: str) -> list[str]:
    return [
        "docker",
        "compose",
        "--project-name",
        project,
        "--project-directory",
        str(ROOT),
        "--file",
        str(ROOT / "compose.yaml"),
    ]


def _valid_compose_project(value: str) -> bool:
    return bool(value) and value[0].isalnum() and all(
        character.isalnum() or character in "-_" for character in value
    )


def _print_compose_logs(project: str, service: str) -> None:
    result = subprocess.run(
        [*_compose_prefix(project), "logs", "--no-color", "--tail", "80", service],
        cwd=ROOT,
        capture_output=True,
        check=False,
        text=True,
    )
    detail = (result.stdout or result.stderr or "").strip()
    if detail:
        print(f"\n{service} diagnostics:\n{detail[-6000:]}", file=sys.stderr)


def _normalise_repository_url(value: str) -> str:
    cleaned = value.strip().rstrip("/").removesuffix(".git").lower()
    if "@" in cleaned and ":" in cleaned and "://" not in cleaned:
        authority, path = cleaned.split(":", 1)
        return f"{authority.rsplit('@', 1)[-1]}/{path.lstrip('/')}"
    if "://" in cleaned:
        scheme, remainder = cleaned.split("://", 1)
        del scheme
        return remainder.rsplit("@", 1)[-1]
    return cleaned


def _verify_server_deployment(project: str, expected_version: str) -> None:
    container_id = _run(
        [*_compose_prefix(project), "ps", "--quiet", "web"],
        capture=True,
    )
    if not container_id:
        raise SystemExit("The web service did not start.")
    actual_project = _container_label(container_id, "com.docker.compose.project")
    if actual_project != project:
        raise SystemExit(
            f"The web service belongs to Compose project {actual_project!r}, expected {project!r}."
        )
    output = _run(
        [*_compose_prefix(project), "exec", "-T", "web", "cope", "version"],
        capture=True,
    )
    marker = f"version={expected_version}"
    if marker not in output:
        raise SystemExit(
            f"The running web service does not contain the requested build {expected_version}."
        )
    status = _run(
        ["docker", "inspect", "--format", "{{.State.Health.Status}}", container_id],
        capture=True,
    )
    if status != "healthy":
        raise SystemExit(f"The running web service failed deployment checks: {status or 'unknown'}.")


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


def _client_machine_id(key: str) -> str:
    path = RUNTIME / f"{key}.machine-id"
    try:
        machine_id = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        machine_id = secrets.token_hex(32)
        path.write_text(machine_id + "\n", encoding="utf-8")
        _restrict_file(path)
    if not 8 <= len(machine_id) <= 128:
        raise SystemExit(f"Invalid persisted {key} machine identity in {path}.")
    return machine_id


def _container_status(name: str) -> str:
    result = subprocess.run(
        ["docker", "inspect", "--format", "{{.State.Status}}", name],
        cwd=ROOT,
        capture_output=True,
        check=False,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def _container_image_id(name: str) -> str:
    result = subprocess.run(
        ["docker", "inspect", "--format", "{{.Image}}", name],
        cwd=ROOT,
        capture_output=True,
        check=False,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def _image_id(name: str) -> str:
    result = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{.Id}}", name],
        cwd=ROOT,
        capture_output=True,
        check=False,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def _container_label(name: str, label: str) -> str:
    quoted_label = json.dumps(label)
    result = subprocess.run(
        [
            "docker",
            "inspect",
            "--format",
            f"{{{{index .Config.Labels {quoted_label}}}}}",
            name,
        ],
        cwd=ROOT,
        capture_output=True,
        check=False,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def _compose_network() -> str:
    project = _env_value("COPE_COMPOSE_PROJECT") or "cope-chess"
    service = subprocess.run(
        [*_compose_prefix(project), "ps", "--quiet", "worker-server"],
        cwd=ROOT,
        capture_output=True,
        check=False,
        text=True,
    )
    container_id = service.stdout.strip()
    if not container_id:
        raise SystemExit("The local worker server is not running.")
    inspection = subprocess.run(
        ["docker", "inspect", "--format", "{{json .NetworkSettings.Networks}}", container_id],
        cwd=ROOT,
        capture_output=True,
        check=False,
        text=True,
    )
    try:
        networks = json.loads(inspection.stdout)
    except json.JSONDecodeError:
        networks = {}
    if inspection.returncode != 0 or not isinstance(networks, dict) or not networks:
        raise SystemExit("Could not determine the local COPE Docker network.")
    return next(iter(networks))


def _restrict_file(path: Path) -> None:
    if os.name != "nt":
        path.chmod(0o600)


def _make_container_readable(path: Path) -> None:
    if os.name != "nt":
        path.chmod(0o644)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nInstallation cancelled.", file=sys.stderr)
        raise SystemExit(130) from None
