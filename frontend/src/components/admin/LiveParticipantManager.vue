<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import AppIcon from '@/components/ui/AppIcon.vue'
import type { Engine, TournamentConfig } from '@/components/admin/types'

interface LiveParticipantSummary {
  engine_id: number
  total: number
  pending: number
  assigned: number
  live: number
  finished: number
  abandoned: number
}

interface LiveTournamentRoster {
  editable: boolean
  reason: string
  available_engines: Engine[]
  participants: LiveParticipantSummary[]
  hero_engine_id: number | null
}

const props = defineProps<{
  roster: LiveTournamentRoster
  config: TournamentConfig
  engineLabels: Record<number, string>
  pending: string
  tournamentStatus: string
}>()

const emit = defineEmits<{
  add: [engineId: number]
  remove: [participant: LiveParticipantSummary]
}>()

const addOpen = ref(false)
const query = ref('')
const rosterQuery = ref('')
const selectedEngineId = ref<number | null>(null)
const engineSearchInput = ref<HTMLInputElement | null>(null)

const selectedEngine = computed(() => (
  props.roster.available_engines.find((engine) => engine.id === selectedEngineId.value) ?? null
))
const filteredEngines = computed(() => {
  const needle = query.value.trim().toLowerCase()
  if (!needle) return props.roster.available_engines
  return props.roster.available_engines.filter((engine) => (
    `${engine.name} ${engine.version} ${engine.author ?? ''}`.toLowerCase().includes(needle)
  ))
})
const visibleParticipants = computed(() => {
  if (props.roster.participants.length <= 8) return props.roster.participants
  const needle = rosterQuery.value.trim().toLowerCase()
  if (!needle) return props.roster.participants
  return props.roster.participants.filter((participant) => (
    engineLabel(participant.engine_id).toLowerCase().includes(needle)
  ))
})
const scheduledGameEstimate = computed(() => {
  if (props.config.format === 'round_robin' && 'cycles' in props.config.format_options) {
    return props.config.participants.length * props.config.format_options.cycles * 2
  }
  return 2
})

watch(() => props.roster.available_engines, (engines) => {
  if (selectedEngineId.value !== null && !engines.some((engine) => engine.id === selectedEngineId.value)) {
    closeAddition()
  }
})

function engineLabel(engineId: number): string {
  return props.engineLabels[engineId] ?? `Engine ${engineId}`
}

async function openAddition(): Promise<void> {
  addOpen.value = true
  query.value = ''
  selectedEngineId.value = null
  await nextTick()
  engineSearchInput.value?.focus()
}

function closeAddition(): void {
  addOpen.value = false
  query.value = ''
  selectedEngineId.value = null
}

function scheduleSelected(): void {
  if (selectedEngineId.value === null) return
  emit('add', selectedEngineId.value)
}
</script>

