<script setup lang="ts">
import { computed, ref, watch, onBeforeUnmount } from 'vue'
import { parseFen } from '@/components/chess/chess'
import type { HandBrainTeam, HandBrainAnalysis } from './types'
import RoleIcon from './RoleIcon.vue'

const props = defineProps<{ team: HandBrainTeam | null; side: string; swapped: boolean; analyses: Record<string, HandBrainAnalysis>; clocks: Record<string, number>; activeEngine?: number | undefined; running?: boolean | undefined; clockOffsetMs?: number | undefined; fen: string }>()
const names: Record<string, string> = { p: 'Pawn', n: 'Knight', b: 'Bishop', r: 'Rook', q: 'Queen', k: 'King' }
const members = computed(() => {
  const roster = props.team?.members ?? []
  const ordered = props.swapped ? [...roster].reverse() : roster
  return ordered.map((member, index) => {
    const role = index === 0 ? 'brain' as const : 'hand' as const
    const analysis = props.analyses[String(member.engine_id)]
    const root = parseFen(analysis?.root_fen ?? props.fen)
    const cp = analysis?.eval_cp
    const mate = analysis?.eval_mate
    const factor = root.turn === 'b' ? -1 : 1
    const evaluation = mate !== undefined && mate !== null ? `#${mate * factor}` : cp !== undefined && cp !== null ? `${cp * factor >= 0 ? '+' : ''}${(cp * factor / 100).toFixed(2)}` : ''
    const clock = Math.max(0, props.clocks[String(member.engine_id)] ?? 40000)
    return { ...member, role, evaluation, analysis, clock: `${Math.floor(clock / 60000)}:${(clock % 60000 / 1000).toFixed(1).padStart(4, '0')}`, active: props.running && props.activeEngine === member.engine_id }
  })
})
const speech = ref<Record<string, string>>({})
const lastSubmission = new Map<string, string>()
const timers = new Map<string, number>()
watch(() => props.analyses, analyses => {
  for (const member of members.value) {
    const id = String(member.engine_id)
    const analysis = analyses[id]
    if (!analysis?.submission_id || !analysis.submitted_move || lastSubmission.get(id) === analysis.submission_id) continue
    lastSubmission.set(id, analysis.submission_id)
    const submitted = Date.parse(analysis.submitted_at ?? '')
    if (!Number.isFinite(submitted) || Math.abs(Date.now() + (props.clockOffsetMs ?? 0) - submitted) > 5000) continue
    const move = analysis.submitted_move
    const position = parseFen(analysis.root_fen ?? props.fen)
    const index = (8 - Number(move[1])) * 8 + 'abcdefgh'.indexOf(move[0] ?? '')
    const piece = position.squares[index]?.toLowerCase()
    const text = member.role === 'brain' ? (piece ? `${names[piece]}!` : '') : `${move.slice(2, 4)}${move.slice(4) ? `=${move.slice(4).toUpperCase()}` : ''}!`
    if (!text) continue
    window.clearTimeout(timers.get(id))
    speech.value = { ...speech.value, [id]: text }
    timers.set(id, window.setTimeout(() => { const next = { ...speech.value }; delete next[id]; speech.value = next }, 2800))
  }
})
onBeforeUnmount(() => { for (const timer of timers.values()) window.clearTimeout(timer) })
</script>
<template>
  <section class="team-card" :style="{ '--team': team?.color ?? 'var(--color-accent)' }" :aria-label="`${side} team ${team?.name ?? 'To be decided'}`">
    <header><span class="side-stone" :class="side.toLowerCase()" /><span class="team-heading"><small>{{ side }}</small><strong>{{ team?.name ?? 'TBD' }}</strong></span></header>
    <div class="duet">
      <svg class="splitter" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true"><path class="hand-field" d="M64 -4 45 42H56L36 104H105V-4Z" /><path class="bolt-shadow" d="M64 -4 45 42H56L36 104" /><path class="bolt-edge" d="M66 -4 47 40H58L38 104" /></svg>
      <RoleIcon class="watermark watermark--brain" role="brain" />
      <RoleIcon class="watermark watermark--hand" role="hand" />
      <div v-for="member in members" :key="member.id" class="performer" :class="[member.role, { active: member.active }]">
        <div class="identity"><span class="role-label"><RoleIcon :role="member.role" />{{ member.role }}</span><div class="engine-name"><strong>{{ member.name }}</strong><Transition name="speech"><div v-if="speech[String(member.engine_id)]" class="speech" role="status">{{ speech[String(member.engine_id)] }}</div></Transition></div></div>
        <div class="clock-row"><time :class="{ ticking: member.active }">{{ member.clock }}</time><span v-if="member.evaluation" class="eval">{{ member.evaluation }}</span></div>
        <div v-if="member.analysis?.depth != null || member.analysis?.nps != null" class="search-stats"><span v-if="member.analysis?.depth != null">Depth <b>{{ member.analysis.depth }}</b></span><span v-if="member.analysis?.nps != null"><b>{{ typeof member.analysis.nps === 'number' ? member.analysis.nps.toLocaleString('en-US') : member.analysis.nps }}</b> nps</span></div>
      </div>
      <div v-if="!members.length" class="empty-duet">TBD</div>
    </div>
  </section>
