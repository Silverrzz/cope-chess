<script setup lang="ts">
import { Chessground } from 'chessground'
import type { Api as ChessgroundApi } from 'chessground/api'
import { getKeyAtDomPos } from 'chessground/board'
import type { Config as ChessgroundConfig } from 'chessground/config'
import type { DrawBrushes, DrawShape } from 'chessground/draw'
import { read as readGroundFen } from 'chessground/fen'
import type { Key, PiecesDiff, Role } from 'chessground/types'
import { key2pos, pos2key } from 'chessground/util'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, useId, watch } from 'vue'

import duckIcon from '@/assets/duck.svg'
import AppIcon from '@/components/ui/AppIcon.vue'
import { useViewerSettings } from '@/composables/useViewerSettings'
import TakBoard from './TakBoard.vue'
import TakNinjaBoard from './TakNinjaBoard.vue'
import TakOverview from './TakOverview.vue'
import { buildTakPositions, takPositionText, takReserves, takRoads } from './tak'

import {
  buildPositions,
  materialSummary,
  positionFen,
  isTurn,
  type Variant,
  type BoardArrow,
  type Color,
} from './chess'

const arrowBrushes: DrawBrushes = {
  green: { key: 'g', color: '#15781b', opacity: 1, lineWidth: 10 },
  red: { key: 'r', color: '#882020', opacity: 1, lineWidth: 10 },
  blue: { key: 'b', color: '#003088', opacity: 1, lineWidth: 10 },
  yellow: { key: 'y', color: '#e68f00', opacity: 1, lineWidth: 10 },
  engineBlack: { key: 'engine-black', color: '#000000', opacity: 0.56, lineWidth: 14 },
  engineWhite: { key: 'engine-white', color: '#ffffff', opacity: 0.72, lineWidth: 8 },
}
const duckImage = `url("${duckIcon}")`

const props = withDefaults(defineProps<{
  fen?: string | null
  variant?: Variant | undefined
  moves?: string[]
  sanMoves?: string[]
  modelValue?: number
  orientation?: Color
  controls?: boolean
  coordinates?: boolean
  compact?: boolean
  embedTak?: boolean
  label?: string
  arrows?: BoardArrow[]
  rings?: string[]
  lastMove?: string
  finished?: boolean
  editMode?: 'move' | 'place' | 'disabled'
}>(), {
  fen: 'startpos',
  moves: () => [],
  sanMoves: () => [],
  orientation: 'white',
  controls: true,
  coordinates: true,
  compact: false,
  label: 'Chess game viewer',
  arrows: () => [],
  rings: () => [],
  lastMove: '',
})

const emit = defineEmits<{
  'update:modelValue': [ply: number]
  position: [value: { ply: number; fen: string }]
  'select-square': [square: Key]
  'move-piece': [from: Key, to: Key]
}>()

const viewer = ref<HTMLElement | null>(null)
const board = ref<HTMLElement | null>(null)
const keyboardSquare = ref<Key | null>(null)
const keyboardHelpId = useId()
const { boardAnnotationsEnabled } = useViewerSettings()
let ground: ChessgroundApi | null = null
let annotationDrag: { pointerId: number; orig: Key; dest: Key | undefined } | null = null
let annotationArrows: DrawShape[] = []
const selectedPly = ref(clampPly(props.modelValue ?? props.moves.length))

