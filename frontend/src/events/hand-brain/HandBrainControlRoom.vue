<script setup lang="ts">
import { computed, ref } from 'vue'
import { api } from '@/api/client'
import type { EventDetailResponse } from '@/types/events'
import type { HandBrainPayload, HandBrainMember } from './types'
import HandBrainBracket from './HandBrainBracket.vue'
const props = defineProps<{ detail: EventDetailResponse }>()
const emit = defineEmits<{ changed: [] }>()
const payload = computed(() => props.detail.custom as HandBrainPayload)
const pending = ref(false)
const error = ref('')
const readyCount = computed(() => payload.value.teams.flatMap(t => t.members).filter(m => m.ready && m.engine_id).length)
const engineCount = computed(() => payload.value.teams.flatMap(t => t.members).length)
const started = computed(() => payload.value.matches.some(m => m.quads.length > 0))
async function action(name: string) {
  pending.value = true; error.value = ''
  try { await api.post(`/api/admin/events/${props.detail.event.id}/hand-brain/${name}`); emit('changed') }
  catch (cause) { error.value = (cause as Error).message }
  finally { pending.value = false }
}
async function selectBuild(member: HandBrainMember, event: Event) {
  const engineId = Number((event.target as HTMLSelectElement).value)
  if (!engineId) return
  await save(member, engineId, false)
}
async function save(member: HandBrainMember, engineId: number, ready: boolean) {
  pending.value = true; error.value = ''
  try { await api.put(`/api/admin/events/${props.detail.event.id}/hand-brain/members/${member.id}`, { body: { engine_id: engineId, searchmoves_confirmed: ready } }); emit('changed') }
  catch (cause) { error.value = (cause as Error).message }
  finally { pending.value = false }
}
</script>
<template>
  <div class="control-room">
    <header class="control-heading"><div><h2>Hand &amp; Brain</h2><p>{{ payload.teams.length }} teams · single elimination · one game at a time</p></div><div class="actions"><RouterLink class="button button--secondary" :to="`/events/${detail.event.slug}/arena`">View arena ↗</RouterLink><button v-if="!payload.matches.length" class="button button--primary" :disabled="pending" @click="action('prepare')">Prepare bracket</button><button v-else-if="!started" class="button button--primary" :disabled="pending || !engineCount || readyCount !== engineCount" @click="action('start')">Start event</button></div></header>
    <p v-if="error" role="alert" class="error">{{ error }}</p>
    <div class="rules"><span><strong>{{ payload.rules.initial_ms / 1000 }}+{{ payload.rules.increment_ms / 1000 }}</strong> per engine</span><span><strong>Four</strong> independent clocks</span><span><strong>1T</strong> per engine</span><span><strong>Lichess UHO</strong> same opening per quad</span><span><strong>&gt;2 points</strong> advances</span></div>
    <p class="rule-note">Games 1–2 reverse colours. Games 3–4 reverse both roles and colours. A 2–2 quad halves both time and increment, using integer milliseconds with a floor of {{ payload.rules.minimum_initial_ms / 1000 }}+{{ payload.rules.minimum_increment_ms / 1000 }}. Your normal draw and resign adjudication applies. All games are unrated.</p>
    <section v-if="payload.teams.length" class="roster"><header><h3>Engine builds</h3><span>{{ readyCount }} / {{ engineCount }} ready</span></header><p>Select each engine’s event build and confirm its restricted-search support before starting.</p><div class="team-builds"><article v-for="team in payload.teams" :key="team.id"><h4><i :style="{ background: team.color }" />{{ team.seed }} · {{ team.name }}</h4><div v-for="member in team.members" :key="member.id" class="member"><label><span>{{ member.name }}</span><select :value="member.engine_id ?? ''" :disabled="pending || started" @change="selectBuild(member, $event)"><option value="" disabled>Select a build</option><option v-for="engine in payload.engine_options?.filter(e => e.name === member.name)" :key="engine.id" :value="engine.id">{{ engine.version }}</option></select></label><label class="ready"><input type="checkbox" :checked="member.ready" :disabled="pending || started || !member.engine_id" @change="save(member, member.engine_id!, ($event.target as HTMLInputElement).checked)">searchmoves verified</label></div></article></div></section>
    <HandBrainBracket v-if="payload.matches.length" :matches="payload.matches" :teams="payload.teams" />
  </div>
</template>
<style scoped>
.control-room{display:grid;gap:20px}.control-heading{display:flex;justify-content:space-between;align-items:center;gap:15px}h2,h3,h4,p{margin:0}.control-heading p,.roster>p{font-size:12px;color:var(--color-text-muted);margin-top:6px}.actions{display:flex;gap:8px;flex-wrap:wrap}.rules{display:flex;gap:20px;flex-wrap:wrap;padding:18px;border:1px solid var(--color-border);border-radius:12px;font-size:12px}.rules strong{display:block;font-size:17px;margin-bottom:5px}.rule-note{font-size:12px;line-height:1.8;color:var(--color-text-muted)}.roster>header{display:flex;justify-content:space-between;align-items:center}.roster>header span{font-size:12px;color:var(--color-text-muted)}.team-builds{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:14px;margin-top:18px}.team-builds article{padding:16px;border:1px solid var(--color-border);border-radius:12px;background:var(--color-surface)}h4{font-size:14px;display:flex;align-items:center;gap:7px}h4 i{width:7px;height:7px;border-radius:50%}.member{display:grid;gap:6px;padding-top:13px}.member label:first-child{display:flex;gap:12px;align-items:center;justify-content:space-between;font-size:12px}.member select{font:inherit;max-width:180px;padding:5px 8px;background:var(--color-bg);color:var(--color-text);border:1px solid var(--color-border);border-radius:6px}.ready{display:flex;align-items:center;gap:6px;font-size:11px;color:var(--color-text-muted)}.error{color:#e6778f;padding:12px;border:1px solid #e6778f44;border-radius:8px}@media(max-width:650px){.control-heading{align-items:start;flex-direction:column}}
</style>
