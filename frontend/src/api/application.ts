import { api } from '@/api/client'
import type { ApplicationCommand, ApplicationInputs } from '@/types/application-inputs'

export type { ApplicationCommand, ApplicationInputs } from '@/types/application-inputs'

export interface EditTarget<C extends ApplicationCommand> {
  object_id: string
  legacy_id: number
  type: string
  version_channel: string
  version: number
  input: ApplicationInputs[C]
}

export interface ApplicationRequest<C extends ApplicationCommand> {
  command: C
  target: string
  expected_version: number
  idempotency_key: string
  input: ApplicationInputs[C]
}

export interface CommandReceipt {
  receipt_id: string
  status: 'completed'
  object_id: string
  legacy_id: number
  version: number
  basis_version: number
}

export function describeTarget<C extends ApplicationCommand>(command: C, target: number | string): Promise<EditTarget<C>> {
  return api.get(`/api/admin/control/application/targets/${encodeURIComponent(command)}/${encodeURIComponent(target)}`)
}

export function executeCommand<C extends ApplicationCommand>(request: ApplicationRequest<C>): Promise<CommandReceipt> {
  return api.post('/api/admin/control/application/commands', { body: request })
}
