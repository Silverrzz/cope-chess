import { fingerprint, verifyBallot, type BallotPackage, type Manifest, type VoteEvidence } from './quorum.ts'

export const historyVersion = 'cope-quorum-vote-history/1'
const hex = /^[a-f0-9]{64}$/
const signatureHex = /^[a-f0-9]{128}$/
const operatorId = /^[a-z0-9][a-z0-9-]{0,31}$/

export interface WitnessPolicy {
  threshold: number
  operators: Record<string, string>
  max_faulty?: number
}
export interface Checkpoint {
  size: number
  digest: string
  signatures: Record<string, string>
}
export interface VoteHistory {
  manifest_digest: string
  vote_hashes: string[]
  open: Checkpoint | null
  closed: Checkpoint | null
}
export interface RecordedReceipt {
  version: 'cope-quorum-recorded-vote/1'
  origin: string
  manifest: Manifest
  vote_hash: string
  proof: VoteEvidence['proof']
  vote_hashes: string[]
  checkpoint: Checkpoint
}
export interface FinalEvidence {
  version: 'cope-quorum-final-evidence/1'
  origin: string
  ballot: BallotPackage
  history: VoteHistory
}

function requireHistory(condition: unknown, message: string): asserts condition {
  if (!condition) throw new Error(message)
}

export function witnessPolicy(value: unknown): WitnessPolicy {
  const policy = value as WitnessPolicy
  requireHistory(policy && typeof policy === 'object' && !Array.isArray(policy)
    && Object.keys(policy).every(key => ['threshold', 'operators', 'max_faulty'].includes(key))
    && policy.operators && typeof policy.operators === 'object' && !Array.isArray(policy.operators),
  'Invalid pinned witness policy.')
  const keys = Object.entries(policy.operators)
  const size = keys.length
  const faulty = policy.max_faulty === undefined ? 2 * policy.threshold - size - 1 : policy.max_faulty
  requireHistory(size >= 1 && size <= 64 && keys.every(([operator, key]) => operatorId.test(operator)
    && typeof key === 'string' && hex.test(key)) && new Set(keys.map(([, key]) => key)).size === size
    && Number.isInteger(policy.threshold) && policy.threshold <= size
    && Number.isInteger(faulty) && faulty >= 0 && 2 * policy.threshold - size > faulty,
    'The witness threshold must force an honest overlap between any two quorums.')
  return policy
}

export function matchesWitnessPolicy(manifest: Manifest, policy: WitnessPolicy) {
  const keys = Object.values(policy.operators)
  const assigned = manifest.witnesses
  return Array.isArray(assigned) && assigned.length === keys.length
    && keys.every(key => assigned.some(item => item?.public_key === key))
}

export async function checkHistoryPolicy(manifest: Manifest, policy: WitnessPolicy) {
  requireHistory(manifest.vote_history?.version === historyVersion
    && manifest.vote_history.threshold === policy.threshold && manifest.vote_history.policy === await fingerprint(policy) && matchesWitnessPolicy(manifest, policy),
  'This ballot does not use the locally pinned witness policy.')
}

export function checkHashes(votes: unknown, limit = 1024): asserts votes is string[] {
  requireHistory(Array.isArray(votes) && votes.length <= limit && new Set(votes).size === votes.length
    && votes.every(hash => typeof hash === 'string' && hex.test(hash)), 'Invalid vote history.')
}

export function extendsHistory(previous: string[], next: string[]) {
  return next.length >= previous.length && previous.every((hash, position) => hash === next[position])
}

export const historyDigest = (hashes: string[]) => fingerprint([historyVersion, hashes])
export const checkpointStatement = (origin: string, id: string, manifestDigest: string, phase: 'open' | 'closed',
  size: number, digest: string) => ['cope-quorum-history-checkpoint/1', origin, id, manifestDigest, phase, size, digest]

const bytes = (value: string) => Uint8Array.from(value.match(/../g) ?? [], part => Number.parseInt(part, 16))

async function checkSignature(key: string, signature: unknown, message: Uint8Array<ArrayBuffer>) {
  if (typeof signature !== 'string' || !signatureHex.test(signature)) return false
  const publicKey = await crypto.subtle.importKey('raw', bytes(key), { name: 'Ed25519' }, false, ['verify'])
  return crypto.subtle.verify('Ed25519', publicKey, bytes(signature), message)
}

async function checkCheckpoint(origin: string, manifest: Manifest, policy: WitnessPolicy, hashes: string[],
  phase: 'open' | 'closed', checkpoint: Checkpoint | null) {
  requireHistory(checkpoint && checkpoint.size === hashes.length && checkpoint.digest === await historyDigest(hashes)
    && checkpoint.signatures && typeof checkpoint.signatures === 'object', 'The witness checkpoint is missing or covers different votes.')
  const encoded = new TextEncoder().encode(JSON.stringify(checkpointStatement(
    origin, manifest.id, await fingerprint(manifest), phase, checkpoint.size, checkpoint.digest)))
  const valid = await Promise.all(Object.values(policy.operators).map(key =>
    checkSignature(key, checkpoint.signatures[key], encoded)))
  requireHistory(valid.filter(Boolean).length >= policy.threshold, 'The checkpoint does not have enough valid pinned witness signatures.')
}

