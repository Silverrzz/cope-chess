<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import api from '@/api/client'
import ChessBoard from '@/components/chess/ChessBoard.vue'
import { parseFen, positionFen, squareIndex, START_FEN, type Piece, type Position } from '@/components/chess/chess'
import InlineFeedback from '@/components/admin/InlineFeedback.vue'
import AppIcon from '@/components/ui/AppIcon.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import type { ChessPositionAnswer } from '@/types/forms'

const props = defineProps<{ initialFen: string }>()
const emit = defineEmits<{ apply: [value: ChessPositionAnswer] }>()

const EMPTY_FEN = '8/8/8/8/8/8/8/8 w - - 0 1'
const pieces = [
  { role: 'king', white: 'K', black: 'k' },
  { role: 'queen', white: 'Q', black: 'q' },
  { role: 'rook', white: 'R', black: 'r' },
  { role: 'bishop', white: 'B', black: 'b' },
  { role: 'knight', white: 'N', black: 'n' },
  { role: 'pawn', white: 'P', black: 'p' },
] as const
const palette = [
  { color: 'black', tool: 'move', icon: 'move', label: 'Move pieces' },
  { color: 'white', tool: 'erase', icon: 'trash', label: 'Remove pieces' },
] as const
const castling = [
  { right: 'K', label: 'O-O', side: 'kingside', king: 'K', rook: 'R', kingSquare: 'e1', rookSquare: 'h1' },
  { right: 'Q', label: 'O-O-O', side: 'queenside', king: 'K', rook: 'R', kingSquare: 'e1', rookSquare: 'a1' },
  { right: 'k', label: 'O-O', side: 'kingside', king: 'k', rook: 'r', kingSquare: 'e8', rookSquare: 'h8' },
  { right: 'q', label: 'O-O-O', side: 'queenside', king: 'k', rook: 'r', kingSquare: 'e8', rookSquare: 'a8' },
] as const
const castlingRows = [
  { color: 'White', options: castling.slice(0, 2) },
  { color: 'Black', options: castling.slice(2) },
]

const fen = ref(START_FEN)
const fenInput = ref(props.initialFen.trim() || START_FEN)
const tool = ref<Piece | 'move' | 'erase'>('move')
const pending = ref<'import' | 'apply' | null>(null)
const error = ref('')
const controller = new AbortController()
const position = computed(() => parseFen(fen.value))
const inactive = computed(() => pending.value !== null)
const editMode = computed(() => inactive.value ? 'disabled' : tool.value === 'move' ? 'move' : 'place')

onMounted(() => {
  if (fenInput.value !== START_FEN) void validateFen('import')
})
onBeforeUnmount(() => controller.abort())

function setPosition(value: string) {
  fen.value = value
  fenInput.value = value
  error.value = ''
}

function hasCastlingPieces(board: Position, option: typeof castling[number]) {
  return board.squares[squareIndex(option.kingSquare)] === option.king &&
    board.squares[squareIndex(option.rookSquare)] === option.rook
}

function updatePieces(board: Position) {
  board.castling = castling
    .filter(option => board.castling.includes(option.right) && hasCastlingPieces(board, option))
    .map(option => option.right).join('') || '-'
  board.enPassant = '-'
  board.halfmove = 0
  setPosition(positionFen(board))
}

function place(square: string) {
  const selected = tool.value
  if (inactive.value || selected === 'move') return
  const board = parseFen(fen.value)
  const index = squareIndex(square)
  const piece = selected === 'erase' ? null : selected
  if (board.squares[index] === piece) return
  board.squares[index] = piece
  updatePieces(board)
}

function movePiece(from: string, to: string) {
  if (inactive.value || tool.value !== 'move') return
  const board = parseFen(fen.value)
  const origin = squareIndex(from)
  const piece = board.squares[origin]
  if (!piece) return
  board.squares[origin] = null
  board.squares[squareIndex(to)] = piece
  updatePieces(board)
}

function setTurn(event: Event) {
  const board = parseFen(fen.value)
  board.turn = (event.target as HTMLSelectElement).value === 'b' ? 'b' : 'w'
  board.enPassant = '-'
  setPosition(positionFen(board))
}

function setCastling(right: typeof castling[number]['right'], checked: boolean) {
  const board = parseFen(fen.value)
  board.castling = castling.filter(option => option.right === right ? checked : board.castling.includes(option.right))
    .map(option => option.right).join('') || '-'
  setPosition(positionFen(board))
}

async function validateFen(action: 'import' | 'apply') {
  if (inactive.value) return
  const input = fenInput.value.trim()
  if (!input) {
    error.value = 'Enter a FEN.'
    return
  }
  pending.value = action
  error.value = ''
  try {
    const answer = await api.post<ChessPositionAnswer>('/api/forms/resources/position', {
      body: { fen: input }, signal: controller.signal,
    })
    if (action === 'apply') emit('apply', answer)
    else setPosition(answer.fen)
  } catch (cause) {
    if (!controller.signal.aborted) error.value = cause instanceof Error ? cause.message : 'Could not validate the position.'
  } finally {
    pending.value = null
  }
}
</script>

