<script setup lang="ts">
import { computed, ref } from 'vue'
import type { HandBrainMatch, HandBrainTeam } from './types'
const props = defineProps<{ matches: HandBrainMatch[]; teams: HandBrainTeam[]; selected?: number | undefined }>()
const emit = defineEmits<{ select: [match: HandBrainMatch] }>()
const zoom = ref(1)
const positions: Record<string, [number, number]> = { P1:[12,225], P2:[12,45], Q1:[285,25], Q2:[285,115], Q3:[285,205], Q4:[285,295], S1:[558,70], S2:[558,250], F:[831,160] }
const team = (id: number | null | undefined) => props.teams.find(t => t.id === id)
const nextMatch = computed(() => props.matches.find(match => match.status === 'pending')?.id)
const nodes = computed(() => props.matches.map(match => ({ ...match, x: positions[match.key]?.[0] ?? 0, y: positions[match.key]?.[1] ?? 0 })))
const edges = computed(() => nodes.value.flatMap(node => node.sources.flatMap(source => {
  if (typeof source !== 'string') return []
  const parent = nodes.value.find(n => n.key === source)
  if (!parent) return []
  const x1 = parent.x + 220, y1 = parent.y + 36, x2 = node.x, y2 = node.y + 36
  return [{ key: `${source}-${node.key}`, path: `M${x1},${y1} H${x1 + 22} C${x1 + 30},${y1} ${x2 - 30},${y2} ${x2 - 22},${y2} H${x2}`, color: team(parent.winner_team_id)?.color }]
})))
</script>
<template>
  <section class="bracket-panel" aria-label="Interactive tournament bracket">
    <header><h2>Bracket</h2><div class="bracket-tools"><button aria-label="Zoom out bracket" :disabled="zoom <= .75" @click="zoom = Math.max(.75, zoom - .1)">−</button><button aria-label="Reset bracket zoom" @click="zoom = 1">{{ Math.round(zoom * 100) }}%</button><button aria-label="Zoom in bracket" :disabled="zoom >= 1.5" @click="zoom = Math.min(1.5, zoom + .1)">+</button></div></header>
    <div class="bracket-scroll" tabindex="0" aria-label="Scroll tournament bracket">
      <div class="bracket-extent" :style="{ width: `${1070 * zoom}px`, height: `${410 * zoom}px` }"><div class="bracket-canvas" :style="{ transform: `scale(${zoom})` }">
        <div class="round-label" v-for="(round, index) in ['Play-in', 'Quarterfinals', 'Semifinals', 'Final']" :key="round" :style="{ left: `${12 + index * 273}px` }"><span>0{{ index + 1 }}</span>{{ round }}</div>
        <svg class="bracket-wires" width="1070" height="375" aria-hidden="true"><path v-for="edge in edges" :key="edge.key" :d="edge.path" :stroke="edge.color ? 'var(--color-success)' : 'var(--color-border-strong)'" :class="{ advanced: edge.color }" /></svg>
        <button v-for="node in nodes" :key="node.id" class="match-node" :class="{ selected: node.id === selected, live: node.status === 'live', completed: node.winner_team_id }" :style="{ left: `${node.x}px`, top: `${node.y + 30}px` }" :aria-pressed="node.id === selected" :title="node.team_ids.map(id => team(id)?.name ?? 'Awaiting winner').join(' vs ')" @click="emit('select', node)">
          <span class="node-meta"><b>{{ node.key }}</b><span>{{ node.status === 'live' ? '● LIVE' : node.status === 'completed' ? 'FINAL' : node.id === nextMatch ? 'UP NEXT' : '' }}</span></span>
          <span v-for="(id, index) in node.team_ids" :key="index" class="node-team" :class="{ winner: id && node.winner_team_id === id, eliminated: node.winner_team_id && node.winner_team_id !== id }"><i :style="{ background: team(id)?.color ?? '#685468' }" /><small>{{ team(id)?.seed ?? '↳' }}</small><strong>{{ team(id)?.name ?? (typeof node.sources[index] === 'string' ? `Winner ${node.sources[index]}` : `Seed ${node.sources[index]}`) }}</strong><b>{{ node.quads.at(-1)?.score[index] ?? '' }}</b></span>
        </button>
      </div></div>
    </div>
  </section>
</template>
<style scoped>
.bracket-panel { overflow: hidden; border: 1px solid var(--color-border); border-radius: var(--radius-lg, .75rem); background: var(--color-surface); color: var(--color-text); }.bracket-panel header { display: flex; justify-content: space-between; align-items: center; gap: 1rem; padding: .75rem 1rem; border-bottom: 1px solid var(--color-border); }h2 { margin: 0; font-size: 1rem; }.bracket-tools { display: flex; align-items: center; gap: .3rem; }.bracket-tools button { min-width: 1.8rem; height: 1.8rem; padding: 0 .3rem; border: 1px solid var(--color-border); border-radius: var(--radius-sm); background: var(--color-surface); color: var(--color-text); font: inherit; font-size: .7rem; cursor: pointer; }.bracket-tools button:disabled { opacity: .4; }
.bracket-scroll { overflow: auto; padding: 1rem; scrollbar-color: var(--color-border-strong) transparent; scrollbar-width: thin; }.bracket-extent { margin-inline: auto; }.bracket-canvas { position: relative; width: 1070px; height: 410px; transform-origin: top left; }.round-label { position: absolute; top: 0; display: flex; align-items: center; gap: .6rem; color: var(--color-text-muted); font-size: .65rem; font-weight: 750; letter-spacing: .04em; text-transform: uppercase; }.round-label>span { color: var(--color-text-faint); font-size: .6rem; }.bracket-wires { position: absolute; top: 30px; left: 0; overflow: visible; }.bracket-wires path { fill: none; stroke-width: 1.2; opacity: .65; }.bracket-wires .advanced { opacity: 1; stroke-width: 1.8; }
.match-node { position: absolute; width: 220px; height: 76px; padding: .4rem .6rem; border: 1px solid var(--color-border); border-radius: var(--radius-md); background: var(--color-surface-raised, var(--color-surface)); color: var(--color-text); text-align: left; cursor: pointer; }.match-node:hover,.match-node:focus-visible { border-color: var(--color-border-strong); outline: 2px solid var(--color-focus); outline-offset: 2px; }.match-node.selected { border-color: var(--color-accent); box-shadow: 0 0 0 1px var(--color-accent); }.node-meta { display: flex; justify-content: space-between; margin-bottom: .25rem; color: var(--color-text-muted); font-size: .5rem; font-weight: 650; letter-spacing: .04em; }.live .node-meta>span { color: var(--color-success); }.node-team { display: flex; align-items: center; gap: .4rem; height: 21px; font-size: .72rem; }.node-team i { width: 3px; height: 12px; border-radius: 2px; }.node-team small { width: .75rem; color: var(--color-text-muted); font-size: .6rem; }.node-team strong { flex: 1; overflow: hidden; font-weight: 600; text-overflow: ellipsis; white-space: nowrap; }.node-team>b { font-size: .72rem; font-weight: 650; font-variant-numeric: tabular-nums; }.node-team.winner { color: var(--color-success); }.node-team.eliminated { opacity: .5; }
</style>
