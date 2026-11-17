<script setup lang="ts">
import { ref, useId, watch } from 'vue'
import api from '@/api/client'
import ChessBoard from '@/components/chess/ChessBoard.vue'
import { parseFen, positionFen, START_FEN, type Piece } from '@/components/chess/chess'
import InlineFeedback from '@/components/admin/InlineFeedback.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import type { ChessPositionAnswer } from '@/types/forms'

const props = defineProps<{ modelValue?: unknown; id: string; disabled?: boolean | undefined }>()
const emit = defineEmits<{ 'update:modelValue': [value: ChessPositionAnswer | undefined] }>()
const fen = ref(START_FEN)
const fenInput = ref('')
const pgn = ref('')
const piece = ref<Piece | ''>('P')
const square = ref('e4')
const pending = ref(false)
const error = ref('')
const ids = useId()
const squares = Array.from({ length: 64 }, (_, index) => 'abcdefgh'[index % 8] + String(8 - Math.floor(index / 8)))
const palette: { name: string; value: Piece | '' }[] = [
  { name: 'White pawn', value: 'P' }, { name: 'White knight', value: 'N' }, { name: 'White bishop', value: 'B' },
  { name: 'White rook', value: 'R' }, { name: 'White queen', value: 'Q' }, { name: 'White king', value: 'K' },
  { name: 'Black pawn', value: 'p' }, { name: 'Black knight', value: 'n' }, { name: 'Black bishop', value: 'b' },
  { name: 'Black rook', value: 'r' }, { name: 'Black queen', value: 'q' }, { name: 'Black king', value: 'k' },
  { name: 'Eraser', value: '' },
]

watch(() => props.modelValue, value => {
  const position = value as ChessPositionAnswer | undefined
  if (!position?.fen) return
  fen.value = position.fen
  fenInput.value = position.fen
  pgn.value = position.pgn ?? ''
}, { immediate: true })

function setPosition(value: ChessPositionAnswer) {
  fen.value = value.fen
  fenInput.value = value.fen
  pgn.value = value.pgn ?? ''
  error.value = ''
  emit('update:modelValue', value)
}

function place(key: string) {
  if (props.disabled || pending.value) return
  const index = squares.indexOf(key)
  if (index < 0) return
  square.value = key
  const position = parseFen(fen.value)
  position.squares[index] = piece.value || null
  position.castling = '-'
  position.enPassant = '-'
  position.halfmove = 0
  setPosition({ fen: positionFen(position) })
}

function setTurn(event: Event) {
  const position = parseFen(fen.value)
  position.turn = (event.target as HTMLSelectElement).value === 'b' ? 'b' : 'w'
  position.enPassant = '-'
  setPosition({ fen: positionFen(position) })
}

async function apply(kind: 'fen' | 'pgn') {
  const payload = kind === 'fen' ? { fen: fenInput.value.trim() } : { pgn: pgn.value.trim() }
  if (!Object.values(payload)[0]) {
    error.value = 'Enter ' + (kind === 'fen' ? 'a FEN.' : 'a PGN.')
    return
  }
  pending.value = true
  error.value = ''
  try {
    setPosition(await api.post<ChessPositionAnswer>('/api/forms/resources/position', { body: payload }))
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : 'Could not import the position.'
  } finally {
    pending.value = false
  }
}

function clear() {
  fen.value = START_FEN
  fenInput.value = ''
  pgn.value = ''
  error.value = ''
  emit('update:modelValue', undefined)
}
</script>

<template>
  <div class="section-stack">
    <div class="position-board">
      <ChessBoard :fen="fen" :controls="false" :selectable="!disabled && !pending" compact label="Editable chess position" @select-square="place" />
    </div>
    <div class="form-grid">
      <label class="field"><span>Piece</span><select v-model="piece" class="select" :disabled="disabled || pending"><option v-for="item in palette" :key="item.name" :value="item.value">{{ item.name }}</option></select></label>
      <label class="field"><span>Square</span><select v-model="square" class="select" :disabled="disabled || pending"><option v-for="item in squares" :key="item">{{ item }}</option></select></label>
      <label class="field"><span>Side to move</span><select class="select" :value="parseFen(fen).turn" :disabled="disabled || pending" @change="setTurn"><option value="w">White</option><option value="b">Black</option></select></label>
      <div class="button-row position-actions"><BaseButton :disabled="disabled || pending" @click="place(square)">Place on {{ square }}</BaseButton></div>
    </div>
    <div class="button-row">
      <BaseButton size="small" :disabled="disabled || pending" @click="setPosition({ fen: START_FEN })">Use starting position</BaseButton>
      <BaseButton size="small" :disabled="disabled || pending" @click="setPosition({ fen: '8/8/8/8/8/8/8/8 w - - 0 1' })">Empty board</BaseButton>
      <BaseButton v-if="modelValue" size="small" variant="ghost" :disabled="disabled || pending" @click="clear">Clear answer</BaseButton>
    </div>
    <label class="field"><span>FEN</span><input :id="id" v-model="fenInput" class="input input--mono" type="text" maxlength="120" :disabled="disabled || pending" spellcheck="false"></label>
    <div class="button-row"><BaseButton size="small" :disabled="disabled" :loading="pending" @click="apply('fen')">Apply FEN</BaseButton></div>
    <details class="section-stack">
      <summary>Import PGN</summary>
      <label class="field" :for="ids + '-pgn'"><span>PGN game</span><textarea :id="ids + '-pgn'" v-model="pgn" class="textarea input--mono" rows="4" maxlength="32000" :disabled="disabled || pending" /></label>
      <div class="button-row"><BaseButton size="small" :disabled="disabled" :loading="pending" @click="apply('pgn')">Use final position</BaseButton></div>
    </details>
    <InlineFeedback :message="error" />
  </div>
</template>

<style scoped>
.position-board {
  max-width: 22rem;
}

.position-actions {
  align-self: end;
}
</style>
