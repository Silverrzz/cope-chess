import { createPrivateKey, createPublicKey, generateKeyPairSync, sign, verify } from 'node:crypto'
import { createServer } from 'node:http'
import { mkdir, open, readFile, readdir, rename, writeFile } from 'node:fs/promises'
import { setTimeout as delay } from 'node:timers/promises'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { createVoterWeb, createWeb, send } from './quorum-web.mjs'
import { hasStoredMembershipDecision, policyDecisionPath, readPolicyDecision, withPolicyLock } from './quorum-policy.mjs'
import { fingerprint, verifyBallot, verifyBallotContext, verifyVote } from './src/quorum.ts'
import { verifierEndpoints } from './src/quorum-relay.ts'
import { authorizedBallot, checkHistoryPacket, checkOpenQuorum, checkPolicySignatures, checkpointStatement, extendsHistory, historyDigest,
  intakeStatement, matchesWitnessPolicy, retainedStatement, settlementEnd, validPolicyRound, verifyHistoryResult, verifyIntake, verifyPolicyChain } from './src/quorum-history.ts'

const origin = new URL(process.argv[2] ?? process.env.COPE_VERIFIER_ORIGIN ?? 'https://dev.cope-chess.live')
if (origin.protocol !== 'https:' || origin.username || origin.password || origin.pathname !== '/' || origin.search || origin.hash) {
  throw new Error('COPE_VERIFIER_ORIGIN must be an HTTPS origin.')
}

const archive = process.env.COPE_VERIFIER_ARCHIVE ?? fileURLToPath(new URL('./state/', import.meta.url))
const uuid = /^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/
await mkdir(archive, { recursive: true })
const originFile = join(archive, 'origin')
try {
  await writeFile(originFile, origin.origin, { flag: 'wx', mode: 0o600 })
} catch (error) {
  if (error.code !== 'EEXIST') throw error
}
if (await readFile(originFile, 'utf8') !== origin.origin) {
  throw new Error('This archive belongs to a different COPE origin.')
}
const keyFile = join(archive, 'witness-ed25519.pem')
try {
  const generated = generateKeyPairSync('ed25519').privateKey
  await writeFile(keyFile, generated.export({ type: 'pkcs8', format: 'pem' }), { flag: 'wx', mode: 0o600 })
} catch (error) {
  if (error.code !== 'EEXIST') throw error
}
const privateKey = createPrivateKey(await readFile(keyFile))
const publicKey = createPublicKey(privateKey).export({ type: 'spki', format: 'der' }).subarray(-32).toString('hex')
console.log('Witness public key: ' + publicKey)
let lastScan = null
let scanErrors = 0
const witnessPolicyFile = process.env.COPE_VERIFIER_WITNESS_POLICY
const policyFile = join(archive, 'witness-policy-head')
const pinnedHead = await readFile(policyFile, 'utf8').catch(error => {
  if (error.code === 'ENOENT') return null
  throw error
})
const policyChain = witnessPolicyFile
  ? await verifyPolicyChain(JSON.parse(await readFile(witnessPolicyFile, 'utf8')), origin.origin) : null
if (pinnedHead && !policyChain?.heads.includes(pinnedHead)) {
  throw new Error('The configured witness policy does not extend the verifier’s pinned policy.')
}
const witnessPolicy = policyChain?.current ?? null
const witnessOperators = witnessPolicy && Object.entries(witnessPolicy.operators)
const journal = join(archive, 'vote-history')
const intakeArchive = join(archive, 'intake')
const relayRequests = new Set()
const forwarding = new Set()
const peerIntake = new Set()
const intakeStatus = (status, message) => Object.assign(new Error(message), { status })

async function relayBody(request) {
  if (request.headers['content-type']?.split(';')[0] !== 'application/json')
    throw intakeStatus(415, 'Use a JSON vote request.')
  const chunks = []
  let size = 0
  for await (const chunk of request.iterator({ destroyOnReturn: false })) {
    size += chunk.length
    if (size > 16384) throw intakeStatus(413, 'Vote request exceeds 16 KiB.')
    chunks.push(chunk)
  }
  try { return JSON.parse(Buffer.concat(chunks).toString('utf8')) }
  catch { throw intakeStatus(400, 'The vote request is not valid JSON.') }
}

