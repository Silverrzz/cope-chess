<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";

import { api } from "@/api/client";
import ChatPanel from "@/components/public/ChatPanel.vue";
import ContentState from "@/components/public/ContentState.vue";
import StatusPill from "@/components/public/StatusPill.vue";
import { errorMessage } from "@/components/public/format";
import { publicEventComponent, publicEventPresentation } from "@/events/registry";
import type { ChatMessage, ChatSettings } from "@/components/public/types";
import type { EventCastMember, EventContest, EventDetailResponse, EventSession } from "@/types/events";

const props = withDefaults(defineProps<{ slug: string; arena?: boolean }>(), { arena: false });
const data = ref<EventDetailResponse | null>(null);
const loading = ref(true);
const loadError = ref("");
const connected = ref(false);
const serverClockOffsetMs = ref(0);
let controller: AbortController | null = null;
let stream: EventSource | null = null;

const customComponent = computed(() => data.value?.handler.current ? publicEventComponent(data.value.handler.key) : null);
const immersiveCustom = computed(() => !!data.value && publicEventPresentation(data.value.handler.key) === "immersive");
const accent = computed(() => {
  const theme = data.value?.event.theme ?? {};
  const value = theme.accent ?? theme.accent_color;
  return typeof value === "string" && /^#[0-9a-f]{3,8}$/i.test(value) ? value : "var(--color-accent, #315fcc)";
});
const eventStyle = computed(() => ({ "--event-accent": accent.value }));
const orderedMessages = computed(() => [...(data.value?.chat_messages ?? [])].sort((left, right) => Number(left.id ?? 0) - Number(right.id ?? 0)));
const activeSession = computed(() => data.value?.sessions.find((session) => ["live", "intermission"].includes(session.status)) ?? null);
const upcomingSession = computed(() => activeSession.value ?? data.value?.sessions.find((session) => ["scheduled", "pending", "postponed"].includes(session.status)) ?? null);
const topCast = computed(() => data.value?.cast.filter((member) => member.parent_id === null) ?? []);
const programme = computed(() => data.value?.stages.map((stage) => ({
  stage,
  sessions: data.value?.sessions.filter((session) => session.stage_id === stage.id) ?? [],
})) ?? []);
const ungroupedSessions = computed(() => data.value?.sessions.filter((session) => session.stage_id === null) ?? []);

onMounted(async () => {
  await load();
  if (data.value) connect();
});
onBeforeUnmount(() => {
  controller?.abort();
  stream?.close();
});

async function load(silent = false): Promise<void> {
  controller?.abort();
  controller = new AbortController();
  if (!silent) loading.value = true;
  loadError.value = "";
  try {
    const requestedAt = Date.now();
    const response = await api.get<EventDetailResponse>(`/api/events/${encodeURIComponent(props.slug)}`, { signal: controller.signal });
    const receivedAt = Date.now();
    const serverTime = Date.parse(response.server_time);
    if (Number.isFinite(serverTime)) serverClockOffsetMs.value = serverTime - (requestedAt + receivedAt) / 2;
    data.value = response;
  } catch (error) {
    if ((error as { name?: string })?.name !== "AbortError") loadError.value = errorMessage(error, "This event could not be loaded.");
  } finally {
    loading.value = false;
  }
}

function connect(): void {
  if (typeof EventSource === "undefined") return;
  stream?.close();
  stream = new EventSource(`/events/${encodeURIComponent(props.slug)}/stream`);
  stream.onopen = () => { connected.value = true; };
  stream.onerror = () => { connected.value = false; };
  stream.addEventListener("event.changed", () => { void load(true); });
  stream.addEventListener("event.snapshot", updateSpectators);
  stream.addEventListener("spectators.changed", updateSpectators);
  stream.addEventListener("event.cheer", (raw) => {
    window.dispatchEvent(new CustomEvent("cope:event-cheer", { detail: streamData(raw) }));
  });
  stream.addEventListener("chat.message", (raw) => {
    const message = streamData<{ message?: ChatMessage }>(raw).message;
    if (message) addMessage(message);
  });
  stream.addEventListener("chat.deleted", (raw) => {
    const messageId = streamData<{ message_id?: number }>(raw).message_id;
    if (messageId !== undefined && data.value) data.value.chat_messages = data.value.chat_messages.filter((message) => Number(message.id) !== messageId);
  });
  stream.addEventListener("chat.settings", (raw) => {
    const settings = streamData<{ settings?: ChatSettings }>(raw).settings;
    if (settings && data.value) data.value.chat_settings = settings;
  });
}

