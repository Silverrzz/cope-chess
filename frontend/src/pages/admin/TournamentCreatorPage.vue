<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { api } from '@/api/client'
import AdminPageHeader from '@/components/admin/AdminPageHeader.vue'
import EngineVersionPicker from '@/components/admin/EngineVersionPicker.vue'
import EngineOptionsEditor from '@/components/admin/EngineOptionsEditor.vue'
import InlineFeedback from '@/components/admin/InlineFeedback.vue'
import TournamentSettingsEditor from '@/components/admin/TournamentSettingsEditor.vue'
import { cloneData, configFromSeed, defaultSettings, errorText, estimateGames, formatNumber, normalizeSettings } from '@/components/admin/format'
import type { FormSeed, TournamentConfig, TournamentSettings } from '@/components/admin/types'
import AppIcon from '@/components/ui/AppIcon.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import { useToast } from '@/composables/useToast'

interface RatingEngine {
  id: number
  name: string
  version: string
  elo: number
  games_played: number
}

interface RatingList {
  id: number
  name: string
  engines: RatingEngine[]
  unavailable_engines: number
}

interface ContextResponse {
  form: FormSeed
  rating_lists: RatingList[]
}

interface DivisionDraft {
  name: string
  engines: RatingEngine[]
}

interface GauntletPreview {
  hero: { id: number; name: string; version: string }
  opponents: Array<RatingEngine & { rating_distance: number; selection_score: number }>
}

interface CreatedDraft {
  id: number
  name: string
  participants: number
}

const toast = useToast()
const context = ref<ContextResponse | null>(null)
const loading = ref(true)
const pending = ref('')
const error = ref('')
const mode = ref<'divisions' | 'gauntlet'>('divisions')
const divisionListId = ref<number | ''>('')
const divisionCount = ref(4)
const divisionNames = ref<string[]>([])
const divisionSettings = ref<TournamentSettings>(defaultSettings())
const gauntletSettings = ref<TournamentSettings>({ ...defaultSettings(), format: 'gauntlet', format_options: { hero_engine_id: 0, cycles: 1 } })
const engineThreads = ref(1)
const engineHashMb = ref(16)
const uciOptions = ref<Record<string, string | number | boolean>>({})
const gauntletListId = ref<number | ''>('')
const heroEngineId = ref<number | ''>('')
const eloEstimate = ref(1500)
const gauntletSize = ref(8)
const roundTotal = ref(1)
const gauntletName = ref('')
const gauntletPreview = ref<GauntletPreview | null>(null)
const createdDrafts = ref<CreatedDraft[]>([])
const draftNames = ref<Record<number, string>>({})
const renamingId = ref<number | null>(null)

const selectedDivisionList = computed(() => context.value?.rating_lists.find((item) => item.id === divisionListId.value) ?? null)
const selectedGauntletList = computed(() => context.value?.rating_lists.find((item) => item.id === gauntletListId.value) ?? null)
const maxDivisions = computed(() => Math.max(1, Math.min(100, Math.floor((selectedDivisionList.value?.engines.length ?? 0) / 2))))

const divisionDrafts = computed<DivisionDraft[]>(() => {
  const engines = selectedDivisionList.value?.engines ?? []
  const count = Math.max(1, Math.min(Math.trunc(divisionCount.value) || 1, engines.length || 1))
  const size = Math.floor(engines.length / count)
  const remainder = engines.length % count
  let offset = 0
  return Array.from({ length: count }, (_, index) => {
    const length = size + (index < remainder ? 1 : 0)
    const group = engines.slice(offset, offset + length)
    offset += length
    return {
      name: divisionNames.value[index] ?? `${selectedDivisionList.value?.name ?? 'Rating list'} Division ${index + 1}`,
      engines: group,
    }
  })
})

const divisionGameTotal = computed(() => divisionDrafts.value.reduce(
  (total, division) => total + estimateGames(divisionSettings.value.format, divisionSettings.value.format_options, division.engines.length),
  0,
))

const gauntletGameTotal = computed(() => Math.max(0, gauntletSize.value - 1) * Math.max(0, roundTotal.value) * 2)

