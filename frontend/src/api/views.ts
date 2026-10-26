import { api } from '@/api/client'
import type { applicationViewContracts, ApplicationView, ApplicationViewInputs } from '@/types/application-inputs'

export type ViewRequest<V extends ApplicationView> = { view: V; input: ApplicationViewInputs[V] }
  & (typeof applicationViewContracts[V]['requiresTarget'] extends true ? { target: string } : { target?: never })

export function viewSnapshot<V extends ApplicationView>(request: ViewRequest<V>, signal: AbortSignal): Promise<unknown> {
  return api.post('/api/admin/control/application/snapshots',{body:{...request,schema_versions:[1]},signal})
}

export function viewBatch<V extends ApplicationView>(request: ViewRequest<V>, checkpoint: string, signal: AbortSignal): Promise<unknown> {
  return api.post('/api/admin/control/application/batches',{body:{...request,checkpoint,schema_versions:[1]},signal})
}
