from __future__ import annotations

import json
import os
import re
import tempfile
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path


DEFAULT_SERVER = "wss://cope-chess.live/worker"
STOP_ORDER = {"grace": 1, "safe": 2, "force": 3}


def default_home() -> Path:
    configured = os.environ.get("COPE_WORKER_HOME")
    if configured:
        return Path(configured).expanduser().resolve()
    state = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")
    return state / "cope-worker"


def read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, mode="w", encoding="utf-8", delete=False) as output:
        temporary = Path(output.name)
        try:
            json.dump(data, output)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
            output.close()
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


@contextmanager
def locked(path: Path, *, wait: bool = True):
    import fcntl

    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with os.fdopen(os.open(path, os.O_CREAT | os.O_RDWR, 0o600), "r+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB))
        yield


def is_running(home: Path) -> bool:
    if not (home / "supervisor.lock").exists():
        return False
    try:
        with locked(home / "supervisor.lock", wait=False):
            return False
    except BlockingIOError:
        return True


def request_stop(home: Path, mode: str) -> None:
    with locked(home / "control.lock"):
        current = read_json(home / "stop.json").get("mode")
        if STOP_ORDER.get(current, 0) < STOP_ORDER[mode]:
            write_json(home / "stop.json", {"mode": mode})


def parse_threads(value: str) -> int | None:
    if value.lower() == "auto":
        return None
    if not value.isascii() or not value.isdecimal() or not 0 < int(value) <= 1_000_000:
        raise ValueError("threads must be a positive integer or auto")
    return int(value)


def parse_memory(value: str) -> int | None:
    if value.lower() == "auto":
        return None
    match = re.fullmatch(r"([1-9][0-9]*)(MiB|GiB)", value, re.IGNORECASE)
    if not match:
        raise ValueError("memory must use MiB or GiB, or be set to auto")
    memory = int(match[1]) * (1024 if match[2].lower() == "gib" else 1)
    if memory > 2**31 - 1:
        raise ValueError("memory limit is too large")
    return memory


def parse_boolean(value: str) -> bool:
    if value.lower() not in {"true", "false"}:
        raise ValueError("expected true or false")
    return value.lower() == "true"


@dataclass(frozen=True)
class WorkerSettings:
    server: str = DEFAULT_SERVER
    token_file: str | None = None
    threads: int | None = None
    memory_mb: int | None = None
    auto_update: bool = True
    compose_file: str | None = None

    @classmethod
    def read(cls, home: Path, *, prefer_environment: bool = False) -> WorkerSettings:
        environment: dict = {}
        if os.environ.get("COPE_WORKER_SERVER_URL"):
            environment["server"] = os.environ["COPE_WORKER_SERVER_URL"]
        token = os.environ.get("COPE_WORKER_IDENTITY_TOKEN_FILE") or os.environ.get("COPE_WORKER_TOKEN_FILE")
        if token:
            environment["token_file"] = token
        if os.environ.get("COPE_WORKER_THREADS"):
            environment["threads"] = parse_threads(os.environ["COPE_WORKER_THREADS"])
        if os.environ.get("COPE_WORKER_MEMORY"):
            environment["memory_mb"] = parse_memory(os.environ["COPE_WORKER_MEMORY"])
        if os.environ.get("COPE_WORKER_AUTO_UPDATE"):
            environment["auto_update"] = parse_boolean(os.environ["COPE_WORKER_AUTO_UPDATE"])
        stored = read_json(home / "config.json")
        values = stored | environment if prefer_environment else environment | stored
        settings = cls(**values)
        if not isinstance(settings.auto_update, bool):
            raise ValueError("auto_update must be true or false")
        for limit, maximum in ((settings.threads, 1_000_000), (settings.memory_mb, 2**31 - 1)):
            if limit is not None and (type(limit) is not int or not 0 < limit <= maximum):
                raise ValueError("worker limits must be within the supported range or auto")
        if not isinstance(settings.server, str) or not settings.server.startswith(("wss://", "ws://")):
            raise ValueError("server must be a WebSocket URL")
        return settings

    def write(self, home: Path) -> None:
        write_json(home / "config.json", asdict(self))

    def credential_file(self, home: Path) -> Path:
        return Path(self.token_file).expanduser() if self.token_file else home / "token"
