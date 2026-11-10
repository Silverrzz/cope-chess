import { shallowRef } from 'vue'
import type { RouteLocationNormalized } from 'vue-router'

import { api } from '@/api/client'

type Id = string | number
type Section = 'ratings' | 'tournaments' | 'engines' | 'versions'
type Catalog = Record<Section, Record<string, string>> & {
  paths: Record<string, { id: number; path: string }>
}

const catalog = shallowRef<Catalog>()
let loading: Promise<void> | undefined

function loadCatalog(): Promise<void> {
  loading ??= api.get<Catalog>('/api/public-urls')
    .then((value) => { catalog.value = value })
    .finally(() => { loading = undefined })
  return loading
}

export function ratingPath(id: Id): string {
  return catalog.value?.ratings[id] ?? `/ratings?rating_list_id=${encodeURIComponent(String(id))}`
}

export function enginePath(id: Id): string {
  return catalog.value?.versions[id] ?? `/engines/${encodeURIComponent(String(id))}`
}

export function engineFamilyPath(id: Id, version: Id): string {
  return catalog.value?.engines[id] ?? enginePath(version)
}

export function tournamentPath(id: Id, game?: Id): string {
  const base = catalog.value?.tournaments[id]
  if (base) return game ? `${base}/games/${encodeURIComponent(String(game))}` : base
  const legacy = `/tournaments/${encodeURIComponent(String(id))}`
  return game ? `${legacy}?game_id=${encodeURIComponent(String(game))}` : legacy
}

function first(value: unknown): string {
  return String((Array.isArray(value) ? value[0] : value) ?? '')
}

export async function resolvePublicUrl(to: RouteLocationNormalized) {
  const section = to.name === 'engine' || to.name === 'engine-version' ? 'engines'
    : to.name === 'tournament' ? 'tournaments'
    : to.name === 'ratings' ? 'ratings' : undefined
  const id = first(to.params.id) || (section === 'ratings' ? first(to.query.rating_list_id) : '')
  const normalized = to.path.replace(/\/+$/, '') || '/'
  const lookup = section === 'tournaments' ? `/tournaments/${id}` : normalized
  const find = () => /^\d+$/.test(id)
    ? catalog.value?.[section === 'engines' ? 'versions' : section!][String(Number(id))]
    : catalog.value?.paths[lookup]?.path
  try {
    if (!catalog.value || (section && id && !find())) await loadCatalog()
  } catch {
    return
  }
  if (!section || !id) {
    if (['/ratings', '/engines', '/tournaments'].includes(normalized) && normalized !== to.path) {
      return { path: normalized, query: to.query, hash: to.hash, replace: true }
    }
    return
  }
  const canonical = find()
  if (!canonical) return

  const query = { ...to.query }
  let path = canonical
  if (section === 'ratings') delete query.rating_list_id
  if (section === 'tournaments') {
    const tab = first(to.params.section) || first(query.tab)
    const game = first(to.params.gameId) || (!to.params.section ? first(query.game_id) : '')
    if (/^[1-9]\d{0,18}$/.test(game)) path += `/games/${game}`
    else if (['standings', 'games', 'settings'].includes(tab)) path += `/${tab}`
    delete query.tab
    delete query.game_id
  }
  if (path !== to.path || Object.keys(query).length !== Object.keys(to.query).length) {
    return { path, query, hash: to.hash, replace: true }
  }
  to.meta.publicResourceId = catalog.value?.paths[canonical]?.id
}