export async function checkHistoryPacket(manifest: Manifest, policy: WitnessPolicy, history: VoteHistory) {
  await checkHistoryPolicy(manifest, policy)
  requireHistory(history && history.manifest_digest === await fingerprint(manifest), 'The vote history belongs to a different ballot manifest.')
  checkHashes(history.vote_hashes)
}

export async function verifyRecordedReceipt(receipt: RecordedReceipt, policy: WitnessPolicy, origin: string) {
  requireHistory(receipt.version === 'cope-quorum-recorded-vote/1' && receipt.origin === origin, 'The receipt belongs to a different COPE site.')
  await checkHistoryPolicy(receipt.manifest, policy)
  checkHashes(receipt.vote_hashes)
  requireHistory(receipt.proof && await fingerprint(receipt.proof) === receipt.vote_hash
    && receipt.vote_hashes.includes(receipt.vote_hash), 'This receipt does not include the vote.')
  await checkCheckpoint(origin, receipt.manifest, policy, receipt.vote_hashes, 'open', receipt.checkpoint)
}

export async function verifyHistoryResult(packet: BallotPackage, history: VoteHistory, policy: WitnessPolicy, origin: string,
  receipt?: RecordedReceipt) {
  await checkHistoryPacket(packet.manifest, policy, history)
  requireHistory(packet.status === 'completed' && Date.now() >= Date.parse(packet.manifest.closes_at), 'The ballot has not reached its scheduled close.')
  requireHistory(Array.isArray(packet.votes) && packet.votes.length === history.vote_hashes.length,
    'The final votes differ from the witnessed vote history.')
  const hashes = await Promise.all(packet.votes.map(fingerprint))
  requireHistory(extendsHistory(hashes, history.vote_hashes),
    'The final votes differ from the witnessed vote history.')
  await checkCheckpoint(origin, packet.manifest, policy, hashes, 'open', history.open)
  await checkCheckpoint(origin, packet.manifest, policy, hashes, 'closed', history.closed)
  if (receipt) {
    await verifyRecordedReceipt(receipt, policy, origin)
    requireHistory(await fingerprint(receipt.manifest) === history.manifest_digest
      && extendsHistory(receipt.vote_hashes, hashes), 'The final history omits or conflicts with the recorded receipt.')
  }
  await verifyBallot(packet)
}

// A closing witness may catch up after the deadline only from a checkpoint that already
// carries an open quorum. Its own previously signed history must still be a prefix.
export async function checkOpenQuorum(origin: string, manifest: Manifest, policy: WitnessPolicy, history: VoteHistory) {
  await checkHistoryPacket(manifest, policy, history)
  await checkCheckpoint(origin, manifest, policy, history.vote_hashes, 'open', history.open)
}

export interface PolicyChange {
  policy: WitnessPolicy
  signatures: Record<string, string>
}

export interface PolicyBundle {
  initial: WitnessPolicy
  changes: PolicyChange[]
}

export interface PolicyChain {
  bundle: PolicyBundle
  head: string
  heads: string[]
  current: WitnessPolicy
  epochs: Map<string, WitnessPolicy>
}

export function policyChangeStatement(origin: string, head: string, previous: string, next: string) {
  return ['cope-quorum-witness-policy-change/1', origin, head, previous, next]
}

export async function verifyPolicyChain(value: unknown, origin: string): Promise<PolicyChain> {
  const raw = value as PolicyBundle
  const bundle = raw && typeof raw === 'object' && Object.hasOwn(raw, 'initial')
    ? raw : { initial: witnessPolicy(value), changes: [] }
  requireHistory(Object.keys(bundle).length === 2 && Array.isArray(bundle.changes) && bundle.changes.length <= 128,
    'Invalid witness policy history.')
  let current = witnessPolicy(bundle.initial)
  let digest = await fingerprint(current)
  let head = await fingerprint(['cope-quorum-witness-policy-anchor/1', origin, digest])
  const heads = [head]
  const epochs = new Map([[head, current]])
  for (const change of bundle.changes) {
    requireHistory(change && typeof change === 'object' && Object.keys(change).length === 2
      && change.signatures && typeof change.signatures === 'object' && !Array.isArray(change.signatures),
    'Invalid witness policy change.')
    const next = witnessPolicy(change.policy)
    const nextDigest = await fingerprint(next)
    const statement = policyChangeStatement(origin, head, digest, nextDigest)
    const message = new TextEncoder().encode(JSON.stringify(statement))
    const keys = Object.values(current.operators)
    const signers = Object.keys(change.signatures)
    requireHistory(signers.length >= current.threshold && signers.every(key => keys.includes(key))
      && (await Promise.all(signers.map(key => checkSignature(key, change.signatures[key], message)))).every(Boolean),
    'The policy change needs the current threshold of valid witness approvals.')
    head = await fingerprint(statement)
    heads.push(head)
    current = next
    digest = nextDigest
    epochs.set(head, current)
  }
  return { bundle, head, heads, current, epochs }
}