async function readRelayFile(path) {
  return JSON.parse(await readFile(path, 'utf8').catch(error => {
    if (error.code === 'ENOENT') return 'null'
    throw error
  }))
}

async function certifiedIntake(id) {
  const path = join(intakeArchive, id)
  const entries = await readdir(path).catch(error => {
    if (error.code === 'ENOENT') return []
    throw error
  })
  return (await Promise.all(entries.filter(name => /^certified-[0-9]{1,77}\.json$/.test(name))
    .map(name => readRelayFile(join(path, name))))).filter(Boolean)
}

async function forwardVote(id, vote) {
  const key = id + '/' + vote.proof.nullifier
  if (forwarding.has(key) || forwarding.size >= 4) return
  forwarding.add(key)
  try {
    await fetch(new URL('/api/quorum/ballots/' + id + '/relay-votes', origin), {
      method: 'POST', redirect: 'error', signal: AbortSignal.timeout(15000),
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ proof: vote.proof, signatures: vote.signatures }),
    })
  } catch { /* A later scan retries from the durable copy. */ }
  finally { forwarding.delete(key) }
}

async function submitRelay(request, id, mode) {
  if (relayRequests.size >= 4) throw intakeStatus(503, 'This verifier is busy. Try again shortly.')
  const token = id + '/' + crypto.randomUUID()
  relayRequests.add(token)
  let locked
  try {
    const { proof, signatures } = await relayBody(request) ?? {}
    if (typeof proof?.nullifier !== 'string' || !/^(0|[1-9][0-9]{0,76})$/.test(proof.nullifier))
      throw intakeStatus(422, 'Invalid vote proof.')
    const packet = await governedPackage(id)
    const { manifest, roster } = packet
    const policy = await authorizedBallot(policyChain, manifest)
    if (!Object.values(policy.operators).includes(publicKey)) throw intakeStatus(403, 'This verifier is not assigned to this ballot.')
    if (!await readJournal(id) && (manifest.vote_history.policy !== witnessPolicyDigest
        || await hasStoredMembershipDecision(archive, policyChain.head))) {
      throw intakeStatus(409, 'This verifier is changing membership and cannot start this ballot.')
    }
    const hash = await fingerprint(proof)
    const manifestDigest = await fingerprint(manifest)
    const statement = intakeStatement(origin.origin, id, manifestDigest, hash)
    const retainReply = () => ({ key: publicKey, signature: sign(null,
      Buffer.from(JSON.stringify(retainedStatement(origin.origin, id, manifestDigest, hash))), privateKey).toString('hex') })
    const dir = join(intakeArchive, id)
    const path = join(dir, (mode === 'accept' ? 'received-' : 'certified-') + proof.nullifier + '.json')
    if (relayRequests.has(path)) throw intakeStatus(503, 'The same vote is already being checked. Try again shortly.')
    relayRequests.add(path)
    locked = path
    const existing = await readRelayFile(path)
    if (existing) {
      if (existing.hash !== hash) throw intakeStatus(409, 'This verifier already saw a different proof from this ballot credential.')
      if (mode !== 'accept' && Date.now() < settlementEnd(manifest)) void forwardVote(id, existing)
      return mode === 'accept' ? { key: publicKey, signature: existing.signature } : retainReply()
    }
    const cutoff = mode === 'accept' ? Date.parse(manifest.closes_at) : settlementEnd(manifest)
    if (Date.now() < Date.parse(manifest.opens_at) || Date.now() >= cutoff)
      throw intakeStatus(409, 'This verifier’s vote intake window has closed or has not opened.')
    try {
      if (mode !== 'accept') await checkPolicySignatures(policy, statement, signatures)
      const { options, scope } = await verifyBallotContext(manifest, roster)
      await verifyVote(proof, roster, options, scope)
    } catch { throw intakeStatus(422, 'Invalid vote proof or intake certificate.') }
    if (Date.now() >= cutoff) throw intakeStatus(409, 'The vote intake window closed while this proof was being checked.')
    await mkdir(dir, { recursive: true, mode: 0o700 })
    const signature = mode === 'accept' ? sign(null, Buffer.from(JSON.stringify(statement)), privateKey).toString('hex') : undefined
    const record = mode === 'accept' ? { hash, proof, signature } : { hash, proof, signatures }
    await durableWrite(path, JSON.stringify(record))
    await syncPath(intakeArchive)
    await syncPath(archive)
    if (mode !== 'accept') void forwardVote(id, record)
    return mode === 'accept' ? { key: publicKey, signature } : retainReply()
  } finally {
    relayRequests.delete(token)
    if (locked) relayRequests.delete(locked)
  }
}

