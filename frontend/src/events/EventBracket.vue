<script lang="ts">
export interface BracketEntrant { id: number; name: string; seed?: number; color?: string }
export interface BracketRound { title: string; x: number; number?: string }
export interface BracketMatch {
  id: number;
  key: string;
  x: number;
  y: number;
  sources: (number | string)[];
  entrantIds: (number | null)[];
  status: string;
  winnerId: number | null;
  score?: (number | string)[];
  selectable?: boolean;
}
export interface BracketGroup {
  id: number;
  title: string;
  x: number;
  y: number;
  status: string;
  selectable: boolean;
  rows: { id: number; points: number; buchholz: number; seed: number | undefined; qualified: boolean }[];
}

const groupRowsTop = 36;
const rowHeight = 21;

export function bracketGroupHeight(rowCount: number): number {
  return groupRowsTop + rowCount * rowHeight + 8;
}
</script>

<script setup lang="ts">
import { computed, ref } from "vue";

const props = defineProps<{
  matches: BracketMatch[];
  entrants: BracketEntrant[];
  rounds: BracketRound[];
  width: number;
  height: number;
  selected?: number | undefined;
  floating?: boolean;
  frameless?: boolean;
  groups?: BracketGroup[];
  sourceLabels?: Record<string, string>;
}>();
const emit = defineEmits<{ select: [id: number, button: HTMLButtonElement]; cheer: [id: number] }>();
const zoom = ref(1);
const frameless = computed(() => props.floating || props.frameless);
const labelOffset = computed(() => frameless.value ? 20 : 30);
const entrantsById = computed(() => new Map(props.entrants.map(entrant => [entrant.id, entrant])));
const entrant = (id: number | null | undefined) => id == null ? undefined : entrantsById.value.get(id);
const nextMatch = computed(() => props.matches.find(match => match.status === "pending")?.id);
const nodes = computed(() => props.matches.map(match => ({ ...match, y: props.floating ? match.y * .94 : match.y })));
const byKey = computed(() => new Map(nodes.value.map(node => [node.key, node])));
const groupSources = computed(() => new Map((props.groups ?? []).flatMap(group =>
  group.rows.flatMap((row, index) => row.seed === undefined ? [] : [[row.seed, {
    x: group.x,
    y: group.y + groupRowsTop + (index + .5) * rowHeight,
    qualified: row.qualified,
  }]] as const),
)));
const edges = computed(() => nodes.value.flatMap(node => node.sources.flatMap((source, index) => {
  if (typeof source === "number") {
    const parent = groupSources.value.get(source);
    if (!parent) return [];
    return [{ key: source + "-" + node.key, path: wire(parent.x + 220, parent.y, node.x, node.y + 29 + index * rowHeight), advanced: parent.qualified }];
  }
  const parent = byKey.value.get(source);
  if (!parent) return [];
  return [{ key: parent.key + "-" + node.key, path: wire(parent.x + 220, parent.y + 36, node.x, node.y + 36), advanced: parent.winnerId !== null }];
})));

function wire(x: number, y: number, end: number, endY: number): string {
  return `M${x},${y} H${x + 22} C${x + 30},${y} ${end - 30},${endY} ${end - 22},${endY} H${end}`;
}

function sourceName(source: number | string | undefined): string {
  if (source === undefined) return "Awaiting qualifier";
  return props.sourceLabels?.[source] ?? (typeof source === "string" ? "Winner " + source : "Seed " + source);
}
</script>

