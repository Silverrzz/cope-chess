import type { Identifier } from "@/components/public/types";

export interface GauntletAttempt {
  id: number;
  game_id: number;
  game_status: string;
  outcome: "pending" | "correct" | "incorrect" | "saved";
  move_uci: string | null;
  elapsed_ms: number | null;
  started_at: string | null;
  finished_at: string | null;
}

export interface GauntletEntry {
  id: number;
  engine_id: Identifier;
  name: string;
  version: string;
  display_name: string;
  author: string;
  status: "active" | "eliminated" | "reserve" | "withdrawn";
  position: number;
  winner: boolean;
  attempt: GauntletAttempt | null;
}

export interface GauntletPuzzle {
  id: number;
  position: number;
  title: string;
  fen: string;
  time_limit_ms: number;
  completed: boolean;
  solutions?: string[];
}

export interface GauntletRound {
  puzzle_id: number;
  position: number;
  title: string;
  fen: string;
  solutions: string[];
  completed_at?: string;
  void: boolean;
  correct_ids: number[];
  eliminated_ids: number[];
}

export interface GauntletTransition {
  completed_puzzle_id: number;
  next_puzzle_id: number;
  started_at: string;
  starts_at: string;
}

export interface GauntletEngineOption {
  id: number;
  engine_id: number;
  name: string;
  version: string;
  author: string;
  source_kind: "release" | "commit";
}

export interface GauntletPayload {
  format: "puzzle-gauntlet";
  phase: "countdown" | "live" | "intermission" | "completed";
  puzzle_count: number;
  puzzles: GauntletPuzzle[];
  entries: GauntletEntry[];
  current_puzzle: GauntletPuzzle | null;
  next_puzzle: GauntletPuzzle | null;
  transition: GauntletTransition | null;
  rounds: GauntletRound[];
  winner_ids: number[];
  tournament: {
    id: number;
    status: string;
    current_round: number;
    scheduled_start_at: string | null;
    started_at: string | null;
    finished_at: string | null;
  } | null;
  worker: {
    id: number;
    label: string;
    status: string;
    claimed_at: string;
    prepared: boolean;
  } | null;
  settings: {
    start_time_ms: number;
    decrement_ms: number;
    minimum_time_ms: number;
    threads: number;
    hash_mb: number;
    scheduled_start_at: string | null;
  };
  engine_options?: GauntletEngineOption[];
}

export interface GauntletEngineInfo {
  engine_id: number;
  game_id: number;
  raw: string;
  root_fen: string;
  engine_data: {
    depth?: string;
    seldepth?: string;
    nps?: string;
    nodes?: string;
    hashfull?: string;
    eval?: string;
    pv?: string;
    pv_san?: string;
    info?: string;
  };
}
