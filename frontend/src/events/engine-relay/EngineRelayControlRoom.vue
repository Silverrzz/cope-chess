<script setup lang="ts">
import { computed, reactive, ref, watch } from "vue";

import { api } from "@/api/client";
import InlineFeedback from "@/components/admin/InlineFeedback.vue";
import ParticipantPicker from "@/components/admin/ParticipantPicker.vue";
import StatusBadge from "@/components/admin/StatusBadge.vue";
import AppIcon from "@/components/ui/AppIcon.vue";
import { errorText, formatDate } from "@/components/admin/format";
import { useToast } from "@/composables/useToast";
import type { EventDetailResponse } from "@/types/events";

import type { EngineRelayPayload, RelayFixture, RelayRosterMember, RelayTeam } from "./types";

const props = defineProps<{ detail: EventDetailResponse }>();
const emit = defineEmits<{ changed: [] }>();
const toast = useToast();
const payload = computed(() => props.detail.custom as EngineRelayPayload);
const pending = ref("");
const error = ref("");
const showTeamCreator = ref(false);
const activeView = ref<"overview" | "teams" | "fixtures">("overview");
const editingTeamId = ref<number | null>(null);
const showFixtureBuilder = ref(false);
const showAdvancedFixture = ref(false);
const visibility = reactive({ published: !!props.detail.event.published_at });
const newTeam = reactive({ name: "", short_name: "", primary_color: "#315fcc", secondary_color: "#8fb3ff", profile: "", motto: "" });
const fixtureForm = reactive({
  title: "",
  team_ids: [] as number[],
  cycles: 1,
  opening_suite_id: "",
  concurrency: 1,
  time_seconds: 60,
  increment_seconds: 1,
  lag_compensation_ms: 50,
  max_moves: 200,
  scheduled_start_at: "",
});
const memberDrafts = reactive<Record<number, { engine_id: number; threads: number; hash_mb: number; position: number; label: string }>>({});
const schedules = reactive<Record<number, string>>({});

const readyTeams = computed(() => payload.value.teams.filter((team) => team.roster.length));
const projectedGames = computed(() => {
  const teamCount = fixtureForm.team_ids.length;
  return teamCount < 2 ? 0 : teamCount * (teamCount - 1) * Math.max(1, Number(fixtureForm.cycles) || 1);
});
const fixtureSelectionValid = computed(() => fixtureForm.team_ids.length >= 2);

watch(
  () => props.detail.event.published_at,
  (value) => { visibility.published = !!value; },
);

function memberDraft(team: RelayTeam) {
  return memberDrafts[team.id] ??= {
    engine_id: 0,
    threads: 1,
    hash_mb: 256,
    position: team.roster.length,
    label: "",
  };
}

function teamBody(team: RelayTeam) {
  return {
    name: team.name,
    short_name: team.short_name,
    primary_color: team.primary_color,
    secondary_color: team.secondary_color,
    profile: team.profile,
    motto: team.motto,
  };
}

async function run(key: string, action: () => Promise<{ message?: string }>): Promise<void> {
  if (pending.value) return;
  pending.value = key;
  error.value = "";
  try {
    const response = await action();
    if (response.message) toast.success(response.message);
    emit("changed");
  } catch (cause) {
    error.value = errorText(cause);
    toast.error(cause);
  } finally {
    pending.value = "";
  }
}

function saveVisibility(): void {
  void run("visibility", () => api.put(`/api/admin/events/${props.detail.event.id}/engine-relay/visibility`, { body: visibility }));
}

function saveTeam(team: RelayTeam): void {
  void run(`team-${team.id}`, () => api.put(`/api/admin/events/${props.detail.event.id}/engine-relay/teams/${team.id}`, { body: teamBody(team) }));
}

function createTeam(): void {
  if (!newTeam.name.trim()) return;
  void run("team-new", async () => {
    const response = await api.post<{ message?: string }>(`/api/admin/events/${props.detail.event.id}/engine-relay/teams`, { body: newTeam });
    Object.assign(newTeam, { name: "", short_name: "", primary_color: "#315fcc", secondary_color: "#8fb3ff", profile: "", motto: "" });
    showTeamCreator.value = false;
    return response;
  });
}

function deleteTeam(team: RelayTeam): void {
  if (!window.confirm(`Delete ${team.name} and its roster?`)) return;
  void run(`team-delete-${team.id}`, () => api.delete(`/api/admin/events/${props.detail.event.id}/engine-relay/teams/${team.id}`));
}

function addMember(team: RelayTeam): void {
  const draft = memberDraft(team);
  if (!draft.engine_id) return;
  void run(`member-new-${team.id}`, async () => {
    const response = await api.post<{ message?: string }>(`/api/admin/events/${props.detail.event.id}/engine-relay/teams/${team.id}/members`, { body: draft });
    memberDrafts[team.id] = { engine_id: 0, threads: 1, hash_mb: 256, position: team.roster.length + 1, label: "" };
    return response;
  });
}

function selectMemberEngine(team: RelayTeam, selection: number[]): void {
  memberDraft(team).engine_id = selection[0] ?? 0;
}

function saveMember(team: RelayTeam, member: RelayRosterMember): void {
  void run(`member-${member.id}`, () => api.put(`/api/admin/events/${props.detail.event.id}/engine-relay/teams/${team.id}/members/${member.id}`, {
    body: {
      engine_id: Number(member.engine_id),
      threads: Number(member.threads),
      hash_mb: Number(member.hash_mb),
      position: Number(member.position),
      label: member.label,
    },
  }));
}