async function readJournal(id) {
  try {
    return JSON.parse(await readFile(join(journal, id + '.json'), 'utf8'))
  } catch (error) {
    if (error.code === 'ENOENT') return null
    throw error
  }
}

// Persist the head before a signature can escape. A crash or restart must not
// let this key endorse a fork. The supervisor holds the installation lock.
async function durableWrite(target, value) {
  const temporary = target + '.new'
  const file = await open(temporary, 'w', 0o600)
  try {
    await file.writeFile(value)
    await file.sync()
  } finally {
    await file.close()
  }
  await rename(temporary, target)
  await syncPath(dirname(target))
}

async function syncPath(path) {
  if (process.platform !== 'win32') {
    const directory = await open(path, 'r')
    try {
      await directory.sync()
    } finally {
      await directory.close()
    }
  }
}

const saveJournal = (id, head) => durableWrite(join(journal, id + '.json'), JSON.stringify(head))

async function publishCheckpoint(id, checkpoint) {
  const response = await fetch(new URL('/api/quorum/ballots/' + id + '/history/checkpoints', origin), {
    method: 'POST', redirect: 'error', signal: AbortSignal.timeout(15000),
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ public_key: publicKey, ...checkpoint }),
  })
  if (!response.ok) throw new Error('COPE returned HTTP ' + response.status + ' for the history checkpoint.')
}

async function witnessHistory(packet, history, previous, opens, closes, policy) {
  const manifest = packet.manifest
  await checkHistoryPacket(manifest, policy, history)
  const { manifest_digest, vote_hashes } = history
  if (Date.now() < opens) return
  if (previous && (previous.manifest_digest !== manifest_digest || !extendsHistory(previous.vote_hashes, vote_hashes))) {
    throw new Error('The supplied history conflicts with the checkpoint already kept by this witness.')
  }

  const phase = Date.now() < closes ? 'open' : 'closed'
  if (phase === 'open') {
    if (previous?.closed) return
    if (!previous && manifest.vote_history.policy !== await fingerprint(witnessPolicy)) return
  }
  const digest = await historyDigest(vote_hashes)
  const recorded = previous?.[phase === 'open' ? 'opening' : 'seal']
  if (recorded?.digest === digest) {
    // Retry the exact durable attestation if the server lost or did not
    // receive it. Never create a fresh open attestation after the deadline.
    if (history[phase]?.digest !== digest) await publishCheckpoint(manifest.id, recorded)
    return
  }
  if (phase === 'closed') {
    if (peerIntake.has(manifest.id)) return
    if ([...relayRequests].some(token => token.startsWith(manifest.id + '/'))) return
    const seen = new Set(vote_hashes)
    if ((await certifiedIntake(manifest.id)).some(vote => !seen.has(vote.hash))) {
      throw new Error('The proposed tally omits a certified vote kept by this verifier.')
    }
    if (previous?.closed && previous.vote_hashes.length !== vote_hashes.length) {
      throw new Error('This witness already closed a different history.')
    }
    try {
      await checkOpenQuorum(origin.origin, manifest, policy, history)
    } catch (error) {
      if (previous?.opening?.digest !== digest) throw error
      await publishCheckpoint(manifest.id, previous.opening)
      return
    }
    const finalHashes = await Promise.all(packet.votes.map(fingerprint))
    if (finalHashes.length !== vote_hashes.length || !extendsHistory(finalHashes, vote_hashes)) {
      throw new Error('The final votes differ from the witnessed history.')
    }
    // An observer with no prior head may only seal a valid open quorum. It
    // never creates an open attestation after the deadline.
    await verifyBallot(packet)
    await verifyIntake(packet, policy, origin.origin, finalHashes)
  }
  const size = vote_hashes.length
  let checkpoint
  const recordCheckpoint = async () => {
    if (phase === 'open' && Date.now() >= closes) return
    checkpoint = { phase, size, digest, signature: sign(null, Buffer.from(JSON.stringify(checkpointStatement(
      origin.origin, manifest.id, manifest_digest, phase, size, digest))), privateKey).toString('hex') }
    const opening = phase === 'open' ? checkpoint : previous?.opening
    const seal = phase === 'closed' ? checkpoint : undefined
    await saveJournal(manifest.id, { manifest_digest, vote_hashes, closed: phase === 'closed', opening, seal })
  }
  if (phase === 'open' && !previous) {
    // Serialize the first checkpoint with storing a policy decision. Once a
    // quorum has stored the decision, no new old-policy ballot can get a quorum.
    await withPolicyLock(archive, policyChain.head, async () => {
      if (!await hasStoredMembershipDecision(archive, policyChain.head)) await recordCheckpoint()
    })
  } else await recordCheckpoint()
  if (checkpoint) await publishCheckpoint(manifest.id, checkpoint)
}

