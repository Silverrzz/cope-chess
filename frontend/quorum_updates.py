"""Local release verification. The COPE election API is not a release authority."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import stat
import subprocess
import tempfile
import time
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, build_opener

FORMAT = "cope-quorum-verifier-release/1"
MAX_MANIFEST = 64 * 1024
MAX_ARTIFACT = 64 * 1024 * 1024
MAX_UNPACKED = 256 * 1024 * 1024
HEX = re.compile(r"[a-f0-9]{64}")
CHANNEL = re.compile(r"[a-z0-9][a-z0-9-]{0,31}")
REQUIRED = {"package.json", "quorum-node.mjs", "quorum-web.mjs", "src/quorum.ts",
            "src/styles/tokens.css", "quorum-web/index.html", "quorum-web/app.js", "quorum-web/style.css"}
SOURCE_FILES = REQUIRED | {"package-lock.json", "quorum-audit.mjs", "quorum-policy.mjs", "src/quorum-history.ts",
                           "src/quorum-relay.ts", "quorum-web/vote.html"}
PACKAGE_FILES = {"package.json", "package-lock.json"}


class ReleaseError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise ReleaseError(message)


def valid(pattern, value):
    return isinstance(value, str) and re.fullmatch(pattern, value) is not None


def integer(value, minimum, maximum=2**53 - 1):
    return type(value) is int and minimum <= value <= maximum


def unique_keys(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "Duplicate JSON field.")
        result[key] = value
    return result


def parse_json(data):
    return json.loads(data.decode("utf-8"), object_pairs_hook=unique_keys)


def read_json(path, limit=MAX_MANIFEST):
    with path.open("rb") as source:
        data = source.read(limit + 1)
    require(len(data) <= limit, "The local update file is too large.")
    value = parse_json(data)
    require(isinstance(value, dict), "The local update file must be a JSON object.")
    return value


def atomic_json(path, value):
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as file:
        temporary = Path(file.name)
        try:
            file.write(json.dumps(value, separators=(",", ":")).encode("utf-8"))
            file.flush()
            os.fsync(file.fileno())
            file.close()
            temporary.replace(path)
            sync_directory(path.parent)
        finally:
            temporary.unlink(missing_ok=True)


def sync_directory(path):
    if os.name != "nt":
        directory = os.open(path, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)


def load_policy(path):
    policy = read_json(path)
    require(policy.keys() == {"manifest_url", "channel", "threshold", "keys", "minimum_sequence"},
            "The local release policy has unexpected fields.")
    url = urlsplit(policy["manifest_url"]) if isinstance(policy["manifest_url"], str) else urlsplit("")
    require(url.scheme == "https" and url.hostname and not (url.username or url.password or url.query or url.fragment)
            and url.path.endswith("/manifest.json"), "Use an HTTPS release URL ending in /manifest.json.")
    keys = policy["keys"]
    require(isinstance(keys, dict) and 1 <= len(keys) <= 16
            and all(valid(CHANNEL, key_id) and valid(HEX, key) for key_id, key in keys.items())
            and len(set(keys.values())) == len(keys), "Pin distinct Ed25519 keys with unique operator IDs.")
    require(valid(CHANNEL, policy["channel"]) and integer(policy["threshold"], 1, len(keys))
            and integer(policy["minimum_sequence"], 1), "Invalid channel, threshold, or minimum release sequence.")
    return policy


def verify_manifest(envelope, policy, node_major, *, fresh=True, now=None):
    require(isinstance(envelope, dict) and envelope.keys() == {"payload", "signatures"},
            "Invalid release manifest envelope.")
    encoded = envelope["payload"]
    require(isinstance(encoded, str) and len(encoded) <= 8192, "Invalid release manifest payload.")
    payload = base64.b64decode(encoded, validate=True)
    manifest = parse_json(payload)
    require(isinstance(manifest, dict) and manifest.keys() == {
        "format", "channel", "commit", "sequence", "published_at", "expires_at", "min_node_major", "artifact"},
        "Invalid release manifest fields.")
    require(manifest["format"] == FORMAT and manifest["channel"] == policy["channel"],
            "The release does not match this verifier's channel or update protocol.")
    require(valid(r"[a-f0-9]{40}", manifest["commit"]) and integer(manifest["sequence"], 1)
            and integer(manifest["min_node_major"], 24, node_major), "The release is incompatible with this verifier.")
    artifact = manifest["artifact"]
    require(isinstance(artifact, dict) and artifact.keys() == {"sha256", "size"}
            and valid(HEX, artifact["sha256"]) and integer(artifact["size"], 1, MAX_ARTIFACT),
            "Invalid release artifact metadata.")
    published, expires = manifest["published_at"], manifest["expires_at"]
    require(integer(published, 1) and integer(expires, published + 1, published + 30 * 86400),
            "Invalid release validity window.")
    now = time.time() if now is None else now
    if fresh:
        require(published <= now < expires, "The signed release is expired or not yet valid.")

    signatures = envelope["signatures"]
    require(isinstance(signatures, dict) and len(signatures) <= 16
            and all(valid(CHANNEL, key_id) and valid(r"[a-f0-9]{128}", sig) for key_id, sig in signatures.items()),
            "Invalid release signatures.")
    accepted = []
    with tempfile.TemporaryDirectory(prefix="cope-release-signatures-") as directory:
        work = Path(directory)
        message, key, signature = (work / name for name in ("payload", "key.der", "signature"))
        message.write_bytes(payload)
        for key_id, public_key in policy["keys"].items():
            if key_id not in signatures:
                continue
            key.write_bytes(bytes.fromhex("302a300506032b6570032100" + public_key))
            signature.write_bytes(bytes.fromhex(signatures[key_id]))
            verified = subprocess.run(
                ["openssl", "pkeyutl", "-verify", "-pubin", "-keyform", "DER", "-inkey", str(key),
                 "-rawin", "-in", str(message), "-sigfile", str(signature)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5,
            )
            if verified.returncode == 0:
                accepted.append(key_id)
    require(len(accepted) >= policy["threshold"], "The release lacks the required signatures from pinned keys.")
    return {**manifest, "digest": hashlib.sha256(payload).hexdigest(), "signers": accepted}


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


def download(url, limit, write):
    # Network responses are data: no redirects, credential forwarding, or executable installation steps.
    end = time.monotonic() + 45
    with build_opener(ProxyHandler({}), NoRedirects()).open(url, timeout=10) as response:
        require(response.status == 200, "The release server did not return a complete file.")
        size = 0
        while chunk := response.read1(64 * 1024):
            size += len(chunk)
            require(time.monotonic() <= end and size <= limit, "The release download exceeded its time or size limit.")
            write(chunk)


def read_feed(policy):
    data = bytearray()
    download(policy["manifest_url"], MAX_MANIFEST, data.extend)
    return parse_json(bytes(data))


def checked_artifact(cache, release, policy=None):
    artifact = release["artifact"]
    path = cache / (artifact["sha256"] + ".zip")
    if not path.exists() and policy is not None:
        host_path = policy["manifest_url"].rsplit("/", 1)[0]
        with tempfile.NamedTemporaryFile(dir=cache, delete=False) as file:
            temporary = Path(file.name)
            try:
                download(host_path + "/artifacts/" + path.name, artifact["size"], file.write)
                file.flush()
                os.fsync(file.fileno())
                file.close()
                with temporary.open("rb") as source:
                    check_bytes(source, artifact)
                temporary.replace(path)
                sync_directory(cache)
            finally:
                temporary.unlink(missing_ok=True)
    return path


def check_bytes(source, artifact):
    require(os.fstat(source.fileno()).st_size == artifact["size"]
            and hashlib.file_digest(source, "sha256").hexdigest() == artifact["sha256"],
            "The artifact does not match the signed size and SHA-256.")
    source.seek(0)


def unpack(path, target, artifact):
    with path.open("rb") as source:
        check_bytes(source, artifact)
        with zipfile.ZipFile(source) as bundle:
            files = bundle.infolist()
            require(len(files) <= 32000 and sum(file.file_size for file in files) <= MAX_UNPACKED,
                    "The unpacked release is too large.")
            seen = set()
            for file in files:
                path = PurePosixPath(file.filename)
                require(not file.is_dir() and str(path) == file.filename and not path.is_absolute()
                        and all(part != ".." and not re.search(r'[\x00-\x1f\\:<>|?*"]|[. ]$', part)
                                and not re.fullmatch(r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", part)
                                for part in path.parts)
                        and stat.S_IFMT(file.external_attr >> 16) in {0, stat.S_IFREG}
                        and (file.filename in SOURCE_FILES or len(path.parts) > 1 and path.parts[0] == "node_modules")
                        and file.filename.casefold() not in seen, "The release contains an unsafe or unexpected file.")
                seen.add(file.filename.casefold())
                destination = target.joinpath(*path.parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(file) as content, destination.open("xb") as output:
                    while chunk := content.read(64 * 1024):
                        output.write(chunk)
            require(REQUIRED <= seen, "The release is missing verifier files.")
