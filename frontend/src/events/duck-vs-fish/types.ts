import type { GameRecord, TournamentRecord } from "@/components/public/types";
import type { EventCastMember } from "@/types/events";

export function pondRoster(cast: EventCastMember[]): EventCastMember[] {
  return cast.filter(member => member.parent_id === null).map(member => ({
    ...member,
    display_name: /^wakwak$/i.test(member.display_name) ? "wakwak" : member.display_name,
    short_name: /^wakwak$/i.test(member.short_name) ? "wakwak" : member.short_name,
  }));
}

export interface PondPayload {
  tournament: TournamentRecord | null;
  games: GameRecord[];
  opening_book: string;
}
