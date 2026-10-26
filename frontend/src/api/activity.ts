import { ViewProtocolError } from '@/composables/useCoherentView'
import type { ApplicationViewOutputs } from '@/types/application-inputs'

type Page = ApplicationViewOutputs['legacy.activity.list.v1']
function record(value: unknown): value is Record<string, unknown> { return value !== null && typeof value === 'object' && !Array.isArray(value) }
function integer(value: unknown, minimum: number): boolean { return typeof value === 'number' && Number.isSafeInteger(value) && value >= minimum }
function instant(value: unknown): boolean { return typeof value === 'string' && value.length < 100 && Number.isFinite(Date.parse(value)) }
const states = new Set(['planned','blocked','queued','leased','exported','running','waiting','cancelling','succeeded','failed','cancelled','unknown'])

export function decodeActivityPage(value: unknown): Page {
  const fail = (): never => { throw new ViewProtocolError('The activity snapshot does not match the installed schema.',true) }
  if (!record(value) || !Array.isArray(value.items) || value.items.length > 100 || !record(value.count) || !record(value.basis)) return fail()
  if (!integer(value.count.count,0) || Number(value.count.count) > 1000 || typeof value.count.more !== 'boolean' || value.count.limit !== 1000
      || value.next_cursor !== null && (typeof value.next_cursor !== 'string' || value.next_cursor.length > 4096)
      || !instant(value.basis.observed_before) || value.basis.membership !== 'current_at_request' || value.basis.order !== 'observed_at_desc_id_desc') return fail()
  const ids = new Set<string>()
  for (const item of value.items) {
    if (!record(item) || typeof item.id !== 'string' || !/^[a-f0-9-]{36}$/i.test(item.id) || ids.has(item.id)
        || !['operation','job'].includes(String(item.kind)) || !states.has(String(item.state))
        || !['source','source_id','title','source_state','execution_owner','inspect_url'].every((field) => typeof item[field] === 'string')
        || !(item.inspect_url as string).startsWith('/admin/') || item.owner_actor !== null && typeof item.owner_actor !== 'string'
        || item.ownership_mode !== 'legacy_observed' || item.mapping_quality !== 'status_only' || item.cleanup_state !== 'not_recorded'
        || !['legacy','held'].includes(String(item.execution_route)) || !integer(item.ownership_generation,1)
        || !instant(item.observed_at) || item.source_created_at !== null && !instant(item.source_created_at)
        || !integer(item.content_revision,1) || !integer(item.state_version,1)) return fail()
    ids.add(item.id)
  }
  return value as Page
}
