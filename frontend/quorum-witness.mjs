import { createPrivateKey, createPublicKey, sign } from 'node:crypto'
import { closeSync, fsyncSync, openSync, readFileSync, renameSync, unlinkSync, writeFileSync } from 'node:fs'
import { certifiedSnapshot, digestBytes, fingerprint, validateGenesis, verifyFounding, verifySnapshot } from './src/quorum.ts'

const submit = process.argv.includes('--submit')
const [command, source, trusted, ...args] = process.argv.slice(2).filter(argument => argument !== '--submit')

function requireValid(condition, message) {
  if (!condition) throw new Error(message)
}

async function readEvidence(source) {
  if (source.startsWith('https://')) {
    const response = await fetch(source, { redirect: 'error', signal: AbortSignal.timeout(15000) })
    requireValid(response.ok, 'Could not fetch the public evidence.')
    return response.json()
  }
  return JSON.parse(readFileSync(source, 'utf8'))
}

function durableWrite(filename, data, flags = 'wx') {
  const file = openSync(filename, flags, 0o600)
  try {
    writeFileSync(file, JSON.stringify(data))
    fsyncSync(file)
  } finally {
    closeSync(file)
  }
}

async function signEvidence(packet, keyPath, statePath, approved, founding) {
  requireValid(keyPath && statePath, 'Supply the independent Ed25519 PEM key and durable witness state file.')
  const key = createPrivateKey(readFileSync(keyPath))
  requireValid(key.asymmetricKeyType === 'ed25519', 'The witness key must be Ed25519.')
  const witness = createPublicKey(key).export({ format: 'der', type: 'spki' }).subarray(-32).toString('base64')
  requireValid(packet.genesis.witnesses.includes(witness), 'The trusted founding document does not name this key.')
  const lock = statePath + '.lock'
  durableWrite(lock, { pid: process.pid })
  try {
    let state
    if (founding) {
      try { durableWrite(statePath, { trusted, witness, ballots: {} }) }
      catch (error) { if (error.code !== 'EEXIST') throw error }
    }
    state = JSON.parse(readFileSync(statePath, 'utf8'))
    requireValid(state.trusted === trusted && state.witness === witness, 'The witness state pins a different founding document or key.')
    if (founding) return { witness, digest: trusted, signature: sign(null, digestBytes(trusted), key).toString('base64') }

    const candidate = packet.candidate
    const { manifest, final } = candidate
    const previous = state.ballots[manifest.id]
    const manifestDigest = await fingerprint(manifest)
    const votes = await Promise.all(candidate.votes.map(fingerprint))
    if (previous) {
      requireValid(previous.manifest === manifestDigest, 'This ballot conflicts with the previously approved manifest.')
      requireValid(previous.votes.length <= votes.length && previous.votes.every((vote, index) => vote === votes[index]),
        'The checkpoint removes, changes, or reorders previously signed votes.')
      requireValid(!previous.final || (final && votes.length === previous.votes.length), 'The previously signed final checkpoint is immutable.')
    } else {
      requireValid(approved === manifestDigest, 'Approve the opening manifest by supplying the manifest fingerprint as the last argument.')
      requireValid(!final && votes.length === 0, 'First approve the empty opening checkpoint before the ballot closes.')
    }
    requireValid(final === (Date.now() >= Date.parse(manifest.closes_at)), 'The candidate closing status disagrees with the witness clock.')
    await verifySnapshot(packet.genesis, candidate, trusted)
    const checkpointDigest = await fingerprint(candidate)
    const temporary = statePath + '.next'
    state.ballots[manifest.id] = { manifest: manifestDigest, votes, final }
    durableWrite(temporary, state, 'w')
    renameSync(temporary, statePath)
    return { witness, digest: checkpointDigest, size: votes.length, final,
      signature: sign(null, digestBytes(checkpointDigest), key).toString('base64') }
  } finally {
    unlinkSync(lock)
  }
}

async function outputSignature(endorsement) {
  if (submit) {
    requireValid(source.startsWith('https://'), 'Submission requires an HTTPS evidence URL.')
    const url = new URL(source)
    requireValid(url.origin === submissionOrigin, 'The submission server differs from the approved founding origin.')
    url.pathname = url.pathname.replace(/\/$/, '') + '/endorse'
    const response = await fetch(url, {
      method: 'POST', redirect: 'error', signal: AbortSignal.timeout(15000),
      headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(endorsement),
    })
    requireValid(response.ok, 'Cope rejected the witness signature: ' + response.status)
  }
  console.log(JSON.stringify(endorsement))
}

let submissionOrigin

async function run() {
  requireValid(source && ['verify', 'inspect', 'sign-genesis', 'sign'].includes(command),
    'Usage: node quorum-witness.mjs <inspect|verify|sign-genesis|sign> <JSON file or HTTPS evidence URL> [trusted genesis SHA-256] [key PEM] [witness state] [approved opening manifest SHA-256]')
  const packet = await readEvidence(source)
  if (command === 'inspect') {
    console.log(JSON.stringify({
      genesis: await fingerprint(packet.genesis),
      manifest: packet.candidate && await fingerprint(packet.candidate.manifest),
    }, null, 2))
    return
  }
  requireValid(trusted && /^[a-f0-9]{64}$/.test(trusted), 'Supply the independently approved founding fingerprint.')
  submissionOrigin = packet.genesis.community
  if (command === 'sign-genesis') {
    await validateGenesis(packet.genesis, trusted)
    await outputSignature(await signEvidence(packet, args[0], args[1], undefined, true))
    return
  }
  await verifyFounding(packet, trusted)
  if (command === 'sign') {
    await outputSignature(await signEvidence(packet, args[0], args[1], args[2], false))
    return
  }
  if (!packet.candidate) {
    console.log(JSON.stringify({ verified: true, rosterSize: packet.genesis.members.length }, null, 2))
    return
  }
  requireValid(Array.isArray(packet.checkpoints), 'The ballot evidence has no checkpoint list.')
  const checkpoint = packet.checkpoints
    .filter(item => Object.keys(item.signatures).length >= packet.genesis.threshold)
    .sort((left, right) => Number(right.final) - Number(left.final) || right.size - left.size)[0]
  requireValid(checkpoint, 'No checkpoint has the required witness signatures.')
  const result = await certifiedSnapshot(packet, checkpoint, trusted)
  console.log(JSON.stringify({ verified: true, final: checkpoint.final, digest: checkpoint.digest, ...result }, null, 2))
}

try {
  await run()
  process.exit(0)
} catch (error) {
  console.error(error.message)
  process.exit(1)
}
