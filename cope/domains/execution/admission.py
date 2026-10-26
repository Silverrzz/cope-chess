from __future__ import annotations

from datetime import timedelta
import json
from uuid import UUID,uuid4
from typing import Literal

from pydantic import Field,ValidationError

from cope.application.contracts import Command,Input,Outcome,Target
from cope.application.errors import ApplicationError
from cope.application.legacy_resources import ENGINE
from cope.execution.control_authority import control_authority

from .authority import capture_caller_authority,current_caller_authority
from .specifications import ArtifactVerificationSpec,LegacyEngineArchive,OperandUse,PreparationMandate,preparation_binding_matches,specification_digest


class RegisterRuntime(Input):
    image_digest: str = Field(pattern=r'^sha256:[a-f0-9]{64}$')
    source_revision: str = Field(pattern=r'^[a-f0-9]{40}$')


class RuntimeOutput(RegisterRuntime):
    runtime_id: UUID
    activation: Literal['pending'] = 'pending'


class PrepareArtifact(Input):
    engine_version_id: int = Field(strict=True,gt=0)
    expected_build_hash: str = Field(pattern=r'^[a-f0-9]{64}$')
    expected_archive_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    runtime_id: UUID


class AdmissionOutput(Input):
    operation_id: UUID
    job_id: UUID
    admission: Literal['accepted'] = 'accepted'
    initial_job_state: Literal['blocked'] = 'blocked'


class CancelOperation(Input):
    reason: str = Field(min_length=1,max_length=500)


class CancellationOutput(Input):
    operation_id: UUID
    status: Literal['cancelled']
    cancelled_jobs: int
    continuing_jobs: int
    cleanup: Literal['no_attempt_started'] = 'no_attempt_started'


def resource_id(value: str) -> UUID:
    try:
        return UUID(value)
    except (ValueError,TypeError,AttributeError) as error:
        raise ApplicationError('invalid_target','Supply a canonical execution Object UUID.',422) from error


def may_inspect(principal) -> bool:
    return '*' in principal.scopes or 'execution:inspect' in principal.scopes


def resolve_operation(connection, principal, target, *, lock=False):
    row = connection.execute('''SELECT operation.*,object.state_version FROM execution_operations operation
        JOIN application_objects object ON object.id=operation.id WHERE operation.id=%s AND (%s OR operation.actor=%s)'''
        +(' FOR UPDATE OF operation,object' if lock else ''),(resource_id(target),may_inspect(principal),principal.actor)).fetchone()
    if row is None:
        raise ApplicationError('operation_unavailable','This operation is not available to the current credential.',404)
    return Target(str(row['id']),row['state_version'],data=dict(row))


def no_target(*args, **kwargs):
    raise ApplicationError('target_not_applicable','This creation command has no existing target.',422)


def new_object(connection, kind):
    return connection.execute('INSERT INTO application_objects(type_key) VALUES (%s) RETURNING id',(kind,)).fetchone()['id']


def register_runtime(context, payload: RegisterRuntime):
    runtime_id = new_object(context.connection,'execution_runtime')
    context.connection.execute('''INSERT INTO execution_runtimes(id,workload,profile,image_digest,source_revision)
        VALUES (%s,'artifacts.verify_legacy_engine.v1','guarded-artifact-validation.v1',%s,%s)''',
        (runtime_id,payload.image_digest,payload.source_revision))
    return Outcome(str(runtime_id),1,RuntimeOutput(runtime_id=runtime_id,**payload.model_dump()).model_dump(mode='json'))


def read_archive(connection, engine_id, version_id):
    row = connection.execute('''SELECT version.build_hash,archive.artifact_sha256 AS sha256,archive.artifact_size AS size,
        archive.artifact_format AS format,archive.platform,archive.entrypoint,archive.storage_key
        FROM engine_versions version JOIN engines engine ON engine.id=version.engine_id
        JOIN engine_artifacts archive ON archive.build_hash=version.build_hash
        WHERE engine.object_id=%s AND version.id=%s AND version.distribution='managed' FOR SHARE OF version,archive''',
        (engine_id,version_id)).fetchone()
    if row is None:
        raise ApplicationError('prepared_archive_unavailable','This engine version needs a stored archive before it can be verified.',409)
    try:
        return LegacyEngineArchive(engine_id=engine_id,engine_version_id=version_id,**dict(row))
    except ValidationError as error:
        raise ApplicationError('archive_profile_incompatible','The stored archive does not fit the bounded verification profile.',409) from error


