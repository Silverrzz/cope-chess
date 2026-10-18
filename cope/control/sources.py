from __future__ import annotations

import base64
import re
from pathlib import PurePosixPath
from urllib.parse import quote

from cope.db import get_git_host
from cope.web.engine_sources import _repository_path, _request_bytes, _request_json


def source_host(connection, host_id):
    host = get_git_host(connection, host_id)
    if host is None or not host.enabled:
        raise ValueError("Git host is unavailable")
    return host


def resolve_revision(connection, host_id, repository, ref):
    host = source_host(connection, host_id)
    repository = _repository_path(repository)
    if not ref or len(ref) > 200:
        raise ValueError("Supply an engine tag, branch or commit reference")
    connection.commit()
    path = f"/repos/{repository}/commits/{quote(ref, safe='')}" if host.provider == "github" else f"/projects/{quote(repository, safe='')}/repository/commits/{quote(ref, safe='')}"
    result = _request_json(host, path)
    commit = result.get("sha") if host.provider == "github" else result.get("id")
    if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("Engine reference did not resolve to a full commit")
    return {"git_host_id": host_id, "repository_full_name": repository, "source_ref": commit, "requested_ref": ref}


def read_source(connection, host_id, repository, commit, path):
    host = source_host(connection, host_id)
    repository = _repository_path(repository)
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("Resolve the exact source commit before reading files")
    relative = PurePosixPath(path)
    if len(path) > 500 or relative.is_absolute() or "\\" in path or any(part in {".", ".."} for part in path.split("/") if path):
        raise ValueError("Choose a repository-relative file path")
    connection.commit()
    if host.provider == "github":
        result = _request_json(host, f"/repos/{repository}/contents/{quote(path, safe='/')}?ref={commit}")
        if isinstance(result, list):
            return {"entries": [{"path": item["path"], "type": item["type"], "size": item.get("size")} for item in result[:1000]]}
        if result.get("type") != "file" or result.get("encoding") != "base64" or int(result.get("size", 0)) > 262144:
            raise ValueError("Choose a text file at most 256 KiB")
        content = base64.b64decode(result["content"])
    else:
        project = quote(repository, safe="")
        if not path or path.endswith("/"):
            result = _request_json(host, f"/projects/{project}/repository/tree?ref={commit}&path={quote(path, safe='')}&per_page=100")
            return {"entries": [{"path": item["path"], "type": item["type"]} for item in result]}
        content = _request_bytes(host, f"/projects/{project}/repository/files/{quote(path, safe='')}/raw?ref={commit}", maximum=262144, accept="text/plain")
    if len(content) > 262144 or b"\0" in content:
        raise ValueError("Choose a text file at most 256 KiB")
    return {"path": path, "source_ref": commit, "content": content.decode("utf-8"), "untrusted_source": True}
