<script setup lang="ts">
import { computed, reactive, ref } from "vue";

import { api } from "@/api/client";
import InlineFeedback from "@/components/admin/InlineFeedback.vue";
import StatusBadge from "@/components/admin/StatusBadge.vue";
import { errorText, formatDate } from "@/components/admin/format";
import { useToast } from "@/composables/useToast";
import type { EventDetailResponse, EventStatus } from "@/types/events";

import type { EngineRelayPayload, RelayFixture, RelayRosterMember, RelayTeam } from "./types";

const props = defineProps<{ detail: EventDetailResponse }>();
const emit = defineEmits<{ changed: [] }>();
const toast = useToast();
const payload = computed(() => props.detail.custom as EngineRelayPayload);
const pending = ref("");
const error = ref("");
const showTeamCreator = ref(false);
const visibility = reactive({ status: props.detail.event.status as EventStatus, published: !!props.detail.event.published_at });
const newTeam = reactive({ name: "", short_name: "", primary_color: "#315fcc", secondary_color: "#8fb3ff", profile: "", motto: "" });
const fixtureForm = reactive({
  title: "",
  team_a_id: 0,
  team_b_id: 0,
  cycles: 1,
  opening_suite_id: "",
  concurrency: 1,
  engine_threads: 1,
  engine_hash_mb: 256,
  lag_compensation_ms: 50,
  max_moves: 200,
  scheduled_start_at: "",
});
const memberDrafts = reactive<Record<number, { engine_id: number; relay_moves: number; nodes: number; position: number; label: string }>>({});
const schedules = reactive<Record<number, string>>({});

const readyTeams = computed(() => payload.value.teams.filter((team) => team.roster.length));
const projectedGames = computed(() => Math.max(1, Number(fixtureForm.cycles) || 1) * 2);

function memberDraft(team: RelayTeam) {
  return memberDrafts[team.id] ??= {
    engine_id: 0,
    relay_moves: 4,
    nodes: 100000,
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
    memberDrafts[team.id] = { engine_id: 0, relay_moves: 4, nodes: 100000, position: team.roster.length + 1, label: "" };
    return response;
  });
}

function saveMember(team: RelayTeam, member: RelayRosterMember): void {
  void run(`member-${member.id}`, () => api.put(`/api/admin/events/${props.detail.event.id}/engine-relay/teams/${team.id}/members/${member.id}`, {
    body: {
      engine_id: Number(member.engine_id),
      relay_moves: Number(member.relay_moves),
      nodes: Number(member.nodes),
      position: Number(member.position),
      label: member.label,
    },
  }));
}

function deleteMember(team: RelayTeam, member: RelayRosterMember): void {
  if (!window.confirm(`Remove ${member.display_name} from ${team.name}?`)) return;
  void run(`member-delete-${member.id}`, () => api.delete(`/api/admin/events/${props.detail.event.id}/engine-relay/teams/${team.id}/members/${member.id}`));
}

