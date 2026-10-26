from __future__ import annotations

from dataclasses import dataclass
import uuid
from typing import Literal

from pydantic import Field

from cope.application.contracts import Command,Input,Outcome,Query,Target
from cope.application.errors import ApplicationError


@dataclass(frozen=True)
class LegacyWork:
    namespace: str
    table: str
    kind: str
    unstarted: str


SOURCES = (
    LegacyWork('legacy.control_operation','control_operations','operation',
               "source.status IN ('planned','pending') AND source.lease_owner IS NULL AND source.lease_until IS NULL AND NOT source.cancel_requested "
               "AND NOT EXISTS (SELECT 1 FROM control_steps step WHERE step.operation_id=source.id AND (step.attempts<>0 OR step.status<>'pending' OR step.started_at IS NOT NULL OR step.finished_at IS NOT NULL OR step.result IS NOT NULL))"),
    LegacyWork('legacy.tool_job','tool_jobs','job',
               "source.status='queued' AND source.attempt=0 AND source.worker_id IS NULL AND source.started_at IS NULL AND source.finished_at IS NULL "
               "AND NOT EXISTS (SELECT 1 FROM tool_job_items item WHERE item.job_id=source.id AND (item.status<>'pending' OR item.started_at IS NOT NULL OR item.finished_at IS NOT NULL)) "
               "AND NOT EXISTS (SELECT 1 FROM puzzle_suite_runs run JOIN puzzle_suite_engine_results result ON result.run_id=run.id WHERE run.job_id=source.id)"),
    LegacyWork('legacy.benchmark_job','benchmark_jobs','job',
               "source.status='queued' AND source.attempt=0 AND source.benchmarker_id IS NULL AND source.started_at IS NULL AND source.finished_at IS NULL "
               "AND NOT EXISTS (SELECT 1 FROM engine_benchmarks result WHERE result.job_id=source.id)"),
)


class HoldLegacy(Input):
    held: bool = Field(strict=True)
    reason: str = Field(min_length=1,max_length=1000)


class RouteOutput(Input):
    id: str
    kind: Literal['operation','job']
    source: str
    source_id: str
    backend: Literal['legacy']
    held: bool
    ownership_generation: int
    source_present: bool
    source_state: str | None
    cleanup_state: Literal['not_recorded'] = 'not_recorded'
    canonical_transfer_supported: Literal[False] = False


class RouteHistory(Input):
    generation: int
    backend: str
    held: bool
    changed_by: str
    reason: str
    changed_at: str


class InspectRoute(Input):
    limit: int = Field(default=30,strict=True,ge=1,le=100)
    before_generation: int | None = Field(default=None,strict=True,gt=0)


class RouteDetail(RouteOutput):
    history: list[RouteHistory]
    next_generation: int | None


def resolve_route(connection, principal, target, *, lock, kind=None) -> Target:
    try:
        resource_id = str(uuid.UUID(target))
    except (ValueError,TypeError,AttributeError) as error:
        raise ApplicationError('invalid_target','Supply the legacy work Object UUID.',422) from error
    row = connection.execute(
        """SELECT ownership.*,object.type_key,identity.namespace,identity.alias FROM execution_ownership ownership
           JOIN application_objects object ON object.id=ownership.object_id
           JOIN application_object_aliases identity ON identity.object_id=object.id
           WHERE object.id=%s::uuid AND identity.namespace=ANY(%s::text[])
             AND (%s::text IS NULL OR object.type_key=%s)"""+(' FOR UPDATE OF ownership NOWAIT' if lock else ' FOR SHARE OF ownership NOWAIT'),
        (resource_id,[source.namespace for source in SOURCES],kind,kind),
    ).fetchone()
    if row is None:
        raise ApplicationError('legacy_route_unavailable','This legacy execution route is unavailable.',404)
    source = next(item for item in SOURCES if item.namespace==row['namespace'])
    if source.kind!=row['type_key']:
        raise ApplicationError('identity_mismatch','The legacy ownership mapping requires operator repair.',409)
    native = connection.execute(
        f'SELECT source.status,({source.unstarted}) AS unstarted FROM {source.table} source WHERE source.object_id=%s::uuid'+(' FOR UPDATE OF source NOWAIT' if lock else ''),
        (resource_id,),
    ).fetchone()
    data = {'id':resource_id,'kind':source.kind,'source':source.namespace,'source_id':row['alias'],'backend':row['backend'],
            'held':row['held'],'ownership_generation':row['generation'],'source_present':native is not None,
            'source_state':native['status'] if native else None,'unstarted':bool(native and native['unstarted'])}
    return Target(resource_id,row['generation'],data=data)