function rebuildDivisionNames(): void {
  const name = selectedDivisionList.value?.name ?? 'Rating list'
  const count = Math.max(1, Math.trunc(divisionCount.value) || 1)
  divisionNames.value = Array.from({ length: count }, (_, index) => `${name} Division ${index + 1}`)
}

function updateDivisionName(index: number, value: string): void {
  divisionNames.value[index] = value
}

function divisionEloRange(draft: DivisionDraft): string {
  const first = draft.engines[0]
  const last = draft.engines[draft.engines.length - 1]
  if (!first || !last) return ''
  return `${Math.round(first.elo)}–${Math.round(last.elo)} Elo`
}

function sharedConfig(settings: TournamentSettings, participants: number[]): TournamentConfig {
  return {
    ...cloneData(settings),
    participants,
    engine_threads: engineThreads.value,
    engine_hash_mb: engineHashMb.value,
    uci_options: cloneData(uciOptions.value),
  }
}

function validateResources(): string {
  if (!Number.isInteger(engineThreads.value) || engineThreads.value < 1) return 'Engine threads must be a whole number of at least 1.'
  if (!Number.isInteger(engineHashMb.value) || engineHashMb.value < 1) return 'Engine hash must be a whole number of at least 1 MB.'
  if (Object.keys(uciOptions.value).some((name) => ['threads', 'hash'].includes(name.trim().toLowerCase()))) return 'Use the thread and hash fields instead of adding Threads or Hash as UCI overrides.'
  return ''
}

async function load(): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    const response = await api.get<ContextResponse>('/api/admin/tools/tournament-creator')
    context.value = response
    const config = configFromSeed(response.form)
    divisionSettings.value = normalizeSettings(config)
    gauntletSettings.value = { ...normalizeSettings(config), format: 'gauntlet', format_options: { hero_engine_id: 0, cycles: 1 } }
    engineThreads.value = config.engine_threads
    engineHashMb.value = config.engine_hash_mb
    uciOptions.value = cloneData(config.uci_options)
    const initialList = response.rating_lists.find((item) => item.engines.length >= 2) ?? response.rating_lists[0]
    divisionListId.value = initialList?.id ?? ''
    gauntletListId.value = initialList?.id ?? ''
    divisionCount.value = Math.min(4, Math.max(1, Math.floor((initialList?.engines.length ?? 0) / 2)))
    const initialHero = response.form.engine_options[0]
    heroEngineId.value = initialHero?.id ?? ''
    if (initialHero) gauntletName.value = `${initialHero.name} ${initialHero.version} Gauntlet`
    rebuildDivisionNames()
  } catch (cause) {
    error.value = errorText(cause)
  } finally {
    loading.value = false
  }
}

async function createDivisions(): Promise<void> {
  error.value = validateResources()
  if (error.value) return
  if (!selectedDivisionList.value || selectedDivisionList.value.engines.length < 2) {
    error.value = 'Choose a rating list with at least two available engines.'
    return
  }
  if (!Number.isInteger(divisionCount.value) || divisionCount.value < 1 || divisionCount.value > maxDivisions.value) {
    error.value = `Choose between 1 and ${maxDivisions.value} divisions so every tournament has at least two engines.`
    return
  }
  if (divisionDrafts.value.some((draft) => !draft.name.trim())) {
    error.value = 'Enter a name for every division.'
    return
  }
  pending.value = 'divisions'
  try {
    const response = await api.post<{ tournaments: CreatedDraft[]; message: string }>('/api/admin/tools/tournament-creator/batch', {
      body: {
        tournaments: divisionDrafts.value.map((draft) => ({
          name: draft.name.trim(),
          config: sharedConfig(divisionSettings.value, draft.engines.map((engine) => engine.id)),
        })),
      },
    })
    setCreatedDrafts(response.tournaments)
    toast.success(response.message)
  } catch (cause) {
    error.value = errorText(cause)
    toast.error(cause)
  } finally {
    pending.value = ''
  }
}

