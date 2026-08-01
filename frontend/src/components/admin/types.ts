export type Id = number

export interface Engine {
  id: Id
  engine_id: Id
  name: string
  author?: string
  version: string
  git_host_id: Id | null
  repository_url: string
  repository_full_name: string
  source_ref: string
  source_kind: 'release' | 'commit'
  dockerfile_path: string
  dockerfile: string
  build_hash: string
  uci_options: Record<string, string | number | boolean>
  active: boolean
  benchmark_current?: boolean
  engine_active?: boolean
  created_at?: string
}

export interface EngineBenchmarkJob {
  id: Id
  build_hash: string
  hardware_key: string
  status: 'queued' | 'running' | 'succeeded' | 'failed'
  attempt: number
  scheduled_at: string
  started_at: string | null
  finished_at: string | null
  next_retry_at: string | null
  error: string
  output: string
  benchmarker: { id: Id | null; label: string; status: string | null } | null
  hardware: { cpu_model: string; physical_cores: number; logical_cores: number; ram_gb: number } | null
  result: { nps: number; elapsed_ms: number; recorded_at: string } | null
}

export interface EngineFamily {
  id: Id
  name: string
  author?: string
  active: boolean
  versions: Engine[]
}

export interface Category {
  id: Id
  name: string
  description?: string
  active?: boolean
  default_config?: Partial<CategorySettings>
  created_at?: string
}

export interface OpeningSuite {
  id: Id
  name: string
  description?: string
  created_at?: string
}

export type TournamentFormat = 'round_robin' | 'swiss' | 'knockout' | 'gauntlet'
export type TimeControlCategory = 'increment' | 'movetime' | 'movestogo' | 'movenodes'

export type FormatOptions =
  | { cycles: number }
  | { rounds: number }
  | { tiebreak: 'extra_pair' }
  | { hero_engine_id: number }

export type TimeControl =
  | { category: 'increment'; initial_ms: number; increment_ms: number }
  | { category: 'movetime'; move_time_ms: number }
  | { category: 'movestogo'; initial_ms: number; moves_to_go: number }
  | { category: 'movenodes'; nodes: number }

export interface TournamentSettings {
  format: TournamentFormat
  format_options: FormatOptions
  time_control: TimeControl
  concurrency: number
  opening_suite_id: number | null
  adjudication: {
    draw: {
      min_fullmove: number
      max_abs_cp: number
      consecutive_plies: number
    } | null
    resign: {
      min_abs_cp: number
      consecutive_plies: number
    } | null
    max_moves: number | null
  }
  rated: boolean
  lag_compensation_ms: number
}

export interface CategorySettings extends TournamentSettings {
  engine_threads: number
  engine_hash_mb: number
}

export interface TournamentConfig extends TournamentSettings {
  participants: number[]
  engine_threads: number
  engine_hash_mb: number
  uci_options: Record<string, string | number | boolean>
}

export interface Tournament {
  id: Id
  name: string
  config: TournamentConfig
  status: string
  current_round?: number
  created_at?: string
  started_at?: string | null
  finished_at?: string | null
}

export interface Game {
  id: Id
  tournament_id: Id
  round: number
  white_engine_id: Id
  black_engine_id: Id
  status: string
  result?: string | null
  termination?: string | null
  started_at?: string | null
  finished_at?: string | null
}

export interface Worker {
  id: Id
  label: string
  status: string
  token_expires_at?: string | null
  session_id?: string | null
  app_version?: string | null
  protocol_version?: number | null
  machine_id?: string | null
  last_seen?: string | null
  hw?: {
    cpu_model: string
    physical_cores: number
    logical_cores: number
    ram_gb: number
    ram_mb?: number | null
    gpu?: string | null
    os?: string
    python?: string
    bench?: { nps_probe?: number | null }
  } | null
}

export interface WorkerRow {
  worker: Worker
  status: string
  machine?: { status: string; label: string; detail: string }
  session?: { status: string; label: string; detail: string }
  token?: { status: string; label: string; detail: string }
  work?: { summary: string; detail?: string; meta?: string; href?: string | null }
}

export interface FormSeed {
  config?: TournamentConfig
  form_values?: Record<string, unknown>
  form_name?: string
  form_participants?: number[]
  engine_options: Engine[]
  opening_suites: OpeningSuite[]
  editing?: Tournament | boolean | null
}
