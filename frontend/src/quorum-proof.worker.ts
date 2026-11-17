import { Group } from '@semaphore-protocol/group'
import { Identity } from '@semaphore-protocol/identity'
import { generateProof } from '@semaphore-protocol/proof'

self.onmessage = async ({ data }) => {
  try {
    const proof = await generateProof(Identity.import(data.secret), new Group(data.members),
      data.message, data.scope, 10, {
        wasm: '/assets/quorum/semaphore-10.wasm',
        zkey: '/assets/quorum/semaphore-10.zkey',
      })
    self.postMessage({ proof })
  } catch (error) {
    self.postMessage({ error: error instanceof Error ? error.message : 'Could not generate a vote proof.' })
  }
}