function createFixture(): void {
  if (!fixtureForm.title.trim() || !fixtureForm.team_a_id || !fixtureForm.team_b_id) return;
  void run("fixture-new", async () => {
    const response = await api.post<{ message?: string }>(`/api/admin/events/${props.detail.event.id}/engine-relay/fixtures`, {
      body: {
        title: fixtureForm.title,
        team_a_id: Number(fixtureForm.team_a_id),
        team_b_id: Number(fixtureForm.team_b_id),
        cycles: Number(fixtureForm.cycles),
        opening_suite_id: fixtureForm.opening_suite_id ? Number(fixtureForm.opening_suite_id) : null,
        concurrency: Number(fixtureForm.concurrency),
        engine_threads: Number(fixtureForm.engine_threads),
        engine_hash_mb: Number(fixtureForm.engine_hash_mb),
        lag_compensation_ms: Number(fixtureForm.lag_compensation_ms),
        adjudication: { draw: null, resign: null, max_moves: fixtureForm.max_moves ? Number(fixtureForm.max_moves) : null },
        scheduled_start_at: fixtureForm.scheduled_start_at ? new Date(fixtureForm.scheduled_start_at).toISOString() : null,
      },
    });
    Object.assign(fixtureForm, { title: "", team_a_id: 0, team_b_id: 0, cycles: 1, opening_suite_id: "", scheduled_start_at: "" });
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

function nodeRatio(member: RelayRosterMember, team: RelayTeam): string {
  const baseline = team.roster[0]?.nodes || member.nodes;
  return `${(member.nodes / baseline).toFixed(2)}×`;
}

function fixtureCycles(fixture: RelayFixture): number | string {
  const options = fixture.tournament?.config?.format_options as { cycles?: number } | undefined;
  return options?.cycles ?? "—";
}
</script>

<template>
  <section class="relay-control">
    <InlineFeedback :message="error" />

    <section class="panel relay-command-bar">
      <div><span>Module</span><strong>Engine Relay v1</strong><small>Normal scheduler · unrated execution</small></div>
      <div><span>Teams</span><strong>{{ payload.teams.length }}</strong><small>{{ payload.teams.reduce((total, team) => total + team.roster.length, 0) }} engine slots</small></div>
      <div><span>Fixtures</span><strong>{{ payload.fixtures.length }}</strong><small>{{ payload.fixtures.reduce((total, fixture) => total + fixture.games.length, 0) }} materialised games</small></div>
      <form class="visibility-control" @submit.prevent="saveVisibility"><label><span>Event state</span><select v-model="visibility.status"><option value="draft">Draft</option><option value="announced">Announced</option><option value="scheduled">Scheduled</option><option value="live">Live</option><option value="intermission">Intermission</option><option value="postponed">Postponed</option><option value="completed">Completed</option><option value="cancelled">Cancelled</option></select></label><label class="publish-toggle"><input v-model="visibility.published" type="checkbox"><span>Public</span></label><button class="button button--secondary button--small" type="submit" :disabled="!!pending">{{ pending === 'visibility' ? 'Saving…' : 'Apply' }}</button></form>
    </section>

    <section class="relay-section">
      <header class="relay-section__heading"><div><span>01 · Field construction</span><h2>Teams and running orders</h2><p>Set the visual identity, exact relay order, handoff length, and node allowance for every engine.</p></div><button class="button button--secondary button--small" type="button" @click="showTeamCreator = !showTeamCreator">{{ showTeamCreator ? 'Close' : 'Add team' }}</button></header>

      <form v-if="showTeamCreator" class="panel new-team" @submit.prevent="createTeam"><label><span>Name</span><input v-model="newTeam.name" class="input" required maxlength="120"></label><label><span>Short name</span><input v-model="newTeam.short_name" class="input" maxlength="20"></label><label><span>Primary</span><input v-model="newTeam.primary_color" type="color"></label><label><span>Secondary</span><input v-model="newTeam.secondary_color" type="color"></label><label class="new-team__wide"><span>Motto</span><input v-model="newTeam.motto" class="input" maxlength="180"></label><button class="button button--primary" type="submit" :disabled="!!pending">Create team</button></form>

      <div class="team-grid">
        <article v-for="team in payload.teams" :key="team.id" class="panel team-editor" :style="{ '--team-primary': team.primary_color, '--team-secondary': team.secondary_color }">
          <header><div class="team-swatch"><i></i><i></i></div><div><span>Team #{{ team.id }}</span><h3>{{ team.name }}</h3></div><StatusBadge :status="team.locked ? 'locked' : 'active'" :label="team.locked ? 'Roster locked' : 'Editable'" /></header>
          <form class="team-identity" @submit.prevent="saveTeam(team)"><label><span>Team name</span><input v-model="team.name" class="input" maxlength="120"></label><label><span>Short name</span><input v-model="team.short_name" class="input" maxlength="20"></label><label><span>Primary colour</span><input v-model="team.primary_color" type="color"></label><label><span>Secondary colour</span><input v-model="team.secondary_color" type="color"></label><label class="team-identity__wide"><span>Motto</span><input v-model="team.motto" class="input" maxlength="180"></label><label class="team-identity__wide"><span>Team profile</span><textarea v-model="team.profile" class="input" rows="2" maxlength="1000"></textarea></label><div class="team-actions"><button class="button button--secondary button--small" type="submit" :disabled="!!pending">{{ pending === `team-${team.id}` ? 'Saving…' : 'Save identity' }}</button><button class="button button--danger button--small" type="button" :disabled="!!pending" @click="deleteTeam(team)">Delete</button></div></form>

          <section class="roster-editor">
            <header><div><span>Relay order</span><strong>{{ team.roster.length }} engine{{ team.roster.length === 1 ? '' : 's' }}</strong></div><small v-if="team.locked">Started fixtures preserve this exact roster.</small><small v-else>Order numbers may be repeated; ties keep their registration order.</small></header>
            <div v-if="team.roster.length" class="roster-table">
              <div class="roster-table__head"><span>Order / engine</span><span>Moves per turn</span><span>Node allowance</span><span>Odds</span><span></span></div>
              <form v-for="member in team.roster" :key="member.id" @submit.prevent="saveMember(team, member)">
                <div class="roster-engine"><input v-model.number="member.position" class="input order-input" type="number" min="0" max="1000" :disabled="team.locked"><div><strong>{{ member.name }}</strong><small>{{ member.version }} · #{{ member.engine_id }}</small></div></div>
                <label><span>Moves</span><input v-model.number="member.relay_moves" class="input" type="number" min="1" max="1000" :disabled="team.locked"></label>
                <label><span>Nodes</span><input v-model.number="member.nodes" class="input" type="number" min="1" step="1" :disabled="team.locked"></label>
                <strong class="odds-chip">{{ nodeRatio(member, team) }}</strong>
                <div class="roster-actions"><button class="button button--secondary button--small" type="submit" :disabled="!!pending || team.locked">Save</button><button class="icon-button icon-button--danger" type="button" :disabled="!!pending || team.locked" aria-label="Remove engine" @click="deleteMember(team, member)">×</button></div>
              </form>
            </div>
            <p v-else class="roster-empty">No engines registered. Add the anchor engine first; it becomes this team’s tournament participant identity.</p>
            <form v-if="!team.locked" class="add-engine" @submit.prevent="addMember(team)"><label><span>Engine version</span><select v-model.number="memberDraft(team).engine_id" class="input"><option :value="0" disabled>Select an engine</option><option v-for="engine in payload.engine_options ?? []" :key="engine.id" :value="engine.id">{{ engine.name }} {{ engine.version }}</option></select></label><label><span>Order</span><input v-model.number="memberDraft(team).position" class="input" type="number" min="0"></label><label><span>Moves</span><input v-model.number="memberDraft(team).relay_moves" class="input" type="number" min="1"></label><label><span>Nodes</span><input v-model.number="memberDraft(team).nodes" class="input" type="number" min="1" step="1"></label><button class="button button--primary button--small" type="submit" :disabled="!!pending || !memberDraft(team).engine_id">Add to relay</button></form>
          </section>
        </article>
      </div>
    </section>

    <section class="relay-section fixture-workspace">
      <header class="relay-section__heading"><div><span>02 · Match production</span><h2>Register relay fixtures</h2><p>Each fixture is a first-class, unrated round-robin tournament with the normal opening, worker, replay, and scheduling lifecycle.</p></div><strong class="game-projection">{{ projectedGames }} games in draft</strong></header>

      <div class="fixture-layout">
        <form class="panel fixture-builder" @submit.prevent="createFixture">
          <header><span>New fixture</span><h3>Build the execution unit</h3></header>
          <label class="fixture-builder__wide"><span>Tournament title</span><input v-model="fixtureForm.title" class="input" required placeholder="OpenBench Engine Clash · Heat 1"></label>
          <label><span>Team A</span><select v-model.number="fixtureForm.team_a_id" class="input"><option :value="0" disabled>Select team</option><option v-for="team in readyTeams" :key="team.id" :value="team.id">{{ team.name }}</option></select></label>
          <label><span>Team B</span><select v-model.number="fixtureForm.team_b_id" class="input"><option :value="0" disabled>Select team</option><option v-for="team in readyTeams" :key="team.id" :value="team.id">{{ team.name }}</option></select></label>
          <label><span>Round-robin cycles</span><input v-model.number="fixtureForm.cycles" class="input" type="number" min="1" max="1000"></label>
          <label><span>Opening suite</span><select v-model="fixtureForm.opening_suite_id" class="input"><option value="">No opening suite</option><option v-for="suite in payload.opening_suites ?? []" :key="suite.id" :value="String(suite.id)">{{ suite.name }}</option></select></label>
          <label><span>Maximum full moves</span><input v-model.number="fixtureForm.max_moves" class="input" type="number" min="1"></label>
          <label><span>Concurrency</span><input v-model.number="fixtureForm.concurrency" class="input" type="number" min="1"></label>
          <label><span>Threads per active engine</span><input v-model.number="fixtureForm.engine_threads" class="input" type="number" min="1"></label>
          <label><span>Hash per roster engine · MB</span><input v-model.number="fixtureForm.engine_hash_mb" class="input" type="number" min="1"></label>
          <label class="fixture-builder__wide"><span>Optional scheduled start</span><input v-model="fixtureForm.scheduled_start_at" class="input" type="datetime-local"></label>
          <div class="fixture-summary"><div><span>Games</span><strong>{{ projectedGames }}</strong></div><div><span>Rating impact</span><strong>None</strong></div><div><span>Worker mode</span><strong>Relay-aware</strong></div></div>
          <button class="button button--primary" type="submit" :disabled="!!pending || readyTeams.length < 2 || fixtureForm.team_a_id === fixtureForm.team_b_id">{{ pending === 'fixture-new' ? 'Registering…' : 'Register fixture' }}</button>
        </form>

        <div class="fixture-list">
          <article v-for="fixture in payload.fixtures" :key="fixture.id" class="panel fixture-card">
            <header><div><span>Fixture {{ fixture.position + 1 }}</span><h3>{{ fixture.title }}</h3><p>{{ fixture.team_a_name }} vs {{ fixture.team_b_name }}</p></div><StatusBadge :status="fixture.tournament?.status ?? 'missing'" /></header>
            <dl><div><dt>Tournament</dt><dd>#{{ fixture.tournament_id }}</dd></div><div><dt>Games</dt><dd>{{ fixture.games.length }}</dd></div><div><dt>Cycles</dt><dd>{{ fixtureCycles(fixture) }}</dd></div><div><dt>Starts</dt><dd>{{ formatDate(fixture.tournament?.scheduled_start_at) }}</dd></div></dl>
            <div class="fixture-games"><RouterLink v-for="game in fixture.games" :key="game.id" :to="`/tournaments/${fixture.tournament_id}?game_id=${game.id}`"><span>G{{ game.game_number ?? game.id }}</span><strong>{{ game.result || game.status }}</strong></RouterLink><span v-if="!fixture.games.length">Games materialise when scheduled or started.</span></div>
            <div v-if="fixture.tournament?.status === 'draft' || fixture.tournament?.status === 'scheduled'" class="fixture-schedule"><input v-model="schedules[fixture.id]" class="input" type="datetime-local"><button class="button button--secondary button--small" type="button" :disabled="!!pending || !schedules[fixture.id]" @click="scheduleFixture(fixture)">{{ fixture.tournament?.status === 'scheduled' ? 'Reschedule' : 'Schedule' }}</button></div>
            <footer><RouterLink class="button button--ghost button--small" :to="`/admin/tournaments/${fixture.tournament_id}`">Tournament controls</RouterLink><RouterLink v-if="fixture.tournament?.status !== 'draft'" class="button button--ghost button--small" :to="`/tournaments/${fixture.tournament_id}`">Public board</RouterLink><button v-if="fixture.tournament?.status === 'scheduled'" class="button button--secondary button--small" type="button" :disabled="!!pending" @click="unscheduleFixture(fixture)">Unschedule</button><button v-if="['draft', 'scheduled'].includes(fixture.tournament?.status ?? '')" class="button button--primary button--small" type="button" :disabled="!!pending" @click="startFixture(fixture)">Start now</button><button v-if="['draft', 'scheduled'].includes(fixture.tournament?.status ?? '')" class="button button--danger button--small" type="button" :disabled="!!pending" @click="deleteFixture(fixture)">Delete</button></footer>
          </article>
          <div v-if="!payload.fixtures.length" class="panel fixture-empty"><strong>No relay fixtures yet</strong><p>Complete two rosters, then register the first execution unit.</p></div>
        </div>
      </div>
    </section>
  </section>
</template>

<style scoped>
.relay-control { display: grid; gap: 1.4rem; }.relay-command-bar { display: grid; grid-template-columns: repeat(3, minmax(8rem, .5fr)) minmax(23rem, 1.3fr); padding: 0; overflow: hidden; }.relay-command-bar > div { display: grid; gap: .1rem; padding: .75rem .85rem; border-right: 1px solid var(--color-border, #d9e0ea); }.relay-command-bar span, .relay-section__heading > div > span, .fixture-builder header span, .team-editor > header > div > span { color: var(--color-text-muted, #64748b); font-size: .56rem; font-weight: 760; letter-spacing: .07em; text-transform: uppercase; }.relay-command-bar strong { font-size: .78rem; }.relay-command-bar small { color: var(--color-text-muted, #64748b); font-size: .57rem; }.visibility-control { display: flex; align-items: end; gap: .55rem; padding: .55rem .7rem; }.visibility-control label:first-child { display: grid; flex: 1; gap: .24rem; }.visibility-control select { height: 2rem; border: 1px solid var(--color-border, #d9e0ea); border-radius: .4rem; background: var(--color-surface, #fff); font-size: .68rem; }.publish-toggle { display: flex; align-items: center; gap: .35rem; min-height: 2rem; font-size: .65rem; }
.relay-section { display: grid; gap: .85rem; }.relay-section__heading { display: flex; align-items: end; justify-content: space-between; gap: 1rem; }.relay-section__heading h2 { margin: .16rem 0 0; font-size: 1.08rem; }.relay-section__heading p { margin: .3rem 0 0; color: var(--color-text-muted, #64748b); font-size: .7rem; }.new-team { display: grid; grid-template-columns: 1fr .5fr auto auto 1fr auto; align-items: end; gap: .7rem; padding: .8rem; }.new-team label, .team-identity label, .add-engine label, .fixture-builder label { display: grid; gap: .3rem; }.new-team label > span, .team-identity label > span, .add-engine label > span, .fixture-builder label > span, .roster-table form label > span { color: var(--color-text-muted, #64748b); font-size: .58rem; font-weight: 680; }.new-team input[type="color"], .team-identity input[type="color"] { width: 100%; height: 2.15rem; padding: .12rem; border: 1px solid var(--color-border, #d9e0ea); border-radius: .4rem; background: white; }.team-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 34rem), 1fr)); gap: .85rem; }.team-editor { position: relative; display: grid; gap: 1rem; overflow: hidden; padding: .9rem; border-top: 4px solid var(--team-primary); }.team-editor::before { position: absolute; inset: 0; pointer-events: none; background: radial-gradient(circle at 100% 0, color-mix(in srgb, var(--team-primary) 10%, transparent), transparent 32%); content: ""; }.team-editor > * { position: relative; }.team-editor > header { display: grid; grid-template-columns: auto minmax(0, 1fr) auto; align-items: center; gap: .7rem; }.team-editor > header h3 { margin: .1rem 0 0; font-size: 1rem; }.team-swatch { display: flex; overflow: hidden; width: 2.3rem; height: 2.3rem; border-radius: .55rem; box-shadow: 0 0 0 1px color-mix(in srgb, var(--team-primary) 35%, transparent); }.team-swatch i { flex: 1; background: var(--team-primary); }.team-swatch i:last-child { background: var(--team-secondary); }.team-identity { display: grid; grid-template-columns: 1fr .55fr auto auto; gap: .65rem; }.team-identity__wide { grid-column: 1 / -1; }.team-actions { grid-column: 1 / -1; display: flex; justify-content: flex-end; gap: .45rem; }.roster-editor { display: grid; gap: .6rem; padding-top: .8rem; border-top: 1px solid var(--color-border, #d9e0ea); }.roster-editor > header { display: flex; align-items: end; justify-content: space-between; gap: .7rem; }.roster-editor > header div { display: grid; gap: .1rem; }.roster-editor > header span { color: var(--team-primary); font-size: .56rem; font-weight: 760; text-transform: uppercase; }.roster-editor > header strong { font-size: .76rem; }.roster-editor > header small { color: var(--color-text-muted, #64748b); font-size: .57rem; }.roster-table { display: grid; gap: 1px; overflow: hidden; border: 1px solid var(--color-border, #d9e0ea); border-radius: .5rem; background: var(--color-border, #d9e0ea); }.roster-table__head, .roster-table form { display: grid; grid-template-columns: minmax(12rem, 1.5fr) minmax(5rem, .55fr) minmax(8rem, .8fr) 3.2rem auto; align-items: center; gap: .45rem; padding: .45rem .55rem; background: var(--color-surface, #fff); }.roster-table__head { background: var(--color-surface-subtle, #f1f5f9); color: var(--color-text-muted, #64748b); font-size: .52rem; font-weight: 730; text-transform: uppercase; }.roster-engine { display: grid; grid-template-columns: 3.2rem minmax(0, 1fr); align-items: center; gap: .45rem; min-width: 0; }.roster-engine > div { display: grid; min-width: 0; }.roster-engine strong, .roster-engine small { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }.roster-engine strong { font-size: .68rem; }.roster-engine small { color: var(--color-text-muted, #64748b); font-size: .56rem; }.order-input { width: 3.2rem; }.odds-chip { justify-self: start; padding: .22rem .32rem; border-radius: .3rem; background: color-mix(in srgb, var(--team-primary) 10%, transparent); color: var(--team-primary); font-size: .58rem; }.roster-actions { display: flex; gap: .3rem; }.roster-empty { margin: 0; padding: .65rem; border: 1px dashed var(--color-border-strong, #bbc5d3); border-radius: .45rem; color: var(--color-text-muted, #64748b); font-size: .65rem; }.add-engine { display: grid; grid-template-columns: minmax(10rem, 1fr) 4.5rem 5rem 8rem auto; align-items: end; gap: .45rem; padding: .55rem; border-radius: .5rem; background: color-mix(in srgb, var(--team-primary) 5%, var(--color-surface-subtle, #f1f5f9)); }
.fixture-workspace { padding-top: .2rem; }.game-projection { padding: .35rem .55rem; border-radius: .4rem; background: var(--color-surface-subtle, #f1f5f9); color: var(--color-accent, #315fcc); font-size: .67rem; }.fixture-layout { display: grid; grid-template-columns: minmax(20rem, .7fr) minmax(0, 1.3fr); align-items: start; gap: .85rem; }.fixture-builder { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: .7rem; padding: .9rem; position: sticky; top: calc(var(--app-header-height, 0px) + 1rem); }.fixture-builder header, .fixture-builder__wide, .fixture-summary, .fixture-builder > button { grid-column: 1 / -1; }.fixture-builder h3 { margin: .14rem 0 0; font-size: .92rem; }.fixture-summary { display: grid; grid-template-columns: repeat(3, 1fr); gap: 1px; overflow: hidden; border: 1px solid var(--color-border, #d9e0ea); border-radius: .45rem; background: var(--color-border, #d9e0ea); }.fixture-summary div { display: grid; gap: .15rem; padding: .55rem; background: var(--color-surface-subtle, #f1f5f9); }.fixture-summary span { color: var(--color-text-muted, #64748b); font-size: .52rem; text-transform: uppercase; }.fixture-summary strong { font-size: .66rem; }.fixture-list { display: grid; gap: .65rem; }.fixture-card { display: grid; gap: .75rem; padding: .85rem; }.fixture-card > header { display: flex; align-items: start; justify-content: space-between; gap: .7rem; }.fixture-card > header span { color: var(--color-accent, #315fcc); font-size: .54rem; font-weight: 760; text-transform: uppercase; }.fixture-card h3 { margin: .12rem 0 0; font-size: .88rem; }.fixture-card header p { margin: .18rem 0 0; color: var(--color-text-muted, #64748b); font-size: .62rem; }.fixture-card > dl { display: grid; grid-template-columns: repeat(4, 1fr); gap: 1px; margin: 0; background: var(--color-border, #d9e0ea); }.fixture-card > dl div { padding: .5rem; background: var(--color-surface-subtle, #f1f5f9); }.fixture-card dt { color: var(--color-text-muted, #64748b); font-size: .5rem; text-transform: uppercase; }.fixture-card dd { margin: .13rem 0 0; font-size: .62rem; font-weight: 700; }.fixture-games { display: flex; flex-wrap: wrap; gap: .35rem; }.fixture-games a { display: flex; gap: .35rem; padding: .3rem .42rem; border: 1px solid var(--color-border, #d9e0ea); border-radius: .35rem; color: inherit; font-size: .56rem; text-decoration: none; }.fixture-games a:hover { border-color: var(--color-accent, #315fcc); }.fixture-games > span { color: var(--color-text-muted, #64748b); font-size: .6rem; }.fixture-schedule { display: grid; grid-template-columns: 1fr auto; gap: .45rem; }.fixture-card footer { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: .35rem; padding-top: .65rem; border-top: 1px solid var(--color-border, #d9e0ea); }.fixture-empty { display: grid; min-height: 12rem; place-content: center; text-align: center; }.fixture-empty strong { font-size: .82rem; }.fixture-empty p { margin: .3rem 0 0; color: var(--color-text-muted, #64748b); font-size: .65rem; }
@media (max-width: 78rem) { .relay-command-bar { grid-template-columns: repeat(3, 1fr); }.visibility-control { grid-column: 1 / -1; border-top: 1px solid var(--color-border, #d9e0ea); }.fixture-layout { grid-template-columns: 1fr; }.fixture-builder { position: static; }.new-team { grid-template-columns: repeat(2, 1fr); }.new-team__wide { grid-column: auto; }.roster-table { overflow-x: auto; }.roster-table__head, .roster-table form { min-width: 42rem; } }
@media (max-width: 46rem) { .relay-command-bar { grid-template-columns: 1fr; }.relay-command-bar > div { border-right: 0; border-bottom: 1px solid var(--color-border, #d9e0ea); }.visibility-control { align-items: stretch; flex-direction: column; grid-column: auto; }.relay-section__heading { align-items: stretch; flex-direction: column; }.new-team, .team-identity, .fixture-builder { grid-template-columns: 1fr; }.team-identity__wide, .fixture-builder header, .fixture-builder__wide, .fixture-summary, .fixture-builder > button { grid-column: auto; }.team-editor > header { grid-template-columns: auto 1fr; }.team-editor > header > :last-child { grid-column: 1 / -1; }.add-engine { grid-template-columns: 1fr 1fr; }.add-engine label:first-child { grid-column: 1 / -1; }.add-engine button { grid-column: 1 / -1; }.fixture-summary { grid-template-columns: 1fr; }.fixture-card > dl { grid-template-columns: repeat(2, 1fr); } }
</style>