const witnessPolicyDigest = witnessOperators ? await fingerprint(witnessPolicy) : null

async function archivedBallots() {
  const files = await readdir(archive)
  return files.filter(file => file.endsWith('.json') && uuid.test(file.slice(0, -5)))
    .sort().map(file => '/ballots/' + file.slice(0, -5))
}

const appCommit = process.env.COPE_VERIFIER_BUILD_COMMIT

async function checkIn() {
  try {
    const sent_at = Math.floor(Date.now() / 1000)
    const last_scan = lastScan ? Math.floor(Date.parse(lastScan) / 1000) : null
    const archived = (await archivedBallots()).length
    const errors = scanErrors
    const runtime = process.version
    const body = { public_key: publicKey, sent_at, last_scan, archived, scan_errors: errors, runtime }
    const statement = ['cope-quorum-verifier-checkin/1', origin.origin, publicKey, sent_at, last_scan, archived, errors, runtime]
    if (appCommit) {
      body.app_commit = appCommit
      body.failed_job = null
      statement[0] = 'cope-quorum-verifier-checkin/2'
      statement.push(appCommit, body.failed_job)
    }
    body.signature = sign(null, Buffer.from(JSON.stringify(statement)), privateKey).toString('hex')
    const response = await fetch(new URL('/api/quorum/verifiers/check-in', origin), {
      method: 'POST', redirect: 'error', signal: AbortSignal.timeout(15000),
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    if (!response.ok && response.status !== 403) throw new Error('COPE returned HTTP ' + response.status)
  } catch (error) {
    console.error('Verifier check-in failed: ' + error.message)
  } finally {
    setTimeout(checkIn, 60000)
  }
}

async function fetchEvidence(path, limit = 16 * 1024 ** 2) {
  const response = await fetch(new URL(path, origin), { redirect: 'error', signal: AbortSignal.timeout(15000) })
  if (!response.ok || !response.body) throw new Error('Evidence request returned HTTP ' + response.status)
  const chunks = []
  let size = 0
  for await (const chunk of response.body) {
    size += chunk.byteLength
    if (size > limit) throw new Error('Evidence response is too large.')
    chunks.push(chunk)
  }
  return JSON.parse(Buffer.concat(chunks).toString('utf8'))
}

async function recoverPeerIntake(packet, policy) {
  const { manifest, roster } = packet
  const id = manifest.id
  if (peerIntake.has(id) || peerIntake.size >= 2) return
  peerIntake.add(id)
  try {
    const manifestDigest = await fingerprint(manifest)
    const { options, scope } = await verifyBallotContext(manifest, roster)
    const peers = verifierEndpoints(manifest, policy).filter(peer => peer.key !== publicKey)
    const dir = join(intakeArchive, id)
    const known = new Set((await certifiedIntake(id)).map(vote => vote.hash))
    for (const peer of peers) {
      if (Date.now() >= settlementEnd(manifest)) return
      let listed
      try { listed = await fetchEvidence(peer.origin + '/api/intake/' + id + '/retained', 81920) }
      catch { continue }
      if (!Array.isArray(listed.hashes) || listed.hashes.length > roster.members.length) continue
      for (const hash of listed.hashes) {
        if (Date.now() >= settlementEnd(manifest)) return
        if (typeof hash !== 'string' || !/^[0-9a-f]{64}$/.test(hash) || known.has(hash)) continue
        try {
          const vote = await fetchEvidence(peer.origin + '/api/intake/' + id + '/retained/' + hash, 16384)
          if (await fingerprint(vote.proof) !== hash) continue
          await checkPolicySignatures(policy, intakeStatement(origin.origin, id, manifestDigest, hash), vote.signatures)
          await verifyVote(vote.proof, roster, options, scope)
          if (Date.now() >= settlementEnd(manifest)) return
          const path = join(dir, 'certified-' + vote.proof.nullifier + '.json')
          if (relayRequests.has(path)) continue
          relayRequests.add(path)
          try {
            if (!await readRelayFile(path)) {
              await mkdir(dir, { recursive: true, mode: 0o700 })
              await durableWrite(path, JSON.stringify({ hash, proof: vote.proof, signatures: vote.signatures }))
              await syncPath(intakeArchive)
              await syncPath(archive)
            }
          } finally { relayRequests.delete(path) }
          known.add(hash)
          void forwardVote(id, vote)
        } catch { /* A peer's missing or invalid copy does not count. */ }
      }
    }
  } finally { peerIntake.delete(id) }
}

async function governedPackage(id) {
  const entry = policyChain?.ballots.get(id)
  if (!entry) throw intakeStatus(404, 'This ballot has no approved verifier decision here.')
  const round = policyChain.bundle.changes[policyChain.heads.indexOf(entry.epoch)]?.round
  const archived = round === undefined ? null : await readPolicyDecision(archive, entry.epoch, round)
  const dossier = archived && JSON.parse(archived).proposal
  if (dossier?.manifest?.id === id) {
    await authorizedBallot(policyChain, dossier.manifest)
    if (await fingerprint(dossier.roster) === entry.roster) return { manifest: dossier.manifest, roster: dossier.roster }
  }
  try {
    const ballot = await fetchEvidence('/api/quorum/ballots/' + id)
    await authorizedBallot(policyChain, ballot.manifest)
    if (await fingerprint(ballot.roster) === entry.roster) return { manifest: ballot.manifest, roster: ballot.roster }
  } catch { /* The relay can only use an approved ballot and exact roster. */ }
  throw intakeStatus(503, 'This verifier cannot load the approved ballot and roster.')
}

async function readRecord(id) {
  try {
    const record = JSON.parse(await readFile(join(archive, id + '.json'), 'utf8'))
    if (!witnessPolicyDigest || record.report.witness_checks?.policy_digest !== witnessPolicyDigest) {
      delete record.report.witness_checks
    }
    return record
  } catch (error) {
    if (error.code === 'ENOENT') return null
    throw error
  }
}

async function saveRecord(id, record) {
  const file = join(archive, id + '.json')
  await writeFile(file + '.new', JSON.stringify(record), { mode: 0o600 })
  await rename(file + '.new', file)
}

async function publishSignature(id, record) {
  if ((witnessOperators && !matchesWitnessPolicy(record.evidence.manifest, witnessPolicy))
    || !record.evidence.manifest.witnesses?.some(witness => witness?.public_key === publicKey)
    || record.report.signature_published_at) return
  const { digest } = record.report
  const statement = JSON.stringify(['cope-quorum-witness-result/1', origin.origin, id, digest])
  const signature = sign(null, Buffer.from(statement), privateKey).toString('hex')
  record.report.public_key = publicKey
  record.report.signature = signature
  await saveRecord(id, record)
  const response = await fetch(new URL('/api/quorum/ballots/' + id + '/witness-signatures', origin), {
    method: 'POST', redirect: 'error', signal: AbortSignal.timeout(15000),
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ public_key: publicKey, digest, signature }),
  })
  if (!response.ok) throw new Error('COPE returned HTTP ' + response.status + ' for the witness signature.')
  record.report.signature_published_at = new Date().toISOString()
  await saveRecord(id, record)
}

