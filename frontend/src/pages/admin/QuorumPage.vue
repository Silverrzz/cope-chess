<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import api from '@/api/client'
import AdminPageHeader from '@/components/admin/AdminPageHeader.vue'
import InlineFeedback from '@/components/admin/InlineFeedback.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import { fingerprint, type Founding } from '@/quorum'
import { downloadQuorumFile } from '@/quorum-voter'

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
  witness_labels: Record<string, string>
  drafts: Draft[]
}

const setup = ref<Setup>()
type ElectionStatus = { genesis: Founding['genesis'] | null; signatures: Founding['signatures']; enrolled: number; published: string[] }
const election = ref<ElectionStatus>()
const foundingFingerprint = ref('')
const foundingApproved = computed(() => Boolean(election.value?.genesis && Object.keys(election.value.signatures).length >= election.value.genesis.threshold))
const isPublished = computed(() => election.value?.published.includes(selected.value) ?? false)
const selected = ref('')
interface DiscordUser { id: string; username: string; display_name: string }
const role = ref('')
const roles = ref<{ id: string; name: string }[]>([])
const roleError = ref('')
const nominees = ref<{ id: string; label: string }[]>([])
const registered = ref<string[]>([])
const witnessQuery = ref('')
const foundUsers = ref<DiscordUser[]>([])
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

watch(witnessQuery, (value, _, cleanup) => {
  const query = value.trim()
  const controller = new AbortController()
  foundUsers.value = []
  searching.value = false
  searchNotice.value = ''
  if (query.length < 2) return
  searching.value = true
  const timer = setTimeout(async () => {
    try {
      const response = await api.get<{ users: DiscordUser[] }>('/api/admin/quorum/discord-users', {
        query: { q: query }, signal: controller.signal,
      })
      if (controller.signal.aborted) return
      foundUsers.value = response.users
      if (!response.users.length) searchNotice.value = 'No guild members match.'
    } catch (cause) {
      if (!controller.signal.aborted) searchNotice.value = cause instanceof Error ? cause.message : 'Could not search the guild.'
    } finally {
      if (!controller.signal.aborted) searching.value = false
    }
  }, 300)
  cleanup(() => { clearTimeout(timer); controller.abort() })
})

function nominateUser(user: DiscordUser) {
  if (nominees.value.length >= 4 || nominees.value.some(item => item.id === user.id)) return
  nominees.value.push({ id: user.id, label: user.display_name })
  witnessQuery.value = ''
}

async function loadRoles() {
  roleError.value = ''
  try {
    roles.value = (await api.get<{ roles: typeof roles.value }>('/api/admin/quorum/discord-roles')).roles
  } catch (cause) {
    roleError.value = cause instanceof Error ? cause.message : 'Could not load Discord roles.'
  }
}

async function loadWitnesses() {
  try {
    registered.value = (await api.get<{ registered: string[] }>('/api/admin/quorum/witnesses')).registered
  } catch {
    registered.value = []
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
  foundingFingerprint.value = election.value.genesis ? await fingerprint(election.value.genesis) : ''
}

async function advance(action: 'freeze' | 'publish') {
  busy.value = true
  error.value = ''
  notice.value = ''
  try {
    if (action === 'freeze') {
      await api.post('/api/admin/quorum/founding')
      notice.value = 'Roster frozen. Send the founding evidence to the independent witnesses for approval.'
    } else {
      await api.post('/api/admin/quorum/publish', { body: { draft_id: selected.value } })
      notice.value = 'Ballot published for witness review. It appears in the public directory after three opening signatures.'
    }
    await loadElection()
  } catch (cause) {
    error.value = (cause as Error).message
  } finally {
    busy.value = false
  }
}

async function load() {
  error.value = ''
  try {
    await loadElection()
    setup.value = await api.get<Setup>('/api/admin/quorum')
    role.value = setup.value.enrollment_role_id ?? ''
    nominees.value = setup.value.witness_user_ids.map((id, index) => ({ id, label: setup.value?.witness_labels[id] ?? ("Witness " + (index + 1)) }))
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
      witness_user_ids: nominees.value.map(item => item.id),
      witness_labels: Object.fromEntries(nominees.value.map(item => [item.id, item.label])),
    } })
    await loadElection()
    await loadWitnesses()
    notice.value = 'Quorum settings saved.'
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : 'Could not save Quorum setup.'
  } finally {
    busy.value = false
  }
}

onMounted(() => { void load(); void loadRoles(); void loadWitnesses() })
</script>

