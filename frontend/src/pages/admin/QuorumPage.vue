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
  witness_user_ids: string[]
  drafts: Draft[]
}

const setup = ref<Setup>()
const selected = ref('')
const role = ref('')
const witnesses = ref('')
const witnessQuery = ref('')
const foundUsers = ref<{ id: string; username: string; display_name: string }[]>([])
const matchingUser = ref('')
const searching = ref(false)
const searchNotice = ref('')
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

async function searchUsers() {
  const query = witnessQuery.value.trim()
  if (query.length < 2) return
  searching.value = true
  searchNotice.value = ''
  matchingUser.value = ''
  foundUsers.value = []
  try {
    const response = await api.get<{ users: typeof foundUsers.value }>('/api/admin/quorum/discord-users', { query: { q: query } })
    foundUsers.value = response.users
    if (!response.users.length) searchNotice.value = 'No connected Discord accounts match.'
  } catch (cause) {
    searchNotice.value = cause instanceof Error ? cause.message : 'Could not search Discord accounts.'
  } finally {
    searching.value = false
  }
}

function nominateUser() {
  if (!matchingUser.value) return
  const ids = new Set(witnesses.value.split(/\s+/).filter(Boolean))
  ids.add(matchingUser.value)
  witnesses.value = [...ids].join('\n')
  matchingUser.value = ''
}

function createDraft() {
  try {
    syncDraft()
    addDraft()
  } catch {
    error.value = 'Complete the opening and closing times before adding another draft.'
  }
}

async function load() {
  error.value = ''
  try {
    setup.value = await api.get<Setup>('/api/admin/quorum')
    role.value = setup.value.enrollment_role_id ?? ''
    witnesses.value = setup.value.witness_user_ids.join('\n')
    selectDraft(setup.value.drafts[0]?.id ?? '')
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : 'Could not load Quorum setup.'
  }
}

async function save() {
  if (!setup.value) return
  busy.value = true
  error.value = ''
  notice.value = ''
  try {
    syncDraft()
    setup.value = await api.put<Setup>('/api/admin/quorum', { body: {
      ...setup.value,
      enrollment_role_id: role.value.trim() || null,
      witness_user_ids: witnesses.value.split(/\s+/).filter(Boolean),
    } })
    notice.value = 'Quorum setup saved.'
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : 'Could not save Quorum setup.'
  } finally {
    busy.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="page-stack">
    <AdminPageHeader title="Quorum" description="Configure founding eligibility and prepare ballot drafts." />
    <InlineFeedback :message="error" />
    <InlineFeedback :message="notice" tone="info" />
    <form v-if="setup" class="section-stack" @submit.prevent="save">
      <fieldset class="panel panel__body section-stack" :disabled="busy">
        <h2>Founding eligibility</h2>
        <label class="field"><span>Discord role ID</span><input v-model="role" class="input" inputmode="numeric" maxlength="20"></label>
        <p class="muted">Eligibility uses the guild connected in Discord settings. Role holders still need to register a credential before they can join the founding roster.</p>
        <div class="form-grid">
          <label class="field"><span>Find a connected Discord account</span>
            <input v-model="witnessQuery" class="input" type="search" maxlength="64" @keydown.enter.prevent="searchUsers">
          </label>
          <div class="button-row"><BaseButton :disabled="witnessQuery.trim().length < 2" :loading="searching" @click="searchUsers">Search accounts</BaseButton></div>
        </div>
        <p v-if="searchNotice" class="muted" role="status">{{ searchNotice }}</p>
        <div v-if="foundUsers.length" class="form-grid">
          <label class="field"><span>Account</span><select v-model="matchingUser" class="select">
            <option value="">Choose an account</option>
            <option v-for="user in foundUsers" :key="user.id" :value="user.id">{{ user.display_name }} (@{{ user.username }})</option>
          </select></label>
          <div class="button-row"><BaseButton :disabled="!matchingUser" @click="nominateUser">Add nominee</BaseButton></div>
        </div>
        <label class="field"><span>Witness nominees</span><textarea v-model="witnesses" class="textarea" rows="3" maxlength="400" placeholder="Discord user IDs, one per line" /></label>
        <p class="muted">Nominees need independent operators and trusted signing keys to witness the public record. This list records nominations for setup.</p>
      </fieldset>
      <fieldset class="panel panel__body section-stack" :disabled="busy">
        <div class="toolbar toolbar--between">
          <h2>Ballot drafts</h2>
          <BaseButton size="small" :disabled="setup.drafts.length >= 32" @click="createDraft">New draft</BaseButton>
        </div>
        <template v-if="draft">
          <label class="field"><span>Draft</span><select class="select" :value="selected" @change="switchDraft(($event.target as HTMLSelectElement).value)">
            <option v-for="item in setup.drafts" :key="item.id" :value="item.id">{{ item.title || 'Untitled draft' }}</option>
          </select></label>
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
          <div class="button-row"><BaseButton variant="ghost" size="small" @click="removeDraft">Remove draft</BaseButton></div>
        </template>
        <p v-else class="muted">No ballot drafts yet.</p>
      </fieldset>
      <div class="button-row"><BaseButton type="submit" variant="primary" :loading="busy">Save Quorum setup</BaseButton></div>
    </form>
    <div v-else-if="error" class="button-row"><BaseButton @click="load">Try again</BaseButton></div>
  </div>
</template>
