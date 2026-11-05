<script setup lang="ts">
defineProps<{ role: 'brain' | 'hand' }>()
</script>
<template>
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
    <g v-if="role === 'brain'"><path d="M12 18V5m3 8a4.2 4.2 0 0 1-3-4 4.2 4.2 0 0 1-3 4" /><path d="M17.7 6.3A3 3 0 1 0 12 5a3 3 0 1 0-5.7 1.3M18 5.1a4 4 0 0 1 2.5 5.8M18 18a4 4 0 0 0 2-7.5M6 18a4 4 0 0 1-2-7.5M6 5.1a4 4 0 0 0-2.5 5.8M12 18a3 3 0 0 0 6 0M12 18a3 3 0 0 1-6 0" /></g>
    <g v-else><path d="M18 11V6a2 2 0 0 0-4 0v4M14 6V4a2 2 0 0 0-4 0v6.5M10 6a2 2 0 0 0-4 0v8M18 8a2 2 0 0 1 4 0v6a8 8 0 0 1-8 8h-2c-2.8 0-4.5-.9-6-2.3l-3.6-3.6a2 2 0 0 1 2.8-2.8L7 15" /></g>
  </svg>
</template>
