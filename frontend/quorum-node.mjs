import { createServer } from 'node:http'
import { mkdir, readFile, readdir, rename, writeFile } from 'node:fs/promises'
import { join } from 'node:path'
import { fingerprint, verifyBallot } from './src/quorum.ts'

const origin = new URL(process.env.COPE_VERIFIER_ORIGIN ?? 'https://dev.cope-chess.live')
if (origin.protocol !== 'https:' || origin.username || origin.password || origin.pathname !== '/' || origin.search || origin.hash) {
  throw new Error('COPE_VERIFIER_ORIGIN must be an HTTPS origin.')
}

const archive = process.env.COPE_VERIFIER_ARCHIVE ?? '/var/lib/cope-quorum-verifier'
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
let lastScan = null

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

async function observe(id) {
  const packet = await fetchEvidence('/api/quorum/ballots/' + id)
  if (packet.manifest?.id !== id) throw new Error('Ballot ID does not match the directory.')
  const digest = await fingerprint(packet)
  const existing = await readRecord(id)
  if (existing?.report.digest === digest || existing?.report.conflict?.digest === digest) return
  await verifyBallot(packet)
  const observedAt = new Date().toISOString()
  if (existing) {
    existing.report.conflict = { digest, observed_at: observedAt }
    await saveRecord(id, existing)
    return
  }
  await saveRecord(id, {
    evidence: packet,
    report: { id, title: packet.manifest.title, origin: origin.origin, digest, observed_at: observedAt, result: packet.result },
  })
}

async function scan() {
  try {
    const directory = await fetchEvidence('/api/quorum')
    for (const ballot of directory.ballots) {
      if (ballot.status !== 'completed' || !uuid.test(ballot.id)) continue
      try {
        await observe(ballot.id)
      } catch (error) {
        console.error('Ballot ' + ballot.id + ': ' + error.message)
      }
    }
    lastScan = new Date().toISOString()
  } catch (error) {
    console.error('Could not read the Quorum directory: ' + error.message)
  } finally {
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
      const files = await readdir(archive)
      const ballots = files.filter(file => file.endsWith('.json') && uuid.test(file.slice(0, -5)))
        .map(file => '/ballots/' + file.slice(0, -5))
      return send(response, 200, { origin: origin.origin, last_scan: lastScan, ballots })
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
}).listen(8788, '0.0.0.0')
void scan()
