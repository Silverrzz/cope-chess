import type { GameRecord, TournamentRecord } from "@/components/public/types";

export interface PondPayload {
  tournament: TournamentRecord | null;
  games: GameRecord[];
  opening_book: string;
}