const positions = computed(() => buildPositions(props.fen, props.moves, props.variant))
const position = computed(() => positions.value[clampPly(selectedPly.value)]!)
const takPositions = computed(() => props.variant === 'tak' ? buildTakPositions(props.fen, props.moves) : [])
const takPosition = computed(() => takPositions.value[clampPly(selectedPly.value)])
const previousTakPosition = computed(() => takPositions.value[clampPly(selectedPly.value) - 1])
const roads = computed(() => takPosition.value ? takRoads(takPosition.value) : null)
const showRoads = ref(true)
const currentFen = computed(() => takPosition.value ? takPositionText(takPosition.value) : positionFen(position.value))
const keyboardSquareDescription = computed(() => {
  const square = keyboardSquare.value
  if (!square) return ''
  const piece = readGroundFen(currentFen.value).get(square)
  return `${square}, ${piece ? `${piece.color} ${piece.role}` : 'empty'}`
})
const material = computed(() => {
  if (!takPosition.value) return materialSummary(position.value)
  const { white, black } = takReserves(takPosition.value)
  return { white: `${white.flats} flats, ${white.caps} capstones`, black: `${black.flats} flats, ${black.caps} capstones`,
    label: `Reserves ${white.flats}+${white.caps} / ${black.flats}+${black.caps}` }
})
const lastMove = computed(() => props.lastMove || (selectedPly.value > 0 ? props.moves[selectedPly.value - 1] ?? '' : ''))
const status = computed(() => {
  if (!props.moves.length) return props.lastMove ? 'Current position' : 'Start position'
  if (selectedPly.value === 0) return `Start position, 0 of ${props.moves.length} moves`
  const san = props.sanMoves[selectedPly.value - 1]
  return `Ply ${selectedPly.value} of ${props.moves.length}${san ? `, ${san}` : ''}`
})

const positionDescription = computed(() => {
  if (takPosition.value) return `${status.value}. ${takPosition.value.turn === 'w' ? 'White' : 'Black'} to move. ${material.value.label}.`
  const side = position.value.turn === 'w' ? 'White' : 'Black'
  const duck = position.value.duckSquare
  const duckLabel = duck === null ? '' : ` Neutral duck on ${'abcdefgh'[duck % 8]}${8 - Math.floor(duck / 8)}.`
  return `${status.value}. ${side} to move. ${material.value.label}.${duckLabel}`
})

watch(() => props.modelValue, (next) => {
  if (next !== undefined && next !== selectedPly.value) selectedPly.value = clampPly(next)
})

let previousFen = props.fen
let previousMoveCount = props.moves.length
watch(() => `${props.fen || ''}\u0000${props.moves.join(' ')}`, () => {
  const followedLatest = selectedPly.value >= previousMoveCount
  if (props.fen !== previousFen || followedLatest) selectPly(props.moves.length)
  else selectPly(selectedPly.value)
  previousFen = props.fen
  previousMoveCount = props.moves.length
})

watch([selectedPly, currentFen], () => {
  emit('position', { ply: selectedPly.value, fen: currentFen.value })
}, { immediate: true })

watch([
  currentFen,
  lastMove,
  () => props.orientation,
  () => props.coordinates,
  () => props.compact,
  () => props.arrows.map((arrow) => `${arrow.color}:${arrow.move}:${arrow.fillColor || ''}:${arrow.outlineColor || ''}:${arrow.label || ''}`).join('|'),
  () => props.rings.join('|'),
], renderBoard)

watch(boardAnnotationsEnabled, (enabled) => {
  if (!enabled) clearAnnotations()
})

watch(() => props.editMode, () => ground?.cancelMove())

watch(keyboardSquare, square => {
  ground?.set({ highlight: { custom: new Map(square ? [[square, 'editor-keyboard-square']] : []) } })
})

onMounted(() => {
  if (board.value) {
    const editing = props.editMode !== undefined
    const editorConfig: ChessgroundConfig = editing ? {
      autoCastle: false,
      highlight: { lastMove: false, check: false },
      movable: {
        color: 'both',
        free: true,
        events: { after: (from, to) => emit('move-piece', from, to) },
      },
      premovable: { enabled: false },
    } : {}
    ground = Chessground(board.value, {
      ...editorConfig,
      viewOnly: !editing,
      coordinates: props.coordinates,
      animation: { enabled: !props.compact, duration: 150 },
      drawable: { enabled: false, brushes: arrowBrushes },
    })
    renderBoard()
  }
  if (props.controls) document.addEventListener('keydown', handleKey)
})

onBeforeUnmount(() => {
  document.removeEventListener('keydown', handleKey)
  ground?.destroy()
  ground = null
})

function clampPly(ply: number): number {
  const numeric = Number.isFinite(ply) ? Math.trunc(ply) : 0
  return Math.max(0, Math.min(props.moves.length, numeric))
}

