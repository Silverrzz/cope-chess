from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import tarfile
from typing import Literal

from pydantic import BaseModel,ConfigDict,Field

from cope.domains.execution.specifications import ArtifactVerificationSpec,specification_digest
from cope.engine_artifacts import MANIFEST_NAME,MARKER_NAME,_member_metadata,_resolve_link_name,_validate_entrypoint,_validate_relative_path


class ManifestValue(BaseModel):
    model_config = ConfigDict(extra='forbid',strict=True,frozen=True)


class FileMember(ManifestValue):
    path: str = Field(min_length=1,max_length=4096)
    type: Literal['file']
    mode: int = Field(ge=0,le=511)
    size: int = Field(ge=0,le=4294967296)
    sha256: str = Field(pattern=r'^[a-f0-9]{64}$')


class DirectoryMember(ManifestValue):
    path: str = Field(min_length=1,max_length=4096)
    type: Literal['directory']
    mode: int = Field(ge=0,le=511)


class LinkMember(ManifestValue):
    path: str = Field(min_length=1,max_length=4096)
    type: Literal['symlink']
    mode: int = Field(ge=0,le=511)
    target: str = Field(min_length=1,max_length=4096)


class EngineManifest(ManifestValue):
    format: Literal['cope-tar-gzip-v1']
    platform: Literal['linux-x86_64']
    build_hash: str = Field(pattern=r'^[a-f0-9]{64}$')
    entrypoint: str = Field(min_length=1,max_length=256)
    files: list[FileMember|DirectoryMember|LinkMember] = Field(max_length=20000)


def unique_object(pairs):
    result = {}
    for key,value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON member')
        result[key]=value
    return result


class DigestReader:
    def __init__(self, stream, size):
        self.stream,self.limit = stream,size
        self.count = 0
        self.digest = hashlib.sha256()

    def read(self, size):
        if size<0 or size>1048576:
            raise ValueError('Unbounded compressed read')
        value = self.stream.read(size)
        self.count+=len(value)
        if self.count>self.limit:
            raise ValueError('Compressed archive exceeds its admitted size')
        self.digest.update(value)
        return value


class ExpandedReader:
    def __init__(self, stream, maximum):
        self.stream,self.maximum = stream,maximum
        self.count = 0

    def read(self, size):
        if size<0 or size>1048576:
            raise ValueError('Unbounded expanded read')
        value = self.stream.read(size)
        self.count+=len(value)
        if self.count>self.maximum:
            raise ValueError('Expanded archive exceeds its profile')
        return value


class BoundedTarInfo(tarfile.TarInfo):
    @classmethod
    def fromtarfile(cls, archive):
        archive.validation_headers = getattr(archive,'validation_headers',0)+1
        if archive.validation_headers>40004:
            raise ValueError('Too many archive headers')
        return super().fromtarfile(archive)

    def _proc_pax(self, archive):
        if self.size>65536:
            raise ValueError('Extended archive metadata exceeds its limit')
        return super()._proc_pax(archive)

    def _proc_gnulong(self, archive):
        if self.size>4096:
            raise ValueError('Archive path metadata exceeds its limit')
        return super()._proc_gnulong(archive)

    def _proc_sparse(self, *args):
        raise ValueError('Sparse archives are unsupported')

    def _proc_gnusparse_00(self, *args):
        raise ValueError('Sparse archives are unsupported')

    def _proc_gnusparse_01(self, *args):
        raise ValueError('Sparse archives are unsupported')

    def _proc_gnusparse_10(self, *args):
        raise ValueError('Sparse archives are unsupported')


def bounded_path(value):
    if value in {'.','..'} or len(value)>4096 or any(ord(char)<32 or ord(char)==127 for char in value):
        raise ValueError('Archive path exceeds its policy')
    return _validate_relative_path(value)


