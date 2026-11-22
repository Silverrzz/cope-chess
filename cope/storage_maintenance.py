from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
from pathlib import Path


LOG = logging.getLogger("cope.storage")
GIB = 1024**3
MIN_BUILD_FREE = 10 * GIB
WARN_FREE = 20 * GIB


def _docker(*arguments: str) -> str:
    result = subprocess.run(
        ["docker", *arguments], capture_output=True, text=True, timeout=300,
    )
    if result.returncode:
        raise RuntimeError(f"Docker storage maintenance failed: {result.stderr[-2000:]}")
    return result.stdout.strip()


def _repository(image_name: str) -> str:
    name = image_name.split("@", 1)[0]
    return name.rsplit(":", 1)[0] if ":" in name.rsplit("/", 1)[-1] else name


def _retire_old_rollbacks(image_name: str) -> None:
    repository = _repository(image_name)
    rollbacks = []
    for line in _docker(
        "image", "ls", "--no-trunc", "--format", "{{.Tag}}\t{{.ID}}", repository,
    ).splitlines():
        tag, image_id = line.split("\t", 1)
        match = re.fullmatch(r"rollback-([0-9]+)", tag)
        if match:
            rollbacks.append((int(match[1]), tag, image_id))
    expired = sorted(rollbacks, reverse=True)[2:]
    if not expired:
        return

    protected = {json.loads(_docker("image", "inspect", image_name))[0]["Id"]}
    container_ids = _docker("ps", "--all", "--quiet").splitlines()
    if container_ids:
        protected.update(
            container["Image"]
            for container in json.loads(_docker("inspect", "--type=container", *container_ids))
        )
    for _, tag, image_id in expired:
        if image_id in protected:
            continue
        reference = f"{repository}:{tag}"
        LOG.info("retiring unused rollback image tag=%s image=%s", reference, image_id)
        _docker("image", "rm", "--no-prune", reference)


def maintain_storage(source_dir: Path, image_name: str, *, require_build_space: bool = False) -> None:
    before = shutil.disk_usage(source_dir)
    _retire_old_rollbacks(image_name)
    LOG.info(
        "aged build cache cleanup: %s",
        _docker("builder", "prune", "--force", "--filter", "until=24h", "--keep-storage", "2048")[-1000:],
    )
    after = shutil.disk_usage(source_dir)
    LOG.info(
        "storage free_gib=%.2f total_gib=%.2f reclaimed_gib=%.2f",
        after.free / GIB, after.total / GIB, (after.free - before.free) / GIB,
    )
    if after.free < WARN_FREE:
        LOG.warning("LOW DISK SPACE: %.2f GiB available; expand storage before game data fills the disk", after.free / GIB)
    if require_build_space and after.free < MIN_BUILD_FREE:
        raise RuntimeError(
            f"Deployment blocked: only {after.free / GIB:.2f} GiB free after safe cleanup; "
            "at least 10 GiB is required. Expand storage; game data and backups were preserved."
        )
