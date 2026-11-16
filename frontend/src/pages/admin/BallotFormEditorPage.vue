<script setup lang="ts">
import { computed, ref, shallowRef, watch } from 'vue'
import { onBeforeRouteLeave, onBeforeRouteUpdate, RouterLink, useRouter } from 'vue-router'
import {
  adminFormApi, ballotApi, ballotTool, errorText, publicFormApi, readJson,
  type AdminDetail, type BallotSettings, type Trustee,
} from '@/api/ballots'
import api from '@/api/client'
import BallotStatus from '@/components/forms/BallotStatus.vue'
import AppIcon from '@/components/ui/AppIcon.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import { useConfirm } from '@/composables/useConfirm'

const props = defineProps<{ slug?: string }>()
const router = useRouter()
const { confirm } = useConfirm()
const model = shallowRef<AdminDetail | null>(null)
const slugInput = ref('')
const title = ref('')
const description = ref('')
const questions = ref<BallotSettings['questions']>([{ title: '', options: ['', ''] }])
const roster = ref('')
const trustees = ref<Trustee[]>([])
const minimum = ref(5)
const original = ref('')
const tab = ref<'questions' | 'voters' | 'trustees'>('questions')
const busy = ref('')
const error = ref('')
const message = ref('')
const canEdit = computed(() => !props.slug || model.value?.status === 'draft')
const rosterKeys = computed(() => roster.value.trim().toLowerCase().split(/\s+/).filter(Boolean))
const draft = computed<BallotSettings>(() => ({
  title: title.value, description: description.value, questions: questions.value,
  roster: rosterKeys.value, trustees: trustees.value, minimum: minimum.value,
}))
const dirty = computed(() => canEdit.value && JSON.stringify(draft.value) !== original.value)
const endpoint = computed(() => props.slug ? adminFormApi(props.slug) : ballotApi + '/admin')
const publicEndpoint = computed(() => publicFormApi(props.slug ?? ''))
const enrollmentPage = computed(() => router.resolve({ name: 'voter-proof', params: { slug: props.slug } }).href)
const ready = computed(() => trustees.value.length === 2 && rosterKeys.value.length >= minimum.value)

function apply(updated: AdminDetail | null = null) {
  model.value = updated
  slugInput.value = updated?.slug ?? ''
  title.value = updated?.settings.title ?? ''
  description.value = updated?.settings.description ?? ''
  questions.value = updated?.settings.questions.map(question => ({ title: question.title, options: [...question.options] })) ?? [{ title: '', options: ['', ''] }]
  roster.value = updated?.settings.roster.join('\n') ?? ''
  trustees.value = [...(updated?.settings.trustees ?? [])]
  minimum.value = updated?.settings.minimum ?? 5
  original.value = JSON.stringify(draft.value)
}

async function action(label: string, run: () => Promise<void>) {
  if (busy.value) return
  busy.value = label
  error.value = ''
  message.value = ''
  try {
    await run()
  } catch (cause) {
    error.value = errorText(cause)
  } finally {
    busy.value = ''
  }
}

watch(() => props.slug, async slug => {
  tab.value = 'questions'
  busy.value = ''
  error.value = ''
  message.value = ''
  apply()
  if (slug) await action('load', async () => apply(await api.get<AdminDetail>(adminFormApi(slug))))
}, { immediate: true })

function validateDraft() {
  if (!title.value.trim() || questions.value.some(question => !question.title.trim() ||
      question.options.some(option => !option.trim()) ||
      new Set(question.options.map(option => option.trim().toLowerCase())).size !== question.options.length)) {
    tab.value = 'questions'
    throw new Error('Enter a title and complete each question with distinct options.')
  }
  if (!Number.isInteger(minimum.value) || minimum.value < 3 || minimum.value > 200 ||
      rosterKeys.value.length > 200 || rosterKeys.value.some(key => !/^[a-f0-9]{64}$/.test(key)) ||
      new Set(rosterKeys.value).size !== rosterKeys.value.length) {
    tab.value = 'voters'
    throw new Error('Enter up to 200 distinct public enrollment codes and a whole-number publication minimum from 3 to 200.')
  }
}