function deleteMember(team: RelayTeam, member: RelayRosterMember): void {
  if (!window.confirm(`Remove ${member.display_name} from ${team.name}?`)) return;
  void run(`member-delete-${member.id}`, () => api.delete(`/api/admin/events/${props.detail.event.id}/engine-relay/teams/${team.id}/members/${member.id}`));
}

function toggleFixtureTeam(teamId: number): void {
  const index = fixtureForm.team_ids.indexOf(teamId);
  if (index < 0) fixtureForm.team_ids.push(teamId);
  else fixtureForm.team_ids.splice(index, 1);
}

function selectAllFixtureTeams(): void {
  fixtureForm.team_ids = readyTeams.value.map((team) => team.id);
}

function fixtureTeamNames(fixture: RelayFixture): string {
  if (fixture.teams?.length) return fixture.teams.map((team) => team.name).join(" · ");
  return `${fixture.team_a_name} vs ${fixture.team_b_name}`;
}

function fixtureTeamCount(fixture: RelayFixture): number {
  return fixture.teams?.length ?? 2;
}

function createFixture(): void {
  if (!fixtureForm.title.trim() || !fixtureSelectionValid.value) return;
  void run("fixture-new", async () => {
    const response = await api.post<{ message?: string }>(`/api/admin/events/${props.detail.event.id}/engine-relay/fixtures`, {
      body: {
        title: fixtureForm.title,
        team_ids: fixtureForm.team_ids.map(Number),
        cycles: Number(fixtureForm.cycles),
        opening_suite_id: fixtureForm.opening_suite_id ? Number(fixtureForm.opening_suite_id) : null,
        concurrency: Number(fixtureForm.concurrency),
        initial_ms: Math.round(Number(fixtureForm.time_seconds) * 1000),
        increment_ms: Math.round(Number(fixtureForm.increment_seconds) * 1000),
        lag_compensation_ms: Number(fixtureForm.lag_compensation_ms),
        adjudication: { draw: null, resign: null, max_moves: fixtureForm.max_moves ? Number(fixtureForm.max_moves) : null },
        scheduled_start_at: fixtureForm.scheduled_start_at ? new Date(fixtureForm.scheduled_start_at).toISOString() : null,
      },
    });
    Object.assign(fixtureForm, { title: "", team_ids: [], cycles: 1, opening_suite_id: "", scheduled_start_at: "" });
    return response;
  });
}

function scheduleFixture(fixture: RelayFixture): void {
  const value = schedules[fixture.id];
  if (!value) return;
  void run(`fixture-schedule-${fixture.id}`, () => api.post(`/api/admin/events/${props.detail.event.id}/engine-relay/fixtures/${fixture.id}/schedule`, { body: { scheduled_start_at: new Date(value).toISOString() } }));
}

function unscheduleFixture(fixture: RelayFixture): void {
  void run(`fixture-unschedule-${fixture.id}`, () => api.delete(`/api/admin/events/${props.detail.event.id}/engine-relay/fixtures/${fixture.id}/schedule`));
}

function startFixture(fixture: RelayFixture): void {
  if (!window.confirm(`Start ${fixture.title} now?`)) return;
  void run(`fixture-start-${fixture.id}`, () => api.post(`/api/admin/events/${props.detail.event.id}/engine-relay/fixtures/${fixture.id}/start`));
}

function deleteFixture(fixture: RelayFixture): void {
  if (!window.confirm(`Delete ${fixture.title}? Its unstarted tournament and generated games will be removed.`)) return;
  void run(`fixture-delete-${fixture.id}`, () => api.delete(`/api/admin/events/${props.detail.event.id}/engine-relay/fixtures/${fixture.id}`));
}

function fixtureCycles(fixture: RelayFixture): number | string {
  const options = fixture.tournament?.config?.format_options as { cycles?: number } | undefined;
  return options?.cycles ?? "—";
}
</script>

