<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'
import AppIcon from '@/components/ui/AppIcon.vue'
import type { EventDetailResponse } from '@/types/events'
import HandBrainBracket from './HandBrainBracket.vue'
import HandBrainCheers from './HandBrainCheers.vue'
import type { HandBrainMatch, HandBrainPayload } from './types'

const props = defineProps<{ detail: EventDetailResponse }>()
const payload = computed(() => props.detail.custom as HandBrainPayload)
const selectedMatchId = ref<number | undefined>()
const panel = ref<HTMLElement>()
const closeButton = ref<HTMLButtonElement>()
let opener: HTMLElement | null = null
const matchup = computed(() => payload.value.matches.find(match => match.id === selectedMatchId.value))
const matchupTeams = computed(() => matchup.value?.sources.map((source, index) => ({
  team: payload.value.teams.find(team => team.id === matchup.value?.team_ids[index]),
  placeholder: typeof source === 'string' ? `Winner ${source}` : `Seed ${source}`,
})) ?? [])
const selected = computed(() => selectedMatchId.value ?? (
  props.detail.event.status === 'completed'
    ? payload.value.matches.find(match => match.key === 'F')?.id
    : payload.value.matches.find(match => match.status === 'live' || match.status === 'pending')?.id
))

async function openMatchup(match: HandBrainMatch, button: HTMLButtonElement) {
  opener = button
  selectedMatchId.value = match.id
  await nextTick()
  closeButton.value?.focus({ preventScroll: true })
}

function closeMatchup(restoreFocus = true) {
  selectedMatchId.value = undefined
  if (restoreFocus) opener?.focus({ preventScroll: true })
  opener = null
}

function handleOutsidePointer(event: PointerEvent) {
  if (!matchup.value || !(event.target instanceof Element)) return
  if (panel.value?.contains(event.target) || event.target.closest('.match-select')) return
  closeMatchup(false)
}

function handleEscape(event: KeyboardEvent) {
  if (event.key === 'Escape' && !event.defaultPrevented && matchup.value) closeMatchup()
}

onMounted(() => {
  document.addEventListener('pointerdown', handleOutsidePointer)
  window.addEventListener('keydown', handleEscape)
})
onUnmounted(() => {
  document.removeEventListener('pointerdown', handleOutsidePointer)
  window.removeEventListener('keydown', handleEscape)
})
</script>

<template>
  <div class="hb-results">
    <HandBrainCheers :detail="detail" v-slot="{ cheer }">
      <HandBrainBracket floating :matches="payload.matches" :teams="payload.teams" :selected="selected" @select="openMatchup" @cheer="cheer" />
    </HandBrainCheers>
    <Teleport to="body">
      <Transition name="matchup-panel">
        <aside v-if="matchup" ref="panel" class="matchup-panel" role="dialog" aria-labelledby="hb-matchup-title">
          <header class="matchup-heading">
            <div><span>{{ matchup.stage }}</span><h2 id="hb-matchup-title">Match {{ matchup.key }}</h2></div>
            <button ref="closeButton" type="button" aria-label="Close matchup" @click="closeMatchup()">
              <AppIcon name="close" :size="20" />
            </button>
          </header>
          <section v-for="({ team, placeholder }, index) in matchupTeams" :key="index" class="matchup-team" :style="{ '--team-color': team?.color ?? '#b98696' }">
            <h3><span v-if="team" class="matchup-seed" :aria-label="`Seed ${team.seed}`">{{ team.seed }}</span>{{ team?.name ?? placeholder }}</h3>
            <ul v-if="team">
              <li v-for="member in team.members" :key="member.id">
                <strong>{{ member.name }}</strong>
                <small v-if="member.version">{{ member.version }}</small>
              </li>
            </ul>
          </section>
        </aside>
      </Transition>
    </Teleport>
  </div>
</template>

<style scoped>
.hb-results { --color-text: #fff0f3; --color-text-muted: #b98696; --color-border: #ff729326; --color-border-strong: #ac5b73; --color-surface: #1c0b14; --color-surface-raised: #29101b; --color-accent: #ff7293; min-width: 0; width: 100%; margin-top: .65rem; color: var(--color-text); }
.matchup-panel { position: fixed; z-index: 45; inset: calc(var(--header-height, 4rem) + 1px) auto 0 0; width: min(23rem, calc(100% - 1.25rem)); box-sizing: border-box; overflow-y: auto; padding: 1.25rem; border-inline-end: 1px solid #ff72934d; background: #1c0b14; color: #fff0f3; box-shadow: .75rem 0 2.5rem #10050c80; }
.matchup-heading { display: flex; align-items: center; justify-content: space-between; gap: 1rem; padding-bottom: 1rem; border-bottom: 1px solid #ff729326; }
.matchup-heading span { color: #ff9eb5; font-size: .65rem; font-weight: 750; letter-spacing: .08em; text-transform: uppercase; }
.matchup-heading h2 { margin: .2rem 0 0; font-size: 1.5rem; line-height: 1.2; }
.matchup-heading button { display: grid; width: 2.25rem; height: 2.25rem; flex: 0 0 auto; place-items: center; padding: 0; border: 1px solid #ff72934d; border-radius: 50%; background: transparent; color: #fff0f3; cursor: pointer; }
.matchup-heading button:hover, .matchup-heading button:focus-visible { border-color: #ff7293; }
.matchup-team { margin-top: .85rem; padding: .8rem; border: 1px solid #ff729326; border-inline-start: 3px solid var(--team-color); border-radius: .5rem; background: #29101b; }
.matchup-team h3 { display: flex; align-items: center; gap: .55rem; margin: 0; font-size: .9rem; }
.matchup-seed { display: grid; width: 1.55rem; height: 1.55rem; flex: 0 0 auto; place-items: center; border-radius: 50%; background: color-mix(in srgb, var(--team-color) 16%, transparent); color: var(--team-color); font-size: .65rem; font-variant-numeric: tabular-nums; }
.matchup-team ul { display: grid; gap: .45rem; margin: .65rem 0 0; padding: .55rem 0 0; border-top: 1px solid #ff729326; list-style: none; }
.matchup-team li { display: grid; gap: .1rem; font-size: .8rem; }
.matchup-team small { color: #c697a8; font-size: .65rem; overflow-wrap: anywhere; }
.matchup-panel-enter-active, .matchup-panel-leave-active { transition: transform .24s cubic-bezier(.22, .75, .28, 1), opacity .2s ease; }
.matchup-panel-enter-from, .matchup-panel-leave-to { opacity: 0; transform: translateX(-100%); }
@media (prefers-reduced-motion: reduce) { .matchup-panel-enter-active, .matchup-panel-leave-active { transition: none; } }
</style>
