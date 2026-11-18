"""Build a release from a reviewed Git commit and collect offline Ed25519 signatures."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import shutil
import stat
import subprocess
import tempfile
import time
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from quorum_updates import CHANNEL, FORMAT, MAX_ARTIFACT, PACKAGE_FILES, SOURCE_FILES, integer, read_json, require, unpack, valid

ROOT = Path(__file__).resolve().parent.parent


def git(*args):
    return subprocess.check_output(["git", "-C", str(ROOT), *args])


def build(args):
    require(valid(CHANNEL, args.channel) and integer(args.sequence, 1) and integer(args.days, 1, 30),
            "Set a valid channel, positive sequence, and validity of 1–30 days.")
    commit = git("rev-parse", "--verify", "--end-of-options", args.revision + "^{commit}").decode().strip()
    npm = shutil.which("npm") or shutil.which("npm.cmd")
    require(npm is not None, "Install npm to build the release.")
    args.output.mkdir(parents=True, exist_ok=True)
    require(not (args.output / "manifest.unsigned.json").exists(), "Choose a new output directory for this release.")
    artifacts = args.output / "artifacts"
    artifacts.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        working = Path(directory)
        source = working / "source"
        for name in sorted(SOURCE_FILES):
            path = source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            repo_file = "quorum-runtime/" + name if name in PACKAGE_FILES else name
            path.write_bytes(git("show", f"{commit}:frontend/{repo_file}"))
        subprocess.run([npm, "ci", "--omit=dev", "--ignore-scripts", "--no-bin-links", "--no-audit", "--no-fund"],
                       cwd=source, check=True)
        archive = working / "release.zip"
        with ZipFile(archive, "w") as bundle:
            for path in sorted(source.rglob("*")):
                rel = path.relative_to(source)
                if ".bin" in rel.parts or rel.parts == ("node_modules", ".package-lock.json"):
                    continue
                require(not path.is_symlink(), f"Unexpected symbolic link: {rel}")
                if path.is_file():
                    info = ZipInfo(rel.as_posix())
                    info.external_attr = (stat.S_IFREG | 0o644) << 16
                    bundle.writestr(info, path.read_bytes(), compress_type=ZIP_DEFLATED, compresslevel=6)
        size = archive.stat().st_size
        require(size <= MAX_ARTIFACT, "The compressed artifact is too large.")
        with archive.open("rb") as file:
            digest = hashlib.file_digest(file, "sha256").hexdigest()
        artifact = {"sha256": digest, "size": size}
        check = working / "check"
        check.mkdir()
        unpack(archive, check, artifact)
        shutil.copyfile(archive, artifacts / (digest + ".zip"))
    now = int(time.time())
    payload = json.dumps({"format": FORMAT, "channel": args.channel, "commit": commit, "sequence": args.sequence,
                          "published_at": now, "expires_at": now + args.days * 86400,
                          "min_node_major": 24, "artifact": artifact}, separators=(",", ":")).encode("utf-8")
    write(args.output / "manifest.unsigned.json", {"payload": base64.b64encode(payload).decode(), "signatures": {}})
    print(f"Built {commit} ({args.channel} sequence {args.sequence}): {digest}")


def write(path, value):
    with path.open("x", encoding="utf-8") as output:
        json.dump(value, output, indent=2)
        output.write("\n")


def payload(envelope):
    return base64.b64decode(envelope["payload"], validate=True)


def inspect(args):
    print(json.dumps(json.loads(payload(read_json(args.manifest))), indent=2))


def sign(args):
    require(valid(CHANNEL, args.operator), "Use a valid release operator ID.")
    envelope = read_json(args.manifest)
    message = payload(envelope)
    fields = json.loads(message)
    require(fields["format"] == FORMAT, "This file is not a Quorum verifier release.")
    with args.artifact.open("rb") as artifact:
        require(artifact.seek(0, 2) == fields["artifact"]["size"], "The artifact size differs from the manifest.")
        artifact.seek(0)
        require(hashlib.file_digest(artifact, "sha256").hexdigest() == fields["artifact"]["sha256"],
                "The artifact hash differs from the manifest.")
    public_key = subprocess.check_output(["openssl", "pkey", "-in", str(args.key), "-pubout", "-outform", "DER"])
    require(len(public_key) == 44 and public_key[:12].hex() == "302a300506032b6570032100", "Use an Ed25519 key.")
    with tempfile.TemporaryDirectory() as directory:
        statement = Path(directory) / "payload"
        statement.write_bytes(message)
        signature = subprocess.check_output(["openssl", "pkeyutl", "-sign", "-inkey", str(args.key),
                                             "-rawin", "-in", str(statement)])
    write(args.output, {"payload": envelope["payload"], "signatures": {args.operator: signature.hex()}})
    print(f"Signed as {args.operator} ({public_key[-32:].hex()})")


def assemble(args):
    envelope = read_json(args.manifest)
    signatures = {}
    for file in args.signatures:
        signed = read_json(file)
        require(signed["payload"] == envelope["payload"], "Signatures must cover the exact same manifest bytes.")
        require(not set(signed["signatures"]) & signatures.keys(), "A release operator signed more than once.")
        signatures.update(signed["signatures"])
    write(args.output, {"payload": envelope["payload"], "signatures": signatures})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(required=True)
    command = commands.add_parser("build")
    command.add_argument("--revision", default="HEAD")
    command.add_argument("--channel", required=True)
    command.add_argument("--sequence", required=True, type=int)
    command.add_argument("--days", default=7, type=int)
    command.add_argument("output", type=Path)
    command.set_defaults(operation=build)
    command = commands.add_parser("inspect")
    command.add_argument("manifest", type=Path)
    command.set_defaults(operation=inspect)
    command = commands.add_parser("sign")
    command.add_argument("--operator", required=True)
    command.add_argument("--key", required=True, type=Path)
    command.add_argument("manifest", type=Path)
    command.add_argument("artifact", type=Path)
    command.add_argument("output", type=Path)
    command.set_defaults(operation=sign)
    command = commands.add_parser("assemble")
    command.add_argument("manifest", type=Path)
    command.add_argument("output", type=Path)
    command.add_argument("signatures", type=Path, nargs="+")
    command.set_defaults(operation=assemble)
    options = parser.parse_args()
    try:
        options.operation(options)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        parser.exit(1, f"Release error: {error}\n")
