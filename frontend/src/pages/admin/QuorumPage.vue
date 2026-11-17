<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import api from '@/api/client'
import AdminPageHeader from '@/components/admin/AdminPageHeader.vue'
import InlineFeedback from '@/components/admin/InlineFeedback.vue'
import BaseButton from '@/components/ui/BaseButton.vue'

type Subject = 'engine' | 'member' | 'poll'
type Rule = 'majority' | 'unanimous_electorate' | 'unanimous_decisive' | 'report_only'
interface Draft {
  id: string
  title: string
  rationale: string
  subject: Subject
  action: 'include' | 'exclude' | null
  target: string | null
  rule: Rule
  quorum_percent: number | null
  opens_at: string
  closes_at: string
  options: string[]
}
interface Setup {
  revision: number
  enrollment_role_id: string | null
  drafts: Draft[]
}

const setup = ref<Setup>()
const quorumForm = ref<HTMLFormElement>()
type ElectionStatus = { enrolled: number; published: string[] }
const election = ref<ElectionStatus>()
const isPublished = computed(() => election.value?.published.includes(selected.value) ?? false)
const selected = ref('')
const role = ref('')
const roles = ref<{ id: string; name: string }[]>([])
const roleError = ref('')
const options = ref('')
const opening = ref('')
const closing = ref('')
const quorum = ref<number | ''>('')
const error = ref('')
const notice = ref('')
const busy = ref(false)
const draft = computed(() => setup.value?.drafts.find(item => item.id === selected.value))

function localTime(value: string) {
  const date = new Date(value)
  return new Date(date.valueOf() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16)
}

function selectDraft(id: string) {
  selected.value = id
  const active = draft.value
  if (!active) return
  options.value = active.options.join('\n')
  opening.value = localTime(active.opens_at)
  closing.value = localTime(active.closes_at)
  quorum.value = active.quorum_percent ?? ''
}

function addDraft() {
  if (!setup.value || setup.value.drafts.length >= 32) return
  const id = crypto.randomUUID()
  const now = new Date()
  setup.value.drafts.push({
    id, title: '', rationale: '', subject: 'engine', action: 'include', target: '',
    rule: 'majority', quorum_percent: null, opens_at: now.toISOString(),
    closes_at: new Date(now.valueOf() + 7 * 86400000).toISOString(), options: [],
  })
  selectDraft(id)
}

function syncDraft() {
  const active = draft.value
  if (!active) return
  active.opens_at = new Date(opening.value).toISOString()
  active.closes_at = new Date(closing.value).toISOString()
  if (active.subject === 'poll') {
    active.action = null
    active.target = null
    active.rule = 'report_only'
    active.options = options.value.split('\n').map(option => option.trim()).filter(Boolean)
    active.quorum_percent = null
  } else {
    active.action ??= 'include'
    if (active.rule === 'report_only') active.rule = 'majority'
    active.options = []
    active.quorum_percent = quorum.value === '' ? null : quorum.value
  }
}

function switchDraft(id: string) {
  try {
    syncDraft()
    selectDraft(id)
  } catch {
    error.value = 'Complete the opening and closing times before switching drafts.'
  }
}

function removeDraft() {
  if (!setup.value || !draft.value) return
  setup.value.drafts = setup.value.drafts.filter(item => item.id !== selected.value)
  selectDraft(setup.value.drafts[0]?.id ?? '')
}

async function loadRoles() {
  roleError.value = ''
  try {
    roles.value = (await api.get<{ roles: typeof roles.value }>('/api/admin/quorum/discord-roles')).roles
  } catch (cause) {
    roleError.value = cause instanceof Error ? cause.message : 'Could not load Discord roles.'
  }
}

function createDraft() {
  try {
    syncDraft()
    addDraft()
  } catch {
    error.value = 'Complete the opening and closing times before adding another draft.'
  }
}

async function loadElection() {
  election.value = await api.get<ElectionStatus>('/api/admin/quorum/status')
}

async function refreshStatus() {
  try {
    await loadElection()
  } catch (cause) {
    error.value = (cause as Error).message
  }
}

async function load() {
  error.value = ''
  try {
    await loadElection()
    setup.value = await api.get<Setup>('/api/admin/quorum')
    role.value = setup.value.enrollment_role_id ?? ''
    selectDraft(setup.value.drafts[0]?.id ?? '')
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : 'Could not load Quorum setup.'
  }
}

async function save(publish = false) {
  if (!setup.value) return
  if (publish && (isPublished.value || !quorumForm.value?.reportValidity())) return
  busy.value = true
  error.value = ''
  notice.value = ''
  try {
    syncDraft()
    setup.value = await api.put<Setup>('/api/admin/quorum', { body: {
      ...setup.value,
      enrollment_role_id: role.value.trim() || null,
    } })
    notice.value = 'Quorum settings saved.'
    if (publish) {
      await api.post('/api/admin/quorum/publish', { body: { draft_id: selected.value, revision: setup.value.revision } })
      notice.value = 'Ballot published.'
    }
    await loadElection()
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : 'Could not save Quorum setup.'
  } finally {
    busy.value = false
  }
}

