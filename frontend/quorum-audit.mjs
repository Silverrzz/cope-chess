import { readFileSync } from 'node:fs'
import { verifyEvidence } from './src/quorum.ts'
import { authorizedBallot, evidenceManifest, verifyHistoryResult, verifyRecordedReceipt, verifyRetentionReceipt, verifyPolicyChain } from './src/quorum-history.ts'

try {
  const [file, policyFile, trustedOrigin, ...extra] = process.argv.slice(2)
  if (!file || extra.length || (Boolean(policyFile) !== Boolean(trustedOrigin))) {
    throw new Error('Usage: node quorum-audit.mjs <evidence.json> [<trusted-policy.json> <trusted-COPE-origin>]')
  }
  const evidence = JSON.parse(readFileSync(file, 'utf8'))
  let result
  if (['cope-quorum-final-evidence/1', 'cope-quorum-recorded-vote/1', 'cope-quorum-relayed-vote/1'].includes(evidence.version)) {
    if (!policyFile || evidence.origin !== trustedOrigin) throw new Error('Provide an independently trusted witness policy and COPE origin.')
    const chain = await verifyPolicyChain(JSON.parse(readFileSync(policyFile, 'utf8')), trustedOrigin)
    const manifest = evidenceManifest(evidence)
    const policy = await authorizedBallot(chain, manifest)
    if (evidence.version === 'cope-quorum-relayed-vote/1') {
      result = { kind: 'retained-vote', ballot: manifest.id, vote_hash: await verifyRetentionReceipt(evidence, policy, trustedOrigin) }
    } else if (evidence.version === 'cope-quorum-recorded-vote/1') {
      await verifyRecordedReceipt(evidence, policy, trustedOrigin)
      result = { kind: 'recorded-vote', ballot: evidence.manifest.id, vote_hash: evidence.vote_hash }
    } else {
      await verifyHistoryResult(evidence.ballot, evidence.history, policy, trustedOrigin)
      result = { kind: 'witnessed-result', title: evidence.ballot.manifest.title, result: evidence.ballot.result }
    }
  } else {
    if (evidence.version === 'cope-quorum-evidence/1' && evidence.manifest?.vote_history) {
      throw new Error('This ballot requires the witnessed history and both checkpoints. Use the full final evidence file.')
    }
    result = { ...await verifyEvidence(evidence), history_verified: false }
  }
  console.log(JSON.stringify(result, null, 2))
  process.exit(0)
} catch (error) {
  console.error(error.message)
  process.exit(1)
}
