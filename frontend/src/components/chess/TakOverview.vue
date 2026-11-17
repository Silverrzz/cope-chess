<script setup lang="ts">
import { computed } from 'vue'

import TakStone from './TakStone.vue'
import { TAK_KOMI, takFlatCounts, takReserves, type TakPosition, type TakSide } from './tak'

const props = defineProps<{ position: TakPosition; roadWinner: TakSide | null; showRoads: boolean; finished: boolean }>()
const emit = defineEmits<{ 'update:showRoads': [value: boolean] }>()
const reserves = computed(() => takReserves(props.position))
const flats = computed(() => takFlatCounts(props.position))
const toMove = computed(() => props.position.turn === 'w' ? 'white' : 'black')
</script>

<template>
  <div class="tak-overview" role="group" aria-label="Flat score and reserves">
    <div v-for="side in (['white', 'black'] as const)" :key="side" class="tak-seat"
      :class="{ 'tak-seat--active': !finished && !roadWinner && toMove === side, 'tak-seat--winner': roadWinner === side }">
      <div class="tak-seat__score" :title="`${flats[side]} visible flats${side === 'black' ? ` plus ${TAK_KOMI} komi` : ''}`">
        <TakStone class="tak-seat__marker" kind="flat" :side="side" />
        <span class="tak-seat__name">{{ side }}</span>
        <span class="tak-seat__total"><strong>{{ flats[side] + (side === 'black' ? TAK_KOMI : 0) }}</strong> flat score</span>
        <span v-if="side === 'black'" class="tak-seat__komi">+{{ TAK_KOMI }} komi included</span>
      </div>
      <div class="tak-seat__reserves" :aria-label="`${side} reserves: ${reserves[side].flats} stones and ${reserves[side].caps} capstones`">
        <span v-if="roadWinner === side" class="tak-seat__road">Road</span>
        <span class="tak-seat__pile" title="Stones in reserve" aria-hidden="true">
          <span class="tak-seat__stack">
            <TakStone v-for="layer in Math.min(4, reserves[side].flats)" :key="layer" kind="flat" :side="side" />
          </span>
          {{ reserves[side].flats }}
        </span>
        <span class="tak-seat__pile" title="Capstones in reserve" aria-hidden="true">
          <TakStone kind="cap" :side="side" :class="{ 'tak-seat__spent': !reserves[side].caps }" />
          {{ reserves[side].caps }}
        </span>
      </div>
    </div>
    <button class="tak-roads-toggle" type="button" :aria-pressed="showRoads" title="Show connections between adjacent flats and capstones"
      @click="emit('update:showRoads', !showRoads)">
      <span class="tak-roads-toggle__icon" aria-hidden="true"></span>
      Roads
    </button>
  </div>
</template>

<style scoped>
.tak-overview {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr) auto;
  gap: .4rem;
  margin-block-end: .5rem;
  align-items: stretch;
}
.tak-seat {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: .15rem .6rem;
  padding: .42rem .56rem;
  min-inline-size: 0;
  border: 1px solid var(--color-border, #d5dbe1);
  border-radius: .55rem;
  background: var(--color-surface, #fff);
  color: var(--color-text, #17202a);
}
.tak-seat--active { border-color: var(--color-primary, #3183c9); box-shadow: inset 0 -2px var(--color-primary, #3183c9); }
.tak-seat--winner { border-color: #739487; box-shadow: inset 0 -2px #739487; }
.tak-seat__score, .tak-seat__reserves, .tak-seat__pile { display: inline-flex; align-items: center; gap: .32rem; }
.tak-seat__score { flex-wrap: wrap; }
.tak-seat__score strong { font-size: 1.03rem; font-variant-numeric: tabular-nums; }
.tak-seat__total { font-size: .65rem; white-space: nowrap; }
.tak-seat__name { text-transform: capitalize; font-size: .76rem; font-weight: 700; }
.tak-seat__marker { --tak-stone-size: .65rem; }
.tak-seat__komi { color: var(--color-text-muted, #607080); font-size: .64rem; white-space: nowrap; }
.tak-seat__reserves { gap: .56rem; --tak-stone-size: .68rem; }
.tak-seat__pile { font: 650 .73rem/1 var(--font-mono, ui-monospace, monospace); }
.tak-seat__stack { display: inline-flex; flex-direction: column; min-width: var(--tak-stone-size); }
.tak-seat__stack > * + * { margin-top: -.55rem; }
.tak-seat__spent { opacity: .38; }
.tak-seat__road { color: var(--color-success, #297961); font-size: .7rem; font-weight: 750; }
.tak-roads-toggle {
  display: grid;
  justify-items: center;
  align-content: center;
  gap: .21rem;
  border: 1px solid var(--color-border, #d5dbe1);
  border-radius: .55rem;
  padding: .34rem .48rem;
  background: var(--color-surface, #fff);
  color: var(--color-text, #17202a);
  font: inherit;
  font-size: .67rem;
  font-weight: 650;
  cursor: pointer;
}
.tak-roads-toggle[aria-pressed='true'] { color: var(--color-accent, #2f78c4); }
.tak-roads-toggle:focus-visible { outline: 2px solid var(--color-accent, #2f78c4); outline-offset: 2px; }
.tak-roads-toggle__icon { width: 1.1rem; height: .55rem; border-bottom: 2px solid; border-left: 2px solid; border-radius: 0 0 0 .25rem; transform: skewX(-17deg); }
@media (max-width: 29rem) {
  .tak-seat { padding-inline: .35rem; }
  .tak-seat__komi { font-size: .59rem; }
  .tak-seat__reserves { gap: .38rem; }
}
</style>
