import { onBeforeUnmount, onMounted, ref } from 'vue'

import type { Identifier, StreamEnvelope } from '@/components/public/types'

interface SpectatorSnapshot {
  spectator_counts?: Record<string, number>
}

interface SpectatorChange {
  tournament_id?: Identifier
  spectator_count?: number
}

export function useTournamentSpectators() {
  const counts = ref<Record<string, number>>({})
  let source: EventSource | null = null

  function spectatorCount(tournamentId: Identifier, fallback = 0): number {
    return counts.value[String(tournamentId)] ?? fallback
  }

  function handleSnapshot(event: Event): void {
    const envelope = parseEnvelope<SpectatorSnapshot>(event)
    if (!envelope?.data.spectator_counts) return
    counts.value = { ...counts.value, ...envelope.data.spectator_counts }
  }

  function handleChange(event: Event): void {
    const envelope = parseEnvelope<SpectatorChange>(event)
    const payload = envelope?.data
    if (payload?.tournament_id === undefined || payload.spectator_count === undefined) return
    counts.value = {
      ...counts.value,
      [String(payload.tournament_id)]: payload.spectator_count,
    }
  }

  onMounted(() => {
    if (typeof EventSource === 'undefined') return
    source = new EventSource('/tournament-spectators/events')
    source.addEventListener('spectators.snapshot', handleSnapshot)
    source.addEventListener('spectators.changed', handleChange)
  })

  onBeforeUnmount(() => {
    source?.close()
    source = null
  })

  return { spectatorCount }
}

function parseEnvelope<T>(event: Event): StreamEnvelope<T> | null {
  try {
    return JSON.parse((event as MessageEvent<string>).data) as StreamEnvelope<T>
  } catch {
    return null
  }
}
