import { api } from '@/api/client'
import type { Variant } from '@/components/chess/chess'
import type { FormChoice } from '@/types/forms'

export interface EmbeddedGame {
  title: string
  tournament: string
  url: string
  variant: Variant
  fen: string
  result: string | null
  moves: string[]
  san: string[]
}

export interface EmbeddedRatings {
  title: string
  url: string
  rows: { name: string; version: string; elo: number; error_margin: number | null }[]
}

export type ResourceKind = 'game' | 'rating_list'
export function resourceChoices(kind: ResourceKind | 'tournament', query: string, tournament?: string, signal?: AbortSignal) {
  return api.get<{ items: FormChoice[] }>('/api/forms/resources/search', { query: { kind, q: query, tournament }, signal })
}

export function gameResource(value: string, signal?: AbortSignal) {
  if (!/^[1-9][0-9]{0,14}\/[1-9][0-9]{0,14}$/.test(value)) throw new Error('Choose a valid tournament game.')
  return api.get<EmbeddedGame>(`/api/forms/resources/game/${value}`, { signal })
}

export function ratingsResource(value: string, signal?: AbortSignal) {
  if (!/^[1-9][0-9]{0,14}$/.test(value)) throw new Error('Choose a valid rating list.')
  return api.get<EmbeddedRatings>(`/api/forms/resources/rating-list/${value}`, { signal })
}
