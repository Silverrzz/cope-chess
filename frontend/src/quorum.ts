import { Group } from '@semaphore-protocol/group'
import { verifyProof, type SemaphoreProof } from '@semaphore-protocol/proof'

export interface Genesis {
  version: 'cope-quorum/1'
  community: string
  enrollment_role: string
  members: string[]
  root: string
  witnesses: string[]
  threshold: number
}

export interface Manifest {
  id: string
  title: string
  rationale: string
  subject: 'engine' | 'member' | 'poll'
  action: 'include' | 'exclude' | null
  target: string | null
  rule: 'majority' | 'unanimous_electorate' | 'unanimous_decisive' | 'report_only'
  quorum_percent: number | null
  opens_at: string
  closes_at: string
  options: string[]
  genesis: string
}

export interface Snapshot {
  version: 'cope-quorum-checkpoint/1'
  manifest: Manifest
  votes: SemaphoreProof[]
  final: boolean
}

export interface Checkpoint {
  size: number
  final: boolean
  digest: string
  signatures: Record<string, string>
}

export interface Founding {
  genesis: Genesis
  signatures: Record<string, string>
}

export interface BallotPackage extends Founding {
  candidate: Snapshot
  checkpoints: Checkpoint[]
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

const bytes = (base64: string) => Uint8Array.from(atob(base64), character => character.charCodeAt(0))
export function digestBytes(hash: string) {
  if (!/^[a-f0-9]{64}$/.test(hash)) throw new Error('Invalid SHA-256 fingerprint.')
  return Uint8Array.from(hash.match(/../g)!, part => Number.parseInt(part, 16))
}

export async function validateGenesis(genesis: Genesis, trusted: string) {
  requireValid(await fingerprint(genesis) === trusted, 'This founding document differs from your trusted fingerprint.')
  requireValid(exactKeys(genesis, ['version', 'community', 'enrollment_role', 'members', 'root', 'witnesses', 'threshold'])
    && genesis.version === 'cope-quorum/1' && genesis.threshold === 3
    && /^[0-9]{17,20}$/.test(genesis.enrollment_role)
    && genesis.witnesses.length === 4 && new Set(genesis.witnesses).size === 4
    && genesis.witnesses.every(key => bytes(key).length === 32 && btoa(String.fromCharCode(...bytes(key))) === key), 'Invalid witness policy.')
  requireValid(genesis.members.length >= 3 && genesis.members.length <= 1024
    && new Set(genesis.members).size === genesis.members.length
    && genesis.members.every(member => /^[1-9][0-9]{0,76}$/.test(member) && BigInt(member) < field)
    && new Group(genesis.members).root.toString() === genesis.root, 'Invalid founding roster.')
}

async function signatureCount(genesis: Genesis, signatures: Record<string, string>, digest: string) {
  const message = digestBytes(digest)
  let count = 0
  for (const [key, signature] of Object.entries(signatures)) {
    requireValid(genesis.witnesses.includes(key), 'A signature came from an unknown witness.')
    const publicKey = await crypto.subtle.importKey('raw', bytes(key), 'Ed25519', false, ['verify'])
    requireValid(await crypto.subtle.verify('Ed25519', publicKey, bytes(signature), message), 'Invalid witness signature.')
    count++
  }
  return count
}

export async function verifyFounding(founding: Founding, trusted: string) {
  await validateGenesis(founding.genesis, trusted)
  const count = await signatureCount(founding.genesis, founding.signatures, trusted)
  requireValid(count >= founding.genesis.threshold, 'The founding document is still awaiting witness signatures.')
}

export async function verifySnapshot(genesis: Genesis, snapshot: Snapshot, trusted: string) {
  const manifest = snapshot.manifest
  const options = ballotOptions(manifest)
  const roles = ['engine', 'member', 'poll']
  const rules = ['majority', 'unanimous_electorate', 'unanimous_decisive', 'report_only']
  requireValid(exactKeys(snapshot, ['version', 'manifest', 'votes', 'final'])
    && exactKeys(manifest, ['id', 'title', 'rationale', 'subject', 'action', 'target', 'rule', 'quorum_percent', 'opens_at', 'closes_at', 'options', 'genesis'])
    && snapshot.version === 'cope-quorum-checkpoint/1' && typeof snapshot.final === 'boolean'
    && manifest.genesis === trusted && /^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/.test(manifest.id)
    && typeof manifest.title === 'string' && manifest.title.length > 0
    && typeof manifest.rationale === 'string' && manifest.rationale.length > 0
    && roles.includes(manifest.subject) && rules.includes(manifest.rule)
    && (manifest.subject === 'poll' ? manifest.rule === 'report_only' && manifest.action === null && manifest.target === null && manifest.quorum_percent === null
      : manifest.rule !== 'report_only' && ['include', 'exclude'].includes(manifest.action ?? '') && typeof manifest.target === 'string'
        && manifest.target.length > 0 && manifest.options.length === 0)
    && (manifest.quorum_percent === null || (Number.isInteger(manifest.quorum_percent) && manifest.quorum_percent > 0 && manifest.quorum_percent <= 100))
    && Date.parse(manifest.closes_at) > Date.parse(manifest.opens_at)
    && options.length >= 2 && options.length <= 20 && new Set(options).size === options.length
    && options.every(option => typeof option === 'string' && option.trim().length > 0 && Array.from(option).length <= 100),
  'Invalid ballot manifest.')
  requireValid(snapshot.votes.length <= genesis.members.length, 'Too many votes for this roster.')
  const seen = new Set<string>()
  const scope = await ballotScope(manifest)
  const counts = options.map(() => 0)
  for (const proof of snapshot.votes) {
    requireValid(exactKeys(proof, ['merkleTreeDepth', 'merkleTreeRoot', 'nullifier', 'message', 'scope', 'points'])
      && decimal(proof.nullifier, field) && !seen.has(proof.nullifier)
      && typeof proof.message === 'string' && /^(0|[1-9][0-9]?)$/.test(proof.message)
      && Number(proof.message) < options.length && proof.scope === scope
      && proof.merkleTreeRoot === genesis.root && proof.merkleTreeDepth === 10
      && Array.isArray(proof.points) && proof.points.length === 8 && proof.points.every(point => decimal(point, curveField))
      && await verifyProof(proof), 'A counted vote has an invalid or repeated proof.')
    seen.add(proof.nullifier)
    const index = Number(proof.message)
    counts[index] = (counts[index] ?? 0) + 1
  }
  const [support = 0, oppose = 0] = counts
  const electorate = genesis.members.length
  const turnout = snapshot.votes.length
  const quorum = manifest.quorum_percent
  const passes = manifest.rule === 'majority' ? support > oppose :
    manifest.rule === 'unanimous_electorate' ? support === electorate : support > 0 && oppose === 0
  const outcome = manifest.rule === 'report_only' ? 'Report only' :
    quorum !== null && turnout * 100 < quorum * electorate ? 'Quorum not met' :
      passes ? 'Motion passed' : 'Motion rejected'
  return { counts, options, electorate, turnout, outcome }
}

export async function certifiedSnapshot(packet: BallotPackage, checkpoint: Checkpoint, trusted: string) {
  await verifyFounding(packet, trusted)
  requireValid(Number.isInteger(checkpoint.size) && checkpoint.size >= 0 && checkpoint.size <= packet.candidate.votes.length
    && (!checkpoint.final || (packet.candidate.final && checkpoint.size === packet.candidate.votes.length)),
  'The checkpoint does not cover its stated vote prefix.')
  const snapshot = { ...packet.candidate, votes: packet.candidate.votes.slice(0, checkpoint.size), final: checkpoint.final }
  requireValid(await fingerprint(snapshot) === checkpoint.digest, 'The checkpoint does not match the public ballot data.')
  requireValid(await signatureCount(packet.genesis, checkpoint.signatures, checkpoint.digest) >= packet.genesis.threshold,
    'This checkpoint is awaiting witness signatures.')
  return verifySnapshot(packet.genesis, snapshot, trusted)
}
