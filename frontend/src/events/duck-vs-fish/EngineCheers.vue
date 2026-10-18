<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from "vue";
import { api } from "@/api/client";
import wakwak from "@/assets/wakwak.svg";
import { useViewerSettings } from "@/composables/useViewerSettings";
import type { EventCastMember } from "@/types/events";
import FishMark from "./FishMark.vue";

interface CheerEvent {
  id: string;
  event_id: number;
  member_id: number;
}

interface CheerBurst {
  id: string;
  color: string;
  x: number;
  y: number;
  pieces: Array<{ x: number; y: number; rotation: number; duration: number; delay: number; size: number }>;
}

const props = defineProps<{ eventId: number; slug: string; members: EventCastMember[] }>();
const { confettiEnabled } = useViewerSettings();
const bursts = ref<CheerBurst[]>([]);
const message = ref("");
const seen = new Set<string>();
const timers = new Set<number>();
const clientId = globalThis.crypto?.randomUUID?.() ?? `cheer-${Date.now()}-${Math.random().toString(36).slice(2)}`;
let lastRequest = 0;

onMounted(() => { window.addEventListener("cope:event-cheer", receiveCheer); });
onBeforeUnmount(() => {
  window.removeEventListener("cope:event-cheer", receiveCheer);
  for (const timer of timers) window.clearTimeout(timer);
});

async function cheer(member: EventCastMember): Promise<void> {
  const now = Date.now();
  if (now - lastRequest < 350) return;
  lastRequest = now;
  try {
    const response = await api.post<{ cheer_id: string }>(`/api/events/${encodeURIComponent(props.slug)}/duck-vs-fish/cheers`, {
      body: { member_id: member.id }, headers: { "X-Cope-Cheer-Client": clientId },
    });
    launch({ id: response.cheer_id, event_id: props.eventId, member_id: member.id });
    message.value = "";
  } catch { message.value = "Cheer could not be sent."; }
}

function receiveCheer(event: Event): void {
  const detail = (event as CustomEvent<CheerEvent>).detail;
  if (detail?.event_id === props.eventId) launch(detail);
}

function launch(cheer: CheerEvent): void {
  const member = props.members.find(member => member.id === cheer.member_id);
  if (!member || !cheer.id || seen.has(cheer.id)) return;
  seen.add(cheer.id);
  if (seen.size > 128) seen.delete(seen.values().next().value!);
  if (!confettiEnabled.value || window.matchMedia("(prefers-reduced-motion: reduce)").matches || bursts.value.length >= 6) return;
  const bounds = document.querySelector<HTMLElement>(`[data-cheer-engine="${member.id}"]`)?.getBoundingClientRect();
  const left = props.members[0]?.id === member.id;
  bursts.value.push({
    id: cheer.id,
    color: /^#[0-9a-f]{6}$/i.test(member.accent_color) ? member.accent_color : left ? "#edc653" : "#a9d8d4",
    x: bounds ? (bounds.left + bounds.width / 2) / window.innerWidth * 100 : left ? 20 : 80,
    y: bounds ? (bounds.top + bounds.height / 2) / window.innerHeight * 100 : 85,
    pieces: Array.from({ length: 30 }, () => ({
      x: -8 + Math.random() * 16,
      y: -5 - Math.random() * 12,
      rotation: -520 + Math.random() * 1040,
      duration: 950 + Math.random() * 400,
      delay: Math.random() * 120,
      size: .2 + Math.random() * .2,
    })),
  });
  const timer = window.setTimeout(() => {
    timers.delete(timer);
    bursts.value = bursts.value.filter(burst => burst.id !== cheer.id);
  }, 1600);
  timers.add(timer);
}
</script>

