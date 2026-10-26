from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ActivitySource:
    key: str
    label: str
    kind: str
    owner: str
    table: str
    title_expression: str
    state_expression: str
    created_expression: str
    actor_expression: str
    inspect_url: str
    owns_actor: bool = False

    def available(self, principal) -> bool:
        return global_inspection(principal) or self.owns_actor and "activity:read" in principal.scopes

    def statement(self, principal, cut) -> tuple[str,list]:
        where = "%s OR source.actor=%s" if self.owns_actor else "%s"
        parameters = [self.kind,self.key,self.owner,self.inspect_url,global_inspection(principal)]
        if self.owns_actor:
            parameters.append(principal.actor)
        parameters.append(cut)
        statement = f"""SELECT object.id,%s::text AS kind,%s::text AS source,
             source.id::text AS source_id,left({self.title_expression},300) AS title,
             {self.state_expression} AS state,source.status::text AS source_state,
             {self.actor_expression} AS owner_actor,%s::text AS execution_owner,%s::text AS inspect_url,
             CASE WHEN routing.held THEN 'held' ELSE 'legacy' END AS execution_route,routing.generation AS ownership_generation,
             'legacy_observed'::text AS ownership_mode,'status_only'::text AS mapping_quality,
             'not_recorded'::text AS cleanup_state,object.created_at AS observed_at,
             left(({self.created_expression})::text,100) AS source_created_at,object.content_revision,object.state_version
             FROM {self.table} source JOIN application_objects object ON object.id=source.object_id
             JOIN execution_ownership routing ON routing.object_id=object.id
             WHERE ({where}) AND object.created_at<=%s"""
        return statement,parameters


def global_inspection(principal) -> bool:
    return "*" in principal.scopes or "activity:inspect_legacy" in principal.scopes


def sources() -> tuple[ActivitySource,...]:
    providers = (
        ActivitySource("legacy.control_operation","Control operations","operation","Legacy control runner","control_operations",
                       "source.title","CASE WHEN source.status='pending' THEN 'queued' ELSE source.status END","source.created_at","source.actor","/admin/control",True),
        ActivitySource("legacy.tool_job","Tool runs","job","Legacy worker server","tool_jobs","source.tool_name",
                       "CASE WHEN source.status='completed' THEN 'succeeded' ELSE source.status END","source.created_at","NULL::text","/admin/tools"),
        ActivitySource("legacy.benchmark_job","Benchmarks","job","Legacy benchmark server","benchmark_jobs",
                       "source.engine_name || ' ' || source.engine_version","source.status","source.scheduled_at","NULL::text","/admin/workers"),
    )
    if len({provider.key for provider in providers})!=len(providers):
        raise RuntimeError("Duplicate activity source registration")
    if any(provider.kind not in {"operation","job"} or not provider.inspect_url.startswith("/admin/") for provider in providers):
        raise RuntimeError("Activity sources require a supported resource kind and an installed admin inspector")
    return providers