async function checkWitnesses(id, record) {
  if (!witnessOperators) return
  const matches = matchesWitnessPolicy(record.evidence.manifest, witnessPolicy)
  const status = { policy_digest: witnessPolicyDigest, policy_matches: matches, checked: [], threshold: witnessPolicy.threshold, operators: witnessOperators.length }
  if (matches) {
    const { signatures } = await fetchEvidence('/api/quorum/ballots/' + id + '/publications')
    const statement = Buffer.from(JSON.stringify(['cope-quorum-witness-result/1', origin.origin, id, record.report.digest]))
    for (const [operator, key] of witnessOperators) {
      const signature = signatures?.[key]
      if (typeof signature !== 'string' || !/^[a-f0-9]{128}$/.test(signature)) continue
      const publicKey = createPublicKey({ key: Buffer.from('302a300506032b6570032100' + key, 'hex'), format: 'der', type: 'spki' })
      if (verify(null, statement, publicKey, Buffer.from(signature, 'hex'))) status.checked.push(operator)
    }
  }
  if (JSON.stringify(record.report.witness_checks) !== JSON.stringify(status)) {
    record.report.witness_checks = status
    await saveRecord(id, record)
  }
}

async function observe(id, witnessed) {
  const packet = await fetchEvidence('/api/quorum/ballots/' + id)
  if (packet.manifest?.id !== id) throw new Error('Ballot ID does not match the directory.')
  if (Boolean(packet.manifest.vote_history) !== witnessed) throw new Error('The ballot history format does not match the directory.')
  const previous = await readJournal(id)
  const existing = await readRecord(id)
  let finalHistory
  let ballotPolicy
  if (packet.manifest.vote_history) {
    ballotPolicy = await authorizedBallot(policyChain, packet.manifest)
    const opens = Date.parse(packet.manifest.opens_at)
    const closes = settlementEnd(packet.manifest)
    if (!Number.isFinite(opens) || !Number.isFinite(closes) || opens >= closes) throw new Error('Invalid ballot window.')
    if (Date.now() < opens) return
    finalHistory = await fetchEvidence('/api/quorum/ballots/' + id + '/history')
    if (Object.values(ballotPolicy.operators).includes(publicKey)) {
      if (Date.now() < closes) {
        const pending = await certifiedIntake(id)
        const posted = new Set(finalHistory.vote_hashes)
        for (const vote of pending) if (!posted.has(vote.hash)) void forwardVote(id, vote)
        if (Date.now() >= Date.parse(packet.manifest.closes_at)) {
          void recoverPeerIntake(packet, ballotPolicy).catch(() => {})
        }
      }
      await witnessHistory(packet, finalHistory, previous, opens, closes, ballotPolicy)
    }
    if (Date.now() < closes || packet.status !== 'completed' || !finalHistory.closed) return
    if (previous && (previous.manifest_digest !== finalHistory.manifest_digest
      || !extendsHistory(previous.vote_hashes, finalHistory.vote_hashes)
      || (previous.closed && previous.vote_hashes.length !== finalHistory.vote_hashes.length))) {
      throw new Error('The final history conflicts with this witness’s stored checkpoint.')
    }
    await checkHistoryPacket(packet.manifest, ballotPolicy, finalHistory)
  } else if (previous) {
    throw new Error('The previously witnessed ballot no longer advertises its history.')
  } else if (packet.status !== 'completed') return
  const digest = await fingerprint(packet)
  if (existing?.report.digest === digest) {
    if (!existing.report.conflict) {
      await publishSignature(id, existing)
      await checkWitnesses(id, existing)
    }
    return
  }
  if (existing?.report.conflict?.digest === digest && (!finalHistory || existing.history)) return
  if (finalHistory) await verifyHistoryResult(packet, finalHistory, ballotPolicy, origin.origin)
  else await verifyBallot(packet)
  const observedAt = new Date().toISOString()
  if (existing && (existing.history || !finalHistory)) {
    existing.report.conflict = { digest, observed_at: observedAt }
    await saveRecord(id, existing)
    return
  }
  const record = {
    evidence: packet,
    ...(finalHistory ? { history: finalHistory } : {}),
    report: { id, title: packet.manifest.title, origin: origin.origin, digest, observed_at: observedAt, result: packet.result,
      ...(finalHistory ? { history_verified: true } : {}),
      ...(existing ? { conflict: { digest: existing.report.digest, observed_at: existing.report.observed_at } } : {}) },
  }
  await saveRecord(id, record)
  await publishSignature(id, record)
  await checkWitnesses(id, record)
}

