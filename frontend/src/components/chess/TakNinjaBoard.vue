<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { TAK_KOMI, TAK_SIZE, takMovePath, takSquareName } from './tak'

const props = defineProps<{
  tps: string
  previousTps?: string | undefined
  lastMove: string
  orientation: 'white' | 'black'
  coordinates: boolean
  showRoads: boolean
  compact: boolean
  label: string
}>()

const origin = 'https://ptn.ninja'
const frame = ref<HTMLIFrameElement | null>(null)
const visible = ref(false)
const failed = ref(false)
let phase: 'connecting' | 'configuring' | 'ready' = 'connecting'
let sentPosition = ''
let confirmedPosition = ''
let highlightedMove = ''
let deadline: number | undefined
let handshake: number | undefined

function send(action: string, value?: unknown): void {
  frame.value?.contentWindow?.postMessage({ action, value }, origin)
}

function markUnavailable(): void {
  window.clearTimeout(deadline)
  window.clearInterval(handshake)
  visible.value = false
  failed.value = true
}

function waitForFrame(): void {
  failed.value = false
  deadline = window.setTimeout(markUnavailable, 12000)
}

function updateBoardOptions(): void {
  send('SET_UI', {
    axisLabels: props.coordinates,
    boardTransform: [props.orientation === 'black' ? 2 : 0, 0],
    showRoads: props.showRoads,
  })
}

function updatePosition(forceSnapshot = false): void {
  if (phase !== 'ready' || failed.value) return
  if (!forceSnapshot && sentPosition !== confirmedPosition) return

  const samePosition = !forceSnapshot && sentPosition === props.tps
  if (samePosition && highlightedMove === props.lastMove) return
  highlightedMove = props.lastMove
  const squares = takMovePath(highlightedMove).map(takSquareName)
  if (samePosition) {
    send('HIGHLIGHT_SQUARES', squares)
    return
  }
  const append = !forceSnapshot && !props.compact && visible.value && props.previousTps === confirmedPosition && squares.length > 0
  sentPosition = props.tps
  window.clearTimeout(deadline)
  if (append) {
    send('HIGHLIGHT_SQUARES', [])
    send('APPEND_PLY', props.lastMove)
    deadline = window.setTimeout(() => updatePosition(true), 1800)
  } else {
    send('SET_CURRENT_PTN', `[Size "${TAK_SIZE}"]\n[Komi "${TAK_KOMI}"]\n[TPS "${props.tps}"]\n`)
    send('HIGHLIGHT_SQUARES', squares)
    deadline = window.setTimeout(markUnavailable, visible.value ? 4000 : 10000)
  }
}

function onMessage(event: MessageEvent): void {
  if (failed.value || event.origin !== origin || event.source !== frame.value?.contentWindow) return
  const message = event.data
  if (!message || typeof message !== 'object') return

  if (message.action === 'GET_THEME') {
    if (phase === 'connecting') {
      phase = 'configuring'
      send('SET_UI', {
        themeID: 'discord',
        disableBoard: true,
        disableNavigation: true,
        disablePTN: true,
        disableText: true,
        showHeader: false,
        showToolbarAnalysis: false,
        turnIndicator: false,
        gameTimer: false,
        moveNumber: false,
        unplayedPieces: !props.compact,
        verticalLayout: !props.compact,
        verticalLayoutAuto: true,
        animateBoard: !props.compact && !window.matchMedia('(prefers-reduced-motion: reduce)').matches,
        autoAnnotateTak: false,
        axisLabelsSmall: true,
        stackCounts: true,
        notifyGame: false,
        notifyNotes: false,
      })
      updateBoardOptions()
      send('GET_THEME')
    } else if (phase === 'configuring' && message.value?.id === 'discord') {
      phase = 'ready'
      window.clearInterval(handshake)
      updatePosition()
    }
  } else if (phase === 'ready' && message.action === 'GAME_STATE') {
    const rendered = message.value?.tps
    if (rendered === sentPosition) {
      window.clearTimeout(deadline)
      confirmedPosition = sentPosition
      visible.value = true
      updatePosition()
    }
  }
}

function handshakeOnLoad(): void {
  if (failed.value) return
  phase = 'connecting'
  sentPosition = ''
  confirmedPosition = ''
  highlightedMove = ''
  visible.value = false
  window.clearTimeout(deadline)
  window.clearInterval(handshake)
  deadline = window.setTimeout(markUnavailable, 10000)
  handshake = window.setInterval(() => send('GET_THEME'), 350)
  send('GET_THEME')
}

onMounted(() => {
  window.addEventListener('message', onMessage)
  waitForFrame()
})

onBeforeUnmount(() => {
  window.removeEventListener('message', onMessage)
  window.clearTimeout(deadline)
  window.clearInterval(handshake)
})

watch([() => props.tps, () => props.lastMove], () => updatePosition())
watch([() => props.orientation, () => props.coordinates, () => props.showRoads], () => {
  if (phase !== 'connecting' && !failed.value) updateBoardOptions()
})
</script>

<template>
  <div class="ninja-board" :class="{ 'ninja-board--compact': compact }">
    <div v-if="!visible" class="ninja-board__status">
      <span role="status">{{ failed ? 'Tak board unavailable' : 'Loading Tak board…' }}</span>
      <button v-if="failed" type="button" class="button button--secondary button--small" :aria-label="`Retry ${label}`" @click="waitForFrame">Retry</button>
    </div>
    <iframe
      v-if="!failed"
      class="ninja-board__frame"
      :class="{ 'ninja-board__frame--visible': visible }"
      :src="origin + '/'"
      :title="`${label} · PTN Ninja`"
      tabindex="-1"
      sandbox="allow-scripts allow-same-origin"
      referrerpolicy="no-referrer"
      ref="frame"
      @load="handshakeOnLoad"
      @error="markUnavailable"
    />
  </div>
</template>

<style scoped>
.ninja-board { position: relative; }
.ninja-board__status {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: .25rem;
  margin: 0;
  padding: .25rem;
  background: var(--color-surface);
  color: var(--color-text-muted);
  font-size: .75rem;
  text-align: center;
}
.ninja-board--compact .ninja-board__status button { font-size: inherit; padding: .1rem .25rem; min-height: 0; }
.ninja-board__frame {
  --ptn-ninja-content-ratio: .9;
  position: absolute;
  inset: 50% auto auto 50%;
  width: calc((100% - 1px) / var(--ptn-ninja-content-ratio));
  height: calc(100% / var(--ptn-ninja-content-ratio) + 18px);
  transform: translate(-50%, -50%);
  border: 0;
  pointer-events: none;
  visibility: hidden;
}
.ninja-board__frame--visible { visibility: visible; }
.ninja-board--compact .ninja-board__frame {
  width: calc(100% + 31px);
  height: calc(100% + 34px);
}
</style>
