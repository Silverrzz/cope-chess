<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, shallowRef, useId, watch } from 'vue'
import { useRoute, useRouter, type RouteLocationRaw } from 'vue-router'

import { api } from '@/api/client'
import AppIcon from '@/components/ui/AppIcon.vue'
import { useConfirm } from '@/composables/useConfirm'
import { useToast } from '@/composables/useToast'
import { chatViewerId, savedViewerName, saveViewerName } from '@/composables/useViewerPresence'
import { useViewerSettings } from '@/composables/useViewerSettings'
import { useSessionStore } from '@/stores/session'

import { errorMessage, formatDate } from './format'
import { emojiDataUrl, formatChatEmoji, loadChatEmoji } from './chatEmoji'
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
const emojiDialog = ref<HTMLDialogElement | null>(null)
const emojiPickerReady = ref(false)
const emojiNames = shallowRef<ReadonlyMap<string, string>>(new Map())
const emojiQuery = ref<{ name: string; start: number; end: number } | null>(null)
const emojiIndex = ref(0)
const emojiListId = useId()
const composerId = useId()
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
const emojiSuggestions = computed(() => {
  const query = emojiQuery.value?.name
  const matches: { name: string; emoji: string }[] = []
  if (!query) return matches
  const seen = new Set<string>()
  const exact = emojiNames.value.get(query)
  if (exact) {
    matches.push({ name: query, emoji: exact })
    seen.add(exact)
  }
  for (const [name, emoji] of emojiNames.value) {
    if (!name.startsWith(query) || seen.has(emoji)) continue
    matches.push({ name, emoji })
    seen.add(emoji)
    if (matches.length === 6) break
  }
  return matches
})

