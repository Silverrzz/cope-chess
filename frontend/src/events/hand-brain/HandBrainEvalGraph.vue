<script setup lang="ts">
import { computed, ref, onMounted, onBeforeUnmount } from 'vue'
import type { HandBrainSearch, HandBrainTeam } from './types'
const props = defineProps<{ searches: HandBrainSearch[]; teams: HandBrainTeam[]; selectedPly: number; startPly: number; totalPlies: number }>()
const emit = defineEmits<{ seek: [ply: number] }>()
const colors = ['var(--color-info)', 'var(--color-warning)', 'var(--color-success)', 'var(--color-danger)']
const hidden = ref<number[]>([])
const hover = ref<number | null>(null)
const chart = ref<SVGElement | null>(null)
const size = ref({ width: 500, height: 142 })
let observer: ResizeObserver | undefined
onMounted(() => {
  observer = new ResizeObserver(([entry]) => {
    if (entry) size.value = { width: Math.max(150, entry.contentRect.width), height: Math.max(70, entry.contentRect.height) }
  })
  if (chart.value) observer.observe(chart.value)
})
onBeforeUnmount(() => observer?.disconnect())
const end = computed(() => Math.max(props.startPly + props.totalPlies, props.startPly + 2, ...props.searches.map(s => s.ply)))
const right = computed(() => size.value.width - 12)
const bottom = computed(() => size.value.height - 24)
const x = (ply: number) => 38 + (ply - props.startPly) / Math.max(1, end.value - props.startPly) * (right.value - 38)
const value = (s: HandBrainSearch) => Number(s.analysis.eval_cp ?? 0) * (s.side === 'white' ? 1 : -1)
const scale = computed(() => {
  const values = props.searches.filter(s => !hidden.value.includes(s.engine_id) && (s.analysis.eval_mate == null && s.analysis.eval_cp != null)).map(value).filter(Number.isFinite)
  const min = values.length ? Math.min(...values) : -50
  const max = values.length ? Math.max(...values) : 50
  const span = Math.max(50, max - min)
  const center = (min + max) / 2
  const low = center - span * .62
  const high = center + span * .62
  const roughStep = (high - low) / 4
  const magnitude = 10 ** Math.floor(Math.log10(roughStep))
  const step = ([1, 2, 2.5, 5, 10].find(n => n * magnitude >= roughStep) ?? 10) * magnitude
  const ticks = []
  for (let cp = Math.ceil(low / step) * step; cp <= high; cp += step) ticks.push(cp)
  return { low, high, ticks }
})
const y = (cp: number) => 12 + (scale.value.high - cp) / (scale.value.high - scale.value.low) * (bottom.value - 12)
const sampleY = (s: HandBrainSearch) => s.analysis.eval_mate != null
  ? ((s.analysis.eval_mate > 0 ? 1 : -1) * (s.side === 'white' ? 1 : -1) > 0 ? 12 : bottom.value)
  : y(value(s))
