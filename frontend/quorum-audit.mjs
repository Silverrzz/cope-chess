import { readFileSync } from 'node:fs'
import { verifyBallot } from './src/quorum.ts'

try {
  if (process.argv.length !== 3) throw new Error('Usage: node quorum-audit.mjs <ballot-evidence.json>')
  const evidence = JSON.parse(readFileSync(process.argv[2], 'utf8'))
  await verifyBallot(evidence)
  console.log('Verified the published vote proofs and tally.')
  process.exit(0)
} catch (error) {
  console.error(error.message)
  process.exit(1)
}
