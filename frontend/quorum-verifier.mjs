import { Group } from '@semaphore-protocol/group'
import { verifyProof } from '@semaphore-protocol/proof'
import { exit, stderr, stdout } from 'node:process'
import { readFileSync } from 'node:fs'

try {
  const request = JSON.parse(readFileSync(0, 'utf8'))
  const group = new Group(request.members)
  const root = group.root.toString()
  if (request.proof === undefined) {
    await new Promise(resolve => stdout.write(JSON.stringify({ root }), resolve))
  } else {
    const proof = request.proof
    const valid = proof.merkleTreeRoot === root && proof.scope === request.scope &&
      proof.merkleTreeDepth === 10 && await verifyProof(proof)
    await new Promise(resolve => stdout.write(JSON.stringify({ valid }), resolve))
  }
} catch {
  stderr.write('Unable to verify Semaphore proof.')
  process.exitCode = 1
}

exit(process.exitCode ?? 0)
