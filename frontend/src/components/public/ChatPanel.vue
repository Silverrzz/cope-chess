<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'

import { api } from '@/api/client'

import { errorMessage, formatDate } from './format'
import type { ChatMessage, ChatSettings, Identifier } from './types'

const props = withDefaults(defineProps<{
  messages?: ChatMessage[]
  settings?: ChatSettings
  tournamentId?: Identifier | null
  eventSlug?: string | null
}>(), {
  messages: () => [],
  settings: () => ({}),
  tournamentId: null,
  eventSlug: null,
})

const emit = defineEmits<{
  sent: [message: ChatMessage]
}>()

const STORAGE_KEY = 'cope.chat.displayName'
const log = ref<HTMLElement | null>(null)
const activeTab = ref<'chat' | 'system'>('chat')
const displayName = ref('')
const text = ref('')
const submitting = ref(false)
const submitError = ref('')

const orderedMessages = computed(() => [...props.messages].sort((left, right) => {
  const leftId = Number(left.id)
  const rightId = Number(right.id)
  if (Number.isFinite(leftId) && Number.isFinite(rightId)) return leftId - rightId
  const leftTime = Date.parse(left.at || '')
  const rightTime = Date.parse(right.at || '')
  if (Number.isFinite(leftTime) && Number.isFinite(rightTime)) return leftTime - rightTime
  return 0
}))
const chatMessages = computed(() => orderedMessages.value.filter((message) => message.display_name !== 'System'))
const systemMessages = computed(() => orderedMessages.value.filter((message) => message.display_name === 'System'))
const visibleMessages = computed(() => activeTab.value === 'chat' ? chatMessages.value : systemMessages.value)
const enabled = computed(() => props.settings.enabled !== false)
const maxLength = computed(() => Math.max(1, props.settings.max_message_length || 500))
const requiresName = computed(() => props.settings.allow_anonymous_names === false)
const reservedName = computed(() => displayName.value.trim().toLocaleLowerCase() === 'system')
const canSend = computed(() => (props.tournamentId !== null || !!props.eventSlug) && enabled.value && !submitting.value && !reservedName.value && text.value.trim() && (!requiresName.value || displayName.value.trim()))

onMounted(() => {
  try { displayName.value = localStorage.getItem(STORAGE_KEY) || '' } catch { /* Storage can be disabled. */ }
  scrollToLatest()
})

watch(() => [activeTab.value, visibleMessages.value.length], scrollToLatest)
watch(displayName, (value) => {
  try {
    if (value.trim()) localStorage.setItem(STORAGE_KEY, value.trim())
    else localStorage.removeItem(STORAGE_KEY)
  } catch { /* Storage can be disabled. */ }
})

async function submit(): Promise<void> {
  if (!canSend.value) return
  submitting.value = true
  submitError.value = ''
  const body = new FormData()
  body.set('display_name', displayName.value.trim())
  body.set('text', text.value.trim())
  try {
    const target = props.eventSlug
      ? `/api/events/${encodeURIComponent(props.eventSlug)}/chat`
      : `/api/tournaments/${encodeURIComponent(String(props.tournamentId))}/chat`
    const response = await api.post<{ message?: ChatMessage | null }>(target, { body })
    if (response.message) emit('sent', response.message)
    text.value = ''
    await scrollToLatest()
  } catch (error) {
    submitError.value = errorMessage(error, 'Your message could not be sent.')
  } finally {
    submitting.value = false
  }
}

async function scrollToLatest(): Promise<void> {
  await nextTick()
  if (log.value) log.value.scrollTop = log.value.scrollHeight
}
</script>

<template>
  <section id="chat" class="chat-panel" aria-label="Chat and system log">
    <header class="chat-tabs" role="tablist" aria-label="Activity feed">
      <button
        id="chat-tab"
        type="button"
        role="tab"
        :aria-selected="activeTab === 'chat'"
        aria-controls="chat-feed"
        @click="activeTab = 'chat'"
      >
        Chat <span>{{ chatMessages.length }}</span>
      </button>
      <button
        id="system-log-tab"
        type="button"
        role="tab"
        :aria-selected="activeTab === 'system'"
        aria-controls="system-log-feed"
        @click="activeTab = 'system'"
      >
        System log <span>{{ systemMessages.length }}</span>
      </button>
    </header>

    <div
      :id="activeTab === 'chat' ? 'chat-feed' : 'system-log-feed'"
      ref="log"
      class="chat-log"
      role="tabpanel"
      :aria-labelledby="activeTab === 'chat' ? 'chat-tab' : 'system-log-tab'"
      aria-live="polite"
      aria-relevant="additions"
    >
      <ol v-if="visibleMessages.length">
        <li v-for="(message, index) in visibleMessages" :key="message.id ?? `${message.at}-${index}`" :class="{ 'chat-message--system': activeTab === 'system' }">
          <div>
            <strong v-if="activeTab === 'chat'">{{ message.display_name }}</strong>
            <time v-if="message.at" :datetime="message.at">{{ formatDate(message.at, true) }}</time>
          </div>
          <p>{{ message.text }}</p>
        </li>
      </ol>
      <p v-else class="chat-empty">{{ activeTab === 'chat' ? 'No chat messages yet.' : 'No system events yet.' }}</p>
    </div>

    <form v-if="activeTab === 'chat' && enabled" class="chat-form" autocomplete="off" @submit.prevent="submit">
      <label>
        <span>Name{{ requiresName ? '' : ' (optional)' }}</span>
        <input v-model="displayName" name="display_name" maxlength="40" autocomplete="nickname" :required="requiresName" placeholder="Your name">
        <small v-if="reservedName" class="chat-error" role="alert">System is a reserved display name.</small>
      </label>
      <label>
        <span>Message</span>
        <textarea v-model="text" name="text" :maxlength="maxLength" rows="2" required placeholder="Write a message"></textarea>
      </label>
      <div class="chat-form__footer">
        <span>{{ text.length }} / {{ maxLength }}</span>
        <button type="submit" :disabled="!canSend">
          {{ submitting ? 'Sending...' : 'Send' }}
        </button>
      </div>
      <p v-if="submitError" class="chat-error" role="alert">{{ submitError }}</p>
    </form>
    <p v-else-if="activeTab === 'chat'" class="chat-disabled">Chat is currently closed.</p>
  </section>
