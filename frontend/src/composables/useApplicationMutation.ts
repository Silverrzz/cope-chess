import { ref } from 'vue'
import { ApiError, api } from '@/api/client'
import type { ApplicationRequest, CommandReceipt } from '@/api/application'
import type { ApplicationCommand } from '@/types/application-inputs'

export function useApplicationMutation(endpoint = '/api/admin/control/application/commands', headers: () => HeadersInit | undefined = () => undefined) {
  const busy = ref(false)
  const uncertain = ref(false)
  const command = ref<ApplicationCommand | null>(null)
  let pending: { body: unknown; signature: string } | null = null

  async function execute<C extends ApplicationCommand>(request: Omit<ApplicationRequest<C>, 'idempotency_key'>): Promise<CommandReceipt<C>> {
    if (busy.value) throw new Error('Wait for the current action to finish.')
    const signature = JSON.stringify(request)
    if (pending && signature !== pending.signature) throw new Error('Retry the pending action before making another change. Its outcome has not been confirmed.')
    pending ??= { body: JSON.parse(JSON.stringify({ ...request, idempotency_key: crypto.randomUUID() })), signature }
    command.value = (request as { command: C }).command
    busy.value = true
    try {
      const receipt = await api.post<CommandReceipt<C>>(endpoint, { body: pending.body, headers: headers() })
      pending = null
      uncertain.value = false
      command.value = null
      return receipt
    } catch (error) {
      if (error instanceof ApiError && error.status >= 400 && error.status < 500 && error.status !== 408) {
        pending = null
        uncertain.value = false
        command.value = null
      } else uncertain.value = true
      throw error
    } finally { busy.value = false }
  }

  function unavailable(name: ApplicationCommand): boolean { return busy.value || uncertain.value && command.value !== name }
  return { busy, uncertain, command, unavailable, execute }
}
