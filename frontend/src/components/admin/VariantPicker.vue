<script setup lang="ts">
import { VARIANT_OPTIONS, type Variant } from '@/components/chess/chess'

defineProps<{ modelValue?: string }>()
const emit = defineEmits<{ 'update:modelValue': [value: Variant] }>()
</script>

<template>
  <select class="input" :value="modelValue || 'standard'" @change="emit('update:modelValue', ($event.target as HTMLSelectElement).value as Variant)">
    <option v-if="modelValue === 'legacy'" value="legacy" disabled>Mixed legacy variants</option>
    <option v-for="variant in VARIANT_OPTIONS" :key="variant.id" :value="variant.id">{{ variant.name }}</option>
  </select>
</template>
