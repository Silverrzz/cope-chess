import { readFileSync } from 'node:fs'
import { verifyEvidence } from './src/quorum.ts'

try {
  if (process.argv.length !== 3) throw new Error('Usage: node quorum-audit.mjs <ballot-or-vote-evidence.json>')
  const evidence = JSON.parse(readFileSync(process.argv[2], 'utf8'))
  console.log(JSON.stringify(await verifyEvidence(evidence), null, 2))
  process.exit(0)
} catch (error) {
  console.error(error.message)
  process.exit(1)
}
