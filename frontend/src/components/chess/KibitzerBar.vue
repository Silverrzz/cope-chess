<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import type { KibitzerEvaluation } from './kibitzer'
const props = defineProps<{ gameId: string; ply: number; fen: string; orientation: 'white' | 'black'; chess960?: boolean; paused?: boolean }>()
const emit = defineEmits<{ evaluation: [sample: KibitzerEvaluation] }>()
type SearchJob = { id: number; gameId: string; ply: number; fen: string; chess960: boolean }
const score = ref<{ kind: 'cp' | 'mate'; value: number; depth: number; side: number } | null>(null)
const status = ref('Loading Stockfish Lite')
const visible = ref(!document.hidden)
let worker: Worker | undefined
let id = 0
let ready = false
let searching = false
let searchId = 0
let searchSide = 1
let pending: SearchJob | undefined
let searchJob: SearchJob | undefined
let timer: number | undefined
const whiteScore = computed(() => score.value ? score.value.value * score.value.side : 0)
const whitePercent = computed(() => score.value?.kind === 'mate' ? ((score.value.value > 0 ? 1 : -1) * score.value.side > 0 ? 100 : 0) : 100 / (1 + Math.exp(-whiteScore.value / 240)))
const bottomPercent = computed(() => props.orientation === 'white' ? whitePercent.value : 100 - whitePercent.value)
const displayed = computed(() => {
  if (!score.value) return '…'
  const n = whiteScore.value * (props.orientation === 'white' ? 1 : -1)
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
  searchJob = job
  searchId = job.id
  searchSide = job.fen.split(/\s+/)[1] === 'b' ? -1 : 1
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
        score.value = { kind: match[1] as 'cp' | 'mate', value: Number(match[2]), depth: Number(/depth (\d+)/.exec(data)?.[1] || 0), side: searchSide }
        if (searchJob) emit('evaluation', { gameId: searchJob.gameId, ply: searchJob.ply, fen: searchJob.fen, kind: score.value.kind, value: score.value.value, depth: score.value.depth, side: searchSide === 1 ? 'white' : 'black' })
        status.value = 'Analyzing'
      }
    }
  }
  worker.onerror = () => { status.value = 'Analysis unavailable'; stop() }
  worker.postMessage('uci')
}
function analyze() {
  window.clearTimeout(timer)
  const current = ++id
  if (!visible.value || props.paused) { stop(); status.value = 'Paused'; return }
  status.value = 'Loading Stockfish Lite'
  timer = window.setTimeout(() => {
    pending = { id: current, gameId: props.gameId, ply: props.ply, fen: props.fen === 'startpos' ? 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1' : props.fen, chess960: props.chess960 ?? false }
    if (!worker) createWorker()
    if (searching) worker?.postMessage('stop')
    else startSearch()
  }, 180)
}
function visibility() { visible.value = !document.hidden }
document.addEventListener('visibilitychange', visibility)
watch(() => [props.gameId, props.ply, props.fen, props.paused, visible.value], analyze, { immediate: true })
onBeforeUnmount(() => { stop(); document.removeEventListener('visibilitychange', visibility) })
</script>
<template>
  <div class="kibitzer-bar" :title="title" role="meter" :aria-label="`Stockfish Lite evaluation, ${orientation} advantage`" :aria-valuenow="Math.round(bottomPercent)" aria-valuemin="0" aria-valuemax="100" :aria-valuetext="score ? title : status" :class="{ flipped: orientation === 'black', unavailable: !score }">
    <div class="kibitzer-bar__white" :style="{ height: `${whitePercent}%` }" />
  </div>
</template>
<style scoped>
.kibitzer-bar { position: relative; width: 1.1rem; height: var(--arena-board-size); max-height: 100%; min-height: 10rem; overflow: hidden; border: 1px solid var(--color-border-strong); border-radius: .3rem 0 0 .3rem; background: #20242b; box-sizing: border-box; }
.kibitzer-bar__white { position: absolute; bottom: 0; width: 100%; background: #f2f2ee; transition: height .35s ease; }
.flipped .kibitzer-bar__white { bottom: auto; top: 0; }
.unavailable { opacity: .6; }
</style>
