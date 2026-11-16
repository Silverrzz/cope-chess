<script setup lang="ts">
import { computed, onBeforeUnmount, ref, shallowRef, watch } from 'vue'
import { RouterLink } from 'vue-router'
import api from '@/api/client'
import {
  ballotManifest, ballotTool, downloadJson, errorText, publicFormApi, readJson, submitBallot,
  type BallotOpening, type Detail, type Envelope, type Manifest, type Receipt, type VoterProof,
} from '@/api/ballots'
import { digest, verifyReceipt, voterProof } from '@/api/ballot-protocol'
import BallotStatus from '@/components/forms/BallotStatus.vue'
import AppIcon from '@/components/ui/AppIcon.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import { useSessionStore } from '@/stores/session'

const props = defineProps<{ slug: string }>()
const session = useSessionStore()
const form = shallowRef<Detail | null>(null)
const manifest = shallowRef<Manifest | null>(null)
const eligible = shallowRef<VoterProof | null>(null)
const envelope = shallowRef<Envelope | null>(null)
const opening = shallowRef<BallotOpening | null>(null)
const receipt = shallowRef<Receipt | null>(null)
const choices = ref<number[]>([])
const loading = ref(false)
const busy = ref('')
const error = ref('')
const actionError = ref('')
const verified = ref('')
const offline = ref(false)
const attempted = ref(false)
let worker: Worker | null = null

const path = computed(() => publicFormApi(props.slug))
const allSelected = computed(() => form.value?.questions.every((_, index) => choices.value[index] !== undefined))

watch(() => props.slug, async (slug, _, cleanup) => {
  let current = true
  cleanup(() => { current = false; worker?.terminate(); worker = null })
  form.value = null
  manifest.value = null
  eligible.value = null
  envelope.value = null
  opening.value = null
  receipt.value = null
  choices.value = []
  busy.value = ''
  error.value = ''
  actionError.value = ''
  verified.value = ''
  attempted.value = false
  offline.value = false
  loading.value = true
  try {
    const detail = await api.get<Detail>(publicFormApi(slug))
    if (current) form.value = detail
  } catch (cause) {
    if (current) error.value = errorText(cause)
  } finally {
    if (current) loading.value = false
  }
}, { immediate: true })

onBeforeUnmount(() => worker?.terminate())

function refresh() {
  void act('refresh', async slug => {
    const update = await api.get<Detail>(publicFormApi(slug))
    if (slug === props.slug) form.value = update
  })
}

async function getManifest(slug: string) {
  if (manifest.value?.slug === slug) return manifest.value
  const expected = form.value?.manifest_digest
  const document = await ballotManifest(slug)
  if (document.slug !== slug || document.protocol !== 'cope-secret-ballot-v1' || await digest(document) !== expected) {
    throw new Error('The published ballot manifest failed verification.')
  }
  if (slug === props.slug) manifest.value = document
  return document
}

async function act(label: string, task: (slug: string) => Promise<void>) {
  if (busy.value) return
  const slug = props.slug
  busy.value = label
  actionError.value = ''
  try {
    await task(slug)
  } catch (cause) {
    if (slug === props.slug) actionError.value = errorText(cause)
  } finally {
    if (slug === props.slug) busy.value = ''
  }
}

function attachProof(event: Event) {
  const input = event.target as HTMLInputElement
  void act('proof', async slug => {
    const file = voterProof(await readJson(input, 4096), slug)
    const document = await getManifest(slug)
    if (!document.roster.includes(file.public)) throw new Error('This enrollment code is missing from the published roster.')
    if (slug === props.slug) eligible.value = file
  })
}

function seal() {
  void act('seal', async slug => {
    const proof = eligible.value
    if (!proof || !allSelected.value) return
    const document = await getManifest(slug)
    if (slug !== props.slug) return
    const result = await new Promise<{ envelope: Envelope; opening: BallotOpening }>((resolve, reject) => {
      const thread = new Worker(new URL('../../api/ballot.worker.ts', import.meta.url), { type: 'module' })
      worker = thread
      function done() {
        thread.terminate()
        if (worker === thread) worker = null
      }
      thread.onmessage = ({ data }) => {
        done()
        if (data.error) reject(new Error(data.error))
        else resolve(data)
      }
      thread.onerror = () => {
        done()
        reject(new Error('The browser could not prepare a ballot. Use the offline tool to prepare it.'))
      }
      thread.postMessage({ manifest: document, proof, choices: [...choices.value] })
    })
    if (slug === props.slug) {
      envelope.value = result.envelope
      opening.value = result.opening
      eligible.value = null
      choices.value = []
    }
  })
}