async function buildGauntletPreview(): Promise<void> {
  error.value = ''
  if (gauntletListId.value === '' || heroEngineId.value === '') {
    error.value = 'Choose a rating list and hero engine.'
    return
  }
  if (!Number.isInteger(gauntletSize.value) || gauntletSize.value < 2) {
    error.value = 'Gauntlet size must be a whole number of at least 2.'
    return
  }
  pending.value = 'preview'
  try {
    gauntletPreview.value = await api.post<GauntletPreview>('/api/admin/tools/tournament-creator/gauntlet-preview', {
      body: {
        rating_list_id: gauntletListId.value,
        hero_engine_id: heroEngineId.value,
        elo_estimate: eloEstimate.value,
        gauntlet_size: gauntletSize.value,
      },
    })
  } catch (cause) {
    error.value = errorText(cause)
  } finally {
    pending.value = ''
  }
}

async function createGauntlet(): Promise<void> {
  error.value = validateResources()
  if (error.value) return
  if (!gauntletPreview.value) {
    error.value = 'Build the gauntlet preview before creating the draft.'
    return
  }
  if (!gauntletName.value.trim()) {
    error.value = 'Enter a tournament name.'
    return
  }
  if (!Number.isInteger(roundTotal.value) || roundTotal.value < 1) {
    error.value = 'Round total must be a whole number of at least 1.'
    return
  }
  const participants = [gauntletPreview.value.hero.id, ...gauntletPreview.value.opponents.map((engine) => engine.id)]
  const settings: TournamentSettings = {
    ...cloneData(gauntletSettings.value),
    format: 'gauntlet',
    format_options: { hero_engine_id: gauntletPreview.value.hero.id, cycles: roundTotal.value },
  }
  pending.value = 'gauntlet'
  try {
    const response = await api.post<{ tournaments: CreatedDraft[]; message: string }>('/api/admin/tools/tournament-creator/batch', {
      body: { tournaments: [{ name: gauntletName.value.trim(), config: sharedConfig(settings, participants) }] },
    })
    setCreatedDrafts(response.tournaments)
    toast.success(response.message)
  } catch (cause) {
    error.value = errorText(cause)
    toast.error(cause)
  } finally {
    pending.value = ''
  }
}

