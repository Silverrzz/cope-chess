import { Group } from '@semaphore-protocol/group'
import { verifyProof, type SemaphoreProof } from '@semaphore-protocol/proof'

export interface Roster {
  members: string[]
  root: string
}

export interface Manifest {
  id: string
  title: string
  rationale: string
  subject: 'motion' | 'poll' | 'engine' | 'member'
  action?: 'include' | 'exclude' | null
  target?: string | null
  rule: 'majority' | 'unanimous_electorate' | 'unanimous_decisive' | 'report_only'
  quorum_percent: number | null
  opens_at: string
  closes_at: string
  options: string[]
  roster?: string
  genesis?: string
}

export interface BallotResult {
  options: string[]
  counts: number[]
  total: number
  electorate: number
  outcome: 'report_only' | 'quorum_not_met' | 'passed' | 'rejected'
}

export interface BallotPackage {
  version: 'cope-quorum-evidence/1'
  manifest: Manifest
  roster: Roster
  votes: SemaphoreProof[]
  status: 'scheduled' | 'open' | 'completed'
  result: BallotResult | null
}

export const ballotOptions = (manifest: Manifest) =>
  manifest.rule === 'report_only' ? manifest.options : ['Support', 'Oppose', 'Abstain']

const field = 21888242871839275222246405745257275088548364400416034343698204186575808495617n
const curveField = 21888242871839275222246405745257275088696311157297823662689037894645226208583n
const decimal = (value: unknown, limit: bigint) =>
  typeof value === 'string' && /^(0|[1-9][0-9]{0,77})$/.test(value) && BigInt(value) < limit
const exactKeys = (record: object, keys: string[]) =>
  Object.keys(record).length === keys.length && keys.every(key => Object.hasOwn(record, key))

function requireValid(valid: unknown, message: string): asserts valid {
  if (!valid) throw new Error(message)
}

function canonical(value: unknown): string {
  if (Array.isArray(value)) return '[' + value.map(canonical).join(',') + ']'
  if (!value || typeof value !== 'object') return JSON.stringify(value)
  const record = value as Record<string, unknown>
  return '{' + Object.keys(record).sort().map(key => JSON.stringify(key) + ':' + canonical(record[key])).join(',') + '}'
}

export async function fingerprint(value: unknown) {
  const result = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(canonical(value)))
  return Array.from(new Uint8Array(result), byte => byte.toString(16).padStart(2, '0')).join('')
}

export async function ballotScope(manifest: Manifest) {
  return BigInt('0x' + await fingerprint(['cope-quorum-ballot/1', manifest])).toString()
}

export async function verifyBallot(packet: BallotPackage) {
  const { manifest, roster, votes, result } = packet
  requireValid(packet.version === 'cope-quorum-evidence/1' && packet.status === 'completed' && result,
    'Results are available after voting closes.')
  const rosterKey = manifest.roster === undefined ? 'genesis' : 'roster'
  requireValid(roster.members.length >= 1 && roster.members.length <= 1024
    && new Set(roster.members).size === roster.members.length
    && roster.members.every(member => member !== '0' && decimal(member, field))
    && await fingerprint(roster) === manifest[rosterKey]
    && new Group(roster.members).root.toString() === roster.root, 'Invalid ballot roster.')
  const options = ballotOptions(manifest)
  const subjects = ['motion', 'poll', 'engine', 'member']
  const rules = ['majority', 'unanimous_electorate', 'unanimous_decisive', 'report_only']
  const manifestKeys = ['id', 'title', 'rationale', 'subject', 'rule',
    'quorum_percent', 'opens_at', 'closes_at', 'options', rosterKey]
  if (Object.hasOwn(manifest, 'action')) manifestKeys.push('action')
  if (Object.hasOwn(manifest, 'target')) manifestKeys.push('target')
  requireValid(exactKeys(manifest, manifestKeys) && /^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/.test(manifest.id)
    && typeof manifest.title === 'string' && manifest.title.length > 0
    && typeof manifest.rationale === 'string' && manifest.rationale.length > 0
    && subjects.includes(manifest.subject) && rules.includes(manifest.rule)
    && (manifest.subject === 'poll' ? manifest.rule === 'report_only' && (manifest.action === undefined || manifest.action === null) && manifest.target == null && manifest.quorum_percent === null
      : manifest.rule !== 'report_only' && manifest.options.length === 0
        && (manifest.subject === 'motion' ? manifest.action === undefined && manifest.target === undefined
          : ['include', 'exclude'].includes(manifest.action ?? '')
            && (manifest.target === undefined || (typeof manifest.target === 'string' && manifest.target.length > 0))))
    && (manifest.quorum_percent === null || (Number.isInteger(manifest.quorum_percent) && manifest.quorum_percent > 0 && manifest.quorum_percent <= 100))
    && Date.parse(manifest.closes_at) > Date.parse(manifest.opens_at)
    && options.length >= 2 && options.length <= 20 && new Set(options).size === options.length
    && options.every(option => typeof option === 'string' && option.trim().length > 0 && Array.from(option).length <= 100),
  'Invalid ballot manifest.')
  requireValid(votes.length <= roster.members.length, 'Too many votes for this roster.')
  const seen = new Set<string>()
  const scope = await ballotScope(manifest)
  const counts = options.map(() => 0)
  for (const proof of votes) {
    requireValid(exactKeys(proof, ['merkleTreeDepth', 'merkleTreeRoot', 'nullifier', 'message', 'scope', 'points'])
      && decimal(proof.nullifier, field) && !seen.has(proof.nullifier)
      && typeof proof.message === 'string' && /^(0|[1-9][0-9]?)$/.test(proof.message)
      && Number(proof.message) < options.length && proof.scope === scope
      && proof.merkleTreeRoot === roster.root && proof.merkleTreeDepth === 10
      && Array.isArray(proof.points) && proof.points.length === 8 && proof.points.every(point => decimal(point, curveField))
      && await verifyProof(proof), 'A counted vote has an invalid or repeated proof.')
    seen.add(proof.nullifier)
    const index = Number(proof.message)
    counts[index] = (counts[index] ?? 0) + 1
  }
  const [support = 0, oppose = 0] = counts
  const electorate = roster.members.length
  const turnout = votes.length
  const quorum = manifest.quorum_percent
  const passes = manifest.rule === 'majority' ? support > oppose :
    manifest.rule === 'unanimous_electorate' ? support === electorate : support > 0 && oppose === 0
  const outcome = manifest.rule === 'report_only' ? 'report_only' :
    quorum !== null && turnout * 100 < quorum * electorate ? 'quorum_not_met' :
      passes ? 'passed' : 'rejected'
  requireValid(canonical({ counts, options, electorate, total: turnout, outcome }) === canonical(result),
    'The published tally differs from the vote proofs.')
}
