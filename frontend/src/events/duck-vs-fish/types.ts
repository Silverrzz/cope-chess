import type { GameRecord, TournamentRecord } from "@/components/public/types";
import type { EventCastMember } from "@/types/events";

export function pondRoster(cast: EventCastMember[]): EventCastMember[] {
  return cast.filter(member => member.parent_id === null).map(member => ({
    ...member,
    display_name: member.member_key === "fish" ? "Fairy-Stockfish" : /^wakwak$/i.test(member.display_name) ? "wakwak" : member.display_name,
    short_name: member.member_key === "fish" ? "Fairy-Stockfish" : /^wakwak$/i.test(member.short_name) ? "wakwak" : member.short_name,
    profile: member.member_key === "fish" ? "Fairy-Stockfish Duck NNUE" : member.profile,
  }));
}

export interface PondPayload {
  tournament: TournamentRecord | null;
  games: GameRecord[];
  opening_book: string;
  odds_steps?: string[];
}