<template>
  <div class="engine-cheers">
    <button v-for="(member, index) in members" :key="member.id" type="button" :data-cheer-engine="member.id" :aria-label="`Cheer for ${member.display_name}`" :style="{ '--engine-color': /^#[0-9a-f]{6}$/i.test(member.accent_color) ? member.accent_color : '#a9d8d4' }" @click="cheer(member)"><span class="cheer-icon"><img v-if="index === 0" :src="wakwak" alt="" /><FishMark v-else /></span><span><small>Cheer for</small><strong>{{ member.display_name }}</strong></span></button>
    <span class="sr-only" role="status">{{ message }}</span>
    <Teleport to="body"><div v-if="confettiEnabled" class="cheer-layer" aria-hidden="true"><span v-for="burst in bursts" :key="burst.id" class="cheer-burst" :style="{ left: `${burst.x}%`, top: `${burst.y}%`, '--cheer-color': burst.color }"><i v-for="(piece, index) in burst.pieces" :key="index" :style="{ '--cheer-end-x': `${piece.x}rem`, '--cheer-end-y': `${piece.y}rem`, '--cheer-rotation': `${piece.rotation}deg`, '--cheer-duration': `${piece.duration}ms`, '--cheer-delay': `${piece.delay}ms`, '--cheer-size': `${piece.size}rem` }"></i></span></div></Teleport>
  </div>
</template>

<style scoped>
.engine-cheers { display: flex; flex-wrap: wrap; justify-content: center; gap: .65rem; }
.engine-cheers button { display: grid; grid-template-columns: auto minmax(0, 1fr); align-items: center; gap: .6rem; min-width: 10rem; min-height: 3.15rem; padding: .4rem .9rem .4rem .45rem; border: 1px solid color-mix(in srgb, var(--engine-color) 30%, transparent); border-radius: .6rem; background: var(--color-surface, #102019); color: var(--color-text, #e1e8d8); font: inherit; text-align: left; cursor: pointer; transition: transform .18s ease, border-color .18s ease; }
.engine-cheers button:hover { border-color: var(--engine-color); transform: translateY(-2px); }.engine-cheers button:active { transform: scale(.98); }.engine-cheers button:focus-visible { outline: 3px solid var(--engine-color); outline-offset: 3px; }
.cheer-icon { display: grid; width: 2.25rem; height: 2.25rem; place-items: center; border-radius: .5rem; background: color-mix(in srgb, var(--engine-color) 10%, transparent); }.cheer-icon img, .cheer-icon svg { width: 1.9rem; height: 1.9rem; }.engine-cheers button > span:last-child { display: grid; gap: .12rem; }.engine-cheers small { color: var(--color-text-muted, #8a9e90); font-size: .55rem; font-weight: 750; letter-spacing: .07em; text-transform: uppercase; }.engine-cheers strong { font-size: .76rem; }
.cheer-layer { position: fixed; z-index: 46; inset: 0; overflow: hidden; pointer-events: none; }.cheer-burst { position: absolute; width: 1px; height: 1px; }.cheer-burst i { position: absolute; width: var(--cheer-size); height: calc(var(--cheer-size) * 1.6); border-radius: .08rem; background: var(--cheer-color); opacity: 0; animation: cheer-pop var(--cheer-duration) cubic-bezier(.16, .72, .28, 1) forwards; animation-delay: var(--cheer-delay); }.cheer-burst i:nth-child(even) { height: var(--cheer-size); border-radius: 50%; filter: brightness(1.22); }
@keyframes cheer-pop { 0% { opacity: 0; transform: rotate(0) scale(.5); } 13% { opacity: .95; } 100% { opacity: 0; transform: translate(var(--cheer-end-x), var(--cheer-end-y)) rotate(var(--cheer-rotation)) scale(1); } }
@media (max-width: 38rem) { .engine-cheers { gap: .4rem; }.engine-cheers button { min-width: 0; padding-right: .6rem; }.engine-cheers strong { font-size: .68rem; } }
@media (prefers-reduced-motion: reduce) { .cheer-layer { display: none; }.engine-cheers button { transition: none; } }
</style>
