<script setup lang="ts">
import { computed, ref, watch } from 'vue'
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
}>()

const emit = defineEmits<{
  add: [engineId: number]
  remove: [participant: LiveParticipantSummary]
}>()

const addOpen = ref(false)
const query = ref('')
const selectedEngineId = ref<number | null>(null)

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
const scheduledGameEstimate = computed(() => {
  if (props.config.format === 'round_robin' && 'cycles' in props.config.format_options) {
    return props.config.participants.length * props.config.format_options.cycles * 2
  }
  return 2
})

watch(() => props.roster.available_engines, (engines) => {
  if (selectedEngineId.value !== null && !engines.some((engine) => engine.id === selectedEngineId.value)) {
    selectedEngineId.value = null
  }
})

function engineLabel(engineId: number): string {
  return props.engineLabels[engineId] ?? `Engine ${engineId}`
}

function openAddition(): void {
  addOpen.value = true
  query.value = ''
  selectedEngineId.value = null
}

function closeAddition(): void {
  addOpen.value = false
  query.value = ''
  selectedEngineId.value = null
}

function scheduleSelected(): void {
  if (selectedEngineId.value === null) return
  emit('add', selectedEngineId.value)
  closeAddition()
}
</script>

<template>
  <section class="panel live-roster">
    <header class="live-roster__header">
      <div class="live-roster__title">
        <span class="live-roster__icon"><AppIcon name="engine" :size="18" /></span>
        <div>
          <h2>Live participants</h2>
          <p v-if="roster.editable">Change the field without stopping the tournament. Schedule updates take effect immediately.</p>
          <p v-else>{{ roster.reason }}</p>
        </div>
      </div>
      <div class="live-roster__header-actions">
        <span class="live-roster__count">{{ config.participants.length }} engines</span>
        <button v-if="roster.editable" class="button button--primary button--small" type="button" :disabled="!!pending || !roster.available_engines.length" @click="openAddition">
          <AppIcon name="plus" :size="15" /> Add engine
        </button>
      </div>
    </header>

    <div class="participant-roster">
      <article v-for="participant in roster.participants" :key="participant.engine_id" class="roster-card" :class="{ 'roster-card--hero': participant.engine_id === roster.hero_engine_id }">
        <div class="roster-card__identity">
          <span class="roster-card__avatar"><AppIcon name="engine" :size="17" /></span>
          <div>
            <strong>{{ engineLabel(participant.engine_id) }}</strong>
            <span v-if="participant.engine_id === roster.hero_engine_id" class="hero-badge">Gauntlet hero</span>
          </div>
        </div>
        <dl class="roster-card__stats">
          <div><dt>Finished</dt><dd>{{ participant.finished }}</dd></div>
          <div><dt>Queued</dt><dd>{{ participant.pending }}</dd></div>
          <div :class="{ active: participant.assigned + participant.live > 0 }"><dt>Active</dt><dd>{{ participant.assigned + participant.live }}</dd></div>
        </dl>
        <button v-if="roster.editable" class="button button--ghost button--small roster-card__remove" type="button" :disabled="!!pending || config.participants.length <= 2" :title="config.participants.length <= 2 ? 'A tournament must keep at least two participants.' : `Remove ${engineLabel(participant.engine_id)}`" @click="emit('remove', participant)">
          <AppIcon name="trash" :size="14" /> Remove
        </button>
      </article>
    </div>

    <div v-if="addOpen" class="add-engine-menu">
      <div class="add-engine-menu__top">
        <div>
          <span>Add a live participant</span>
          <h3>Choose an engine version</h3>
        </div>
        <button class="icon-button" type="button" aria-label="Close engine picker" @click="closeAddition"><AppIcon name="close" :size="18" /></button>
      </div>
      <label class="engine-search">
        <AppIcon name="search" :size="16" />
        <input v-model="query" class="input" type="search" placeholder="Search engines, versions, or authors" autofocus>
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
.participant-roster { display: grid; gap: .65rem; grid-template-columns: repeat(auto-fill, minmax(min(100%, 19rem), 1fr)); padding: .85rem 1rem 1rem; }
.roster-card { background: var(--color-surface-subtle, #f8fafc); border: 1px solid var(--color-border, #d9e0ea); border-radius: .65rem; display: grid; gap: .7rem; grid-template-columns: minmax(0, 1fr) auto; padding: .75rem; }
.roster-card--hero { border-color: color-mix(in srgb, var(--color-accent, #315fcc) 45%, var(--color-border, #d9e0ea)); }
.roster-card__identity { align-items: center; display: flex; gap: .55rem; min-width: 0; }
.roster-card__avatar { align-items: center; background: var(--color-surface, #fff); border: 1px solid var(--color-border, #d9e0ea); border-radius: .5rem; color: var(--color-text-muted, #64748b); display: inline-flex; flex: 0 0 auto; height: 2rem; justify-content: center; width: 2rem; }
.roster-card__identity > div { display: grid; gap: .2rem; min-width: 0; }
.roster-card__identity strong { font-size: .78rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.hero-badge { color: var(--color-accent, #315fcc); font-size: .62rem; font-weight: 700; text-transform: uppercase; }
.roster-card__stats { display: flex; gap: .65rem; grid-column: 1 / -1; margin: 0; }
.roster-card__stats div { min-width: 3.2rem; }
.roster-card__stats dt { color: var(--color-text-muted, #64748b); font-size: .59rem; text-transform: uppercase; }
.roster-card__stats dd { font-size: .75rem; font-weight: 750; margin: .08rem 0 0; }
.roster-card__stats .active dd { color: var(--color-accent, #315fcc); }
.roster-card__remove { align-self: start; align-items: center; color: var(--color-danger, #b42318); display: inline-flex; gap: .25rem; }
.add-engine-menu { background: color-mix(in srgb, var(--color-accent, #315fcc) 3%, var(--color-surface, #fff)); border-top: 1px solid var(--color-border, #d9e0ea); display: grid; gap: .8rem; padding: 1rem; }
.add-engine-menu__top { align-items: center; display: flex; justify-content: space-between; }
.add-engine-menu__top span, .schedule-preview span { color: var(--color-text-muted, #64748b); font-size: .62rem; font-weight: 700; letter-spacing: .04em; text-transform: uppercase; }
.add-engine-menu h3 { font-size: .86rem; margin-top: .15rem; }
.engine-search { position: relative; }
.engine-search > :first-child { color: var(--color-text-muted, #64748b); left: .75rem; pointer-events: none; position: absolute; top: 50%; transform: translateY(-50%); }
.engine-search .input { padding-left: 2.2rem; width: 100%; }
.engine-choice-grid { display: grid; gap: .5rem; grid-template-columns: repeat(auto-fill, minmax(min(100%, 15rem), 1fr)); max-height: 19rem; overflow: auto; padding: .1rem; }
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
@media (max-width: 42rem) { .live-roster__header, .schedule-preview { align-items: stretch; flex-direction: column; } .live-roster__header-actions { justify-content: space-between; } .schedule-preview__actions { justify-content: flex-end; } }
</style>
