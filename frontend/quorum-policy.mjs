import { createPrivateKey, createPublicKey, randomUUID, sign } from 'node:crypto'
import { mkdir, open, readFile, rename, rmdir, unlink, writeFile } from 'node:fs/promises'
import { dirname, join, resolve } from 'node:path'
import { pathToFileURL } from 'node:url'
import { fingerprint } from './src/quorum.ts'
import { checkPolicySignature, checkPolicySignatures, policyRetryStatement, policyVoteStatement,
  validPolicyRound, verifyPolicyChain, witnessPolicy } from './src/quorum-history.ts'

const requirePolicy = (condition, message) => { if (!condition) throw new Error(message) }
const digestPattern = /^[a-f0-9]{64}$/
const json = async file => JSON.parse(await readFile(file, 'utf8'))
const optional = async file => json(file).catch(error => {
  if (error.code === 'ENOENT') return null
  throw error
})

async function syncDirectory(path) {
  if (process.platform !== 'win32') {
    const directory = await open(path, 'r')
    try { await directory.sync() } finally { await directory.close() }
  }
}

async function persist(target, value) {
  const temporary = target + '.' + randomUUID() + '.tmp'
  try {
    const file = await open(temporary, 'wx', 0o600)
    try {
      await file.writeFile(JSON.stringify(value))
      await file.sync()
    } finally { await file.close() }
    await rename(temporary, target)
    await syncDirectory(dirname(target))
  } finally {
    await unlink(temporary).catch(error => { if (error.code !== 'ENOENT') throw error })
  }
}

export async function withPolicyLock(archive, head, action) {
  const lock = join(archive, '.policy-' + head + '.lock')
  try { await mkdir(lock, { mode: 0o700 }) } catch (error) {
    if (error.code === 'EEXIST') return false
    throw error
  }
  try { await action() } finally { await rmdir(lock) }
  return true
}

export const policyDecisionPath = (archive, head) => join(archive, 'policy-decisions', head)

export async function readPolicyDecision(archive, head, round) {
  const dir = policyDecisionPath(archive, head)
  const record = await readFile(join(dir, round + '.json'), 'utf8').catch(error => {
    if (error.code === 'ENOENT') return null
    throw error
  })
  if (record) {
    // A reader can observe the rename before the writer has synced it.
    // Never serve the signed receipt while its directory entry is volatile.
    await syncDirectory(dir)
    await syncDirectory(dirname(dir))
    await syncDirectory(archive)
  }
  return record
}

const usage = `Usage: quorum-policy.mjs <command> <origin> <current.json> <round> ...
  prepare ... <archive> <proposed.json> [retry.json...]
  commit  ... <archive> <proposed.json> <prepare.json...>
  store   ... <archive> <proposed.json> <commit.json... | peer-stored.json>
  retry   ... <archive>
  assemble ... <proposed.json> <output.json> <stored.json...>`

function signaturesFrom(messages, round, digest) {
  const signatures = Object.create(null)
  for (const record of messages) {
    requirePolicy(record?.round === round && record.digest === digest && typeof record.public_key === 'string'
      && !Object.hasOwn(signatures, record.public_key), 'Policy votes disagree or repeat a verifier.')
    signatures[record.public_key] = record.signature
  }
  return signatures
}

async function retryProposal(files, policy, origin, head, round) {
  // A retry quorum intersects every possible earlier commit quorum in at
  // least one honest signer. That signer promised to leave the old round and
  // reports its last prepare certificate, even if the commits were withheld.
  requirePolicy(files.length >= policy.threshold && files.length <= Object.keys(policy.operators).length,
    'A retry requires signed round changes from the current quorum.')
  const signers = new Set()
  let highest = null
  for (const path of files) {
    const record = await json(path)
    const prepared = record?.prepared
    requirePolicy(record?.round === round && (prepared === null || (prepared && validPolicyRound(prepared.round)
      && prepared.round < round && typeof prepared.digest === 'string' && digestPattern.test(prepared.digest)))
      && !signers.has(record.public_key)
      && await checkPolicySignature(policy, policyRetryStatement(origin, head, round, prepared), record.public_key, record.signature),
    'Invalid or duplicate policy round change.')
    signers.add(record.public_key)
    if (prepared) {
      await checkPolicySignatures(policy, policyVoteStatement('prepare', origin, head, prepared.round, prepared.digest), prepared.signatures)
      requirePolicy(!highest || highest.round !== prepared.round || highest.digest === prepared.digest,
        'The verifiers supplied conflicting prepare certificates.')
      if (!highest || highest.round < prepared.round) highest = prepared
    }
  }
  return highest?.digest ?? null
}