function selectPly(ply: number): void {
  const next = clampPly(ply)
  if (next === selectedPly.value) return
  selectedPly.value = next
  emit('update:modelValue', next)
}

function boardSquare(event: MouseEvent): Key | undefined {
  if (!ground) return
  const bounds = ground.state.dom.elements.board.getBoundingClientRect()
  return getKeyAtDomPos([event.clientX, event.clientY], ground.state.orientation === 'white', bounds)
}

function handleBoardClick(event: MouseEvent): void {
  if (props.editMode === undefined) {
    clearAnnotations()
    return
  }
  if (props.editMode !== 'place') return
  const square = boardSquare(event)
  if (square) emit('select-square', square)
}

function handleEditorKey(event: KeyboardEvent): void {
  const square = keyboardSquare.value
  if (!square || !ground || props.editMode === 'disabled') return
  if (event.key === 'Enter' || event.key === ' ') {
    event.preventDefault()
    if (props.editMode === 'move') ground.selectSquare(square)
    else emit('select-square', square)
    return
  }
  if (event.key === 'Escape') {
    ground.cancelMove()
    return
  }
  const directions: Record<string, [number, number]> = {
    ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, 1], ArrowDown: [0, -1],
  }
  const direction = directions[event.key]
  if (!direction) return
  event.preventDefault()
  const [file, rank] = key2pos(square)
  const sign = props.orientation === 'white' ? 1 : -1
  const nextFile = Math.max(0, Math.min(7, file + sign * direction[0]))
  const nextRank = Math.max(0, Math.min(7, rank + sign * direction[1]))
  keyboardSquare.value = pos2key([nextFile, nextRank])
}

function startAnnotation(event: PointerEvent): void {
  if (props.editMode !== undefined || !boardAnnotationsEnabled.value || event.button !== 2 || !board.value) return
  const square = boardSquare(event)
  if (!square) return
  event.preventDefault()
  board.value.setPointerCapture(event.pointerId)
  annotationDrag = { pointerId: event.pointerId, orig: square, dest: undefined }
}

function previewAnnotation(event: PointerEvent): void {
  if (!ground || annotationDrag?.pointerId !== event.pointerId) return
  const { orig } = annotationDrag
  const square = boardSquare(event)
  const dest = square === orig ? undefined : square
  if (dest === annotationDrag.dest) return
  annotationDrag.dest = dest
  const hasArrow = annotationArrows.some((arrow) => arrow.orig === orig && arrow.dest === dest)
  ground.setShapes(dest && !hasArrow ? [...annotationArrows, { orig, dest, brush: 'yellow' }] : annotationArrows)
}

function finishAnnotation(event: PointerEvent): void {
  if (event.button !== 2 || !ground || annotationDrag?.pointerId !== event.pointerId) return
  const { orig } = annotationDrag
  annotationDrag = null
  const dest = boardSquare(event)
  if (dest === orig) {
    const custom = new Map(ground.state.highlight.custom)
    if (custom.has(orig)) custom.delete(orig)
    else custom.set(orig, 'annotation-highlight')
    ground.set({ highlight: { custom } })
  } else if (dest) {
    const remaining = annotationArrows.filter((arrow) => arrow.orig !== orig || arrow.dest !== dest)
    annotationArrows = remaining.length === annotationArrows.length
      ? [...annotationArrows, { orig, dest, brush: 'yellow' }]
      : remaining
  }
  ground.setShapes(annotationArrows)
}

function cancelAnnotation(event: PointerEvent): void {
  if (annotationDrag?.pointerId !== event.pointerId) return
  annotationDrag = null
  ground?.setShapes(annotationArrows)
}

function clearAnnotations(): void {
  if (!ground) return
  const pointerId = annotationDrag?.pointerId
  annotationDrag = null
  if (pointerId !== undefined && board.value?.hasPointerCapture(pointerId)) board.value.releasePointerCapture(pointerId)
  annotationArrows = []
  if (ground.state.highlight.custom?.size) ground.set({ highlight: { custom: new Map() } })
  if (ground.state.drawable.shapes.length) ground.setShapes([])
}