<template>
  <section class="panel live-roster">
    <header class="live-roster__header">
      <div class="live-roster__title">
        <span class="live-roster__icon"><AppIcon name="engine" :size="18" /></span>
        <div>
          <h2>Live participants</h2>
          <p v-if="roster.editable && tournamentStatus === 'paused'">Adjust the field before resuming. Newly scheduled games remain queued until the tournament resumes.</p>
          <p v-else-if="roster.editable">Change the field without stopping the tournament. Schedule updates take effect immediately.</p>
          <p v-else>{{ roster.reason }}</p>
        </div>
      </div>
      <div class="live-roster__header-actions">
        <span class="live-roster__count">{{ config.participants.length }} {{ config.participants.length === 1 ? 'engine' : 'engines' }}</span>
        <button v-if="roster.editable" class="button button--primary button--small" type="button" :disabled="!!pending || !roster.available_engines.length" aria-controls="live-engine-picker" :aria-expanded="addOpen" @click="openAddition">
          <AppIcon name="plus" :size="15" /> Add engine
        </button>
      </div>
    </header>

    <div v-if="addOpen" id="live-engine-picker" class="add-engine-menu" role="region" aria-labelledby="live-engine-picker-title" @keydown.esc="closeAddition">
      <div class="add-engine-menu__top">
        <div>
          <span>Add a live participant</span>
          <h3 id="live-engine-picker-title">Choose an engine version</h3>
        </div>
        <button class="icon-button" type="button" aria-label="Close engine picker" @click="closeAddition"><AppIcon name="close" :size="18" /></button>
      </div>
      <label class="engine-search">
        <AppIcon name="search" :size="16" />
        <input ref="engineSearchInput" v-model="query" class="input" type="search" placeholder="Search engines, versions, or authors">
      </label>
      <div v-if="filteredEngines.length" class="engine-choice-grid" role="radiogroup" aria-label="Available engine versions">
        <button v-for="engine in filteredEngines" :key="engine.id" class="engine-choice" :class="{ 'engine-choice--selected': selectedEngineId === engine.id }" type="button" role="radio" :aria-checked="selectedEngineId === engine.id" @click="selectedEngineId = engine.id">
          <span class="engine-choice__mark"><AppIcon :name="selectedEngineId === engine.id ? 'check' : 'engine'" :size="15" /></span>
          <span><strong>{{ engine.name }}</strong><small>{{ engine.version }}<template v-if="engine.author"> · {{ engine.author }}</template></small></span>
        </button>
      </div>
      <p v-else class="engine-choice-empty">No available engines match “{{ query }}”.</p>
      <div class="schedule-preview" :class="{ 'schedule-preview--ready': selectedEngine }">
        <div>
          <span>Schedule impact</span>
          <strong v-if="selectedEngine">{{ scheduledGameEstimate }} new games</strong>
          <strong v-else>Select an engine</strong>
          <small v-if="selectedEngine && config.format === 'round_robin'">Every opponent, both colours, across every configured cycle. Earlier-cycle games enter the queue first.</small>
          <small v-else-if="selectedEngine">Both colours against the gauntlet hero. The first leg enters its historical round position.</small>
          <small v-else>Choose a version to preview the live schedule change.</small>
        </div>
        <div class="schedule-preview__actions">
          <button class="button button--ghost" type="button" :disabled="!!pending" @click="closeAddition">Cancel</button>
          <button class="button button--primary" type="button" :disabled="!!pending || !selectedEngine" @click="scheduleSelected">{{ pending === 'participant-add' ? 'Scheduling…' : 'Add and schedule' }}</button>
        </div>
      </div>
    </div>

    <div v-if="roster.participants.length > 8" class="participant-roster__toolbar">
      <label class="roster-search">
        <AppIcon name="search" :size="15" />
        <input v-model="rosterQuery" class="input" type="search" placeholder="Find a participant" aria-label="Find a participant">
      </label>
      <span v-if="rosterQuery">{{ visibleParticipants.length }} of {{ roster.participants.length }}</span>
    </div>

    <div class="participant-roster" :class="{ 'participant-roster--readonly': !roster.editable }" role="table" aria-label="Live tournament participants">
      <div class="participant-roster__head" role="row">
        <span role="columnheader">Engine</span>
        <span role="columnheader">Finished</span>
        <span role="columnheader">Queued</span>
        <span role="columnheader">Active</span>
        <span v-if="roster.editable" role="columnheader">Actions</span>
      </div>
      <div class="participant-roster__body" role="rowgroup">
        <div v-for="participant in visibleParticipants" :key="participant.engine_id" class="roster-row" :class="{ 'roster-row--hero': participant.engine_id === roster.hero_engine_id }" role="row">
          <div class="roster-row__identity" role="cell">
            <span class="roster-row__avatar"><AppIcon name="engine" :size="15" /></span>
            <div>
              <strong :title="engineLabel(participant.engine_id)">{{ engineLabel(participant.engine_id) }}</strong>
              <span v-if="participant.engine_id === roster.hero_engine_id" class="hero-badge">Gauntlet hero</span>
            </div>
          </div>
          <span class="roster-row__stat" data-label="Finished" role="cell">{{ participant.finished }}</span>
          <span class="roster-row__stat" data-label="Queued" role="cell">{{ participant.pending }}</span>
          <span class="roster-row__stat" data-label="Active" :class="{ active: participant.assigned + participant.live > 0 }" role="cell">{{ participant.assigned + participant.live }}</span>
          <div v-if="roster.editable" class="roster-row__action" role="cell">
            <button class="button button--ghost button--small roster-row__remove" type="button" :disabled="!!pending || config.participants.length <= 2" :aria-label="`Remove ${engineLabel(participant.engine_id)}`" :title="config.participants.length <= 2 ? 'A tournament must keep at least two participants.' : `Remove ${engineLabel(participant.engine_id)}`" @click="emit('remove', participant)">
              <AppIcon name="trash" :size="14" /><span>Remove</span>
            </button>
          </div>
        </div>
        <p v-if="!visibleParticipants.length" class="participant-roster__empty">No participants match “{{ rosterQuery }}”.</p>
      </div>
    </div>
  </section>
</template>