function attachEnvelope(event: Event) {
  const input = event.target as HTMLInputElement
  void act('offline', async slug => {
    const file = await readJson(input) as Partial<Envelope> | null
    if (file?.manifest !== form.value?.manifest_digest || !file ||
        typeof file.credential !== 'string' || !/^[a-f0-9]{64}$/.test(file.credential) ||
        typeof file.signature !== 'string' || !/^[a-f0-9]{128}$/.test(file.signature) ||
        !Array.isArray(file.questions) || Object.keys(file).sort().join(',') !== 'credential,manifest,questions,signature' ||
        file.questions.some(question => !question || Object.keys(question).sort().join(',') !== 'cells,proof' ||
          !Array.isArray(question.cells) || !validProof(question.proof, 2) ||
          question.cells.some(cell => !Array.isArray(cell) || cell.length !== 3 ||
            !hexNumber(cell[0]) || !hexNumber(cell[1]) || !validProof(cell[2], 4)))) {
      throw new Error('Choose the encrypted ballot file for this form.')
    }
    if (slug === props.slug) envelope.value = file as Envelope
  })
}

function hexNumber(value: unknown): boolean {
  return typeof value === 'string' && /^(0|[1-9a-f][0-9a-f]{0,511})$/.test(value)
}

function validProof(value: unknown, size: number): boolean {
  return Array.isArray(value) && value.length === size && value.every(hexNumber)
}

function submit() {
  void act('submit', async slug => {
    const prepared = envelope.value
    if (!prepared) return
    const document = await getManifest(slug)
    attempted.value = true
    const accepted = await submitBallot(slug, prepared, Boolean(session.session?.privatise_platform))
    await verifyReceipt(document, accepted)
    if (accepted.fingerprint !== await digest(prepared)) throw new Error('Cope returned a receipt for a different ballot.')
    if (slug === props.slug) {
      receipt.value = accepted
      envelope.value = null
    }
  })
}

function checkReceipt(event: Event) {
  const input = event.target as HTMLInputElement
  verified.value = ''
  void act('check', async slug => {
    const file = await readJson(input) as (Partial<Receipt> & Partial<Envelope>) | null
    if (!file || file.manifest !== form.value?.manifest_digest) throw new Error('Choose a receipt, private opening or encrypted ballot for this form.')
    const document = await getManifest(slug)
    const isEnvelope = 'questions' in file && 'credential' in file
    const fingerprint = isEnvelope ? await digest(file) : file.fingerprint
    if (typeof fingerprint !== 'string' || !/^[a-f0-9]{64}$/.test(fingerprint)) throw new Error('This file has no valid ballot fingerprint.')
    if (!isEnvelope && typeof file.signature === 'string') await verifyReceipt(document, file as Receipt)
    const accepted = await api.get<Receipt>(publicFormApi(slug) + '/receipts/' + fingerprint)
    await verifyReceipt(document, accepted)
    if (accepted.fingerprint !== fingerprint) throw new Error('Cope returned a receipt for a different ballot.')
    if (slug === props.slug) {
      verified.value = 'The ballot board contains this receipt.'
      receipt.value = accepted
      const local = file as Partial<BallotOpening>
      if (Array.isArray(local.openings)) opening.value = local as BallotOpening
      else if (opening.value?.fingerprint !== accepted.fingerprint) opening.value = null
      envelope.value = null
      eligible.value = null
      choices.value = []
    }
  })
}

function saveOpening() {
  if (opening.value) {
    downloadJson(props.slug + '-private-choice', {
      ...opening.value,
      ...(receipt.value?.fingerprint === opening.value.fingerprint ? receipt.value : {}),
    })
  }
}

function clearPrepared() {
  envelope.value = null
  opening.value = null
  attempted.value = false
  actionError.value = ''
}

