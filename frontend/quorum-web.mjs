import { createHash, randomBytes, timingSafeEqual } from 'node:crypto'
import { isIP } from 'node:net'
import { readFile, rename, unlink, writeFile } from 'node:fs/promises'

const files = new Map([
  ['/assets/verifier.js', ['quorum-web/app.js', 'text/javascript; charset=utf-8']],
  ['/assets/verifier.css', ['quorum-web/style.css', 'text/css; charset=utf-8']],
  ['/assets/tokens.css', ['src/styles/tokens.css', 'text/css; charset=utf-8']],
])
const hash = value => createHash('sha256').update(value).digest()
const hex = /^[0-9a-f]{64}$/
const tokenPattern = /^[A-Za-z0-9_-]{43,128}$/
const cookieTTL = 3600

export function send(response, status, body, type = 'application/json; charset=utf-8', headers = {}) {
  response.writeHead(status, {
    'Content-Type': type,
    'Cache-Control': 'no-store',
    'X-Content-Type-Options': 'nosniff',
    'Referrer-Policy': 'no-referrer',
    'Content-Security-Policy': "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
    ...headers,
  })
  response.end(typeof body === 'object' && !Buffer.isBuffer(body) ? JSON.stringify(body) : body)
}

class RequestError extends Error {
  constructor(status, message) {
    super(message)
    this.status = status
  }
}

async function body(request) {
  if (request.headers['content-type']?.split(';')[0] !== 'application/json') {
    throw new RequestError(415, 'Use a JSON request.')
  }
  if (Number(request.headers['content-length']) > 2048) throw new RequestError(413, 'Request is too large.')
  let size = 0
  const chunks = []
  for await (const chunk of request.iterator({ destroyOnReturn: false })) {
    size += chunk.length
    if (size > 2048) throw new RequestError(413, 'Request is too large.')
    chunks.push(chunk)
  }
  try {
    const value = JSON.parse(Buffer.concat(chunks).toString('utf8'))
    if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error()
    return value
  } catch {
    throw new RequestError(400, 'Enter a valid request.')
  }
}

