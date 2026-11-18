import { Group } from '@semaphore-protocol/group'
import { Identity } from '@semaphore-protocol/identity'
import { generateProof, type SemaphoreProof } from '@semaphore-protocol/proof'
import { unlockCredential } from './quorum-credential.ts'
import { authorizedBallot, verifyPolicyChain } from './quorum-history.ts'
import { ballotOptions, verifyBallotContext, type BallotPackage } from './quorum.ts'
import { submitToVerifiers } from './quorum-relay.ts'

const element = <T extends HTMLElement>(id: string) => document.getElementById(id) as T
const app = element('voter')
const form = element<HTMLFormElement>('vote-form')
const status = element('vote-status')
const password = element<HTMLInputElement>('password')
const backup = element<HTMLInputElement>('backup')
const choice = element<HTMLSelectElement>('choice')
const retry = element<HTMLButtonElement>('retry')
const download = element<HTMLButtonElement>('download')

async function start() {
  const id = app.dataset.ballot!
  const release = app.dataset.release!
  const get = async (path: string) => {
    const response = await fetch(path, { credentials: 'omit', redirect: 'error', signal: AbortSignal.timeout(12000) })
    if (!response.ok) throw new Error('The verifier could not load this approved ballot.')
    return response.json()
  }
  const [{ origin, policy: bundle }, packet] = await Promise.all([
    get('/api/voting/policy'), get('/api/intake/' + id) as Promise<Pick<BallotPackage, 'manifest' | 'roster'>>,
  ])
  const chain = await verifyPolicyChain(bundle, origin)
  if (chain.voterRelease !== release) throw new Error('This voting release is not the one approved by the verifier quorum.')
  const { manifest, roster } = packet
  const policy = await authorizedBallot(chain, manifest)
  const { scope } = await verifyBallotContext(manifest, roster)
  element('ballot-title').textContent = manifest.title
  element('rationale').textContent = manifest.rationale
  const opens = Date.parse(manifest.opens_at)
  const closes = Date.parse(manifest.closes_at)
  if (Date.now() < opens || Date.now() >= closes) throw new Error(
    Date.now() < opens ? 'Voting opens ' + new Date(opens).toLocaleString() + '.' : 'Voting has closed.',
  )
  const first = new Option('Select a choice', '', true, true)
  first.disabled = true
  choice.append(first, ...ballotOptions(manifest).map((text, i) => new Option(text, String(i))))
  status.textContent = 'Voting closes ' + new Date(closes).toLocaleString() + '.'
  let proof: SemaphoreProof | undefined
  let busy = false
  async function submit() {
    if (busy) return
    busy = true
    retry.disabled = true
    ;(form.querySelector('button') as HTMLButtonElement).disabled = true
    try {
      if (!proof) {
        const file = backup.files?.[0]
        const selected = choice.value
        if (!file || file.size > 4096 || !selected) throw new Error('Choose your encrypted backup and a ballot choice.')
        status.textContent = 'Creating your anonymous proof in this browser…'
        const secret = password.value
        password.value = ''
        const credential = await unlockCredential(secret, await file.text())
        if (!roster.members.includes(credential.commitment)) throw new Error('This credential is not on the approved roster.')
        const directory = '/voting/release/' + release + '/'
        proof = await generateProof(Identity.import(credential.secret), new Group(roster.members), selected, scope, 10, {
          wasm: directory + 'semaphore-10.wasm', zkey: directory + 'semaphore-10.zkey',
        })
        form.hidden = true
      }
      status.textContent = 'Sending the same proof directly to the assigned verifiers…'
      const receipt = await submitToVerifiers(manifest, policy, origin, proof)
      status.textContent = 'A quorum of verifiers retained your vote. You can check it against the final certified tally after closing.'
      retry.hidden = true
      download.hidden = false
      download.onclick = () => {
        const url = URL.createObjectURL(new Blob([JSON.stringify({
          version: 'cope-quorum-relayed-vote/1', origin, manifest, roster, ...receipt,
        })], { type: 'application/json' }))
        const link = document.createElement('a')
        link.download = 'quorum-retention-' + id + '.json'
        link.href = url
        link.click()
        setTimeout(() => URL.revokeObjectURL(url), 1000)
      }
    } catch (error) {
      status.textContent = (error as Error).message + (proof ? ' Keep this tab open to retry the exact same proof.' : '')
      retry.hidden = !proof
    } finally {
      busy = false
      retry.disabled = false
      ;(form.querySelector('button') as HTMLButtonElement).disabled = false
    }
  }
  form.addEventListener('submit', event => { event.preventDefault(); void submit() })
  retry.addEventListener('click', () => { void submit() })
  form.hidden = false
}

void start().catch(error => { status.textContent = (error as Error).message })
