<script setup lang="ts">
import { computed } from 'vue'

import TakStone from './TakStone.vue'
import { TAK_SIZE, parseTakMove, takRoads, takStoneKind, takStoneName, takStoneSide, type TakPosition } from './tak'

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

function squareName(index: number): string {
  return `${'abcdef'[index % TAK_SIZE]}${TAK_SIZE - Math.floor(index / TAK_SIZE)}`
}

function stackLabel(index: number): string {
  const stones = props.position.stacks[index] ?? []
  const names = stones.map((stone) => `${takStoneSide(stone)} ${takStoneName(takStoneKind(stone))}`)
  return `${squareName(index)} · ${names.length ? names.join(', ') : 'empty'}`
}
</script>

<template>
  <div class="tak-board" :class="{ 'tak-board--flipped': orientation === 'black', 'tak-board--compact': compact }" role="group" :aria-label="label">
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
      <span v-if="coordinates && displayIndex % TAK_SIZE === 0" class="tak-rank" aria-hidden="true">{{ squareName(index)[1] }}</span>
      <span v-if="coordinates && displayIndex >= TAK_SIZE * (TAK_SIZE - 1)" class="tak-file" aria-hidden="true">{{ squareName(index)[0] }}</span>
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
  --tak-tile: #476071;
  position: relative;
  isolation: isolate;
  background: radial-gradient(ellipse at 22% 16%, #e5f4fc0d, transparent 78%), var(--tak-tile);
  color: #e3eff4;
  box-shadow: inset 0 0 0 1px #2336454d;
}
.tak-square--dark { --tak-tile: #3b5263; }
.tak-square--last { --tak-tile: #416f91; }
.tak-square--last::after { position: absolute; inset: 0; z-index: 2; border: 1px solid #98d4f7b3; pointer-events: none; content: ''; }
.tak-square--origin::after { border-style: dashed; }
.tak-square--winning { --tak-tile: #386f68; }
.tak-connections { position: absolute; inset: 0; pointer-events: none; }
.tak-board--flipped .tak-connections { transform: rotate(180deg); }
.tak-connection { --connection: #f4dfad53; position: absolute; display: block; background: var(--connection); }
.tak-connection--black { --connection: #121c32a6; }
.tak-connection--north, .tak-connection--south { width: 20%; height: 52%; left: 40%; }
.tak-connection--north { top: 0; }
.tak-connection--south { bottom: 0; }
.tak-connection--east, .tak-connection--west { width: 52%; height: 20%; top: 40%; }
.tak-connection--east { right: 0; }
.tak-connection--west { left: 0; }
.tak-connection--winning { --connection: #7ff4cfe0; box-shadow: 0 0 10px #91ffdf5e; }
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
.tak-count, .tak-drop { padding: .15em .28em; border-radius: .22rem; background: #111f30e6; color: #fff; }
.tak-count { inset: 6% 6% auto auto; }
.tak-drop { inset: auto auto 5% 6%; background: #aedaf5; color: #102e43; }
.tak-board--compact .tak-square--last::after { border-color: #98d4f76b; }
</style>
