<script setup lang="ts">
import type { EngineVersionTag } from './types'

defineProps<{ tags: EngineVersionTag[] }>()
</script>

<template>
  <span v-if="tags.length" class="engine-tags">
    <span v-for="tag in tags" :key="tag.id" class="engine-tag" :style="{ '--tag-color': tag.color }">{{ tag.name }}</span>
  </span>
</template>

<style scoped>
.engine-tags { display: inline-flex; flex-wrap: wrap; gap: .3rem; vertical-align: middle; }
.engine-tag {
  align-items: center;
  background: color-mix(in srgb, var(--tag-color) 12%, var(--color-surface));
  border: 1px solid color-mix(in srgb, var(--tag-color) 48%, var(--color-border));
  border-radius: 999px;
  color: var(--color-text);
  display: inline-flex;
  font-size: .68rem;
  font-weight: 650;
  gap: .3rem;
  line-height: 1.2;
  padding: .2rem .45rem;
}
.engine-tag::before { background: var(--tag-color); border-radius: 50%; content: ''; height: .4rem; width: .4rem; }
</style>
