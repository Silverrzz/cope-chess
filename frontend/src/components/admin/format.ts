import type {
  FormSeed,
  TimeControl,
  TournamentConfig,
  TournamentFormat,
  TournamentSettings,
} from './types'

export function cloneData<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T
}

export function humanize(value: string | null | undefined): string {
  if (!value) return 'Unknown'
  return value.replaceAll('_', ' ').replace(/\b\w/g, (letter) => letter.toUpperCase())
}

export function formatDate(value: string | null | undefined): string {
  if (!value) return 'Never'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(date)
}

export function formatNumber(value: number | null | undefined): string {
  return new Intl.NumberFormat().format(value ?? 0)
}

export function formatTimeControl(control: TimeControl | undefined): string {
  if (!control) return 'Not set'
  if (control.category === 'increment') {
    return `${formatSeconds(control.initial_ms)} + ${formatSeconds(control.increment_ms)}`
  }
  if (control.category === 'movetime') return `${formatSeconds(control.move_time_ms)} per move`
  if (control.category === 'movestogo') {
    return `${formatSeconds(control.initial_ms)} / ${control.moves_to_go} moves`
  }
  return `${formatNumber(control.nodes)} nodes per move`
}

function formatSeconds(milliseconds: number): string {
  const seconds = milliseconds / 1000
  if (seconds >= 60 && seconds % 60 === 0) return `${seconds / 60} min`
  return `${seconds.toLocaleString()} sec`
}

export function defaultSettings(): TournamentSettings {
  return {
    format: 'round_robin',
    format_options: { cycles: 1 },
    time_control: { category: 'increment', initial_ms: 60_000, increment_ms: 1_000 },
    concurrency: 1,
    opening_suite_id: null,
    adjudication: { draw: null, resign: null, max_moves: null },
    rated: true,
    lag_compensation_ms: 50,
  }
}

function positiveInt(value: unknown, fallback: number): number {
  const parsed = Number(value)
  return Number.isInteger(parsed) && parsed > 0 ? parsed : fallback
}

function nonNegativeInt(value: unknown, fallback: number): number {
  const parsed = Number(value)
  return Number.isInteger(parsed) && parsed >= 0 ? parsed : fallback
}

export function normalizeSettings(value: Partial<TournamentSettings> | undefined): TournamentSettings {
  const defaults = defaultSettings()
  if (!value || !value.format || !value.time_control) return defaults
  const rawOptions = (value.format_options || {}) as unknown as Record<string, unknown>
  const format_options: TournamentSettings['format_options'] = value.format === 'round_robin'
    ? {
        cycles: positiveInt(rawOptions.cycles, 1),
      }
    : value.format === 'swiss'
      ? { rounds: positiveInt(rawOptions.rounds, 7) }
      : value.format === 'knockout'
        ? { tiebreak: 'extra_pair' }
        : { hero_engine_id: positiveInt(rawOptions.hero_engine_id, 0) }
  return {
    ...defaults,
    ...cloneData(value),
    format_options,
    adjudication: {
      draw: value.adjudication?.draw
        ? {
            min_fullmove: positiveInt(value.adjudication.draw.min_fullmove, 40),
            max_abs_cp: nonNegativeInt(value.adjudication.draw.max_abs_cp, 10),
            consecutive_plies: Math.max(2, positiveInt(value.adjudication.draw.consecutive_plies, 8)),
          }
        : null,
      resign: value.adjudication?.resign
        ? {
            min_abs_cp: positiveInt(value.adjudication.resign.min_abs_cp, 800),
            consecutive_plies: Math.max(2, positiveInt(value.adjudication.resign.consecutive_plies, 6)),
          }
        : null,
      max_moves: value.adjudication?.max_moves ?? null,
    },
    lag_compensation_ms: value.lag_compensation_ms ?? 50,
  } as TournamentSettings
}

export function configFromSeed(seed: FormSeed): TournamentConfig {
  return normalizeConfig(seed.config)
}

function normalizeConfig(config: Partial<TournamentConfig>): TournamentConfig {
  const normalized = normalizeSettings(config)
  return {
    ...config,
    ...normalized,
    engine_threads: positiveInt(config.engine_threads, 1),
    engine_hash_mb: positiveInt(config.engine_hash_mb, 16),
    uci_options: config.uci_options ?? {},
  } as TournamentConfig
}

export function estimatePairs(format: TournamentFormat, options: TournamentSettings['format_options'], players: number): number {
  if (players < 2) return 0
  if (format === 'round_robin') {
    return (players * (players - 1)) / 2 * ('cycles' in options ? options.cycles : 0)
  }
  if (format === 'swiss') {
    return Math.floor(players / 2) * ('rounds' in options ? options.rounds : 0)
  }
  if (format === 'knockout') {
    return players - 1
  }
  return players - 1
}

export function estimateGames(format: TournamentFormat, options: TournamentSettings['format_options'], players: number): number {
  return estimatePairs(format, options, players) * 2
}

export function errorText(error: unknown): string {
  if (error instanceof Error) return error.message
  if (typeof error === 'string') return error
  if (error && typeof error === 'object' && 'detail' in error) {
    const detail = (error as { detail: unknown }).detail
    return Array.isArray(detail) ? detail.join(' ') : String(detail)
  }
  return 'Something went wrong. Please try again.'
}
