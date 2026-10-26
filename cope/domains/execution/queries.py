from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import Field

from cope.application.contracts import Input,Query
from cope.application.errors import ApplicationError

from .admission import may_inspect,resolve_operation,resource_id
from .specifications import ArtifactVerificationSpec,PreparationMandate,preparation_binding_matches,specification_digest


class Empty(Input):
    pass


class BlockReason(Input):
    code: Literal['control_plane_unavailable']
    message: str


class JobSummary(Input):
    id: UUID
    workload: str
    status: Literal['blocked','cancelled']
    state_version: int
    readiness: BlockReason | None
    cleanup: Literal['no_attempt_started'] = 'no_attempt_started'


class JobInputView(Input):
    engine_id: UUID
    engine_version_id: int
    build_hash: str
    archive_sha256: str
    archive_bytes: int
    profile: str
    runtime_id: UUID
    runtime_image: str
    runtime_source: str
    specification_digest: str
    cpu_millis: int
    memory_bytes: int
    scratch_bytes: int
    timeout_seconds: int


class JobDetail(JobSummary):
    kind: Literal['job']
    operation_id: UUID
    created_at: str
    not_after: str
    input: JobInputView | None
    authority_mode: Literal['on_behalf_of_caller'] = 'on_behalf_of_caller'
    completion_policy: Literal['finish_admitted_pure_work'] = 'finish_admitted_pure_work'


class OperationDetail(Input):
    kind: Literal['operation']
    id: UUID
    command: str
    status: Literal['waiting','cancelled']
    state_version: int
    created_at: str
    jobs: list[JobSummary] = Field(max_length=16)
    may_cancel: bool


class RuntimeItem(Input):
    id: UUID
    image_digest: str
    source_revision: str
    workload: str
    profile: str
    activation: Literal['pending'] = 'pending'


class RuntimePage(Input):
    items: list[RuntimeItem] = Field(max_length=50)
    truncated: bool


def summary(row):
    return {'id':row['id'],'workload':row['workload'],'status':row['status'],'state_version':row['state_version'],
            'readiness':row['block_reason'] if row['status']=='blocked' else None}


def operation_detail(connection, principal, target, payload):
    operation = resolve_operation(connection,principal,target)
    rows = connection.execute('''SELECT job.*,object.state_version FROM execution_jobs job JOIN application_objects object ON object.id=job.id
        JOIN execution_operation_jobs link ON link.job_id=job.id WHERE link.operation_id=%s ORDER BY job.id LIMIT 17''',(operation.object_id,)).fetchall()
    if len(rows)>16:
        raise ApplicationError('operation_projection_unavailable','This operation needs a compatible bounded work-graph inspector.',503)
    permitted = ('*' in principal.scopes or 'operations:cancel' in principal.scopes) and ('*' in principal.scopes or operation.data['actor']==principal.actor)
    return {'kind':'operation','id':operation.object_id,'command':operation.data['command'],'status':operation.data['status'],'state_version':operation.version,
            'created_at':operation.data['created_at'].isoformat(),'jobs':[summary(row) for row in rows],
            'may_cancel':permitted and operation.data['status']=='waiting'}


def checked_specification(row):
    spec = ArtifactVerificationSpec.model_validate(row['specification'])
    mandate = PreparationMandate.model_validate(row['mandate'])
    if (str(spec.logical_item)!=str(row['id']) or spec.runtime_id!=row['runtime_id'] or spec.workload!=row['workload']
        or specification_digest(spec)!=row['specification_digest'] or mandate.specification_digest!=row['specification_digest']
        or not preparation_binding_matches(spec,mandate)
        or mandate.expires_at!=row['not_after'] or mandate.allocation_root!=row['admission_root']):
        raise ApplicationError('execution_specification_mismatch','The admitted work specification does not match its durable identity.',503)
    return spec


def job_detail(connection, principal, target, payload):
    row = connection.execute('''SELECT job.*,object.state_version,visible.operation_id FROM execution_jobs job
        JOIN application_objects object ON object.id=job.id JOIN LATERAL (
            SELECT operation.id AS operation_id FROM execution_operation_jobs link JOIN execution_operations operation ON operation.id=link.operation_id
            WHERE link.job_id=job.id AND (%s OR operation.actor=%s)
            ORDER BY (operation.actor=%s) DESC,operation.id LIMIT 1) visible ON TRUE
        WHERE job.id=%s''',(may_inspect(principal),principal.actor,principal.actor,resource_id(target))).fetchone()
    if row is None:
        raise ApplicationError('job_unavailable','This job is not available to the current credential.',404)
    spec = checked_specification(row)
    details = None
    if '*' in principal.scopes or 'engines:prepare_artifact' in principal.scopes:
        source = spec.input
        details = {'engine_id':source.engine_id,'engine_version_id':source.engine_version_id,'build_hash':source.build_hash,
            'archive_sha256':source.sha256,'archive_bytes':source.size,'profile':spec.profile,'runtime_id':spec.runtime_id,
            'runtime_image':spec.runtime_image,'runtime_source':spec.runtime_source,'specification_digest':row['specification_digest'],
            'cpu_millis':spec.envelope.cpu_millis,'memory_bytes':spec.envelope.memory_bytes,'scratch_bytes':spec.envelope.scratch_bytes,
            'timeout_seconds':spec.envelope.timeout_seconds}
    return {**summary(row),'kind':'job','operation_id':row['operation_id'],'created_at':row['created_at'].isoformat(),'not_after':row['not_after'].isoformat(),'input':details}


def runtime_list(connection, principal, target, payload):
    rows = connection.execute('SELECT id,image_digest,source_revision,workload,profile FROM execution_runtimes ORDER BY created_at DESC,id DESC LIMIT 51').fetchall()
    return {'items':[dict(row) for row in rows[:50]],'truncated':len(rows)>50}


def queries():
    return (
        Query('execution.operations.detail.v1','operations:read',Empty,OperationDetail,operation_detail,True),
        Query('execution.jobs.detail.v1','operations:read',Empty,JobDetail,job_detail,True),
        Query('execution.runtimes.v1','engines:prepare_artifact',Empty,RuntimePage,runtime_list),
    )