</template>

<style scoped>
.chat-panel {
  display: flex;
  min-height: 0;
  flex-direction: column;
  overflow: hidden;
  border: 1px solid var(--color-border, #d5dbe1);
  border-radius: var(--radius-md, 0.5rem);
  background: var(--color-surface, #fff);
}

.chat-tabs {
  display: flex;
  gap: 0.15rem;
  padding: 0.2rem;
  border-block-end: 1px solid var(--color-border, #d5dbe1);
  background: color-mix(in srgb, var(--color-bg, #f5f7f9) 72%, var(--color-surface, #fff));
}

.chat-tabs button {
  display: inline-flex;
  min-height: 1.7rem;
  align-items: center;
  gap: 0.35rem;
  padding: 0.15rem 0.5rem;
  border: 0;
  border-radius: var(--radius-sm, 0.35rem);
  background: transparent;
  color: var(--color-text-muted, #607080);
  font: inherit;
  font-size: 0.7rem;
  font-weight: 750;
  cursor: pointer;
}

.chat-tabs button[aria-selected='true'] {
  background: var(--color-surface, #fff);
  color: var(--color-text, #17202a);
  box-shadow: 0 0 0 1px var(--color-border, #d5dbe1);
}

.chat-tabs button:focus-visible {
  outline: 2px solid var(--color-accent, #2f78c4);
  outline-offset: 1px;
}

.chat-tabs span {
  min-width: 1rem;
  padding: 0.05rem 0.25rem;
  border-radius: 999px;
  background: color-mix(in srgb, var(--color-border, #d5dbe1) 60%, transparent);
  font-size: 0.6rem;
  line-height: 1.2;
  text-align: center;
}

.chat-log {
  min-height: 0;
  flex: 1;
  overflow-y: auto;
  padding: 0.6rem 0.75rem;
  scrollbar-gutter: stable;
  overscroll-behavior: contain;
}

.chat-log ol {
  display: grid;
  gap: 0.7rem;
  margin: 0;
  padding: 0;
  list-style: none;
}

.chat-log li > div {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 0.45rem;
}

.chat-log strong {
  font-size: 0.77rem;
}

.chat-log time {
  color: var(--color-text-muted, #607080);
  font-size: 0.62rem;
}

.chat-log p {
  margin: 0.16rem 0 0;
  font-size: 0.78rem;
  line-height: 1.4;
  overflow-wrap: anywhere;
}

.chat-log .chat-message--system {
  padding-inline-start: 0.55rem;
  border-inline-start: 2px solid color-mix(in srgb, var(--color-accent, #2f78c4) 45%, transparent);
}

.chat-log .chat-message--system time {
  font-variant-numeric: tabular-nums;
}

.chat-empty,
.chat-disabled {
  color: var(--color-text-muted, #607080);
  text-align: center;
}

.chat-form {
  display: grid;
  gap: 0.55rem;
  padding: 0.7rem;
  border-block-start: 1px solid var(--color-border, #d5dbe1);
  background: color-mix(in srgb, var(--color-bg, #f5f7f9) 72%, var(--color-surface, #fff));
}

.chat-form label {
  display: grid;
  gap: 0.22rem;
  color: var(--color-text-muted, #607080);
  font-size: 0.67rem;
  font-weight: 700;
}

.chat-form input,
.chat-form textarea {
  width: 100%;
  box-sizing: border-box;
  padding: 0.48rem 0.58rem;
  border: 1px solid var(--color-border, #d5dbe1);
  border-radius: var(--radius-sm, 0.35rem);
  background: var(--color-surface, #fff);
  color: var(--color-text, #17202a);
  font: inherit;
  font-size: 0.78rem;
  resize: vertical;
}

.chat-form input:focus,
.chat-form textarea:focus {
  border-color: var(--color-accent, #2f78c4);
  outline: 2px solid color-mix(in srgb, var(--color-accent, #2f78c4) 22%, transparent);
}

.chat-form__footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.7rem;
}

.chat-form__footer > span {
  color: var(--color-text-muted, #607080);
  font-size: 0.65rem;
}

.chat-form button {
  min-height: 2.15rem;
  padding-inline: 0.85rem;
  border: 0;
  border-radius: var(--radius-sm, 0.35rem);
  background: var(--color-primary, var(--color-accent, #2f78c4));
  color: var(--color-on-primary, var(--color-on-accent, #fff));
  font: inherit;
  font-size: 0.75rem;
  font-weight: 750;
  cursor: pointer;
}

.chat-form button:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.chat-error {
  margin: 0;
  color: var(--color-danger, #b42318);
  font-size: 0.72rem;
}

.chat-disabled {
  margin: 0;
  padding: 1rem;
  font-size: 0.78rem;
}
</style>
