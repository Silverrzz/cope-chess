<script setup lang="ts">
import { computed } from 'vue'

import AppIcon from '@/components/ui/AppIcon.vue'

const props = defineProps<{
  count: number
}>()

const label = computed(() => `${props.count} ${props.count === 1 ? 'spectator' : 'spectators'}`)
</script>

<template>
  <span class="spectator-count" :aria-label="label" :title="label">
    <AppIcon name="eye" :size="14" :stroke-width="1.9" />
    <span>{{ count }}</span>
  </span>
</template>

<style scoped>
.spectator-count {
  display: inline-flex;
  align-items: center;
  gap: 0.25rem;
  color: var(--color-text-muted, #607080);
  font-size: 0.72rem;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  line-height: 1;
  white-space: nowrap;
}
</style>
