<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { TAK_KOMI, TAK_SIZE, takMovePath, takSquareName } from './tak'

const props = defineProps<{
  tps: string
  lastMove: string
  orientation: 'white' | 'black'
  coordinates: boolean
  showRoads: boolean
}>()

const origin = 'https://ptn.ninja'
const frame = ref<HTMLIFrameElement | null>(null)
const visible = ref(false)
const failed = ref(false)
let configured = false
let themeReady = false
let sentPosition = ''
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

function updatePosition(): void {
  if (!themeReady || failed.value) return
  if (sentPosition !== props.tps) {
    sentPosition = props.tps
    window.clearTimeout(deadline)
    deadline = window.setTimeout(fallback, visible.value ? 4000 : 10000)
    send('SET_CURRENT_PTN', `[Size "${TAK_SIZE}"]\n[Komi "${TAK_KOMI}"]\n[TPS "${props.tps}"]\n\n*`)
  }
  send('HIGHLIGHT_SQUARES', takMovePath(props.lastMove).map(takSquareName))
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
        moveNumber: false,
        unplayedPieces: false,
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
    if (typeof rendered === 'string' && rendered.split(' ', 1)[0] === sentPosition.split(' ', 1)[0]) {
      window.clearTimeout(deadline)
      visible.value = true
    }
  }
}

function handshakeOnLoad(): void {
  if (failed.value) return
  configured = false
  themeReady = false
  sentPosition = ''
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

watch([() => props.tps, () => props.lastMove], updatePosition)
watch([() => props.orientation, () => props.coordinates, () => props.showRoads], () => {
  if (configured && !failed.value) updateBoardOptions()
})
</script>

<template>
  <div class="ninja-board">
    <slot />
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
.ninja-board { position: relative; }
.ninja-board__frame {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  border: 0;
  pointer-events: none;
  visibility: hidden;
}
.ninja-board__frame--visible { visibility: visible; }
</style>