const observing = new Set()
const completedHistoryScans = new Map()
let historyScanErrors = 0
let legacyScanErrors = 0

async function scan(witnessed) {
  let errors = 0
  try {
    const directory = await fetchEvidence(witnessed ? '/api/quorum/history/ballots' : '/api/quorum')
    for (const ballot of directory.ballots) {
      const { id, status } = ballot
      if (!uuid.test(id) || observing.has(id)) continue
      if (!witnessed && (status !== 'completed' || ballot.vote_history)) continue
      if (witnessed && status === 'completed' && (completedHistoryScans.get(id) ?? 0) > Date.now()) continue
      try {
        observing.add(id)
        await observe(id, witnessed)
        if (witnessed && status === 'completed' && await readRecord(id)) completedHistoryScans.set(id, Date.now() + 60000)
      } catch (error) {
        errors++
        console.error('Ballot ' + id + ': ' + error.message)
      } finally {
        observing.delete(id)
      }
    }
    lastScan = new Date().toISOString()
  } catch (error) {
    errors++
    console.error('Could not read the Quorum directory: ' + error.message)
  } finally {
    if (witnessed) historyScanErrors = errors
    else legacyScanErrors = errors
    scanErrors = historyScanErrors + legacyScanErrors
    setTimeout(scan, witnessed ? 5000 : 60000, witnessed)
  }
}

