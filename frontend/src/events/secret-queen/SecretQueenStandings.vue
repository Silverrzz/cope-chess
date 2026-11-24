<script setup lang="ts">
import { computed } from "vue";

import StatusPill from "@/components/public/StatusPill.vue";
import AppIcon from "@/components/ui/AppIcon.vue";
import type { EventCastMember } from "@/types/events";
import EventBracket, { type BracketMatch } from "../EventBracket.vue";
import type { SecretQueenContest } from "./types";

const props = defineProps<{ contests: SecretQueenContest[]; cast: EventCastMember[]; selectedContest?: number | null }>();
const emit = defineEmits<{ select: [contest: number] }>();
const groups = computed(() => props.contests.filter(contest => contest.kind === "group"));
const stages = computed(() => ["Quarterfinal", "Semifinal", "Final"].map(title => ({
  title, matches: props.contests.filter(contest => contest.kind === "bracket" && contest.stage === title),
})).filter(stage => stage.matches.length));
const seeds = computed(() => {
  const list: Array<{ name: string; id: number | undefined }> = [];
  const maximum = Math.max(0, ...groups.value.map(group => group.qualifier_count));
  for (let rank = 0; rank < maximum; rank += 1) {
    for (const group of groups.value) {
      if (rank < group.qualifier_count) list.push({ name: group.title + " · #" + (rank + 1), id: group.qualified[rank] });
    }
  }
  return list;
});
const sourceLabels = computed(() => Object.fromEntries(seeds.value.map((seed, index) => [index + 1, seed.name])));
const seeded = computed(() => new Map(seeds.value.flatMap((seed, index) => seed.id === undefined ? [] : [[seed.id, index + 1]])));
const entrants = computed(() => props.cast.flatMap(member => member.engine_version_id == null ? [] : [{
  id: Number(member.engine_version_id),
  name: member.display_name,
  color: member.accent_color || "var(--color-accent)",
  ...(seeded.value.has(Number(member.engine_version_id)) ? { seed: seeded.value.get(Number(member.engine_version_id))! } : {}),
}]));
const names = computed(() => new Map(entrants.value.map(entrant => [entrant.id, entrant.name])));
const rowsHeight = computed(() => Math.max(1, ...stages.value.map(stage => stage.matches.length)) * 90);
const rounds = computed(() => stages.value.map((stage, index) => ({
  title: stage.title === "Final" ? stage.title : stage.title + "s", x: 12 + index * 273,
})));
const bracket = computed<BracketMatch[]>(() => {
  const winners = new Map(props.contests.map(match => [match.key, match.winner_engine_id]));
  return stages.value.flatMap((stage, column) => stage.matches.map((match, row) => {
    const entrantIds = match.engine_ids.length ? match.engine_ids : match.sources.map(source => (
      (typeof source === "string" ? winners.get(source) : seeds.value[source - 1]?.id) ?? null
    ));
    return {
      id: match.id,
      key: match.title,
      x: rounds.value[column]!.x,
      y: Math.round((row + .5) * rowsHeight.value / stage.matches.length - 38),
      sources: match.sources.map(source => typeof source === "string" ? source.toUpperCase() : source),
      entrantIds,
      status: match.status,
      winnerId: match.winner_engine_id,
      score: pairScore(match),
      selectable: match.games.length > 0,
    };
  }));
});
const groupProgress = computed(() => new Map(groups.value.map(group => {
  const finished = group.games.filter(game => game.status === "finished").length;
  const total = Math.max(finished, group.rounds * Math.floor(group.engine_ids.length / 2) * 2);
  const round = Math.max(1, ...group.games.map(game => game.round));
  return [group.id, { finished, total, round }];
})));

function pairScore(match: SecretQueenContest): number[] {
  const latest = match.winning_pair ?? match.games.at(-1)?.pair_index;
  const results = match.games.filter(game => game.pair_index === latest && game.result);
  if (!results.length) return [];
  return match.engine_ids.map(id => results.reduce((points, game) => (
    points + (game.result === "1/2-1/2" ? .5 : (
      (game.result === "1-0" && game.white_engine_id === id) || (game.result === "0-1" && game.black_engine_id === id)
    ) ? 1 : 0)
  ), 0));
}
</script>

