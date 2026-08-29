<script setup lang="ts">
import { clockLabel, engineName, gameHref, statusLabel } from './format'
import type { CrossTableGame } from './types'
import CrossTableBoard from './CrossTableBoard.vue'
import StreamIndicator from './StreamIndicator.vue'

const props = defineProps<{
  games: CrossTableGame[]
  engines?: Record<string, string>
  streamState: 'connecting' | 'live' | 'reconnecting' | 'closed'
}>()

function name(game: CrossTableGame, side: 'white' | 'black'): string {
  return side === 'white'
    ? engineName(props.engines, game.white_engine_id, game.white_name)
    : engineName(props.engines, game.black_engine_id, game.black_name)
}

function clock(game: CrossTableGame, side: 'white' | 'black'): string {
  return clockLabel(game.clocks_ms?.[side])
}

function evaluation(game: CrossTableGame, side: 'white' | 'black'): string {
  const score = game.evaluations?.[side]
  if (!score) return '-'
  const factor = side === 'white' ? 1 : -1
  const bound = factor === 1
    ? score.score_bound
    : score.score_bound === 'lowerbound'
      ? 'upperbound'
      : score.score_bound === 'upperbound'
        ? 'lowerbound'
        : null
  const prefix = bound === 'lowerbound' ? '≥' : bound === 'upperbound' ? '≤' : ''
  if (score.eval_mate !== null && score.eval_mate !== undefined) {
    return `${prefix}#${factor * score.eval_mate}`
  }
  if (score.eval_cp !== null && score.eval_cp !== undefined) {
    const value = factor * score.eval_cp / 100
    return `${prefix}${value >= 0 ? '+' : ''}${value.toFixed(2)}`
  }
  return '-'
}

function evaluationWinner(game: CrossTableGame, side: 'white' | 'black'): 'white' | 'black' | 'even' {
  const score = game.evaluations?.[side]
  if (!score) return 'even'
  const value = score.eval_mate ?? score.eval_cp
  if (value === null || value === undefined || value === 0) return 'even'
  return (side === 'white' ? value : -value) > 0 ? 'white' : 'black'
}

function boardLabel(game: CrossTableGame): string {
  return `${name(game, 'white')} versus ${name(game, 'black')}`
}
</script>

<template>
  <section class="cross-table" aria-labelledby="cross-table-title">
    <header class="cross-table__header">
      <div>
        <p>Cross-table</p>
        <h2 id="cross-table-title">All active games</h2>
      </div>
      <div class="cross-table__status">
        <StreamIndicator :state="streamState" />
        <strong>{{ games.length.toLocaleString() }} {{ games.length === 1 ? 'board' : 'boards' }}</strong>
      </div>
    </header>

    <div v-if="games.length" class="cross-table__grid">
      <RouterLink
        v-for="game in games"
        :key="game.id"
        v-memo="[game.fen, game.last_move, game.ply, game.status, game.result, game.clocks_ms.white, game.clocks_ms.black, game.evaluations?.white?.eval_cp, game.evaluations?.white?.eval_mate, game.evaluations?.white?.score_bound, game.evaluations?.black?.eval_cp, game.evaluations?.black?.eval_mate, game.evaluations?.black?.score_bound, game.active_side, game.running]"
        class="cross-game"
        :to="gameHref(game)"
        :aria-label="`Open ${boardLabel(game)}`"
      >
        <header class="cross-game__header">
          <span>Round {{ game.round ?? '-' }}<template v-if="game.pair_index"> · Board {{ game.pair_index }}</template></span>
          <strong :data-status="game.status">{{ statusLabel(game.status) }}</strong>
        </header>

        <div class="cross-game__player" :class="{ active: game.running && game.active_side === 'black' }">
          <span class="cross-game__piece cross-game__piece--black" aria-hidden="true"></span>
          <strong :title="name(game, 'black')">{{ name(game, 'black') }}</strong>
          <span class="cross-game__metrics">
            <span class="cross-game__eval" :data-winner="evaluationWinner(game, 'black')">{{ evaluation(game, 'black') }}</span>
            <span aria-hidden="true">|</span>
            <time>{{ clock(game, 'black') }}</time>
          </span>
        </div>

        <CrossTableBoard :fen="game.fen" :last-move="game.last_move || null" :label="boardLabel(game)" />

        <div class="cross-game__player" :class="{ active: game.running && game.active_side === 'white' }">
          <span class="cross-game__piece cross-game__piece--white" aria-hidden="true"></span>
          <strong :title="name(game, 'white')">{{ name(game, 'white') }}</strong>
          <span class="cross-game__metrics">
            <span class="cross-game__eval" :data-winner="evaluationWinner(game, 'white')">{{ evaluation(game, 'white') }}</span>
            <span aria-hidden="true">|</span>
            <time>{{ clock(game, 'white') }}</time>
          </span>
        </div>

        <footer>
          <span>{{ game.ply ? `Move ${Math.ceil(game.ply / 2)}` : 'Awaiting first move' }}</span>
          <strong>Open game</strong>
        </footer>
      </RouterLink>
    </div>

    <div v-else class="cross-table__empty">
      <strong>No active games</strong>
      <span>Boards will appear here as games are assigned.</span>
    </div>
  </section>
