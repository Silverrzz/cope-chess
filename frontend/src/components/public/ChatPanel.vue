<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter, type RouteLocationRaw } from 'vue-router'

import { api } from '@/api/client'
import AppIcon from '@/components/ui/AppIcon.vue'
import { useConfirm } from '@/composables/useConfirm'
import { useToast } from '@/composables/useToast'
import { chatViewerId, savedViewerName, saveViewerName } from '@/composables/useViewerPresence'
import { useViewerSettings } from '@/composables/useViewerSettings'
import { useSessionStore } from '@/stores/session'

import { errorMessage, formatDate } from './format'
import type { ChatMessage, ChatSettings, Identifier } from './types'

const props = withDefaults(defineProps<{
  messages?: ChatMessage[]
  settings?: ChatSettings
  tournamentId?: Identifier | null
  eventSlug?: string | null
  gameId?: Identifier | null
  navigateGame?: (gameId: Identifier) => void
}>(), {
  messages: () => [],
  settings: () => ({}),
  tournamentId: null,
  eventSlug: null,
  gameId: null,
})

const emit = defineEmits<{
  sent: [message: ChatMessage]
}>()

const log = ref<HTMLElement | null>(null)
const composer = ref<HTMLTextAreaElement | null>(null)
const menuElement = ref<HTMLElement | null>(null)
const menu = ref<{ message: ChatMessage; anchor: HTMLElement; x: number; y: number } | null>(null)
const replyId = ref<Identifier | null>(null)
const deletedIds = ref(new Set<string>())
const showJumpToLatest = ref(false)
const activeTab = ref<'chat' | 'system'>('chat')
const displayName = ref('')
const text = ref('')
const submitting = ref(false)
const submitError = ref('')
const route = useRoute()
const router = useRouter()
const session = useSessionStore()
const { confirm } = useConfirm()
const toast = useToast()
const { chatGameLinksVisible } = useViewerSettings()
const isAdmin = computed(() => session.user?.role === 'admin')