function save() {
  void action('save', async () => {
    validateDraft()
    if (model.value && props.slug) {
      apply(await api.put<AdminDetail>(endpoint.value, { body: { revision: model.value.revision, settings: draft.value } }))
      message.value = 'Draft saved.'
      return
    }
    const slug = slugInput.value.trim()
    if (!/^[a-z0-9]+(-[a-z0-9]+)*$/.test(slug)) throw new Error('Use lowercase words separated by hyphens for the URL.')
    const created = await api.post<{ slug: string }>(endpoint.value, { body: { slug, settings: draft.value } })
    original.value = JSON.stringify(draft.value)
    await router.replace({ name: 'admin-ballot-form', params: { slug: created.slug } })
  })
}

async function leaveDraft(): Promise<boolean> {
  return !dirty.value || await confirm({ title: 'Discard draft changes?', message: 'Save your draft to keep these edits.', confirmLabel: 'Discard changes' })
}

onBeforeRouteLeave(leaveDraft)
onBeforeRouteUpdate(leaveDraft)

async function copyEnrollment() {
  await action('copy', async () => {
    await navigator.clipboard.writeText(new URL(enrollmentPage.value, window.location.origin).href)
    message.value = 'Enrollment link copied.'
  })
}

function hexNumber(value: unknown): boolean {
  return typeof value === 'string' && /^(0|[1-9a-f][0-9a-f]{0,511})$/.test(value)
}

function isProof(value: unknown): boolean {
  return Array.isArray(value) && value.length === 2 && value.every(hexNumber)
}

function addTrustee(event: Event) {
  const input = event.target as HTMLInputElement
  void action('trustee', async () => {
    const entry = await readJson(input, 8192) as Partial<Trustee> | null
    if (!entry || !hexNumber(entry.public) || !isProof(entry.proof) || Object.keys(entry).sort().join(',') !== 'proof,public') {
      throw new Error('Choose the public trustee registration file generated for this form.')
    }
    if (trustees.value.some(trustee => trustee.public === entry.public)) throw new Error('This trustee key is already in the draft.')
    trustees.value = [...trustees.value, entry as Trustee]
  })
}

async function openVoting() {
  if (!model.value || dirty.value || !ready.value) return
  if (!await confirm({
    title: 'Open the ballot form?',
    message: 'This fixes the questions, roster and trustee keys. Anyone with the link can view the form, and enrolled voters can submit.',
    confirmLabel: 'Open voting',
  })) return
  await action('open', async () => {
    apply(await api.post<AdminDetail>(endpoint.value + '/open', { body: { revision: model.value?.revision } }))
    message.value = 'Voting is open.'
  })
}

async function closeVoting() {
  if (!model.value) return
  const belowMinimum = model.value.ballot_count < model.value.settings.minimum
  if (!await confirm({
    title: 'Close voting permanently?',
    message: belowMinimum ? 'Current turnout is below your publication minimum. If it stays below the minimum at closing, the results will remain sealed.' :
      'This freezes the encrypted ballot board and makes it available for the trustees to verify and tally.',
    confirmLabel: 'Close voting',
  })) return
  await action('close', async () => {
    apply(await api.post<AdminDetail>(endpoint.value + '/close'))
    message.value = 'Voting is closed.'
  })
}

function setListing(listed: boolean) {
  void action('visibility', async () => {
    const updated = await api.put<AdminDetail>(endpoint.value + '/visibility', { body: { listed } })
    model.value = updated
    message.value = listed ? 'Directory listing enabled.' : 'Directory listing disabled.'
  })
}

