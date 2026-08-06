export type Identifier = number | string

export type TimeControl =
  | { category: 'increment'; initial_ms: number; increment_ms: number }
  | { category: 'movetime'; move_time_ms: number }
  | { category: 'movestogo'; initial_ms: number; moves_to_go: number }
  | { category: 'movenodes'; nodes: number }

export interface TournamentConfig {
  participants?: Identifier[]
  format?: string | { value?: string }
  time_control?: TimeControl
  [key: string]: unknown
}

export interface TournamentRecord {
  id: Identifier
  name: string
  status: string
  current_round?: number
  created_at?: string
  scheduled_start_at?: string | null
  started_at?: string | null
  finished_at?: string | null
  config?: TournamentConfig
}

export interface GameRecord {
  id: Identifier
  tournament_id: Identifier
  round?: number
  pair_index?: number
  game_number?: number
  opening_id?: Identifier | null
  white_engine_id: Identifier
  black_engine_id: Identifier
  white_name?: string
  black_name?: string
  status: string
  result?: string | null
  termination?: string | null
  pgn?: string | null
  started_at?: string | null
  finished_at?: string | null
}

export type EngineGameResultFilter = '' | 'win' | 'draw' | 'loss'
export type EngineGameSideFilter = '' | 'white' | 'black'

export interface EngineGameFilters {
  result: EngineGameResultFilter
  timeControl: string
  opponentId: string
  side: EngineGameSideFilter
}

export interface EngineGameFilterOptions {
  opponent_ids: number[]
  time_controls: Array<{ value: string; label: string }>
}

export interface MoveRecord {
  game_id?: Identifier
  ply: number
  uci: string
  san?: string
  is_book?: boolean
  eval_cp?: number | null
  eval_mate?: number | null
  score_bound?: 'lowerbound' | 'upperbound' | null
  depth?: number | null
  seldepth?: number | null
  nodes?: number | null
  nps?: number | null
  hashfull?: number | null
  pv?: string | null
  pv_san?: string | null
  info_line?: string | null
  time_ms?: number | null
  clock_after_ms?: number | null
  engine_version_id?: Identifier | null
}

export interface OpeningRecord {
  name: string
  fen: string
  book_moves?: string[]
  final_fen?: string
}

export interface EngineRecord {
  id?: Identifier
  engine_id?: Identifier
  name: string
  author?: string | null
  version?: string | null
  repository_url?: string | null
  repository_full_name?: string | null
  source_ref?: string | null
  source_kind?: "release" | "commit" | null
  build_hash?: string | null
  created_at?: string | null
  uci_options?: Record<string, unknown>
  active?: boolean
}

export interface GameSummary {
  total: number
  pairs?: number
  pending?: number
  assigned?: number
  live?: number
  finished?: number
  abandoned?: number
}

export interface TournamentSummary {
  record: TournamentRecord
  summary: GameSummary
  spectator_count?: number
  participant_names?: string[]
  participant_preview?: string[]
  participant_overflow?: number
  participant_count?: number
  progress_percent?: number
  time_control?: string
  format?: string
  estimate?: TournamentEstimate
}

export interface TournamentEstimate {
  estimated_finish_at: string | null
  estimated_remaining_seconds: number | null
  median_game_seconds: number | null
  sample_size: number
  remaining_games: number
  projected_total_games: number
  concurrency: number
  confidence: 'high' | 'medium' | 'low' | 'unavailable'
  basis: 'tournament' | 'historical' | 'unavailable'
  state: 'estimated' | 'paused' | 'complete' | 'unavailable'
}

export interface StandingRecord {
  engine_id: Identifier
  name: string
  points: number
  played: number
  score_percent: number
  wins: number
  draws: number
  losses: number
  buchholz?: number
  bye_points?: number
  stage?: number
}

export interface TournamentRatingRow {
  engine_id: Identifier
  elo_before: number
  elo_after: number
  elo_change: number
  score: number
  games: number
  wins: number
  draws: number
  losses: number
  average_opponent_elo: number
  performance_elo: number
}

export interface TournamentRatingSummary {
  rating_list_id: Identifier
  rating_list_name: string
  average_competitor_elo: number | null
  rows: TournamentRatingRow[]
}

export interface EngineAnalysis {
  engine_id?: Identifier | null
  relay_team_id?: Identifier | null
  relay_team_name?: string | null
  relay_position?: number | null
  relay_moves?: number | null
  node_limit?: number | null
  depth?: string | number | null
  seldepth?: string | number | null
  nodes?: string | number | null
  nps?: string | number | null
  hashfull?: string | number | null
  eval?: string | number | null
  info?: string | null
  pv?: string | null
  pv_san?: string | null
  root_fen?: string | null
}

export interface ClockState {
  game_id?: Identifier
  active_side?: 'white' | 'black' | null
  running?: boolean
  clocks_ms?: Partial<Record<'white' | 'black', number | null>>
  sent_at?: string
  observed_at?: string | null
}

export interface ChatMessage {
  id?: Identifier
  tournament_id?: Identifier | null
  event_id?: Identifier | null
  display_name: string
  text: string
  at?: string
}

export interface ChatSettings {
  enabled?: boolean
  slowmode_seconds?: number
  max_message_length?: number
  allow_anonymous_names?: boolean
}

export interface HardwareRecord {
  engine_id: Identifier
  name: string
  hash?: string
  threads?: string
  hardware?: string
}

export interface TournamentDetailResponse {
  tournament: TournamentRecord
  spectator_count?: number
  estimate?: TournamentEstimate
  games: GameRecord[]
  active_games: GameRecord[]
  game_pagination: GamePagination
  engines: Record<string, string>
  viewer_game: GameRecord | null
  viewer_moves: MoveRecord[]
  viewer_locked?: boolean
  engine_data?: Partial<Record<'white' | 'black', EngineAnalysis>>
  clocks?: Partial<Record<'white' | 'black', string>>
  clock_state?: ClockState | null
  standings?: StandingRecord[]
  rating_summaries?: TournamentRatingSummary[]
  settings?: Array<{ label: string; value: string } | [string, string]>
  engine_hardware?: HardwareRecord[]
  chat_messages?: ChatMessage[]
  chat_settings?: ChatSettings
  opening?: OpeningRecord | null
}

export interface LiveSnapshot {
  tournament?: Partial<TournamentRecord>
  game?: GameRecord | null
  opening?: OpeningRecord | null
  moves?: MoveRecord[]
  engine_data?: Partial<Record<'white' | 'black', EngineAnalysis>>
  clocks?: Partial<Record<'white' | 'black', string>>
  clock_state?: ClockState | null
  standings?: StandingRecord[]
  active_games?: GameRecord[]
}

export interface GamePagination {
  page: number
  page_size: number
  total: number
  pages: number
}

export interface StreamEnvelope<T = Record<string, unknown>> {
  type: string
  sent_at?: string
  data: T
}
