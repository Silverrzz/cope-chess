<script setup lang="ts">
import { computed } from 'vue'

import BaseButton from '@/components/ui/BaseButton.vue'
import { TAK_KOMI, takFlatCounts, takReserves, type TakPosition, type TakSide } from './tak'

const props = defineProps<{ position: TakPosition; roadWinner: TakSide | null; showRoads: boolean; finished: boolean }>()
const emit = defineEmits<{ 'update:showRoads': [value: boolean] }>()
const reserves = computed(() => takReserves(props.position))
const flats = computed(() => takFlatCounts(props.position))
const toMove = computed(() => props.finished || props.roadWinner ? null : props.position.turn === 'w' ? 'white' : 'black')
</script>

<template>
  <div class="tak-overview">
    <table class="tak-overview__stats" aria-label="Tak flat score and reserves">
      <colgroup>
        <col class="tak-overview__side-column">
        <col class="tak-overview__score-column">
        <col span="2">
      </colgroup>
      <thead>
        <tr>
          <th scope="col"><span class="sr-only">Side</span></th>
          <th scope="col">Flat score</th>
          <th scope="col" title="Stones in reserve">Stones</th>
          <th scope="col" title="Capstones in reserve">Caps</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="side in (['white', 'black'] as const)" :key="side" :class="{ 'tak-overview__turn': toMove === side }">
          <th scope="row">
            {{ side === 'white' ? 'White' : 'Black' }}
            <span class="tak-overview__winner" :class="{ 'tak-overview__winner--hidden': roadWinner !== side }">Road win</span>
            <span v-if="toMove === side" class="sr-only">to move</span>
          </th>
          <td>
            <strong class="tak-overview__score">{{ flats[side] + (side === 'black' ? TAK_KOMI : 0) }}</strong>
            <span v-if="side === 'black'" class="tak-overview__komi" :title="`Total includes +${TAK_KOMI} komi`">
              <span class="sr-only">including </span>+{{ TAK_KOMI }} komi
            </span>
          </td>
          <td>{{ reserves[side].flats }}</td>
          <td>{{ reserves[side].caps }}</td>
        </tr>
      </tbody>
    </table>
    <BaseButton class="tak-overview__roads" size="small" :aria-pressed="showRoads" title="Show connections between adjacent flats and capstones"
      @click="emit('update:showRoads', !showRoads)">
      Roads
    </BaseButton>
  </div>
</template>

<style scoped>
.tak-overview {
  container-type: inline-size;
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: center;
  column-gap: var(--space-2);
  margin-block-end: var(--space-2);
  padding-inline-end: var(--space-2);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface);
  color: var(--color-text);
  font-size: .8125rem;
  line-height: 1.25;
}
.tak-overview__stats {
  width: 100%;
  table-layout: fixed;
  border-collapse: collapse;
  font-variant-numeric: tabular-nums;
}
.tak-overview__side-column { width: 28%; }
.tak-overview__score-column { width: 34%; }
.tak-overview__stats th, .tak-overview__stats td { padding: var(--space-1) .375rem; text-align: start; }
.tak-overview__stats th:first-child { padding-inline-start: var(--space-2); }
.tak-overview__stats :is(th, td):nth-child(n+3) { text-align: center; }
.tak-overview__stats thead {
  border-bottom: 1px solid var(--color-border);
  color: var(--color-text-muted);
  font-size: .75rem;
  white-space: nowrap;
}
.tak-overview__stats th, .tak-overview__score { font-weight: 650; }
.tak-overview__stats td { white-space: nowrap; }
.tak-overview__score { display: inline-block; width: 2ch; text-align: end; }
.tak-overview__komi, .tak-overview__winner { margin-inline-start: var(--space-1); font-size: .75rem; white-space: nowrap; }
.tak-overview__komi { color: var(--color-text-muted); }
.tak-overview__winner { color: var(--color-success); }
.tak-overview__winner--hidden { visibility: hidden; }
.tak-overview__turn { background: color-mix(in srgb, var(--color-accent) 8%, transparent); }
.tak-overview__roads { padding-inline: var(--space-2); }
.tak-overview__roads[aria-pressed='true'] {
  border-color: color-mix(in srgb, var(--color-accent) 38%, var(--color-border));
  background: var(--color-accent-soft);
  color: var(--color-accent);
}
@container (max-width: 26rem) {
  .tak-overview__side-column, .tak-overview__score-column { width: 30%; }
  .tak-overview__stats th, .tak-overview__stats td { padding-inline: var(--space-1); vertical-align: top; }
  .tak-overview__score { text-align: start; }
  .tak-overview__komi, .tak-overview__winner { display: block; margin-inline-start: 0; }
  .tak-overview__roads { padding-inline: .375rem; }
}
</style>
