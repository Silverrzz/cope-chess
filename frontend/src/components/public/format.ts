import { localDateFormatter, timestampDate } from '@/utils/time'
import type { GameRecord, Identifier, MoveRecord, TimeControl } from './types'

export function recordValue<T>(record: Record<string, T> | undefined, id: Identifier): T | undefined {
  return record?.[String(id)]
}

export function engineName(
  engines: Record<string, string> | undefined,
  id: Identifier,
  explicitName?: string,
): string {
  return explicitName || recordValue(engines, id) || `Engine ${id}`
}

export function tournamentName(
  tournaments: Record<string, string> | undefined,
  id: Identifier,
): string {
  return recordValue(tournaments, id) || `Tournament ${id}`
}

export function statusLabel(status?: string | null): string {
  if (!status) return 'Unknown'
  return status.replaceAll('_', ' ').replace(/\b\w/g, (letter) => letter.toUpperCase())
}

export function resultLabel(result?: string | null): string {
  if (result === '1/2-1/2') return '1/2-1/2'
  return result || 'Not finished'
}

export function shortResult(result?: string | null): string {
  return result || '-'
}

export function formatNumber(value?: string | number | null): string {
  if (value === undefined || value === null || value === '') return '-'
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed.toLocaleString() : String(value)
}

function compactMetric(
  value: string | number | null | undefined,
  units: ReadonlyArray<{ threshold: number; divisor: number; suffix: string }>,
  baseSuffix: string,
): string {
  if (value === undefined || value === null || value === '') return '-'

  const parsed = typeof value === 'string'
    ? Number(value.replaceAll(',', '').trim())
    : Number(value)
  if (!Number.isFinite(parsed)) return String(value)

  const magnitude = Math.abs(parsed)
  let unitIndex = units.findIndex((candidate) => magnitude >= candidate.threshold)
  const matchedUnit = units[unitIndex]
  if (unitIndex > 0 && matchedUnit) {
    const rounded = Math.round((magnitude / matchedUnit.divisor) * 10) / 10
    if (rounded >= 1_000) unitIndex -= 1
  }
  const unit = unitIndex >= 0 ? units[unitIndex] : undefined
  const scaled = unit ? parsed / unit.divisor : parsed
  const formatted = scaled.toLocaleString(undefined, { maximumFractionDigits: 1 })
  return `${formatted}${unit?.suffix ?? baseSuffix}`
}

export function formatNodes(value?: string | number | null): string {
  return compactMetric(value, [
    { threshold: 1_000_000_000, divisor: 1_000_000_000, suffix: 'B' },
    { threshold: 1_000_000, divisor: 1_000_000, suffix: 'M' },
    { threshold: 1_000, divisor: 1_000, suffix: 'K' },
  ], '')
}

export function formatNps(value?: string | number | null): string {
  return compactMetric(value, [
    { threshold: 1_000_000_000, divisor: 1_000_000_000, suffix: 'G' },
    { threshold: 1_000_000, divisor: 1_000_000, suffix: 'M' },
    { threshold: 1_000, divisor: 1_000, suffix: 'K' },
  ], '')
}

export function formatDate(value?: string | null, includeTime = false): string {
  if (!value) return '-'
  const date = timestampDate(value)
  if (Number.isNaN(date.valueOf())) return value
  return localDateFormatter({
    dateStyle: 'medium',
    ...(includeTime ? { timeStyle: 'short' as const } : {}),
  }).format(date)
}

export function gameHref(game: GameRecord): { name: string; params: { id: Identifier }; query: { game_id: Identifier } } {
  return {
    name: 'tournament',
    params: { id: game.tournament_id },
    query: { game_id: game.id },
  }
}

export function moveEvaluation(move?: MoveRecord | null): string {
  if (!move) return '-'
  const prefix = move.score_bound === 'lowerbound' ? '≥' : move.score_bound === 'upperbound' ? '≤' : ''
  if (move.eval_mate !== null && move.eval_mate !== undefined) return `${prefix}#${move.eval_mate}`
  if (move.eval_cp !== null && move.eval_cp !== undefined) {
    const value = move.eval_cp / 100
    return `${prefix}${value >= 0 ? '+' : ''}${value.toFixed(2)}`
  }
  return '-'
}

export function scorePercentLabel(value: number): string {
  return `${value.toLocaleString(undefined, { maximumFractionDigits: 1 })}%`
}

export function timeControlLabel(control?: TimeControl): string {
  if (!control) return ''
  if (control.category === 'increment') {
    return `${durationLabel(control.initial_ms)}+${durationLabel(control.increment_ms)}`
  }
  if (control.category === 'movetime') return `${durationLabel(control.move_time_ms)}/move`
  if (control.category === 'movestogo') {
    return `${durationLabel(control.initial_ms)}/${control.moves_to_go}`
  }
  return `${formatNumber(control.nodes)} nodes`
}

function durationLabel(milliseconds: number): string {
  if (milliseconds === 0) return '0s'
  if (milliseconds >= 60_000) {
    const minutes = Math.floor(milliseconds / 60_000)
    const remainder = milliseconds % 60_000
    return remainder ? `${minutes}m${durationLabel(remainder)}` : `${minutes}m`
  }
  if (milliseconds >= 1_000) return `${(milliseconds / 1_000).toLocaleString()}s`
  return `${milliseconds}ms`
}

export function moveNps(move?: MoveRecord | null): string {
  if (!move) return '-'
  if (move.nps !== null && move.nps !== undefined) return formatNumber(move.nps)
  if (move.nodes && move.time_ms && move.time_ms > 0) {
    return formatNumber(Math.floor(move.nodes / (move.time_ms / 1000)))
  }
  return '-'
}

export function clockLabel(milliseconds?: number | null): string {
  if (milliseconds === null || milliseconds === undefined || !Number.isFinite(milliseconds)) return '--:--'
  const total = Math.max(0, Math.floor(milliseconds))
  const seconds = Math.floor(total / 1000)
  const minutes = Math.floor(seconds / 60)
  return `${String(minutes).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}.${Math.floor((total % 1000) / 100)}`
}

export function errorMessage(error: unknown, fallback = 'Something went wrong.'): string {
  if (error && typeof error === 'object') {
    const candidate = error as { detail?: unknown; message?: unknown }
    if (typeof candidate.detail === 'string' && candidate.detail) return candidate.detail
    if (typeof candidate.message === 'string' && candidate.message) return candidate.message
  }
  return fallback
}