function updateSpectators(raw: Event): void {
  const count = streamData<{ spectator_count?: number }>(raw).spectator_count;
  if (count !== undefined && data.value) data.value.spectator_count = count;
}

function streamData<T>(raw: Event): T {
  try {
    const envelope = JSON.parse((raw as MessageEvent).data) as { data?: T };
    return envelope.data ?? ({} as T);
  } catch {
    return {} as T;
  }
}

function addMessage(message: ChatMessage): void {
  if (!data.value) return;
  if (message.id !== undefined && data.value.chat_messages.some((item) => String(item.id) === String(message.id))) return;
  data.value.chat_messages.push(message);
}

function castChildren(member: EventCastMember): EventCastMember[] {
  return data.value?.cast.filter((item) => item.parent_id === member.id) ?? [];
}

function castStyle(member: EventCastMember): Record<string, string> | undefined {
  return /^#[0-9a-f]{3,8}$/i.test(member.accent_color) ? { "--cast-accent": member.accent_color } : undefined;
}

function contestCast(contest: EventContest): EventCastMember[] {
  if (!data.value) return [];
  const ids = data.value.contest_cast.filter((item) => item.contest_id === contest.id).map((item) => item.cast_member_id);
  return ids.map((id) => data.value?.cast.find((member) => member.id === id)).filter((member): member is EventCastMember => !!member);
}

function dateLabel(value: string | null | undefined, includeTime = true): string {
  if (!value) return "To be announced";
  return new Intl.DateTimeFormat(undefined, {
    day: "numeric",
    month: "short",
    year: "numeric",
    ...(includeTime ? { hour: "2-digit", minute: "2-digit" } : {}),
  }).format(new Date(value));
}

function sessionTiming(session: EventSession): string {
  if (session.status === "live") return "Live now";
  if (session.status === "completed") return session.finished_at ? `Finished ${dateLabel(session.finished_at)}` : "Complete";
  return dateLabel(session.scheduled_start_at);
}
</script>

