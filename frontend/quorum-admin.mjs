import { createServer } from 'node:http'
import { createWeb, send } from './quorum-web.mjs'

// This process is started from the operator-installed bootstrap, never the
// candidate release. Route the dedicated admin origin here.
const web = await createWeb()
const server = createServer({ headersTimeout: 10000, requestTimeout: 15000 }, async (request, response) => {
  const path = new URL(request.url, 'http://localhost').pathname
  if (path === '/healthz') return send(response, 200, { ready: true })
  if (await web(request, response, path, false, 'json')) return
  send(response, 404, { error: 'Open /admin to manage this verifier.' })
})
server.listen(8789, process.env.COPE_VERIFIER_ADMIN_BIND || '127.0.0.1')
