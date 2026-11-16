import { sealBallot } from './ballot-protocol'
import { errorText, type Manifest, type VoterProof } from './ballots'

self.onmessage = async (event: MessageEvent<{ manifest: Manifest; proof: VoterProof; choices: number[] }>) => {
  try {
    const { manifest, proof, choices } = event.data
    self.postMessage(await sealBallot(manifest, proof, choices))
  } catch (error) {
    self.postMessage({ error: errorText(error) })
  }
}
