from __future__ import annotations

import re
import sys
from collections.abc import Iterator
from pathlib import Path, PurePosixPath


def _unescape(value: str) -> str:
    return re.sub(r"\\([0-7]{3})", lambda match: chr(int(match[1], 8)), value)


def _directories(controller: str) -> Iterator[tuple[Path, bool]]:
    if sys.platform != "linux":
        return
    try:
        memberships = Path("/proc/self/cgroup").read_text().splitlines()
        mounts = Path("/proc/self/mountinfo").read_text().splitlines()
    except OSError:
        return
    paths = {}
    for entry in memberships:
        fields = entry.split(":", 2)
        if len(fields) == 3:
            for name in fields[1].split(","):
                paths[name] = PurePosixPath(fields[2])
    seen = set()
    for entry in mounts:
        before, separator, after = entry.partition(" - ")
        source = before.split()
        filesystem = after.split()
        if not separator or len(source) < 5 or len(filesystem) < 3:
            continue
        unified = filesystem[0] == "cgroup2"
        if not unified and (filesystem[0] != "cgroup" or controller not in filesystem[2].split(",")):
            continue
        membership = paths.get("" if unified else controller)
        if membership is None:
            continue
        root = PurePosixPath(_unescape(source[3]))
        mountpoint = Path(_unescape(source[4]))
        try:
            relative = membership.relative_to(root)
        except ValueError:
            if membership != PurePosixPath("/"):
                continue
            relative = PurePosixPath(".")
        current = mountpoint.joinpath(*relative.parts)
        while current.is_relative_to(mountpoint):
            key = (current, unified)
            if key not in seen:
                seen.add(key)
                yield key
            if current == mountpoint:
                break
            current = current.parent


def memory_limit_and_use() -> tuple[int | None, int | None]:
    limit = None
    used = None
    for path, unified in _directories("memory"):
        try:
            maximum = int((path / ("memory.max" if unified else "memory.limit_in_bytes")).read_text())
        except (OSError, ValueError):
            continue
        if not 0 < maximum < 1 << 60 or (limit is not None and maximum >= limit):
            continue
        limit = maximum
        try:
            used = int((path / ("memory.current" if unified else "memory.usage_in_bytes")).read_text())
        except (OSError, ValueError):
            used = None
    return limit, used


def cpu_quota_threads() -> int | None:
    limits = []
    for path, unified in _directories("cpu"):
        try:
            if unified:
                quota, period = map(int, (path / "cpu.max").read_text().split())
            else:
                quota = int((path / "cpu.cfs_quota_us").read_text())
                period = int((path / "cpu.cfs_period_us").read_text())
        except (OSError, ValueError):
            continue
        if quota > 0 and period > 0:
            limits.append(max(1, quota // period))
    return min(limits) if limits else None
