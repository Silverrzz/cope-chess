<script setup lang="ts">
defineProps<{ animated?: boolean }>();

const size = 64;
const horizontalSeams = [[1, -1, 1], [-1, -1, 1]];
const verticalSeams = [[1, -1], [1, 1], [-1, 1]];

function edge(x: number, y: number, dx: number, dy: number, tab: number): string {
  const point = (along: number, offset: number) =>
    `${x + dx * along - dy * offset * tab} ${y + dy * along + dx * offset * tab}`;
  const end = `L${point(size, 0)}`;
  if (!tab) return end;
  return [
    `L${point(24, 0)}`,
    `C${point(28, 0)} ${point(28, 2)} ${point(27, 5)}`,
    `C${point(22, 18)} ${point(42, 18)} ${point(37, 5)}`,
    `C${point(36, 2)} ${point(36, 0)} ${point(40, 0)}`,
    end,
  ].join(" ");
}

const pieces = Array.from({ length: 9 }, (_, index) => {
  const row = Math.floor(index / 3);
  const column = index % 3;
  const x = column * size;
  const y = row * size;
  return [
    `M${x} ${y}`,
    edge(x, y, 1, 0, horizontalSeams[row - 1]?.[column] ?? 0),
    edge(x + size, y, 0, 1, verticalSeams[row]?.[column] ?? 0),
    edge(x + size, y + size, -1, 0, -(horizontalSeams[row]?.[column] ?? 0)),
    edge(x, y + size, 0, -1, -(verticalSeams[row]?.[column - 1] ?? 0)),
    "Z",
  ].join(" ");
});
</script>

<template>
  <svg :class="{ 'puzzle-mosaic--animated': animated }" viewBox="-3 -3 198 198" aria-hidden="true">
    <path v-for="(piece, index) in pieces" :key="piece" class="puzzle-mosaic__piece" :d="piece" :style="{ '--piece-index': index }" />
  </svg>
</template>

<style scoped>
.puzzle-mosaic__piece {
  fill: color-mix(in srgb, var(--puzzle-ink) 11%, var(--puzzle-surface));
  stroke: var(--puzzle-ink);
  stroke-width: 1.1;
  stroke-linejoin: round;
}
.puzzle-mosaic__piece:is(:nth-child(2), :nth-child(6), :nth-child(7)) {
  fill: color-mix(in srgb, var(--puzzle-ink) 23%, var(--puzzle-surface));
}
.puzzle-mosaic__piece:is(:nth-child(5), :nth-child(9)) {
  fill: color-mix(in srgb, var(--puzzle-accent) 29%, var(--puzzle-surface));
}
.puzzle-mosaic--animated .puzzle-mosaic__piece { fill: transparent; }
@media (prefers-reduced-motion: no-preference) {
  .puzzle-mosaic--animated .puzzle-mosaic__piece {
    animation: highlight-piece 11s linear infinite;
    animation-delay: calc(var(--puzzle-delay, 0s) - var(--piece-index) * .8s);
  }
}
@keyframes highlight-piece {
  0%, 21%, 100% { fill: transparent; }
  7%, 11% { fill: var(--puzzle-accent); }
}
</style>
