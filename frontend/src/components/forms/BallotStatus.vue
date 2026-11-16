<script setup lang="ts">
import { statusLabels, type BallotStatus } from '@/api/ballots'
import StatusBadge from '@/components/ui/StatusBadge.vue'
import type { FeedbackTone } from '@/types/ui'

defineProps<{ status: BallotStatus }>()

const tones: Record<BallotStatus, FeedbackTone> = {
  draft: 'neutral',
  open: 'info',
  tallying: 'warning',
  sealed: 'neutral',
  published: 'success',
}
</script>

<template>
  <StatusBadge :tone="tones[status]" dot>{{ statusLabels[status] }}</StatusBadge>
</template>
