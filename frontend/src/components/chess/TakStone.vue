<script setup lang="ts">
import type { TakSide, TakStoneKind } from './tak'

defineProps<{ side: TakSide; kind: TakStoneKind }>()
</script>

<template>
  <span class="tak-stone" :class="[`tak-stone--${side}`, `tak-stone--${kind}`]" aria-hidden="true"></span>
</template>

<style scoped>
.tak-stone {
  --stone-light: #fff9e7;
  --stone-face: #e1d3b7;
  --stone-edge: #8b7457;
  position: relative;
  display: inline-block;
  flex: none;
  inline-size: var(--tak-stone-size, 1em);
  aspect-ratio: 1;
  box-sizing: border-box;
  border: 1px solid var(--stone-edge);
  border-radius: 18%;
  background: linear-gradient(145deg, var(--stone-light), var(--stone-face) 75%);
  box-shadow: inset 0 1px 0 #fff4, 0 2px 0 var(--stone-edge), 0 3px 5px #0005;
}
.tak-stone--black {
  --stone-light: #526784;
  --stone-face: #26364f;
  --stone-edge: #101a2b;
}
.tak-stone--wall {
  inline-size: calc(var(--tak-stone-size, 1em) * 0.38);
  block-size: var(--tak-stone-size, 1em);
  border-radius: 21%;
  background: linear-gradient(90deg, var(--stone-edge), var(--stone-light) 30% 54%, var(--stone-face) 64%);
}
.tak-stone--cap { border-radius: 50%; }
.tak-stone--cap::after {
  position: absolute;
  inset: 21%;
  border-radius: inherit;
  border: 1px solid color-mix(in srgb, var(--stone-edge) 68%, transparent);
  box-shadow: inset 0 1px 2px #0003;
  content: '';
}
</style>
