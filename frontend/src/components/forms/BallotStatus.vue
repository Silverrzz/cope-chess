<script setup lang="ts">
import { statusLabels, type BallotStatus } from '@/api/ballots'

defineProps<{ status: BallotStatus }>()
</script>

<template>
  <span class="ballot-status" :data-status="status">{{ statusLabels[status] }}</span>
</template>
