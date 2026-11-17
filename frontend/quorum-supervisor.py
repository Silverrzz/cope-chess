from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from pathlib import Path


def read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def stop(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def acquire_lock(root: Path):
    lock = (root / "verifier.lock").open("a+b")
    try:
        if os.name == "nt":
            import msvcrt

            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        lock.close()
        raise SystemExit("This verifier installation is already running.")
    return lock


def run(root: Path, config: dict) -> int:
    commit = config["commit"]
    script = root / "releases" / commit / "quorum-node.mjs"
    archive = Path(os.environ.get("COPE_VERIFIER_ARCHIVE") or root / "state").expanduser().resolve()
    env = {**os.environ, "COPE_VERIFIER_ARCHIVE": str(archive), "COPE_VERIFIER_BUILD_COMMIT": commit}

    def interrupted(_signal, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupted)
    process = subprocess.Popen([config["node"], str(script), config["origin"]], env=env)
    try:
        return process.wait()
    except KeyboardInterrupt:
        return 0
    finally:
        stop(process)


if __name__ == "__main__":
    config_file = Path(sys.argv[1]).resolve()
    with acquire_lock(config_file.parent):
        raise SystemExit(run(config_file.parent, read_json(config_file)))
