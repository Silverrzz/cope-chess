import { readFileSync } from 'node:fs'
import { verifyEvidence } from './src/quorum.ts'
import { verifyHistoryResult, verifyRecordedReceipt, verifyPolicyChain } from './src/quorum-history.ts'

try {
  const [file, policyFile, trustedOrigin, ...extra] = process.argv.slice(2)
  if (!file || extra.length || (Boolean(policyFile) !== Boolean(trustedOrigin))) {
    throw new Error('Usage: node quorum-audit.mjs <evidence.json> [<trusted-policy.json> <trusted-COPE-origin>]')
  }
  const evidence = JSON.parse(readFileSync(file, 'utf8'))
  let result
  if (evidence.version === 'cope-quorum-final-evidence/1' || evidence.version === 'cope-quorum-recorded-vote/1') {
    if (!policyFile || evidence.origin !== trustedOrigin) throw new Error('Provide an independently trusted witness policy and COPE origin.')
    const chain = await verifyPolicyChain(JSON.parse(readFileSync(policyFile, 'utf8')), trustedOrigin)
    const manifest = evidence.manifest ?? evidence.ballot.manifest
    const policy = chain.epochs.get(manifest.vote_history?.epoch)
    if (!policy) throw new Error('The trusted policy chain does not cover this ballot.')
    if (evidence.version === 'cope-quorum-recorded-vote/1') {
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
