<script setup lang="ts">
import { computed } from 'vue'

import TakStone from './TakStone.vue'
import { parseTakMove, type TakSide } from './tak'

const props = withDefaults(defineProps<{ move: string; side?: TakSide; chip?: boolean }>(), { side: 'white', chip: false })
const notation = computed(() => parseTakMove(props.move))
</script>

<template>
  <span v-if="notation" class="tak-notation" :class="{ 'tak-notation--chip': chip }" :title="`${move} · ${notation.description}`">
    <template v-if="notation.direction">
      <b v-if="notation.count > 1" class="tak-notation__carry" aria-hidden="true">{{ notation.count }}</b>
      <span>{{ notation.square }}</span>
      <b class="tak-notation__arrow" aria-hidden="true">{{ notation.arrow }}</b>
      <span v-if="notation.drops.length > 1" class="tak-notation__drops" aria-hidden="true">{{ notation.drops.join('·') }}</span>
      <b v-if="notation.flattened" class="tak-notation__flatten" aria-hidden="true">✦</b>
    </template>
    <template v-else>
      <TakStone class="tak-notation__piece" :kind="notation.kind" :side="side" />
      <span>{{ notation.square }}</span>
    </template>
    <span class="sr-only">{{ notation.description }}</span>
  </span>
  <span v-else>{{ move }}</span>
</template>

<style scoped>
.tak-notation {
  display: inline-flex;
  align-items: center;
  width: max-content;
  gap: .14em;
  font-family: var(--font-mono, ui-monospace, monospace);
  font-variant-numeric: tabular-nums;
  line-height: 1.3;
}
.tak-notation--chip {
  border: 1px solid var(--color-border, #d5dbe1);
  border-radius: .35rem;
  background: color-mix(in srgb, var(--color-text, #17202a) 4%, var(--color-surface, #fff));
  padding: .14rem .33rem;
}
.tak-notation__piece { --tak-stone-size: .68em; margin-inline-end: .19em; }
.tak-notation__carry, .tak-notation__drops { color: inherit; opacity: .75; }
.tak-notation__arrow { font-size: 1.18em; line-height: .85; }
.tak-notation__flatten { font-size: .78em; }
</style>
