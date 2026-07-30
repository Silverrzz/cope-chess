from __future__ import annotations

import os
from importlib.resources import files


def app_version() -> str:
    """Return the deployment version shared by servers and workers."""
    configured = os.environ.get("COPE_BUILD_VERSION", "").strip()
    if configured:
        return configured
    build_file = files("cope").joinpath("BUILD_VERSION")
    if build_file.is_file():
        build_version = build_file.read_text(encoding="utf-8").strip()
        if build_version:
            return build_version
    version = files("cope").joinpath("VERSION").read_text(encoding="utf-8").strip()
    if not version:
        raise RuntimeError("cope/VERSION is empty")
    return version