def validate_archive(stream, spec: ArtifactVerificationSpec):
    source,envelope = spec.input,spec.envelope
    raw = DigestReader(stream,source.size)
    with gzip.GzipFile(fileobj=raw,mode='rb') as compressed:
        expanded = ExpandedReader(compressed,envelope.scratch_bytes+67108864)
        members,actual = {},[]
        manifest,manifest_bytes = None,None
        file_bytes = 0
        with tarfile.open(fileobj=expanded,mode='r|',tarinfo=BoundedTarInfo) as archive:
            for member in archive:
                if len(members)>=envelope.max_files+1:
                    raise ValueError('Too many archive entries')
                name = bounded_path(member.name)
                if name in members or name==MARKER_NAME or not (member.isfile() or member.isdir() or member.issym()):
                    raise ValueError('Duplicate, reserved or unsupported archive entry')
                if member.sparse is not None:
                    raise ValueError('Sparse archives are unsupported')
                if member.mode & ~0o777:
                    raise ValueError('Unsupported archive permission bits')
                if member.issym():
                    bounded_path(_resolve_link_name(name,member.linkname))
                    if len(member.linkname)>4096 or any(ord(char)<32 or ord(char)==127 for char in member.linkname):
                        raise ValueError('Unsupported archive link')
                if member.isfile():
                    file_bytes+=member.size
                    if member.size<0 or file_bytes>envelope.scratch_bytes:
                        raise ValueError('Archive files exceed their profile')
                members[name]=member
                if name==MANIFEST_NAME:
                    if not member.isfile() or member.size>envelope.manifest_bytes:
                        raise ValueError('Archive manifest exceeds its policy')
                    manifest_bytes = archive.extractfile(member).read(envelope.manifest_bytes+1)
                    manifest = EngineManifest.model_validate(json.loads(manifest_bytes.decode('utf-8'),object_pairs_hook=unique_object))
                else:
                    actual.append(_member_metadata(archive,member))
            while block:=archive.fileobj.read(65536):
                if any(block):
                    raise ValueError('Nonempty data follows the archive end')
        while block:=expanded.read(65536):
            if any(block):
                raise ValueError('Nonempty data follows the archive end')
    if raw.count!=source.size or raw.digest.hexdigest()!=source.sha256:
        raise ValueError('Archive bytes do not match the admitted input')
    if manifest is None or manifest.build_hash!=source.build_hash or manifest.entrypoint!=source.entrypoint:
        raise ValueError('Manifest does not match the admitted build and entrypoint')
    if manifest.format!=source.format or manifest.platform!=source.platform or [member.model_dump() for member in manifest.files]!=actual:
        raise ValueError('Manifest does not match the observed contents')
    for name,member in members.items():
        parent = name
        while '/' in parent:
            parent = parent.rsplit('/',1)[0]
            if parent in members and not members[parent].isdir():
                raise ValueError('Archive entry traverses another entry')
        if member.issym():
            current,seen = name,set()
            while members[current].issym():
                if current in seen:
                    raise ValueError('Archive link cycle')
                seen.add(current)
                current = _resolve_link_name(current,members[current].linkname)
                if current not in members:
                    raise ValueError('Archive link target is absent')
    _validate_entrypoint(members,source.entrypoint)
    return {'schema_version':1,'status':'verified','logical_item':str(spec.logical_item),'specification_digest':specification_digest(spec),
        'archive_sha256':source.sha256,'archive_bytes':raw.count,'build_hash':source.build_hash,'entrypoint':source.entrypoint,
        'manifest_sha256':hashlib.sha256(manifest_bytes).hexdigest(),'entries':len(actual),'file_bytes':file_bytes-len(manifest_bytes),
        'validation_policy':'engine-archive.v1'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--spec-file',required=True)
    parser.add_argument('--archive',required=True)
    args = parser.parse_args()
    try:
        with Path(args.spec_file).open('rb') as source:
            value = source.read(65537)
        if len(value)>65536:
            raise ValueError('Specification exceeds its limit')
        spec = ArtifactVerificationSpec.model_validate(json.loads(value.decode('utf-8'),object_pairs_hook=unique_object))
        descriptor = os.open(args.archive,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0))
        with os.fdopen(descriptor,'rb') as source:
            result = validate_archive(source,spec)
        print(json.dumps(result,sort_keys=True,separators=(',',':')))
        return 0
    except (ValueError,OSError,EOFError,tarfile.TarError,RecursionError):
        print(json.dumps({'schema_version':1,'status':'rejected','code':'archive_verification_failed'}))
        return 2


if __name__=='__main__':
    raise SystemExit(main())