function setCreatedDrafts(drafts: CreatedDraft[]): void {
  createdDrafts.value = drafts
  draftNames.value = Object.fromEntries(drafts.map((draft) => [draft.id, draft.name]))
  window.scrollTo({ top: 0, behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' })
}

async function renameDraft(draft: CreatedDraft): Promise<void> {
  const name = draftNames.value[draft.id]?.trim()
  if (!name || name === draft.name) return
  renamingId.value = draft.id
  try {
    const response = await api.put<{ message: string }>(`/api/admin/tournaments/${draft.id}/name`, { body: { name } })
    draft.name = name
    toast.success(response.message)
  } catch (cause) {
    error.value = errorText(cause)
    toast.error(cause)
  } finally {
    renamingId.value = null
  }
}

watch([divisionListId, divisionCount], rebuildDivisionNames)
watch([gauntletListId, heroEngineId, eloEstimate, gauntletSize], () => { gauntletPreview.value = null })
watch(heroEngineId, (current, previous) => {
  const previousEngine = context.value?.form.engine_options.find((engine) => engine.id === previous)
  const previousDefault = previousEngine ? `${previousEngine.name} ${previousEngine.version} Gauntlet` : ''
  if (gauntletName.value && gauntletName.value !== previousDefault) return
  const currentEngine = context.value?.form.engine_options.find((engine) => engine.id === current)
  gauntletName.value = currentEngine ? `${currentEngine.name} ${currentEngine.version} Gauntlet` : ''
})
onMounted(load)
</script>

<template>
  <div class="admin-page page-stack creator-page">
    <AdminPageHeader title="Tournament creator">
      <template #actions><BaseButton variant="ghost" to="/admin/tools"><template #icon><AppIcon name="arrow-left" :size="16" /></template>All tools</BaseButton></template>
    </AdminPageHeader>

    <InlineFeedback :message="error" />

    <section v-if="createdDrafts.length" class="panel created-panel" aria-labelledby="created-drafts-title">
      <div class="created-panel__heading">
        <span class="created-panel__icon"><AppIcon name="check-circle" :size="20" /></span>
        <div><h2 id="created-drafts-title">Drafts created</h2><p>Rename them here or open any draft to edit its participants and settings. Nothing has been scheduled or started.</p></div>
      </div>
      <div class="created-list">
        <form v-for="draft in createdDrafts" :key="draft.id" class="created-row" @submit.prevent="renameDraft(draft)">
          <span class="created-row__status">Draft</span>
          <input v-model="draftNames[draft.id]" class="input" maxlength="160" :aria-label="`Name for tournament ${draft.id}`">
          <span class="created-row__meta">{{ draft.participants }} engines</span>
          <button class="button button--secondary button--small" type="submit" :disabled="renamingId === draft.id || !draftNames[draft.id]?.trim() || draftNames[draft.id]?.trim() === draft.name">{{ renamingId === draft.id ? 'Saving…' : 'Rename' }}</button>
          <RouterLink class="button button--ghost button--small" :to="`/admin/tournaments/${draft.id}`">Edit details</RouterLink>
        </form>
      </div>
    </section>

    <div v-if="loading" class="panel loading" role="status">Loading tournament creator…</div>
    <template v-else-if="context">
      <div class="mode-switch" role="radiogroup" aria-label="Tournament creator mode">
        <button type="button" role="radio" :aria-checked="mode === 'divisions'" :class="{ active: mode === 'divisions' }" @click="mode = 'divisions'">
          <AppIcon name="trophy" :size="20" /><span><strong>Division creator</strong><small>Split a rating list into balanced divisions</small></span>
        </button>
        <button type="button" role="radio" :aria-checked="mode === 'gauntlet'" :class="{ active: mode === 'gauntlet' }" @click="mode = 'gauntlet'">
          <AppIcon name="gauge" :size="20" /><span><strong>Gauntlet creator</strong><small>Match a hero around an estimated Elo</small></span>
        </button>
      </div>

      <template v-if="mode === 'divisions'">
        <section class="panel creator-panel">
          <div class="panel-title"><span class="step">1</span><div><h2>Choose the field</h2><p>Engines stay in rating order and are distributed as evenly as possible.</p></div></div>
          <div class="setup-grid">
            <label class="field"><span>Rating list</span><select v-model.number="divisionListId" class="input"><option disabled value="">Select a list</option><option v-for="list in context.rating_lists" :key="list.id" :value="list.id" :disabled="list.engines.length < 2">{{ list.name }} · {{ list.engines.length }} available</option></select></label>
            <label class="field"><span>Number of divisions</span><input v-model.number="divisionCount" class="input" type="number" min="1" :max="maxDivisions" step="1"><small>Maximum {{ maxDivisions }} so every division has at least two engines.</small></label>
          </div>
          <p v-if="selectedDivisionList?.unavailable_engines" class="availability-note"><AppIcon name="info" :size="15" /> {{ selectedDivisionList.unavailable_engines }} inactive or unbenchmarked engine{{ selectedDivisionList.unavailable_engines === 1 ? '' : 's' }} cannot be drafted and {{ selectedDivisionList.unavailable_engines === 1 ? 'is' : 'are' }} excluded.</p>
        </section>

        <section v-if="selectedDivisionList?.engines.length" class="panel division-preview">
          <div class="preview-heading"><div><span class="eyebrow">Batch preview</span><h2>{{ divisionDrafts.length }} draft{{ divisionDrafts.length === 1 ? '' : 's' }}</h2></div><strong>{{ formatNumber(divisionGameTotal) }} estimated games</strong></div>
          <div class="division-list">
            <article v-for="(draft, index) in divisionDrafts" :key="index" class="division-row">
              <span class="division-row__rank">D{{ index + 1 }}</span>
              <div class="division-row__main">
                <input class="input" maxlength="160" :value="draft.name" :aria-label="`Division ${index + 1} tournament name`" @input="updateDivisionName(index, ($event.target as HTMLInputElement).value)">
                <div class="engine-strip"><span v-for="engine in draft.engines" :key="engine.id">{{ engine.name }} {{ engine.version }} <small>{{ Math.round(engine.elo) }}</small></span></div>
              </div>
              <div class="division-row__range"><strong>{{ draft.engines.length }}</strong><span>engines</span><small v-if="draft.engines.length">{{ divisionEloRange(draft) }}</small></div>
            </article>
          </div>
        </section>

        <section class="panel creator-panel">
          <div class="panel-title"><span class="step">2</span><div><h2>Shared tournament settings</h2><p>These settings are applied identically to every division draft.</p></div></div>
          <TournamentSettingsEditor v-model="divisionSettings" :engines="context.form.engine_options" :opening-suites="context.form.opening_suites" :allow-gauntlet="false" />
        </section>

        <section class="panel creator-panel">
          <div class="panel-title"><span class="step">3</span><div><h2>Shared engine resources</h2><p>Threads, hash, and UCI options are copied to every division.</p></div></div>
          <div class="setup-grid">
            <label class="field"><span>Threads per engine</span><input v-model.number="engineThreads" class="input" type="number" min="1" step="1"></label>
            <label class="field"><span>Hash per engine <small>MB</small></span><input v-model.number="engineHashMb" class="input" type="number" min="1" step="1"></label>
          </div>
          <EngineOptionsEditor v-model="uciOptions" />
        </section>

        <div class="creator-actions"><span>All tournaments will be saved as editable drafts.</span><button class="button button--primary" type="button" :disabled="pending === 'divisions' || !divisionDrafts.length" @click="createDivisions"><span v-if="pending === 'divisions'" class="button-spinner" />{{ pending === 'divisions' ? 'Creating drafts…' : `Create ${divisionDrafts.length} draft${divisionDrafts.length === 1 ? '' : 's'}` }}</button></div>
      </template>

      <template v-else>
        <section class="panel creator-panel">
          <div class="panel-title"><span class="step">1</span><div><h2>Build the field</h2><p>Every available engine on the list is ranked by relative Elo proximity and game coverage, with no fixed rating band.</p></div></div>
          <div class="gauntlet-grid">
            <label class="field"><span>Rating list</span><select v-model.number="gauntletListId" class="input"><option disabled value="">Select a list</option><option v-for="list in context.rating_lists" :key="list.id" :value="list.id">{{ list.name }} · {{ list.engines.length }} available</option></select></label>
            <label class="field"><span>Hero engine</span><EngineVersionPicker v-model="heroEngineId" :engines="context.form.engine_options" placeholder="Select a hero" /></label>
            <label class="field"><span>Estimated hero Elo</span><input v-model.number="eloEstimate" class="input" type="number" min="-10000" max="10000" step="1"></label>
            <label class="field"><span>Gauntlet size</span><input v-model.number="gauntletSize" class="input" type="number" min="2" :max="Math.min(500, (selectedGauntletList?.engines.length ?? 0) + (selectedGauntletList?.engines.some((engine) => engine.id === heroEngineId) ? 0 : 1))" step="1"><small>Includes the hero; {{ Math.max(0, gauntletSize - 1) }} opponents will be selected.</small></label>
            <label class="field"><span>Round total</span><input v-model.number="roundTotal" class="input" type="number" min="1" step="1"><small>Each round gives the hero a colour-balanced pair against every opponent.</small></label>
            <label class="field field--wide"><span>Tournament name</span><input v-model="gauntletName" class="input" maxlength="160"></label>
          </div>
          <div class="preview-action"><span>{{ formatNumber(gauntletGameTotal) }} estimated games</span><button class="button button--secondary" type="button" :disabled="pending === 'preview'" @click="buildGauntletPreview">{{ pending === 'preview' ? 'Selecting field…' : gauntletPreview ? 'Rebuild field' : 'Build field preview' }}</button></div>
        </section>

        <section v-if="gauntletPreview" class="panel gauntlet-preview">
          <div class="preview-heading"><div><span class="eyebrow">Selected field</span><h2>{{ gauntletPreview.hero.name }} {{ gauntletPreview.hero.version }} versus {{ gauntletPreview.opponents.length }} opponents</h2></div><span class="draft-pill">Draft only</span></div>
          <div class="opponent-list">
            <div v-for="(engine, index) in gauntletPreview.opponents" :key="engine.id" class="opponent-row">
              <span class="opponent-row__rank">{{ index + 1 }}</span>
              <span class="opponent-row__name"><strong>{{ engine.name }}</strong><small>{{ engine.version }}</small></span>
              <span><strong>{{ Math.round(engine.elo) }}</strong><small>Elo</small></span>
              <span><strong>{{ formatNumber(engine.games_played) }}</strong><small>games</small></span>
              <span><strong>Δ {{ Math.round(engine.rating_distance) }}</strong><small>from estimate</small></span>
            </div>
          </div>
        </section>

        <section class="panel creator-panel">
          <div class="panel-title"><span class="step">2</span><div><h2>Tournament settings</h2><p>The gauntlet structure is fixed above; configure how its games will be played.</p></div></div>
          <TournamentSettingsEditor v-model="gauntletSettings" :engines="context.form.engine_options" :opening-suites="context.form.opening_suites" :allow-format="false" />
        </section>

        <section class="panel creator-panel">
          <div class="panel-title"><span class="step">3</span><div><h2>Engine resources</h2></div></div>
          <div class="setup-grid">
            <label class="field"><span>Threads per engine</span><input v-model.number="engineThreads" class="input" type="number" min="1" step="1"></label>
            <label class="field"><span>Hash per engine <small>MB</small></span><input v-model.number="engineHashMb" class="input" type="number" min="1" step="1"></label>
          </div>
          <EngineOptionsEditor v-model="uciOptions" />
        </section>

        <div class="creator-actions"><span>The tournament will not be scheduled or started.</span><button class="button button--primary" type="button" :disabled="pending === 'gauntlet' || !gauntletPreview" @click="createGauntlet"><span v-if="pending === 'gauntlet'" class="button-spinner" />{{ pending === 'gauntlet' ? 'Creating draft…' : 'Create gauntlet draft' }}</button></div>
      </template>
    </template>
  </div>
</template>

<style scoped>
.creator-page{gap:1.25rem}.loading{color:var(--color-text-muted);min-height:18rem;padding:2rem}.mode-switch{display:grid;gap:.75rem;grid-template-columns:repeat(2,minmax(0,1fr))}.mode-switch button{align-items:center;background:var(--color-surface);border:1px solid var(--color-border);border-radius:var(--radius-lg);color:var(--color-text-muted);cursor:pointer;display:flex;gap:.8rem;padding:1rem;text-align:left;transition:border-color var(--transition-fast),background-color var(--transition-fast),color var(--transition-fast)}.mode-switch button:hover{border-color:var(--color-border-strong);color:var(--color-text)}.mode-switch button.active{background:var(--color-accent-soft);border-color:color-mix(in srgb,var(--color-accent) 45%,var(--color-border));color:var(--color-accent)}.mode-switch span{display:grid;gap:.2rem}.mode-switch strong{font-size:.86rem}.mode-switch small{color:var(--color-text-muted);font-size:.7rem}.creator-panel{display:grid;gap:1.25rem;padding:clamp(1rem,2vw,1.4rem)}.panel-title{align-items:flex-start;display:flex;gap:.75rem}.panel-title h2,.preview-heading h2,.created-panel h2{font-size:1rem;margin:0}.panel-title p,.created-panel p{color:var(--color-text-muted);font-size:.74rem;line-height:1.5;margin:.25rem 0 0}.step{align-items:center;background:var(--color-accent-soft);border-radius:50%;color:var(--color-accent);display:flex;flex:0 0 auto;font-size:.7rem;font-weight:750;height:1.65rem;justify-content:center;width:1.65rem}.setup-grid,.gauntlet-grid{display:grid;gap:.9rem;grid-template-columns:repeat(2,minmax(0,1fr))}.gauntlet-grid{grid-template-columns:repeat(3,minmax(0,1fr))}.field--wide{grid-column:span 2}.availability-note{align-items:center;background:color-mix(in srgb,var(--color-warning) 8%,transparent);border:1px solid color-mix(in srgb,var(--color-warning) 25%,var(--color-border));border-radius:var(--radius-md);color:var(--color-text-muted);display:flex;font-size:.72rem;gap:.45rem;margin:0;padding:.65rem .75rem}.division-preview,.gauntlet-preview,.created-panel{overflow:hidden;padding:0}.preview-heading{align-items:center;background:var(--color-surface-sunken);border-bottom:1px solid var(--color-border);display:flex;justify-content:space-between;padding:.9rem 1rem}.preview-heading>strong{color:var(--color-accent);font-size:.78rem}.eyebrow{color:var(--color-accent);font-size:.6rem;font-weight:750;letter-spacing:.1em;text-transform:uppercase}.preview-heading h2{margin-top:.22rem}.division-list,.opponent-list,.created-list{display:grid}.division-row{align-items:start;display:grid;gap:.8rem;grid-template-columns:auto minmax(0,1fr) auto;padding:.9rem 1rem}.division-row+.division-row,.opponent-row+.opponent-row,.created-row+.created-row{border-top:1px solid var(--color-border)}.division-row__rank{align-items:center;background:var(--color-accent-soft);border-radius:.45rem;color:var(--color-accent);display:flex;font-size:.68rem;font-weight:750;height:2.15rem;justify-content:center;width:2.15rem}.division-row__main{display:grid;gap:.55rem}.division-row__main>.input{font-weight:650}.engine-strip{display:flex;flex-wrap:wrap;gap:.3rem}.engine-strip>span{background:var(--color-surface-sunken);border-radius:999px;color:var(--color-text-muted);font-size:.62rem;padding:.28rem .45rem}.engine-strip small{color:var(--color-text-faint);font-size:.58rem}.division-row__range{display:grid;justify-items:end;min-width:5rem}.division-row__range strong{font-size:1rem}.division-row__range span,.division-row__range small{color:var(--color-text-muted);font-size:.62rem}.creator-actions{align-items:center;display:flex;gap:1rem;justify-content:flex-end;padding:.25rem 0 .75rem}.creator-actions>span,.preview-action>span{color:var(--color-text-muted);font-size:.72rem}.preview-action{align-items:center;border-top:1px solid var(--color-border);display:flex;justify-content:flex-end;gap:1rem;padding-top:1rem}.draft-pill,.created-row__status{background:color-mix(in srgb,var(--color-warning) 10%,transparent);border-radius:999px;color:var(--color-warning);font-size:.62rem;font-weight:720;padding:.28rem .5rem}.opponent-row{align-items:center;display:grid;gap:.7rem;grid-template-columns:2rem minmax(0,1fr) repeat(3,minmax(5rem,auto));padding:.7rem 1rem}.opponent-row__rank{color:var(--color-text-faint);font-size:.7rem;font-weight:720}.opponent-row>span{display:grid;gap:.12rem}.opponent-row strong{font-size:.72rem}.opponent-row small{color:var(--color-text-muted);font-size:.6rem}.created-panel__heading{align-items:flex-start;background:color-mix(in srgb,var(--color-success) 7%,var(--color-surface-sunken));border-bottom:1px solid color-mix(in srgb,var(--color-success) 18%,var(--color-border));display:flex;gap:.7rem;padding:1rem}.created-panel__icon{color:var(--color-success)}.created-row{align-items:center;display:grid;gap:.65rem;grid-template-columns:auto minmax(12rem,1fr) auto auto auto;padding:.7rem 1rem}.created-row__meta{color:var(--color-text-muted);font-size:.65rem}@media(max-width:54rem){.gauntlet-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.opponent-row{grid-template-columns:2rem minmax(0,1fr) repeat(2,minmax(4.5rem,auto))}.opponent-row>span:last-child{display:none}.created-row{grid-template-columns:auto minmax(0,1fr) auto}.created-row__meta{display:none}.created-row .button{grid-row:2}.created-row .button--secondary{grid-column:2}.created-row .button--ghost{grid-column:3}}@media(max-width:38rem){.mode-switch,.setup-grid,.gauntlet-grid{grid-template-columns:1fr}.field--wide{grid-column:auto}.division-row{grid-template-columns:auto minmax(0,1fr)}.division-row__range{grid-column:2;grid-row:2;justify-items:start}.opponent-row{grid-template-columns:1.5rem minmax(0,1fr) auto}.opponent-row>span:nth-last-child(-n+2){display:none}.creator-actions,.preview-action{align-items:stretch;flex-direction:column}.creator-actions .button,.preview-action .button{width:100%}.created-row{grid-template-columns:minmax(0,1fr) auto}.created-row__status{display:none}.created-row .button--secondary{grid-column:1}.created-row .button--ghost{grid-column:2}}
</style>
