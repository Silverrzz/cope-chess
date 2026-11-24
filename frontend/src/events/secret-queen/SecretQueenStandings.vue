<script setup lang="ts">
import { computed } from "vue";

import type { EventCastMember } from "@/types/events";
import EventBracket, { bracketGroupHeight, type BracketGroup, type BracketMatch } from "../EventBracket.vue";
import { secretQueenTheme } from "./theme";
import type { SecretQueenContest } from "./types";

const props = defineProps<{ contests: SecretQueenContest[]; cast: EventCastMember[]; selectedContest?: number | null; preview?: boolean }>();
const emit = defineEmits<{ select: [contest: number] }>();
const columnStep = 273;
const groupGap = 9;
const groups = computed(() => props.contests.filter(contest => contest.kind === "group"));
const stages = computed(() => ["Quarterfinal", "Semifinal", "Final"].map(title => ({
  title, matches: props.contests.filter(contest => contest.kind === "bracket" && contest.stage === title),
})).filter(stage => stage.matches.length));
const seeds = computed(() => {
  const list: Array<{ groupId: number; rank: number; name: string; tag: string; id: number | undefined }> = [];
  const maximum = Math.max(0, ...groups.value.map(group => group.qualifier_count));
  for (let rank = 0; rank < maximum; rank += 1) {
    for (const [index, group] of groups.value.entries()) {
      if (rank < group.qualifier_count) {
        list.push({ groupId: group.id, rank, name: group.title + " · #" + (rank + 1), tag: `G${index + 1} #${rank + 1}`, id: group.qualified[rank] });
      }
    }
  }
  return list;
});
const sourceLabels = computed(() => Object.fromEntries(seeds.value.map((seed, index) => [index + 1, seed.name])));
const sourceTags = computed(() => groups.value.length > 1 ? Object.fromEntries(seeds.value.map((seed, index) => [index + 1, seed.tag])) : undefined);
const seeded = computed(() => new Map(seeds.value.flatMap((seed, index) => seed.id === undefined ? [] : [[seed.id, index + 1]])));
const entrants = computed(() => props.cast.flatMap(member => member.engine_version_id == null ? [] : [{
  id: Number(member.engine_version_id),
  name: member.display_name,
  color: member.accent_color || "var(--color-accent)",
  ...(seeded.value.has(Number(member.engine_version_id)) ? { seed: seeded.value.get(Number(member.engine_version_id))! } : {}),
}]));
const groupRows = computed(() => groups.value.map(group => {
  const standings = group.standings.length ? group.standings : group.engine_ids.map(engine_id => ({ engine_id, points: 0, buchholz: 0 }));
  const groupSeeds = seeds.value.flatMap((seed, index) => seed.groupId === group.id ? [[seed.rank, index + 1] as const] : []);
  const seedsByRank = new Map(groupSeeds);
  return standings.map((row, rank) => ({
    id: row.engine_id,
    points: row.points,
    buchholz: row.buchholz,
    seed: seedsByRank.get(rank),
    qualified: group.qualified.includes(row.engine_id),
  }));
}));
const groupsHeight = computed(() => groupRows.value.reduce((height, rows) => height + bracketGroupHeight(rows.length, props.preview) + groupGap, 0) - groupGap);
const rowsHeight = computed(() => Math.max(groupsHeight.value, ...stages.value.map(stage => stage.matches.length * 90), 90));
const rounds = computed(() => ["Swiss groups", ...stages.value.map(stage => stage.title === "Final" ? stage.title : stage.title + "s")]
  .map((title, index) => ({ title, x: 12 + index * columnStep, number: String(index + 1).padStart(2, "0") })));
const groupNodes = computed<BracketGroup[]>(() => {
  let y = 16 + Math.round((rowsHeight.value - groupsHeight.value) / 2);
  return groups.value.map((group, index) => {
    const rows = groupRows.value[index]!;
    const node = { id: group.id, title: group.title, x: rounds.value[0]!.x, y, status: group.status, selectable: group.games.length > 0, preview: props.preview, rows };
    y += bracketGroupHeight(rows.length, props.preview) + groupGap;
    return node;
  });
});
const bracket = computed<BracketMatch[]>(() => {
  const winners = new Map(props.contests.map(match => [match.key, match.winner_engine_id]));
  return stages.value.flatMap((stage, column) => stage.matches.map((match, row) => {
    const entrantIds = match.engine_ids.length ? match.engine_ids : match.sources.map(source => (
      (typeof source === "string" ? winners.get(source) : seeds.value[source - 1]?.id) ?? null
    ));
    return {
      id: match.id,
      key: match.title,
      x: rounds.value[column + 1]!.x,
      y: 16 + Math.round((row + .5) * rowsHeight.value / stage.matches.length - 38),
      sources: match.sources.map(source => typeof source === "string" ? source.toUpperCase() : source),
      entrantIds,
      status: match.status,
      winnerId: match.winner_engine_id,
      score: pairScore(match),
      selectable: match.games.length > 0,
    };
  }));
});
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
  <div id="secret-queen-competition" class="competition-overview" :style="secretQueenTheme">
    <EventBracket frameless :groups="groupNodes" :matches="bracket" :entrants="entrants" :rounds="rounds"
      :width="rounds.length * columnStep - 29" :height="rowsHeight + 48" :selected="selectedContest ?? undefined"
      :source-labels="sourceLabels" :source-tags="sourceTags" @select="emit('select', $event)" />
  </div>
</template>

<style scoped>
.competition-overview {
  min-width: 0;
  scroll-margin-top: calc(var(--header-height, 4rem) + 1rem);
  --color-surface-raised: var(--event-view-surface);
  --color-border: var(--event-view-border);
  --color-border-strong: color-mix(in srgb, var(--event-view-accent) 42%, transparent);
  --color-text: var(--event-view-text);
  --color-text-muted: var(--event-view-muted);
  --color-text-faint: color-mix(in srgb, var(--event-view-muted) 78%, var(--event-view-surface));
  --color-accent: var(--event-view-accent);
  --color-success: var(--event-view-highlight);
  --color-focus: var(--event-view-accent);
  --bracket-advanced-wire: color-mix(in srgb, var(--event-view-accent) 62%, transparent);
}
</style>
