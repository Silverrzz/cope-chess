from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

from .local_state import WorkerSettings


class ContainerWorker:
    def __init__(self, settings: WorkerSettings, home: Path) -> None:
        if settings.compose_file is None:
            raise ValueError("no worker Compose file configured")
        compose = Path(settings.compose_file)
        name = "cope-worker-" + hashlib.sha256(os.fsencode(compose)).hexdigest()[:10]
        self.command = ["docker", "compose", "--project-name", name, "--file", str(compose)]
        version_file = compose.parent / "cope" / "BUILD_VERSION"
        self.token_file = settings.credential_file(home)
        self.environment = {
            **os.environ,
            "COPE_WORKER_SERVER_URL": settings.server,
            "COPE_WORKER_THREADS": str(settings.threads or "auto"),
            "COPE_WORKER_MEMORY": f"{settings.memory_mb}MiB" if settings.memory_mb else "auto",
            "COPE_WORKER_AUTO_UPDATE": str(settings.auto_update).lower(),
            "COPE_WORKER_MANAGED_ENV": "1",
        }
        if version_file.is_file():
            self.environment.setdefault("COPE_BUILD_VERSION", version_file.read_text().strip())

    def call(self, *args: str, capture: bool = False) -> subprocess.CompletedProcess[str]:
        environment = self.environment.copy()
        if args[0] in {"up", "run"}:
            environment["COPE_WORKER_COMPOSE_SECRET"] = self.token_file.read_text(encoding="utf-8").strip()
            if not environment["COPE_WORKER_COMPOSE_SECRET"]:
                raise ValueError(f"worker token file is empty: {self.token_file}")
        return subprocess.run(
            [*self.command, *args], env=environment, text=True,
            stdout=subprocess.PIPE if capture else None,
        )

    def running(self) -> bool:
        state = self.call("ps", "--status", "running", "-q", "worker", capture=True)
        if state.returncode:
            raise RuntimeError("could not inspect the Cope worker container")
        return bool(state.stdout.strip())

    def live_config(self, changes: dict) -> bool:
        config = []
        if "threads" in changes:
            config.extend(["--threads", str(changes["threads"] or "auto")])
        if "memory_mb" in changes:
            memory = changes["memory_mb"]
            config.extend(["--memory", f"{memory}MiB" if memory else "auto"])
        if "auto_update" in changes:
            config.extend(["--auto-update", str(changes["auto_update"]).lower()])
        if not config or not self.running():
            return False
        return self.call("exec", "-T", "worker", "cope", "config", *config).returncode == 0

    def run(self, args) -> int:
        if args.role == "start":
            if not self.running():
                started = self.call("up", "-d", "--wait", "--wait-timeout", str(args.timeout), "worker")
                if started.returncode:
                    self.call("stop", "--timeout", "25", "worker")
                    return started.returncode
            if args.foreground:
                return self.call("logs", "--follow", "worker").returncode
            return self.call("exec", "-T", "worker", "cope", "health", "status", "--ready").returncode
        if args.role == "stop":
            if not self.running():
                print("Worker stopped.")
                return 0
            if args.mode == "grace":
                stop = ["stop", "grace"]
                if args.timeout is not None:
                    stop.extend(["--timeout", str(args.timeout)])
                result = self.call("exec", "-T", "worker", "cope", *stop)
            elif args.mode == "force":
                result = self.call("kill", "--signal", "SIGKILL", "worker")
                self.call("stop", "--timeout", "0", "worker")
            else:
                result = self.call("stop", "--timeout", str(args.timeout or 25), "worker")
            if not self.running():
                print("Worker stopped.")
                return 0
            return result.returncode or 1
        if args.role == "update":
            command = ["update", "--output", args.output]
            if args.info:
                command.append("-i")
        else:
            command = ["health", args.action, "--output", args.output]
            if args.ready:
                command.append("--ready")
        if self.running():
            return self.call("exec", "-T", "worker", "cope", *command).returncode
        return self.call("run", "--no-deps", "--rm", "worker", *command).returncode
