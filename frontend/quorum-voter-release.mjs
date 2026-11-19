import { createHash } from 'node:crypto'
import { copyFile, readFile, writeFile } from 'node:fs/promises'
import { join } from 'node:path'
import { fingerprint } from './src/quorum.ts'

const [artifacts, directory = 'dist/quorum-voter', ...extra] = process.argv.slice(2)
if (!artifacts || extra.length) throw new Error('Usage: node quorum-voter-release.mjs <proof-artifacts-dir> [vite-output-dir]')
const files = {}
for (const name of ['vote.js', 'semaphore-10.wasm', 'semaphore-10.zkey']) {
  const destination = join(directory, name)
  if (name !== 'vote.js') await copyFile(join(artifacts, name), destination)
  const bytes = await readFile(destination)
  files[name] = { size: bytes.length, sha256: createHash('sha256').update(bytes).digest('hex') }
}
const release = { version: 'cope-quorum-voter-release/1', files }
await writeFile(join(directory, 'release.json'), JSON.stringify(release, null, 2) + '\n', { flag: 'wx' })
await writeFile(join(directory, 'proposal.json'), JSON.stringify({
  approval: { voter_release: await fingerprint(release) }, release,
}, null, 2) + '\n', { flag: 'wx' })