<style scoped>
.live-roster { overflow: hidden; padding: 0; }
.live-roster__header { align-items: center; border-bottom: 1px solid var(--color-border, #d9e0ea); display: flex; gap: 1rem; justify-content: space-between; padding: 1rem; }
.live-roster__title { align-items: center; display: flex; gap: .75rem; min-width: 0; }
.live-roster__icon { align-items: center; background: color-mix(in srgb, var(--color-accent, #315fcc) 11%, var(--color-surface, #fff)); border-radius: .6rem; color: var(--color-accent, #315fcc); display: inline-flex; height: 2.25rem; justify-content: center; width: 2.25rem; }
.live-roster h2, .live-roster h3 { margin: 0; }
.live-roster h2 { font-size: .94rem; }
.live-roster__title p { color: var(--color-text-muted, #64748b); font-size: .73rem; margin: .22rem 0 0; }
.live-roster__header-actions { align-items: center; display: flex; flex: 0 0 auto; gap: .55rem; }
.live-roster__header-actions .button { align-items: center; display: inline-flex; gap: .3rem; }
.live-roster__count { background: var(--color-surface-subtle, #f1f5f9); border-radius: 999px; color: var(--color-text-muted, #64748b); font-size: .68rem; font-weight: 700; padding: .35rem .55rem; }
.participant-roster__toolbar { align-items: center; background: var(--color-surface-subtle, #f8fafc); border-bottom: 1px solid var(--color-border, #d9e0ea); display: flex; gap: .75rem; justify-content: space-between; padding: .55rem 1rem; }
.participant-roster__toolbar > span { color: var(--color-text-muted, #64748b); font-size: .68rem; font-weight: 650; white-space: nowrap; }
.roster-search { display: block; max-width: 19rem; position: relative; width: 100%; }
.roster-search > :first-child { color: var(--color-text-muted, #64748b); left: .65rem; pointer-events: none; position: absolute; top: 50%; transform: translateY(-50%); }
.roster-search .input { font-size: .72rem; height: 2rem; padding-left: 1.95rem; width: 100%; }
.participant-roster { background: var(--color-surface, #fff); max-height: min(31rem, 55vh); overflow-y: auto; scrollbar-gutter: stable; }
.participant-roster__head, .roster-row { align-items: center; display: grid; gap: .75rem; grid-template-columns: minmax(12rem, 1fr) repeat(3, minmax(4rem, .22fr)) 5.5rem; padding-left: 1rem; padding-right: 1rem; }
.participant-roster--readonly .participant-roster__head, .participant-roster--readonly .roster-row { grid-template-columns: minmax(12rem, 1fr) repeat(3, minmax(4rem, .22fr)); }
.participant-roster__head { background: var(--color-surface, #fff); border-bottom: 1px solid var(--color-border, #d9e0ea); color: var(--color-text-muted, #64748b); font-size: .59rem; font-weight: 700; letter-spacing: .035em; min-height: 2rem; position: sticky; text-transform: uppercase; top: 0; z-index: 1; }
.roster-row { border-bottom: 1px solid color-mix(in srgb, var(--color-border, #d9e0ea) 72%, transparent); min-height: 3.1rem; }
.roster-row:last-child { border-bottom: 0; }
.roster-row:hover { background: var(--color-surface-subtle, #f8fafc); }
.roster-row--hero { background: color-mix(in srgb, var(--color-accent, #315fcc) 5%, var(--color-surface, #fff)); box-shadow: inset 3px 0 var(--color-accent, #315fcc); }
.roster-row__identity { align-items: center; display: flex; gap: .55rem; min-width: 0; }
.roster-row__avatar { align-items: center; background: var(--color-surface-subtle, #f8fafc); border: 1px solid var(--color-border, #d9e0ea); border-radius: .42rem; color: var(--color-text-muted, #64748b); display: inline-flex; flex: 0 0 auto; height: 1.75rem; justify-content: center; width: 1.75rem; }
.roster-row__identity > div { align-items: center; display: flex; gap: .5rem; min-width: 0; }
.roster-row__identity strong { font-size: .75rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.hero-badge { color: var(--color-accent, #315fcc); font-size: .62rem; font-weight: 700; text-transform: uppercase; }
.roster-row__stat { font-size: .73rem; font-variant-numeric: tabular-nums; font-weight: 700; }
.roster-row__stat.active { color: var(--color-accent, #315fcc); }
.roster-row__action { justify-self: start; }
.roster-row__remove { align-items: center; color: var(--color-danger, #b42318); display: inline-flex; gap: .25rem; justify-self: start; }
.participant-roster__empty { color: var(--color-text-muted, #64748b); font-size: .74rem; margin: 0; padding: 2.5rem 1rem; text-align: center; }
.add-engine-menu { background: color-mix(in srgb, var(--color-accent, #315fcc) 3%, var(--color-surface, #fff)); border-bottom: 1px solid var(--color-border, #d9e0ea); box-shadow: 0 .8rem 1.75rem color-mix(in srgb, #000 8%, transparent); display: grid; gap: .8rem; padding: 1rem; position: relative; z-index: 1; }
.add-engine-menu__top { align-items: center; display: flex; justify-content: space-between; }
.add-engine-menu__top span, .schedule-preview span { color: var(--color-text-muted, #64748b); font-size: .62rem; font-weight: 700; letter-spacing: .04em; text-transform: uppercase; }
.add-engine-menu h3 { font-size: .86rem; margin-top: .15rem; }
.engine-search { position: relative; }
.engine-search > :first-child { color: var(--color-text-muted, #64748b); left: .75rem; pointer-events: none; position: absolute; top: 50%; transform: translateY(-50%); }
.engine-search .input { padding-left: 2.2rem; width: 100%; }
.engine-choice-grid { display: grid; gap: .5rem; grid-template-columns: repeat(auto-fill, minmax(min(100%, 15rem), 1fr)); max-height: min(17rem, 40vh); overflow: auto; padding: .1rem; }
.engine-choice { align-items: center; background: var(--color-surface, #fff); border: 1px solid var(--color-border, #d9e0ea); border-radius: .55rem; color: inherit; cursor: pointer; display: flex; gap: .55rem; padding: .65rem; text-align: left; }
.engine-choice:hover { border-color: color-mix(in srgb, var(--color-accent, #315fcc) 45%, var(--color-border, #d9e0ea)); }
.engine-choice--selected { background: color-mix(in srgb, var(--color-accent, #315fcc) 7%, var(--color-surface, #fff)); border-color: var(--color-accent, #315fcc); box-shadow: 0 0 0 2px color-mix(in srgb, var(--color-accent, #315fcc) 12%, transparent); }
.engine-choice__mark { align-items: center; border: 1px solid var(--color-border, #d9e0ea); border-radius: 999px; color: var(--color-text-muted, #64748b); display: inline-flex; flex: 0 0 auto; height: 1.75rem; justify-content: center; width: 1.75rem; }
.engine-choice--selected .engine-choice__mark { background: var(--color-accent, #315fcc); border-color: var(--color-accent, #315fcc); color: #fff; }
.engine-choice > span:last-child { display: grid; min-width: 0; }
.engine-choice strong { font-size: .75rem; }
.engine-choice small { color: var(--color-text-muted, #64748b); font-size: .65rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.engine-choice-empty { color: var(--color-text-muted, #64748b); font-size: .74rem; margin: .5rem 0; text-align: center; }
.schedule-preview { align-items: center; background: var(--color-surface, #fff); border: 1px solid var(--color-border, #d9e0ea); border-radius: .65rem; display: flex; gap: 1rem; justify-content: space-between; padding: .8rem; }
.schedule-preview--ready { border-color: color-mix(in srgb, var(--color-accent, #315fcc) 40%, var(--color-border, #d9e0ea)); }
.schedule-preview > div:first-child { display: grid; gap: .14rem; }
.schedule-preview strong { font-size: .82rem; }
.schedule-preview small { color: var(--color-text-muted, #64748b); font-size: .66rem; }
.schedule-preview__actions { display: flex; flex: 0 0 auto; gap: .45rem; }
@media (max-width: 56rem) { .participant-roster__head, .roster-row { grid-template-columns: minmax(10rem, 1fr) repeat(3, 3.5rem) 2.2rem; } .participant-roster--readonly .participant-roster__head, .participant-roster--readonly .roster-row { grid-template-columns: minmax(10rem, 1fr) repeat(3, 3.5rem); } .roster-row__remove span { display: none; } }
@media (max-width: 42rem) { .live-roster__header, .schedule-preview { align-items: stretch; flex-direction: column; } .live-roster__header-actions { justify-content: space-between; } .schedule-preview__actions { justify-content: flex-end; } .participant-roster { max-height: min(30rem, 62vh); } .participant-roster__head { display: none; } .roster-row, .participant-roster--readonly .roster-row { gap: .65rem .5rem; grid-template-columns: repeat(3, 1fr) auto; min-height: 5.2rem; padding-bottom: .6rem; padding-top: .6rem; } .roster-row__identity { grid-column: 1 / 4; grid-row: 1; } .roster-row__identity > div { align-items: start; flex-direction: column; gap: .1rem; } .roster-row__stat { grid-row: 2; } .roster-row__stat::before { color: var(--color-text-muted, #64748b); content: attr(data-label); display: block; font-size: .55rem; font-weight: 650; margin-bottom: .1rem; text-transform: uppercase; } .roster-row__action { grid-column: 4; grid-row: 1; justify-self: end; } }
</style>
