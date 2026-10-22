<script setup lang="ts">
import { computed, useId } from "vue";

const props = defineProps<{ archive?: boolean }>();
const id = useId();
const pads = computed(() => props.archive
  ? [{ x: 12, y: 22, angle: -35 }, { x: 49, y: 16, angle: 65 }, { x: 89, y: 80, angle: -50 }, { x: 54, y: 87, angle: 115 }]
  : [{ x: 10, y: 16, angle: -35 }, { x: 90, y: 16, angle: 65 }, { x: 14, y: 86, angle: -50 }, { x: 86, y: 86, angle: 115 }]);
const pieces = computed(() => props.archive
  ? [{ x: 29, y: 19, symbol: '♞', angle: -18 }, { x: 70, y: 16, symbol: '♝', angle: 12 }, { x: 94, y: 45, symbol: '♜', angle: 15 }, { x: 70, y: 86, symbol: '♟', angle: -12 }]
  : [{ x: 27, y: 17, symbol: '♞', angle: -18 }, { x: 73, y: 83, symbol: '♜', angle: 15 }, { x: 7, y: 53, symbol: '♟', angle: -12 }, { x: 93, y: 53, symbol: '♝', angle: 12 }]);
</script>

<template>
  <div class="pond-scene" aria-hidden="true">
    <svg v-for="(pad, index) in pads" :key="index" class="pond-pad" viewBox="-70 -70 140 140" preserveAspectRatio="none" :style="{ left: `${pad.x}%`, top: `${pad.y}%` }">
      <defs><radialGradient :id="`${id}-leaf-${index}`"><stop stop-color="#788951" /><stop offset="1" stop-color="#354b35" /></radialGradient></defs>
      <g :transform="`rotate(${pad.angle})`">
        <path d="M0 0 45-45A64 64 0 1 0 58 27Z" :fill="`url(#${id}-leaf-${index})`" stroke="#b2c18a" stroke-opacity=".18" />
        <path d="M0 0-49-12M0 0-29 43M0 0 18 49M0 0-16-49" stroke="#c2ce99" stroke-opacity=".16" fill="none" />
      </g>
    </svg>
    <span v-for="(piece, index) in pieces" :key="index" class="pond-piece" :style="{ left: `${piece.x}%`, top: `${piece.y}%`, '--piece-angle': `${piece.angle}deg` }">{{ piece.symbol }}</span>
  </div>
</template>

<style scoped>
.pond-scene { position: absolute; z-index: -1; inset: 0; overflow: hidden; pointer-events: none; }
.pond-pad { position: absolute; width: clamp(4rem, 7vw, 8rem); height: clamp(1.6rem, 2.8vw, 3.2rem); opacity: .46; transform: translate(-50%, -50%); }
.pond-piece { position: absolute; color: #a9c2ad; opacity: .16; font: clamp(2rem, 3.5vw, 3.5rem)/1 Georgia, serif; transform: translate(-50%, -50%) rotate(var(--piece-angle)); }
@media (max-width: 58rem) { .pond-pad { opacity: .3; }.pond-piece { opacity: .1; } }
</style>