async function main() {
  const [command, origin, currentFile, roundText, ...args] = process.argv.slice(2)
  const round = Number(roundText)
  requirePolicy(origin && new URL(origin).origin === origin && origin.startsWith('https://') && currentFile
    && /^(0|[1-9][0-9]*)$/.test(roundText ?? '') && validPolicyRound(round)
    && (command === 'retry' && round > 0 && args.length === 1
      || command === 'prepare' && (round === 0 ? args.length === 2 : args.length >= 3)
      || ['commit', 'store', 'assemble'].includes(command) && args.length >= 3), usage)
  const chain = await verifyPolicyChain(await json(currentFile), origin)
  const { current, head } = chain
  const next = command === 'retry' ? null : witnessPolicy(await json(command === 'assemble' ? args[0] : args[1]))
  const digest = next && await fingerprint(next)
  requirePolicy(!next || digest !== await fingerprint(current), 'The proposed policy is identical to the current policy.')
  const vote = (phase, at = round, hash = digest) => policyVoteStatement(phase, origin, head, at, hash)
  const files = args.slice(2)
  const size = Object.keys(current.operators).length
  requirePolicy(files.length <= size, 'Too many verifier records.')

  if (command === 'assemble') {
    const records = await Promise.all(files.map(json))
    const commits = records[0]?.commits
    const stored = signaturesFrom(records, round, digest)
    const bundle = { initial: chain.bundle.initial, changes: [...chain.bundle.changes, { policy: next, round, commits, stored }] }
    await verifyPolicyChain(bundle, origin)
    await writeFile(args[1], JSON.stringify(bundle, null, 2) + '\n', { flag: 'wx' })
    return
  }

  const archive = args[0]
  const locked = await withPolicyLock(archive, head, async () => {
    requirePolicy(await readFile(join(archive, 'origin'), 'utf8') === origin
      && await readFile(join(archive, 'witness-policy-head'), 'utf8') === head,
    'The local verifier is not pinned to the current policy for this COPE origin.')
    const key = createPrivateKey(await readFile(join(archive, 'witness-ed25519.pem')))
    const public_key = createPublicKey(key).export({ format: 'der', type: 'spki' }).subarray(-32).toString('hex')
    requirePolicy(Object.values(current.operators).includes(public_key), 'This verifier is not a current operator.')
    const attest = statement => ({ round, public_key, signature: sign(null, Buffer.from(JSON.stringify(statement)), key).toString('hex') })

    if (command === 'store') {
      const records = await Promise.all(files.map(json))
      const commits = records.length === 1 && records[0]?.commits
        ? records[0].commits : signaturesFrom(records, round, digest)
      await checkPolicySignatures(current, vote('commit'), commits)
      const dir = policyDecisionPath(archive, head)
      await mkdir(dir, { recursive: true, mode: 0o700 })
      const target = join(dir, round + '.json')
      const previous = await optional(target)
      requirePolicy(!previous || previous.digest === digest, 'A different decision is already stored for this round.')
      const receipt = previous ?? { ...attest(vote('store')), digest, policy: next, commits }
      // Publish a receipt only after this verifier has a durable copy of an
      // entire commit quorum. Peers can fetch the same record from this archive.
      await persist(target, receipt)
      await syncDirectory(dirname(dir))
      await syncDirectory(archive)
      console.log(JSON.stringify(receipt))
      return
    }

    const journal = join(archive, 'policy-rounds')
    const target = join(journal, head + '.json')
    const state = await optional(target) ?? { round: 0, vote: null, prepared: null, retry: null }
    requirePolicy(round >= state.round, 'This verifier has already moved to a later round.')
    if (round > state.round) {
      state.round = round
      state.vote = null
      state.retry = null
    }

    let record
    if (command === 'retry') {
      requirePolicy(state.retry || (!state.vote && (!state.prepared || state.prepared.round < round)),
        'This verifier has already voted in that round; choose a higher round.')
      record = state.retry ?? { ...attest(policyRetryStatement(origin, head, round, state.prepared)), prepared: state.prepared }
      state.retry = record
    } else if (command === 'prepare') {
      if (round > 0) {
        const carry = await retryProposal(files, current, origin, head, round)
        requirePolicy(!carry || carry === digest, 'This round must carry forward an earlier prepared proposal: ' + carry)
      }
      requirePolicy(!state.vote || state.vote === digest, 'This verifier already voted for a different proposal in this round.')
      requirePolicy(state.prepared?.round !== round || state.prepared.digest === digest,
        'This verifier already committed to a different proposal in this round.')
      state.vote = digest
      record = { ...attest(vote('prepare')), digest }
    } else {
      const signatures = signaturesFrom(await Promise.all(files.map(json)), round, digest)
      await checkPolicySignatures(current, vote('prepare'), signatures)
      requirePolicy(state.prepared?.round !== round || state.prepared.digest === digest,
        'This verifier already committed to a different proposal in this round.')
      // Remember the prepare certificate before a commit signature escapes.
      // A later retry reports this certificate even if the collector hid the
      // commit signatures from everyone else.
      state.prepared = state.prepared?.round === round ? state.prepared : { round, digest, signatures }
      record = { ...attest(vote('commit')), digest }
    }
    await mkdir(journal, { recursive: true, mode: 0o700 })
    await persist(target, state)
    await syncDirectory(archive)
    console.log(JSON.stringify(record))
  })
  requirePolicy(locked, 'The verifier is using its policy archive; try again.')
}

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  try { await main() } catch (error) {
    console.error(error.message)
    process.exitCode = 1
  }
}