export async function createWeb() {
  const pages = new Map()
  for (const [url, [file, type]] of files) {
    pages.set(url, [await readFile(new URL(file, import.meta.url)), type])
  }
  const shell = await readFile(new URL('quorum-web/index.html', import.meta.url))
  const tokenFile = process.env.COPE_VERIFIER_ADMIN_TOKEN_FILE
  const token = tokenFile ? (await readFile(tokenFile, 'utf8')).trim() : null
  if (token !== null && !tokenPattern.test(token)) {
    throw new Error('The verifier admin token must be 43–128 URL-safe characters.')
  }
  const tokenHash = token && hash(token)
  const adminOrigin = new URL(process.env.COPE_VERIFIER_ADMIN_ORIGIN || 'http://127.0.0.1:8788')
  if (adminOrigin.href !== adminOrigin.origin + '/' || !(
    adminOrigin.protocol === 'https:' || adminOrigin.origin === 'http://127.0.0.1:8788'
  )) throw new Error('The verifier admin origin must be an HTTPS origin or http://127.0.0.1:8788.')
  const secure = adminOrigin.protocol === 'https:'
  const proxies = new Set((process.env.COPE_VERIFIER_ADMIN_PROXIES || '').split(',').map(item => item.trim()).filter(Boolean))
  if ([...proxies].some(address => !isIP(address))) throw new Error('Verifier admin proxies must be IP addresses.')
  const cookieName = secure ? '__Host-cope-verifier-session' : 'cope-verifier-session'
  const cookie = (value, age) => cookieName + '=' + value + '; Path=/; HttpOnly; SameSite=Strict; Max-Age=' + age + (secure ? '; Secure' : '')
  const sessions = new Map()
  let loginAttempts = []
  const approvalPath = process.env.COPE_VERIFIER_UPDATE_APPROVAL
  const statusPath = process.env.COPE_VERIFIER_UPDATE_STATUS

  async function updateStatus() {
    if (!statusPath) return { state: 'disabled', commit: process.env.COPE_VERIFIER_BUILD_COMMIT || null }
    const status = JSON.parse(await readFile(statusPath, 'utf8'))
    if (status.offer && status.offer.expires_at <= Date.now() / 1000) {
      return { ...status, offer: null, state: 'error', message: 'The signed release has expired.' }
    }
    return status
  }

  async function admin(request, response, path) {
    const enabled = Boolean(tokenHash) && request.headers.host === adminOrigin.host
      && (secure
        ? proxies.has(request.socket.remoteAddress?.replace(/^::ffff:/, '')) && request.headers['x-forwarded-proto'] === 'https'
        : request.socket.remoteAddress === '127.0.0.1' && !request.headers['x-forwarded-proto'])
    const now = Date.now()
    for (const [id, expires] of sessions) if (expires <= now) sessions.delete(id)
    const key = request.headers.cookie?.split(';').map(item => item.trim())
      .find(item => item.startsWith(cookieName + '='))?.slice(cookieName.length + 1)
    const authenticated = enabled && sessions.has(key)
    if (request.method === 'GET' && path === '/api/admin/session') {
      return send(response, 200, { enabled, authenticated })
    }
    if (!enabled) throw new RequestError(403, 'Admin access is unavailable at this address.')
    if (request.method === 'POST' && request.headers.origin !== adminOrigin.origin) {
      throw new RequestError(403, 'Use the configured verifier admin origin.')
    }
    if (path === '/api/admin/session' && request.method === 'POST') {
      loginAttempts = loginAttempts.filter(attempt => attempt > now - 60000)
      if (loginAttempts.length >= 12) throw new RequestError(429, 'Too many sign-in attempts. Try again in a minute.')
      loginAttempts.push(now)
      const input = await body(request)
      if (typeof input.token !== 'string' || !timingSafeEqual(hash(input.token), tokenHash)) {
        throw new RequestError(401, 'Incorrect admin token.')
      }
      loginAttempts = []
      if (sessions.size >= 32) sessions.delete(sessions.keys().next().value)
      sessions.delete(key)
      const session = randomBytes(32).toString('hex')
      sessions.set(session, now + cookieTTL * 1000)
      return send(response, 200, { authenticated: true }, undefined, { 'Set-Cookie': cookie(session, cookieTTL) })
    }
    if (!authenticated) throw new RequestError(401, 'Sign in with this verifier’s admin token.')
    if (path === '/api/admin/logout' && request.method === 'POST') {
      sessions.delete(key)
      return send(response, 200, { authenticated: false }, undefined, { 'Set-Cookie': cookie('', 0) })
    }
    if (path === '/api/admin/update' && request.method === 'GET') {
      return send(response, 200, await updateStatus())
    }
    if (path === '/api/admin/update/approve' && request.method === 'POST') {
      const { digest, challenge } = await body(request)
      const state = await updateStatus()
      if (!approvalPath || state.state !== 'available' || !hex.test(digest) || !hex.test(challenge)
        || digest !== state.offer?.digest || challenge !== state.offer?.challenge) {
        throw new RequestError(409, 'This offer has changed or expired. Review the current release first.')
      }
      const temporary = approvalPath + '.' + randomBytes(12).toString('hex')
      try {
        await writeFile(temporary, JSON.stringify({ digest, challenge }), { flag: 'wx', mode: 0o600 })
        await rename(temporary, approvalPath)
      } finally {
        await unlink(temporary).catch(error => { if (error.code !== 'ENOENT') throw error })
      }
      return send(response, 202, { approved: true })
    }
    throw new RequestError(404, 'No admin endpoint at this address.')
  }

  return async (request, response, path, isReport, format) => {
    try {
      if (path.startsWith('/api/admin/')) {
        await admin(request, response, path)
        return true
      }
      if (request.method !== 'GET') return false
      if (pages.has(path)) {
        const [content, type] = pages.get(path)
        send(response, 200, content, type)
        return true
      }
      if (path === '/admin' || (format !== 'json' && (path === '/' || isReport) && /(?:^|,)\s*text\/html(?:\s*[,;]|$)/i.test(request.headers.accept || ''))) {
        send(response, 200, shell, 'text/html; charset=utf-8', { Vary: 'Accept' })
        return true
      }
      return false
    } catch (error) {
      if (!(error instanceof RequestError)) console.error('Verifier web request failed:', error.message)
      send(response, error.status ?? 500, { error: error.status ? error.message : 'Could not load this verifier request.' })
      return true
    }
  }
}
