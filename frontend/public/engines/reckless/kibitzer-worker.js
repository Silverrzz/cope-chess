import init, { Engine } from './reckless.js'
let engine
let latest
let scheduled = false
async function initialize() {
  await init({ module_or_path: new URL('./reckless_bg.wasm', import.meta.url) })
  engine = new Engine()
  engine.set_threads(1)
}
const ready = initialize()
function search() {
  scheduled = false
  const job = latest
  if (!job) return
  try {
    engine.set_position(job.fen)
    engine.go_movetime(1500, 1, line => {
      const match = /score (cp|mate) (-?\d+)/.exec(line)
      if (match) postMessage({ id: job.id, kind: match[1], value: Number(match[2]), depth: Number(/depth (\d+)/.exec(line)?.[1] || 0) })
    })
    engine.take_output()
    if (!scheduled) { scheduled = true; setTimeout(search, 350) }
  } catch (error) { postMessage({ id: job.id, error: String(error) }) }
}
onmessage = async ({ data }) => {
  latest = data
  try { await ready } catch (error) { postMessage({ id: data.id, error: String(error) }); return }
  if (!scheduled) { scheduled = true; setTimeout(search, 40) }
}
