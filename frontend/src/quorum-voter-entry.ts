import { Group } from '@semaphore-protocol/group'
import { Identity } from '@semaphore-protocol/identity'
import { generateProof, type SemaphoreProof } from '@semaphore-protocol/proof'
import { unlockCredential } from './quorum-credential.ts'
import { authorizedBallot, verifyPolicyChain } from './quorum-history.ts'
import { verifyBallotContext, type BallotPackage } from './quorum.ts'
import { pendingVote, resumeVote } from './quorum-pending.ts'
import { submitToVerifiers } from './quorum-relay.ts'

const element = <T extends HTMLElement>(id: string) => document.getElementById(id) as T
const app = element('voter')
const form = element<HTMLFormElement>('vote-form')
const status = element('vote-status')
const password = element<HTMLInputElement>('password')
const backup = element<HTMLInputElement>('backup')
const choice = element<HTMLSelectElement>('choice')
const send = element<HTMLButtonElement>('retry')
const download = element<HTMLButtonElement>('download')

function save(name: string, value: object) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(value)], { type: 'application/json' }))
  const link = document.createElement('a')
  link.download = name
  link.href = url
  link.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

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
  const policy = await authorizedBallot(chain, manifest, id)
  const { options, scope } = await verifyBallotContext(manifest, roster)
  element('ballot-title').textContent = manifest.title
  element('rationale').textContent = manifest.rationale
  const opens = Date.parse(manifest.opens_at)
  const closes = Date.parse(manifest.closes_at)
  if (Date.now() < opens) throw new Error('Voting opens ' + new Date(opens).toLocaleString() + '.')

  // Keep recovery controls in the approved JS so it works with existing verifier HTML.
  const restore = document.createElement('label')
  restore.textContent = 'Resume a previously saved vote'
  const input = document.createElement('input')
  input.type = 'file'
  input.accept = '.json,application/json'
  restore.append(input)
  form.before(restore)
  const first = new Option('Select a choice', '', true, true)
  first.disabled = true
  choice.append(first, ...options.map((text, i) => new Option(text, String(i))))
  const prepare = form.querySelector('button')!
  prepare.textContent = 'Prepare vote'
  form.hidden = Date.now() >= closes
  status.textContent = form.hidden ? 'Voting has closed. You can still retry a saved submission or recover an existing receipt.'
    : 'Voting closes ' + new Date(closes).toLocaleString() + '.'

  let proof: SemaphoreProof | undefined
  let busy = false
  const run = async (action: () => Promise<void>) => {
    if (busy) return
    busy = true
    input.disabled = prepare.disabled = send.disabled = true
    try { await action() } catch (error) { status.textContent = (error as Error).message }
    finally { busy = false; input.disabled = prepare.disabled = send.disabled = false }
  }

  function ready(value: SemaphoreProof, alreadySaved: boolean) {
    proof = value
    form.hidden = restore.hidden = true
    send.textContent = 'Send saved vote'
    send.hidden = !alreadySaved
    status.textContent = 'Choice: ' + options[Number(value.message)] + '. ' + (alreadySaved
      ? 'Send this exact proof to resume your submission.' : 'Nothing has been sent. Save your recovery file first. It reveals your choice and lets its holder submit this vote.')
  }
  form.addEventListener('submit', event => {
    event.preventDefault()
    void run(async () => {
      if (Date.now() >= closes) throw new Error('Voting has closed. Resume a saved vote instead.')
      const file = backup.files?.[0]
      const selected = choice.value
      if (!file || file.size > 4096 || !selected) throw new Error('Choose your encrypted backup and a ballot choice.')
      status.textContent = 'Creating your anonymous proof in this browser…'
      const secret = password.value
      password.value = ''
      const credential = await unlockCredential(secret, await file.text())
      if (!roster.members.includes(credential.commitment)) throw new Error('This credential is not on the approved roster.')
      const directory = '/voting/release/' + release + '/'
      const value = await generateProof(Identity.import(credential.secret), new Group(roster.members), selected, scope, 10, {
        wasm: directory + 'semaphore-10.wasm', zkey: directory + 'semaphore-10.zkey',
      })
      const pending = await pendingVote(origin, manifest, value)
      ready(value, false)
      download.hidden = false
      download.textContent = 'Save vote recovery file'
      download.onclick = () => {
        save('quorum-pending-' + id + '.json', pending)
        send.hidden = false
        status.textContent = 'Once the file is saved, send your vote. Keep the file until you have your receipt.'
      }
    })
  })
  input.addEventListener('change', () => { void run(async () => {
    const file = input.files?.[0]
    input.value = ''
    if (!file || file.size > 16384) throw new Error('Choose a vote recovery file up to 16 KiB.')
    ready(await resumeVote(JSON.parse(await file.text()), origin, manifest, roster, options, scope), true)
  }) })
  send.addEventListener('click', () => { void run(async () => {
    if (!proof) return
    status.textContent = 'Sending the same proof directly to the assigned verifiers…'
    const receipt = await submitToVerifiers(manifest, policy, origin, proof)
    send.hidden = true
    status.textContent = 'A quorum of verifiers retained your vote. Download your receipt to check it against the final tally.'
    download.hidden = false
    download.textContent = 'Download retention receipt'
    download.onclick = () => save('quorum-retention-' + id + '.json', {
      version: 'cope-quorum-relayed-vote/1', origin, manifest, roster, ...receipt,
    })
  }) })
}

void start().catch(error => { status.textContent = (error as Error).message })
