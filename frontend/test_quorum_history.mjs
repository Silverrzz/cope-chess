import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { generateKeyPairSync, sign } from 'node:crypto'
import { mkdtemp, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import test from 'node:test'
import { Group } from '@semaphore-protocol/group'
import { Identity } from '@semaphore-protocol/identity'
import { generateProof } from '@semaphore-protocol/proof'
import { ballotScope, fingerprint, verifyBallot } from './src/quorum.ts'
import { checkpointStatement, extendsHistory, historyDigest, historyVersion, policyChangeStatement,
  verifyHistoryResult, verifyPolicyChain, verifyRecordedReceipt, witnessPolicy } from './src/quorum-history.ts'

const origin = 'https://cope.test'
const signers = Array.from({ length: 6 }, (_, i) => {
  const { privateKey, publicKey } = generateKeyPairSync('ed25519')
  return { name: 'witness-' + i, key: publicKey.export({ format: 'der', type: 'spki' }).subarray(-32).toString('hex'), privateKey }
})
const policyFor = (threshold, size, max_faulty) => ({
  threshold, operators: Object.fromEntries(signers.slice(0, size).map(({ name, key }) => [name, key])), max_faulty,
})
const original = policyFor(3, 4, 1)
const state = await verifyPolicyChain(original, origin)
const signAll = (statement, keys = signers.slice(0, 3)) => Object.fromEntries(keys.map(({ key, privateKey }) =>
  [key, sign(null, Buffer.from(JSON.stringify(statement)), privateKey).toString('hex')]))

async function evidence(withProof) {
  const identity = new Identity()
  const group = new Group([identity.commitment])
  const roster = { members: [identity.commitment.toString()], root: group.root.toString() }
  const manifest = {
    id: 'd222cd5e-91c1-4ed7-9313-33a200002a11', title: 'Test ballot', rationale: 'test',
    subject: 'motion', rule: 'majority', quorum_percent: null, options: [],
    opens_at: '2020-01-01T00:00:00Z', closes_at: '2020-01-02T00:00:00Z', roster: await fingerprint(roster),
    witnesses: signers.slice(0, 4).map(({ name, key }) => ({ name, public_key: key, report_origin: '' })),
    vote_history: { version: historyVersion, policy: await fingerprint(original), epoch: state.head, threshold: 3 },
  }
  const artifactPath = process.env.COPE_TEST_PROOF_ARTIFACTS
  const votes = withProof ? [await generateProof(identity, group, '0', await ballotScope(manifest), 10, {
    wasm: join(artifactPath, 'semaphore-10.wasm'), zkey: join(artifactPath, 'semaphore-10.zkey'),
  })] : []
  const outcome = withProof ? 'passed' : 'rejected'
  const packet = { version: 'cope-quorum-evidence/1', manifest, roster, votes, status: 'completed',
    result: { options: ['Support', 'Oppose', 'Abstain'], counts: [votes.length, 0, 0], total: votes.length, electorate: 1, outcome } }
  const vote_hashes = await Promise.all(votes.map(fingerprint))
  const manifest_digest = await fingerprint(manifest)
  const size = votes.length
  const digest = await historyDigest(vote_hashes)
  const checkpoint = phase => ({ size, digest, signatures: signAll(checkpointStatement(origin, manifest.id, manifest_digest, phase, size, digest)) })
  return { packet, history: { vote_hashes, manifest_digest, open: checkpoint('open'), closed: checkpoint('closed') } }
}

test('a threshold must enforce honest quorum intersection for the configured faults', () => {
  assert.equal(witnessPolicy(policyFor(4, 6, 1)).threshold, 4)
  assert.throws(() => witnessPolicy(policyFor(3, 6, 0)), /honest overlap/)
  assert.throws(() => witnessPolicy(policyFor(3, 4, 2)), /honest overlap/)
  assert.equal(extendsHistory(['x'], ['x', 'y']), true)
  assert.equal(extendsHistory(['x', 'y'], ['x']), false)
  assert.equal(extendsHistory(['x'], ['y', 'x']), false)
})

test('a complete tally requires independent opening and closing attestations over the exact history', async () => {
  const { packet, history } = await evidence(false)
  await verifyHistoryResult(packet, history, original, origin)
  await assert.rejects(verifyHistoryResult(packet, { ...history, closed: history.open }, original, origin), /enough valid/)
  await assert.rejects(verifyHistoryResult(packet, { ...history, open: null }, original, origin), /checkpoint/)
  await assert.rejects(verifyHistoryResult(packet, history, policyFor(4, 6, 1), origin), /locally pinned/)
  await assert.rejects(verifyHistoryResult(packet, history, original, 'https://another.test'), /enough valid/)
})

test('a real proof can be recorded; removing it and recomputing the tally fails complete verification', {
  skip: !process.env.COPE_TEST_PROOF_ARTIFACTS && 'set COPE_TEST_PROOF_ARTIFACTS to the local depth-10 artifacts directory',
}, async () => {
  const { packet, history } = await evidence(true)
  const receipt = { version: 'cope-quorum-recorded-vote/1', origin, manifest: packet.manifest, proof: packet.votes[0],
    vote_hash: history.vote_hashes[0], vote_hashes: history.vote_hashes, checkpoint: history.open }
  await verifyRecordedReceipt(receipt, original, origin)
  await verifyHistoryResult(packet, history, original, origin, receipt)
  const omitted = { ...packet, votes: [], result: { ...packet.result, counts: [0, 0, 0], total: 0, outcome: 'rejected' } }
  await verifyBallot(omitted)
  await assert.rejects(verifyHistoryResult(omitted, history, original, origin, receipt), /final votes differ/)
  await assert.rejects(verifyHistoryResult(omitted,
    { ...history, vote_hashes: [], open: { ...history.open, size: 0, digest: await historyDigest([]) } },
    original, origin, receipt), /enough valid/)
  await assert.rejects(verifyRecordedReceipt({ ...receipt, proof: { ...receipt.proof, message: '1' } }, original, origin), /does not include/)
})

test('policy changes need the old quorum, preserve history, and are bound to the origin and predecessor', async () => {
  const next = policyFor(4, 6, 1)
  const statement = policyChangeStatement(origin, state.head, await fingerprint(original), await fingerprint(next))
  const changes = signatures => ({ initial: original, changes: [{ policy: next, signatures }] })
  await assert.rejects(verifyPolicyChain(changes(signAll(statement, signers.slice(0, 2))), origin), /current threshold/)
  await assert.rejects(verifyPolicyChain(changes(signAll(statement, [signers[0], signers[1], signers[5]])), origin), /current threshold/)
  const bundle = changes(signAll(statement))
  const updated = await verifyPolicyChain(bundle, origin)
  assert.deepEqual(updated.epochs.get(state.head), original)
  assert.deepEqual(updated.current, next)
  await assert.rejects(verifyPolicyChain(bundle, 'https://another.test'), /current threshold/)
  await assert.rejects(verifyPolicyChain({ ...bundle, changes: [...bundle.changes, ...bundle.changes] }, origin), /current threshold/)
})

test('a verifier will not release signatures for competing policy successors', async () => {
  const local = await mkdtemp(join(tmpdir(), 'cope-policy-test-'))
  try {
    await writeFile(join(local, 'origin'), origin)
    await writeFile(join(local, 'witness-policy-head'), state.head)
    await writeFile(join(local, 'witness-ed25519.pem'), signers[0].privateKey.export({ type: 'pkcs8', format: 'pem' }))
    for (const [file, policy] of [['current', original], ['next', policyFor(4, 6, 1)], ['fork', policyFor(4, 5, 1)]]) {
      await writeFile(join(local, file+'.json'), JSON.stringify(policy))
    }
    const approve = file => execFileSync(process.execPath, [
      new URL('./quorum-policy.mjs', import.meta.url).pathname, 'approve', origin, join(local, 'current.json'),
      join(local, file+'.json'), local,
    ], { stdio: ['ignore', 'pipe', 'pipe'] }).toString()
    const signature = approve('next')
    assert.equal(approve('next'), signature)
    assert.throws(() => approve('fork'), /already approved a different successor/)
  } finally {
    await rm(local, { force: true, recursive: true })
  }
})