function handleContextMenu(event: MouseEvent): void {
  if (boardAnnotationsEnabled.value) event.preventDefault()
}

function predictionShapes(arrow: BoardArrow, brush: string): DrawShape[] {
  const shapes: DrawShape[] = [{
    orig: arrow.move.slice(0, 2).toLowerCase() as Key,
    dest: arrow.move.slice(2, 4).toLowerCase() as Key,
    brush,
  }]
  if (arrow.move.includes(',')) {
    const square = position.value.duckSquare
    const orig = square === null
      ? arrow.move.slice(2, 4).toLowerCase() as Key
      : `${'abcdefgh'[square % 8]}${8 - Math.floor(square / 8)}` as Key
    const dest = arrow.move.slice(-2).toLowerCase() as Key
    if (orig !== dest) shapes.push({ orig, dest, brush })
  }
  return shapes
}

function renderBoard(): void {
  if (!ground) return
  if (props.editMode !== undefined) ground.cancelMove()
  const move = lastMove.value
  const lastMoveSquares = /^[a-h][1-8][a-h][1-8]/.test(move)
    ? [move.slice(0, 2) as Key, move.slice(2, 4) as Key]
    : []
  const arrows = props.arrows
    .filter((arrow) => isTurn(arrow.move))
    .sort((left, right) => left.color === right.color ? 0 : left.color === 'black' ? -1 : 1)
  const styledArrows = arrows.filter((arrow) => arrow.fillColor)
  const outlinedArrows = styledArrows.filter((arrow) => arrow.outlineColor)
  const outlinedShapes = outlinedArrows.flatMap((arrow, index) => predictionShapes(arrow, `engineOutline${index}`))
  const fillBrushes = Object.fromEntries(styledArrows.map((arrow, index) => [
    `engineTeam${index}`,
    {
      key: `engine-team-${index}-${arrow.fillColor!.replace(/[^a-z0-9]/gi, '')}`,
      color: arrow.fillColor!,
      opacity: 0.72,
      lineWidth: 11,
    },
  ]))
  const outlineBrushes = Object.fromEntries(outlinedArrows.map((arrow, index) => [
    `engineOutline${index}`,
    {
      key: `engine-outline-${index}-${arrow.outlineColor!.replace(/[^a-z0-9]/gi, '')}`,
      color: arrow.outlineColor!,
      opacity: 1,
      lineWidth: 17,
    },
  ]))
  const autoShapes = [
    ...props.rings.filter(square => /^[a-h][1-8]$/.test(square)).map(square => ({ orig: square as Key, brush: 'brainRing' })),
    ...outlinedShapes,
    ...arrows.flatMap((arrow) => {
      const brush = arrow.fillColor
        ? `engineTeam${styledArrows.indexOf(arrow)}`
        : arrow.color === 'white' ? 'engineWhite' : 'engineBlack'
      const shapes = predictionShapes(arrow, brush)
      if (arrow.label) shapes[0]!.label = { text: arrow.label }
      return shapes
    }),
  ]
  const config: ChessgroundConfig = {
    orientation: props.orientation,
    coordinates: props.coordinates,
    lastMove: lastMoveSquares,
    animation: { enabled: !props.compact, duration: 150 },
    drawable: { autoShapes, brushes: { ...arrowBrushes, brainRing: { key: 'brain-ring', color: arrowBrushes.yellow.color, opacity: .8, lineWidth: 5 }, ...fillBrushes, ...outlineBrushes } },
  }
  const pieces: PiecesDiff = new Map([...ground.state.pieces.keys()].map((key) => [key, undefined]))
  for (const [key, piece] of readGroundFen(currentFen.value.replaceAll('*', '1'))) pieces.set(key, piece)
  const duck = position.value.duckSquare
  if (duck !== null) {
    const key = `${'abcdefgh'[duck % 8]}${8 - Math.floor(duck / 8)}` as Key
    pieces.set(key, { role: 'duck' as Role, color: 'white' })
  }
  ground.set(config)
  ground.setPieces(pieces)
}

