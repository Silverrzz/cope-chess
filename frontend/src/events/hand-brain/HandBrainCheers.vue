<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { api } from '@/api/client'
import { useViewerSettings } from '@/composables/useViewerSettings'
import type { EventDetailResponse } from '@/types/events'
import type { HandBrainPayload, HandBrainTeam } from './types'

const props = defineProps<{ detail: EventDetailResponse }>()
const payload = computed(() => props.detail.custom as HandBrainPayload)
const { confettiEnabled } = useViewerSettings()
const clientId = globalThis.crypto?.randomUUID?.() ?? `cheer-${Date.now()}-${Math.random().toString(36).slice(2)}`
const seenCheers = new Set<string>()
const timers = new Set<number>()
const bursts = ref<{ id: string; x: number; y: number; color: string; pieces: Record<string, string>[] }[]>([])
let lastCheer = 0
let mounted = false

async function cheer(team: HandBrainTeam) {
  const now = Date.now()
  if (now - lastCheer < 350) return
  lastCheer = now
  try {
    const response = await api.post<{ cheer_id: string }>(
      `/api/events/${encodeURIComponent(props.detail.event.slug)}/hand-brain/cheers`,
      { body: { team_id: team.id }, headers: { 'X-Cope-Cheer-Client': clientId } },
    )
    if (mounted) launchCheer(team.id, response.cheer_id)
  } catch {
    return
  }
}

function handleCheer(event: Event) {
  const detail = (event as CustomEvent<{ id?: string; event_id?: number; team_id?: number }>).detail
  if (detail?.event_id === props.detail.event.id && detail.id && detail.team_id != null) {
    launchCheer(detail.team_id, detail.id)
  }
}

function launchCheer(teamId: number, id: string) {
  if (!id || !confettiEnabled.value || seenCheers.has(id) || bursts.value.length >= 6) return
  const team = payload.value.teams.find(item => item.id === teamId)
  if (!team) return
  seenCheers.add(id)
  if (seenCheers.size > 128) seenCheers.delete(seenCheers.values().next().value!)
  const buttons = document.querySelectorAll(`[data-cheer-team-id="${teamId}"]`)
  const button = Array.from(buttons).reverse().find(item => {
    const bounds = item.getBoundingClientRect()
    const clip = item.closest('.bracket-scroll')?.getBoundingClientRect()
    return bounds.left >= (clip?.left ?? 0) && bounds.right <= (clip?.right ?? window.innerWidth)
      && bounds.top >= 0 && bounds.bottom <= window.innerHeight
  })
  const origin = button?.getBoundingClientRect()
  bursts.value.push({
    id,
    color: team.color,
    x: origin ? origin.left + origin.width / 2 : window.innerWidth / 2,
    y: origin ? origin.top + origin.height / 2 : window.innerHeight * .7,
    pieces: Array.from({ length: 14 }, () => ({
      '--cheer-start-x': `${-1.4 + Math.random() * 2.8}rem`,
      '--cheer-start-y': `${-.7 + Math.random() * 1.4}rem`,
      '--cheer-end-x': `${-5.8 + Math.random() * 11.6}rem`,
      '--cheer-end-y': `${-3.2 - Math.random() * 5.6}rem`,
      '--cheer-rotation': `${-260 + Math.random() * 520}deg`,
      '--cheer-delay': `${Math.random() * 140}ms`,
      '--cheer-duration': `${720 + Math.random() * 330}ms`,
      '--cheer-size': `${.18 + Math.random() * .2}rem`,
    })),
  })
  const timer = window.setTimeout(() => {
    timers.delete(timer)
    bursts.value = bursts.value.filter(burst => burst.id !== id)
  }, 1250)
  timers.add(timer)
}

onMounted(() => {
  mounted = true
  window.addEventListener('cope:event-cheer', handleCheer)
})
onUnmounted(() => {
  mounted = false
  window.removeEventListener('cope:event-cheer', handleCheer)
  for (const timer of timers) window.clearTimeout(timer)
})
</script>

<template>
  <slot :cheer="cheer" />
  <Teleport to="body">
    <div v-if="confettiEnabled && bursts.length" class="cheer-layer" aria-hidden="true">
      <span v-for="burst in bursts" :key="burst.id" class="cheer-burst" :style="{ left: `${burst.x}px`, top: `${burst.y}px`, '--cheer-color': burst.color }">
        <i v-for="(piece, index) in burst.pieces" :key="index" :style="piece"></i>
      </span>
    </div>
  </Teleport>
</template>

<style scoped>
.cheer-layer { position: fixed; z-index: 46; inset: 0; overflow: hidden; pointer-events: none; }
.cheer-burst { position: absolute; width: 1px; height: 1px; }
.cheer-burst i { position: absolute; width: var(--cheer-size); height: calc(var(--cheer-size) * 1.6); border-radius: .08rem; background: var(--cheer-color); opacity: 0; animation: cheer-pop var(--cheer-duration) cubic-bezier(.16, .72, .28, 1) forwards; animation-delay: var(--cheer-delay); }
.cheer-burst i:nth-child(even) { height: var(--cheer-size); border-radius: 50%; filter: brightness(1.22); }
@keyframes cheer-pop {
  0% { opacity: 0; transform: translate(var(--cheer-start-x), var(--cheer-start-y)) rotate(0) scale(.5); }
  13% { opacity: .95; }
  100% { opacity: 0; transform: translate(var(--cheer-end-x), var(--cheer-end-y)) rotate(var(--cheer-rotation)) scale(1); }
}
@media (prefers-reduced-motion: reduce) { .cheer-burst i { animation-duration: .18s; } }
</style>