def resolve_operation(connection, principal, target, *, lock):
    return resolve_route(connection,principal,target,lock=lock,kind='operation')


def resolve_job(connection, principal, target, *, lock):
    return resolve_route(connection,principal,target,lock=lock,kind='job')


def output(target):
    return RouteOutput.model_validate({key:value for key,value in target.data.items() if key!='unstarted'}).model_dump(mode='json')


def hold_route(context, payload: HoldLegacy):
    target = context.target
    if not target.data['source_present']:
        raise ApplicationError('legacy_source_removed','This source record has been removed; its ownership history is retained.',410)
    if target.data['held']==payload.held:
        return Outcome(target.object_id,target.version,output(target))
    if payload.held and not target.data['unstarted']:
        raise ApplicationError('legacy_work_started','Only never-started work can use this migration hold. Running or previously attempted work needs its owning cleanup and handoff policy.',409)
    changed = context.connection.execute(
        """UPDATE execution_ownership SET held=%s,generation=generation+1,changed_by=%s,reason=%s
           WHERE object_id=%s::uuid AND generation=%s AND backend='legacy' RETURNING generation""",
        (payload.held,context.principal.actor,payload.reason,target.object_id,target.version),
    ).fetchone()
    if changed is None:
        raise ApplicationError('ownership_conflict','The execution owner changed. Refresh this work before changing its routing.',409)
    data = dict(target.data,held=payload.held,ownership_generation=changed['generation'])
    updated = Target(target.object_id,changed['generation'],data=data)
    return Outcome(target.object_id,updated.version,output(updated))


def describe_route(connection, principal, target):
    return {'object_id':target.object_id,'type':target.data['kind'],'version_channel':'ownership_generation','version':target.version,
            'input':{'held':target.data['held'],'reason':''}}


def project_route(connection, principal, result):
    resolve_route(connection,principal,result['object_id'],lock=False)
    return result


def inspect_route(connection, principal, target, payload: InspectRoute):
    resolved = resolve_route(connection,principal,target,lock=False)
    history = connection.execute(
        """SELECT generation,backend,held,changed_by,reason,changed_at::text FROM execution_ownership_history
           WHERE object_id=%s::uuid AND (%s::bigint IS NULL OR generation<%s)
           ORDER BY generation DESC LIMIT %s""",(resolved.object_id,payload.before_generation,payload.before_generation,payload.limit+1),
    ).fetchall()
    items = [dict(row) for row in history[:payload.limit]]
    return dict(output(resolved),history=items,next_generation=items[-1]['generation'] if len(history)>payload.limit else None)


def commands() -> tuple[Command,...]:
    return tuple(Command(f'{prefix}.legacy_hold.v1',kind,'execution:route_legacy','ownership_generation',HoldLegacy,RouteOutput,
                         resolver,hold_route,describe_route,project_route)
                 for prefix,kind,resolver in [('operations','operation',resolve_operation),('jobs','job',resolve_job)])


def queries() -> tuple[Query,...]:
    return (Query('execution.legacy_route.v1','execution:inspect_legacy',InspectRoute,RouteDetail,inspect_route,True),)