const web = await createWeb()
const voterWeb = await createVoterWeb(policyChain, origin.origin).catch(error => {
  console.error('Independent voting disabled:', error.message)
  return null
})
const ready = process.env.COPE_VERIFIER_READY_FILE
const activate = process.env.COPE_VERIFIER_ACTIVATE_FILE
let serving = !(ready && activate)
const server = createServer({ headersTimeout: 10000, requestTimeout: 15000 }, async (request, response) => {
  try {
    if (!serving) return send(response, 503, { error: 'The verifier is starting.' })
    const url = new URL(request.url, 'http://localhost')
    const path = url.pathname
    if (voterWeb?.(request, response, path)) return
    const retained = /^\/api\/intake\/([^/]+)\/retained(?:\/([0-9a-f]{64}))?$/.exec(path)
    if (request.method === 'GET' && retained && uuid.test(retained[1])) {
      try {
        const { manifest } = await governedPackage(retained[1])
        if (Date.now() < Date.parse(manifest.closes_at)) return send(response, 404, { error: 'Voting is still open.' })
        const votes = await certifiedIntake(retained[1])
        if (!retained[2]) return send(response, 200, { hashes: votes.map(vote => vote.hash) })
        const vote = votes.find(vote => vote.hash === retained[2])
        return vote ? send(response, 200, vote) : send(response, 404, { error: 'No retained vote at this address.' })
      } catch { return send(response, 404, { error: 'No approved ballot at this address.' }) }
    }
    const intake = /^\/api\/intake\/([^/]+)(?:\/(accept|retain))?$/.exec(path)
    if (intake && uuid.test(intake[1])) {
      const cors = { 'Access-Control-Allow-Origin': '*', 'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
        'Access-Control-Allow-Headers': 'Content-Type' }
      try {
        if (request.method === 'OPTIONS') return send(response, 204, '', undefined, cors)
        if (request.method === 'GET' && !intake[2]) return send(response, 200, await governedPackage(intake[1]), undefined, cors)
        if (request.method === 'POST' && intake[2]) return send(response, 200, await submitRelay(request, intake[1], intake[2]), undefined, cors)
        return send(response, 405, { error: 'Method not allowed' }, undefined, cors)
      } catch (error) {
        return send(response, error.status ?? 503, { error: error.status ? error.message : 'Verifier vote intake is unavailable.' }, undefined, cors)
      }
    }
    const match = /^\/ballots\/([^/]+)(\/evidence)?$/.exec(path)
    const ballotId = match && uuid.test(match[1]) ? match[1] : null
    if (await web(request, response, path, ballotId && !match[2], url.searchParams.get('format'))) return
    if (request.method !== 'GET') return send(response, 405, { error: 'Method not allowed' })
    if (path === '/' || path === '/api/status') {
      const ballots = await archivedBallots()
      return send(response, 200, { origin: origin.origin, public_key: publicKey, last_scan: lastScan,
        scan_errors: scanErrors, ballots, witness_policy: Boolean(witnessOperators), commit: appCommit ?? null }, undefined, { Vary: 'Accept' })
    }
    const decision = /^\/api\/policy-decisions\/([a-f0-9]{64})(?:\/(0|[1-9][0-9]*))?$/.exec(path)
    if (decision && policyChain?.heads.includes(decision[1])) {
      const dir = policyDecisionPath(archive, decision[1])
      if (!decision[2]) {
        const files = await readdir(dir).catch(error => {
          if (error.code === 'ENOENT') return []
          throw error
        })
        const rounds = files.filter(file => /^(0|[1-9][0-9]*)\.json$/.test(file))
          .map(file => Number(file.slice(0, -5))).filter(validPolicyRound).sort((a, b) => a - b)
        return send(response, 200, { rounds })
      }
      if (validPolicyRound(Number(decision[2]))) {
        const record = await readPolicyDecision(archive, decision[1], decision[2])
        return record ? send(response, 200, record) : send(response, 404, { error: 'No stored decision for this round' })
      }
    }
    if (ballotId) {
      const record = await readRecord(ballotId)
      if (record) {
        const evidence = record.history ? { version: 'cope-quorum-final-evidence/1', origin: origin.origin,
          ballot: record.evidence, history: record.history } : record.evidence
        return send(response, 200, match[2] ? evidence : record.report, undefined, { Vary: 'Accept' })
      }
    }
    return send(response, 404, { error: 'No archived ballot at this address' })
  } catch {
    return send(response, 500, { error: 'Could not read the archive' })
  }
})
server.listen(8788, process.env.COPE_VERIFIER_BIND || '127.0.0.1')
await new Promise(resolve => server.once('listening', resolve))
if (ready && activate) {
  await writeFile(ready, '', { flag: 'wx', mode: 0o600 })
  while (true) {
    try {
      await readFile(activate)
      break
    } catch (error) {
      if (error.code !== 'ENOENT') throw error
      await delay(100)
    }
  }
}
if (witnessPolicy) {
  await mkdir(journal, { recursive: true, mode: 0o700 })
  if (policyChain.head !== pinnedHead) await durableWrite(policyFile, policyChain.head)
}
serving = true
void scan(false)
if (witnessPolicy) void scan(true)
void checkIn()
