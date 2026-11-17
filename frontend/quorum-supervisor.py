from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path


FILES = ("package.json", "package-lock.json", "quorum-node.mjs", "quorum-audit.mjs", "src/quorum.ts")
COMMIT = re.compile(r"^[0-9a-f]{40}$")


def read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def save_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(".new")
    temporary.write_text(json.dumps(value), encoding="utf-8")
    temporary.replace(path)


def stage_release(root: Path, config: dict, current: str, target: str) -> Path:
    repository = root / "repository.git"
    if not repository.is_dir():
        subprocess.run(["git", "init", "--bare", str(repository)], check=True, timeout=60)
    git = ["git", "--git-dir", str(repository)]
    branch = config["branch"]
    local_ref = "refs/heads/" + branch
    subprocess.run([*git, "fetch", "--no-tags", config["repository"], "+" + local_ref + ":" + local_ref],
                   check=True, timeout=180)
    for earlier, later in ((target, local_ref), (current, target)):
        if subprocess.run([*git, "merge-base", "--is-ancestor", earlier, later], timeout=60).returncode != 0:
            raise RuntimeError("The requested release is outside the trusted branch or would downgrade this verifier.")
    releases = root / "releases"
    release = releases / target
    if (release / "ready").is_file():
        return release
    with tempfile.TemporaryDirectory(prefix="stage-", dir=releases) as staging:
        staged = Path(staging) / "release"
        for file in FILES:
            destination = staged / file
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(subprocess.check_output([*git, "show", f"{target}:frontend/{file}"], timeout=60))
        subprocess.run([config["npm"], "--prefix", str(staged), "ci", "--omit=dev", "--ignore-scripts"],
                       check=True, timeout=300)
        subprocess.run([config["node"], "--check", str(staged / "quorum-node.mjs")], check=True, timeout=60)
        (staged / "ready").touch()
        staged.replace(release)
    return release


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
    active = root / "active.json"
    update_file = root / "update.json"
    failure_file = root / "update-failure.json"
    state = read_json(active)
    current = state.get("commit") if state.get("installed_from") == config["commit"] else config["commit"]
    if not isinstance(current, str) or not COMMIT.fullmatch(current) or not (root / "releases" / current / "quorum-node.mjs").is_file():
        current = config["commit"]
    origin = config["origin"]
    archive = Path(os.environ.get("COPE_VERIFIER_ARCHIVE") or root / "state").expanduser().resolve()
    env = {**os.environ, "COPE_VERIFIER_ARCHIVE": str(archive), "COPE_VERIFIER_UPDATE_FILE": str(update_file),
           "COPE_VERIFIER_UPDATE_FAILURE": str(failure_file)}
    update_file.unlink(missing_ok=True)

    def start(commit: str):
        script = root / "releases" / commit / "quorum-node.mjs"
        return subprocess.Popen([config["node"], str(script), origin],
                                env={**env, "COPE_VERIFIER_BUILD_COMMIT": commit})

    def interrupted(_signal, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupted)
    process = start(current)
    try:
        while process.poll() is None:
            time.sleep(1)
            offer = read_json(update_file)
            if not offer:
                continue
            update_file.unlink(missing_ok=True)
            target = offer.get("commit")
            job = offer.get("job_id")
            if not isinstance(job, int) or job < 1 or not isinstance(target, str) or not COMMIT.fullmatch(target):
                continue
            if target == current or read_json(failure_file).get("job_id") == job:
                continue
            switching = False
            try:
                if offer.get("repository_url") != config["repository_id"]:
                    raise RuntimeError("The requested update repository differs from this installation's trusted repository.")
                stage_release(root, config, current, target)
                switching = True
                stop(process)
                process = start(target)
                time.sleep(2)
                if process.poll() is not None:
                    raise RuntimeError("The new verifier exited during startup.")
                save_json(active, {"installed_from": config["commit"], "commit": target})
                failure_file.unlink(missing_ok=True)
                current = target
                print("Quorum verifier updated to " + target, flush=True)
            except (OSError, RuntimeError, subprocess.SubprocessError) as error:
                print("Quorum verifier update failed: " + str(error), file=sys.stderr, flush=True)
                save_json(failure_file, {"job_id": job})
                if switching:
                    stop(process)
                    process = start(current)
        return process.returncode or 0
    except KeyboardInterrupt:
        return 0
    finally:
        stop(process)


if __name__ == "__main__":
    config_file = Path(sys.argv[1]).resolve()
    with acquire_lock(config_file.parent):
        raise SystemExit(run(config_file.parent, read_json(config_file)))