const orderedMessages = computed(() => props.messages.filter((message) => !deletedIds.value.has(String(message.id))).sort((left, right) => {
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
const messagesById = computed(() => new Map(chatMessages.value.map((message) => [String(message.id), message])))
const replyingTo = computed(() => replyId.value === null ? undefined : messagesById.value.get(String(replyId.value)))
const chatRows = computed(() => visibleMessages.value.map((message, index, messages) => {
  const previous = messages[index - 1]
  const continued = activeTab.value === 'chat' && previous?.display_name === message.display_name
    && (message.sender_key || previous.sender_key
      ? message.sender_key === previous.sender_key
      : message.display_name !== 'Anonymous')
  return {
    message,
    continued,
    showGame: chatGameLinksVisible.value && message.game_id != null
      && (!continued || String(previous?.game_id) !== String(message.game_id)),
    reply: message.reply_to_id == null ? undefined : messagesById.value.get(String(message.reply_to_id)),
  }
}))
const enabled = computed(() => props.settings.enabled !== false)
const maxLength = computed(() => Math.max(1, props.settings.max_message_length || 500))
const requiresName = computed(() => props.settings.allow_anonymous_names === false)
const reservedName = computed(() => displayName.value.trim().toLocaleLowerCase() === 'system')
const canSend = computed(() => (props.tournamentId !== null || !!props.eventSlug) && enabled.value && !submitting.value && !reservedName.value && text.value.trim() && (!requiresName.value || displayName.value.trim()))

onMounted(() => {
  displayName.value = savedViewerName()
  scrollToLatest()
  document.addEventListener('pointerdown', dismissMenuOnOutsideClick)
})
onBeforeUnmount(() => document.removeEventListener('pointerdown', dismissMenuOnOutsideClick))

watch([activeTab, visibleMessages], async ([tab], [previousTab]) => {
  if (tab !== previousTab || (menu.value && !visibleMessages.value.includes(menu.value.message))) closeMenu()
  const followLatest = tab !== previousTab || isAtBottom()
  await nextTick()
  if (followLatest) scrollToLatest()
  else updateScrollPosition()
})
watch(displayName, saveViewerName)

async function submit(): Promise<void> {
  if (!canSend.value) return
  submitting.value = true
  submitError.value = ''
  const body = new FormData()
  body.set('display_name', displayName.value.trim())
  body.set('text', text.value.trim())
  body.set('viewer_id', chatViewerId())
  if (props.gameId !== null) body.set('game_id', String(props.gameId))
  if (replyingTo.value && replyId.value !== null) body.set('reply_to_id', String(replyId.value))
  try {
    const target = props.eventSlug
      ? `/api/events/${encodeURIComponent(props.eventSlug)}/chat`
      : `/api/tournaments/${encodeURIComponent(String(props.tournamentId))}/chat`
    const response = await api.post<{ message?: ChatMessage | null }>(target, { body })
    if (response.message) emit('sent', response.message)
    text.value = ''
    replyId.value = null
  } catch (error) {
    submitError.value = errorMessage(error, 'Your message could not be sent.')
  } finally {
    submitting.value = false
  }
}

function gameTarget(gameId: Identifier): RouteLocationRaw {
  if (props.eventSlug) return {
    name: 'event-arena',
    params: { slug: props.eventSlug },
    query: { ...(route.name === 'event-arena' ? route.query : {}), game_id: String(gameId) },
  }
  return {
    name: 'tournament',
    params: { id: String(props.tournamentId) },
    query: { ...route.query, game_id: String(gameId) },
  }
}

function jumpToGame(gameId: Identifier | null | undefined, event?: MouseEvent): void {
  if (gameId == null || !chatGameLinksVisible.value) return
  if (event && (event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey)) return
  event?.preventDefault()
  closeMenu()
  if (props.navigateGame) props.navigateGame(gameId)
  else void router.push(gameTarget(gameId))
}

async function openMenu(event: MouseEvent | KeyboardEvent, message: ChatMessage): Promise<void> {
  if (activeTab.value !== 'chat' || message.id === undefined) return
  event.preventDefault()
  const anchor = event.currentTarget as HTMLElement
  const bounds = anchor.getBoundingClientRect()
  const next = {
    message,
    anchor,
    x: event instanceof MouseEvent ? event.clientX : bounds.left,
    y: event instanceof MouseEvent ? event.clientY : bounds.bottom,
  }
  menu.value = next
  await nextTick()
  const element = menuElement.value
  if (!element || menu.value?.anchor !== anchor) return
  menu.value = {
    ...next,
    x: Math.max(4, Math.min(next.x, window.innerWidth - element.offsetWidth - 4)),
    y: Math.max(4, Math.min(next.y, window.innerHeight - element.offsetHeight - 4)),
  }
  element.querySelector<HTMLElement>('button:not(:disabled)')?.focus()
}

function handleMessageKey(event: KeyboardEvent, message: ChatMessage): void {
  if (event.key === 'ContextMenu' || (event.shiftKey && event.key === 'F10')) void openMenu(event, message)
}

function closeMenu(restoreFocus = false): void {
  const anchor = menu.value?.anchor
  menu.value = null
  if (restoreFocus) anchor?.focus()
}

function dismissMenuOnOutsideClick(event: PointerEvent): void {
  if (menu.value && !menuElement.value?.contains(event.target as Node)) closeMenu()
}

async function replyToMessage(message: ChatMessage): Promise<void> {
  if (!enabled.value || message.id === undefined) return
  replyId.value = message.id
  closeMenu()
  await nextTick()
  composer.value?.focus()
}

async function moderateMessage(message: ChatMessage, action: 'delete' | 'block'): Promise<void> {
  if (!isAdmin.value || message.id === undefined) return
  closeMenu()
  const blocking = action === 'block'
  const accepted = await confirm({
    title: blocking ? 'Block this IP from chat?' : 'Delete chat message?',
    message: blocking
      ? `Block new chat messages from the IP address used by ${message.display_name}?`
      : `Remove the message from ${message.display_name} for all viewers?`,
    confirmLabel: blocking ? 'Block IP' : 'Delete message',
    tone: 'danger',
  })
  if (!accepted) return
  const path = `/api/admin/chat/messages/${encodeURIComponent(String(message.id))}`
  try {
    const response = blocking
      ? await api.post<{ message: string }>(`${path}/block`)
      : await api.delete<{ message: string }>(path)
    if (!blocking) deletedIds.value.add(String(message.id))
    toast.success(response.message)
  } catch (error) {
    toast.error(error)
  }
}

function isAtBottom(): boolean {
  const feed = log.value
  return !feed || feed.scrollHeight - feed.clientHeight - feed.scrollTop <= 2
}

function updateScrollPosition(): void {
  showJumpToLatest.value = !isAtBottom()
}

function scrollToLatest(): void {
  if (log.value) log.value.scrollTop = log.value.scrollHeight
  updateScrollPosition()
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

    <div class="chat-feed">
      <div
        :id="activeTab === 'chat' ? 'chat-feed' : 'system-log-feed'"
        ref="log"
        class="chat-log"
        role="tabpanel"
        :aria-labelledby="activeTab === 'chat' ? 'chat-tab' : 'system-log-tab'"
        aria-live="polite"
        aria-relevant="additions"
        @scroll.passive="updateScrollPosition"
      >
        <ol v-if="visibleMessages.length">
          <li
            v-for="({ message, continued, showGame, reply }, index) in chatRows"
            :key="message.id ?? `${message.at}-${index}`"
            :class="{ 'chat-message--system': activeTab === 'system', 'chat-message--continued': continued }"
            :tabindex="activeTab === 'chat' ? 0 : undefined"
            @contextmenu="openMenu($event, message)"
            @keydown="handleMessageKey($event, message)"
          >
            <div v-if="!continued || showGame" class="chat-message__header">
              <strong v-if="activeTab === 'chat' && !continued">{{ message.display_name }}</strong>
              <span v-if="showGame && message.game_id != null" class="chat-message__game">
                {{ continued ? 'Viewing' : '– viewing' }}
                <a :href="router.resolve(gameTarget(message.game_id)).href" @click="jumpToGame(message.game_id, $event)">
                  {{ message.game_label || `Game ${message.game_id}` }}
                </a>
              </span>
              <time v-if="!continued && message.at" :datetime="message.at">{{ formatDate(message.at, true) }}</time>
            </div>
            <div v-if="message.reply_to_id != null" class="chat-message__reply">
              <template v-if="reply">Replying to <strong>{{ reply.display_name }}</strong><span>{{ reply.text }}</span></template>
              <template v-else>Original message unavailable</template>
            </div>
            <p>{{ message.text }}</p>
          </li>
        </ol>
        <p v-else class="chat-empty">{{ activeTab === 'chat' ? 'No chat messages yet.' : 'No system events yet.' }}</p>
      </div>
      <button
        v-if="showJumpToLatest"
        type="button"
        class="chat-jump"
        aria-label="Scroll to latest"
        title="Scroll to latest"
        @click="scrollToLatest"
      >
        <AppIcon name="chevron-down" :size="16" />
      </button>
    </div>

    <form v-if="activeTab === 'chat' && enabled" class="chat-form" autocomplete="off" @submit.prevent="submit">
      <div v-if="replyingTo" class="chat-form__reply">
        <span>Replying to <strong>{{ replyingTo.display_name }}</strong></span>
        <button type="button" aria-label="Cancel reply" @click="replyId = null; composer?.focus()">
          <AppIcon name="close" :size="14" />
        </button>
      </div>
      <label>
        <span>Name{{ requiresName ? '' : ' (optional)' }}</span>
        <input v-model="displayName" name="display_name" maxlength="40" autocomplete="nickname" :required="requiresName" placeholder="Your name">
        <small v-if="reservedName" class="chat-error" role="alert">System is a reserved display name.</small>
      </label>
      <label>
        <span>Message</span>
        <textarea ref="composer" v-model="text" name="text" :maxlength="maxLength" rows="2" required placeholder="Write a message" @keydown.enter.prevent="submit"></textarea>
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

    <Teleport to="body">
      <div
        v-if="menu"
        ref="menuElement"
        class="chat-menu"
        :style="{ left: `${menu.x}px`, top: `${menu.y}px` }"
        role="group"
        aria-label="Chat message actions"
        @keydown.esc.stop="closeMenu(true)"
      >
        <button v-if="chatGameLinksVisible" type="button" :disabled="menu.message.game_id == null" @click="jumpToGame(menu.message.game_id)">Jump to game</button>
        <button type="button" :disabled="!enabled" @click="replyToMessage(menu.message)">Reply</button>
        <template v-if="isAdmin">
          <button type="button" class="chat-menu__danger" @click="moderateMessage(menu.message, 'delete')">Delete</button>
          <button type="button" class="chat-menu__danger" @click="moderateMessage(menu.message, 'block')">Block IP</button>
        </template>
      </div>
    </Teleport>
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

.chat-feed {
  position: relative;
  display: flex;
  min-height: 0;
  flex: 1;
  flex-direction: column;
}

.chat-log {
  min-height: 0;
  flex: 1;
  overflow-y: auto;
  padding: 0.6rem 0.75rem;
  scrollbar-gutter: stable;
  overscroll-behavior: contain;
}

.chat-jump {
  position: absolute;
  inset-block-end: 0.5rem;
  inset-inline-end: 1.25rem;
  display: grid;
  width: 2rem;
  height: 2rem;
  place-items: center;
  padding: 0;
  border: 1px solid var(--color-border, #d5dbe1);
  border-radius: 50%;
  background: var(--color-surface, #fff);
  color: var(--color-text-muted, #607080);
  cursor: pointer;
}

.chat-jump:hover {
  color: var(--color-text, #17202a);
}

.chat-jump:focus-visible {
  outline: 2px solid var(--color-accent, #2f78c4);
  outline-offset: 1px;
}

.chat-log ol {
  margin: 0;
  padding: 0;
  list-style: none;
}

.chat-log li + li {
  margin-block-start: 0.7rem;
}

.chat-log li.chat-message--continued {
  margin-block-start: 0.1rem;
}

.chat-message__header {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 0.45rem;
}

.chat-message__game,
.chat-message__reply {
  color: var(--color-text-muted);
  font-size: 0.7rem;
  overflow-wrap: anywhere;
}

.chat-message__game a {
  color: var(--color-accent);
  text-decoration: underline;
  text-underline-offset: 0.14em;
}

.chat-message__reply {
  margin-block-start: 0.18rem;
  padding-inline-start: 0.45rem;
  border-inline-start: 2px solid var(--color-border);
}

.chat-message__reply span {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
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

.chat-form__reply {
  display: flex;
  justify-content: space-between;
  align-items: center;
  color: var(--color-text-muted);
  font-size: 0.72rem;
}

.chat-form__reply button {
  display: grid;
  min-height: 1.5rem;
  padding-inline: 0.25rem;
  place-items: center;
  background: transparent;
  color: inherit;
}

.chat-menu {
  position: fixed;
  z-index: 850;
  display: grid;
  min-width: 8rem;
  padding: 0.2rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: var(--color-surface);
  box-shadow: var(--shadow-md);
}

.chat-menu button {
  padding: 0.4rem 0.65rem;
  border: 0;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--color-text);
  font: inherit;
  font-size: 0.75rem;
  text-align: start;
  cursor: pointer;
}

.chat-menu button:enabled:hover,
.chat-menu button:focus-visible {
  background: var(--color-surface-hover);
}

.chat-menu button:disabled {
  opacity: 0.5;
  cursor: default;
}

.chat-menu button.chat-menu__danger {
  color: var(--color-danger);
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
