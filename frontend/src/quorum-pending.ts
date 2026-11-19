import { fingerprint, verifyVote, type Manifest, type Roster, type VoteEvidence } from './quorum.ts'

type Proof = VoteEvidence['proof']

export async function pendingVote(origin: string, manifest: Manifest, proof: Proof) {
  return { version: 'cope-quorum-pending-vote/1', origin, ballot: manifest.id, manifest: await fingerprint(manifest), proof }
}

export async function resumeVote(value: unknown, origin: string, manifest: Manifest, roster: Roster, options: string[], scope: string) {
  const saved = value as Awaited<ReturnType<typeof pendingVote>>
  if (saved?.version !== 'cope-quorum-pending-vote/1' || saved.origin !== origin || saved.ballot !== manifest.id
    || saved.manifest !== await fingerprint(manifest)) throw new Error('Choose a saved vote for this exact ballot.')
  await verifyVote(saved.proof, roster, options, scope)
  return saved.proof
}