<template>
  <section class="bracket-panel" :class="{ 'bracket-panel--frameless': frameless }" aria-label="Interactive tournament bracket">
    <header v-if="!frameless"><h2>Bracket</h2><div class="bracket-tools"><button aria-label="Zoom out bracket" :disabled="zoom <= .75" @click="zoom = Math.max(.75, zoom - .1)">−</button><button aria-label="Reset bracket zoom" @click="zoom = 1">{{ Math.round(zoom * 100) }}%</button><button aria-label="Zoom in bracket" :disabled="zoom >= 1.5" @click="zoom = Math.min(1.5, zoom + .1)">+</button></div></header>
    <div class="bracket-scroll" tabindex="0" aria-label="Scroll tournament bracket">
      <div class="bracket-extent" :style="{ width: width * zoom + 'px', height: height * zoom + 'px' }">
        <div class="bracket-canvas" :style="{ width: width + 'px', height: height + 'px', transform: 'scale(' + zoom + ')' }">
          <div class="round-label" v-for="round in rounds" :key="round.title" :style="{ left: round.x + 'px' }"><span v-if="round.number">{{ round.number }}</span>{{ round.title }}</div>
          <svg class="bracket-wires" :width="width" :height="height - labelOffset" :style="{ top: labelOffset + 'px' }" aria-hidden="true">
            <path v-for="edge in edges" :key="edge.key" :d="edge.path" :stroke="edge.advanced ? 'var(--bracket-advanced-wire, var(--color-success))' : 'var(--color-border-strong)'" :class="{ advanced: edge.advanced }" />
          </svg>
          <article v-for="group in groups" :key="group.id" class="match-node group-node"
            :class="{ selected: group.id === selected, live: group.status === 'live', 'match-node--static': !group.selectable }"
            :aria-label="group.title" :style="{ left: group.x + 'px', top: group.y + labelOffset + 'px', height: bracketGroupHeight(group.rows.length) + 'px' }">
            <button v-if="group.selectable" type="button" class="match-select" :aria-pressed="group.id === selected"
              :aria-label="'View ' + group.title + ' games'" @click="emit('select', group.id, $event.currentTarget as HTMLButtonElement)" />
            <span class="node-meta"><b>{{ group.title }}</b><span v-if="group.status === 'live'">● LIVE</span></span>
            <div class="group-columns"><span>Pts</span><abbr title="Buchholz: sum of opponents’ scores, counted once per completed game">BH</abbr></div>
            <ol>
              <li v-for="(row, index) in group.rows" :key="row.id" class="node-team"
                :class="{ 'group-advancing': row.seed !== undefined, 'group-qualified': row.qualified }">
                <i aria-hidden="true" />
                <small>{{ index + 1 }}</small>
                <strong :title="entrant(row.id)?.name">{{ entrant(row.id)?.name ?? 'Engine ' + row.id }}</strong>
                <b><span class="sr-only">Points </span>{{ row.points }}</b>
                <span><span class="sr-only">Buchholz </span>{{ row.buchholz }}</span>
              </li>
            </ol>
          </article>
          <div v-for="node in nodes" :key="node.id" class="match-node" :class="{ selected: node.id === selected, live: node.status === 'live', completed: node.winnerId, 'match-node--static': node.selectable === false }" :style="{ left: node.x + 'px', top: node.y + labelOffset + 'px' }">
            <button v-if="node.selectable !== false" type="button" class="match-select" :aria-pressed="node.id === selected" :aria-haspopup="floating ? 'dialog' : undefined"
              :aria-label="node.key + ' · ' + node.entrantIds.map((id, index) => entrant(id)?.name ?? sourceName(node.sources[index])).join(' vs ')"
              @click="emit('select', node.id, $event.currentTarget as HTMLButtonElement)" />
            <span class="node-meta"><b>{{ node.key }}</b><span>{{ node.status === 'live' ? '● LIVE' : node.status === 'completed' ? 'FINAL' : node.id === nextMatch ? 'UP NEXT' : '' }}</span></span>
            <span v-for="(id, index) in node.entrantIds" :key="index" class="node-team" :class="{ winner: id && node.winnerId === id, eliminated: node.winnerId && node.winnerId !== id }">
              <i :style="{ background: entrant(id)?.color || 'var(--color-border-strong)' }" />
              <small>{{ entrant(id)?.seed ?? '↳' }}</small>
              <strong>{{ entrant(id)?.name ?? sourceName(node.sources[index]) }}</strong>
              <b>{{ node.score?.[index] ?? '' }}</b>
              <button v-if="floating && id != null && entrant(id)" type="button" class="node-cheer" :style="{ '--team-color': entrant(id)?.color }"
                :data-cheer-team-id="id" :aria-label="'Cheer for ' + entrant(id)?.name" :title="'Cheer for ' + entrant(id)?.name" @click="emit('cheer', id)"><span aria-hidden="true">🎉</span></button>
            </span>
          </div>
        </div>
      </div>
    </div>
  </section>
