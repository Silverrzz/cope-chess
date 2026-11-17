<script setup lang="ts">
import { computed } from 'vue'

import { TAK_SIZE, takMovePath, type TakPosition } from './tak'

const props = defineProps<{
  position: TakPosition
  orientation: 'white' | 'black'
  lastMove: string
  coordinates: boolean
  label: string
}>()

const squares = computed(() => {
  const result = Array.from({ length: TAK_SIZE ** 2 }, (_, index) => index)
  return props.orientation === 'white' ? result : result.reverse()
})
const changed = computed(() => new Set(takMovePath(props.lastMove)))

function squareName(index: number): string {
  return `${'abcdef'[index % TAK_SIZE]}${TAK_SIZE - Math.floor(index / TAK_SIZE)}`
}

function stackLabel(index: number): string {
  const stones = props.position.stacks[index] ?? []
  const names = stones.map((stone) => `${stone.startsWith('1') ? 'white' : 'black'} ${stone.endsWith('S') ? 'wall' : stone.endsWith('C') ? 'capstone' : 'flat'}`)
  return `${squareName(index)} · ${names.length ? names.join(', ') : 'empty'}`
}
</script>

<template>
  <div class="tak-board" role="group" :aria-label="label">
    <div v-for="(index, displayIndex) in squares" :key="index" class="tak-square"
      :class="{ 'tak-square--dark': (index % TAK_SIZE + Math.floor(index / TAK_SIZE)) % 2, 'tak-square--last': changed.has(index) }"
      role="img" :aria-label="stackLabel(index)" :title="stackLabel(index)">
      <span v-if="coordinates && displayIndex % TAK_SIZE === 0" class="tak-rank" aria-hidden="true">{{ squareName(index)[1] }}</span>
      <span v-if="coordinates && displayIndex >= TAK_SIZE * (TAK_SIZE - 1)" class="tak-file" aria-hidden="true">{{ squareName(index)[0] }}</span>
      <div v-if="position.stacks[index]?.length" class="tak-stack" aria-hidden="true">
        <i v-for="(stone, level) in position.stacks[index]?.slice(-5)" :key="level" class="tak-stone"
          :class="{ 'tak-stone--black': stone.startsWith('2'), 'tak-stone--wall': stone.endsWith('S'), 'tak-stone--cap': stone.endsWith('C') }" />
      </div>
      <span v-if="(position.stacks[index]?.length ?? 0) > 1" class="tak-count" aria-hidden="true">{{ position.stacks[index]?.length }}</span>
    </div>
  </div>
</template>

<style scoped>
.tak-board { display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); aspect-ratio: 1; overflow: hidden; border-radius: 0.25rem; }
.tak-square { position: relative; display: grid; place-items: center; background: #ded8c8; color: #30302e; }
.tak-square--dark { background: #aaa697; }
.tak-square--last { box-shadow: inset 0 0 0 100vmax rgb(112 146 65 / 40%); }
.tak-rank, .tak-file, .tak-count { position: absolute; font-size: clamp(0.45rem, 2.9cqw, 0.85rem); font-weight: 700; }
.tak-rank { inset: 5% auto auto 7%; }
.tak-file { inset: auto 6% 3% auto; }
.tak-count { inset: 5% 7% auto auto; }
.tak-stack { width: 62%; height: 70%; display: flex; flex-direction: column-reverse; align-items: center; justify-content: center; }
.tak-stone { display: block; width: 88%; height: 19%; margin-top: -4%; border: 1px solid #8b8273; border-radius: 18%; background: #fbf5e8; box-shadow: 0 1px 1px #0005; }
.tak-stone--black { background: #28313a; border-color: #10181f; }
.tak-stone--wall { width: 28%; height: 57%; border-radius: 14%; }
.tak-stone--cap { width: 56%; height: 53%; border-radius: 48% 48% 34% 34%; }
</style>