def prepare_artifact(context, payload: PrepareArtifact):
    with control_authority(context.connection,allow_held=True,allow_unconfigured=True) as control:
        return prepare_artifact_under_control(context,payload,control)


def prepare_artifact_under_control(context, payload: PrepareArtifact, control):
    connection,principal = context.connection,context.principal
    caller = capture_caller_authority(connection,principal,'engines:prepare_artifact',completion_policy='finish_admitted_pure_work')
    archive = read_archive(connection,UUID(context.target.object_id),payload.engine_version_id)
    if archive.build_hash!=payload.expected_build_hash or archive.sha256!=payload.expected_archive_sha256:
        raise ApplicationError('preparation_input_changed','The selected build or archive changed. Refresh before admitting preparation.',409)
    runtime = connection.execute('SELECT * FROM execution_runtimes WHERE id=%s FOR SHARE',(payload.runtime_id,)).fetchone()
    if runtime is None:
        raise ApplicationError('runtime_unavailable','Select a registered artifact-verification runtime.',409)
    root = connection.execute("SELECT * FROM execution_admission_roots WHERE key='engine_preparation.v1' FOR UPDATE").fetchone()
    if root is None:
        raise ApplicationError('admission_unavailable','The preparation allocation root is unavailable.',503)
    if not control['recovery_hold']:
        raise ApplicationError('execution_activation_incompatible','This release supports admission while execution activation is held.',503)
    count = connection.execute('''SELECT count(*) AS total,count(*) FILTER (WHERE operation.actor=%s) AS actor
        FROM execution_jobs job JOIN execution_operations operation ON operation.id=job.owner_operation
        WHERE job.admission_root=%s AND job.status NOT IN ('cancelled','succeeded','failed')''',(principal.actor,root['key'])).fetchone()
    if count['total']>=root['max_outstanding_jobs'] or count['actor']>=root['max_actor_jobs']:
        raise ApplicationError('preparation_quota_exceeded','The outstanding preparation limit is reached. Finish or cancel pending work before admitting more.',429)
    now = connection.execute('SELECT clock_timestamp() AS now').fetchone()['now']
    job_id,operation_id = uuid4(),uuid4()
    spec = ArtifactVerificationSpec(logical_item=job_id,runtime_id=payload.runtime_id,runtime_image=runtime['image_digest'],runtime_source=runtime['source_revision'],input=archive)
    digest = specification_digest(spec)
    mandate = PreparationMandate(caller=caller,specification_digest=digest,namespace=job_id,expires_at=now+timedelta(hours=24),
        output_engine=archive.engine_id,operand=OperandUse(engine_id=archive.engine_id,engine_version_id=archive.engine_version_id,build_hash=archive.build_hash,archive_sha256=archive.sha256))
    for identity,kind in ((operation_id,'operation'),(job_id,'job')):
        connection.execute('INSERT INTO application_objects(id,type_key) VALUES (%s,%s)',(identity,kind))
    connection.execute('''INSERT INTO execution_operations(id,actor,command,admission_root) VALUES (%s,%s,%s,%s)''',
        (operation_id,principal.actor,context.request.command,mandate.allocation_root))
    reason = {'code':'control_plane_unavailable','message':'Execution is waiting for a verified controller and runtime.'}
    connection.execute('''INSERT INTO execution_jobs(id,owner_operation,runtime_id,admission_root,workload,specification,specification_digest,mandate,not_after,block_reason,admitted_incarnation)
        VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s,%s::jsonb,%s,%s::jsonb,%s)''',
        (job_id,operation_id,payload.runtime_id,mandate.allocation_root,spec.workload,spec.model_dump_json(),digest,mandate.model_dump_json(),mandate.expires_at,json.dumps(reason),control['control_incarnation']))
    connection.execute("INSERT INTO execution_operation_jobs(operation_id,job_id,interest,owner_operation) VALUES (%s,%s,'owning',%s)",(operation_id,job_id,operation_id))
    for source,relation,target in ((operation_id,'operation.job',job_id),(job_id,'job.runtime',payload.runtime_id),(job_id,'job.input_engine',archive.engine_id)):
        connection.execute('INSERT INTO application_object_relations(source_id,relation,target_id) VALUES (%s,%s,%s)',(source,relation,target))
    return Outcome(str(operation_id),1,AdmissionOutput(operation_id=operation_id,job_id=job_id).model_dump(mode='json'))