onMounted(() => { void load(); void loadRoles() })
</script>

<template>
  <div class="page-stack">
    <AdminPageHeader title="Quorum" />
    <InlineFeedback :message="error" />
    <InlineFeedback :message="notice" tone="info" />
    <form v-if="setup" ref="quorumForm" class="section-stack" @submit.prevent="save()">
      <fieldset class="panel panel__body section-stack" :disabled="busy">
        <div class="toolbar toolbar--between">
          <h2>Voter enrollment</h2>
          <BaseButton size="small" @click="refreshStatus">Refresh count</BaseButton>
        </div>
        <p v-if="election" class="muted">{{ election.enrolled }} enrolled</p>
        <label class="field"><span>Discord role</span><select v-model="role" class="select" :disabled="Boolean(election?.enrolled)">
          <option value="">No enrollment role</option>
          <option v-if="role && !roles.some(item => item.id === role)" :value="role">Previously selected role</option>
          <option v-for="item in roles" :key="item.id" :value="item.id">{{ item.name }}</option>
        </select></label>
        <InlineFeedback :message="roleError" />
        <div v-if="roleError" class="button-row"><BaseButton size="small" @click="loadRoles">Retry role list</BaseButton></div>
      </fieldset>
      <fieldset class="panel panel__body section-stack" :disabled="busy">
        <div class="toolbar toolbar--between">
          <h2>Ballots</h2>
          <BaseButton size="small" :disabled="setup.drafts.length >= 32" @click="createDraft">New draft</BaseButton>
        </div>
        <template v-if="draft">
          <label class="field"><span>Draft</span><select class="select" :value="selected" @change="switchDraft(($event.target as HTMLSelectElement).value)">
            <option v-for="item in setup.drafts" :key="item.id" :value="item.id">{{ item.title || 'Untitled draft' }}</option>
          </select></label>
          <div v-if="isPublished" class="button-row">
            <BaseButton :to="{ name: 'quorum-ballot', params: { id: selected } }">Open published ballot</BaseButton>
          </div>
          <template v-else>
            <label class="field"><span>Title</span><input v-model="draft.title" class="input" required maxlength="160"></label>
            <label class="field"><span>Rationale</span><textarea v-model="draft.rationale" class="textarea" required maxlength="4000" /></label>
            <div class="form-grid">
              <label class="field"><span>Motion type</span><select v-model="draft.subject" class="select">
                <option value="engine">Engine decision</option><option value="member">Membership decision</option><option value="poll">Report-only poll</option>
              </select></label>
              <template v-if="draft.subject !== 'poll'">
                <label class="field"><span>Action</span><select v-model="draft.action" class="select" required>
                  <option value="include">Add or admit</option><option value="exclude">Remove</option>
                </select></label>
                <label class="field"><span>{{ draft.subject === 'member' ? 'Credential commitment' : 'Engine identifier' }}</span>
                  <input v-model="draft.target" class="input" required maxlength="128"></label>
                <label class="field"><span>Decision rule</span><select v-model="draft.rule" class="select" required>
                  <option value="majority">Simple majority</option><option value="unanimous_electorate">Unanimity of all eligible members</option>
                  <option value="unanimous_decisive">Unanimity of decisive votes</option>
                </select></label>
                <label class="field"><span>Minimum turnout (%)</span><input v-model.number="quorum" class="input" type="number" min="1" max="100" step="1" placeholder="Optional"></label>
              </template>
              <label class="field"><span>Opens</span><input v-model="opening" class="input" type="datetime-local" required></label>
              <label class="field"><span>Closes</span><input v-model="closing" class="input" type="datetime-local" required></label>
            </div>
            <label v-if="draft.subject === 'poll'" class="field"><span>Options, one per line</span><textarea v-model="options" class="textarea" required maxlength="2020" /></label>
            <div class="button-row">
              <BaseButton variant="primary" :disabled="!draft.title" @click="save(true)">Save and publish ballot</BaseButton>
              <BaseButton variant="ghost" size="small" @click="removeDraft">Remove draft</BaseButton>
            </div>
            <p v-if="election && election.enrolled < 3" class="muted">Publishing requires at least three enrolled voters.</p>
          </template>
        </template>
        <p v-else class="muted">No ballot drafts yet.</p>
      </fieldset>
      <div class="button-row"><BaseButton type="submit" variant="primary" :loading="busy">Save Quorum setup</BaseButton></div>
    </form>
    <div v-else-if="error" class="button-row"><BaseButton @click="load">Try again</BaseButton></div>
  </div>
</template>
