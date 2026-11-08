<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { api } from '@/api/client'
import ChessViewer from '@/components/chess/ChessViewer.vue'
import MoveList from '@/components/chess/MoveList.vue'
import { buildPositions, parseFen, positionFen, type BoardArrow } from '@/components/chess/chess'
import ChatPanel from '@/components/public/ChatPanel.vue'
import AppIcon from '@/components/ui/AppIcon.vue'
import type { MoveRecord, TournamentDetailResponse } from '@/components/public/types'
import type { EventDetailResponse } from '@/types/events'
import type { HandBrainPayload, HandBrainMatch, HandBrainSearch, HandBrainClock, HandBrainAnalysis, HandBrainTeam } from './types'
import HandBrainTeamCard from './HandBrainTeamCard.vue'
import HandBrainCheers from './HandBrainCheers.vue'
import HandBrainBracket from './HandBrainBracket.vue'
import { handBrainStandings } from './standings'
import HandBrainEvalGraph from './HandBrainEvalGraph.vue'
import StreamIndicator from '@/components/public/StreamIndicator.vue'

const props = withDefaults(defineProps<{ detail: EventDetailResponse; clockOffsetMs?: number }>(), { clockOffsetMs: 0 })
const payload = computed(() => props.detail.custom as HandBrainPayload)
const selectedMatchId = ref<number | null>(null)
const selectedGameId = ref<number | null>(null)
const data = ref<TournamentDetailResponse | null>(null)
const searches = ref<HandBrainSearch[]>([])
const analyses = ref<Record<string, HandBrainAnalysis>>({})
const clock = ref<HandBrainClock>({})
const now = ref(Date.now())
const selectedPly = ref(0)
const orientation = ref<'white' | 'black'>('white')
const connected = ref(false)
const error = ref('')
const tab = ref('standings')
const headingElement = ref<HTMLElement | null>(null)
const arenaElement = ref<HTMLElement | null>(null)
const boardColumnElement = ref<HTMLElement | null>(null)
let arenaFitFrame: number | undefined
let headingResizeObserver: ResizeObserver | null = null
let stream: EventSource | null = null
let clockTimer: number | undefined
let recoveryTimer: number | undefined
let pollTimer: number | undefined
let controller: AbortController | null = null
let lastLoadAt = 0
let snapshotPly = 0
let clockRevision = 0
const pendingMoves = new Map<number, MoveRecord>()
const match = computed(() => payload.value.matches.find(m => m.id === selectedMatchId.value) ?? payload.value.matches.find(m => m.status === 'live') ?? payload.value.matches[0] ?? null)
const allGames = computed(() => match.value?.quads.flatMap(q => q.games.map(game => ({ game, quad: q }))) ?? [])
const selection = computed(() => allGames.value.find(g => Number(g.game.id) === selectedGameId.value) ?? allGames.value.find(g => ['live','assigned','pending'].includes(g.game.status)) ?? allGames.value.at(-1))
const moves = computed(() => data.value?.viewer_moves ?? [])
const opening = computed(() => data.value?.opening ?? { name: 'Lichess UHO', fen: 'startpos' })
const startPly = computed(() => { const p = parseFen(opening.value.fen); return (p.fullmove - 1) * 2 + (p.turn === 'b' ? 1 : 0) })
const positions = computed(() => buildPositions(opening.value.fen, moves.value.map(m => m.uci)))
const currentFen = computed(() => positionFen(positions.value.at(-1)!))
const followed = computed(() => selectedPly.value >= moves.value.length)
const teamById = (id: number | null | undefined) => payload.value.teams.find(t => t.id === id) ?? null
const teams = computed(() => (match.value?.team_ids ?? []).map(id => teamById(id)).filter((t): t is HandBrainTeam => t !== null))
const whiteTeam = computed(() => {
  if (!selection.value) return teams.value[0] ?? null
  return teams.value.find(t => t.members.some(m => m.engine_id === Number(selection.value!.game.white_engine_id))) ?? teams.value[0] ?? null
})
const blackTeam = computed(() => teams.value.find(t => t.id !== whiteTeam.value?.id) ?? null)
const swapped = computed(() => (Number(selection.value?.game.game_number ?? 1) - 1) >= 2)
const liveStageTimeControls: Record<string, { initial_ms: number; increment_ms: number }> = {
  Quarterfinal: { initial_ms: 80000, increment_ms: 800 },
  Semifinal: { initial_ms: 120000, increment_ms: 1200 },
  Final: { initial_ms: 180000, increment_ms: 1800 }
}
const liveStageTimeControl = computed(() => props.detail.event.id === 6 && props.detail.event.slug === 'engine-hand-brain' && match.value ? liveStageTimeControls[match.value.stage] : undefined)
const tc = computed(() => selection.value?.quad.time_control ?? match.value?.time_control ?? liveStageTimeControl.value ?? payload.value.rules)
const tcLabel = computed(() => `${tc.value.initial_ms / 1000}+${tc.value.increment_ms / 1000}`)
const threads = computed(() => {
  const quad = selection.value?.quad
  const tournament = data.value?.tournament
  const assigned = quad && tournament && Number(tournament.id) === quad.tournament_id
    ? tournament.config?.engine_threads
    : undefined
  return quad?.threads ?? (typeof assigned === 'number' ? assigned : undefined) ?? match.value?.threads ?? payload.value.rules.threads
})
const threadLabel = computed(() => threads.value === 1 ? '1 thread per engine' : `Up to ${threads.value} threads per engine`)
const replayAnalyses = computed(() => {
  const result: Record<string, HandBrainAnalysis> = {}
  for (const search of [...searches.value].sort((a,b) => a.ply-b.ply)) if (search.ply <= startPly.value + selectedPly.value) result[String(search.engine_id)] = { ...search.analysis, root_fen: search.root_fen, role: search.role, pv: search.analysis.pv ?? search.bestmove }
  return followed.value ? { ...result, ...analyses.value } : result
})
const engineClocks = computed(() => {
  const values: Record<string, number> = {}
  for (const team of teams.value) for (const member of team.members) values[String(member.engine_id)] = tc.value.initial_ms
  const latest = [...searches.value].filter(s => s.ply <= startPly.value + selectedPly.value).sort((a,b) => a.ply-b.ply || (a.role === 'hand' ? 1 : -1)).at(-1)
  Object.assign(values, latest?.clocks ?? {})
  if (followed.value) {
    Object.assign(values, clock.value.engine_clocks_ms ?? {})
    const observed = Date.parse(clock.value.sent_at ?? clock.value.observed_at ?? '')
    if (clock.value.running && clock.value.active_engine_id && Number.isFinite(observed)) {
      const key = String(clock.value.active_engine_id)
      values[key] = Math.max(0, (values[key] ?? 0) - Math.max(0, now.value + props.clockOffsetMs - observed))
    }
  }
  return values
})
const phaseAnalysis = computed(() => replayAnalyses.value[String(clock.value.active_engine_id)])
const rootMatches = computed(() => phaseAnalysis.value?.root_fen?.split(' ').slice(0,4).join(' ') === currentFen.value.split(' ').slice(0,4).join(' '))
const liveThinking = computed(() => followed.value && !!clock.value.running && selection.value?.game.status === 'live')
const arrows = computed<BoardArrow[]>(() => {
  const move = phaseAnalysis.value?.pv?.split(' ')[0]
  if (!liveThinking.value || clock.value.phase !== 'hand' || !rootMatches.value || !move) return []
  return [{ move, color: clock.value.active_side === 'black' ? 'black' : 'white' }]
})
const rings = computed(() => {
  const pv = phaseAnalysis.value?.pv?.split(' ')[0]
  if (!liveThinking.value || clock.value.phase !== 'brain' || !rootMatches.value || !pv) return []
  const p = parseFen(currentFen.value)
  const index = (8 - Number(pv[1])) * 8 + 'abcdefgh'.indexOf(pv[0] ?? '')
  const piece = p.squares[index]
  return p.squares.flatMap((candidate,i) => piece && candidate === piece ? [`${'abcdefgh'[i % 8]}${8 - Math.floor(i / 8)}`] : [])
})
const standings = computed(() => handBrainStandings(payload.value))
function cell(a: number, b: number) {
  const fixture = payload.value.matches.find(m => m.team_ids.includes(a) && m.team_ids.includes(b))
  if (!fixture) return ''
  const index = fixture.team_ids.indexOf(a)
  const score = fixture.quads.reduce((sum,q) => sum + (q.score[index] ?? 0),0)
  return `${score}${fixture.status === 'live' ? ' ·' : ''}`
}
function selectMatch(value: HandBrainMatch) { selectedMatchId.value = value.id; selectedGameId.value = null }
function parseEvent<T>(event: Event): T | null { try { return JSON.parse((event as MessageEvent).data).data as T } catch { return null } }
function lastPly() { return moves.value.at(-1)?.ply ?? startPly.value }