def validate_preparation_mandate(connection, specification: ArtifactVerificationSpec, mandate: PreparationMandate):
    source = specification.input
    if not preparation_binding_matches(specification,mandate):
        raise ApplicationError('execution_mandate_mismatch','The requested work is outside its admitted input and destination tuple.',403)
    now = connection.execute('SELECT clock_timestamp() AS now').fetchone()['now']
    if mandate.expires_at<=now:
        raise ApplicationError('execution_mandate_expired','The admitted preparation deadline expired.',403)
    principal = current_caller_authority(connection,mandate.caller,mandate.operand.permission)
    if read_archive(connection,source.engine_id,source.engine_version_id)!=source:
        raise ApplicationError('preparation_input_changed','The admitted archive source changed; a new preparation decision is required.',409)
    runtime = connection.execute('SELECT image_digest,source_revision FROM execution_runtimes WHERE id=%s FOR SHARE',(specification.runtime_id,)).fetchone()
    if runtime is None or runtime['image_digest']!=specification.runtime_image or runtime['source_revision']!=specification.runtime_source:
        raise ApplicationError('runtime_mismatch','The admitted runtime identity is unavailable.',409)
    return principal


def cancel_operation(context, payload: CancelOperation):
    connection,target = context.connection,context.target
    if target.data['actor']!=context.principal.actor and '*' not in context.principal.scopes:
        raise ApplicationError('permission_denied','Only this operation owner or a site administrator can cancel its interest.',403)
    jobs = connection.execute('''SELECT job.* FROM execution_jobs job JOIN execution_operation_jobs link ON link.job_id=job.id
        WHERE link.operation_id=%s ORDER BY job.id FOR UPDATE OF job''',(target.object_id,)).fetchall()
    if len(jobs)>16 or any(job['status'] not in {'blocked','cancelled'} for job in jobs):
        raise ApplicationError('operation_control_unavailable','This operation requires its active execution and cleanup control path.',409)
    if target.data['status']!='cancelled':
        connection.execute("UPDATE execution_operations SET status='cancelled',cancel_requested=TRUE,cancel_reason=%s WHERE id=%s",(payload.reason,target.object_id))
    cancelled,continuing = 0,0
    for job in jobs:
        consumers = connection.execute('''SELECT 1 FROM execution_operation_jobs link JOIN execution_operations operation ON operation.id=link.operation_id
            WHERE link.job_id=%s AND operation.status<>'cancelled' LIMIT 1''',(job['id'],)).fetchone()
        if consumers is not None:
            continuing+=1
        elif job['status']!='cancelled':
            connection.execute("UPDATE execution_jobs SET status='cancelled',cancel_requested=TRUE,cancel_reason=%s WHERE id=%s",(payload.reason,job['id']))
            cancelled+=1
    version = connection.execute('SELECT state_version FROM application_objects WHERE id=%s',(target.object_id,)).fetchone()['state_version']
    return Outcome(target.object_id,version,CancellationOutput(operation_id=target.object_id,status='cancelled',cancelled_jobs=cancelled,continuing_jobs=continuing).model_dump(mode='json'))


def project_operation(connection, principal, result):
    resolve_operation(connection,principal,result['object_id'])
    return result


def project_runtime(connection, principal, result):
    principal.require('execution:configure')
    return result


def describe_cancel(connection, principal, target):
    return {'object_id':target.object_id,'type':'operation','version_channel':'state_version','version':target.version,'input':{'reason':''}}


def describe_preparation(connection, principal, target):
    return {'object_id':target.object_id,'legacy_id':target.legacy_id,'type':'engine','result_type':'operation',
            'version_channel':None,'version':None,'input_schema':PrepareArtifact.model_json_schema()}


def commands():
    return (
        Command('execution.runtimes.register.v1','execution_runtime','execution:configure',None,RegisterRuntime,RuntimeOutput,no_target,register_runtime,no_target,project_runtime,False,False),
        Command('engines.prepare_artifact.v1','operation','engines:prepare_artifact',None,PrepareArtifact,AdmissionOutput,ENGINE.resolve,prepare_artifact,describe_preparation,project_operation,True,False),
        Command('operations.cancel.v1','operation','operations:cancel','state_version',CancelOperation,CancellationOutput,resolve_operation,cancel_operation,describe_cancel,project_operation),
    )