</template>
<style scoped>
.bracket-panel { overflow: hidden; border: 1px solid var(--color-border); border-radius: var(--radius-lg, .75rem); background: var(--color-surface); color: var(--color-text); }.bracket-panel header { display: flex; justify-content: space-between; align-items: center; gap: 1rem; padding: .75rem 1rem; border-bottom: 1px solid var(--color-border); }h2 { margin: 0; font-size: 1rem; }.bracket-tools { display: flex; align-items: center; gap: .3rem; }.bracket-tools button { min-width: 1.8rem; height: 1.8rem; padding: 0 .3rem; border: 1px solid var(--color-border); border-radius: var(--radius-sm); background: var(--color-surface); color: var(--color-text); font: inherit; font-size: .7rem; cursor: pointer; }.bracket-tools button:disabled { opacity: .4; }
.bracket-scroll { overflow: auto; padding: 1rem; scrollbar-color: var(--color-border-strong) transparent; scrollbar-width: thin; }.bracket-extent { margin-inline: auto; }.bracket-canvas { position: relative; transform-origin: top left; }.round-label { position: absolute; top: 0; display: flex; align-items: center; gap: .6rem; color: var(--color-text-muted); font-size: .65rem; font-weight: 750; letter-spacing: .04em; text-transform: uppercase; }.round-label>span { color: var(--color-text-faint); font-size: .6rem; }.bracket-wires { position: absolute; top: 30px; left: 0; overflow: visible; }.bracket-wires path { fill: none; stroke-width: 1.2; opacity: .65; }.bracket-wires .advanced { opacity: 1; stroke-width: 1.8; }
.match-node { position: absolute; width: 220px; height: 76px; padding: .4rem .6rem; border: 1px solid var(--color-border); border-radius: var(--radius-md); background: var(--color-surface-raised, var(--color-surface)); color: var(--color-text); text-align: left; cursor: pointer; }.match-node:not(.match-node--static):hover,.match-node:has(.match-select:focus-visible) { border-color: var(--color-border-strong); outline: 2px solid var(--color-focus); outline-offset: 2px; }.match-node.selected { border-color: var(--color-accent); box-shadow: 0 0 0 1px var(--color-accent); }.node-meta { display: flex; justify-content: space-between; margin-bottom: .25rem; color: var(--color-text-muted); font-size: .5rem; font-weight: 650; letter-spacing: .04em; }.live .node-meta>span { color: var(--color-success); }.node-team { display: flex; align-items: center; gap: .4rem; height: 21px; font-size: .72rem; }.node-team i { width: 3px; height: 12px; border-radius: 2px; }.node-team small { width: .75rem; color: var(--color-text-muted); font-size: .6rem; }.node-team strong { flex: 1; overflow: hidden; font-weight: 600; text-overflow: ellipsis; white-space: nowrap; }.node-team>b { font-size: .72rem; font-weight: 650; font-variant-numeric: tabular-nums; }.node-team.winner { color: var(--color-success); }.node-team.eliminated { opacity: .5; }
.bracket-panel--frameless { border: 0; border-radius: 0; background: transparent; }
.bracket-panel--frameless .bracket-scroll { padding: 0; }
.group-node { padding: 7px 10px; }
.group-node .node-meta { align-items: center; gap: .5rem; margin: 0; height: 13px; text-transform: uppercase; }
.group-node .node-meta b { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.group-node .node-meta span { flex-shrink: 0; }
.group-columns { display: flex; justify-content: flex-end; gap: .4rem; align-items: center; height: 15px; color: var(--color-text-muted); font-size: .55rem; }
.group-columns, .group-node .node-team { gap: .32rem; }
.group-columns > *, .group-node .node-team > b, .group-node .node-team > span { flex: 0 0 1.4rem; text-align: right; font-variant-numeric: tabular-nums; }
.group-columns abbr { position: relative; z-index: 2; cursor: help; }
.group-node ol { margin: 0; padding: 0; list-style: none; }
.group-node .node-team i { background: var(--color-border-strong); }
.group-node .group-advancing i { background: var(--color-accent); }
.group-node .group-qualified strong { color: var(--color-success); }
.group-node .node-team > span { color: var(--color-text-muted); font-size: .65rem; }
.match-select { position: absolute; z-index: 1; inset: 0; padding: 0; border: 0; border-radius: inherit; background: transparent; cursor: pointer; }
.match-select:focus-visible { outline: none; }
.node-cheer { position: relative; z-index: 2; display: grid; place-items: center; flex: 0 0 1.5rem; height: 1.25rem; padding: 0; border: 1px solid transparent; border-radius: .25rem; background: transparent; color: var(--color-text); font-size: .75rem; line-height: 1; cursor: pointer; }
.node-cheer:hover, .node-cheer:focus-visible { border-color: var(--team-color); background: color-mix(in srgb, var(--team-color) 18%, transparent); }
.match-node--static { cursor: default; }
</style>