<template>
  <div id="secret-queen-competition" class="competition-overview">
    <section v-if="groups.length" class="competition-section" aria-labelledby="secret-queen-groups">
      <header class="section-title"><h2 id="secret-queen-groups">Swiss groups</h2></header>
      <div class="swiss-groups">
        <article v-for="group in groups" :key="group.id" class="group-card" :class="{ 'group-card--selected': selectedContest === group.id }">
          <header class="group-heading">
            <div>
              <h3>{{ group.title }}</h3>
              <p >Round {{ groupProgress.get(group.id)?.round }} of {{ group.rounds }} · {{ group.qualifier_count }} {{ group.qualifier_count === 1 ? 'qualifies' : 'qualify' }}</p>
            </div>
            <StatusPill :status="group.status" />
          </header>
          <progress v-if="groupProgress.get(group.id)?.total" class="group-progress" :value="groupProgress.get(group.id)?.finished" :max="groupProgress.get(group.id)?.total" :aria-label="group.title + ' games completed'"></progress>
          <div class="group-table">
            <table>
              <thead><tr><th scope="col" class="rank-cell">Rank</th><th scope="col">Engine</th><th scope="col">Pts</th><th scope="col"><abbr title="Sum of opponents' scores, counted once per completed game">Buchholz</abbr></th></tr></thead>
              <tbody>
                <tr v-for="(row, index) in group.standings" :key="row.engine_id" :class="{ 'in-qualifying-places': index < group.qualifier_count, 'qualification-cut': index === group.qualifier_count && group.qualifier_count > 0 }">
                  <td class="rank-cell">{{ index + 1 }}</td>
                  <th scope="row"><span>{{ names.get(row.engine_id) }}</span><small v-if="group.qualified.includes(row.engine_id)" class="qualified">Qualified</small></th>
                  <td class="points-cell">{{ row.points }}</td><td>{{ row.buchholz }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <footer v-if="group.games.length" class="group-footer">
            <span>{{ groupProgress.get(group.id)?.finished }} / {{ groupProgress.get(group.id)?.total }} games</span>
            <button type="button" class="button button--ghost button--small" :aria-pressed="selectedContest === group.id" @click="emit('select', group.id)">View games <AppIcon name="arrow-right" :size="14" /></button>
          </footer>
        </article>
      </div>
    </section>
    <EventBracket v-if="bracket.length" :matches="bracket" :entrants="entrants" :rounds="rounds"
      :width="stages.length * 273 - 34" :height="rowsHeight + 40" :selected="selectedContest ?? undefined" :source-labels="sourceLabels" @select="emit('select', $event)" />
  </div>
</template>

<style scoped>
.competition-overview { display: grid; gap: 1.5rem; min-width: 0; scroll-margin-top: calc(var(--header-height, 4rem) + 1rem); }
.competition-section { display: grid; gap: .85rem; min-width: 0; }
.section-title h2 { margin: 0; font-size: 1.1rem; }
.swiss-groups { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 22rem), 1fr)); gap: .85rem; align-items: start; }
.group-card { min-width: 0; overflow: hidden; border: 1px solid var(--color-border); border-radius: var(--radius-lg, .75rem); background: var(--color-surface); color: var(--color-text); }
.group-card--selected { border-color: var(--color-accent); }
.group-heading, .group-footer { display: flex; justify-content: space-between; align-items: center; gap: .75rem; padding: .8rem 1rem; }
.group-heading h3 { margin: 0; font-size: 1rem; }
.group-heading p { margin: .2rem 0 0; color: var(--color-text-muted); font-size: .72rem; }
.group-progress { display: block; width: 100%; height: .2rem; border: 0; appearance: none; background: var(--color-border); accent-color: var(--color-accent); }
.group-progress::-webkit-progress-bar { background: var(--color-border); }
.group-progress::-webkit-progress-value { background: var(--color-accent); }
.group-progress::-moz-progress-bar { background: var(--color-accent); }
.group-table { overflow-x: auto; }
.group-table table { width: 100%; border-collapse: collapse; font-size: .82rem; font-variant-numeric: tabular-nums; }
.group-table :is(th, td) { padding: .6rem .65rem; text-align: right; border-block-end: 1px solid var(--color-border); }
.group-table th:nth-child(2), .group-table th[scope="row"] { width: 100%; text-align: left; }
.group-table thead th { color: var(--color-text-muted); font-weight: 700; font-size: .66rem; white-space: nowrap; }
.group-table tbody th { font-weight: 650; word-break: break-word; }
.group-table .rank-cell { padding-inline-start: 1rem; text-align: start; color: var(--color-text-muted); }
.group-table .points-cell { font-weight: 760; }
.group-table tr.in-qualifying-places { background: color-mix(in srgb, var(--color-success) 9%, transparent); }
.group-table tr.in-qualifying-places td:first-child { box-shadow: inset .2rem 0 var(--color-success); }
.group-table tr.qualification-cut > * { border-block-start: 1px dashed var(--color-border-strong); }
.qualified { display: inline-block; margin-inline-start: .5rem; color: var(--color-success); font-size: .65rem; white-space: nowrap; }
.group-footer { padding-block: .4rem; color: var(--color-text-muted); font-size: .7rem; }
</style>
