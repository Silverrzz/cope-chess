from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC,datetime
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import time
from typing import Literal
from uuid import UUID,uuid4

from pydantic import BaseModel,ConfigDict,Field,model_validator


class AuthorityStoreError(ValueError):
    pass


class AuthorityRecord(BaseModel):
    model_config = ConfigDict(extra='forbid',frozen=True)
    schema_version: Literal[1] = 1
    id: UUID
    installation_id: UUID
    sequence: int = Field(strict=True,ge=1,le=4096)
    previous_digest: str | None = Field(pattern=r'^[a-f0-9]{64}$')
    incarnation: UUID
    previous_incarnation: UUID | None
    target_database: str = Field(min_length=1,max_length=100)
    action: Literal['initialize','restore']
    mode: Literal['held'] = 'held'
    reason: str = Field(min_length=1,max_length=500)
    fence_evidence_digest: str | None = Field(pattern=r'^[a-f0-9]{64}$')
    created_at: datetime

    @model_validator(mode='after')
    def coherent_record(self):
        if self.created_at.tzinfo is None or self.action=='restore' and self.fence_evidence_digest is None:
            raise ValueError('Recovery requires an absolute time and recorded fencing evidence')
        if self.sequence==1 and self.previous_digest is not None or self.sequence>1 and self.previous_digest is None:
            raise ValueError('Authority journal chain is incomplete')
        if self.action=='initialize' and (self.sequence!=1 or self.previous_incarnation is not None):
            raise ValueError('Initialization must be the first authority transition')
        return self

    def digest(self):
        return hashlib.sha256(json.dumps(self.model_dump(mode='json'),sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def unique_object(pairs):
    result = {}
    for key,value in pairs:
        if key in result:
            raise AuthorityStoreError('Authority metadata contains duplicate fields')
        result[key]=value
    return result


class AuthorityStore:
    def __init__(self, path: str|Path):
        self.path = Path(path)

    @classmethod
    def configured(cls):
        value = os.environ.get('COPE_EXECUTION_AUTHORITY_DIR','').strip()
        return cls(value) if value else None

    def _validate_directory(self, path):
        value = path.lstat()
        if not stat.S_ISDIR(value.st_mode) or value.st_mode & 0o027:
            raise AuthorityStoreError('Authority storage must be a real directory without group/world write access')
        return value

    def initialize_directory(self, reader_gid: int):
        self.path.mkdir(mode=0o750,parents=False,exist_ok=True)
        value = self._validate_directory(self.path)
        if value.st_uid!=os.geteuid():
            raise AuthorityStoreError('The bootstrap writer must own its authority storage')
        os.chown(self.path,-1,reader_gid)
        os.chmod(self.path,0o750)
        records = self.path/'records'
        records.mkdir(mode=0o750,exist_ok=True)
        self._validate_directory(records)
        os.chown(records,-1,reader_gid)
        for name in ('writer.lock','authority.lock'):
            descriptor = os.open(self.path/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o640) if not (self.path/name).exists() else None
            if descriptor is not None:
                try:
                    os.fchown(descriptor,-1,reader_gid)
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
        self._sync_directory(self.path)

    @staticmethod
    def _sync_directory(path):
        descriptor = os.open(path,os.O_RDONLY|os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @contextmanager
    def _lock(self, name, exclusive):
        import fcntl

        self._validate_directory(self.path)
        descriptor = os.open(self.path/name,os.O_RDONLY|os.O_NOFOLLOW)
        try:
            value = os.fstat(descriptor)
            if not stat.S_ISREG(value.st_mode) or value.st_mode & 0o027:
                raise AuthorityStoreError('Authority lock permissions are invalid')
            if name=='writer.lock' and value.st_uid!=os.geteuid():
                raise AuthorityStoreError('Only the external-store owner may run bootstrap transitions')
            deadline = time.monotonic()+2
            while True:
                try:
                    fcntl.flock(descriptor,(fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)|fcntl.LOCK_NB)
                    break
                except BlockingIOError as error:
                    if time.monotonic()>=deadline:
                        raise AuthorityStoreError('Authority transition is busy; retry after recovery finishes') from error
                    time.sleep(.01)
            try:
                yield
            finally:
                fcntl.flock(descriptor,fcntl.LOCK_UN)
        finally:
            os.close(descriptor)

    def writer(self):
        return self._lock('writer.lock',True)

    def exclusive(self):
        return self._lock('authority.lock',True)

    @contextmanager
    def read(self):
        with self._lock('authority.lock',False):
            yield self.records()

    def records(self):
        root = self.path/'records'
        self._validate_directory(root)
        paths = sorted(root.iterdir())
        if len(paths)>4224:
            raise AuthorityStoreError('Authority journal needs explicit retention maintenance')
        records,previous,seen = [],None,set()
        for path in paths:
            if path.name.startswith('.') and path.name.endswith('.pending'):
                continue
            if not re.fullmatch(r'[0-9]{20}\.json',path.name):
                raise AuthorityStoreError('Authority journal contains an unknown record')
            descriptor = os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
            with os.fdopen(descriptor,'rb') as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o027:
                    raise AuthorityStoreError('Authority journal permissions are invalid')
                data = stream.read(16385)
            if len(data)>16384:
                raise AuthorityStoreError('Authority record exceeds its size limit')
            record = AuthorityRecord.model_validate(json.loads(data.decode('utf-8'),object_pairs_hook=unique_object))
            canonical = (json.dumps(record.model_dump(mode='json'),sort_keys=True,separators=(',',':'))+'\n').encode()
            if data!=canonical:
                raise AuthorityStoreError('Authority record encoding is not canonical')
            if record.sequence!=len(records)+1 or path.name!=f'{record.sequence:020d}.json':
                raise AuthorityStoreError('Authority journal sequence is discontinuous')
            if previous is not None and (record.installation_id!=previous.installation_id or record.previous_digest!=previous.digest()
                or record.target_database!=previous.target_database or record.previous_incarnation!=previous.incarnation):
                raise AuthorityStoreError('Authority journal lineage does not match')
            if record.incarnation in seen:
                raise AuthorityStoreError('A recovery incarnation was reused')
            records.append(record)
            previous=record
            seen.add(record.incarnation)
        return records

    def append(self, records: list[AuthorityRecord], *, database: str, action: str, reason: str, evidence: str|None, previous_incarnation: UUID|None):
        previous = records[-1] if records else None
        record = AuthorityRecord(id=uuid4(),installation_id=previous.installation_id if previous else uuid4(),sequence=len(records)+1,
            previous_digest=previous.digest() if previous else None,incarnation=uuid4(),previous_incarnation=previous.incarnation if previous else previous_incarnation,
            target_database=database,action=action,reason=reason,fence_evidence_digest=evidence,created_at=datetime.now(UTC))
        data = (json.dumps(record.model_dump(mode='json'),sort_keys=True,separators=(',',':'))+'\n').encode()
        root = self.path/'records'
        temporary = root/('.'+str(record.id)+'.pending')
        descriptor = os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o640)
        try:
            with os.fdopen(descriptor,'wb') as stream:
                os.fchown(stream.fileno(),-1,root.stat().st_gid)
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            destination = root/f'{record.sequence:020d}.json'
            os.link(temporary,destination,follow_symlinks=False)
            self._sync_directory(root)
        finally:
            temporary.unlink(missing_ok=True)
        self._sync_directory(root)
        return record
