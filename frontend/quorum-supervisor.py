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
                            MAX_MANIFEST, require, unpack, verify_manifest)


def stop(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def acquire_lock(root: Path, name="verifier.lock"):
    descriptor = os.open(root / name, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    lock = os.fdopen(descriptor, "a+b")
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
        raise SystemExit("This verifier installation or signing archive is already in use.")
    return lock


POLL_SECONDS = 60
STARTUP_SECONDS = 20


def discover(cache, policy, node_major, highest):
    envelope = read_feed(policy)
    release = verify_manifest(envelope, policy, node_major)
    if highest and release["digest"] == highest["digest"]:
        return None
    require(release["sequence"] >= policy["minimum_sequence"]
            and (not highest or release["sequence"] > highest["sequence"]),
            "The release is below this verifier's allowed sequence.")
    path = checked_artifact(cache, release, policy)
    # Validate the release layout before displaying an offer for approval.
    with tempfile.TemporaryDirectory(dir=cache) as directory:
        unpack(path, Path(directory), release["artifact"])
    return envelope, release


def signing_archive(root: Path, config: dict) -> Path:
    return Path(os.environ.get("COPE_VERIFIER_ARCHIVE") or config.get("archive") or root / "state").expanduser().resolve()


def supervisor_owned(path: Path):
    for part in (path.resolve(), *path.resolve().parents):
        owner = part.stat()
        require(owner.st_uid == 0 and not owner.st_mode & 0o022,
                f"Untrusted permissions in the supervisor path: {part}")


def run(root: Path, config: dict) -> int:
    cache = root / "artifacts"
    archive = signing_archive(root, config)
    node = config["node"]
    policy_path = os.environ.get("COPE_VERIFIER_RELEASE_POLICY")
    policy = load_policy(Path(policy_path)) if policy_path else None
    worker_identity = {}
    privileged = sys.platform == "linux" and os.geteuid() == 0
    require(not policy or privileged, "Signed updates require a Linux supervisor and separate unprivileged verifier account.")
    if privileged:
        uid = int(os.environ.get("COPE_VERIFIER_WORKER_UID", "0"))
        gid = int(os.environ.get("COPE_VERIFIER_WORKER_GID", "0"))
        require(uid > 0 and gid > 0 and archive.stat().st_uid == uid,
                "Set COPE_VERIFIER_WORKER_UID and COPE_VERIFIER_WORKER_GID to the unprivileged archive owner's IDs.")
        worker_identity = {"user": uid, "group": gid, "extra_groups": [], "umask": 0o077}
    if policy:
        require(os.environ.get("COPE_VERIFIER_ADMIN_ORIGIN", "").startswith("https://"),
                "Signed updates require a dedicated HTTPS admin origin routed to the trusted admin port (8789).")
        require(os.environ.get("COPE_VERIFIER_ADMIN_ORIGIN") != os.environ.get("COPE_VERIFIER_PUBLIC_ORIGIN"),
                "The verifier's public and trusted admin origins must be different.")
        supervisor_owned(root)
        supervisor_owned(Path(config["bootstrap"]))
        supervisor_owned(Path(node))
        supervisor_owned(Path(policy_path))
        token_path = os.environ.get("COPE_VERIFIER_ADMIN_TOKEN_FILE")
        require(token_path, "Set a private token file for the trusted verifier admin service.")
        token = Path(token_path)
        supervisor_owned(token)
        require(token.is_file() and not token.stat().st_mode & 0o077,
                "The trusted verifier admin token must only be readable by the supervisor.")
    node_major = int(subprocess.check_output([node, "-p", "process.versions.node.split('.')[0]"], timeout=10))
    cache.mkdir(mode=0o700, exist_ok=True)
    active_file = root / "approved-v1.json"
    status_file = root / "update-status-v1.json"
    approval_file = root / "update-approval-v1.json"
    approval_file.unlink(missing_ok=True)
    current = fallback = highest = None
    try:
        approved = read_json(active_file, 4 * MAX_MANIFEST)
    except FileNotFoundError:
        approved = None
    if approved is not None:
        require(policy is not None, "The installed release requires its locally pinned release policy.")
    def installed(envelope):
        return verify_manifest(envelope, policy, node_major, fresh=False) if envelope else None

    active_envelope = fallback_envelope = highest_envelope = None
    if approved and approved.get("version") == "cope-quorum-verifier-installation/2":
        require(approved.keys() == {"version", "active", "fallback", "highest"}, "Invalid installed release record.")
        active_envelope, fallback_envelope, highest_envelope = (approved[key] for key in ("active", "fallback", "highest"))
        current, fallback, highest = map(installed, (active_envelope, fallback_envelope, highest_envelope))
        require(highest and (not current or current["sequence"] <= highest["sequence"])
                and (not fallback or current and fallback["sequence"] < current["sequence"]),
                "Invalid installed release sequence.")
    elif approved is not None:
        current = highest = installed(approved)
        active_envelope = highest_envelope = approved

    def save_installation():
        atomic_json(active_file, {"version": "cope-quorum-verifier-installation/2", "active": active_envelope,
                                  "fallback": fallback_envelope, "highest": highest_envelope})

    env = {**os.environ, "COPE_VERIFIER_ARCHIVE": str(archive)}
    for key in [name for name in env if name.startswith(("COPE_VERIFIER_ADMIN_", "COPE_VERIFIER_UPDATE_"))
                or name in {"COPE_VERIFIER_RELEASE_POLICY", "COPE_VERIFIER_TRUSTED_ADMIN"}]:
        if policy:
            env.pop(key)

    def interrupted(_signal, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupted)

    for stale in root.glob("run-*"):
        if stale.is_dir() and not stale.is_symlink():
            shutil.rmtree(stale)
    runtime = Path(tempfile.mkdtemp(prefix="run-", dir=root))
    if worker_identity:
        root.chmod(0o711)
        runtime.chmod(0o711)

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
            signals = workspace / "signals"
            signals.mkdir(mode=0o700)
            if worker_identity:
                workspace.chmod(0o755)
                os.chown(signals, worker_identity["user"], worker_identity["group"])
            ready, activate = signals / "ready", workspace / "activate"
            process = subprocess.Popen(
                [node, str(script), config["origin"]], cwd=script.parent,
                env={**env, "COPE_VERIFIER_BUILD_COMMIT": release["commit"] if release else config["commit"],
                     "COPE_VERIFIER_READY_FILE": str(ready), "COPE_VERIFIER_ACTIVATE_FILE": str(activate)},
                **worker_identity,
            )
            deadline = time.monotonic() + STARTUP_SECONDS
            while not ready.is_file():
                require(process.poll() is None and time.monotonic() < deadline,
                        "The verifier did not become ready.")
                time.sleep(0.1)
            activate.touch(mode=0o644)
            activate.chmod(0o644)
            # Let startup and early failures after activation surface before
            # promoting the candidate to active.
            time.sleep(2)
            require(process.poll() is None, "The verifier exited during activation.")
            return process, workspace
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
        keep = {entry["artifact"]["sha256"] + ".zip" for entry in (current, fallback, offer) if entry}
        for path in cache.glob("*.zip"):
            if path.name not in keep:
                path.unlink(missing_ok=True)

    pool = ThreadPoolExecutor(max_workers=1)
    future = offer = envelope = None
    next_check = 0
    process = None
    admin = None
    try:
        def recover():
            nonlocal current, fallback, active_envelope, fallback_envelope
            require(current is not None, "The bootstrap verifier could not start.")
            current, fallback = fallback, None
            active_envelope, fallback_envelope = fallback_envelope, None
            save_installation()
            return start(current)

        if highest and (not current or highest["digest"] != current["digest"]):
            status("failed", message="The last approved release is inactive. The previous release is active.")
        else:
            status("checking" if policy else "disabled")
        if policy:
            admin_script = Path(config["bootstrap"]) / "quorum-admin.mjs"
            admin = subprocess.Popen([node, str(admin_script)], cwd=admin_script.parent,
                                     env={**os.environ, "COPE_VERIFIER_TRUSTED_ADMIN": "1",
                                          "COPE_VERIFIER_UPDATE_STATUS": str(status_file),
                                          "COPE_VERIFIER_UPDATE_APPROVAL": str(approval_file)})
            time.sleep(0.5)
            require(admin.poll() is None, "The trusted verifier administration service could not start.")
        try:
            process, workspace = start(current)
        except Exception:
            process, workspace = recover()
            status("failed", message="The approved release could not start. The previous release is active.")
        while True:
            require(not admin or admin.poll() is None, "The trusted verifier administration service exited.")
            if process.poll() is not None:
                shutil.rmtree(workspace, ignore_errors=True)
                process, workspace = recover()
                status("failed", message="The approved release exited. The previous release is active.")
            if policy and future is None and time.monotonic() >= next_check:
                future = pool.submit(discover, cache, policy, node_major, highest)
            if future is not None and future.done():
                try:
                    discovered = future.result()
                    if discovered is None:
                        offer = envelope = None
                        if not highest or current and current["digest"] == highest["digest"]:
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
                                and (not highest or release["sequence"] > highest["sequence"]),
                                "The approved release no longer matches the allowed offer.")
                        # Recheck the bytes before stopping the current service.
                        artifact = checked_artifact(cache, release)
                        with tempfile.TemporaryDirectory(dir=runtime) as directory:
                            unpack(artifact, Path(directory), release["artifact"])
                        highest, highest_envelope = release, envelope
                        save_installation()
                        stop(process)
                        stopped = True
                        candidate, candidate_workspace = start(release)
                    except Exception as error:
                        print(f"Quorum verifier update failed: {error}", file=sys.stderr, flush=True)
                        if stopped:
                            shutil.rmtree(workspace, ignore_errors=True)
                            process, workspace = start(current)
                        status("failed", message="The approved release could not start. The previous release is still active.")
                    else:
                        try:
                            current, fallback = release, current
                            active_envelope, fallback_envelope = envelope, active_envelope
                            save_installation()
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
    except KeyboardInterrupt:
        return 0
    finally:
        if process:
            stop(process)
        if admin:
            stop(admin)
        pool.shutdown(wait=False, cancel_futures=True)
        shutil.rmtree(runtime, ignore_errors=True)


if __name__ == "__main__":
    try:
        config_file = Path(sys.argv[1]).resolve()
        root = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else config_file.parent
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if os.environ.get("COPE_VERIFIER_RELEASE_POLICY"):
            require(sys.platform == "linux" and os.geteuid() == 0,
                    "Signed updates require a Linux supervisor and separate unprivileged verifier account.")
            supervisor_owned(config_file)
            supervisor_owned(root)
        config = read_json(config_file)
        archive = signing_archive(root, config)
        archive.mkdir(mode=0o700, parents=True, exist_ok=True)
        with acquire_lock(root), acquire_lock(archive, "signer.lock"):
            raise SystemExit(run(root, config))
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        raise SystemExit(f"Could not start the Quorum verifier: {error}") from error