<template>
  <div class="event-page" :style="eventStyle">
    <div class="page-container">
      <ContentState v-if="loading" kind="loading" title="Preparing the event" />
      <ContentState v-else-if="loadError" kind="error" :message="loadError" action-label="Try again" @action="load" />
    </div>

    <template v-if="data && !loading">
      <component :is="customComponent" v-if="customComponent && props.arena" :detail="data" :clock-offset-ms="serverClockOffsetMs" view="arena" />

      <component :is="customComponent" v-else-if="customComponent && immersiveCustom" :detail="data" :clock-offset-ms="serverClockOffsetMs" view="event" />

      <template v-else>
      <header class="event-hero">
        <div class="event-hero__light" aria-hidden="true"></div>
        <div class="page-container event-hero__inner">
          <div class="event-hero__topline">
            <RouterLink to="/events">← All events</RouterLink>
            <div><span v-if="connected" class="event-connected">Live updates</span><StatusPill :status="data.event.status" /></div>
          </div>
          <div class="event-hero__body">
            <span class="event-kicker">Unrated exhibition · {{ data.event.featured ? "COPE headline event" : "COPE special event" }}</span>
            <p v-if="data.event.subtitle" class="event-subtitle">{{ data.event.subtitle }}</p>
            <h1>{{ data.event.title }}</h1>
            <p class="event-deck">{{ data.event.summary || "A one-off event created beyond the constraints of the rating circuit." }}</p>
          </div>
          <dl class="event-hero__facts">
            <div><dt>Starts</dt><dd>{{ dateLabel(data.event.scheduled_start_at) }}</dd></div>
            <div><dt>Cast</dt><dd>{{ data.counts.cast }}</dd></div>
            <div><dt>Programme</dt><dd>{{ data.counts.sessions }} sessions</dd></div>
            <div><dt>Format</dt><dd>Special event</dd></div>
          </dl>
        </div>
      </header>

      <main class="page-container event-body">
        <section v-if="upcomingSession" class="now-card" :class="{ 'now-card--live': activeSession }">
          <span>{{ activeSession ? "Live now" : "Up next" }}</span>
          <div><h2>{{ upcomingSession.title }}</h2><p>{{ upcomingSession.summary || sessionTiming(upcomingSession) }}</p></div>
          <time v-if="!activeSession" :datetime="upcomingSession.scheduled_start_at ?? undefined">{{ sessionTiming(upcomingSession) }}</time>
          <strong v-else>On air</strong>
        </section>

        <component :is="customComponent" v-if="customComponent" :detail="data" :clock-offset-ms="serverClockOffsetMs" />

        <div class="event-grid">
          <div class="event-main">
            <section v-if="programme.length || ungroupedSessions.length" class="event-section programme-section">
              <header><span>Run of show</span><h2>Programme</h2></header>
              <div class="programme">
                <article v-for="group in programme" :key="group.stage.id" class="programme-stage">
                  <div class="stage-marker"><span></span></div>
                  <div class="stage-content">
                    <div class="stage-heading"><div><small>Stage {{ group.stage.position + 1 }}</small><h3>{{ group.stage.title }}</h3></div><StatusPill :status="group.stage.status" /></div>
                    <p v-if="group.stage.summary">{{ group.stage.summary }}</p>
                    <ol v-if="group.sessions.length">
                      <li v-for="session in group.sessions" :key="session.id">
                        <span>{{ session.position + 1 }}</span>
                        <div><strong>{{ session.title }}</strong><small>{{ sessionTiming(session) }}</small></div>
                        <StatusPill :status="session.status" />
                      </li>
                    </ol>
                  </div>
                </article>
                <article v-if="ungroupedSessions.length" class="programme-stage">
                  <div class="stage-marker"><span></span></div>
                  <div class="stage-content">
                    <div class="stage-heading"><div><small>Schedule</small><h3>Sessions</h3></div></div>
                    <ol><li v-for="session in ungroupedSessions" :key="session.id"><span>{{ session.position + 1 }}</span><div><strong>{{ session.title }}</strong><small>{{ sessionTiming(session) }}</small></div><StatusPill :status="session.status" /></li></ol>
                  </div>
                </article>
              </div>
            </section>

            <section v-if="data.contests.length" class="event-section">
              <header><span>The action</span><h2>Contests</h2></header>
              <div class="contest-list">
                <article v-for="contest in data.contests" :key="contest.id">
                  <div class="contest-copy"><StatusPill :status="contest.status" /><h3>{{ contest.title }}</h3><p v-if="contest.summary">{{ contest.summary }}</p></div>
                  <div v-if="contestCast(contest).length" class="contest-cast"><span v-for="member in contestCast(contest)" :key="member.id">{{ member.short_name || member.display_name }}</span></div>
                  <strong v-if="contest.result" class="contest-result">{{ contest.result }}</strong>
                </article>
              </div>
            </section>

            <section v-if="data.updates.length" class="event-section">
              <header><span>Story feed</span><h2>Latest updates</h2></header>
              <div class="update-list">
                <article v-for="update in data.updates" :key="update.id" :class="{ pinned: update.pinned }">
                  <div><span>{{ update.kind }}</span><time :datetime="update.occurred_at">{{ dateLabel(update.occurred_at) }}</time></div>
                  <h3>{{ update.title }}</h3><p>{{ update.body }}</p>
                </article>
              </div>
            </section>

            <section v-if="data.event.description || data.event.rules" class="event-section about-section">
              <header><span>Event notes</span><h2>About this event</h2></header>
              <div v-if="data.event.description"><h3>The concept</h3><p>{{ data.event.description }}</p></div>
              <details v-if="data.event.rules"><summary>Rules and format <span>+</span></summary><p>{{ data.event.rules }}</p></details>
            </section>
          </div>

          <aside class="event-sidebar">
            <section v-if="topCast.length" class="sidebar-card cast-card">
              <header><span>Meet the field</span><h2>Cast</h2></header>
              <div class="cast-list">
                <article v-for="member in topCast" :key="member.id">
                  <span class="cast-avatar" :style="castStyle(member)">{{ (member.short_name || member.display_name).slice(0, 2).toUpperCase() }}</span>
                  <div><strong>{{ member.display_name }}</strong><small>{{ member.role || member.kind }}</small><p v-if="member.profile">{{ member.profile }}</p><div v-if="castChildren(member).length" class="cast-children"><span v-for="child in castChildren(member)" :key="child.id">{{ child.display_name }}</span></div></div>
                </article>
              </div>
            </section>

            <section v-if="data.awards.length" class="sidebar-card awards-card">
              <header><span>Honours</span><h2>Awards</h2></header>
              <article v-for="award in data.awards" :key="award.id"><span aria-hidden="true">◇</span><div><strong>{{ award.title }}</strong><p>{{ award.recipient_label || award.description }}</p></div></article>
            </section>

            <ChatPanel :messages="orderedMessages" :settings="data.chat_settings" :event-slug="data.event.slug" @sent="addMessage" />
          </aside>
        </div>
      </main>
      </template>
    </template>
  </div>
