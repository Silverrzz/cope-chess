import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { generateKeyPairSync, sign } from 'node:crypto'
import { mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import test from 'node:test'
import { Group } from '@semaphore-protocol/group'
import { Identity } from '@semaphore-protocol/identity'
import { generateProof } from '@semaphore-protocol/proof'
import { ballotScope, fingerprint, verifyBallot } from './src/quorum.ts'
import { approvalDigest, authorizedBallot, checkpointStatement, extendsHistory, historyDigest, historyVersion, intakeStatement, policyVoteStatement,
  retainedStatement, verifyHistoryResult, verifyPolicyChain, verifyRecordedReceipt, verifyRetentionReceipt, witnessPolicy } from './src/quorum-history.ts'

const origin = 'https://cope.test'
const signers = Array.from({ length: 7 }, (_, i) => {
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
  packet.intake = vote_hashes.map(hash => signAll(intakeStatement(origin, manifest.id, manifest_digest, hash)))
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
  const receipt = { version: 'cope-quorum-recorded-vote/1', origin, manifest: packet.manifest, roster: packet.roster, proof: packet.votes[0],
    vote_hash: history.vote_hashes[0], vote_hashes: history.vote_hashes, checkpoint: history.open }
  await verifyRecordedReceipt(receipt, original, origin)
  await verifyHistoryResult(packet, history, original, origin, receipt)
  const relay = { version: 'cope-quorum-relayed-vote/1', origin, manifest: packet.manifest, roster: packet.roster, proof: packet.votes[0],
    signatures: packet.intake[0], retained: signAll(retainedStatement(origin, packet.manifest.id, history.manifest_digest, history.vote_hashes[0])) }
  assert.equal(await verifyRetentionReceipt(relay, original, origin), history.vote_hashes[0])
  await assert.rejects(verifyRetentionReceipt({ ...relay, retained: relay.signatures }, original, origin), /signature/)
  await assert.rejects(verifyHistoryResult({ ...packet, intake: [] }, history, original, origin), /intake certificates/)
  const omitted = { ...packet, votes: [], intake: [], result: { ...packet.result, counts: [0, 0, 0], total: 0, outcome: 'rejected' } }
  await verifyBallot(omitted)
  await assert.rejects(verifyHistoryResult(omitted, history, original, origin, receipt), /final votes differ/)
  await assert.rejects(verifyHistoryResult(omitted,
    { ...history, vote_hashes: [], open: { ...history.open, size: 0, digest: await historyDigest([]) } },
    original, origin, receipt), /enough valid/)
  await assert.rejects(verifyRecordedReceipt({ ...receipt, proof: { ...receipt.proof, message: '1' } }, original, origin), /does not include/)
})

test('policy changes need the old quorum, preserve history, and are bound to the origin and predecessor', async () => {
  const next = policyFor(4, 6, 1)
  const digest = await fingerprint(next)
  const commit = policyVoteStatement('commit', origin, state.head, 0, digest)
  const store = policyVoteStatement('store', origin, state.head, 0, digest)
  const ready = signAll(policyVoteStatement('ready', origin, state.head, 0, digest), signers.slice(0, 4))
  const changes = (commits, stored) => ({ initial: original, changes: [{ policy: next, round: 0, commits, ready, stored }] })
  await assert.rejects(verifyPolicyChain(changes(signAll(commit, signers.slice(0, 2)), signAll(store)), origin), /current threshold/)
  await assert.rejects(verifyPolicyChain(changes(signAll(commit), signAll(store, [signers[0], signers[1], signers[5]])), origin), /current threshold/)
  await assert.rejects(verifyPolicyChain(changes(signAll(commit), signAll(commit)), origin), /current threshold/)
  const bundle = changes(signAll(commit), signAll(store))
  const { [signers[3].key]: _, ...short } = ready
  await assert.rejects(verifyPolicyChain({ ...bundle, changes: [{ ...bundle.changes[0], ready: short }] }, origin), /threshold/)
  const updated = await verifyPolicyChain(bundle, origin)
  assert.deepEqual(updated.epochs.get(state.head), original)
  assert.deepEqual(updated.current, next)
  await assert.rejects(verifyPolicyChain(bundle, 'https://another.test'), /current threshold/)
  await assert.rejects(verifyPolicyChain({ ...bundle, initial: policyFor(3, 4, 0) }, origin), /current threshold/)
  await assert.rejects(verifyPolicyChain({ ...bundle, changes: [{ ...bundle.changes[0], round: 1 }] }, origin), /current threshold/)
  await assert.rejects(verifyPolicyChain({ ...bundle, changes: [...bundle.changes, ...bundle.changes] }, origin), /separately/)
})

test('the approved roster and exact ballot survive a disjoint verifier rotation', async () => {
  const append = async (chain, policy, approval, old = signers.slice(0, 3), next = old) => {
    const digest = await approvalDigest(await fingerprint(policy), approval)
    const votes = (phase, keys) => signAll(policyVoteStatement(phase, origin, chain.head, 0, digest), keys)
    return verifyPolicyChain({ initial: original, changes: [...chain.bundle.changes,
      { policy, ...(approval && { approval }), round: 0, commits: votes('commit', old), ready: votes('ready', next),
        stored: votes('store', old) }] }, origin)
  }
  const { packet } = await evidence(false)
  const roster = await append(state, original, { roster: packet.manifest.roster })
  packet.manifest.vote_history.epoch = roster.head
  const ballot = { id: packet.manifest.id, manifest: await fingerprint(packet.manifest), roster: packet.manifest.roster }
  await assert.rejects(authorizedBallot(roster, packet.manifest), /not approved/)
  const approved = await append(roster, original, { ballot })
  const nextKeys = signers.slice(4)
  const successor = { threshold: 2, max_faulty: 0,
    operators: Object.fromEntries(nextKeys.map(({ name, key }) => [name, key])) }
  const rotated = await append(approved, successor, undefined, signers.slice(0, 3), nextKeys)
  assert.deepEqual(await authorizedBallot(rotated, packet.manifest), original)
  await assert.rejects(authorizedBallot(rotated, { ...packet.manifest, title: 'Different' }), /not approved/)
  await assert.rejects(append(rotated, successor, { ballot }, nextKeys), /already has a quorum/)
})

test('even a fully signed successor must have a safe quorum of its own', async () => {
  async function approved(policy) {
    const digest = await fingerprint(policy)
    const commits = signAll(policyVoteStatement('commit', origin, state.head, 0, digest), signers.slice(0, 4))
    const stored = signAll(policyVoteStatement('store', origin, state.head, 0, digest), signers.slice(0, 4))
    const ready = signAll(policyVoteStatement('ready', origin, state.head, 0, digest), signers.slice(0, policy.threshold))
    return { initial: original, changes: [{ policy, round: 0, commits, ready, stored }] }
  }
  await assert.rejects(verifyPolicyChain(await approved(policyFor(1, 4, 0)), origin), /honest overlap/)
  const next = policyFor(5, 7, 2)
  assert.deepEqual((await verifyPolicyChain(await approved(next), origin)).current, next)
})

async function withPolicyOperators(testOperators) {
  const local = await mkdtemp(join(tmpdir(), 'cope-policy-test-'))
  try {
    const file = name => join(local, name + '.json')
    for (const [name, policy] of [['current', original], ['A', policyFor(4, 6, 1)], ['B', policyFor(4, 5, 1)],
      ['invalid', policyFor(1, 4, 0)]]) {
      await writeFile(file(name), JSON.stringify(policy))
    }
    const archives = signers.slice(0, 4).map(signer => join(local, signer.name))
    for (const [i, archive] of archives.entries()) {
      await mkdir(archive)
      await writeFile(join(archive, 'origin'), origin)
      await writeFile(join(archive, 'witness-policy-head'), state.head)
      await writeFile(join(archive, 'witness-ed25519.pem'), signers[i].privateKey.export({ type: 'pkcs8', format: 'pem' }))
    }
    const command = (phase, round, ...args) => execFileSync(process.execPath, [
      new URL('./quorum-policy.mjs', import.meta.url).pathname, phase, origin, file('current'), String(round), ...args,
    ], { stdio: ['ignore', 'pipe', 'pipe'] }).toString()
    const run = (phase, round, i, proposal, inputs = []) => command(
      phase, round, archives[i], ...(proposal ? [file(proposal)] : []), ...inputs)
    const exchange = async (phase, round, i, proposal, inputs) => {
      const output = file([phase, round, i, proposal].join('-'))
      await writeFile(output, run(phase, round, i, proposal, inputs))
      return output
    }
    await testOperators({ archives, file, command, run, exchange })
  } finally {
    await rm(local, { force: true, recursive: true })
  }
}

test('a 2–2 split leaves the current policy in place and operators can approve a different proposal next round', async () => {
  await withPolicyOperators(async ({ archives, file, command, run, exchange }) => {
    assert.throws(() => run('prepare', 0, 0, 'invalid'), /honest overlap/)
    const initial = await Promise.all([0, 1, 2, 3].map(i => exchange('prepare', 0, i, i < 2 ? 'A' : 'B')))
    assert.throws(() => run('prepare', 0, 0, 'B'), /already voted/)
    assert.throws(() => run('commit', 0, 0, 'A', initial.slice(0, 2)), /current threshold/)
    assert.throws(() => run('commit', 0, 0, 'A', initial.slice(0, 3)), /disagree/)
    assert.equal(await readFile(join(archives[0], 'witness-policy-head'), 'utf8'), state.head)
    const retry = await Promise.all([0, 1, 2].map(i => exchange('retry', 1, i)))
    assert.throws(() => run('prepare', 1, 3, 'B', [retry[0], retry[0], retry[1]]), /duplicate/)
    assert.throws(() => run('prepare', 0, 0, 'A'), /later round/)
    const prepared = await Promise.all([0, 1, 2].map(i => exchange('prepare', 1, i, 'B', retry)))
    const committed = await Promise.all([0, 1, 2].map(i => exchange('commit', 1, i, 'B', prepared)))
    const ready = await Promise.all([0, 1, 2, 3].map(i => exchange('ready', 1, i, 'B', committed)))
    assert.throws(() => run('store', 1, 0, 'B', ready.slice(0, 3)), /threshold/)
    const first = await exchange('store', 1, 0, 'B', ready)
    const stored = [first, ...await Promise.all([1, 3].map(i => exchange('store', 1, i, 'B', [first])))]
    assert.deepEqual(JSON.parse(await readFile(join(archives[3], 'policy-decisions', state.head, '1.json'), 'utf8')),
      JSON.parse(await readFile(stored[2], 'utf8')))
    assert.throws(() => command('assemble', 1, file('B'), file('approved'), ...stored.slice(0, 2)), /current threshold/)
    command('assemble', 1, file('B'), file('approved'), ...stored)
    const updated = await verifyPolicyChain(JSON.parse(await readFile(file('approved'), 'utf8')), origin)
    assert.deepEqual(updated.current, policyFor(4, 5, 1))
  })
})

test('a retry must preserve an earlier decision even if the collector withheld the commit quorum', async () => {
  await withPolicyOperators(async ({ run, exchange, file }) => {
    const prepared = await Promise.all([0, 1, 2].map(i => exchange('prepare', 0, i, 'A')))
    const hidden = await Promise.all([0, 1, 2].map(i => exchange('commit', 0, i, 'A', prepared)))
    const retry = await Promise.all([0, 2, 3].map(i => exchange('retry', 1, i)))
    assert.throws(() => run('prepare', 1, 3, 'B', retry), /must carry forward/)
    await writeFile(file('stripped'), JSON.stringify({ ...JSON.parse(await readFile(retry[0], 'utf8')), prepared: null }))
    assert.throws(() => run('prepare', 1, 3, 'A', [file('stripped'), ...retry.slice(1)]), /Invalid or duplicate/)
    assert.doesNotThrow(() => run('prepare', 1, 3, 'A', retry))
    assert.throws(() => run('commit', 0, 0, 'A', prepared), /later round/)
    // Once someone finds the original commits, the older decision can still be published.
    const ready = await Promise.all([0, 1, 2, 3].map(i => exchange('ready', 0, i, 'A', hidden)))
    assert.doesNotThrow(() => run('store', 0, 3, 'A', ready))
  })
})