const series = computed(() => props.teams.flatMap(team => team.members).map((member, index) => {
  const samples = props.searches.filter(s => s.engine_id === member.engine_id && (s.analysis.eval_cp != null || s.analysis.eval_mate != null)).sort((a,b) => a.ply-b.ply)
  const latest = [...samples].reverse().find(s => s.ply <= (hover.value ?? end.value))
  const cp = latest ? value(latest) : null
  return { ...member, samples, color: colors[index % colors.length], path: samples.map((s,i) => `${i ? 'L' : 'M'}${x(s.ply).toFixed(2)},${sampleY(s).toFixed(2)}`).join(' '), label: cp === null ? '' : latest?.analysis.eval_mate != null ? `#${latest.analysis.eval_mate * (latest.side === 'white' ? 1 : -1)}` : `${cp >= 0 ? '+' : ''}${(cp / 100).toFixed(2)}` }
}))
function pointer(event: MouseEvent) {
  const bounds = (event.currentTarget as SVGElement).getBoundingClientRect()
  hover.value = Math.round(props.startPly + Math.max(0, Math.min(1, (event.clientX - bounds.left - 38) / (right.value - 38))) * (end.value - props.startPly))
}
function toggle(id: number | null) { if (id === null) return; hidden.value = hidden.value.includes(id) ? hidden.value.filter(v => v !== id) : [...hidden.value, id] }
</script>
<template>
  <section class="eval-panel" aria-label="Four-engine evaluation graph">
    <header><h2>Evaluation</h2><span>White / pawns</span></header>
    <svg ref="chart" :viewBox="`0 0 ${size.width} ${size.height}`" preserveAspectRatio="none" class="eval-chart" role="img" aria-label="Evaluation history for both brains and both hands; click to replay a position" @mousemove="pointer" @mouseleave="hover = null" @click="emit('seek', Math.max(0, Math.min(totalPlies, (hover ?? end) - startPly)))">
      <defs><linearGradient id="hb-eval-fill" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="var(--color-accent)" stop-opacity=".045" /><stop offset="1" stop-color="var(--color-accent)" stop-opacity="0" /></linearGradient></defs>
      <rect x="38" y="12" :width="right - 38" :height="bottom - 12" rx="5" fill="url(#hb-eval-fill)" />
      <g v-for="cp in scale.ticks" :key="cp"><line x1="38" :x2="right" :y1="y(cp)" :y2="y(cp)" :class="{ zero: cp === 0 }" /><text x="30" :y="y(cp)+3" text-anchor="end">{{ cp / 100 }}</text></g>
      <path v-for="line in series.filter(s => !hidden.includes(s.engine_id ?? -1))" :key="line.id" :d="line.path" :stroke="line.color" class="eval-line"><title>{{ line.name }}: {{ line.label }}</title></path>
      <line v-if="totalPlies" :x1="x(startPly + selectedPly)" :x2="x(startPly + selectedPly)" y1="12" :y2="bottom" class="replay-line" />
      <line v-if="hover !== null" :x1="x(hover)" :x2="x(hover)" y1="12" :y2="bottom" class="hover-line" />
      <text x="38" :y="size.height - 5">{{ Math.floor(startPly / 2) + 1 }}</text><text :x="right" :y="size.height - 5" text-anchor="end">Move {{ Math.floor(end / 2) + 1 }}</text>
      <text v-if="!searches.length" :x="(38 + right) / 2" :y="(12 + bottom) / 2" text-anchor="middle" class="no-data">No evaluations yet</text>
    </svg>
    <div class="eval-legend"><button v-for="line in series" :key="line.id" :aria-pressed="!hidden.includes(line.engine_id ?? -1)" :class="{ muted: hidden.includes(line.engine_id ?? -1) }" @click="toggle(line.engine_id)"><i :style="{ background: line.color }" /><span>{{ line.name }}</span><strong>{{ line.label }}</strong></button></div>
  </section>
</template>
<style scoped>
.eval-panel { height: 100%; box-sizing: border-box; display: grid; grid-template-rows: auto minmax(0, 1fr) auto; gap: .3rem; min-height: 0; padding: .75rem .85rem; overflow: hidden; border: 1px solid var(--color-border); border-radius: var(--radius-md); background: var(--color-surface); color: var(--color-text); }.eval-panel header { display: flex; justify-content: space-between; align-items: baseline; gap: .5rem; }.eval-panel h2 { margin: 0; font-size: .85rem; font-weight: 750; }.eval-panel header span { color: var(--color-text-muted); font-size: .6rem; }.eval-chart { display: block; width: 100%; height: 100%; min-height: 0; margin-top: .25rem; overflow: visible; cursor: crosshair; }.eval-chart line { stroke: var(--color-border); stroke-width: 1; opacity: .5; }.eval-chart line.zero { stroke: var(--color-border-strong); opacity: .8; }.eval-chart text { fill: var(--color-text-muted); font-size: 11px; font-family: inherit; }.eval-chart text.no-data { font-size: 12px; }.eval-line { fill: none; stroke-width: 1.7; stroke-linejoin: round; stroke-linecap: round; vector-effect: non-scaling-stroke; }.eval-chart .replay-line { stroke: var(--color-text-faint); stroke-dasharray: 3 4; opacity: .8; }.eval-chart .hover-line { stroke: var(--color-text); opacity: .8; }.eval-legend { display: grid; grid-template-columns: 1fr 1fr; gap: .35rem .75rem; margin-top: .4rem; }.eval-legend button { display: flex; align-items: center; gap: .35rem; min-width: 0; padding: 0; border: 0; background: transparent; color: var(--color-text-muted); font: inherit; font-size: .65rem; text-align: left; cursor: pointer; }.eval-legend i { flex-shrink: 0; width: 5px; height: 5px; border-radius: 50%; }.eval-legend span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }.eval-legend strong { margin-left: auto; color: var(--color-text); font-size: .65rem; font-weight: 650; font-variant-numeric: tabular-nums; }.eval-legend .muted { opacity: .35; }
</style>