<template>
  <section class="relay-control">
    <InlineFeedback :message="error" />

    <section class="relay-toolbar panel">
      <nav aria-label="Event workspace">
        <button type="button" :class="{ active: activeView === 'overview' }" @click="activeView = 'overview'"><AppIcon name="gauge" :size="17" />Overview</button>
        <button type="button" :class="{ active: activeView === 'teams' }" @click="activeView = 'teams'"><AppIcon name="engine" :size="17" />Teams <span>{{ payload.teams.length }}</span></button>
        <button type="button" :class="{ active: activeView === 'fixtures' }" @click="activeView = 'fixtures'"><AppIcon name="trophy" :size="17" />Fixtures <span>{{ payload.fixtures.length }}</span></button>
      </nav>
      <form class="visibility-control" @submit.prevent="saveVisibility"><div class="lifecycle-status"><span>Lifecycle</span><StatusBadge :status="detail.event.status" /></div><label class="publish-toggle"><input v-model="visibility.published" type="checkbox"><span>{{ visibility.published ? 'Public' : 'Private' }}</span></label><button class="button button--primary button--small" type="submit" :disabled="!!pending">{{ pending === 'visibility' ? 'Saving…' : 'Save' }}</button></form>
    </section>

    <section v-if="activeView === 'overview'" class="overview-grid">
      <button class="panel overview-card overview-card--primary" type="button" @click="activeView = 'teams'"><span class="overview-card__icon"><AppIcon name="engine" :size="20" /></span><span><small>Teams</small><strong>{{ payload.teams.length }}</strong></span><AppIcon name="arrow-right" :size="18" /></button>
      <button class="panel overview-card" type="button" @click="activeView = 'fixtures'"><span class="overview-card__icon"><AppIcon name="trophy" :size="20" /></span><span><small>Fixtures</small><strong>{{ payload.fixtures.length }}</strong></span><AppIcon name="arrow-right" :size="18" /></button>
      <section class="panel event-health"><header><h2>Event setup</h2><StatusBadge :status="detail.event.status" /></header><dl><div><dt>Visibility</dt><dd>{{ detail.event.published_at ? 'Public' : 'Private' }}</dd></div><div><dt>Scheduler</dt><dd>Standard</dd></div><div><dt>Ratings</dt><dd>Unrated</dd></div><div><dt>Relay module</dt><dd>v1</dd></div></dl></section>
    </section>

    <section v-show="activeView === 'teams'" class="relay-section">
      <header class="relay-section__heading"><div><span>Teams</span><h2>Relay line-ups</h2></div><button class="button button--primary" type="button" @click="showTeamCreator = !showTeamCreator"><AppIcon :name="showTeamCreator ? 'close' : 'plus'" :size="16" />{{ showTeamCreator ? 'Cancel' : 'New team' }}</button></header>

      <form v-if="showTeamCreator" class="panel new-team" @submit.prevent="createTeam"><label><span>Name</span><input v-model="newTeam.name" class="input" required maxlength="120"></label><label><span>Short name</span><input v-model="newTeam.short_name" class="input" maxlength="20"></label><label><span>Primary</span><input v-model="newTeam.primary_color" type="color"></label><label><span>Secondary</span><input v-model="newTeam.secondary_color" type="color"></label><label class="new-team__wide"><span>Motto</span><input v-model="newTeam.motto" class="input" maxlength="180"></label><button class="button button--primary" type="submit" :disabled="!!pending">Create team</button></form>

      <div class="team-grid">
        <article v-for="team in payload.teams" :key="team.id" class="panel team-editor" :style="{ '--team-primary': team.primary_color, '--team-secondary': team.secondary_color }">
          <header><div class="team-swatch"><i></i><i></i></div><div><span>{{ team.short_name || `Team ${team.id}` }}</span><h3>{{ team.name }}</h3></div><div class="team-header-actions"><StatusBadge :status="team.locked ? 'locked' : 'active'" :label="team.locked ? 'Locked' : 'Ready'" /><button class="icon-button" type="button" :aria-label="`Edit ${team.name}`" @click="editingTeamId = editingTeamId === team.id ? null : team.id"><AppIcon :name="editingTeamId === team.id ? 'close' : 'settings'" :size="16" /></button></div></header>
          <form v-if="editingTeamId === team.id" class="team-identity" @submit.prevent="saveTeam(team)"><label><span>Team name</span><input v-model="team.name" class="input" maxlength="120"></label><label><span>Short name</span><input v-model="team.short_name" class="input" maxlength="20"></label><label><span>Primary colour</span><input v-model="team.primary_color" type="color"></label><label><span>Secondary colour</span><input v-model="team.secondary_color" type="color"></label><label class="team-identity__wide"><span>Motto</span><input v-model="team.motto" class="input" maxlength="180"></label><label class="team-identity__wide"><span>Team profile</span><textarea v-model="team.profile" class="input" rows="2" maxlength="1000"></textarea></label><div class="team-actions"><button class="button button--primary button--small" type="submit" :disabled="!!pending">{{ pending === `team-${team.id}` ? 'Saving…' : 'Save changes' }}</button><button class="button button--ghost button--small" type="button" @click="editingTeamId = null">Cancel</button><button class="button button--danger button--small" type="button" :disabled="!!pending" @click="deleteTeam(team)"><AppIcon name="trash" :size="14" />Delete</button></div></form>

          <section class="roster-editor">
            <header><div><span>Relay order</span><strong>{{ team.roster.length }} engine{{ team.roster.length === 1 ? '' : 's' }}</strong></div></header>
            <div v-if="team.roster.length" class="roster-table">
              <div class="roster-table__head"><span>Order / engine</span><span>Threads</span><span>Hash</span><span></span></div>
              <form v-for="member in team.roster" :key="member.id" @submit.prevent="saveMember(team, member)">
                <div class="roster-engine"><input v-model.number="member.position" class="input order-input" type="number" min="0" max="1000" :disabled="team.locked"><div><strong>{{ member.name }}</strong><small>{{ member.version }} · #{{ member.engine_id }}</small></div></div>
                <label><span>Threads</span><input v-model.number="member.threads" class="input" type="number" min="1" max="1024" :disabled="team.locked"></label>
                <label><span>Hash · MB</span><input v-model.number="member.hash_mb" class="input" type="number" min="1" step="1" :disabled="team.locked"></label>
                <div class="roster-actions"><button class="button button--secondary button--small" type="submit" :disabled="!!pending || team.locked">Save</button><button class="icon-button icon-button--danger" type="button" :disabled="!!pending || team.locked" aria-label="Remove engine" @click="deleteMember(team, member)">×</button></div>
              </form>
            </div>
            <p v-else class="roster-empty">No engines</p>
            <form v-if="!team.locked" class="add-engine" @submit.prevent="addMember(team)"><div class="add-engine__picker"><span>Engine version</span><ParticipantPicker :model-value="memberDraft(team).engine_id ? [memberDraft(team).engine_id] : []" :engines="payload.engine_options ?? []" single @update:model-value="selectMemberEngine(team, $event)" /></div><label><span>Order</span><input v-model.number="memberDraft(team).position" class="input" type="number" min="0"></label><label><span>Threads</span><input v-model.number="memberDraft(team).threads" class="input" type="number" min="1" max="1024"></label><label><span>Hash · MB</span><input v-model.number="memberDraft(team).hash_mb" class="input" type="number" min="1" step="1"></label><button class="button button--primary button--small" type="submit" :disabled="!!pending || !memberDraft(team).engine_id">Add to relay</button></form>
          </section>
        </article>
      </div>
    </section>

    <section v-show="activeView === 'fixtures'" class="relay-section fixture-workspace">
      <header class="relay-section__heading"><div><span>Fixtures</span><h2>Matches and schedule</h2></div><button class="button button--primary" type="button" @click="showFixtureBuilder = !showFixtureBuilder"><AppIcon :name="showFixtureBuilder ? 'close' : 'plus'" :size="16" />{{ showFixtureBuilder ? 'Cancel' : 'New fixture' }}</button></header>

      <div class="fixture-layout" :class="{ 'fixture-layout--building': showFixtureBuilder }">
        <form v-if="showFixtureBuilder" class="panel fixture-builder" @submit.prevent="createFixture">
          <header><span>New fixture</span><h3>Set up a match</h3></header>
          <label class="fixture-builder__wide"><span>Fixture name</span><input v-model="fixtureForm.title" class="input" required placeholder="Heat 1"></label>
          <fieldset class="fixture-team-picker fixture-builder__wide">
            <legend>Round-robin teams</legend>
            <header><span>{{ fixtureForm.team_ids.length }} selected</span><span><button type="button" @click="selectAllFixtureTeams">Select all</button><button type="button" @click="fixtureForm.team_ids = []">Clear</button></span></header>
            <div><label v-for="team in readyTeams" :key="team.id" :class="{ selected: fixtureForm.team_ids.includes(team.id) }"><input type="checkbox" :checked="fixtureForm.team_ids.includes(team.id)" @change="toggleFixtureTeam(team.id)"><span class="team-pick-swatch" :style="{ '--team-primary': team.primary_color, '--team-secondary': team.secondary_color }"><i></i><i></i></span><span><strong>{{ team.name }}</strong><small>{{ team.roster.length }} {{ team.roster.length === 1 ? 'engine' : 'engines' }}</small></span><AppIcon name="check" :size="14" /></label></div>
            <small>Select at least two teams. Every pairing plays both colours in each cycle.</small>
          </fieldset>
          <label><span>Round-robin cycles</span><input v-model.number="fixtureForm.cycles" class="input" type="number" min="1" max="1000"></label>
          <label><span>Opening suite</span><select v-model="fixtureForm.opening_suite_id" class="input"><option value="">No opening suite</option><option v-for="suite in payload.opening_suites ?? []" :key="suite.id" :value="String(suite.id)">{{ suite.name }}</option></select></label>
          <label><span>Time · seconds</span><input v-model.number="fixtureForm.time_seconds" class="input" type="number" min="0.001" step="any"></label>
          <label><span>Increment · seconds</span><input v-model.number="fixtureForm.increment_seconds" class="input" type="number" min="0" step="any"></label>
          <label class="fixture-builder__wide"><span>Optional scheduled start</span><input v-model="fixtureForm.scheduled_start_at" class="input" type="datetime-local"></label>
          <button class="advanced-toggle" type="button" :aria-expanded="showAdvancedFixture" @click="showAdvancedFixture = !showAdvancedFixture"><span><AppIcon name="settings" :size="15" />Advanced settings</span><AppIcon name="chevron-down" :size="15" :class="{ rotated: showAdvancedFixture }" /></button>
          <div v-if="showAdvancedFixture" class="advanced-fields"><label><span>Maximum full moves</span><input v-model.number="fixtureForm.max_moves" class="input" type="number" min="1"></label><label><span>Concurrency</span><input v-model.number="fixtureForm.concurrency" class="input" type="number" min="1"></label></div>
          <div class="fixture-summary"><div><span>Teams</span><strong>{{ fixtureForm.team_ids.length }}</strong></div><div><span>Games</span><strong>{{ projectedGames }}</strong></div><div><span>Mode</span><strong>Round robin</strong></div></div>
          <button class="button button--primary" type="submit" :disabled="!!pending || readyTeams.length < 2 || !fixtureSelectionValid">{{ pending === 'fixture-new' ? 'Creating…' : 'Create fixture' }}</button>
        </form>

        <div class="fixture-list">
          <article v-for="fixture in payload.fixtures" :key="fixture.id" class="panel fixture-card">
            <header><div><span>Fixture {{ fixture.position + 1 }}</span><h3>{{ fixture.title }}</h3><p>{{ fixtureTeamNames(fixture) }}</p></div><StatusBadge :status="fixture.tournament?.status ?? 'missing'" /></header>
            <dl><div><dt>Games</dt><dd>{{ fixture.games.length }}</dd></div><div><dt>Teams</dt><dd>{{ fixtureTeamCount(fixture) }}</dd></div><div><dt>Cycles</dt><dd>{{ fixtureCycles(fixture) }}</dd></div><div><dt>Starts</dt><dd>{{ formatDate(fixture.tournament?.scheduled_start_at) }}</dd></div></dl>
            <div class="fixture-games"><RouterLink v-for="game in fixture.games" :key="game.id" :to="`/tournaments/${fixture.tournament_id}?game_id=${game.id}`"><span>G{{ game.game_number ?? game.id }}</span><strong>{{ game.result || game.status }}</strong></RouterLink><span v-if="!fixture.games.length">No games</span></div>
            <div v-if="fixture.tournament?.status === 'draft' || fixture.tournament?.status === 'scheduled'" class="fixture-schedule"><input v-model="schedules[fixture.id]" class="input" type="datetime-local"><button class="button button--secondary button--small" type="button" :disabled="!!pending || !schedules[fixture.id]" @click="scheduleFixture(fixture)">{{ fixture.tournament?.status === 'scheduled' ? 'Reschedule' : 'Schedule' }}</button></div>
            <footer><RouterLink class="button button--ghost button--small" :to="`/admin/tournaments/${fixture.tournament_id}`"><AppIcon name="settings" :size="14" />Manage</RouterLink><RouterLink v-if="fixture.tournament?.status !== 'draft'" class="button button--ghost button--small" :to="`/tournaments/${fixture.tournament_id}`"><AppIcon name="external-link" :size="14" />View</RouterLink><button v-if="fixture.tournament?.status === 'scheduled'" class="button button--secondary button--small" type="button" :disabled="!!pending" @click="unscheduleFixture(fixture)">Unschedule</button><button v-if="['draft', 'scheduled'].includes(fixture.tournament?.status ?? '')" class="button button--primary button--small" type="button" :disabled="!!pending" @click="startFixture(fixture)"><AppIcon name="play" :size="14" />Start now</button><button v-if="['draft', 'scheduled'].includes(fixture.tournament?.status ?? '')" class="icon-button icon-button--danger" type="button" :disabled="!!pending" aria-label="Delete fixture" @click="deleteFixture(fixture)"><AppIcon name="trash" :size="15" /></button></footer>
          </article>
          <div v-if="!payload.fixtures.length" class="panel fixture-empty"><span><AppIcon name="trophy" :size="24" /></span><strong>No fixtures</strong><button class="button button--primary button--small" type="button" :disabled="readyTeams.length < 2" @click="showFixtureBuilder = true">Create fixture</button></div>
        </div>
      </div>
    </section>
  </section>
