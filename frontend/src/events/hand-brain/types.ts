import type { GameRecord, EngineAnalysis } from '@/components/public/types'

export interface HandBrainMember { id: number; name: string; engine_id: number | null; ready: boolean }
export interface HandBrainTeam { id: number; name: string; seed: number; color: string; elo: number; members: HandBrainMember[] }
export interface HandBrainQuad { tournament_id: number; time_control: { initial_ms: number; increment_ms: number }; score: number[]; games: GameRecord[] }
export interface HandBrainMatch { id: number; key: string; stage: string; sources: (number | string)[]; team_ids: (number | null)[]; status: string; winner_team_id: number | null; result: string; quads: HandBrainQuad[] }
export interface HandBrainPayload { teams: HandBrainTeam[]; matches: HandBrainMatch[]; engine_options?: { id: number; name: string; version: string }[]; rules: { initial_ms: number; increment_ms: number; minimum_initial_ms: number; minimum_increment_ms: number; threads: number; concurrency: number; book: string } }
export interface HandBrainAnalysis extends EngineAnalysis { role?: 'brain' | 'hand'; brain_move?: string | null; submitted_move?: string; submission_id?: string; submitted_at?: string }
export interface HandBrainSearch { game_id: number; ply: number; engine_id: number; role: 'brain' | 'hand'; side: 'white' | 'black'; root_fen: string; bestmove: string; analysis: HandBrainAnalysis; clocks: Record<string, number> }
export interface HandBrainClock { game_id?: number; active_side?: string; active_engine_id?: number; phase?: 'brain' | 'hand'; brain_move?: string; engine_clocks_ms?: Record<string, number>; running?: boolean; observed_at?: string; sent_at?: string }
