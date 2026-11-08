<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import ChessBoard from '@/components/chess/ChessBoard.vue'
import { gameResource, ratingsResource, type EmbeddedGame, type EmbeddedRatings } from '@/api/form-resources'

const props = defineProps<{ kind: 'game' | 'board' | 'ratings'; value: string }>()
const game = ref<EmbeddedGame | null>(null)
const ratings = ref<EmbeddedRatings | null>(null)
const error = ref('')
const loading = ref(false)
const ply = ref(0)
const validBoard = computed(() => {
  if (props.value === 'startpos') return true
  const [position, turn] = props.value.split(/\s+/)
  const ranks = position?.split('/') ?? []
  return (turn === 'w' || turn === 'b') && ranks.length === 8 && ranks.every(rank =>
    /^[prnbqkPRNBQK1-8]+$/.test(rank) && [...rank].reduce((sum, piece) => sum + (Number(piece) || 1), 0) === 8)
})

watch(() => [props.kind, props.value] as const, async ([kind, value], _, cleanup) => {
  game.value = null
  ratings.value = null
  error.value = ''
  ply.value = 0
  loading.value = kind !== 'board'
  if (kind === 'board') return
  const controller = new AbortController()
  cleanup(() => controller.abort())
  try {
    if (kind === 'game') {
      const result = await gameResource(value, controller.signal)
      if (!controller.signal.aborted) game.value = result
    } else {
      const result = await ratingsResource(value, controller.signal)
      if (!controller.signal.aborted) ratings.value = result
    }
  } catch {
    if (!controller.signal.aborted) error.value = 'This Cope resource is currently unavailable.'
  } finally {
    if (!controller.signal.aborted) loading.value = false
  }
}, { immediate: true })
</script>

<template>
  <figure class="form-embed" :aria-busy="loading">
    <p v-if="loading" role="status">Loading Cope {{ kind === 'game' ? 'game' : 'rating list' }}…</p>
    <p v-else-if="error">{{ error }}</p>
    <template v-else-if="kind === 'board'">
      <ChessBoard v-if="validBoard" class="embed-board" :fen="value" :controls="false" compact label="Position from the form" />
      <p v-else>Enter a valid chess position in FEN.</p>
    </template>
    <template v-else-if="game">
      <figcaption><strong>{{ game.title }}</strong><span>{{ game.tournament }}<template v-if="game.result"> · {{ game.result }}</template></span></figcaption>
      <ChessBoard v-model="ply" class="embed-board" :fen="game.fen" :moves="game.moves" :san-moves="game.san" :variant="game.variant" :label="game.title" compact />
      <RouterLink :to="game.url">Open game</RouterLink>
    </template>
    <template v-else-if="ratings">
      <figcaption><strong>{{ ratings.title }}</strong><span>Top 10 · live ratings</span></figcaption>
      <div v-if="ratings.rows.length" class="embed-table">
        <table>
          <thead><tr><th scope="col">#</th><th scope="col">Engine</th><th scope="col">Elo</th></tr></thead>
          <tbody>
            <tr v-for="(row, index) in ratings.rows" :key="index">
              <td>{{ index + 1 }}</td><th scope="row">{{ row.name }} <small>{{ row.version }}</small></th>
              <td>{{ Math.round(row.elo) }} <small v-if="row.error_margin !== null">± {{ Math.round(row.error_margin) }}</small></td>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-else>This list has no ratings yet.</p>
      <RouterLink :to="ratings.url">Open rating list</RouterLink>
    </template>
  </figure>
</template>

<style scoped>
.form-embed { margin: 1rem 0; padding: clamp(.85rem, 3vw, 1.25rem); display: grid; gap: .75rem; border: 1px solid var(--color-border); border-radius: var(--radius-lg); background: var(--color-surface-raised); }
.form-embed p { margin: 0; color: var(--color-text-muted); }
.form-embed figcaption { display: grid; gap: .15rem; }
.form-embed figcaption span, .form-embed small { color: var(--color-text-muted); font-size: .8em; }
.embed-board { width: min(100%, 23rem); justify-self: center; }
.embed-table { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; font-size: .85rem; }
th, td { padding: .45rem; border-bottom: 1px solid var(--color-border); text-align: left; }
td:last-child, th:last-child { text-align: right; white-space: nowrap; }
.form-embed a { justify-self: start; font-size: .85rem; }
</style>
