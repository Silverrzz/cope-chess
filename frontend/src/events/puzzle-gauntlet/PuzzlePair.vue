<script setup lang="ts">
defineProps<{ animated?: boolean }>();
</script>

<template>
  <svg :class="{ 'puzzle-pair--animated': animated }" viewBox="0 0 128 96" fill="none" stroke-width="1.5" stroke-linejoin="round" aria-hidden="true">
    <path class="puzzle-pair__left" d="M14 28h16v-4a8 8 0 0 1 16 0v4h15v14h-4a8 8 0 0 0 0 16h4v17H14z" />
    <path class="puzzle-pair__right" d="M69 34h47v47H99v-4a8 8 0 0 0-16 0v4H69V58h-4a8 8 0 0 1 0-16h4z" />
  </svg>
</template>

<style scoped>
.puzzle-pair__left {
  fill: color-mix(in srgb, var(--pair-primary) 13%, var(--pair-surface));
  stroke: var(--pair-primary);
}
.puzzle-pair__right {
  fill: color-mix(in srgb, var(--pair-accent) 10%, var(--pair-surface));
  stroke: var(--pair-accent);
}
@media (prefers-reduced-motion: no-preference) {
  .puzzle-pair--animated .puzzle-pair__right { animation: fit-piece 12s ease-in-out infinite; }
}
@keyframes fit-piece {
  0%, 100% { transform: translate(6px, 5px); }
  35%, 65% { transform: translateX(-8px); }
}
</style>
