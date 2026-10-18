from __future__ import annotations

import base64
import os
import re
import subprocess
import tempfile
import urllib.parse
from pathlib import Path

from pydantic import Field, field_validator, model_validator

from cope.db import get_cope_build_settings

from .registry import Input, action


class Recipe(Input):
    path: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,150}\.Dockerfile$")
    content: str = Field(min_length=1, max_length=100_000)

    @field_validator("content")
    @classmethod
    def valid_content(cls, value):
        if "\0" in value or len(value.encode()) > 100_000 or not re.search(r"(?im)^\s*FROM\s+", value):
            raise ValueError("Supply a UTF-8 Dockerfile with a FROM instruction, at most 100,000 bytes")
        return value.rstrip() + "\n"


class PublishRecipes(Input):
    recipes: list[Recipe] = Field(min_length=1, max_length=100)
    base_commit: str = Field(pattern=r"^[0-9a-f]{40}$")

    @model_validator(mode="after")
    def unique_paths(self):
        if len({item.path.casefold() for item in self.recipes}) != len(self.recipes):
            raise ValueError("Recipe paths must be unique")
        return self


def repository_settings(connection):
    settings = get_cope_build_settings(connection, default_repository_url=os.environ.get("COPE_UPDATE_REPOSITORY_URL", ""), default_update_ref=os.environ.get("COPE_UPDATE_REF", "main"))
    url = os.environ.get("COPE_CONTROL_REPOSITORY_URL", "").strip() or settings.repository_url
    branch = os.environ.get("COPE_CONTROL_REPOSITORY_BRANCH", "main").strip()
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Configure an HTTPS COPE_CONTROL_REPOSITORY_URL without embedded credentials")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9/_-]{0,150}", branch) or "//" in branch:
        raise ValueError("Invalid COPE_CONTROL_REPOSITORY_BRANCH")
    return url, branch


def repository_credential(connection, url):
    parsed = urllib.parse.urlsplit(url)
    rows = connection.execute("SELECT base_url, access_token FROM git_hosts WHERE enabled = 1 AND access_token <> ''")
    for row in rows:
        host = urllib.parse.urlsplit(row["base_url"])
        if host.scheme == parsed.scheme and host.netloc == parsed.netloc and parsed.path.startswith(host.path.rstrip("/") + "/"):
            return row["access_token"]
    return ""


def _environment(credential=""):
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    token_file = os.environ.get("COPE_CONTROL_GIT_TOKEN_FILE", "").strip()
    token = Path(token_file).read_text(encoding="utf-8").strip() if token_file else credential
    if token:
        if not token or "\n" in token or "\r" in token:
            raise ValueError("Invalid repository credential file")
        auth = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        env["GIT_CONFIG_COUNT"] = "1"
        env["GIT_CONFIG_KEY_0"] = "http.extraHeader"
        env["GIT_CONFIG_VALUE_0"] = f"Authorization: Basic {auth}"
    return env


def git(*args, cwd=None, check=True, credential=""):
    result = subprocess.run(
        ["git", *args], cwd=cwd, env=_environment(credential), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=120,
    )
    if check and result.returncode:
        raise ValueError(f"Repository {args[0]} failed; verify repository access, branch protection and concurrent changes")
    return result


def repository_head(connection):
    url, branch = repository_settings(connection)
    credential = repository_credential(connection, url)
    connection.commit()
    output = git("ls-remote", "--exit-code", url, f"refs/heads/{branch}", credential=credential).stdout.strip()
    commit = output.split()[0] if output else ""
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("Repository branch did not resolve to a commit")
    return {"repository_url": url, "branch": branch, "commit": commit}


@action("recipes.publish", "Publish engine Dockerfiles to the configured repository", "recipes:publish", PublishRecipes, external=True)
def publish_recipes(context, payload):
    url, branch = repository_settings(context.connection)
    credential = repository_credential(context.connection, url)
    context.connection.commit()
    marker = f"Cope-Operation: {context.operation['id']}/{context.step_key}"
    with tempfile.TemporaryDirectory(prefix="cope-control-git-") as temporary:
        root = Path(temporary)
        git("clone", "--single-branch", "--branch", branch, "--", url, str(root / "repository"), credential=credential)
        repo = root / "repository"
        previous = git("log", "--format=%H", "--fixed-strings", f"--grep={marker}", cwd=repo).stdout.splitlines()
        if previous:
            commit = previous[0]
            for recipe in payload.recipes:
                content = git("show", f"{commit}:data/engines/{recipe.path}", cwd=repo).stdout
                if content != recipe.content:
                    raise ValueError("Published operation marker has different Dockerfile content")
            return {"commit": commit, "branch": branch, "files": [item.path for item in payload.recipes], "reconciled": True}
        head = git("rev-parse", "HEAD", cwd=repo).stdout.strip()
        if head != payload.base_commit:
            raise ValueError("Repository branch changed since planning; prepare a new intake plan against the current commit")
        folder = repo / "data" / "engines"
        if folder.is_symlink() or any(path.is_symlink() for path in (repo / "data",)):
            raise ValueError("Dockerfile directory cannot be a symbolic link")
        folder.mkdir(parents=True, exist_ok=True)
        for recipe in payload.recipes:
            path = folder / recipe.path
            if path.is_symlink():
                raise ValueError("Dockerfile path cannot be a symbolic link")
            path.write_text(recipe.content, encoding="utf-8", newline="\n")
        paths = [f"data/engines/{item.path}" for item in payload.recipes]
        git("add", "--", *paths, cwd=repo)
        changed = git("diff", "--cached", "--quiet", cwd=repo, check=False).returncode
        if changed == 0:
            return {"commit": head, "branch": branch, "files": [item.path for item in payload.recipes], "unchanged": True}
        if changed != 1:
            raise ValueError("Could not inspect staged Dockerfile changes")
        git("-c", "user.name=Cope Control", "-c", "user.email=cope-control@localhost", "commit", "-m",
            f"Add engine intake Dockerfiles\n\n{marker}", cwd=repo)
        commit = git("rev-parse", "HEAD", cwd=repo).stdout.strip()
        context.authorize("recipes:publish")
        git("push", "origin", f"HEAD:refs/heads/{branch}", cwd=repo, credential=credential)
        return {"commit": commit, "branch": branch, "files": [item.path for item in payload.recipes]}
