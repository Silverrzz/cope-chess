<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useViewerSettings } from '@/composables/useViewerSettings'
const props = defineProps<{ fen: string; orientation: 'white' | 'black'; chess960?: boolean; paused?: boolean }>()
const { evaluationPerspective } = useViewerSettings()
const score = ref<{ kind: 'cp' | 'mate'; value: number; depth: number } | null>(null)
const status = ref('Loading Stockfish Lite')
const visible = ref(!document.hidden)
let worker: Worker | undefined
let id = 0
let ready = false
let searching = false
let searchId = 0
let pending: { id: number; fen: string; chess960: boolean } | undefined
let timer: number | undefined
const side = computed(() => props.fen.split(/\s+/)[1] === 'b' ? -1 : 1)
const whiteScore = computed(() => score.value ? score.value.value * side.value : 0)
const whitePercent = computed(() => score.value?.kind === 'mate' ? ((score.value.value > 0 ? 1 : -1) * side.value > 0 ? 100 : 0) : 100 / (1 + Math.exp(-whiteScore.value / 240)))
const displayed = computed(() => {
  if (!score.value) return '…'
  const n = evaluationPerspective.value === 'white' ? whiteScore.value : score.value.value
  return score.value.kind === 'mate' ? `#${n}` : `${n >= 0 ? '+' : ''}${(n / 100).toFixed(1)}`
})
const title = computed(() => `Stockfish 19 Lite · ${score.value ? `${displayed.value} · depth ${score.value.depth}` : status.value}`)
function stop() {
  window.clearTimeout(timer)
  worker?.terminate()
  worker = undefined
  ready = false
  searching = false
  pending = undefined
}
function startSearch() {
  if (!worker || !ready || searching || !pending) return
  const job = pending
  pending = undefined
  searchId = job.id
  searching = true
  worker.postMessage(`setoption name UCI_Chess960 value ${job.chess960}`)
  worker.postMessage(`position fen ${job.fen}`)
  worker.postMessage('go movetime 1500')
}
function createWorker() {
  worker = new Worker('/assets/engines/stockfish-19.0.1-lite/stockfish-19-lite-single.js', { name: 'cope-kibitzer' })
  worker.onmessage = ({ data }) => {
    if (typeof data !== 'string') return
    if (data === 'uciok') {
      worker?.postMessage('setoption name Hash value 16')
      worker?.postMessage('isready')
    } else if (data === 'readyok') {
      ready = true
      startSearch()
    } else if (data.startsWith('bestmove ')) {
      searching = false
      if (pending) startSearch()
    } else if (searchId === id) {
      const match = /score (cp|mate) (-?\d+)/.exec(data)
      if (match) {
        score.value = { kind: match[1] as 'cp' | 'mate', value: Number(match[2]), depth: Number(/depth (\d+)/.exec(data)?.[1] || 0) }
        status.value = 'Analyzing'
      }
    }
  }
  worker.onerror = () => { status.value = 'Analysis unavailable'; stop() }
  worker.postMessage('uci')
}
function analyze() {
  window.clearTimeout(timer)
  score.value = null
  const current = ++id
  if (!visible.value || props.paused) { stop(); status.value = 'Paused'; return }
  status.value = 'Loading Stockfish Lite'
  timer = window.setTimeout(() => {
    pending = { id: current, fen: props.fen === 'startpos' ? 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1' : props.fen, chess960: props.chess960 ?? false }
    if (!worker) createWorker()
    if (searching) worker?.postMessage('stop')
    else startSearch()
  }, 180)
}
function visibility() { visible.value = !document.hidden }
document.addEventListener('visibilitychange', visibility)
watch(() => [props.fen, props.paused, visible.value], analyze, { immediate: true })
onBeforeUnmount(() => { stop(); document.removeEventListener('visibilitychange', visibility) })
</script>
<template>
  <div class="kibitzer-bar" :title="title" role="meter" aria-label="Stockfish Lite evaluation, White advantage" :aria-valuenow="Math.round(whitePercent)" aria-valuemin="0" aria-valuemax="100" :aria-valuetext="score ? title : status" :class="{ flipped: orientation === 'black', unavailable: !score }">
    <div class="kibitzer-bar__white" :style="{ height: `${whitePercent}%` }" />
    <strong>{{ displayed }}</strong>
  </div>
</template>
<style scoped>
.kibitzer-bar { position: relative; width: 1.1rem; height: var(--arena-board-size); max-height: 100%; min-height: 10rem; overflow: hidden; border: 1px solid var(--color-border-strong); border-radius: .3rem 0 0 .3rem; background: #20242b; box-sizing: border-box; }
.kibitzer-bar__white { position: absolute; bottom: 0; width: 100%; background: #f2f2ee; transition: height .35s ease; }
.flipped .kibitzer-bar__white { bottom: auto; top: 0; }
.kibitzer-bar strong { position: absolute; left: 0; right: 0; top: 50%; transform: translateY(-50%); padding: .25rem 0; color: #fff; background: #343a43; font-size: .6rem; writing-mode: vertical-rl; text-align: center; font-variant-numeric: tabular-nums; }
.unavailable { opacity: .6; }
</style>