function importShare(event: Event) {
  const input = event.target as HTMLInputElement
  void action('share', async () => {
    const share = await readJson(input, 96 * 1024) as { trustee?: unknown; board_digest?: unknown; factors?: unknown } | null
    if (!share || share.board_digest !== model.value?.board_digest || !hexNumber(share.trustee) ||
        Object.keys(share).sort().join(',') !== 'board_digest,factors,trustee' ||
        !Array.isArray(share.factors) || share.factors.some(row => !Array.isArray(row) ||
          row.some(entry => !Array.isArray(entry) || entry.length !== 2 || !hexNumber(entry[0]) || !isProof(entry[1])))) {
      throw new Error('Choose a public tally share created for this closed board.')
    }
    const updated = await api.post<AdminDetail>(endpoint.value + '/shares', { body: share })
    apply(updated)
    message.value = updated.status === 'published' ? 'Both shares verified. Aggregate results are published.' : 'Trustee share verified.'
  })
}
</script>

<template>
  <div class="ballot-shell">
    <RouterLink class="ballot-back" to="/admin/forms"><AppIcon name="arrow-left" :size="16" /> Forms</RouterLink>
    <header class="ballot-hero">
      <div>
        <BallotStatus v-if="model" :status="model.status" />
        <h1>{{ model?.title ?? (slug ? 'Ballot form' : 'New ballot form') }}</h1>
        <p v-if="!slug">Create a draft, then enroll voters and register the independent trustees.</p>
        <p v-else-if="model">{{ model.ballot_count }} accepted · {{ model.settings.roster.length }} eligible voters</p>
      </div>
      <BaseButton v-if="slug && model?.manifest_digest" :to="{ name: 'secret-ballot', params: { slug } }">
        <template #icon><AppIcon name="external-link" :size="16" /></template>
        View public form
      </BaseButton>
    </header>

    <p v-if="busy === 'load'" role="status">Loading ballot form…</p>
    <p v-if="error" class="ballot-message ballot-message--error" role="alert">{{ error }}</p>
    <p v-if="message" class="ballot-message" role="status">{{ message }}</p>

    <div v-if="!slug || model" class="ballot-columns">
      <div class="ballot-stack">
        <template v-if="canEdit">
          <nav v-if="slug" class="ballot-tabs" aria-label="Ballot setup">
            <button type="button" :aria-current="tab === 'questions' ? 'step' : undefined" @click="tab = 'questions'">Questions</button>
            <button type="button" :aria-current="tab === 'voters' ? 'step' : undefined" @click="tab = 'voters'">Voters <span>{{ rosterKeys.length }}</span></button>
            <button type="button" :aria-current="tab === 'trustees' ? 'step' : undefined" @click="tab = 'trustees'">Trustees <span>{{ trustees.length }}/2</span></button>
          </nav>
          <form class="ballot-stack" @submit.prevent="save">
            <fieldset class="ballot-editor-fieldset ballot-stack" :disabled="Boolean(busy)">
              <template v-if="tab === 'questions'">
                <section class="ballot-panel ballot-stack">
                  <h2>Form details</h2>
                  <label class="ballot-field">Title<input v-model="title" class="input" maxlength="200" placeholder="Community rules vote"></label>
                  <label v-if="!slug" class="ballot-field">URL name<input v-model="slugInput" class="input" maxlength="80" placeholder="community-rules" required></label>
                  <label class="ballot-field">Description<textarea v-model="description" rows="3" class="input" maxlength="3000"></textarea></label>
                </section>
                <section v-for="(question, index) in questions" :key="index" class="ballot-panel ballot-stack">
                  <div class="ballot-section-heading ballot-section-heading--split">
                    <h2>Question {{ index + 1 }}</h2>
                    <BaseButton v-if="questions.length > 1" size="small" variant="ghost" @click="questions.splice(index, 1)" :aria-label="'Remove question ' + (index + 1)">Remove</BaseButton>
                  </div>
                  <label class="ballot-field">Question text<input v-model="question.title" class="input" maxlength="200"></label>
                  <div v-for="(_, optionIndex) in question.options" :key="optionIndex" class="ballot-option-editor">
                    <label class="ballot-field">Option {{ optionIndex + 1 }}<input v-model="question.options[optionIndex]" class="input" maxlength="200"></label>
                    <BaseButton v-if="question.options.length > 2" size="small" variant="ghost" icon-only @click="question.options.splice(optionIndex, 1)">
                      <template #icon><AppIcon name="trash" :size="16" /></template>
                      Remove option {{ optionIndex + 1 }}
                    </BaseButton>
                  </div>
                  <BaseButton v-if="question.options.length < 6" size="small" variant="secondary" @click="question.options.push('')">Add option</BaseButton>
                </section>
                <BaseButton v-if="questions.length < 4" variant="secondary" @click="questions.push({ title: '', options: ['', ''] })">
                  <template #icon><AppIcon name="plus" :size="16" /></template>
                  Add question
                </BaseButton>
              </template>
              <section v-else-if="tab === 'voters'" class="ballot-panel ballot-stack">
                <h2>Voter roster</h2>
                <p>Share the enrollment link with eligible voters. Each voter sends you their public enrollment code. Keep a separate record of who is eligible and enroll one code per voter.</p>
                <div class="ballot-actions">
                  <BaseButton :to="enrollmentPage">Open enrollment page</BaseButton>
                  <BaseButton @click="copyEnrollment">Copy enrollment link</BaseButton>
                </div>
                <label class="ballot-field">Public enrollment codes<textarea v-model="roster" class="input ballot-code" rows="9" maxlength="14000" spellcheck="false" placeholder="Paste one enrollment code per line"></textarea></label>
                <p>{{ rosterKeys.length }} enrollment {{ rosterKeys.length === 1 ? 'code' : 'codes' }}</p>
                <label class="ballot-field">Minimum ballots to publish results<input v-model.number="minimum" class="input" type="number" min="3" max="200" step="1"></label>
                <p>Closing below this minimum keeps results sealed.</p>
              </section>
              <section v-else class="ballot-panel ballot-stack">
                <h2>Trustee keys</h2>
                <p>Ask two people to generate separate keys offline. Each trustee keeps their private key and sends you only their public registration file.</p>
                <a :href="ballotTool">Download offline voting tool <AppIcon name="download" :size="15" /></a>
                <p>Each trustee runs this command with Python 3.11+ and <code>cryptography</code> 43 or newer.</p>
                <pre class="ballot-command">python cope_ballot.py trustee {{ slug }} trustee-private.json trustee-public.json</pre>
                <ul v-if="trustees.length" class="ballot-admin-list">
                  <li v-for="(trustee, index) in trustees" :key="trustee.public">
                    <div><strong>Trustee {{ index + 1 }}</strong><small class="ballot-code">{{ trustee.public.slice(0, 18) }}…</small></div>
                    <BaseButton size="small" variant="ghost" @click="trustees.splice(index, 1)">Remove</BaseButton>
                  </li>
                </ul>
                <label v-if="trustees.length < 2" class="ballot-field ballot-file">Add public trustee registration<input type="file" accept=".json,application/json" @change="addTrustee"></label>
              </section>
            </fieldset>
            <div class="ballot-actions">
              <BaseButton type="submit" variant="primary" :loading="busy === 'save'" :disabled="Boolean(busy) || Boolean(slug && !dirty)">
                {{ slug ? 'Save draft' : 'Create draft' }}
              </BaseButton>
              <span v-if="slug && dirty" class="ballot-muted">Unsaved edits</span>
            </div>
          </form>
        </template>

        <template v-else-if="model">
          <section class="ballot-panel ballot-stack">
            <h2>Published questions</h2>
            <p v-if="model.description" class="ballot-description">{{ model.description }}</p>
            <div v-for="(question, index) in model.settings.questions" :key="index" class="ballot-published-question">
              <h3>{{ question.title }}</h3>
              <ul><li v-for="option in question.options" :key="option">{{ option }}</li></ul>
            </div>
          </section>
          <section v-if="model.board_digest" class="ballot-panel ballot-stack">
            <h2>Closed ballot board</h2>
            <a :href="publicEndpoint + '/audit'">Download encrypted audit archive <AppIcon name="download" :size="15" /></a>
            <dl class="ballot-fingerprints">
              <dt>Closed board fingerprint</dt><dd>{{ model.board_digest }}</dd>
            </dl>
            <template v-if="model.status === 'tallying'">
              <p>Have both trustees compare the closed board fingerprint with each other and with voters, then verify the archive against the manifest fingerprint saved when voting opened. Each trustee sends one public share.</p>
              <pre class="ballot-command">python cope_ballot.py share {{ slug }}-audit.json MANIFEST_FINGERPRINT BOARD_FINGERPRINT trustee-private.json trustee-share.json</pre>
              <p>{{ model.trustees_submitted.length }} of 2 verified shares received. Results publish automatically after both shares pass verification.</p>
              <label class="ballot-field ballot-file">Import public tally share<input type="file" accept=".json,application/json" :disabled="Boolean(busy)" @change="importShare"></label>
            </template>
            <p v-else-if="model.status === 'sealed'">Turnout was below the minimum of {{ model.settings.minimum }}. The board remains encrypted.</p>
            <p v-else>Both trustee shares verified. The audit archive includes the proofs and published totals.</p>
          </section>
        </template>
      </div>

      <aside v-if="model" class="ballot-stack ballot-aside">
        <section class="ballot-panel ballot-stack">
          <h2>Voting</h2>
          <template v-if="canEdit">
            <ul class="ballot-checklist">
              <li><AppIcon :name="rosterKeys.length >= minimum ? 'check-circle' : 'clock'" :size="16" />{{ rosterKeys.length }} enrolled · {{ minimum }} minimum</li>
              <li><AppIcon :name="trustees.length === 2 ? 'check-circle' : 'clock'" :size="16" />{{ trustees.length }} of 2 trustees added</li>
              <li><AppIcon :name="dirty ? 'clock' : 'check-circle'" :size="16" />{{ dirty ? 'Save your draft' : 'Draft saved' }}</li>
            </ul>
            <BaseButton variant="primary" :disabled="dirty || !ready || Boolean(busy)" :loading="busy === 'open'" @click="openVoting">Open voting</BaseButton>
          </template>
          <template v-else>
            <dl class="ballot-fingerprints"><dt>Manifest fingerprint</dt><dd>{{ model.manifest_digest }}</dd></dl>
            <a :href="publicEndpoint + '/manifest?download=true'">Download ballot manifest <AppIcon name="download" :size="15" /></a>
            <p v-if="model.status === 'open'">Share the manifest fingerprint with voters and both trustees so they can save it independently.</p>
            <div v-if="model.status === 'open'" class="ballot-actions">
              <BaseButton :disabled="Boolean(busy)" :loading="busy === 'refresh'" @click="action('refresh', async () => apply(await api.get<AdminDetail>(endpoint)))">Refresh turnout</BaseButton>
              <BaseButton variant="danger" :disabled="Boolean(busy)" :loading="busy === 'close'" @click="closeVoting">Close voting</BaseButton>
            </div>
          </template>
        </section>
        <section class="ballot-panel ballot-stack">
          <h2>Directory visibility</h2>
          <label class="ballot-toggle">
            <input type="checkbox" :checked="model.listed" :disabled="Boolean(busy)" @change="setListing(($event.target as HTMLInputElement).checked)">
            Show on the public Forms page
          </label>
          <p>Opened forms can be shared by link. The directory only displays forms with listing enabled.</p>
        </section>
      </aside>
    </div>
  </div>
</template>
