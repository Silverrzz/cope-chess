import type { EngineRecord } from '@/components/public/types'

export type VersionTrack = 'releases' | 'strength' | 'builds'

const numericPrefix = /^v\.?\s*(?=\d)/i
const numberedRelease = /^\d+(?:\.\d+)*(?:[a-z])?$/i
const buildLabel = /(?:^|[\s._-])(?:rc\d*|alpha\d*|beta\d*|dev\d*|snapshot|nightly|pre(?:release)?\d*|unreleased|placeholder|temporary|temp|modified|experimental|test\d*|obfix|cope\d*)(?=$|[\s._-])/i
const prereleaseSuffix = /\d(?:rc|alpha|beta|pre)\d*$/i
const randomSuffix = /-R(100|[0-9]{1,2})$/i
const collator = new Intl.Collator('en', { numeric: true, sensitivity: 'base' })

export const versionTrackLabels: Record<VersionTrack, string> = {
  releases: 'Releases', strength: 'Strength variants', builds: 'Experimental builds',
}

export function versionTrack(version: EngineRecord): VersionTrack {
  const tags = version.tags?.map((tag) => tag.name.trim().toLowerCase()) ?? []
  if (tags.includes('strength variant')) return 'strength'
  if (tags.some((tag) => ['experimental', 'modified', 'temporary'].includes(tag))) return 'builds'
  if (tags.includes('release')) return 'releases'
  const label = version.version?.trim() ?? ''
  if (randomSuffix.test(label)) return 'strength'
  if (!label || ['unversioned', 'development'].includes(label.toLowerCase()) || buildLabel.test(label) || prereleaseSuffix.test(label)) return 'builds'
  return version.source_kind === 'release' || numberedRelease.test(label.replace(numericPrefix, '')) ? 'releases' : 'builds'
}

function versionDate(version: EngineRecord): number {
  return Date.parse(version.source_date || version.created_at || '') || 0
}

export function orderVersionHistory<T extends EngineRecord>(versions: T[], track: VersionTrack): T[] {
  const label = (version: T) => (version.version?.trim() ?? '').replace(numericPrefix, '')
  const numbered = track === 'releases' && versions.every((version) => numberedRelease.test(label(version)))
  return [...versions].sort((left, right) => {
    if (numbered) return collator.compare(label(left), label(right)) || String(left.id).localeCompare(String(right.id))
    if (track === 'strength' && (randomSuffix.test(label(left)) || randomSuffix.test(label(right)))) {
      if (!randomSuffix.test(label(left))) return 1
      if (!randomSuffix.test(label(right))) return -1
      return collator.compare(label(left).replace(randomSuffix, ''), label(right).replace(randomSuffix, ''))
        || Number(label(right).match(randomSuffix)?.[1]) - Number(label(left).match(randomSuffix)?.[1])
        || String(left.id).localeCompare(String(right.id))
    }
    return versionDate(left) - versionDate(right) || collator.compare(label(left), label(right))
      || String(left.id).localeCompare(String(right.id))
  })
}
