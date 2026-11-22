<script setup lang="ts">
import { computed } from 'vue'

import TakStone from './TakStone.vue'
import { TAK_SIZE, parseTakMove, takRoads, takSquareName, takStoneKind, takStoneName, takStoneSide, type TakPosition } from './tak'

const props = defineProps<{
  position: TakPosition
  orientation: 'white' | 'black'
  lastMove: string
  coordinates: boolean
  compact: boolean
  showRoads: boolean
  roads: ReturnType<typeof takRoads>
  label: string
}>()

const squares = computed(() => {
  const result = Array.from({ length: TAK_SIZE ** 2 }, (_, index) => index)
  return props.orientation === 'white' ? result : result.reverse()
})
const lastTurn = computed(() => parseTakMove(props.lastMove))
const changed = computed(() => new Map(lastTurn.value?.path.map((square, index) => [square, index])))
const visibleStacks = computed(() => props.position.stacks.map((stack) => stack.slice(props.compact ? -3 : -5)))

function stackLabel(index: number): string {
  const stones = props.position.stacks[index] ?? []
  const names = stones.map((stone) => `${takStoneSide(stone)} ${takStoneName(takStoneKind(stone))}`)
  return `${takSquareName(index)} · ${names.length ? names.join(', ') : 'empty'}`
}
</script>

<template>
  <div class="tak-board" :class="{ 'tak-board--flipped': orientation === 'black' }" role="group" :aria-label="label">
    <div v-for="(index, displayIndex) in squares" :key="index" class="tak-square"
      :class="{
        'tak-square--dark': (index % TAK_SIZE + Math.floor(index / TAK_SIZE)) % 2,
        'tak-square--last': changed.has(index),
        'tak-square--origin': lastTurn?.direction && changed.get(index) === 0,
        'tak-square--winning': showRoads && roads.winningSquares.has(index),
      }"
      role="img" :aria-label="stackLabel(index)" :title="stackLabel(index)">
      <div v-if="showRoads && roads.links[index]?.length" class="tak-connections" aria-hidden="true">
        <span v-for="link in roads.links[index]" :key="link.name" class="tak-connection"
          :class="[`tak-connection--${link.name}`, `tak-connection--${link.side}`, { 'tak-connection--winning': link.onPath }]" />
      </div>
      <span v-if="coordinates && displayIndex % TAK_SIZE === 0" class="tak-rank" aria-hidden="true">{{ takSquareName(index)[1] }}</span>
      <span v-if="coordinates && displayIndex >= TAK_SIZE * (TAK_SIZE - 1)" class="tak-file" aria-hidden="true">{{ takSquareName(index)[0] }}</span>
      <div v-if="visibleStacks[index]?.length" class="tak-stack" aria-hidden="true">
        <TakStone v-for="(stone, level) in visibleStacks[index]" :key="level" class="tak-stack__stone"
          :side="takStoneSide(stone)" :kind="takStoneKind(stone)"
          :style="{ top: `${50 + ((visibleStacks[index]!.length - 1) / 2 - level) * 6}%` }" />
      </div>
      <span v-if="!compact && (position.stacks[index]?.length ?? 0) > 1" class="tak-count" aria-hidden="true">{{ position.stacks[index]?.length }}</span>
      <span v-if="!compact && lastTurn?.direction && (changed.get(index) ?? 0) > 0" class="tak-drop"
        :title="`${lastTurn.drops[changed.get(index)! - 1]} dropped`" aria-hidden="true">+{{ lastTurn.drops[changed.get(index)! - 1] }}</span>
    </div>
  </div>
</template>

<style scoped>
.tak-board { display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); grid-template-rows: repeat(6, minmax(0, 1fr)); aspect-ratio: 1; overflow: hidden; }
.tak-square {
  --tak-tile: var(--board-light, #f0d9b5);
  position: relative;
  isolation: isolate;
  background-color: var(--tak-tile);
  box-shadow: inset 0 0 0 1px #0001;
}
.tak-square--dark { --tak-tile: var(--board-dark, #b58863); }
.tak-square--winning { background-color: color-mix(in srgb, var(--tak-tile) 90%, var(--color-success)); }
.tak-square--last {
  --tak-last-highlight: color-mix(in srgb, var(--color-accent) 10%, transparent);
  background-image: linear-gradient(var(--tak-last-highlight), var(--tak-last-highlight));
}
.tak-square--last::after {
  position: absolute;
  inset: 0;
  z-index: 2;
  border: 1px solid color-mix(in srgb, var(--color-accent) 65%, var(--tak-tile));
  pointer-events: none;
  content: '';
}
.tak-square--origin::after { border-style: dashed; }
.tak-connections { position: absolute; inset: 0; pointer-events: none; }
.tak-board--flipped .tak-connections { transform: rotate(180deg); }
.tak-connection {
  --tak-road-color: #fff8ea;
  --tak-road-opacity: 24%;
  position: absolute;
  display: block;
  background: color-mix(in srgb, var(--tak-road-color) var(--tak-road-opacity), transparent);
}
.tak-connection--black { --tak-road-color: #151821; }
.tak-connection--north, .tak-connection--south { width: 14%; height: 52%; left: 43%; }
.tak-connection--north { top: 0; }
.tak-connection--south { bottom: 0; }
.tak-connection--east, .tak-connection--west { width: 52%; height: 14%; top: 43%; }
.tak-connection--east { right: 0; }
.tak-connection--west { left: 0; }
.tak-connection--winning { --tak-road-opacity: 40%; }
.tak-stack { position: absolute; inset: 0; --tak-stone-size: 54%; pointer-events: none; }
.tak-stack .tak-stack__stone { position: absolute; left: 50%; transform: translate(-50%, -50%); }
.tak-rank, .tak-file, .tak-count, .tak-drop {
  position: absolute;
  z-index: 1;
  font-family: var(--font-mono, ui-monospace, monospace);
  font-size: clamp(0.53rem, 2.2cqw, 0.78rem);
  font-weight: 700;
  line-height: 1.12;
}
.tak-rank { inset: 6% auto auto 7%; }
.tak-file { inset: auto 7% 5% auto; }
.tak-rank, .tak-file { color: #fff; -webkit-text-stroke: 2px #000a; paint-order: stroke fill; }
.tak-count, .tak-drop { padding: .15em .28em; border-radius: .22rem; background: var(--color-surface); color: var(--color-text); }
.tak-count { inset: 6% 6% auto auto; }
.tak-drop { inset: auto auto 5% 6%; background: var(--color-accent-soft); }
</style>
