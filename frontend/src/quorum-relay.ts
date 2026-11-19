import { fingerprint, type Manifest, type VoteEvidence } from './quorum.ts'
import { checkPolicySignature, checkPolicySignatures, intakeStatement, retainedStatement, settlementEnd, type WitnessPolicy } from './quorum-history.ts'

export function verifierEndpoints(manifest: Manifest, policy: WitnessPolicy) {
  const assigned = new Set(Object.values(policy.operators))
  const hosts = new Set<string>()
  return (manifest.witnesses ?? []).flatMap(witness => {
    if (!assigned.has(witness.public_key)) return []
    try {
      const url = new URL(witness.report_origin)
      if (url.protocol !== 'https:' || url.origin !== witness.report_origin || hosts.has(url.origin)) return []
      hosts.add(url.origin)
      return [{ key: witness.public_key, origin: url.origin }]
    } catch { return [] }
  })
}

async function post(site: string, id: string, mode: 'accept' | 'retain', value: object) {
  const response = await fetch(site + '/api/intake/' + id + '/' + mode, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(value),
    credentials: 'omit', referrerPolicy: 'no-referrer', redirect: 'error', signal: AbortSignal.timeout(18000),
  })
  if (!response.ok) throw new Error('A verifier did not accept the vote.')
  return response.json()
}

export async function submitToVerifiers(manifest: Manifest, policy: WitnessPolicy, copeOrigin: string, proof: VoteEvidence['proof']) {
  const endpoints = verifierEndpoints(manifest, policy)
  if (endpoints.length < policy.threshold) throw new Error('This ballot needs more independent verifier addresses.')
  const hash = await fingerprint(proof)
  const manifestDigest = await fingerprint(manifest)
  const statement = intakeStatement(copeOrigin, manifest.id, manifestDigest, hash)
  const signatures: Record<string, string> = Object.create(null)
  await Promise.allSettled(endpoints.map(async endpoint => {
    const reply = await post(endpoint.origin, manifest.id, 'accept', { proof })
    if (reply.key === endpoint.key && await checkPolicySignature(policy, statement, endpoint.key, reply.signature)) {
      signatures[endpoint.key] = reply.signature
    }
  }))
  await checkPolicySignatures(policy, statement, signatures).catch(() => {
    throw new Error(Date.now() < Date.parse(manifest.closes_at)
      ? 'Too few verifiers confirmed intake. Retry the saved vote before voting closes.'
      : 'Voting has closed. Only verifiers that accepted this proof before closing can confirm its intake. Retry with the same file.')
  })
  const retained = retainedStatement(copeOrigin, manifest.id, manifestDigest, hash)
  const copies = await Promise.allSettled(endpoints.map(async endpoint => {
    const reply = await post(endpoint.origin, manifest.id, 'retain', { proof, signatures })
    if (reply.key !== endpoint.key || !await checkPolicySignature(policy, retained, endpoint.key, reply.signature))
      throw new Error('This verifier did not confirm retention.')
    return [endpoint.key, reply.signature] as const
  }))
  if (copies.filter(copy => copy.status === 'fulfilled').length < policy.threshold) {
    throw new Error(Date.now() < settlementEnd(manifest)
      ? 'Too few verifiers retained the certified vote. Retry the saved vote.'
      : 'Settlement has ended. Only verifiers that already retained this vote can return a receipt. Retry with the same file.')
  }
  return { proof, signatures, retained: Object.fromEntries(copies.flatMap(copy => copy.status === 'fulfilled' ? [copy.value] : [])) }
}
