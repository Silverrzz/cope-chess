<script setup lang="ts">
import type { BallotResult } from '@/quorum'

defineProps<{ result: BallotResult }>()

const motionColors = ['success', 'danger', 'text-muted']
const pollColors = ['accent', 'success', 'warning', 'danger', 'text-muted']

function voteColor(result: BallotResult, index: number) {
  const colors = result.outcome === 'report_only' ? pollColors : motionColors
  return 'var(--color-' + colors[index % colors.length] + ')'
}
</script>

<template>
  <div class="ballot-result">
    <div class="ballot-result__bar" aria-hidden="true">
      <span v-for="(count, index) in result.counts" v-show="count > 0" :key="index"
        :style="{ flexGrow: count, backgroundColor: voteColor(result, index) }" />
    </div>
    <dl class="ballot-result__counts">
      <div v-for="(option, index) in result.options" :key="index" :style="{ '--vote-color': voteColor(result, index) }">
        <dt>{{ option }}</dt>
        <dd>{{ result.counts[index] }}</dd>
      </div>
    </dl>
  </div>
</template>

<style scoped>
.ballot-result { display: grid; gap: var(--space-3); }
.ballot-result__bar {
  display: flex;
  overflow: hidden;
  height: .5rem;
  border-radius: var(--radius-round);
  background: var(--color-surface-sunken);
  box-shadow: inset 0 0 0 1px var(--color-border);
}
.ballot-result__bar span { flex-basis: 0; }
.ballot-result__counts {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(100%, 7rem), 1fr));
  gap: var(--space-2);
  margin: 0;
}
.ballot-result__counts > div {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: var(--space-2);
  padding: var(--space-2) var(--space-3);
  border-radius: var(--radius-sm);
  background: var(--color-surface-sunken);
}
.ballot-result__counts dt { font-size: .875rem; overflow-wrap: anywhere; }
.ballot-result__counts dt::before {
  display: inline-block;
  width: .5rem;
  height: .5rem;
  margin-inline-end: var(--space-2);
  border-radius: var(--radius-round);
  background: var(--vote-color);
  content: '';
}
.ballot-result__counts dd { margin: 0; font-weight: 700; font-variant-numeric: tabular-nums; }
</style>