<template>
  <div class="page-stack">
    <AdminPageHeader title="Quorum" description="Manage Discord enrollment, independent witnesses, and ballots." />
    <InlineFeedback :message="error" />
    <InlineFeedback :message="notice" tone="info" />
    <form v-if="setup" class="section-stack" @submit.prevent="save">
      <fieldset class="panel panel__body section-stack" :disabled="busy || Boolean(election?.genesis)">
        <h2>Founding eligibility</h2>
        <label class="field"><span>Discord role</span><select v-model="role" class="select">
          <option value="">No enrollment role</option>
          <option v-if="role && !roles.some(item => item.id === role)" :value="role">Previously selected role</option>
          <option v-for="item in roles" :key="item.id" :value="item.id">{{ item.name }}</option>
        </select></label>
        <InlineFeedback :message="roleError" />
        <div v-if="roleError" class="button-row"><BaseButton size="small" @click="loadRoles">Retry role list</BaseButton></div>
      </fieldset>
      <fieldset class="panel panel__body section-stack" :disabled="busy || Boolean(election?.genesis)">
        <div class="toolbar toolbar--between">
          <h2>Independent witnesses</h2>
          <BaseButton size="small" variant="ghost" :to="{ name: 'quorum-witness' }">Witness registration page</BaseButton>
        </div>
        <label class="field"><span>Find a member of the connected Discord server</span>
          <input v-model="witnessQuery" class="input" type="search" maxlength="64" placeholder="Username or server nickname"
            @keydown.enter.prevent>
        </label>
        <p v-if="searching || searchNotice" class="muted" role="status">{{ searching ? 'Searching Discord…' : searchNotice }}</p>
        <ul v-if="foundUsers.length" class="witness-list">
          <li v-for="user in foundUsers" :key="user.id" class="toolbar toolbar--between">
            <span><strong>{{ user.display_name }}</strong> <span class="muted">@{{ user.username }}</span></span>
            <BaseButton size="small" :disabled="nominees.length >= 4 || nominees.some(item => item.id === user.id)"
              @click="nominateUser(user)">Nominate</BaseButton>
          </li>
        </ul>
        <ul v-if="nominees.length" class="witness-list">
          <li v-for="nominee in nominees" :key="nominee.id" class="toolbar toolbar--between">
            <span><strong>{{ nominee.label }}</strong> <span class="muted">{{ registered.includes(nominee.id) ? 'Key registered' : 'Awaiting signing key' }}</span></span>
            <BaseButton size="small" variant="ghost" @click="nominees = nominees.filter(item => item.id !== nominee.id)">Remove</BaseButton>
          </li>
        </ul>
        <p v-else class="muted">No nominees yet.</p>
      </fieldset>
      <section v-if="election" class="panel panel__body section-stack">
        <div class="toolbar toolbar--between">
          <h2>Founding roster</h2>
          <BaseButton size="small" :disabled="busy" @click="load()">Refresh status</BaseButton>
        </div>
        <p>{{ election.enrolled }} enrolled · {{ registered.length }} of 4 witness keys registered</p>
        <div v-if="election.genesis" class="section-stack">
          <p>{{ Object.keys(election.signatures).length }} of 3 required signatures</p>
          <label class="field"><span>Proposed founding fingerprint</span><input class="input input--mono" :value="foundingFingerprint" readonly></label>
          <div class="button-row">
            <BaseButton @click="downloadQuorumFile('quorum-founding.json', JSON.stringify({ genesis: election.genesis, signatures: election.signatures }))">Download founding evidence</BaseButton>
          </div>
          <p class="muted">Each witness reviews the roster and shares the approved fingerprint through their own channel.</p>
        </div>
        <template v-else>
          <p class="muted">Save the Discord role and four witness nominations. Freeze the roster once eligible voters have enrolled.</p>
          <div class="button-row">
            <BaseButton :disabled="busy || election.enrolled < 3 || registered.length !== 4" @click="advance('freeze')">Freeze founding roster</BaseButton>
          </div>
        </template>
      </section>
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
            <BaseButton variant="ghost" size="small" @click="removeDraft">Remove draft</BaseButton>
            <BaseButton v-if="foundingApproved" size="small" :disabled="!draft.title" @click="advance('publish')">Publish saved draft</BaseButton>
          </div>
          </template>
        </template>
        <p v-else class="muted">No ballot drafts yet.</p>
      </fieldset>
      <div class="button-row"><BaseButton type="submit" variant="primary" :loading="busy">Save Quorum setup</BaseButton></div>
    </form>
    <div v-else-if="error" class="button-row"><BaseButton @click="load">Try again</BaseButton></div>
  </div>
</template>

<style scoped>
.witness-list {
  display: grid;
  gap: var(--space-2);
  list-style: none;
}
.witness-list li {
  padding-block: var(--space-2);
  border-block-start: 1px solid var(--color-border);
}
</style>