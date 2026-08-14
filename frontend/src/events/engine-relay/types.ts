import type { GameRecord, Identifier, TournamentRecord } from "@/components/public/types";

export interface RelayRosterMember {
  id: number;
  engine_id: Identifier;
  name: string;
  version: string;
  display_name: string;
  label: string;
  threads: number;
  hash_mb: number;
  position: number;
  rating: {
    elo: number;
    error_margin: number | null;
    list_name: string;
  } | null;
}

export interface RelayTeam {
  id: number;
  name: string;
  short_name: string;
  profile: string;
  motto: string;
  primary_color: string;
  secondary_color: string;
  roster: RelayRosterMember[];
  locked: boolean;
}

export interface RelayGame extends GameRecord {
  white_team_id: number;
  black_team_id: number;
  white_active_engine_id: Identifier | null;
  black_active_engine_id: Identifier | null;
}

export interface RelayFixture {
  id: number;
  event_id: number;
  tournament_id: number;
  team_a_id: number;
  team_b_id: number;
  anchor_a_engine_id: Identifier;
  anchor_b_engine_id: Identifier;
  title: string;
  position: number;
  created_at: string;
  team_a_name: string;
  team_b_name: string;
  teams?: Array<{
    id: number;
    name: string;
    anchor_engine_id: Identifier;
    position: number;
  }>;
  tournament: TournamentRecord | null;
  worker: {
    id: number;
    label: string;
    status: string;
    claimed_at: string;
    prepared: boolean;
  } | null;
  games: RelayGame[];
  winner_team_id: number | null;
  kibitzer: {
    engine_id: Identifier;
    name: string;
    version: string;
    threads: number;
    hash_mb: number;
  } | null;
}

export interface RelayEngineOption {
  id: number;
  engine_id: number;
  name: string;
  version: string;
  author: string;
  source_kind: "release" | "commit";
}

export interface RelayOpeningSuite {
  id: number;
  name: string;
  description: string;
}

export interface EngineRelayPayload {
  format: "engine-relay" | "engine-relay-finale";
  teams: RelayTeam[];
  fixtures: RelayFixture[];
  engine_options?: RelayEngineOption[];
  opening_suites?: RelayOpeningSuite[];
}
