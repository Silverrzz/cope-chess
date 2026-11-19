import { fingerprint, verifyBallot, verifyBallotContext, verifyVote, type BallotPackage, type Manifest, type Roster, type VoteEvidence } from './quorum.ts'

export const historyVersion = 'cope-quorum-vote-history/1'
export const maxPolicyChanges = 128
export const settlementEnd = (manifest: Manifest) => Date.parse(manifest.closes_at) + 120_000
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
  frozen?: Checkpoint | null
  closed: Checkpoint | null
}
export interface RecordedReceipt {
  version: 'cope-quorum-recorded-vote/1'
  origin: string
  manifest: Manifest
  roster: Roster
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

export interface RetentionReceipt {
  version: 'cope-quorum-relayed-vote/1'
  origin: string
  manifest: Manifest
  roster: Roster
  proof: VoteEvidence['proof']
  signatures: Record<string, string>
  retained: Record<string, string>
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
export const checkpointStatement = (origin: string, id: string, manifestDigest: string, phase: 'open' | 'frozen' | 'closed',
  size: number, digest: string) => ['cope-quorum-history-checkpoint/1', origin, id, manifestDigest, phase, size, digest]
export const intakeStatement = (origin: string, id: string, manifestDigest: string, voteHash: string) =>
  ['cope-quorum-vote-intake/1', origin, id, manifestDigest, voteHash]
export const retainedStatement = (origin: string, id: string, manifestDigest: string, voteHash: string) =>
  ['cope-quorum-vote-retained/1', origin, id, manifestDigest, voteHash]

export async function verifyIntake(packet: BallotPackage, policy: WitnessPolicy, origin: string, hashes: string[]) {
  requireHistory(Array.isArray(packet.intake) && packet.intake.length === hashes.length,
    'The final evidence is missing verifier intake certificates.')
  const manifestDigest = await fingerprint(packet.manifest)
  for (const [index, hash] of hashes.entries()) {
    await checkPolicySignatures(policy, intakeStatement(origin, packet.manifest.id, manifestDigest, hash), packet.intake[index]!)
  }
}

const bytes = (value: string) => Uint8Array.from(value.match(/../g) ?? [], part => Number.parseInt(part, 16))

async function checkSignature(key: string, signature: unknown, message: Uint8Array<ArrayBuffer>) {
  if (typeof signature !== 'string' || !signatureHex.test(signature)) return false
  const publicKey = await crypto.subtle.importKey('raw', bytes(key), { name: 'Ed25519' }, false, ['verify'])
  return crypto.subtle.verify('Ed25519', publicKey, bytes(signature), message)
}

async function checkCheckpoint(origin: string, manifest: Manifest, policy: WitnessPolicy, hashes: string[],
  phase: 'open' | 'frozen' | 'closed', checkpoint: Checkpoint | null) {
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
  const { options, scope } = await verifyBallotContext(receipt.manifest, receipt.roster)
  await verifyVote(receipt.proof, receipt.roster, options, scope)
}

export async function verifyRetentionReceipt(receipt: RetentionReceipt, policy: WitnessPolicy, origin: string) {
  requireHistory(receipt.version === 'cope-quorum-relayed-vote/1' && receipt.origin === origin,
    'The retention receipt belongs to a different COPE site.')
  await checkHistoryPolicy(receipt.manifest, policy)
  const { options, scope } = await verifyBallotContext(receipt.manifest, receipt.roster)
  await verifyVote(receipt.proof, receipt.roster, options, scope)
  const manifestDigest = await fingerprint(receipt.manifest)
  const voteHash = await fingerprint(receipt.proof)
  await checkPolicySignatures(policy, intakeStatement(origin, receipt.manifest.id, manifestDigest, voteHash), receipt.signatures)
  await checkPolicySignatures(policy, retainedStatement(origin, receipt.manifest.id, manifestDigest, voteHash), receipt.retained)
  return voteHash
}

export async function verifySettledVotes(packet: BallotPackage, history: VoteHistory, policy: WitnessPolicy, origin: string) {
  requireHistory(packet.status === 'completed' && Date.now() >= settlementEnd(packet.manifest), 'The ballot has not settled.')
  requireHistory(Array.isArray(packet.votes) && packet.votes.length === history.vote_hashes.length,
    'The final votes differ from the witnessed vote history.')
  const hashes = await Promise.all(packet.votes.map(fingerprint))
  requireHistory(extendsHistory(hashes, history.vote_hashes),
    'The final votes differ from the witnessed vote history.')
  await verifyIntake(packet, policy, origin, hashes)
  await verifyBallot(packet)
}

export async function verifyHistoryResult(packet: BallotPackage, history: VoteHistory, policy: WitnessPolicy, origin: string,
  receipt?: RecordedReceipt) {
  await checkHistoryPacket(packet.manifest, policy, history)
  await verifySettledVotes(packet, history, policy, origin)
  const hashes = history.vote_hashes
  await checkOrderQuorum(origin, packet.manifest, policy, history)
  await checkCheckpoint(origin, packet.manifest, policy, hashes, 'closed', history.closed)
  if (receipt) {
    await verifyRecordedReceipt(receipt, policy, origin)
    requireHistory(await fingerprint(receipt.manifest) === history.manifest_digest
      && extendsHistory(receipt.vote_hashes, hashes), 'The final history omits or conflicts with the recorded receipt.')
  }
}

// Older ballots may already have an open certificate over the complete order.
export async function checkOrderQuorum(origin: string, manifest: Manifest, policy: WitnessPolicy, history: VoteHistory) {
  if (history.frozen) {
    await checkCheckpoint(origin, manifest, policy, history.vote_hashes, 'frozen', history.frozen)
    if (!history.open) return
    // The open receipt still attests only the earlier prefix, never the late suffix.
    await checkCheckpoint(origin, manifest, policy, history.vote_hashes.slice(0, history.open.size), 'open', history.open)
  } else {
    await checkCheckpoint(origin, manifest, policy, history.vote_hashes, 'open', history.open)
  }
}

export async function nextCheckpointPhase(manifest: Manifest, policy: WitnessPolicy, history: VoteHistory, origin: string,
  previous: { manifest_digest: string, vote_hashes: string[], closed: boolean, freeze?: unknown } | null, now: number) {
  await checkHistoryPacket(manifest, policy, history)
  if (previous) {
    requireHistory(previous.manifest_digest === history.manifest_digest && extendsHistory(previous.vote_hashes, history.vote_hashes),
      'The supplied history conflicts with the checkpoint already kept by this witness.')
    requireHistory(!(previous.freeze || previous.closed) || previous.vote_hashes.length === history.vote_hashes.length,
      'The proposed order differs from the one this witness already froze or sealed.')
  }
  if (now < Date.parse(manifest.opens_at)) return null
  if (now < settlementEnd(manifest)) return previous?.closed || previous?.freeze ? null : 'open'
  if (history.frozen || history.open?.size === history.vote_hashes.length) {
    await checkOrderQuorum(origin, manifest, policy, history)
    return 'closed'
  }
  return 'frozen'
}

export interface PolicyChange {
  policy: WitnessPolicy
  approval?: GovernanceApproval
  round: number
  commits: Record<string, string>
  ready: Record<string, string>
  stored: Record<string, string>
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
  roster: string | undefined
  ballots: Map<string, { manifest: string, roster: string, epoch: string }>
  voterRelease: string | undefined
}

export type GovernanceApproval =
  | { roster: string }
  | { ballot: { id: string, manifest: string, roster: string } }
  | { voter_release: string }

export function governanceApproval(value: unknown): GovernanceApproval {
  const entry = value as GovernanceApproval
  const hash = (part: unknown) => typeof part === 'string' && hex.test(part)
  requireHistory(entry && typeof entry === 'object' && Object.keys(entry).length === 1
    && ('roster' in entry && hash(entry.roster) || 'voter_release' in entry && hash(entry.voter_release)
      || 'ballot' in entry && entry.ballot && typeof entry.ballot === 'object'
      && Object.keys(entry.ballot).length === 3 && typeof entry.ballot.id === 'string'
      && /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(entry.ballot.id)
      && hash(entry.ballot.manifest) && hash(entry.ballot.roster)), 'Invalid governance approval.')
  return entry
}

export const approvalDigest = (policyDigest: string, approval?: GovernanceApproval) =>
  approval ? fingerprint(['cope-quorum-governance/1', policyDigest, approval]) : Promise.resolve(policyDigest)

export function checkGovernance(chain: Pick<PolicyChain, 'roster' | 'ballots'>, approval: GovernanceApproval) {
  if ('ballot' in approval) {
    requireHistory(!chain.ballots.has(approval.ballot.id), 'This ballot ID already has a quorum decision.')
    requireHistory(chain.roster === approval.ballot.roster, 'A ballot must use the current approved roster.')
  }
  if ('roster' in approval) requireHistory(chain.roster !== approval.roster, 'The roster is already approved.')
}

export function evidenceManifest(evidence: FinalEvidence | RecordedReceipt | RetentionReceipt) {
  if (evidence.version !== 'cope-quorum-final-evidence/1') return evidence.manifest
  requireHistory(!Object.hasOwn(evidence, 'manifest'), 'A final evidence file must contain its manifest only inside the ballot.')
  return evidence.ballot.manifest
}

export async function authorizedBallot(chain: PolicyChain | null | undefined, manifest: Manifest, expectedId = manifest.id) {
  const entry = chain?.ballots.get(expectedId)
  const policy = chain?.epochs.get(manifest.vote_history?.epoch ?? '')
  requireHistory(manifest.id === expectedId && entry && policy && entry.epoch === manifest.vote_history?.epoch
    && entry.roster === manifest.roster && entry.manifest === await fingerprint(manifest),
  'The pinned verifier quorum has not approved this exact ballot and roster.')
  return policy
}

export function policyChangeStatement(origin: string, head: string, previous: string, next: string) {
  // A decision has the same epoch regardless of which round certifies it.
  return ['cope-quorum-witness-policy-change/2', origin, head, previous, next]
}

export const validPolicyRound = (round: unknown): round is number =>
  typeof round === 'number' && Number.isInteger(round) && round >= 0 && round <= 0x7fffffff

export const policyVoteStatement = (phase: 'prepare' | 'commit' | 'ready' | 'store', origin: string, head: string,
  round: number, digest: string) => [`cope-quorum-policy-${phase}/1`, origin, head, round, digest]

export const policyRetryStatement = (origin: string, head: string, round: number,
  prepared: { round: number, digest: string } | null) =>
  ['cope-quorum-policy-retry/1', origin, head, round, prepared ? [prepared.round, prepared.digest] : null]

export async function checkPolicySignature(policy: WitnessPolicy, statement: unknown[], key: string, signature: unknown) {
  return Object.values(policy.operators).includes(key)
    && checkSignature(key, signature, new TextEncoder().encode(JSON.stringify(statement)))
}

export async function checkPolicySignatures(policy: WitnessPolicy, statement: unknown[], signatures: Record<string, string>) {
  requireHistory(signatures && typeof signatures === 'object' && !Array.isArray(signatures),
    'Invalid policy decision signatures.')
  const signers = Object.keys(signatures)
  requireHistory(signers.length >= policy.threshold && signers.length <= Object.keys(policy.operators).length
    && (await Promise.all(signers.map(key => checkPolicySignature(policy, statement, key, signatures[key])))).every(Boolean),
  'The policy decision needs the current threshold of valid witness signatures.')
}

export async function verifyPolicyChain(value: unknown, origin: string): Promise<PolicyChain> {
  const raw = value as PolicyBundle
  const bundle = raw && typeof raw === 'object' && Object.hasOwn(raw, 'initial')
    ? raw : { initial: witnessPolicy(value), changes: [] }
  requireHistory(Object.keys(bundle).length === 2 && Array.isArray(bundle.changes) && bundle.changes.length <= maxPolicyChanges,
    'Invalid witness policy history.')
  let current = witnessPolicy(bundle.initial)
  let digest = await fingerprint(current)
  let head = await fingerprint(['cope-quorum-witness-policy-anchor/1', origin, digest])
  const heads = [head]
  const epochs = new Map([[head, current]])
  const ballots: PolicyChain['ballots'] = new Map()
  let roster: string | undefined
  let voterRelease: string | undefined
  for (const change of bundle.changes) {
    requireHistory(change && typeof change === 'object' && !Array.isArray(change)
      && Object.keys(change).length === (Object.hasOwn(change, 'approval') ? 6 : 5)
      && validPolicyRound(change.round),
    'Invalid witness policy change.')
    const next = witnessPolicy(change.policy)
    const nextDigest = await fingerprint(next)
    const approval = Object.hasOwn(change, 'approval') ? governanceApproval(change.approval) : undefined
    requireHistory(approval ? nextDigest === digest : nextDigest !== digest,
      'Approve governance separately from any change to verifier membership.')
    if (approval) checkGovernance({ roster, ballots }, approval)
    const proposal = await approvalDigest(nextDigest, approval)
    await checkPolicySignatures(current, policyVoteStatement('commit', origin, head, change.round, proposal), change.commits)
    await checkPolicySignatures(next, policyVoteStatement('ready', origin, head, change.round, proposal), change.ready)
    await checkPolicySignatures(current, policyVoteStatement('store', origin, head, change.round, proposal), change.stored)
    if (approval && 'roster' in approval) roster = approval.roster
    if (approval && 'ballot' in approval) ballots.set(approval.ballot.id, { ...approval.ballot, epoch: head })
    if (approval && 'voter_release' in approval) voterRelease = approval.voter_release
    const statement = policyChangeStatement(origin, head, digest, proposal)
    head = await fingerprint(statement)
    heads.push(head)
    current = next
    digest = nextDigest
    epochs.set(head, current)
  }
  return { bundle, head, heads, current, epochs, roster, ballots, voterRelease }
}
