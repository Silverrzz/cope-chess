<script setup lang="ts">
import { computed } from "vue";

import StatusPill from "@/components/public/StatusPill.vue";
import type { EventCastMember } from "@/types/events";
import type { SecretQueenContest } from "./types";

const props = defineProps<{ contests: SecretQueenContest[]; cast: EventCastMember[]; selectedContest?: number | null }>();
const emit = defineEmits<{ select: [contest: number] }>();
const groups = computed(() => props.contests.filter(contest => contest.kind === "group"));
const bracket = computed(() => ["Quarterfinal", "Semifinal", "Final"].map(title => ({
  title, contests: props.contests.filter(contest => contest.kind === "bracket" && contest.stage === title),
})));
const names = computed(() => new Map(props.cast.map(member => [Number(member.engine_version_id), member.display_name])));
</script>

<template>
  <div class="standings">
    <section v-if="groups.length" class="standings-section" aria-label="Swiss group standings">
      <h2>Group stage</h2>
      <div class="swiss-groups">
        <article v-for="group in groups" :key="group.id" class="competition-card">
          <header><h3>{{ group.title }}</h3><StatusPill :status="group.status" /></header>
          <table>
            <thead><tr><th scope="col">Engine</th><th scope="col">Points</th><th scope="col">Buchholz</th></tr></thead>
            <tbody>
              <tr v-for="row in group.standings" :key="row.engine_id">
                <th scope="row">{{ names.get(row.engine_id) }}<span v-if="group.qualified.includes(row.engine_id)" class="qualified">Qualified</span></th>
                <td>{{ row.points }}</td><td>{{ row.buchholz }}</td>
              </tr>
            </tbody>
          </table>
          <button v-if="group.games.length && selectedContest !== undefined" type="button" class="match-link" :aria-pressed="selectedContest === group.id" @click="emit('select', group.id)">View group games</button>
        </article>
      </div>
    </section>
    <section v-if="bracket.some(stage => stage.contests.length)" class="standings-section" aria-label="Knockout bracket">
      <h2>Knockout bracket</h2>
      <div class="bracket">
        <section v-for="stage in bracket" :key="stage.title" class="bracket-stage">
          <h3>{{ stage.title }}</h3>
          <article v-for="match in stage.contests" :key="match.id" class="competition-card">
            <header><strong>{{ match.title }}</strong><StatusPill :status="match.status" /></header>
            <ul v-if="match.engine_ids.length">
              <li v-for="id in match.engine_ids" :key="id" :class="{ qualified: id === match.winner_engine_id }">{{ names.get(id) }}</li>
            </ul>
            <p v-else>TBD</p>
            <p v-if="match.winning_pair">Pair {{ match.winning_pair }} · {{ match.result }}</p>
            <button v-if="match.games.length && selectedContest !== undefined" type="button" class="match-link" :aria-pressed="selectedContest === match.id" @click="emit('select', match.id)">View match games</button>
          </article>
        </section>
      </div>
    </section>
  </div>
</template>

<style scoped>
.standings, .standings-section, .bracket-stage { display: grid; gap: 1rem; align-content: start; min-width: 0; }
.standings { gap: 2rem; }
.swiss-groups { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 24rem), 1fr)); gap: .8rem; }
.bracket { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: .8rem; }
.competition-card { display: grid; gap: .65rem; padding: .9rem; border: 1px solid var(--event-view-border); border-radius: var(--radius-md); background: #140d20; }
.competition-card header { display: flex; justify-content: space-between; align-items: center; gap: .5rem; }
.competition-card h3, .bracket-stage h3 { font-size: 1rem; }
.competition-card table { width: 100%; font-size: .85rem; font-variant-numeric: tabular-nums; }
.competition-card :is(th, td) { text-align: right; padding: .35rem 0 .35rem .4rem; border-bottom: 1px solid var(--event-view-border); }
.competition-card th:first-child { text-align: left; padding-left: 0; }
.competition-card ul { list-style: none; }
.competition-card p { color: var(--event-view-muted); font-size: .8rem; }
.qualified { color: var(--event-view-accent); font-weight: 750; }
span.qualified { margin-left: .55rem; font-size: .72rem; }
.match-link { justify-self: start; background: transparent; color: var(--event-view-accent); cursor: pointer; padding: .2rem 0; text-decoration: underline; }
@media (max-width: 44rem) { .bracket { grid-template-columns: 1fr; } }
</style>
