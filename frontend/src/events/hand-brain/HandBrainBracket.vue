<script setup lang="ts">
import { computed } from 'vue'
import EventBracket, { type BracketMatch } from '../EventBracket.vue'
import type { HandBrainMatch, HandBrainTeam } from './types'

const props = defineProps<{ matches: HandBrainMatch[]; teams: HandBrainTeam[]; selected?: number | undefined; floating?: boolean }>()
const emit = defineEmits<{ select: [match: HandBrainMatch, button: HTMLButtonElement]; cheer: [team: HandBrainTeam] }>()
const rounds = ['Play-in', 'Quarterfinals', 'Semifinals', 'Final'].map((title, index) => ({ title, x: 12 + index * 273, number: '0' + (index + 1) }))
const positions = computed<Record<string, [number, number]>>(() => ({
  P1: [12, props.matches.some(match => match.key === 'P3') ? 295 : 225],
  P2: [12, props.matches.some(match => match.key === 'P3') ? 205 : 45],
  P3: [12, 25],
  P4: [12, 115],
  Q1: [285, 25], Q2: [285, 115], Q3: [285, 205], Q4: [285, 295],
  S1: [558, 70], S2: [558, 250], F: [831, 160],
}))
const nodes = computed<BracketMatch[]>(() => props.matches.map(match => {
  const [x, y] = positions.value[match.key] ?? [0, 0]
  return { id: match.id, key: match.key, x, y, sources: match.sources, entrantIds: match.team_ids, status: match.status, winnerId: match.winner_team_id, score: match.quads.at(-1)?.score ?? [] }
}))

function select(id: number, button: HTMLButtonElement): void {
  const match = props.matches.find(item => item.id === id)
  if (match) emit('select', match, button)
}

function cheer(id: number): void {
  const team = props.teams.find(item => item.id === id)
  if (team) emit('cheer', team)
}
</script>

<template>
  <EventBracket :matches="nodes" :entrants="teams" :rounds="rounds" :width="1070" :height="floating ? 382 : 410"
    :selected="selected" :floating="Boolean(floating)" @select="select" @cheer="cheer" />
</template>
