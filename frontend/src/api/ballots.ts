import api from '@/api/client'

export const ballotApi = '/api/forms/secret-ballots'
export const ballotTool = ballotApi + '/offline-tool'

export type BallotStatus = 'draft' | 'open' | 'tallying' | 'sealed' | 'published'

export const statusLabels: Record<BallotStatus, string> = {
  draft: 'Draft',
  open: 'Open for voting',
  tallying: 'Awaiting tally',
  sealed: 'Closed without results',
  published: 'Results published',
}

export interface Question {
  title: string
  options: string[]
}

type GroupNumber = string
export type EqualityProof = [GroupNumber, GroupNumber]
export type SelectionProof = [GroupNumber, GroupNumber, GroupNumber, GroupNumber]
export type Cell = [GroupNumber, GroupNumber, SelectionProof]

export interface Trustee {
  public: string
  proof: EqualityProof
}

export interface BallotSettings {
  title: string
  description: string
  questions: Question[]
  roster: string[]
  trustees: Trustee[]
  minimum: number
}

export interface Summary {
  slug: string
  title: string
  description: string
  question_count: number
  ballot_count: number
  opened_at: string | null
  closed_at: string | null
  status: BallotStatus
}

export interface Detail extends Summary {
  questions: Question[]
  minimum: number
  manifest_digest: string
  board_digest: string | null
  results: number[][] | null
}

export interface AdminSummary extends Summary {
  listed: boolean
}

export interface AdminDetail extends AdminSummary {
  settings: BallotSettings
  revision: number
  manifest_digest: string | null
  board_digest: string | null
  trustees_submitted: string[]
}

export interface Manifest extends BallotSettings {
  protocol: 'cope-secret-ballot-v1'
  slug: string
  key: string
  receipt_key: string
}

export interface VoterProof {
  type: 'cope-voter-proof-v1'
  slug: string
  public: string
  private: string
}

export interface Envelope {
  manifest: string
  credential: string
  questions: { cells: Cell[]; proof: EqualityProof }[]
  signature: string
}

export interface Receipt {
  manifest: string
  fingerprint: string
  signature: string
}

export type Openings = [number, string[]][]
export interface BallotOpening extends Pick<Receipt, 'manifest' | 'fingerprint'> {
  openings: Openings
}

export interface Page<T> {
  items: T[]
  more: boolean
}

export function publicFormApi(slug: string): string {
  return ballotApi + '/forms/' + encodeURIComponent(slug)
}

export function adminFormApi(slug: string): string {
  return ballotApi + '/admin/' + encodeURIComponent(slug)
}

export function ballotManifest(slug: string): Promise<Manifest> {
  return api.get(publicFormApi(slug) + '/manifest')
}

export async function submitBallot(slug: string, envelope: Envelope, privatePlatform: boolean): Promise<Receipt> {
  const response = await fetch(publicFormApi(slug) + '/cast', {
    method: 'POST',
    credentials: privatePlatform ? 'same-origin' : 'omit',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(envelope),
  })
  const result = await response.json()
  if (!response.ok) {
    throw new Error(typeof result.detail === 'string' ? result.detail : result.detail?.message ?? 'Could not accept this ballot.')
  }
  return result
}

export function downloadJson(filename: string, value: unknown): void {
  const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], { type: 'application/json' }))
  const link = document.createElement('a')
  link.download = filename + '.json'
  link.href = url
  link.click()
  window.setTimeout(() => URL.revokeObjectURL(url), 1000)
}

export async function readJson(input: HTMLInputElement, maxBytes = 128 * 1024): Promise<unknown> {
  const file = input.files?.[0]
  input.value = ''
  if (!file) throw new Error('Choose a JSON file.')
  if (file.size > maxBytes) throw new Error('This file is too large.')
  try {
    return JSON.parse(await file.text())
  } catch {
    throw new Error('This file does not contain valid JSON.')
  }
}

export function errorText(error: unknown): string {
  return error instanceof Error ? error.message : 'The request could not be completed.'
}