</template>

<style scoped>
.relay-control { display: grid; gap: 1.4rem; }.relay-command-bar { display: grid; grid-template-columns: repeat(3, minmax(8rem, .5fr)) minmax(23rem, 1.3fr); padding: 0; overflow: hidden; }.relay-command-bar > div { display: grid; gap: .1rem; padding: .75rem .85rem; border-right: 1px solid var(--color-border, #d9e0ea); }.relay-command-bar span, .relay-section__heading > div > span, .fixture-builder header span, .team-editor > header > div > span { color: var(--color-text-muted, #64748b); font-size: .56rem; font-weight: 760; letter-spacing: .07em; text-transform: uppercase; }.relay-command-bar strong { font-size: .78rem; }.relay-command-bar small { color: var(--color-text-muted, #64748b); font-size: .57rem; }.visibility-control { display: flex; align-items: end; gap: .55rem; padding: .55rem .7rem; }.publish-toggle { display: flex; align-items: center; gap: .35rem; min-height: 2rem; font-size: .65rem; }
.relay-section { display: grid; gap: .85rem; }.relay-section__heading { display: flex; align-items: end; justify-content: space-between; gap: 1rem; }.relay-section__heading h2 { margin: .16rem 0 0; font-size: 1.08rem; }.relay-section__heading p { margin: .3rem 0 0; color: var(--color-text-muted, #64748b); font-size: .7rem; }.new-team { display: grid; grid-template-columns: 1fr .5fr auto auto 1fr auto; align-items: end; gap: .7rem; padding: .8rem; }.new-team label, .team-identity label, .add-engine label, .fixture-builder label { display: grid; gap: .3rem; }.new-team label > span, .team-identity label > span, .add-engine label > span, .fixture-builder label > span, .roster-table form label > span { color: var(--color-text-muted, #64748b); font-size: .58rem; font-weight: 680; }.new-team input[type="color"], .team-identity input[type="color"] { width: 100%; height: 2.15rem; padding: .12rem; border: 1px solid var(--color-border, #d9e0ea); border-radius: .4rem; background: white; }.team-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 34rem), 1fr)); gap: .85rem; }.team-editor { position: relative; display: grid; gap: 1rem; overflow: hidden; padding: .9rem; border-top: 4px solid var(--team-primary); }.team-editor::before { position: absolute; inset: 0; pointer-events: none; background: radial-gradient(circle at 100% 0, color-mix(in srgb, var(--team-primary) 10%, transparent), transparent 32%); content: ""; }.team-editor > * { position: relative; }.team-editor > header { display: grid; grid-template-columns: auto minmax(0, 1fr) auto; align-items: center; gap: .7rem; }.team-editor > header h3 { margin: .1rem 0 0; font-size: 1rem; }.team-swatch { display: flex; overflow: hidden; width: 2.3rem; height: 2.3rem; border-radius: .55rem; box-shadow: 0 0 0 1px color-mix(in srgb, var(--team-primary) 35%, transparent); }.team-swatch i { flex: 1; background: var(--team-primary); }.team-swatch i:last-child { background: var(--team-secondary); }.team-identity { display: grid; grid-template-columns: 1fr .55fr auto auto; gap: .65rem; }.team-identity__wide { grid-column: 1 / -1; }.team-actions { grid-column: 1 / -1; display: flex; justify-content: flex-end; gap: .45rem; }.roster-editor { display: grid; gap: .6rem; padding-top: .8rem; border-top: 1px solid var(--color-border, #d9e0ea); }.roster-editor > header { display: flex; align-items: end; justify-content: space-between; gap: .7rem; }.roster-editor > header div { display: grid; gap: .1rem; }.roster-editor > header span { color: var(--team-primary); font-size: .56rem; font-weight: 760; text-transform: uppercase; }.roster-editor > header strong { font-size: .76rem; }.roster-editor > header small { color: var(--color-text-muted, #64748b); font-size: .57rem; }.roster-table { display: grid; gap: 1px; overflow: hidden; border: 1px solid var(--color-border, #d9e0ea); border-radius: .5rem; background: var(--color-border, #d9e0ea); }.roster-table__head, .roster-table form { display: grid; grid-template-columns: minmax(12rem, 1.5fr) minmax(5rem, .55fr) minmax(8rem, .8fr) auto; align-items: center; gap: .45rem; padding: .45rem .55rem; background: var(--color-surface, #fff); }.roster-table__head { background: var(--color-surface-subtle, #f1f5f9); color: var(--color-text-muted, #64748b); font-size: .52rem; font-weight: 730; text-transform: uppercase; }.roster-engine { display: grid; grid-template-columns: 3.2rem minmax(0, 1fr); align-items: center; gap: .45rem; min-width: 0; }.roster-engine > div { display: grid; min-width: 0; }.roster-engine strong, .roster-engine small { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }.roster-engine strong { font-size: .68rem; }.roster-engine small { color: var(--color-text-muted, #64748b); font-size: .56rem; }.order-input { width: 3.2rem; }.roster-actions { display: flex; gap: .3rem; }.roster-empty { margin: 0; padding: .65rem; border: 1px dashed var(--color-border-strong, #bbc5d3); border-radius: .45rem; color: var(--color-text-muted, #64748b); font-size: .65rem; }.add-engine { display: grid; grid-template-columns: minmax(10rem, 1fr) 4.5rem 5rem 8rem auto; align-items: end; gap: .45rem; padding: .55rem; border-radius: .5rem; background: color-mix(in srgb, var(--team-primary) 5%, var(--color-surface-subtle, #f1f5f9)); }
.fixture-workspace { padding-top: .2rem; }.game-projection { padding: .35rem .55rem; border-radius: .4rem; background: var(--color-surface-subtle, #f1f5f9); color: var(--color-accent, #315fcc); font-size: .67rem; }.fixture-layout { display: grid; grid-template-columns: minmax(20rem, .7fr) minmax(0, 1.3fr); align-items: start; gap: .85rem; }.fixture-builder { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: .7rem; padding: .9rem; position: sticky; top: calc(var(--app-header-height, 0px) + 1rem); }.fixture-builder header, .fixture-builder__wide, .fixture-summary, .fixture-builder > button { grid-column: 1 / -1; }.fixture-builder h3 { margin: .14rem 0 0; font-size: .92rem; }.fixture-summary { display: grid; grid-template-columns: repeat(3, 1fr); gap: 1px; overflow: hidden; border: 1px solid var(--color-border, #d9e0ea); border-radius: .45rem; background: var(--color-border, #d9e0ea); }.fixture-summary div { display: grid; gap: .15rem; padding: .55rem; background: var(--color-surface-subtle, #f1f5f9); }.fixture-summary span { color: var(--color-text-muted, #64748b); font-size: .52rem; text-transform: uppercase; }.fixture-summary strong { font-size: .66rem; }.fixture-list { display: grid; gap: .65rem; }.fixture-card { display: grid; gap: .75rem; padding: .85rem; }.fixture-card > header { display: flex; align-items: start; justify-content: space-between; gap: .7rem; }.fixture-card > header span { color: var(--color-accent, #315fcc); font-size: .54rem; font-weight: 760; text-transform: uppercase; }.fixture-card h3 { margin: .12rem 0 0; font-size: .88rem; }.fixture-card header p { margin: .18rem 0 0; color: var(--color-text-muted, #64748b); font-size: .62rem; }.fixture-card > dl { display: grid; grid-template-columns: repeat(4, 1fr); gap: 1px; margin: 0; background: var(--color-border, #d9e0ea); }.fixture-card > dl div { padding: .5rem; background: var(--color-surface-subtle, #f1f5f9); }.fixture-card dt { color: var(--color-text-muted, #64748b); font-size: .5rem; text-transform: uppercase; }.fixture-card dd { margin: .13rem 0 0; font-size: .62rem; font-weight: 700; }.fixture-games { display: flex; flex-wrap: wrap; gap: .35rem; }.fixture-games a { display: flex; gap: .35rem; padding: .3rem .42rem; border: 1px solid var(--color-border, #d9e0ea); border-radius: .35rem; color: inherit; font-size: .56rem; text-decoration: none; }.fixture-games a:hover { border-color: var(--color-accent, #315fcc); }.fixture-games > span { color: var(--color-text-muted, #64748b); font-size: .6rem; }.fixture-schedule { display: grid; grid-template-columns: 1fr auto; gap: .45rem; }.fixture-card footer { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: .35rem; padding-top: .65rem; border-top: 1px solid var(--color-border, #d9e0ea); }.fixture-empty { display: grid; min-height: 12rem; place-content: center; text-align: center; }.fixture-empty strong { font-size: .82rem; }.fixture-empty p { margin: .3rem 0 0; color: var(--color-text-muted, #64748b); font-size: .65rem; }
@media (max-width: 78rem) { .relay-command-bar { grid-template-columns: repeat(3, 1fr); }.visibility-control { grid-column: 1 / -1; border-top: 1px solid var(--color-border, #d9e0ea); }.fixture-layout { grid-template-columns: 1fr; }.fixture-builder { position: static; }.new-team { grid-template-columns: repeat(2, 1fr); }.new-team__wide { grid-column: auto; }.roster-table { overflow-x: auto; }.roster-table__head, .roster-table form { min-width: 42rem; } }
@media (max-width: 46rem) { .relay-command-bar { grid-template-columns: 1fr; }.relay-command-bar > div { border-right: 0; border-bottom: 1px solid var(--color-border, #d9e0ea); }.visibility-control { align-items: stretch; flex-direction: column; grid-column: auto; }.relay-section__heading { align-items: stretch; flex-direction: column; }.new-team, .team-identity, .fixture-builder { grid-template-columns: 1fr; }.team-identity__wide, .fixture-builder header, .fixture-builder__wide, .fixture-summary, .fixture-builder > button { grid-column: auto; }.team-editor > header { grid-template-columns: auto 1fr; }.team-editor > header > :last-child { grid-column: 1 / -1; }.add-engine { grid-template-columns: 1fr 1fr; }.add-engine label:first-child { grid-column: 1 / -1; }.add-engine button { grid-column: 1 / -1; }.fixture-summary { grid-template-columns: 1fr; }.fixture-card > dl { grid-template-columns: repeat(2, 1fr); } }
.relay-control { gap: 1.25rem; }
.relay-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 1rem; padding: .4rem; border-radius: .85rem; box-shadow: var(--shadow-xs); }
.relay-toolbar nav { display: flex; align-items: center; gap: .2rem; }
.relay-toolbar nav button { display: flex; align-items: center; gap: .45rem; min-height: 2.45rem; padding: 0 .8rem; border: 0; border-radius: .55rem; background: transparent; color: var(--color-text-muted); cursor: pointer; font: inherit; font-size: .72rem; font-weight: 700; transition: background var(--transition-fast), color var(--transition-fast); }
.relay-toolbar nav button:hover { background: var(--color-surface-hover); color: var(--color-text); }
.relay-toolbar nav button.active { background: var(--color-accent-soft); color: var(--color-accent); }
.relay-toolbar nav button span { min-width: 1.25rem; padding: .12rem .35rem; border-radius: 99px; background: color-mix(in srgb, currentColor 10%, transparent); font-size: .56rem; text-align: center; }
.visibility-control { align-items: center; padding: 0; }
.lifecycle-status { display: flex; align-items: center; gap: .45rem; }
.lifecycle-status > span { color: var(--color-text-muted); font-size: .62rem; font-weight: 700; }
.publish-toggle { padding: 0 .45rem; border-left: 1px solid var(--color-border); }
.overview-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: .85rem; }
.overview-card { display: grid; grid-template-columns: auto 1fr auto; align-items: center; gap: .9rem; padding: 1.15rem; border: 1px solid var(--color-border); color: inherit; cursor: pointer; font: inherit; text-align: left; transition: transform var(--transition-fast), border-color var(--transition-fast), box-shadow var(--transition-fast); }
.overview-card:hover { transform: translateY(-2px); border-color: color-mix(in srgb, var(--color-accent) 45%, var(--color-border)); box-shadow: var(--shadow-sm); }
.overview-card__icon { display: grid; width: 2.7rem; height: 2.7rem; place-items: center; border-radius: .75rem; background: var(--color-accent-soft); color: var(--color-accent); }
.overview-card > span:nth-child(2) { display: grid; gap: .1rem; }
.overview-card small { color: var(--color-text-muted); font-size: .58rem; font-style: normal; font-weight: 750; letter-spacing: .06em; text-transform: uppercase; }
.overview-card strong { font-size: 1.4rem; line-height: 1.1; }
.overview-card > .app-icon { color: var(--color-text-muted); }
.event-health h2 { margin: 0; font-size: 1rem; }
.event-health { grid-column: 1 / -1; padding: 0; overflow: hidden; }
.event-health header { display: flex; align-items: center; justify-content: space-between; padding: .8rem 1rem; border-bottom: 1px solid var(--color-border); }
.event-health dl { display: grid; grid-template-columns: repeat(4, 1fr); margin: 0; }
.event-health dl div { padding: .8rem 1rem; border-right: 1px solid var(--color-border); }
.event-health dl div:last-child { border-right: 0; }
.event-health dt { color: var(--color-text-muted); font-size: .56rem; text-transform: uppercase; }
.event-health dd { margin: .2rem 0 0; font-size: .7rem; font-weight: 700; }
.relay-section__heading { align-items: center; }
.relay-section__heading h2 { font-size: 1.2rem; }
.new-team { border-color: color-mix(in srgb, var(--color-accent) 30%, var(--color-border)); box-shadow: var(--shadow-sm); }
.team-editor { gap: .85rem; padding: 1rem; border-top-width: 3px; }
.team-header-actions { display: flex; align-items: center; gap: .45rem; }
.team-identity { padding: .8rem; border: 1px solid var(--color-border); border-radius: .6rem; background: var(--color-surface-subtle); }
.team-actions { align-items: center; }
.team-actions .button--danger { margin-left: auto; }
.roster-editor { padding-top: .2rem; border-top: 0; }
.roster-table { border-radius: .6rem; }
.add-engine { padding: .7rem; border: 1px dashed color-mix(in srgb, var(--team-primary) 35%, var(--color-border)); }
.add-engine { grid-template-columns: 4.5rem 5rem 8rem auto; }
.add-engine__picker { display: grid; grid-column: 1 / -1; gap: .3rem; }
.add-engine__picker > span { color: var(--color-text-muted, #64748b); font-size: .58rem; font-weight: 680; }
.fixture-layout { display: block; }
.fixture-layout--building { display: grid; grid-template-columns: minmax(20rem, .72fr) minmax(0, 1.28fr); }
.fixture-builder { border-color: color-mix(in srgb, var(--color-accent) 30%, var(--color-border)); box-shadow: var(--shadow-sm); }
.fixture-team-picker { display: grid; gap: .55rem; min-width: 0; margin: 0; padding: .7rem; border: 1px solid var(--color-border); border-radius: .55rem; }
.fixture-team-picker legend { padding: 0 .25rem; color: var(--color-text-muted); font-size: .58rem; font-weight: 680; }
.fixture-team-picker > header { display: flex; align-items: center; justify-content: space-between; }
.fixture-team-picker > header > span:first-child { color: var(--color-accent); font-size: .6rem; font-weight: 750; }
.fixture-team-picker > header > span:last-child { display: flex; gap: .3rem; }
.fixture-team-picker > header button { padding: .2rem .4rem; border: 0; background: transparent; color: var(--color-accent); cursor: pointer; font: inherit; font-size: .58rem; font-weight: 700; }
.fixture-team-picker > div { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: .4rem; }
.fixture-team-picker > div > label { display: grid; grid-template-columns: auto auto minmax(0, 1fr) auto; align-items: center; gap: .5rem; min-width: 0; padding: .55rem; border: 1px solid var(--color-border); border-radius: .45rem; cursor: pointer; }
.fixture-team-picker > div > label.selected { border-color: color-mix(in srgb, var(--color-accent) 55%, var(--color-border)); background: var(--color-accent-soft); }
.fixture-team-picker input { position: absolute; opacity: 0; pointer-events: none; }
.fixture-team-picker label > span:nth-of-type(2) { display: grid; min-width: 0; }
.fixture-team-picker label strong, .fixture-team-picker label small { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.fixture-team-picker label strong { font-size: .64rem; }
.fixture-team-picker label small, .fixture-team-picker > small { color: var(--color-text-muted); font-size: .54rem; }
.fixture-team-picker label > .app-icon { color: transparent; }
.fixture-team-picker label.selected > .app-icon { color: var(--color-accent); }
.team-pick-swatch { display: flex; overflow: hidden; width: 1.45rem; height: 1.45rem; border-radius: .35rem; }
.team-pick-swatch i { flex: 1; background: var(--team-primary); }
.team-pick-swatch i:last-child { background: var(--team-secondary); }
.advanced-toggle { grid-column: 1 / -1; display: flex; align-items: center; justify-content: space-between; min-height: 2.35rem; padding: 0 .65rem; border: 1px solid var(--color-border); border-radius: .45rem; background: var(--color-surface-subtle); color: var(--color-text-secondary); cursor: pointer; }
.advanced-toggle span { display: flex; align-items: center; gap: .4rem; font-size: .66rem; font-weight: 700; }
.advanced-toggle .app-icon { transition: transform var(--transition-fast); }
.advanced-toggle .rotated { transform: rotate(180deg); }
.advanced-fields { grid-column: 1 / -1; display: grid; grid-template-columns: repeat(2, 1fr); gap: .65rem; padding: .7rem; border-radius: .5rem; background: var(--color-surface-subtle); }
.fixture-list { grid-template-columns: repeat(auto-fit, minmax(min(100%, 26rem), 1fr)); }
.fixture-card { align-self: start; padding: 1rem; transition: border-color var(--transition-fast), box-shadow var(--transition-fast); }
.fixture-card:hover { border-color: color-mix(in srgb, var(--color-accent) 30%, var(--color-border)); box-shadow: var(--shadow-xs); }
.fixture-card > dl { grid-template-columns: repeat(3, 1fr); overflow: hidden; border-radius: .45rem; }
.fixture-empty { gap: .45rem; }
.fixture-empty > span { display: grid; width: 3rem; height: 3rem; place-items: center; justify-self: center; border-radius: .8rem; background: var(--color-accent-soft); color: var(--color-accent); }
.fixture-empty .button { justify-self: center; margin-top: .3rem; }
@media (max-width: 78rem) { .relay-toolbar { align-items: stretch; flex-direction: column; }.visibility-control { justify-content: flex-end; padding-top: .4rem; border-top: 1px solid var(--color-border); }.fixture-layout--building { grid-template-columns: 1fr; }.fixture-builder { position: static; } }
@media (max-width: 46rem) { .relay-toolbar nav { display: grid; grid-template-columns: repeat(3, 1fr); }.relay-toolbar nav button { justify-content: center; padding: 0 .35rem; }.visibility-control { align-items: stretch; flex-direction: column; }.lifecycle-status { justify-content: space-between; }.publish-toggle { border-left: 0; }.overview-grid { grid-template-columns: 1fr; }.event-health dl { grid-template-columns: repeat(2, 1fr); }.event-health dl div:nth-child(2) { border-right: 0; }.event-health dl div:nth-child(-n+2) { border-bottom: 1px solid var(--color-border); }.team-header-actions { grid-column: 1 / -1; justify-content: space-between; }.advanced-fields { grid-template-columns: 1fr; } }
@media (max-width: 46rem) { .fixture-team-picker > div { grid-template-columns: 1fr; } }
</style>
