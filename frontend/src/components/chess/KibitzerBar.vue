<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useViewerSettings } from '@/composables/useViewerSettings'
const props = defineProps<{ fen: string; orientation: 'white' | 'black'; chess960?: boolean; paused?: boolean }>()
const { evaluationPerspective } = useViewerSettings()
const score = ref<{ kind: 'cp' | 'mate'; value: number; depth: number } | null>(null)
const status = ref('Loading Reckless')
const visible = ref(!document.hidden)
let worker: Worker | undefined
let id = 0
let timer: number | undefined
const side = computed(() => props.fen.split(/\s+/)[1] === 'b' ? -1 : 1)
const whiteScore = computed(() => score.value ? score.value.value * side.value : 0)
const whitePercent = computed(() => score.value?.kind === 'mate' ? (whiteScore.value > 0 ? 100 : 0) : 100 / (1 + Math.exp(-whiteScore.value / 240)))
const displayed = computed(() => {
  if (!score.value) return '…'
  const n = evaluationPerspective.value === 'white' ? whiteScore.value : score.value.value
  return score.value.kind === 'mate' ? `#${n}` : `${n >= 0 ? '+' : ''}${(n / 100).toFixed(1)}`
})
const title = computed(() => `Reckless dev aec5cb2 · ${score.value ? `${displayed.value} · depth ${score.value.depth}` : status.value}`)
function stop() {
  window.clearTimeout(timer)
  worker?.terminate()
  worker = undefined
}
function analyze() {
  window.clearTimeout(timer)
  score.value = null
  const current = ++id
  if (!visible.value || props.paused) { stop(); status.value = 'Paused'; return }
  status.value = 'Loading Reckless'
  timer = window.setTimeout(() => {
    if (!worker) worker = new Worker('/engines/reckless/kibitzer-worker.js', { type: 'module', name: 'cope-reckless' })
    worker.onmessage = ({ data }) => {
      if (data.id !== id) return
      if (data.error) { status.value = 'Analysis unavailable'; stop(); return }
      score.value = data
      status.value = 'Analyzing'
    }
    worker.onerror = () => { status.value = 'Analysis unavailable'; stop() }
    worker.postMessage({ id: current, fen: props.fen === 'startpos' ? 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1' : props.fen })
  }, 180)
}
function visibility() { visible.value = !document.hidden }
document.addEventListener('visibilitychange', visibility)
watch(() => [props.fen, props.paused, visible.value], analyze, { immediate: true })
onBeforeUnmount(() => { stop(); document.removeEventListener('visibilitychange', visibility) })
</script>
<template>
  <div class="kibitzer-bar" :title="title" role="meter" aria-label="Reckless evaluation, White advantage" :aria-valuenow="Math.round(whitePercent)" aria-valuemin="0" aria-valuemax="100" :aria-valuetext="score ? title : status" :class="{ flipped: orientation === 'black', unavailable: !score }">
    <div class="kibitzer-bar__white" :style="{ height: `${whitePercent}%` }" />
    <strong>{{ displayed }}</strong>
  </div>
</template>
<style scoped>
.kibitzer-bar { position: relative; width: 1.75rem; height: var(--arena-board-size); max-height: 100%; min-height: 10rem; overflow: hidden; border: 1px solid var(--color-border-strong); border-radius: .3rem; background: #20242b; box-sizing: border-box; }
.kibitzer-bar__white { position: absolute; bottom: 0; width: 100%; background: #f2f2ee; transition: height .35s ease; }
.flipped .kibitzer-bar__white { bottom: auto; top: 0; }
.kibitzer-bar strong { position: absolute; left: 0; right: 0; top: 50%; transform: translateY(-50%); padding: .25rem 0; color: #fff; background: #343a43; font-size: .62rem; text-align: center; font-variant-numeric: tabular-nums; }
.unavailable { opacity: .6; }
</style>