function applyPendingMoves() {
  if (!data.value) return
  const wasLatest = followed.value
  let ply = lastPly()
  for (const key of pendingMoves.keys()) if (key <= ply) pendingMoves.delete(key)
  const added: MoveRecord[] = []
  while (pendingMoves.has(ply + 1)) {
    ply += 1
    added.push(pendingMoves.get(ply)!)
    pendingMoves.delete(ply)
  }
  if (!added.length) return
  data.value.viewer_moves = [...moves.value, ...added]
  if (wasLatest) selectedPly.value = moves.value.length
}

function requestRecovery(verifyGap = false) {
  if (recoveryTimer !== undefined || !selection.value) return
  recoveryTimer = window.setTimeout(() => {
    recoveryTimer = undefined
    if (!verifyGap || pendingMoves.size || snapshotPly > lastPly()) void load()
  }, Math.max(1200, lastLoadAt + 5000 - Date.now()))
}

async function load() {
  const selected = selection.value
  if (!selected || controller) return
  const request = new AbortController()
  controller = request
  lastLoadAt = Date.now()
  const id = Number(selected.game.id), tournament = selected.quad.tournament_id
  const slug = encodeURIComponent(props.detail.event.slug)
  const clockAtRequest = clockRevision
  try {
    const [game, history] = await Promise.all([
      api.get<TournamentDetailResponse>(`/api/events/${slug}/tournaments/${tournament}`, { query: { game_id: id }, signal: request.signal }),
      api.get<{ searches: HandBrainSearch[] }>(`/api/events/${slug}/hand-brain/games/${id}`, { signal: request.signal }),
    ])
    if (controller !== request) return
    const wasLatest = followed.value
    const receivedPly = game.viewer_moves.at(-1)?.ply ?? -1
    for (const move of moves.value) if (move.ply > receivedPly) pendingMoves.set(move.ply, move)
    const mergedSearches = new Map(searches.value.map(search => [`${search.ply}:${search.engine_id}`, search]))
    for (const search of history.searches) mergedSearches.set(`${search.ply}:${search.engine_id}`, search)
    data.value = game
    searches.value = [...mergedSearches.values()]
    applyPendingMoves()
    if (wasLatest) selectedPly.value = moves.value.length
    if (game.clock_state && clockRevision === clockAtRequest) clock.value = { ...clock.value, ...game.clock_state } as HandBrainClock
    error.value = ''
    if ((!stream || stream.readyState === EventSource.CLOSED) && selected.game.status !== 'finished') {
      stream?.close()
      connect(slug, tournament, id)
    }
  } catch {
    if (!request.signal.aborted) {
      request.abort()
      error.value = 'Connection interrupted. Retrying the broadcast…'
    }
  } finally {
    if (controller === request) controller = null
  }
}
function connect(slug: string, tournament: number, id: number) {
  const source = new EventSource(`/events/${slug}/tournaments/${tournament}/stream?game_id=${id}&spectator=0`)
  stream = source
  let opened = false
  source.onopen = () => {
    if (stream !== source) return
    connected.value = true
    if (opened) requestRecovery()
    opened = true
  }
  source.onerror = () => { if (stream === source) connected.value = false }
  source.addEventListener('game.move', event => {
    const incoming = parseEvent<{ game_id: number; move?: MoveRecord }>(event)
    if (stream !== source || Number(incoming?.game_id) !== id) return
    if (!incoming?.move) { requestRecovery(); return }
    if (incoming.move.ply <= lastPly()) return
    pendingMoves.set(incoming.move.ply, incoming.move)
    applyPendingMoves()
    if (pendingMoves.size) requestRecovery(true)
  })
  source.addEventListener('engine.info', event => {
    const incoming = parseEvent<{ game_id: number; engine_id: number; side: 'white' | 'black'; engine_data: HandBrainAnalysis }>(event)
    if (stream !== source || !incoming || Number(incoming.game_id) !== id) return
    analyses.value = { ...analyses.value, [incoming.engine_id]: incoming.engine_data }
    const analysis = incoming.engine_data
    if (!analysis.root_fen || !analysis.role) return
    const position = parseFen(analysis.root_fen)
    const ply = (position.fullmove - 1) * 2 + (position.turn === 'b' ? 1 : 0)
    searches.value = [...searches.value.filter(s => !(s.ply === ply && s.engine_id === incoming.engine_id)), { game_id: id, engine_id: incoming.engine_id, role: analysis.role, ply, side: incoming.side, root_fen: analysis.root_fen, bestmove: analysis.pv?.split(' ')[0] ?? '', analysis, clocks: clock.value.engine_clocks_ms ?? {} }]
  })
  source.addEventListener('clock.sync', event => {
    const value = parseEvent<HandBrainClock>(event)
    if (stream !== source || !value || Number(value.game_id) !== id) return
    clockRevision += 1
    clock.value = value
  })
  source.addEventListener('tournament.snapshot', event => {
    const snapshot = parseEvent<{ game?: { id: number }; moves?: MoveRecord[] }>(event)
    if (stream !== source || Number(snapshot?.game?.id) !== id) return
    snapshotPly = snapshot?.moves?.at(-1)?.ply ?? 0
    if (snapshotPly > lastPly()) requestRecovery(true)
  })
}
watch(() => `${match.value?.id ?? ''}:${selection.value?.game.id ?? ''}`, () => {
  stream?.close(); stream = null; connected.value = false
  controller?.abort(); controller = null
  window.clearTimeout(recoveryTimer); recoveryTimer = undefined
  pendingMoves.clear(); snapshotPly = 0
  data.value = null; searches.value = []; analyses.value = {}; clock.value = {}; selectedPly.value = 0
  error.value = ''
  void load()
}, { immediate: true })
watch([() => selection.value?.game.id, () => selection.value?.game.status], ([id, status], [previousId, previousStatus]) => {
  if (id === previousId && status === 'finished' && previousStatus === 'live') requestRecovery()
})
watch([data, error], scheduleArenaFit, { flush: 'post' })
function scheduleArenaFit(): void {
  if (arenaFitFrame !== undefined) window.cancelAnimationFrame(arenaFitFrame)
  arenaFitFrame = window.requestAnimationFrame(() => {
    arenaFitFrame = undefined
    void fitArenaToViewport()
  })
}
async function fitArenaToViewport(): Promise<void> {
  await nextTick()
  const arena = arenaElement.value
  const viewer = boardColumnElement.value?.querySelector<HTMLElement>('.viewer-shell')
  const board = viewer?.querySelector<HTMLElement>('.board-mount')
  if (!arena || !viewer || !board) return
  const viewportHeight = window.visualViewport?.height ?? window.innerHeight
  const arenaDocumentTop = arena.getBoundingClientRect().top + window.scrollY
  const availableHeight = Math.floor(viewportHeight - arenaDocumentTop - 8)
  if (availableHeight <= 0) return
  const chrome = Math.max(0, viewer.getBoundingClientRect().height - board.getBoundingClientRect().height)
  const rootFontSize = Number.parseFloat(getComputedStyle(document.documentElement).fontSize) || 16
  const boardSize = Math.max(0, Math.min(42 * rootFontSize, Math.floor(availableHeight - chrome)))
  arena.style.setProperty('--arena-content-height', `${availableHeight}px`)
  arena.style.setProperty('--arena-board-size', `${boardSize}px`)
}
onMounted(() => {
  clockTimer = window.setInterval(() => { now.value = Date.now() }, 100)
  pollTimer = window.setInterval(() => {
    if (error.value || pendingMoves.size || snapshotPly > lastPly()
      || (!connected.value && selection.value?.game.status !== 'finished')) requestRecovery()
  }, 15000)
  headingResizeObserver = new ResizeObserver(scheduleArenaFit)
  if (headingElement.value) headingResizeObserver.observe(headingElement.value)
  window.addEventListener('resize', scheduleArenaFit)
  window.visualViewport?.addEventListener('resize', scheduleArenaFit)
  scheduleArenaFit()
})
onBeforeUnmount(() => {
  stream?.close(); stream = null; controller?.abort(); controller = null
  window.clearInterval(clockTimer); window.clearInterval(pollTimer); window.clearTimeout(recoveryTimer)
  if (arenaFitFrame !== undefined) window.cancelAnimationFrame(arenaFitFrame)
  headingResizeObserver?.disconnect()
  window.removeEventListener('resize', scheduleArenaFit)
  window.visualViewport?.removeEventListener('resize', scheduleArenaFit)
})
</script>
<template>
  <section class="relay-viewer hb-arena">
    <header ref="headingElement" class="tournament-heading">
      <div class="tournament-heading__title">
        <div class="title-line"><h1>{{ detail.event.title }}</h1><span class="relay-status">{{ selection?.game.result ?? (selection?.game.status === 'live' ? 'Live' : detail.event.status) }}</span><span class="relay-spectators"><AppIcon name="user" :size="14" />{{ detail.spectator_count ?? 0 }}</span></div>
        <p>{{ match?.stage ?? 'Play-in' }} {{ match?.key }} / Quad {{ Math.max(1, match?.quads.findIndex(q => q.tournament_id === selection?.quad.tournament_id)! + 1 || 1) }}<template v-if="selection"> / Game {{ selection.game.game_number }}</template> / {{ tcLabel }} / {{ threadLabel }}</p>
      </div>
      <div class="tournament-heading__controls">
        <RouterLink :to="`/events/${detail.event.slug}`" class="event-page-link"><AppIcon name="arrow-left" :size="15" />Event page</RouterLink>
        <div class="relay-broadcast__selectors"><label><span>Match</span><select :value="match?.id ?? ''" @change="selectMatch(payload.matches.find(m => m.id === Number(($event.target as HTMLSelectElement).value))!)"><option v-for="item in payload.matches" :key="item.id" :value="item.id">{{ item.key }} · {{ item.team_ids.map(id => teamById(id)?.name ?? 'TBD').join(' vs ') }}</option></select></label><StreamIndicator :state="connected ? 'live' : selection ? 'connecting' : 'closed'" /></div>
      </div>
    </header>
    <p v-if="error" class="connection-error" role="status">{{ error }}</p>
    <section ref="arenaElement" class="arena" aria-label="Hand and brain match">
      <div class="engine-column">
        <HandBrainCheers :detail="detail" v-slot="{ cheer }">
          <HandBrainTeamCard :team="blackTeam" side="Black" :swapped="swapped" :analyses="replayAnalyses" :clocks="engineClocks" :active-engine="clock.active_engine_id" :running="liveThinking" :clock-offset-ms="clockOffsetMs" :fen="currentFen" @cheer="cheer" />
          <HandBrainTeamCard :team="whiteTeam" side="White" :swapped="swapped" :analyses="replayAnalyses" :clocks="engineClocks" :active-engine="clock.active_engine_id" :running="liveThinking" :clock-offset-ms="clockOffsetMs" :fen="currentFen" @cheer="cheer" />
        </HandBrainCheers>
      </div>
      <div ref="boardColumnElement" class="board-column">
        <ChessViewer v-model="selectedPly" :opening="opening" :moves="moves" :arrows="arrows" :rings="rings" label="Hand and brain game" @orientation="orientation = $event" />
      </div>
      <aside class="activity-column" aria-label="Game activity">
        <HandBrainEvalGraph :orientation="orientation" :searches="searches" :teams="teams" :selected-ply="selectedPly" :start-ply="startPly" :total-plies="moves.length" @seek="selectedPly = $event" />
        <div class="activity-logs"><MoveList class="arena-moves" v-model="selectedPly" :moves="moves.map(m => m.san || m.uci)" :uci-moves="moves.map(m => m.uci)" :fen="opening.fen" :book-plies="moves.filter(m => m.is_book).length" /><ChatPanel class="arena-chat" :messages="detail.chat_messages" :settings="detail.chat_settings" :event-slug="detail.event.slug" /></div>
      </aside>
    </section>
    <div v-if="allGames.length" class="game-strip" aria-label="Select a game"><span>{{ teams[0]?.name }} {{ selection?.quad.score[0] ?? 0 }} – {{ selection?.quad.score[1] ?? 0 }} {{ teams[1]?.name }}</span><button v-for="item in allGames" :key="String(item.game.id)" :class="{ selected: item.game.id === selection?.game.id }" @click="selectedGameId = Number(item.game.id)">Q{{ match?.quads.findIndex(q => q.tournament_id === item.quad.tournament_id)! + 1 }} / G{{ item.game.game_number }} · {{ item.game.result ?? item.game.status }}</button></div>
    <HandBrainBracket :matches="payload.matches" :teams="payload.teams" :selected="match?.id" @select="selectMatch" />
    <section class="tournament-data">
      <nav class="data-tabs" aria-label="Tournament information"><button v-for="name in ['standings', 'crosstable', 'settings']" :key="name" :aria-current="tab === name ? 'page' : undefined" @click="tab = name">{{ name === 'crosstable' ? 'Crosstable' : name === 'settings' ? 'Settings' : 'Standings' }}</button></nav>
      <section class="data-panel">
        <header><h2>{{ tab === 'crosstable' ? 'Crosstable' : tab === 'settings' ? 'Settings' : 'Standings' }}</h2></header>
        <dl v-if="tab === 'settings'" class="settings-list"><div><dt>Time control per engine</dt><dd>{{ tcLabel }}</dd></div><div><dt>Threads per engine</dt><dd>{{ threads === 1 ? threads : `Up to ${threads}` }}</dd></div><div><dt>Concurrent games</dt><dd>1</dd></div><div><dt>Openings</dt><dd>Lichess UHO</dd></div><div><dt>Advance</dt><dd>More than 2 points in a quad</dd></div><div><dt>Drawn quad</dt><dd>Halve time control; minimum {{ payload.rules.minimum_initial_ms / 1000 }}+{{ payload.rules.minimum_increment_ms / 1000 }}</dd></div></dl>
        <div v-else class="table-wrap"><table v-if="tab === 'standings'"><thead><tr><th>Seed</th><th>Team</th><th>Engines</th><th>Matches W–L</th><th>Game points</th><th>Stage</th></tr></thead><tbody><tr v-for="team in standings" :key="team.id"><td class="rank-cell">{{ team.seed }}</td><td class="team-cell">{{ team.name }}</td><td>{{ team.members.map(m => m.name).join(' + ') }}</td><td class="number-cell">{{ team.won }}–{{ team.lost }}</td><td class="number-cell">{{ team.points }} / {{ team.games }}</td><td>{{ team.stage }}</td></tr></tbody></table>
        <table v-else class="cross-table"><thead><tr><th>Team / seed</th><th v-for="team in payload.teams" :key="team.id" :title="team.name">{{ team.seed }}</th></tr></thead><tbody><tr v-for="team in payload.teams" :key="team.id"><td class="team-cell">{{ team.name }}</td><td v-for="opponent in payload.teams" :key="opponent.id" :class="{ diagonal: opponent.id === team.id }">{{ opponent.id === team.id ? '·' : cell(team.id, opponent.id) }}</td></tr></tbody></table></div>
      </section>
    </section>
  </section>
