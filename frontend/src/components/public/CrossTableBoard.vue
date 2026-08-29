<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'

import ChessBoard from '@/components/chess/ChessBoard.vue'

defineProps<{
  fen: string
  lastMove?: string | null
  label: string
}>()

const root = ref<HTMLElement | null>(null)
const rendered = ref(false)
let observer: IntersectionObserver | null = null

onMounted(() => {
  if (!root.value || typeof IntersectionObserver === 'undefined') {
    rendered.value = true
    return
  }
  observer = new IntersectionObserver(([entry]) => {
    rendered.value = Boolean(entry?.isIntersecting)
  }, { rootMargin: '384px 0px' })
  observer.observe(root.value)
})

onBeforeUnmount(() => {
  observer?.disconnect()
  observer = null
})
</script>

<template>
  <div ref="root" class="cross-table-board">
    <ChessBoard
      v-if="rendered"
      :fen="fen"
      :last-move="lastMove || ''"
      :label="label"
      :controls="false"
      :coordinates="false"
      compact
    />
    <div v-else class="cross-table-board__placeholder" aria-hidden="true"></div>
  </div>
</template>

<style scoped>
.cross-table-board,
.cross-table-board__placeholder {
  width: 100%;
  aspect-ratio: 1;
}

.cross-table-board__placeholder {
  border: 1px solid color-mix(in srgb, var(--color-text, #17202a) 18%, transparent);
  border-radius: var(--radius-sm, 0.35rem);
  background:
    linear-gradient(45deg, color-mix(in srgb, var(--color-text, #17202a) 7%, transparent) 25%, transparent 25%, transparent 75%, color-mix(in srgb, var(--color-text, #17202a) 7%, transparent) 75%),
    linear-gradient(45deg, color-mix(in srgb, var(--color-text, #17202a) 7%, transparent) 25%, transparent 25%, transparent 75%, color-mix(in srgb, var(--color-text, #17202a) 7%, transparent) 75%);
  background-position: 0 0, 12.5% 12.5%;
  background-size: 25% 25%;
}
</style>