<template>
  <div class="section-stack position-editor">
    <div class="position-layout">
      <ChessBoard :fen="fen" :controls="false" :edit-mode="editMode" compact label="Editable chess position"
        @select-square="place" @move-piece="movePiece" />

      <div class="section-stack position-controls">
        <div class="position-palette" role="group" aria-label="Board tools">
          <template v-for="row in palette" :key="row.color">
            <BaseButton
              class="position-tool" size="small" icon-only :variant="tool === row.tool ? 'primary' : 'secondary'"
              :disabled="inactive" :aria-pressed="tool === row.tool" :title="row.label" @click="tool = row.tool"
            >
              <template #icon><AppIcon :name="row.icon" :size="18" /></template>
              {{ row.label }}
            </BaseButton>
            <BaseButton
              v-for="piece in pieces" :key="piece[row.color]"
              class="position-tool" size="small" icon-only :variant="tool === piece[row.color] ? 'primary' : 'secondary'"
              :disabled="inactive" :aria-pressed="tool === piece[row.color]" :title="`${row.color} ${piece.role}`"
              @click="tool = piece[row.color]"
            >
              <template #icon>
                <span class="cg-wrap position-piece" aria-hidden="true">
                  <component :is="'piece'" :class="[row.color, piece.role]" />
                </span>
              </template>
              Place {{ row.color }} {{ piece.role }}
            </BaseButton>
          </template>
        </div>

        <label class="field">
          <span>Side to move</span>
          <select class="select" :value="position.turn" :disabled="inactive" @change="setTurn">
            <option value="w">White</option>
            <option value="b">Black</option>
          </select>
        </label>

        <fieldset class="position-castling field" :disabled="inactive">
          <legend>Castling rights</legend>
          <div v-for="row in castlingRows" :key="row.color" class="position-castling-row">
            <span>{{ row.color }}</span>
            <label v-for="option in row.options" :key="option.right" class="position-castling-option">
              <input type="checkbox" :checked="position.castling.includes(option.right)"
                :disabled="!hasCastlingPieces(position, option)" :aria-label="`${row.color} ${option.side} castling`"
                @change="setCastling(option.right, ($event.target as HTMLInputElement).checked)">
              {{ option.label }}
            </label>
          </div>
        </fieldset>

        <div class="button-row">
          <BaseButton size="small" :disabled="inactive" @click="setPosition(START_FEN)">Starting position</BaseButton>
          <BaseButton size="small" :disabled="inactive" @click="setPosition(EMPTY_FEN)">Empty board</BaseButton>
        </div>

        <div class="position-import">
          <label class="field">
            <span>FEN</span>
            <input v-model="fenInput" class="input input--mono" type="text" maxlength="120" :disabled="inactive"
              spellcheck="false" autocapitalize="off" @keydown.enter.prevent="validateFen('import')">
          </label>
          <BaseButton size="small" :disabled="inactive" :loading="pending === 'import'" @click="validateFen('import')">Import FEN</BaseButton>
        </div>

        <InlineFeedback :message="error" />
      </div>
    </div>
    <div class="button-row position-apply">
      <BaseButton variant="primary" :disabled="inactive" :loading="pending === 'apply'" @click="validateFen('apply')">Use position</BaseButton>
    </div>
  </div>
</template>

<style scoped>
.position-editor {
  container-type: inline-size;
}

.position-layout {
  display: grid;
  gap: var(--space-4);
  align-items: start;
}

.position-layout > .chess-viewer {
  max-width: 25rem;
}

.position-controls {
  min-width: 0;
}

.position-palette {
  display: grid;
  grid-template-columns: repeat(7, minmax(0, 1fr));
  gap: var(--space-1);
}

.position-palette > .position-tool {
  width: 100%;
  aspect-ratio: 1;
}

.position-piece {
  width: 86%;
  aspect-ratio: 1;
}

.position-piece :deep(piece) {
  width: 100%;
  height: 100%;
}

.position-piece :deep(piece.black) {
  filter: drop-shadow(0 0 1px color-mix(in srgb, var(--color-text) 60%, transparent));
}

.position-castling {
  padding: 0;
  margin: 0;
  border: 0;
}

.position-castling legend {
  margin-bottom: var(--space-2);
  padding: 0;
}

.position-castling-row {
  display: grid;
  grid-template-columns: 3rem 4rem 1fr;
  align-items: center;
  gap: var(--space-2);
}

.position-castling-option {
  display: flex;
  align-items: center;
  gap: var(--space-1);
  font-weight: 500;
}

.position-import {
  display: grid;
  gap: var(--space-2);
  justify-items: start;
}

.position-import .field {
  width: 100%;
}

.position-apply {
  justify-content: flex-end;
}

@container (min-width: 34rem) {
  .position-layout {
    grid-template-columns: minmax(0, 1.25fr) minmax(16.5rem, 1fr);
  }
}
</style>
