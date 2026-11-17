import { createPrivateKey, createPublicKey, generateKeyPairSync, sign } from 'node:crypto'
import { createServer } from 'node:http'
import { mkdir, readFile, readdir, rename, writeFile } from 'node:fs/promises'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { fingerprint, verifyBallot } from './src/quorum.ts'

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

async function archivedBallots() {
  const files = await readdir(archive)
  return files.filter(file => file.endsWith('.json') && uuid.test(file.slice(0, -5)))
    .map(file => '/ballots/' + file.slice(0, -5))
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

async function fetchEvidence(path) {
  const response = await fetch(new URL(path, origin), { redirect: 'error', signal: AbortSignal.timeout(15000) })
  if (!response.ok || !response.body) throw new Error('Evidence request returned HTTP ' + response.status)
  const chunks = []
  let size = 0
  for await (const chunk of response.body) {
    size += chunk.byteLength
    if (size > 4 * 1024 * 1024) throw new Error('Evidence response is too large.')
    chunks.push(chunk)
  }
  return JSON.parse(Buffer.concat(chunks).toString('utf8'))
}

async function readRecord(id) {
  try {
    return JSON.parse(await readFile(join(archive, id + '.json'), 'utf8'))
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
  if (!record.evidence.manifest.witnesses?.some(witness => witness.public_key === publicKey)
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

async function observe(id) {
  const packet = await fetchEvidence('/api/quorum/ballots/' + id)
  if (packet.manifest?.id !== id) throw new Error('Ballot ID does not match the directory.')
  const digest = await fingerprint(packet)
  const existing = await readRecord(id)
  if (existing?.report.digest === digest) {
    if (!existing.report.conflict) await publishSignature(id, existing)
    return
  }
  if (existing?.report.conflict?.digest === digest) return
  await verifyBallot(packet)
  const observedAt = new Date().toISOString()
  if (existing) {
    existing.report.conflict = { digest, observed_at: observedAt }
    await saveRecord(id, existing)
    return
  }
  const record = {
    evidence: packet,
    report: { id, title: packet.manifest.title, origin: origin.origin, digest, observed_at: observedAt, result: packet.result },
  }
  await saveRecord(id, record)
  await publishSignature(id, record)
}

async function scan() {
  let errors = 0
  try {
    const directory = await fetchEvidence('/api/quorum')
    for (const ballot of directory.ballots) {
      if (ballot.status !== 'completed' || !uuid.test(ballot.id)) continue
      try {
        await observe(ballot.id)
      } catch (error) {
        errors++
        console.error('Ballot ' + ballot.id + ': ' + error.message)
      }
    }
    lastScan = new Date().toISOString()
  } catch (error) {
    errors++
    console.error('Could not read the Quorum directory: ' + error.message)
  } finally {
    scanErrors = errors
    setTimeout(scan, 60000)
  }
}

function send(response, status, body) {
  response.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8', 'X-Content-Type-Options': 'nosniff' })
  response.end(JSON.stringify(body))
}

createServer(async (request, response) => {
  if (request.method !== 'GET') return send(response, 405, { error: 'Method not allowed' })
  try {
    const path = new URL(request.url, 'http://localhost').pathname
    if (path === '/') {
      const ballots = await archivedBallots()
      return send(response, 200, { origin: origin.origin, public_key: publicKey, last_scan: lastScan, ballots })
    }
    const match = /^\/ballots\/([^/]+)(\/evidence)?$/.exec(path)
    if (match && uuid.test(match[1])) {
      const record = await readRecord(match[1])
      if (record) return send(response, 200, match[2] ? record.evidence : record.report)
    }
    return send(response, 404, { error: 'No verified ballot at this address' })
  } catch {
    return send(response, 500, { error: 'Could not read the archive' })
  }
}).listen(8788, process.env.COPE_VERIFIER_BIND || '127.0.0.1', () => {
  void scan()
  void checkIn()
})
