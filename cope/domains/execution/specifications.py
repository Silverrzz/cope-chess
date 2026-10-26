from __future__ import annotations

from datetime import datetime
import hashlib
import json
from typing import Literal
from uuid import UUID

from pydantic import ConfigDict,Field,model_validator

from cope.application.contracts import Input

from .authority import CallerAuthority


class FrozenInput(Input):
    model_config = ConfigDict(frozen=True)


class LegacyEngineArchive(FrozenInput):
    engine_id: UUID
    engine_version_id: int = Field(strict=True,gt=0)
    build_hash: str = Field(pattern=r'^[a-f0-9]{64}$')
    sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    size: int = Field(strict=True,gt=0,le=1073741824)
    storage_key: str = Field(pattern=r'^[a-f0-9]{64}$')
    format: Literal['cope-tar-gzip-v1']
    platform: Literal['linux-x86_64']
    entrypoint: str = Field(min_length=1,max_length=256,pattern=r'^[A-Za-z0-9][A-Za-z0-9._/-]*$')

    @model_validator(mode='after')
    def relative_entrypoint(self):
        if any(part in {'','..','.'} for part in self.entrypoint.split('/')):
            raise ValueError('The entrypoint must be a normalized artifact-relative path')
        return self


class VerificationEnvelope(FrozenInput):
    cpu_millis: Literal[1000] = 1000
    memory_bytes: Literal[1073741824] = 1073741824
    scratch_bytes: Literal[4294967296] = 4294967296
    timeout_seconds: Literal[120] = 120
    output_bytes: Literal[262144] = 262144
    max_files: Literal[20000] = 20000
    manifest_bytes: Literal[4194304] = 4194304


class ArtifactVerificationSpec(FrozenInput):
    schema_version: Literal[1] = 1
    logical_item: UUID
    workload: Literal['artifacts.verify_legacy_engine.v1'] = 'artifacts.verify_legacy_engine.v1'
    implementation: Literal['cope.artifact-verification.v1'] = 'cope.artifact-verification.v1'
    profile: Literal['guarded-artifact-validation.v1'] = 'guarded-artifact-validation.v1'
    profile_revision: Literal[1] = 1
    runtime_id: UUID
    runtime_image: str = Field(pattern=r'^sha256:[a-f0-9]{64}$')
    runtime_source: str = Field(pattern=r'^[a-f0-9]{40}$')
    input: LegacyEngineArchive
    envelope: VerificationEnvelope = Field(default_factory=VerificationEnvelope)
    mode: Literal['online'] = 'online'


def specification_digest(value: FrozenInput) -> str:
    return hashlib.sha256(json.dumps(value.model_dump(mode='json'),sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


class OperandUse(FrozenInput):
    permission: Literal['engines:prepare_artifact'] = 'engines:prepare_artifact'
    engine_id: UUID
    engine_version_id: int = Field(strict=True,gt=0)
    build_hash: str = Field(pattern=r'^[a-f0-9]{64}$')
    archive_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')


class PreparationMandate(FrozenInput):
    schema_version: Literal[1] = 1
    caller: CallerAuthority
    command: Literal['engines.prepare_artifact.v1'] = 'engines.prepare_artifact.v1'
    workload: Literal['artifacts.verify_legacy_engine.v1'] = 'artifacts.verify_legacy_engine.v1'
    specification_digest: str = Field(pattern=r'^[a-f0-9]{64}$')
    operand: OperandUse
    allocation_root: Literal['engine_preparation.v1'] = 'engine_preparation.v1'
    namespace: UUID
    output_purpose: Literal['prepared_engine_artifact'] = 'prepared_engine_artifact'
    output_engine: UUID
    max_attempts: Literal[3] = 3
    max_children: Literal[0] = 0
    total_runtime_seconds: Literal[360] = 360
    expires_at: datetime

    @model_validator(mode='after')
    def bounded_destination(self):
        if self.output_engine!=self.operand.engine_id or self.expires_at.tzinfo is None:
            raise ValueError('Preparation authority must preserve its admitted destination and absolute deadline')
        if self.caller.scopes!=('engines:prepare_artifact',):
            raise ValueError('Preparation authority must contain only its declared operand permission')
        return self


def preparation_binding_matches(spec: ArtifactVerificationSpec, mandate: PreparationMandate) -> bool:
    source,operand = spec.input,mandate.operand
    return (specification_digest(spec)==mandate.specification_digest and spec.logical_item==mandate.namespace
        and (source.engine_id,source.engine_version_id,source.build_hash,source.sha256)
        ==(operand.engine_id,operand.engine_version_id,operand.build_hash,operand.archive_sha256))
