from __future__ import annotations

import json
import logging
import shutil
import subprocess
from datetime import UTC, datetime, timedelta
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


def maintain_storage(source_dir: Path, image_name: str, *, require_build_space: bool = False) -> None:
    before = shutil.disk_usage(source_dir)
    repository = _repository(image_name)
    current = json.loads(_docker("image", "inspect", image_name))[0]["Id"]
    image_ids = _docker("image", "ls", "--quiet", "--no-trunc", repository).splitlines()
    images = (
        json.loads(_docker("image", "inspect", *sorted(set(image_ids))))
        if image_ids else []
    )
    images.sort(key=lambda image: image["Created"], reverse=True)
    protected = {current, *(image["Id"] for image in images[:3])}
    container_ids = _docker("ps", "--all", "--quiet").splitlines()
    if container_ids:
        protected.update(
            container["Image"] for container in json.loads(_docker("inspect", *container_ids))
        )
    cutoff = datetime.now(UTC) - timedelta(days=7)
    for image in images:
        created = datetime.fromisoformat(image["Created"].replace("Z", "+00:00"))
        if image["Id"] in protected or created >= cutoff:
            continue
        tags = [
            tag for tag in image.get("RepoTags", [])
            if _repository(tag) == repository
        ]
        for tag in tags:
            LOG.info("retiring unused application image tag=%s image=%s", tag, image["Id"])
            _docker("image", "rm", tag)
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
