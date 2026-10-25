from __future__ import annotations

import codecs
import hashlib
import os
import shutil
import stat
import uuid
from pathlib import Path

from cope.application.errors import ApplicationError
from cope.domains.artifacts.policy import MEDIA_POLICIES


def storage_root() -> Path:
    return Path(os.environ.get("COPE_ARTIFACT_DIR", "/var/lib/cope/artifacts"))


def replica_path(key: str) -> Path:
    return storage_root() / "replicas" / str(uuid.UUID(str(key)))


class ArtifactWriter:
    def __init__(self, key: str, expected_size: int, expected_digest: str, media_type: str):
        self.expected_size = expected_size
        self.expected_digest = expected_digest
        self.media_type = media_type
        self.size = 0
        self.digest = hashlib.sha256()
        self.destination = replica_path(key)
        self.destination.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        if shutil.disk_usage(self.destination.parent).free < expected_size + 209715200:
            raise ApplicationError("storage_unavailable", "Storage capacity is below its upload reserve.", 503)
        self.staging = self.destination.parent / (str(uuid.uuid4()) + ".partial")
        self.descriptor = os.open(self.staging, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        self.decoder = codecs.getincrementaldecoder("utf-8")(errors="strict") if media_type == "text/plain" else None
        self.installed = False

    def write(self, chunk: bytes) -> None:
        self.size += len(chunk)
        if self.size > self.expected_size:
            raise ApplicationError("upload_size_mismatch", "The transfer exceeds its reserved size.", 413)
        if self.decoder:
            try:
                text = self.decoder.decode(chunk)
                if any(ord(character) < 32 and character not in "\t\r\n" for character in text):
                    raise UnicodeError()
            except UnicodeError as exc:
                raise ApplicationError("validation_failed", "The attachment does not satisfy the UTF-8 text policy.", 422) from exc
        self.digest.update(chunk)
        view = memoryview(chunk)
        while view:
            written = os.write(self.descriptor, view)
            if written <= 0:
                raise OSError("Attachment storage stopped accepting bytes")
            view = view[written:]

    def validate(self) -> str:
        if self.size != self.expected_size or self.digest.hexdigest() != self.expected_digest:
            raise ApplicationError("upload_content_mismatch", "The transferred bytes do not match the upload reservation.", 422)
        if self.decoder:
            try:
                self.decoder.decode(b"", final=True)
            except UnicodeError as exc:
                raise ApplicationError("validation_failed", "The attachment is incomplete UTF-8 text.", 422) from exc
        return MEDIA_POLICIES[self.media_type]

    def install(self) -> str:
        policy = self.validate()
        os.fsync(self.descriptor)
        os.close(self.descriptor)
        self.descriptor = -1
        os.link(self.staging, self.destination)
        os.unlink(self.staging)
        directory = os.open(self.destination.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        self.installed = True
        return policy

    def close(self) -> None:
        if self.descriptor >= 0:
            os.close(self.descriptor)
            self.descriptor = -1
        self.staging.unlink(missing_ok=True)


def open_replica(key: str, expected_size: int):
    descriptor = os.open(replica_path(key), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    metadata = os.fstat(descriptor)
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_size != expected_size:
        os.close(descriptor)
        raise ApplicationError("artifact_not_ready", "The retained attachment needs a storage repair.", 503)
    return os.fdopen(descriptor, "rb")
