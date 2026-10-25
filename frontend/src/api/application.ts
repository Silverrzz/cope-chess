import { api } from '@/api/client'
import type { applicationCommandContracts, ApplicationCommand, ApplicationInputs, ApplicationOutputs, ApplicationQuery, ApplicationQueryInputs, ApplicationQueryOutputs } from '@/types/application-inputs'

export type { ApplicationCommand, ApplicationInputs } from '@/types/application-inputs'

export interface EditTarget<C extends ApplicationCommand> {
  object_id: string
  legacy_id: number
  type: string
  version_channel: string
  version: number
  input: ApplicationInputs[C]
}

export type ApplicationRequest<C extends ApplicationCommand> = {
  command: C
  idempotency_key: string
  input: ApplicationInputs[C]
} & (typeof applicationCommandContracts[C]['requiresTarget'] extends true ? { target: string } : { target?: never })
  & (typeof applicationCommandContracts[C]['requiresVersion'] extends true ? { expected_version: number } : { expected_version?: never })

export interface CommandReceipt<C extends ApplicationCommand = ApplicationCommand> {
  receipt_id: string
  status: 'completed'
  command: C
  object_id: string
  legacy_id: number | null
  version: number | null
  basis_version: number | null
  output: ApplicationOutputs[C]
}

export function describeTarget<C extends ApplicationCommand>(command: C, target: number | string): Promise<EditTarget<C>> {
  return api.get(`/api/admin/control/application/targets/${encodeURIComponent(command)}/${encodeURIComponent(target)}`)
}

export function executeCommand<C extends ApplicationCommand>(request: ApplicationRequest<C>): Promise<CommandReceipt<C>> {
  return api.post('/api/admin/control/application/commands', { body: request })
}

export async function applicationQuery<T = never, Q extends ApplicationQuery = ApplicationQuery>(query: Q, input: ApplicationQueryInputs[Q], target?: string, signal?: AbortSignal): Promise<[T] extends [never] ? ApplicationQueryOutputs[Q] : T> {
  const response = await api.post<{ query: Q; data: [T] extends [never] ? ApplicationQueryOutputs[Q] : T }>('/api/admin/control/application/queries', { body: { query, input, ...(target ? { target } : {}) }, signal })
  return response.data
}
