export interface SecretQueenClock {
  initial_ms: number;
  increment_ms: number;
}

export interface SwissGroup {
  name: string;
  engine_ids: number[];
  rounds: number;
  qualifiers: number;
}

export interface SecretQueenSettings {
  groups: SwissGroup[];
  group_clock: SecretQueenClock;
  knockout_clock: SecretQueenClock;
  hash_mb: number;
  group_concurrency: number;
}

export interface SecretQueenGame {
  id: number;
  round: number;
  pair_index: number;
  white_engine_id: number;
  black_engine_id: number;
  status: string;
  result: string | null;
}

export interface SecretQueenContest {
  id: number;
  key: string;
  title: string;
  stage: string;
  kind: "group" | "bracket";
  status: string;
  result: string;
  tournament_id: number | null;
  engine_ids: number[];
  winner_engine_id: number | null;
  winning_pair: number | null;
  sources: (number | string)[];
  qualifier_count: number;
  rounds: number;
  qualified: number[];
  standings: Array<{ engine_id: number; points: number; buchholz: number }>;
  games: SecretQueenGame[];
  time_control: SecretQueenClock | null;
}
export interface SecretQueenCompetition {
  contests: SecretQueenContest[];
  planned_contests?: SecretQueenContest[];
  winner_engine_id: number | null;
  launch_ready: boolean;
}

export interface SecretQueenEngineChoice {
  id: number;
  engine_id: number;
  name: string;
  version: string;
  author: string;
  active: boolean;
}

export interface SecretQueenAdmin extends SecretQueenCompetition {
  settings: SecretQueenSettings;
  engines: SecretQueenEngineChoice[];
}
