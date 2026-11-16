<script setup lang="ts">
defineProps<{
  title: string
  description?: string
}>()
</script>

<template>
  <header class="page-header">
    <div class="page-header__copy">
      <h1>{{ title }}</h1>
      <p v-if="description" class="page-header__description">{{ description }}</p>
    </div>
    <div v-if="$slots.actions" class="page-header__actions">
      <slot name="actions" />
    </div>
  </header>
</template>

<style scoped>
.page-header {
  align-items: flex-start;
  display: flex;
  gap: var(--space-5, 1.25rem);
  justify-content: space-between;
  min-width: 0;
}

.page-header__copy { min-width: 0; overflow-wrap: anywhere; }
.page-header h1 { margin: 0; text-wrap: balance; }

.page-header__description {
  color: var(--color-text-muted, #64748b);
  margin: .45rem 0 0;
  max-width: 70ch;
}

.page-header__actions {
  align-items: center;
  display: flex;
  flex: 0 0 auto;
  flex-wrap: wrap;
  gap: .65rem;
  justify-content: flex-end;
}

@media (max-width: 42rem) {
  .page-header { align-items: stretch; flex-direction: column; }
  .page-header__actions { justify-content: flex-start; }
}
</style>