function handleKey(event: KeyboardEvent): void {
  const target = event.target as HTMLElement | null
  if (target instanceof Element && target.closest('input, textarea, select, [contenteditable="true"]')) return
  if (event.key === 'ArrowLeft') {
    event.preventDefault()
    selectPly(selectedPly.value - 1)
  } else if (event.key === 'ArrowRight') {
    event.preventDefault()
    selectPly(selectedPly.value + 1)
  } else if (event.key === 'Home') {
    event.preventDefault()
    selectPly(0)
  } else if (event.key === 'End') {
    event.preventDefault()
    selectPly(props.moves.length)
  }
}

async function focusBoard(): Promise<void> {
  await nextTick()
  viewer.value?.focus()
}

defineExpose({ selectPly, focusBoard })
</script>

<template>
  <section
    ref="viewer"
    class="chess-viewer"
    :class="{ 'chess-viewer--compact': compact, 'chess-viewer--tak': takPosition }"
    :aria-label="label"
    :tabindex="controls ? 0 : undefined"
  >
    <TakOverview v-if="takPosition && roads && controls && !compact" v-model:show-roads="showRoads" :position="takPosition" :road-winner="roads.winner" :finished="Boolean(finished && selectedPly === moves.length)" />
    <div class="board-frame" :class="{ 'board-frame--placement': editMode === 'place', 'board-frame--inactive': editMode === 'disabled' }">
      <TakNinjaBoard v-if="takPosition && roads && embedTak" class="board-mount" :tps="currentFen" :previous-tps="previousTakPosition && takPositionText(previousTakPosition)" :orientation="orientation" :last-move="lastMove" :coordinates="coordinates" :show-roads="showRoads">
        <TakBoard :position="takPosition" :orientation="orientation" :last-move="lastMove" :coordinates="coordinates" :compact="compact" :show-roads="showRoads" :roads="roads" :label="label" />
      </TakNinjaBoard>
      <TakBoard v-else-if="takPosition && roads" class="board-mount" :position="takPosition" :orientation="orientation" :last-move="lastMove" :coordinates="coordinates" :compact="compact" :show-roads="showRoads" :roads="roads" :label="label" />
      <div
        v-else
        ref="board"
        class="board-mount"
        :role="editMode && editMode !== 'disabled' ? 'group' : 'img'"
        :tabindex="editMode && editMode !== 'disabled' ? 0 : undefined"
        :aria-label="editMode ? label : `${label}, ${positionDescription}`"
        :aria-describedby="editMode && editMode !== 'disabled' ? keyboardHelpId : undefined"
        @focus="editMode && (keyboardSquare = orientation === 'white' ? 'a8' : 'h1')"
        @blur="keyboardSquare = null"
        @keydown="handleEditorKey"
        @click="handleBoardClick"
        @pointerdown="startAnnotation"
        @pointermove="previewAnnotation"
        @pointerup="finishAnnotation"
        @lostpointercapture="cancelAnnotation"
        @contextmenu="handleContextMenu"
      ></div>
    </div>

    <template v-if="editMode && editMode !== 'disabled'">
      <p :id="keyboardHelpId" class="sr-only">{{ editMode === 'move'
        ? 'Use the arrow keys to navigate. Press Enter to pick up a piece and again to move it. Press Escape to cancel.'
        : 'Use the arrow keys to navigate and press Enter to use the selected tool.' }}</p>
      <p class="sr-only" aria-live="polite">{{ keyboardSquareDescription }}</p>
    </template>

    <div v-if="controls" class="board-controls">
      <div class="board-controls__buttons" role="group" aria-label="Replay controls">
        <button type="button" :disabled="selectedPly === 0" title="First position (Home)" aria-label="First position" @click="selectPly(0)">
          <AppIcon name="step-back" />
        </button>
        <button type="button" :disabled="selectedPly === 0" title="Previous move (Left arrow)" aria-label="Previous move" @click="selectPly(selectedPly - 1)">
          <AppIcon name="chevron-left" />
        </button>
        <button type="button" :disabled="selectedPly === moves.length" title="Next move (Right arrow)" aria-label="Next move" @click="selectPly(selectedPly + 1)">
          <AppIcon name="chevron-right" />
        </button>
        <button type="button" :disabled="selectedPly === moves.length" title="Latest position (End)" aria-label="Latest position" @click="selectPly(moves.length)">
          <AppIcon name="step-forward" />
        </button>
      </div>
      <p class="board-controls__status" aria-live="polite">{{ status }}</p>
      <p v-if="!takPosition" class="material" :title="`White ${material.white}, Black ${material.black}`">{{ material.label }}</p>
    </div>

    <p class="sr-only">{{ positionDescription }}</p>
  </section>
