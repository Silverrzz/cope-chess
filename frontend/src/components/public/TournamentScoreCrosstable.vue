<script setup lang="ts">
import { computed } from 'vue'
import type { HeadToHeadScore, StandingRecord } from './types'

const props = defineProps<{
  standings: StandingRecord[]
}>()

const rows = computed(() => props.standings.map((standing) => ({
  standing,
  cells: props.standings.map((opponent) => {
    const self = String(standing.engine_id) === String(opponent.engine_id)
    const score = standing.head_to_head?.[String(opponent.engine_id)]
    return {
      opponentId: opponent.engine_id,
      self,
      score,
      label: self ? `${standing.name}: same engine` : matchupLabel(standing.name, opponent.name, score),
    }
  }),
})))

function matchupLabel(name: string, opponent: string, score?: HeadToHeadScore): string {
  if (!score?.played) return `${name} versus ${opponent}: no completed games`
  return `${name} versus ${opponent}: ${score.points} points from ${score.played} games; ${score.wins} wins, ${score.draws} draws, ${score.losses} losses`
}
</script>

<template>
  <section class="score-crosstable" aria-labelledby="score-crosstable-title">
    <header>
      <h3 id="score-crosstable-title">Score crosstable</h3>
    </header>
    <div class="table-wrap" tabindex="0" role="region" aria-label="Head-to-head tournament scores">
      <table>
        <caption class="sr-only">Head-to-head scores in standings order, from the row engine's perspective</caption>
        <thead>
          <tr>
            <th scope="col" class="engine-heading">Engine</th>
            <th v-for="(standing, index) in standings" :key="standing.engine_id" scope="col" :title="standing.name">
              <span aria-hidden="true">{{ index + 1 }}</span>
              <span class="sr-only">{{ index + 1 }}. {{ standing.name }}</span>
            </th>
            <th scope="col">Points</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(row, index) in rows" :key="row.standing.engine_id">
            <th scope="row" class="engine-heading">
              <span class="engine-rank">{{ index + 1 }}</span>
              <RouterLink :to="`/engines/${row.standing.engine_id}`">{{ row.standing.name }}</RouterLink>
            </th>
            <td
              v-for="cell in row.cells"
              :key="cell.opponentId"
              :class="{ 'self-cell': cell.self }"
              :title="cell.label"
              :aria-label="cell.label"
            >
              <span v-if="cell.self" aria-hidden="true">×</span>
              <template v-else-if="cell.score?.played">{{ cell.score.points }}<span class="games-count"> / {{ cell.score.played }}</span></template>
              <span v-else aria-hidden="true">-</span>
            </td>
            <td class="total-cell">{{ row.standing.points }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>

<style scoped>
.score-crosstable {
  border-block-start: 1px solid var(--color-border, #d5dbe1);
}

header {
  padding: 1rem;
  border-block-end: 1px solid var(--color-border, #d5dbe1);
}

h3 {
  margin: 0;
  font-size: 1rem;
}

.table-wrap {
  overflow-x: auto;
}

table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.82rem;
  font-variant-numeric: tabular-nums;
}

th,
td {
  min-width: 4.5rem;
  padding: 0.72rem 0.85rem;
  border-block-end: 1px solid var(--color-border, #d5dbe1);
  text-align: center;
  white-space: nowrap;
}

thead th {
  color: var(--color-text-muted, #607080);
  font-size: 0.7rem;
}

tbody tr:last-child > * {
  border-block-end: 0;
}

.engine-heading {
  position: sticky;
  z-index: 1;
  inset-inline-start: 0;
  min-width: 12rem;
  background: var(--color-surface, #fff);
  text-align: start;
}

.engine-rank {
  display: inline-block;
  min-width: 2rem;
  color: var(--color-text-muted, #607080);
}

.engine-heading a {
  color: inherit;
  font-size: 0.82rem;
  text-decoration: none;
  text-transform: none;
  letter-spacing: normal;
}

.engine-heading a:hover {
  color: var(--color-accent, #2f78c4);
  text-decoration: underline;
}

.self-cell {
  background: color-mix(in srgb, var(--color-text, #17202a) 6%, var(--color-surface, #fff));
  color: var(--color-text-muted, #607080);
}

.games-count {
  color: var(--color-text-muted, #607080);
  font-size: 0.75rem;
}

.total-cell {
  font-weight: 730;
}
</style>
