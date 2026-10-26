import { onBeforeUnmount, shallowRef } from 'vue'
import { ApiError } from '@/api/client'
import { viewBatch, viewSnapshot } from '@/api/views'
import type { ViewRequest } from '@/api/views'
import type { ApplicationView, ApplicationViewBatch, ApplicationViewOutputs, ApplicationViewSnapshot } from '@/types/application-inputs'

type ViewStatus = 'stopped' | 'connecting' | 'synchronizing' | 'current' | 'stale' | 'recovering' | 'incompatible' | 'denied' | 'unavailable'
type ViewState<T> = { data: T | null; checkpoint: string | null; context: string | null; dirty: boolean; status: ViewStatus; error: string; snapshotSerial: number; checkedAt: string | null }

export class ViewProtocolError extends Error {
  constructor(message: string, readonly incompatible = false) { super(message); this.name = 'ViewProtocolError' }
}

function record(value: unknown): value is Record<string, unknown> { return value !== null && typeof value === 'object' && !Array.isArray(value) }
function envelope(value: unknown, view: string): asserts value is Record<string, unknown> {
  if (!record(value) || value.schema_version !== 1) throw new ViewProtocolError('This view requires a compatible application version.',true)
  if (value.view !== view || typeof value.context !== 'string' || !/^[a-f0-9]{64}$/.test(value.context)
      || typeof value.checkpoint !== 'string' || !value.checkpoint.length || value.checkpoint.length > 4096
      || typeof value.expires_at !== 'string' || !Number.isFinite(Date.parse(value.expires_at))) {
    throw new ViewProtocolError('The view response has an invalid context or checkpoint.')
  }
}
function snapshotEnvelope(value: unknown, view: string): ApplicationViewSnapshot {
  envelope(value,view)
  if (value.refresh_mode !== 'invalidation' || !record(value.data)) throw new ViewProtocolError('The snapshot cannot be applied with this view contract.',true)
  return value as ApplicationViewSnapshot
}
function batchEnvelope(value: unknown, view: string, previous: string, context: string): ApplicationViewBatch {
  envelope(value,view)
  if (value.previous_checkpoint !== previous || value.context !== context || typeof value.invalidated !== 'boolean' || typeof value.has_more !== 'boolean') {
    throw new ViewProtocolError('The change batch does not continue the application-applied checkpoint.')
  }
  return value as ApplicationViewBatch
}

export function useCoherentView<V extends ApplicationView>(decode: (data: unknown) => ApplicationViewOutputs[V]) {
  type Output = ApplicationViewOutputs[V]
  let snapshotSerial = 0
  const empty = (): ViewState<Output> => ({data:null,checkpoint:null,context:null,dirty:true,status:'stopped',error:'',snapshotSerial,checkedAt:null})
  const state = shallowRef<ViewState<Output>>(empty())
  let configured: ViewRequest<V> | null = null
  let generation = 0
  let controller: AbortController | null = null
  let timer: ReturnType<typeof setTimeout> | null = null
  let networkFailures = 0
  let recoveryFailures = 0
  let catchupBatches = 0
  let disposed = false

  function cancel(): number {
    ++generation; controller?.abort(); controller = null
    if (timer !== null) clearTimeout(timer)
    timer = null
    return generation
  }
  function schedule(current: number, delay: number): void {
    if (!disposed && configured && current === generation) timer = setTimeout(() => { timer = null; void tick(current) },delay)
  }
  async function tick(current: number): Promise<void> {
    const request = configured
    if (disposed || !request || current !== generation) return
    const flight = new AbortController()
    controller = flight
    let delay = 1500 + Math.floor(Math.random()*300)
    let retry = true
    try {
      const previous = state.value
      if (previous.dirty || !previous.checkpoint || !previous.context) {
        const result = snapshotEnvelope(await viewSnapshot(request,flight.signal),request.view)
        if (disposed || current !== generation) return
        let data: Output
        try { data = decode(result.data) }
        catch (cause) { throw cause instanceof ViewProtocolError ? cause : new ViewProtocolError('The snapshot data does not match the installed view schema.',true) }
        if (disposed || current !== generation) return
        state.value = {data,checkpoint:result.checkpoint,context:result.context,dirty:false,status:'current',error:'',snapshotSerial:++snapshotSerial,checkedAt:new Date().toISOString()}
        catchupBatches = 0
      } else {
        const result = batchEnvelope(await viewBatch(request,previous.checkpoint,flight.signal),request.view,previous.checkpoint,previous.context)
        if (disposed || current !== generation) return
        catchupBatches = result.has_more ? catchupBatches+1 : 0
        const dirty = previous.dirty || result.invalidated || catchupBatches >= 10
        state.value = {...previous,checkpoint:result.checkpoint,dirty,status:dirty || result.has_more ? 'synchronizing' : 'current',error:'',checkedAt:new Date().toISOString()}
        delay = dirty ? 250 : result.has_more ? 50 : delay
        recoveryFailures = 0
      }
      networkFailures = 0
    } catch (cause) {
      if (disposed || current !== generation || cause instanceof DOMException && cause.name === 'AbortError') return
      const error = cause instanceof Error ? cause.message : 'The view could not synchronize.'
      if (cause instanceof ApiError && [401,403].includes(cause.status)) {
        state.value = {...empty(),status:'denied',error}; retry = false
      } else if (cause instanceof ApiError && [404,422,426].includes(cause.status) || cause instanceof ViewProtocolError && cause.incompatible) {
        state.value = {...state.value,dirty:true,status:cause instanceof ApiError && cause.status === 404 ? 'unavailable' : 'incompatible',error}; retry = false
      } else if (cause instanceof ViewProtocolError || cause instanceof SyntaxError || cause instanceof ApiError && cause.status === 409) {
        ++recoveryFailures
        state.value = {...state.value,dirty:true,status:recoveryFailures < 3 ? 'recovering' : 'unavailable',error}
        retry = recoveryFailures < 3; delay = 500*recoveryFailures
      } else {
        ++networkFailures
        state.value = {...state.value,status:networkFailures < 8 ? 'stale' : 'unavailable',error}
        retry = networkFailures < 8; delay = Math.min(15000,500*2**networkFailures)+Math.floor(Math.random()*300)
      }
    } finally {
      if (controller === flight) controller = null
      if (retry) schedule(current,delay)
    }
  }
  function configure(request: ViewRequest<V> | null): void {
    const current = cancel()
    networkFailures = 0; recoveryFailures = 0; catchupBatches = 0
    configured = request ? JSON.parse(JSON.stringify(request)) as ViewRequest<V> : null
    state.value = {...empty(),status:configured ? 'connecting' : 'stopped'}
    if (configured && !disposed) void tick(current)
  }
  function refresh(): void {
    if (!configured || disposed) return
    const current = cancel()
    networkFailures = 0; recoveryFailures = 0; catchupBatches = 0
    state.value = {...state.value,dirty:true,status:state.value.data ? 'synchronizing' : 'connecting',error:''}
    void tick(current)
  }
  function stop(): void { cancel(); configured = null; state.value = empty() }
  function deny(error: string): void { cancel(); state.value = {...empty(),status:'denied',error} }
  function accessExpired(): void { deny('Your session expired. Sign in again to read this private view.') }
  window.addEventListener('cope:session-expired',accessExpired)
  onBeforeUnmount(() => { disposed = true; stop(); window.removeEventListener('cope:session-expired',accessExpired) })
  return {state,configure,refresh,stop,deny}
}