</template>

<style scoped>
.cross-table {
  overflow: hidden;
  border: 1px solid var(--color-border, #d5dbe1);
  border-radius: var(--radius-lg, 0.75rem);
  background: var(--color-surface-sunken, #edf2f7);
}

.cross-table__header {
  display: flex;
  align-items: end;
  justify-content: space-between;
  gap: 1rem;
  padding: clamp(0.85rem, 1.5vw, 1.2rem);
  border-block-end: 1px solid var(--color-border, #d5dbe1);
  background: var(--color-surface, #fff);
}

.cross-table__header p,
.cross-table__header h2,
.cross-table__header > div:first-child > span {
  margin: 0;
}

.cross-table__header p {
  color: var(--color-accent, #2f78c4);
  font-size: 0.63rem;
  font-weight: 780;
  letter-spacing: 0.06em;
  text-transform: uppercase;
}

.cross-table__header h2 {
  margin-block: 0.15rem;
  font-size: 1.05rem;
}

.cross-table__header > div:first-child > span,
.cross-table__status > strong {
  color: var(--color-text-muted, #607080);
  font-size: 0.72rem;
}

.cross-table__status {
  display: flex;
  align-items: center;
  gap: 0.75rem;
}

.cross-table__status > strong {
  white-space: nowrap;
}

.cross-table__grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(min(100%, 17.5rem), 1fr));
  gap: clamp(0.55rem, 1vw, 0.85rem);
  padding: clamp(0.55rem, 1vw, 0.85rem);
}

.cross-game {
  contain: layout paint style;
  content-visibility: auto;
  contain-intrinsic-size: auto 27rem;
  display: grid;
  min-width: 0;
  gap: 0.42rem;
  padding: 0.55rem;
  border: 1px solid var(--color-border, #d5dbe1);
  border-radius: var(--radius-md, 0.5rem);
  background: var(--color-surface, #fff);
  color: var(--color-text, #17202a);
  text-decoration: none;
  transition: border-color 120ms ease, box-shadow 120ms ease, transform 120ms ease;
}

.cross-game:hover {
  border-color: var(--color-border-strong, #99a8bb);
  box-shadow: var(--shadow-sm, 0 2px 8px rgb(0 0 0 / 8%));
  transform: translateY(-1px);
}

.cross-game:focus-visible {
  outline: 2px solid var(--color-accent, #2f78c4);
  outline-offset: 2px;
}

.cross-game__header,
.cross-game__player,
.cross-game footer {
  display: flex;
  align-items: center;
  min-width: 0;
}

.cross-game__header {
  justify-content: space-between;
  gap: 0.5rem;
  color: var(--color-text-muted, #607080);
  font-size: 0.64rem;
  font-weight: 700;
}

.cross-game__header > strong {
  color: var(--color-warning, #835700);
  font-size: 0.6rem;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}

.cross-game__header > strong[data-status='live'] {
  color: var(--color-success, #16734d);
}

.cross-game__player {
  gap: 0.42rem;
  padding-inline: 0.1rem;
}

.cross-game__player > strong {
  min-width: 0;
  overflow: hidden;
  font-size: 0.76rem;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.cross-game__metrics {
  margin-inline-start: auto;
  display: flex;
  align-items: center;
  gap: 0.3rem;
  color: var(--color-text-muted, #607080);
  font-family: var(--font-mono, monospace);
  font-size: 0.7rem;
  font-variant-numeric: tabular-nums;
  font-weight: 700;
  white-space: nowrap;
}

.cross-game__eval[data-winner='white'] {
  color: var(--color-success, #16734d);
}

.cross-game__eval[data-winner='black'] {
  color: var(--color-danger, #b42318);
}

.cross-game__player.active .cross-game__metrics > time {
  color: var(--color-accent, #2f78c4);
}

.cross-game__piece {
  width: 0.62rem;
  height: 0.62rem;
  flex: 0 0 auto;
  border: 1px solid color-mix(in srgb, var(--color-text, #17202a) 52%, transparent);
  border-radius: 50%;
}

.cross-game__piece--black {
  background: var(--color-text, #17202a);
}

.cross-game__piece--white {
  background: var(--color-surface, #fff);
}

.cross-game footer {
  justify-content: space-between;
  gap: 0.5rem;
  padding-block-start: 0.15rem;
  color: var(--color-text-muted, #607080);
  font-size: 0.64rem;
}

.cross-game footer strong {
  color: var(--color-accent, #2f78c4);
}

.cross-table__empty {
  display: grid;
  place-items: center;
  gap: 0.25rem;
  min-height: 15rem;
  padding: 2rem;
  color: var(--color-text-muted, #607080);
  text-align: center;
}

.cross-table__empty strong {
  color: var(--color-text, #17202a);
}

.cross-table__empty span {
  font-size: 0.76rem;
}

@media (max-width: 36rem) {
  .cross-table__header {
    align-items: start;
  }

  .cross-table__header > div:first-child > span {
    display: none;
  }
}

@media (prefers-reduced-motion: reduce) {
  .cross-game {
    transition: none;
  }
}
</style>
