<script setup lang="ts">
import { computed, ref, onMounted, onBeforeUnmount } from 'vue'
import type { EngineAnalysis } from '@/components/public/types'
interface Sample { ply: number; engine_id: number; side: 'white' | 'black'; analysis: EngineAnalysis }
interface Team { members: { id: number; engine_id: number | null; name: string; color?: string }[] }
const props = defineProps<{ orientation: 'white' | 'black'; kibitzer?: Sample[]; searches: Sample[]; teams: Team[]; selectedPly: number; startPly: number; totalPlies: number }>()
const emit = defineEmits<{ seek: [ply: number] }>()
const colors = ['#fff', '#000']
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
const samples = computed(() => [...props.searches, ...(props.kibitzer ?? [])])
const members = computed(() => [...props.teams.flatMap(team => team.members), ...(props.kibitzer?.length ? [{ id: 2, engine_id: 2, name: 'Stockfish Lite', color: '#ef4444' }] : [])])
const left = 38
const label = (cp: number) => cp === 0 ? '0' : `${cp > 0 ? '+' : ''}${Number((cp / 100).toFixed(2))}`
const end = computed(() => Math.max(props.startPly + props.totalPlies, props.startPly + 2, ...samples.value.map(s => s.ply)))
const right = computed(() => size.value.width - 6)
const bottom = computed(() => size.value.height - 6)
const x = (ply: number) => left + (ply - props.startPly) / Math.max(1, end.value - props.startPly) * (right.value - left)
const direction = (s: Sample) => s.side === props.orientation ? 1 : -1
const value = (s: Sample) => Number(s.analysis.eval_cp ?? 0) * direction(s)
const scale = computed(() => {
  const values = samples.value.filter(s => (s.analysis.eval_mate == null && s.analysis.eval_cp != null)).map(value).filter(Number.isFinite)
  const min = values.length ? Math.min(0, ...values) : -50
  const max = values.length ? Math.max(0, ...values) : 50
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
const y = (cp: number) => 6 + (scale.value.high - cp) / (scale.value.high - scale.value.low) * (bottom.value - 6)
const sampleY = (s: Sample) => s.analysis.eval_mate != null
  ? ((s.analysis.eval_mate > 0 ? 1 : -1) * direction(s) > 0 ? 6 : bottom.value)
  : y(value(s))
const series = computed(() => members.value.map((member, index) => {
  const points = samples.value.filter(s => s.engine_id === member.engine_id && (s.analysis.eval_cp != null || s.analysis.eval_mate != null)).sort((a,b) => a.ply-b.ply)
  const path = points.map((s,i) => `${i ? 'L' : 'M'}${x(s.ply).toFixed(2)},${sampleY(s).toFixed(2)}`).join(' ')
  const first = points[0]
  const last = points.at(-1)
  const focused = [...points].reverse().find(s => s.ply <= (hover.value ?? props.startPly + props.selectedPly))
  return { ...member, color: member.color || colors[index % colors.length], path,
    area: first && last ? `${path} L${x(last.ply)},${y(0)} L${x(first.ply)},${y(0)} Z` : '',
    point: focused ? { x: x(focused.ply), y: sampleY(focused) } : null,
  }
}))
function pointer(event: MouseEvent) {
  const bounds = (event.currentTarget as SVGElement).getBoundingClientRect()
  hover.value = Math.round(props.startPly + Math.max(0, Math.min(1, (event.clientX - bounds.left - left) / (right.value - left))) * (end.value - props.startPly))
}

</script>
<template>
  <section class="eval-panel" aria-label="Engine evaluation graph">
    <svg ref="chart" :viewBox="`0 0 ${size.width} ${size.height}`" preserveAspectRatio="none" class="eval-chart" role="img" aria-label="Engine evaluation history; click to replay a position" @mousemove="pointer" @mouseleave="hover = null" @click="emit('seek', Math.max(0, Math.min(totalPlies, (hover ?? end) - startPly)))">
      <line v-for="cp in scale.ticks.filter(cp => cp !== 0)" :key="cp" :x1="left" :x2="right" :y1="y(cp)" :y2="y(cp)" />
      <line :x1="left" :x2="right" :y1="y(0)" :y2="y(0)" class="zero" />
      <text :x="left - 5" y="10" text-anchor="end">{{ label(scale.high) }}</text>
      <text :x="left - 5" :y="y(0) + 3" text-anchor="end" class="zero-label">0</text>
      <text :x="left - 5" :y="bottom + 3" text-anchor="end">{{ label(scale.low) }}</text>
      <path v-for="line in series" :key="`fill-${line.id}`" :d="line.area" :fill="line.color" class="eval-area" />
      <path v-for="line in series" :key="`outline-${line.id}`" :d="line.path" class="eval-outline" />
      <path v-for="line in series" :key="line.id" :d="line.path" :stroke="line.color" class="eval-line"><title>{{ line.name }}</title></path>
      <circle v-for="line in series.filter(item => item.point)" :key="`point-${line.id}`" :cx="line.point!.x" :cy="line.point!.y" r="2.8" :fill="line.color" class="eval-point" />
      <line v-if="totalPlies" :x1="x(startPly + selectedPly)" :x2="x(startPly + selectedPly)" y1="6" :y2="bottom" class="replay-line" />
      <line v-if="hover !== null" :x1="x(hover)" :x2="x(hover)" y1="6" :y2="bottom" class="hover-line" />
    </svg>
  </section>
</template>
<style scoped>
.eval-panel { height: 100%; box-sizing: border-box; min-height: 0; padding: .15rem; overflow: hidden; border: 1px solid var(--color-border); border-radius: var(--radius-md); background: color-mix(in srgb, var(--color-surface) 94%, var(--color-text) 6%); }
.eval-chart { display: block; width: 100%; height: 100%; min-height: 0; cursor: crosshair; }
.eval-chart text { fill: var(--color-text-muted); font-size: 9px; font-variant-numeric: tabular-nums; }
.eval-chart .zero-label { fill: var(--color-text); }
.eval-chart line { stroke: var(--color-border); stroke-width: 1; opacity: .35; }
.eval-chart line.zero { stroke: var(--color-border-strong); opacity: .6; }
.eval-area { opacity: .065; }
.eval-outline { fill: none; stroke: #8b929c; stroke-opacity: .8; stroke-width: 3.6; stroke-linejoin: round; stroke-linecap: round; vector-effect: non-scaling-stroke; }
.eval-point { stroke: #8b929c; stroke-width: 1; }
.eval-line { fill: none; stroke-width: 2; stroke-linejoin: round; stroke-linecap: round; vector-effect: non-scaling-stroke; }
.eval-chart .replay-line { stroke: var(--color-text-faint); stroke-dasharray: 3 4; opacity: .7; }
.eval-chart .hover-line { stroke: var(--color-text); opacity: .7; }
</style>