</template>

<style scoped>
.event-page { margin-block-start: calc(0px - clamp(var(--space-6), 4vw, var(--space-12))); margin-block-end: -3rem; }
.event-hero { position: relative; isolation: isolate; overflow: hidden; border-bottom: 1px solid color-mix(in srgb, var(--event-accent) 28%, var(--color-border, #d5dbe1)); background: linear-gradient(145deg, color-mix(in srgb, var(--event-accent) 10%, var(--color-bg-subtle, #f5f7fa)), var(--color-bg, #fff) 56%); }
.event-hero__light { position: absolute; z-index: -1; top: -16rem; right: -12rem; width: 42rem; height: 42rem; border-radius: 50%; background: color-mix(in srgb, var(--event-accent) 20%, transparent); filter: blur(5rem); }
.event-hero__inner { display: grid; min-height: min(47rem, calc(100vh - var(--header-height, 4rem))); align-content: space-between; gap: 3rem; padding-block: 1.2rem clamp(2.5rem, 8vw, 6rem); }
.event-hero__topline { display: flex; align-items: center; justify-content: space-between; gap: 1rem; }
.event-hero__topline > a { color: var(--color-text-muted, #607080); font-size: .74rem; font-weight: 700; text-decoration: none; }
.event-hero__topline > div { display: flex; align-items: center; gap: .7rem; }
.event-connected { color: var(--event-accent); font-size: .65rem; font-weight: 750; letter-spacing: .06em; text-transform: uppercase; }
.event-connected::before { display: inline-block; width: .38rem; height: .38rem; margin-right: .35rem; border-radius: 50%; background: currentColor; content: ""; }
.event-hero__body { max-width: 70rem; }
.event-kicker { color: var(--event-accent); font-size: .68rem; font-weight: 820; letter-spacing: .14em; text-transform: uppercase; }
.event-subtitle { margin: 1.2rem 0 .45rem; color: var(--color-text-muted, #607080); font-size: clamp(1rem, 2vw, 1.25rem); font-weight: 650; }
.event-hero h1 { max-width: 14ch; margin: 1rem 0 0; font-size: clamp(3.2rem, 9vw, 8.2rem); letter-spacing: -.075em; line-height: .85; text-wrap: balance; }
.event-subtitle + h1 { margin-top: 0; }
.event-deck { max-width: 55ch; margin: 1.5rem 0 0; color: var(--color-text-muted, #607080); font-size: clamp(.95rem, 1.8vw, 1.18rem); line-height: 1.7; }
.event-hero__facts { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 1px; overflow: hidden; margin: 0; border: 1px solid var(--color-border, #d5dbe1); border-radius: .8rem; background: var(--color-border, #d5dbe1); }
.event-hero__facts div { padding: .85rem 1rem; background: color-mix(in srgb, var(--color-surface, #fff) 88%, transparent); }
.event-hero__facts dt { color: var(--color-text-muted, #607080); font-size: .6rem; font-weight: 780; letter-spacing: .09em; text-transform: uppercase; }
.event-hero__facts dd { margin: .25rem 0 0; font-size: .78rem; font-weight: 730; }
.event-body { display: grid; gap: clamp(2rem, 5vw, 4rem); padding-block: clamp(2rem, 5vw, 4.5rem); }
.now-card { display: grid; grid-template-columns: auto minmax(0, 1fr) auto; align-items: center; gap: 1.2rem; padding: 1rem 1.2rem; border: 1px solid color-mix(in srgb, var(--event-accent) 35%, var(--color-border, #d5dbe1)); border-radius: .85rem; background: color-mix(in srgb, var(--event-accent) 7%, var(--color-surface, #fff)); }
.now-card > span { color: var(--event-accent); font-size: .64rem; font-weight: 820; letter-spacing: .1em; text-transform: uppercase; }.now-card h2, .now-card p { margin: 0; }.now-card h2 { font-size: .95rem; }.now-card p, .now-card time { margin-top: .2rem; color: var(--color-text-muted, #607080); font-size: .72rem; }.now-card > strong { color: var(--event-accent); font-size: .73rem; text-transform: uppercase; }
.now-card--live > span::before { display: inline-block; width: .42rem; height: .42rem; margin-right: .4rem; border-radius: 50%; background: currentColor; box-shadow: 0 0 0 .35rem color-mix(in srgb, currentColor 14%, transparent); content: ""; }
.event-grid { display: grid; grid-template-columns: minmax(0, 1.7fr) minmax(18rem, .7fr); align-items: start; gap: clamp(2rem, 5vw, 4rem); }.event-main, .event-sidebar { display: grid; min-width: 0; gap: clamp(2rem, 5vw, 4rem); }.event-sidebar { position: sticky; top: calc(var(--header-height, 4rem) + 1.5rem); }
.event-section { display: grid; gap: 1.2rem; }.event-section > header > span, .sidebar-card > header > span { color: var(--event-accent); font-size: .62rem; font-weight: 800; letter-spacing: .13em; text-transform: uppercase; }.event-section > header h2, .sidebar-card > header h2 { margin: .25rem 0 0; font-size: clamp(1.45rem, 3vw, 2.1rem); letter-spacing: -.04em; }
.programme { display: grid; }.programme-stage { display: grid; grid-template-columns: 1.15rem minmax(0, 1fr); gap: .8rem; }.stage-marker { display: flex; justify-content: center; background: linear-gradient(var(--color-border, #d5dbe1), var(--color-border, #d5dbe1)) center / 1px 100% no-repeat; }.stage-marker span { width: .65rem; height: .65rem; margin-top: .35rem; border: 2px solid var(--event-accent); border-radius: 50%; background: var(--color-bg, #fff); }.stage-content { padding-block-end: 1.8rem; }.stage-heading { display: flex; align-items: start; justify-content: space-between; gap: 1rem; }.stage-heading small { color: var(--event-accent); font-size: .62rem; font-weight: 750; text-transform: uppercase; }.stage-heading h3 { margin: .15rem 0 0; font-size: 1.05rem; }.stage-content > p { margin: .55rem 0 0; color: var(--color-text-muted, #607080); font-size: .8rem; line-height: 1.55; }.stage-content ol { display: grid; gap: .45rem; margin: .8rem 0 0; padding: 0; list-style: none; }.stage-content li { display: grid; grid-template-columns: auto minmax(0, 1fr) auto; align-items: center; gap: .7rem; padding: .65rem .75rem; border: 1px solid var(--color-border, #d5dbe1); border-radius: .55rem; background: var(--color-surface, #fff); }.stage-content li > span { display: grid; width: 1.55rem; height: 1.55rem; place-items: center; border-radius: .4rem; background: var(--color-surface-subtle, #f1f4f7); color: var(--color-text-muted, #607080); font-size: .64rem; font-weight: 760; }.stage-content li div { display: grid; }.stage-content li strong { font-size: .78rem; }.stage-content li small { margin-top: .15rem; color: var(--color-text-muted, #607080); font-size: .65rem; }
.contest-list { display: grid; gap: .65rem; }.contest-list article { position: relative; display: grid; grid-template-columns: minmax(0, 1fr) auto; align-items: center; gap: 1rem; overflow: hidden; padding: 1rem; border: 1px solid var(--color-border, #d5dbe1); border-radius: .75rem; background: var(--color-surface, #fff); }.contest-copy h3 { margin: .65rem 0 0; font-size: 1rem; }.contest-copy p { margin: .3rem 0 0; color: var(--color-text-muted, #607080); font-size: .78rem; line-height: 1.5; }.contest-cast { display: flex; flex-wrap: wrap; justify-content: end; gap: .3rem; }.contest-cast span { padding: .3rem .5rem; border-radius: 999px; background: var(--color-surface-subtle, #f1f4f7); font-size: .66rem; font-weight: 680; }.contest-result { grid-column: 1 / -1; color: var(--event-accent); font-size: .82rem; }
.update-list { display: grid; gap: .7rem; }.update-list article { padding: 1rem 1.1rem; border: 1px solid var(--color-border, #d5dbe1); border-radius: .7rem; background: var(--color-surface, #fff); }.update-list article.pinned { border-color: color-mix(in srgb, var(--event-accent) 45%, var(--color-border, #d5dbe1)); }.update-list article > div { display: flex; align-items: center; justify-content: space-between; gap: 1rem; color: var(--color-text-muted, #607080); font-size: .64rem; }.update-list article > div span { color: var(--event-accent); font-weight: 780; letter-spacing: .08em; text-transform: uppercase; }.update-list h3 { margin: .55rem 0 0; font-size: .92rem; }.update-list p { margin: .35rem 0 0; color: var(--color-text-muted, #607080); font-size: .78rem; line-height: 1.6; white-space: pre-wrap; }
.about-section > div, .about-section details { padding: 1rem 1.1rem; border: 1px solid var(--color-border, #d5dbe1); border-radius: .7rem; background: var(--color-surface, #fff); }.about-section h3 { margin: 0; font-size: .9rem; }.about-section p { margin: .55rem 0 0; color: var(--color-text-muted, #607080); font-size: .82rem; line-height: 1.7; white-space: pre-wrap; }.about-section summary { display: flex; justify-content: space-between; font-size: .84rem; font-weight: 730; cursor: pointer; }
.sidebar-card { display: grid; gap: 1rem; padding: 1rem; border: 1px solid var(--color-border, #d5dbe1); border-radius: .8rem; background: var(--color-surface, #fff); }.sidebar-card > header h2 { font-size: 1.25rem; }.cast-list { display: grid; gap: .8rem; }.cast-list > article { display: grid; grid-template-columns: auto minmax(0, 1fr); align-items: start; gap: .7rem; }.cast-avatar { --cast-accent: var(--event-accent); display: grid; width: 2.3rem; height: 2.3rem; place-items: center; border-radius: .7rem; background: color-mix(in srgb, var(--cast-accent) 14%, var(--color-surface-subtle, #f1f4f7)); color: var(--cast-accent); font-size: .66rem; font-weight: 820; }.cast-list strong { display: block; font-size: .8rem; }.cast-list small { display: block; margin-top: .1rem; color: var(--color-text-muted, #607080); font-size: .64rem; text-transform: capitalize; }.cast-list p { margin: .35rem 0 0; color: var(--color-text-muted, #607080); font-size: .7rem; line-height: 1.45; }.cast-children { display: flex; flex-wrap: wrap; gap: .25rem; margin-top: .4rem; }.cast-children span { padding: .22rem .38rem; border-radius: .3rem; background: var(--color-surface-subtle, #f1f4f7); font-size: .61rem; }.awards-card > article { display: flex; gap: .65rem; }.awards-card > article > span { color: var(--event-accent); }.awards-card strong { font-size: .78rem; }.awards-card p { margin: .15rem 0 0; color: var(--color-text-muted, #607080); font-size: .68rem; }
.event-sidebar :deep(.chat-panel) { min-height: 23rem; border-radius: .8rem; }
@media (max-width: 58rem) { .event-grid { grid-template-columns: 1fr; }.event-sidebar { position: static; grid-template-columns: repeat(2, minmax(0, 1fr)); }.event-sidebar :deep(.chat-panel) { grid-column: 1 / -1; } }
@media (max-width: 42rem) { .event-hero__facts { grid-template-columns: repeat(2, 1fr); }.event-sidebar { grid-template-columns: 1fr; }.event-sidebar :deep(.chat-panel) { grid-column: auto; }.now-card { grid-template-columns: 1fr; }.now-card > time, .now-card > strong { justify-self: start; }.contest-list article { grid-template-columns: 1fr; }.contest-cast { justify-content: start; } }
</style>