</template>
<style scoped>
.team-card { container-type: inline-size; position: relative; display: grid; grid-template-rows: auto minmax(0, 1fr); min-height: 0; height: 100%; overflow: hidden; border: 1px solid var(--color-border); border-inline-start: 4px solid #05070a; border-radius: var(--radius-md, .5rem); background: var(--color-surface-raised, var(--color-surface)); color: var(--color-text); }
.team-card:has(.side-stone.white) { border-inline-start-color: #f7f8fa; }
.team-card header { display: flex; align-items: center; gap: .6rem; padding: .65rem .85rem; border-bottom: 1px solid var(--color-border); }
.side-stone { width: .55rem; height: .55rem; border-radius: 50%; border: 1px solid var(--color-border-strong); }.side-stone.white { background: #f7f8fa; }.side-stone.black { background: #05070a; }
.team-heading { display: grid; gap: .1rem; min-width: 0; }.team-heading small { color: var(--color-text-muted); font-size: .62rem; font-weight: 750; letter-spacing: .045em; text-transform: uppercase; }.team-heading strong { color: var(--color-text); font-size: .9rem; font-weight: 750; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.duet { position: relative; min-height: 0; isolation: isolate; }.splitter { position: absolute; inset: 0; width: 100%; height: 100%; pointer-events: none; }.hand-field { fill: var(--color-surface); }.bolt-shadow { fill: none; stroke: var(--color-border-strong); stroke-width: 2; vector-effect: non-scaling-stroke; }.bolt-edge { fill: none; stroke: var(--color-border); stroke-width: 1; vector-effect: non-scaling-stroke; }
.performer { position: absolute; top: 0; left: 0; width: 63%; z-index: 1; padding: .85rem; box-sizing: border-box; }.performer.hand { top: auto; left: auto; bottom: 0; right: 0; text-align: right; }
.watermark { position: absolute; pointer-events: none; width: 5.5rem; height: 5.5rem; opacity: .055; color: var(--color-text); }.watermark--brain { left: 10%; top: 42%; transform: translateY(-50%); }.watermark--hand { right: 9%; bottom: 28%; }
.identity { position: relative; display: grid; justify-items: start; gap: .2rem; }.hand .identity { justify-items: end; }.engine-name { position: relative; width: max-content; max-width: 100%; }.identity strong { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--color-text); font-size: 1.05rem; font-weight: 750; line-height: 1.25; }.role-label { display: flex; align-items: center; gap: .3rem; color: var(--color-text-muted); font-size: .7rem; font-weight: 750; letter-spacing: .045em; text-transform: uppercase; }.role-label svg { width: .8rem; height: .8rem; }.hand .role-label { justify-content: flex-end; }
.speech { --bubble-bg: light-dark(#000, #fff); --bubble-text: light-dark(#fff, #000); position: absolute; z-index: 6; bottom: -.15rem; left: calc(100% + .9rem); width: max-content; max-width: 8rem; padding: .65rem .9rem; border-radius: .8rem .8rem .8rem .2rem; background: var(--bubble-bg); box-shadow: 0 4px 14px #0003; color: var(--bubble-text); font-size: 1rem; font-weight: 850; line-height: 1.15; pointer-events: none; }.speech:after { position: absolute; left: -.55rem; bottom: .3rem; width: .75rem; height: .65rem; background: var(--bubble-bg); clip-path: polygon(100% 0, 100% 100%, 0 75%); content: ''; }.hand .speech { right: calc(100% + .9rem); left: auto; border-radius: .8rem .8rem .2rem .8rem; }.hand .speech:after { left: auto; right: -.55rem; transform: scaleX(-1); }.speech-enter-active { transition: opacity .18s ease, transform .18s ease; }.speech-leave-active { transition: opacity .4s ease, transform .4s ease; }.speech-enter-from,.speech-leave-to { opacity: 0; transform: translateY(3px) scale(.94); }
.clock-row { margin-top: 1rem; display: flex; align-items: baseline; flex-wrap: wrap; gap: .45rem .9rem; }
.hand .clock-row { justify-content: flex-end; }
time { color: var(--color-text); font-size: clamp(1.9rem, 10cqw, 2.75rem); font-weight: 850; font-variant-numeric: tabular-nums; line-height: 1; letter-spacing: -.025em; }
.eval { color: var(--color-text); font-size: 1.25rem; font-weight: 700; font-variant-numeric: tabular-nums; line-height: 1.2; }
.search-stats { display: flex; align-items: baseline; flex-wrap: wrap; gap: .3rem 1rem; margin-top: .65rem; color: var(--color-text-muted); font-size: .85rem; line-height: 1.3; font-variant-numeric: tabular-nums; }.search-stats > span { white-space: nowrap; }
.hand .search-stats { justify-content: flex-end; }
.search-stats b { color: var(--color-text); font-weight: 650; }
.empty-duet { display: grid; place-items: center; height: 100%; color: var(--color-text-muted); font-size: .8rem; }
@container (max-width: 23rem) { .performer { width: 66%; padding: .7rem; }time { font-size: 1.9rem; }.eval { font-size: 1rem; }.clock-row { gap: .4rem .6rem; }.search-stats { gap: .25rem .65rem; font-size: .75rem; }.speech { padding: .55rem .7rem; font-size: .9rem; } }
@media(max-width: 40rem) { .duet { min-height: 17rem; } }
</style>
