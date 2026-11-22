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
}>()

const origin = 'https://ptn.ninja'
const frame = ref<HTMLIFrameElement | null>(null)
const visible = ref(false)
const failed = ref(false)
let configured = false
let themeReady = false
let sentPosition = ''
let confirmedPosition = ''
let deadline: number | undefined
let handshake: number | undefined

function send(action: string, value?: unknown): void {
  frame.value?.contentWindow?.postMessage({ action, value }, origin)
}

function fallback(): void {
  window.clearTimeout(deadline)
  window.clearInterval(handshake)
  visible.value = false
  failed.value = true
}

function updateBoardOptions(): void {
  send('SET_UI', {
    axisLabels: props.coordinates,
    boardTransform: [props.orientation === 'black' ? 2 : 0, 0],
    showRoads: props.showRoads,
  })
}

function updatePosition(forceSnapshot = false): void {
  if (!themeReady || failed.value) return
  if (!forceSnapshot && sentPosition !== confirmedPosition) return

  const squares = takMovePath(props.lastMove).map(takSquareName)
  if (!forceSnapshot && sentPosition === props.tps) {
    if (props.compact) send('HIGHLIGHT_SQUARES', squares)
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
    deadline = window.setTimeout(fallback, visible.value ? 4000 : 10000)
  }
}

function onMessage(event: MessageEvent): void {
  if (failed.value || event.origin !== origin || event.source !== frame.value?.contentWindow) return
  const message = event.data
  if (!message || typeof message !== 'object') return

  if (message.action === 'GET_THEME') {
    if (!configured) {
      configured = true
      window.clearInterval(handshake)
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
    } else if (!themeReady && message.value?.id === 'discord') {
      themeReady = true
      updatePosition()
    }
  } else if (themeReady && message.action === 'GAME_STATE') {
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
  configured = false
  themeReady = false
  sentPosition = ''
  confirmedPosition = ''
  visible.value = false
  window.clearTimeout(deadline)
  window.clearInterval(handshake)
  deadline = window.setTimeout(fallback, 10000)
  handshake = window.setInterval(() => send('GET_THEME'), 350)
  send('GET_THEME')
}

onMounted(() => {
  window.addEventListener('message', onMessage)
  deadline = window.setTimeout(fallback, 12000)
})

onBeforeUnmount(() => {
  window.removeEventListener('message', onMessage)
  window.clearTimeout(deadline)
  window.clearInterval(handshake)
})

watch([() => props.tps, () => props.lastMove], () => updatePosition())
watch([() => props.orientation, () => props.coordinates, () => props.showRoads], () => {
  if (configured && !failed.value) updateBoardOptions()
})
</script>

<template>
  <div class="ninja-board" :class="{ 'ninja-board--compact': compact }">
    <div class="ninja-board__fallback"><slot /></div>
    <iframe
      v-if="!failed"
      class="ninja-board__frame"
      :class="{ 'ninja-board__frame--visible': visible }"
      :src="origin + '/'"
      title="PTN Ninja Discord board"
      aria-hidden="true"
      tabindex="-1"
      sandbox="allow-scripts allow-same-origin"
      referrerpolicy="no-referrer"
      ref="frame"
      @load="handshakeOnLoad"
      @error="fallback"
    />
  </div>
</template>

<style scoped>
.ninja-board { position: relative; container-type: size; }
.ninja-board__fallback { position: absolute; inset: 0; display: grid; place-items: center; }
.ninja-board__fallback :deep(.tak-board) { width: min(100cqw, 100cqh); }
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