function percentage(count: number, total: number): string {
  return (total ? 100 * count / total : 0).toFixed(1).replace(/\.0$/, '')
}
</script>

<template>
  <div class="ballot-shell page-container">
    <RouterLink class="ballot-back" to="/forms"><AppIcon name="arrow-left" :size="16" /> Forms</RouterLink>
    <p v-if="loading" role="status">Loading form…</p>
    <p v-else-if="error" class="ballot-message ballot-message--error" role="alert">{{ error }}</p>
    <template v-else-if="form">
      <header class="ballot-hero">
        <div>
          <BallotStatus :status="form.status" />
          <h1>{{ form.title }}</h1>
          <p v-if="form.description" class="ballot-description">{{ form.description }}</p>
        </div>
        <div class="ballot-actions ballot-turnout">
          <span class="ballot-count"><strong>{{ form.ballot_count }}</strong> {{ form.ballot_count === 1 ? 'ballot' : 'ballots' }} accepted</span>
          <BaseButton v-if="form.status === 'open' || form.status === 'tallying'" size="small" variant="ghost" icon-only
            :disabled="Boolean(busy)" :loading="busy === 'refresh'"
            @click="refresh">
            <template #icon><AppIcon name="refresh" :size="16" /></template>
            Refresh form
          </BaseButton>
        </div>
      </header>
      <div class="ballot-columns">
        <div class="ballot-stack">
          <template v-if="form.results">
            <section v-for="(question, index) in form.questions" :key="index" class="ballot-panel ballot-results">
              <h2>{{ question.title }}</h2>
              <div v-for="(option, optionIndex) in question.options" :key="option" class="ballot-result">
                <div><strong>{{ option }}</strong><span>{{ form.results[index]?.[optionIndex] ?? 0 }} votes · {{ percentage(form.results[index]?.[optionIndex] ?? 0, form.ballot_count) }}%</span></div>
                <div class="ballot-meter" aria-hidden="true"><span :style="{ width: percentage(form.results[index]?.[optionIndex] ?? 0, form.ballot_count) + '%' }" /></div>
              </div>
            </section>
          </template>
          <section v-else-if="form.status !== 'open'" class="ballot-panel ballot-empty">
            <AppIcon :name="form.status === 'sealed' ? 'archive' : 'clock'" :size="25" />
            <h2>{{ form.status === 'sealed' ? 'Results are sealed' : 'Results are being tallied' }}</h2>
            <p>{{ form.status === 'sealed' ? 'This form received fewer than ' + form.minimum + ' ballots, its minimum for publishing results.' : 'Voting is closed. The aggregate will be published when both trustees submit a verified tally share.' }}</p>
          </section>

          <form v-else-if="eligible && !envelope && !receipt && !offline" class="ballot-stack" @submit.prevent="seal">
            <fieldset v-for="(question, index) in form.questions" :key="index" class="ballot-panel ballot-question" :disabled="Boolean(busy)">
              <legend>{{ question.title }}</legend>
              <label v-for="(option, optionIndex) in question.options" :key="option" class="ballot-option">
                <input v-model="choices[index]" type="radio" :name="'question-' + index" :value="optionIndex" required>
                <span>{{ option }}</span>
              </label>
            </fieldset>
            <BaseButton type="submit" variant="primary" :disabled="!allSelected || Boolean(busy)" :loading="busy === 'seal'">
              {{ busy === 'seal' ? 'Encrypting on this device…' : 'Prepare encrypted ballot' }}
            </BaseButton>
          </form>
          <section v-else-if="!receipt && !envelope" class="ballot-panel ballot-stack">
            <div class="ballot-section-heading"><AppIcon name="key" /><h2>Cast a ballot</h2></div>
            <template v-if="!offline">
              <p>Select your registered voter proof from this device. Your browser uses it locally to sign one encrypted ballot.</p>
              <label class="ballot-field ballot-file">Voter proof<input type="file" accept=".json,application/json" :disabled="Boolean(busy)" @change="attachProof"></label>
              <BaseButton variant="ghost" :disabled="Boolean(busy)" @click="offline = true">Use an offline ballot</BaseButton>
            </template>
            <template v-else>
              <p>Choose an encrypted ballot prepared by the offline tool. Keep the private opening on your device.</p>
              <label class="ballot-field ballot-file">Encrypted ballot<input type="file" accept=".json,application/json" :disabled="Boolean(busy)" @change="attachEnvelope"></label>
              <BaseButton variant="ghost" :disabled="Boolean(busy)" @click="offline = false">Use a voter proof</BaseButton>
            </template>
          </section>

          <section v-if="envelope" class="ballot-panel ballot-stack">
            <div class="ballot-section-heading"><AppIcon name="check-circle" /><h2>Encrypted ballot ready</h2></div>
            <p>One ballot can be accepted per enrollment code. Save the encrypted file to retry if the connection drops.</p>
            <div class="ballot-actions">
              <BaseButton variant="primary" :loading="busy === 'submit'" :disabled="Boolean(busy)" @click="submit">
                {{ attempted ? 'Retry the same ballot' : 'Submit encrypted ballot' }}
              </BaseButton>
              <BaseButton @click="downloadJson(slug + '-encrypted-ballot', envelope)">Save encrypted ballot</BaseButton>
              <BaseButton v-if="opening" @click="saveOpening">Save private opening</BaseButton>
              <BaseButton v-if="!attempted" variant="ghost" :disabled="Boolean(busy)" @click="clearPrepared">Start over</BaseButton>
            </div>
          </section>
          <section v-if="receipt" class="ballot-panel ballot-stack">
            <div class="ballot-section-heading"><AppIcon name="check-circle" /><h2>{{ verified ? 'Receipt verified' : 'Ballot accepted' }}</h2></div>
            <p v-if="verified" role="status">{{ verified }}</p>
            <div class="ballot-receipts">
              <div>
                <h3>Inclusion receipt</h3>
                <p>Use this signed receipt to check that your encrypted ballot appears on the board. It contains no choices.</p>
                <BaseButton @click="downloadJson(slug + '-inclusion-receipt', receipt)">Download inclusion receipt</BaseButton>
              </div>
              <div v-if="opening">
                <h3>Private choice proof</h3>
                <p>This file reveals your choices. Share it only when you want to prove how this enrollment code voted.</p>
                <BaseButton @click="saveOpening">Download private choice proof</BaseButton>
              </div>
            </div>
          </section>
          <p v-if="actionError" class="ballot-message ballot-message--error" role="alert">{{ actionError }}</p>
        </div>

        <aside class="ballot-stack ballot-aside">
          <section class="ballot-panel ballot-stack">
            <div class="ballot-section-heading"><AppIcon name="book-open" /><h2>Verify this form</h2></div>
            <p>Each ballot has a zero-knowledge proof that it contains one valid choice per question. The trustees release aggregate totals after voting closes.</p>
            <a :href="path + '/manifest?download=true'">Download ballot manifest <AppIcon name="download" :size="15" /></a>
            <a v-if="form.board_digest" :href="path + '/audit'">Download audit archive <AppIcon name="download" :size="15" /></a>
            <a :href="ballotTool">Download offline voting tool <AppIcon name="download" :size="15" /></a>
            <label class="ballot-field ballot-file">Check an inclusion receipt or encrypted ballot<input type="file" accept=".json,application/json" :disabled="Boolean(busy)" @change="checkReceipt"></label>
            <details>
              <summary>Fingerprints and verification</summary>
              <dl class="ballot-fingerprints">
                <dt>Manifest fingerprint</dt><dd>{{ form.manifest_digest }}</dd>
                <template v-if="form.board_digest"><dt>Closed board fingerprint</dt><dd>{{ form.board_digest }}</dd></template>
              </dl>
              <p>Save the manifest fingerprint when voting opens. Compare the closed board fingerprint with other voters when it closes. The offline tool can verify the archive, signed receipts and disclosed choices using those fingerprints.</p>
              <p>The tool requires Python 3.11+ and <code>cryptography</code> 43 or newer. Use <code>--help</code> for usage.</p>
              <pre v-if="form.board_digest" class="ballot-command">python cope_ballot.py verify {{ slug }}-audit.json MANIFEST_FINGERPRINT BOARD_FINGERPRINT</pre>
            </details>
          </section>
        </aside>
      </div>
    </template>
  </div>
</template>
