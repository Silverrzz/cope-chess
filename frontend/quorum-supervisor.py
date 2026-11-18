from __future__ import annotations

import os
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from quorum_updates import (ReleaseError, atomic_json, checked_artifact, load_policy, read_feed, read_json,
                            require, unpack, verify_manifest)


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


POLL_SECONDS = 60
STARTUP_SECONDS = 20


def discover(cache, policy, node_major, current):
    envelope = read_feed(policy)
    release = verify_manifest(envelope, policy, node_major)
    if current and release["digest"] == current["digest"]:
        return None
    require(release["sequence"] >= policy["minimum_sequence"]
            and (not current or release["sequence"] > current["sequence"]),
            "The release is below this verifier's allowed sequence.")
    path = checked_artifact(cache, release, policy)
    # Validate the release layout before displaying an offer for approval.
    with tempfile.TemporaryDirectory(dir=cache) as directory:
        unpack(path, Path(directory), release["artifact"])
    return envelope, release


def run(root: Path, config: dict) -> int:
    cache = root / "artifacts"
    cache.mkdir(mode=0o700, exist_ok=True)
    archive = Path(os.environ.get("COPE_VERIFIER_ARCHIVE") or config.get("archive") or root / "state").expanduser().resolve()
    node = config["node"]
    node_major = int(subprocess.check_output([node, "-p", "process.versions.node.split('.')[0]"], timeout=10))
    policy_path = os.environ.get("COPE_VERIFIER_RELEASE_POLICY")
    policy = load_policy(Path(policy_path)) if policy_path else None
    active_file = root / "approved-v1.json"
    status_file = root / "update-status-v1.json"
    approval_file = root / "update-approval-v1.json"
    approval_file.unlink(missing_ok=True)
    current = None
    try:
        active = read_json(active_file)
    except FileNotFoundError:
        active = None
    if active is not None:
        require(policy is not None, "The installed release requires its locally pinned release policy.")
        # Expiry limits approval, not continued execution of an already approved release.
        current = verify_manifest(active, policy, node_major, fresh=False)

    env = {**os.environ, "COPE_VERIFIER_ARCHIVE": str(archive), "COPE_VERIFIER_UPDATE_STATUS": str(status_file),
           "COPE_VERIFIER_UPDATE_APPROVAL": str(approval_file)}

    def interrupted(_signal, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupted)

    for stale in root.glob("run-*"):
        if stale.is_dir() and not stale.is_symlink():
            shutil.rmtree(stale)
    runtime = Path(tempfile.mkdtemp(prefix="run-", dir=root))

    def start(release):
        workspace = Path(tempfile.mkdtemp(dir=runtime))
        process = None
        try:
            if release:
                artifact = checked_artifact(cache, release)
                unpack(artifact, workspace, release["artifact"])
                script = workspace / "quorum-node.mjs"
            else:
                script = Path(config["bootstrap"]) / "quorum-node.mjs"
            ready, activate = workspace / "ready", workspace / "activate"
            process = subprocess.Popen(
                [node, str(script), config["origin"]], cwd=script.parent,
                env={**env, "COPE_VERIFIER_BUILD_COMMIT": release["commit"] if release else config["commit"],
                     "COPE_VERIFIER_READY_FILE": str(ready), "COPE_VERIFIER_ACTIVATE_FILE": str(activate)},
            )
            deadline = time.monotonic() + STARTUP_SECONDS
            while not ready.is_file():
                require(process.poll() is None and time.monotonic() < deadline,
                        "The verifier did not become ready.")
                time.sleep(0.1)
            # Let immediate listener/import failures surface before committing the switch.
            time.sleep(1)
            require(process.poll() is None, "The verifier exited during startup.")
            return process, workspace, activate
        except BaseException:
            if process:
                stop(process)
            shutil.rmtree(workspace, ignore_errors=True)
            raise

    def status(state, *, message=None, offer=None):
        atomic_json(status_file, {"state": state, "message": message, "offer": offer,
                                  "commit": current["commit"] if current else config["commit"],
                                  "sequence": current["sequence"] if current else None,
                                  "threshold": policy["threshold"] if policy else None})

    def prune(offer=None):
        keep = {entry["artifact"]["sha256"] + ".zip" for entry in (current, offer) if entry}
        for path in cache.glob("*.zip"):
            if path.name not in keep:
                path.unlink(missing_ok=True)

    pool = ThreadPoolExecutor(max_workers=1)
    future = offer = envelope = None
    next_check = 0
    process = None
    try:
        status("checking" if policy else "disabled")
        process, workspace, activate = start(current)
        activate.touch()
        while process.poll() is None:
            if policy and future is None and time.monotonic() >= next_check:
                future = pool.submit(discover, cache, policy, node_major, current)
            if future is not None and future.done():
                try:
                    discovered = future.result()
                    if discovered is None:
                        offer = envelope = None
                        status("current")
                    else:
                        candidate_envelope, release = discovered
                        challenge = offer["challenge"] if offer and offer["digest"] == release["digest"] else secrets.token_hex(32)
                        envelope, offer = candidate_envelope, {**release, "challenge": challenge}
                        status("available", offer=offer)
                except Exception as error:
                    offer = envelope = None
                    print(f"Quorum release discovery: {error}", file=sys.stderr, flush=True)
                    status("error", message=str(error) if isinstance(error, ReleaseError)
                           else "Could not download or read the release. See verifier logs.")
                prune(offer)
                future = None
                next_check = time.monotonic() + POLL_SECONDS

            if offer and future is None and approval_file.is_file():
                try:
                    approval = read_json(approval_file)
                except (OSError, ValueError):
                    approval = {}
                approval_file.unlink(missing_ok=True)
                if approval == {key: offer[key] for key in ("digest", "challenge")}:
                    candidate = None
                    stopped = False
                    status("applying", offer=offer)
                    try:
                        release = verify_manifest(envelope, policy, node_major)
                        require(release["digest"] == offer["digest"]
                                and release["sequence"] >= policy["minimum_sequence"]
                                and (not current or release["sequence"] > current["sequence"]),
                                "The approved release no longer matches the allowed offer.")
                        # Recheck the bytes before stopping the current service.
                        artifact = checked_artifact(cache, release)
                        with tempfile.TemporaryDirectory(dir=runtime) as directory:
                            unpack(artifact, Path(directory), release["artifact"])
                        stop(process)
                        stopped = True
                        candidate, candidate_workspace, candidate_activate = start(release)
                    except Exception as error:
                        print(f"Quorum verifier update failed: {error}", file=sys.stderr, flush=True)
                        if stopped:
                            shutil.rmtree(workspace, ignore_errors=True)
                            process, workspace, activate = start(current)
                            activate.touch()
                        status("failed", message="The approved release could not start. The previous release is still active.")
                    else:
                        try:
                            atomic_json(active_file, envelope)
                            current = release
                            candidate_activate.touch()
                        except BaseException:
                            stop(candidate)
                            raise
                        process = candidate
                        shutil.rmtree(workspace, ignore_errors=True)
                        workspace = candidate_workspace
                        status("installed")
                    offer = envelope = None
                    next_check = time.monotonic() + POLL_SECONDS
            time.sleep(0.25)
        return process.returncode or 0
    except KeyboardInterrupt:
        return 0
    finally:
        if process:
            stop(process)
        pool.shutdown(wait=False, cancel_futures=True)
        shutil.rmtree(runtime, ignore_errors=True)


if __name__ == "__main__":
    try:
        config_file = Path(sys.argv[1]).resolve()
        root = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else config_file.parent
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        with acquire_lock(root):
            raise SystemExit(run(root, read_json(config_file)))
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        raise SystemExit(f"Could not start the Quorum verifier: {error}") from error