onMounted(() => {
  displayName.value = savedViewerName()
  scrollToLatest()
  document.addEventListener('pointerdown', dismissMenuOnOutsideClick)
  void loadChatEmoji().then((names) => { emojiNames.value = names }).catch(() => {})
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
  const messageText = text.value.trim()
  body.set('viewer_id', chatViewerId())
  if (props.gameId !== null) body.set('game_id', String(props.gameId))
  if (replyingTo.value && replyId.value !== null) body.set('reply_to_id', String(replyId.value))
  try {
    const names = messageText.includes(':') && !emojiNames.value.size
      ? await loadChatEmoji().catch(() => emojiNames.value)
      : emojiNames.value
    body.set('text', formatChatEmoji(messageText, names))
    const target = props.eventSlug
      ? `/api/events/${encodeURIComponent(props.eventSlug)}/chat`
      : `/api/tournaments/${encodeURIComponent(String(props.tournamentId))}/chat`
    const response = await api.post<{ message?: ChatMessage | null }>(target, { body })
    if (response.message) emit('sent', response.message)
    text.value = ''
    emojiQuery.value = null
    replyId.value = null
  } catch (error) {
    submitError.value = errorMessage(error, 'Your message could not be sent.')
  } finally {
    submitting.value = false
  }
}

function updateEmojiQuery(): void {
  const input = composer.value
  if (!input || input.selectionStart !== input.selectionEnd) {
    emojiQuery.value = null
    return
  }
  const cursor = input.selectionStart
  const before = input.value.slice(0, cursor)
  const name = /(?:^|[\s([{"':]):([a-z0-9_+-]+)$/i.exec(before)?.[1]?.toLowerCase()
  const start = name ? cursor - name.length - 1 : cursor
  if (emojiQuery.value?.name === name && emojiQuery.value?.start === start && emojiQuery.value?.end === cursor) return
  emojiQuery.value = name ? { name, start, end: cursor } : null
  emojiIndex.value = 0
}

async function insertEmoji(emoji: string, range = emojiQuery.value): Promise<void> {
  const input = composer.value
  if (!input) return
  const start = range?.start ?? input.selectionStart
  const end = range?.end ?? input.selectionEnd
  const tail = text.value.slice(end)
  const suffix = range ? /^[a-z0-9_+-]*:?/i.exec(tail)?.[0] ?? '' : ''
  const next = text.value.slice(0, start) + emoji + tail.slice(suffix.length)
  if (next.length > maxLength.value) return
  text.value = next
  emojiQuery.value = null
  emojiDialog.value?.close()
  await nextTick()
  input.focus()
  input.setSelectionRange(start + emoji.length, start + emoji.length)
}

function handleComposerKey(event: KeyboardEvent): void {
  if (event.isComposing) return
  const choices = emojiSuggestions.value
  if (choices.length && (event.key === 'ArrowDown' || event.key === 'ArrowUp')) {
    event.preventDefault()
    const offset = event.key === 'ArrowDown' ? 1 : -1
    emojiIndex.value = (emojiIndex.value + offset + choices.length) % choices.length
  } else if (choices.length && (event.key === 'Enter' || event.key === 'Tab') && !event.shiftKey) {
    event.preventDefault()
    const choice = choices[emojiIndex.value]
    if (choice) void insertEmoji(choice.emoji)
  } else if (event.key === 'Escape' && choices.length) {
    event.preventDefault()
    emojiQuery.value = null
  } else if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault()
    void submit()
  }
}

async function openEmojiPicker(): Promise<void> {
  try {
    await import('emoji-picker-element')
    emojiPickerReady.value = true
    emojiDialog.value?.showModal()
  } catch (error) {
    toast.error(error)
  }
}

function selectPickerEmoji(event: Event): void {
  const emoji = (event as CustomEvent<{ unicode?: string }>).detail?.unicode
  if (emoji) void insertEmoji(emoji, null)
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
              <strong v-if="activeTab === 'chat' && !continued" :title="message.display_name">{{ message.display_name }}</strong>
              <template v-if="showGame && message.game_id != null">
                <span v-if="!continued" aria-hidden="true">-</span>
                <a
                  class="chat-message__game"
                  :href="router.resolve(gameTarget(message.game_id)).href"
                  :title="message.game_label ?? undefined"
                  @click="jumpToGame(message.game_id, $event)"
                >View Game</a>
              </template>
              <template v-if="!continued && message.at">
                <span v-if="activeTab === 'chat'" aria-hidden="true">-</span>
                <time :datetime="message.at">{{ formatDate(message.at, true) }}</time>
              </template>
            </div>
            <blockquote
              v-if="message.reply_to_id != null"
              class="chat-message__reply"
              :title="reply ? formatChatEmoji(reply.text, emojiNames) : undefined"
            >{{ reply ? formatChatEmoji(reply.text, emojiNames) : 'Original message unavailable' }}</blockquote>
            <p>{{ activeTab === 'chat' ? formatChatEmoji(message.text, emojiNames) : message.text }}</p>
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
      <div class="chat-form__message">
        <label :for="composerId">Message</label>
        <div class="chat-composer">
          <textarea
            :id="composerId"
            ref="composer"
            v-model="text"
            name="text"
            :maxlength="maxLength"
            rows="2"
            required
            placeholder="Write a message"
            aria-autocomplete="list"
            :aria-controls="emojiSuggestions.length ? emojiListId : undefined"
            :aria-activedescendant="emojiSuggestions.length ? emojiListId + '-' + emojiIndex : undefined"
            @input="updateEmojiQuery"
            @click="updateEmojiQuery"
            @keyup.left.right.home.end="updateEmojiQuery"
            @blur="emojiQuery = null"
            @keydown="handleComposerKey"
          ></textarea>
          <ul v-if="emojiSuggestions.length" :id="emojiListId" class="chat-emoji-suggestions" role="listbox" aria-label="Emoji suggestions">
            <li
              v-for="({ name, emoji }, index) in emojiSuggestions"
              :id="emojiListId + '-' + index"
              :key="name"
              role="option"
              :aria-selected="index === emojiIndex"
              @pointerenter="emojiIndex = index"
              @mousedown.prevent
              @click="insertEmoji(emoji)"
            ><span aria-hidden="true">{{ emoji }}</span> :{{ name }}:</li>
          </ul>
        </div>
      </div>
      <div class="chat-form__footer">
        <span>{{ text.length }} / {{ maxLength }}</span>
        <div class="chat-form__actions">
          <button type="button" class="chat-emoji-trigger" aria-haspopup="dialog" aria-label="Choose an emoji" title="Choose an emoji" @click="openEmojiPicker">
            <span aria-hidden="true">☺</span>
          </button>
          <button type="submit" :disabled="!canSend">
            {{ submitting ? 'Sending...' : 'Send' }}
          </button>
        </div>
      </div>
      <p v-if="submitError" class="chat-error" role="alert">{{ submitError }}</p>
    </form>
    <p v-else-if="activeTab === 'chat'" class="chat-disabled">Chat is currently closed.</p>

    <dialog ref="emojiDialog" class="chat-emoji-dialog" aria-label="Choose an emoji" @click.self="emojiDialog?.close()">
      <header>
        <strong>Choose an emoji</strong>
        <button type="button" aria-label="Close emoji picker" @click="emojiDialog?.close()"><AppIcon name="close" :size="16" /></button>
      </header>
      <emoji-picker v-if="emojiPickerReady" :data-source="emojiDataUrl" @emoji-click="selectPickerEmoji" />
    </dialog>

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
  align-items: baseline;
  gap: 0.32rem;
  color: var(--color-text-muted);
  font-size: 0.7rem;
  white-space: nowrap;
}

.chat-message__header strong {
  overflow: hidden;
  color: var(--color-text);
  text-overflow: ellipsis;
  font-size: 0.77rem;
}

.chat-message__game {
  color: var(--color-accent);
  text-decoration: underline;
  text-underline-offset: 0.14em;
}

.chat-message__reply {
  margin: 0.18rem 0 0;
  padding-inline-start: 0.45rem;
  border-inline-start: 2px solid var(--color-border);
  color: var(--color-text-muted);
  font-size: 0.7rem;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
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

.chat-form__message {
  display: grid;
  gap: 0.22rem;
}

.chat-composer {
  position: relative;
}

.chat-composer textarea {
  display: block;
}

.chat-emoji-suggestions {
  position: absolute;
  z-index: 2;
  inset-inline: 0;
  inset-block-end: calc(100% + 0.25rem);
  margin: 0;
  padding: 0.25rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: var(--color-surface-raised);
  box-shadow: var(--shadow-md);
  list-style: none;
}

.chat-emoji-suggestions li {
  padding: 0.3rem 0.45rem;
  border-radius: var(--radius-sm);
  font-size: 0.75rem;
  overflow-wrap: anywhere;
  cursor: pointer;
}

.chat-emoji-suggestions li[aria-selected='true'] {
  background: var(--color-surface-hover);
}

.chat-emoji-suggestions li span {
  margin-inline-end: 0.3rem;
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

.chat-form__actions {
  display: flex;
  gap: 0.35rem;
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

.chat-form .chat-emoji-trigger {
  width: 2.15rem;
  padding: 0;
  border: 1px solid var(--color-border);
  background: var(--color-surface);
  color: var(--color-text-muted);
  font-size: 1.2rem;
}

.chat-form .chat-emoji-trigger:hover {
  color: var(--color-accent);
}

.chat-emoji-dialog {
  width: min(25rem, calc(100vw - 2rem));
  max-height: calc(100dvh - 2rem);
  margin: auto;
  padding: 0;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface-raised);
  color: var(--color-text);
}

.chat-emoji-dialog::backdrop {
  background: var(--color-overlay);
}

.chat-emoji-dialog header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 0.6rem 0.75rem;
  font-size: 0.85rem;
}

.chat-emoji-dialog header button {
  display: grid;
  padding: 0.25rem;
  border: 0;
  border-radius: var(--radius-sm);
  background: transparent;
  color: inherit;
  cursor: pointer;
}

.chat-emoji-dialog emoji-picker {
  --background: var(--color-surface-raised);
  --border-color: transparent;
  --indicator-color: var(--color-accent);
  --input-border-color: var(--color-border-strong);
  --input-font-color: var(--color-text);
  --input-placeholder-color: var(--color-text-muted);
  --outline-color: var(--color-focus);
  --text-color: var(--color-text);
  width: 100%;
  height: min(25rem, calc(100dvh - 6rem));
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