</template>

<style scoped>
.board-frame { position: relative; container-type: inline-size; }
.board-mount :deep(piece.duck) {
  background-image: v-bind(duckImage);
  background-size: 85%;
  background-position: center;
  background-repeat: no-repeat;
}
.board-mount :deep(cg-board square.annotation-highlight) {
  background: rgb(204 36 36 / 68%);
}
.board-frame--placement .board-mount {
  cursor: crosshair;
}
.board-frame--placement :deep(cg-board),
.board-frame--inactive :deep(cg-board) {
  pointer-events: none;
}
.board-mount:focus-visible :deep(cg-board square.editor-keyboard-square) {
  box-shadow: inset 0 0 0 2px var(--color-primary);
  z-index: 3;
}
.chess-viewer {
  width: 100%;
  min-width: 0;
  border-radius: var(--radius-lg, 0.75rem);
  outline: none;
}

.chess-viewer:focus-visible {
  outline: none;
}

.chess-viewer:focus-visible .board-controls__status {
  color: var(--color-text, #17202a);
  font-weight: 650;
}

.board-mount {
  width: 100%;
  aspect-ratio: 1;
  box-sizing: border-box;
  overflow: hidden;
  border: 1px solid color-mix(in srgb, var(--color-text, #17202a) 28%, transparent);
  border-radius: var(--radius-md, 0.5rem);
  background: #b58863;
  box-shadow: 0 0.75rem 2.5rem color-mix(in srgb, #000 14%, transparent);
}

.board-mount.ninja-board { aspect-ratio: var(--arena-board-aspect, 1.2916667); }

.board-controls {
  display: grid;
  grid-template-columns: auto minmax(7rem, 1fr) auto;
  align-items: center;
  gap: var(--space-sm, 0.5rem);
  padding-block-start: var(--space-sm, 0.5rem);
}

.board-controls__buttons {
  display: flex;
  gap: 0.25rem;
}

.board-controls button {
  display: grid;
  width: 2.25rem;
  height: 2.25rem;
  place-items: center;
  border: 1px solid var(--color-border, #ccd3da);
  border-radius: var(--radius-sm, 0.35rem);
  background: var(--color-surface, #fff);
  color: var(--color-text, #17202a);
  font: inherit;
  font-size: 1.25rem;
  line-height: 1;
  cursor: pointer;
}

.board-controls button:hover:not(:disabled) {
  border-color: var(--color-accent, #2f78c4);
  color: var(--color-accent, #2f78c4);
}

.board-controls button:focus-visible {
  outline: 2px solid var(--color-accent, #2f78c4);
  outline-offset: 2px;
}

.board-controls button:disabled {
  opacity: 0.42;
  cursor: not-allowed;
}

.board-controls__status,
.material {
  margin: 0;
  color: var(--color-text-muted, #607080);
  font-size: 0.82rem;
}

.board-controls__status {
  text-align: center;
}
.chess-viewer--tak .board-controls { grid-template-columns: auto minmax(0, 1fr); }
.chess-viewer--tak .board-controls__status { grid-column: auto; grid-row: auto; text-align: end; }

.material {
  white-space: nowrap;
}

.chess-viewer--compact .board-mount {
  border-radius: var(--radius-sm, 0.35rem);
  box-shadow: none;
}

@media (max-width: 36rem) {
  .board-controls {
    grid-template-columns: 1fr auto;
  }

  .board-controls__status {
    grid-column: 1 / -1;
    grid-row: 2;
    text-align: start;
  }
}

@media (forced-colors: active) {
  .board-mount {
    border: 1px solid CanvasText;
  }
}
</style>
