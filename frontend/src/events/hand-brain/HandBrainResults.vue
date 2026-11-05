<script setup lang="ts">
import { computed, ref } from 'vue'
import type { EventDetailResponse } from '@/types/events'
import HandBrainBracket from './HandBrainBracket.vue'
import type { HandBrainPayload } from './types'

const props = defineProps<{ detail: EventDetailResponse }>()
const payload = computed(() => props.detail.custom as HandBrainPayload)
const selectedMatchId = ref<number | undefined>()
const selected = computed(() => selectedMatchId.value ?? payload.value.matches.find(match => match.key === 'F')?.id)
</script>

<template>
  <div class="hb-results">
    <HandBrainBracket :matches="payload.matches" :teams="payload.teams" :selected="selected" @select="selectedMatchId = $event.id" />
  </div>
</template>

<style scoped>
.hb-results { --color-text: #fff0f3; --color-text-muted: #b98696; --color-border: #ff729326; --color-border-strong: #ac5b73; --color-surface: #1c0b14; --color-surface-raised: #29101b; --color-accent: #ff7293; width: 100%; margin-top: 1.5rem; color: var(--color-text); }
</style>