</template>
<style scoped>
.relay-viewer {
  --arena-board-size: clamp(30rem, calc(100dvh - 15rem), 42rem);
  --arena-content-height: calc(var(--arena-board-size) + 6.35rem);
  position: relative;
  display: grid;
  overflow-anchor: none;
  width: 100%;
  gap: var(--space-md, 1rem);
  padding: .55rem clamp(.75rem, 1.5vw, 1.75rem) 3rem;
  background: var(--color-bg, #f3f6fa);
  color: var(--color-text, #17202a);
}
.tournament-heading { display: flex; align-items: end; justify-content: space-between; gap: var(--space-xl, 2rem); padding-block-end: .55rem; border-block-end: 1px solid var(--color-border, #d5dbe1); }
.tournament-heading__title { display: grid; align-items: center; min-width: 0; }
.title-line { display: flex; align-items: center; gap: var(--space-sm, .5rem); min-width: 0; }
.tournament-heading h1, .tournament-heading p { margin: 0; }
.tournament-heading h1 { max-width: 54rem; font-size: clamp(1.45rem, 2.7vw, 2.15rem); letter-spacing: -.035em; line-height: 1.05; overflow-wrap: anywhere; }
.tournament-heading__title > p { margin-block-start: .18rem; color: var(--color-text-muted, #607080); font-size: .75rem; }
.relay-status, .relay-spectators { display: inline-flex; align-items: center; gap: .25rem; min-height: 1.45rem; padding-inline: .5rem; border: 1px solid var(--color-border, #d5dbe1); border-radius: 999px; color: var(--color-text-muted, #607080); font-size: .62rem; font-weight: 730; white-space: nowrap; }
.tournament-heading__controls, .relay-broadcast__selectors { display: flex; align-items: end; gap: .65rem; }
.relay-broadcast__selectors label { display: grid; gap: .25rem; min-width: 0; color: var(--color-text-muted, #607080); font-size: .64rem; font-weight: 700; }
.relay-broadcast__selectors label:last-of-type { width: clamp(16rem, 27vw, 27rem); }
.relay-broadcast__selectors select { width: 100%; min-height: 2.35rem; padding: .4rem 2rem .4rem .65rem; border: 1px solid var(--color-border, #d5dbe1); border-radius: var(--radius-sm, .35rem); background: var(--color-surface, #fff); color: var(--color-text, #17202a); font: inherit; font-size: .77rem; }
.event-page-link { display: inline-flex; min-height: 2.35rem; align-items: center; gap: .35rem; padding-inline: .8rem; border: 1px solid var(--color-border, #d5dbe1); border-radius: var(--radius-sm, .35rem); color: var(--color-text-muted, #607080); font-size: .72rem; font-weight: 730; text-decoration: none; white-space: nowrap; }
.arena { display: grid; grid-template-columns: minmax(25rem, 29rem) var(--arena-board-size) minmax(28rem, 1fr); gap: clamp(.7rem, 1.5vw, 1.2rem); align-items: start; height: var(--arena-content-height); min-height: 0; }
.engine-column, .activity-column { display: grid; min-height: 0; gap: var(--space-sm, .5rem); }
.engine-column { grid-template-rows: repeat(2, minmax(0, 1fr)) auto; height: var(--arena-content-height); }
.relay-engine-slot { display: grid; grid-template-rows: auto minmax(0, 1fr); gap: .3rem; min-width: 0; min-height: 0; padding: .25rem; border-radius: calc(var(--radius-md, .5rem) + .25rem); background: color-mix(in srgb, var(--relay-primary) 5%, transparent); box-shadow: 0 0 1.35rem color-mix(in srgb, var(--relay-primary) 22%, transparent); }
.relay-engine-slot :deep(.engine-panel) { min-height: 0; border-color: var(--color-border, #d5dbe1); }
.relay-engine-slot :deep(.engine-panel--active) { border-color: var(--color-border-strong, #99a8bb); box-shadow: 0 0 0 1px var(--color-border-strong, #99a8bb); }
.relay-team-strip { display: flex; align-items: center; gap: .3rem; min-width: 0; min-height: 1.75rem; padding-inline: .35rem; }
.relay-team-strip strong { flex: 0 0 auto; margin-right: .15rem; color: var(--relay-primary); font-size: .62rem; }
.relay-team-strip__track { display: flex; flex: 1 1 auto; align-items: center; gap: .3rem; min-width: 0; overflow: hidden; }
.relay-team-strip span { flex: 0 0 auto; padding: .2rem .38rem; border: 1px solid var(--color-border, #d5dbe1); border-radius: 999px; background: var(--color-surface, #fff); color: var(--color-text-muted, #607080); font-size: .52rem; font-weight: 680; white-space: nowrap; transition: transform .38s ease, border-color .2s ease, background-color .2s ease, color .2s ease; }
.relay-team-strip span.current { border-color: var(--relay-primary); background: color-mix(in srgb, var(--relay-primary) 12%, var(--color-surface, #fff)); color: var(--color-text, #17202a); }
.relay-chip-enter-active, .relay-chip-leave-active { transition: opacity .2s ease; }
.relay-chip-enter-from, .relay-chip-leave-to { opacity: 0; }
.board-column { width: var(--arena-board-size); min-width: 0; justify-self: center; }
.activity-column { grid-template-columns: repeat(2, minmax(0, 1fr)); grid-template-rows: minmax(0, 1fr); height: var(--arena-content-height); }
.activity-column > * { min-width: 0; min-height: 0; height: 100%; }
.arena-cheers { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: .5rem; padding-top: .15rem; }

.tournament-data { display: grid; gap: var(--space-sm, .5rem); margin-block-start: var(--space-md, 1rem); }
.data-tabs { display: flex; gap: .3rem; overflow-x: auto; }
.data-tabs button { display: inline-flex; min-height: 2.35rem; align-items: center; gap: .45rem; padding-inline: .85rem; border: 1px solid transparent; border-radius: var(--radius-sm, .35rem); color: var(--color-text-muted, #607080); font-size: .78rem; font-weight: 730; text-decoration: none; white-space: nowrap; }
.data-tabs button:hover { color: var(--color-text, #17202a); background: color-mix(in srgb, var(--color-text, #17202a) 5%, transparent); }
.data-tabs button[aria-current="page"] { border-color: color-mix(in srgb, var(--color-accent, #2f78c4) 24%, transparent); background: color-mix(in srgb, var(--color-accent, #2f78c4) 9%, var(--color-surface, #fff)); color: var(--color-accent, #2f78c4); }
.data-tabs button span { color: inherit; font-size: .64rem; opacity: .75; }
.data-panel { overflow: hidden; border: 1px solid var(--color-border, #d5dbe1); border-radius: var(--radius-lg, .75rem); background: var(--color-surface, #fff); }
.data-panel > header { padding: var(--space-md, 1rem); border-block-end: 1px solid var(--color-border, #d5dbe1); }
.data-panel h2, .data-panel header p, .hardware-section h3 { margin: 0; }
.data-panel h2 { font-size: 1rem; }
.data-panel header p { margin-block-start: .18rem; color: var(--color-text-muted, #607080); font-size: .7rem; }
.table-wrap { overflow-x: auto; }
.data-panel table { width: 100%; border-collapse: collapse; font-size: .82rem; }
.data-panel th, .data-panel td { padding: .72rem .85rem; border-block-end: 1px solid var(--color-border, #d5dbe1); text-align: start; }
.data-panel th { color: var(--color-text-muted, #607080); font-size: .63rem; letter-spacing: .04em; text-transform: uppercase; white-space: nowrap; }
.data-panel tbody tr:last-child td { border-block-end: 0; }
.data-panel tbody tr:hover { background: color-mix(in srgb, var(--color-accent, #2f78c4) 4.5%, transparent); }
.data-panel td a { color: inherit; font-weight: 700; text-decoration: none; }
.data-panel td a:hover { color: var(--color-accent, #2f78c4); text-decoration: underline; text-underline-offset: .16em; }
.pagination { display: flex; align-items: center; justify-content: flex-end; gap: .75rem; padding: .75rem .85rem; border-top: 1px solid var(--color-border, #d5dbe1); }
.pagination button { padding: .4rem .65rem; border: 1px solid var(--color-border, #d5dbe1); border-radius: .35rem; background: var(--color-surface, #fff); color: inherit; cursor: pointer; font: inherit; }
.pagination button:disabled { cursor: default; opacity: .45; }
.pagination span { color: var(--color-text-muted, #607080); font-size: .72rem; }
.rank-cell { width: 4rem; color: var(--color-text-muted, #607080); font-variant-numeric: tabular-nums; }
.team-cell { font-weight: 700; }
.number-cell { width: 6rem; font-weight: 730; font-variant-numeric: tabular-nums; }
.action-cell { width: 4rem; text-align: end !important; }
.action-cell a { color: var(--color-accent, #2f78c4) !important; font-size: .72rem; }
.settings-list { display: grid; grid-template-columns: repeat(auto-fill, minmax(15rem, 1fr)); gap: 1px; margin: 0; background: var(--color-border, #d5dbe1); }
.settings-list div { min-width: 0; padding: .85rem 1rem; background: var(--color-surface, #fff); }
.settings-list dt { color: var(--color-text-muted, #607080); font-size: .62rem; font-weight: 750; letter-spacing: .04em; text-transform: uppercase; }
.settings-list dd { margin: .22rem 0 0; font-size: .8rem; font-weight: 650; overflow-wrap: anywhere; }
.hardware-section { border-block-start: 1px solid var(--color-border, #d5dbe1); }
.hardware-section h3 { padding: .85rem 1rem; border-block-end: 1px solid var(--color-border, #d5dbe1); font-size: .88rem; }

@media (max-width: 96rem) {
  .arena { grid-template-columns: minmax(25rem, 1fr) var(--arena-board-size); height: auto; }
  .engine-column { grid-column: 1; grid-row: 1; }
  .board-column { grid-column: 2; grid-row: 1; }
  .activity-column { grid-column: 1 / -1; grid-row: 2; grid-template-columns: repeat(2, minmax(0, 1fr)); grid-template-rows: minmax(24rem, 34rem); height: min(34rem, calc(100dvh - 8rem)); }
}
@media (max-width: 58rem) {
  .tournament-heading { align-items: stretch; flex-direction: column; }
  .tournament-heading__controls { justify-content: space-between; }
  .arena { grid-template-columns: 1fr; }
  .engine-column { grid-column: 1; grid-row: 2; grid-template-columns: 1fr 1fr; grid-template-rows: auto auto; height: auto; }
  .engine-column .arena-cheers { grid-column: 1 / -1; }
  .board-column { grid-column: 1; grid-row: 1; width: min(100%, var(--arena-board-size)); }
  .activity-column { grid-column: 1; grid-row: 3; }
}
@media (max-width: 40rem) {
  .tournament-heading__controls, .relay-broadcast__selectors { align-items: stretch; flex-direction: column-reverse; }
  .relay-broadcast__selectors label, .relay-broadcast__selectors label:last-of-type { width: 100%; }
  .engine-column { grid-template-columns: 1fr; }
  .engine-column .arena-cheers { grid-column: 1; }
  .activity-column { grid-template-columns: 1fr; grid-template-rows: minmax(12rem, 19rem) minmax(18rem, 26rem); }
  .settings-list { grid-template-columns: 1fr; }
}


.engine-column { grid-template-rows: repeat(2, minmax(0, 1fr)); }
.activity-column { grid-template-columns: minmax(0, 1fr); grid-template-rows: minmax(10rem, .36fr) minmax(0, 1fr); }
.activity-logs { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: var(--space-sm, .5rem); min-height: 0; }
.activity-logs > * { min-height: 0; height: 100%; }
.activity-logs :deep(.move-list__scroll), .activity-logs :deep(.chat-log) { min-height: 0; max-height: none; flex: 1; }
.activity-logs :deep(.chat-form) { padding: .5rem; gap: .35rem; }
.activity-logs :deep(.chat-form label) { gap: .15rem; }
.activity-logs :deep(.chat-form textarea) { min-height: 2.25rem; height: 2.25rem; }
.activity-logs :deep(.chat-form input), .activity-logs :deep(.chat-form textarea) { font-size: .65rem; padding: .35rem .45rem; }
.activity-logs :deep(.chat-tabs button) { font-size: .64rem; white-space: nowrap; }
.activity-logs :deep(.move-row) { font-size: .72rem; }
.data-tabs button { background: transparent; font: inherit; cursor: pointer; }
.game-strip { display: flex; align-items: center; flex-wrap: wrap; gap: .4rem; color: var(--color-text-muted); font-size: .7rem; }
.game-strip > span { margin-inline-end: .6rem; color: var(--color-text); font-weight: 650; }
.game-strip button { padding: .35rem .55rem; border: 1px solid var(--color-border); border-radius: var(--radius-sm); background: var(--color-surface); color: var(--color-text-muted); font: inherit; cursor: pointer; }
.game-strip button.selected { border-color: var(--color-accent); color: var(--color-accent); }
.cross-table th:not(:first-child), .cross-table td:not(:first-child) { text-align: center; }
.cross-table .diagonal { background: var(--color-bg-subtle); color: var(--color-text-faint); }
.connection-error { color: var(--color-warning); font-size: .75rem; }
@media (max-width: 96rem) { .activity-column { grid-template-columns: minmax(18rem, .7fr) minmax(0, 1fr); grid-template-rows: minmax(18rem, 24rem); height: auto; } }
@media (max-width: 58rem) { .engine-column { grid-template-rows: auto; }.engine-column > * { min-height: 21rem; }.activity-column { grid-template-columns: 1fr; grid-template-rows: 14rem minmax(20rem, 26rem); } }
@media (max-width: 40rem) { .engine-column { grid-template-columns: 1fr; }.activity-logs { grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); }.activity-column { height: auto; }.title-line { flex-wrap: wrap; } }

</style>
