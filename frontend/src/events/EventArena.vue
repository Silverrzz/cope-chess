<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";

import ContentState from "@/components/public/ContentState.vue";
import AppIcon from "@/components/ui/AppIcon.vue";

withDefaults(defineProps<{
  title: string;
  slug: string;
  status: string;
  gameContext: string;
  gameContextTitle?: string;
  spectatorCount?: number | undefined;
  canFollowLive?: boolean;
  error?: string;
}>(), { spectatorCount: 0, error: "" });

const emit = defineEmits<{ followLive: []; retry: [] }>();
const heading = ref<HTMLElement | null>(null);
const arena = ref<HTMLElement | null>(null);
const boardColumn = ref<HTMLElement | null>(null);
let fitFrame: number | undefined;
let observer: ResizeObserver | undefined;

function scheduleFit(): void {
  if (fitFrame !== undefined) window.cancelAnimationFrame(fitFrame);
  fitFrame = window.requestAnimationFrame(() => {
    fitFrame = undefined;
    void fitToViewport();
  });
}

async function fitToViewport(): Promise<void> {
  await nextTick();
  const shell = boardColumn.value?.querySelector<HTMLElement>(".viewer-shell");
  const board = shell?.querySelector<HTMLElement>(".board-mount");
  if (!arena.value || !shell || !board) return;
  const viewportHeight = window.visualViewport?.height ?? window.innerHeight;
  const top = arena.value.getBoundingClientRect().top + window.scrollY;
  const availableHeight = Math.floor(viewportHeight - top - 8);
  if (availableHeight <= 0) return;
  const chrome = Math.max(0, shell.getBoundingClientRect().height - board.getBoundingClientRect().height);
  const rootFont = Number.parseFloat(getComputedStyle(document.documentElement).fontSize) || 16;
  const boardSize = Math.max(0, Math.min(42 * rootFont, Math.floor(availableHeight - chrome)));
  arena.value.style.setProperty("--arena-content-height", `${availableHeight}px`);
  arena.value.style.setProperty("--arena-board-size", `${boardSize}px`);
}

watch(heading, (next, previous) => {
  if (previous) observer?.unobserve(previous);
  if (next) observer?.observe(next);
  scheduleFit();
}, { flush: "post" });
onMounted(() => {
  observer = new ResizeObserver(scheduleFit);
  if (heading.value) observer.observe(heading.value);
  window.addEventListener("resize", scheduleFit);
  window.visualViewport?.addEventListener("resize", scheduleFit);
  scheduleFit();
});
onBeforeUnmount(() => {
  if (fitFrame !== undefined) window.cancelAnimationFrame(fitFrame);
  observer?.disconnect();
  window.removeEventListener("resize", scheduleFit);
  window.visualViewport?.removeEventListener("resize", scheduleFit);
});
</script>

<template>
  <section class="relay-viewer">
    <slot name="background" />
    <header ref="heading" class="tournament-heading" :class="{ 'tournament-heading--separate-nav': $slots.navigation }">
      <div class="tournament-heading__title">
        <div class="title-line">
          <slot name="title-leading" />
          <h1>{{ title }}</h1>
          <span class="relay-status">{{ status }}</span>
          <span class="relay-spectators"><AppIcon name="user" :size="14" /> {{ spectatorCount.toLocaleString() }}</span>
        </div>
        <p :title="gameContextTitle">{{ gameContext }}</p>
      </div>
      <nav v-if="$slots.navigation" class="tournament-heading__navigation" aria-label="Event navigation">
        <slot name="navigation" />
        <RouterLink :to="{ name: 'event', params: { slug } }"><AppIcon name="arrow-left" :size="14" /> Event page</RouterLink>
      </nav>
      <div class="tournament-heading__controls">
        <div v-if="$slots.selectors" class="relay-broadcast__selectors"><slot name="selectors" /></div>
        <button v-if="canFollowLive" type="button" class="event-page-link tournament-heading__follow-live" @click="emit('followLive')">Follow live <AppIcon name="arrow-right" :size="14" /></button>
        <slot name="actions" />
        <RouterLink v-if="!$slots.navigation" :to="{ name: 'event', params: { slug } }" class="event-page-link"><AppIcon name="arrow-left" :size="15" /> Event page</RouterLink>
      </div>
    </header>
    <ContentState v-if="error" kind="error" :message="error" action-label="Try again" compact @action="emit('retry')" />
    <section ref="arena" class="arena" :aria-label="title">
      <div class="engine-column"><slot name="engines" /></div>
      <div ref="boardColumn" class="board-column"><slot name="board" /></div>
      <aside class="activity-column" aria-label="Game activity"><slot name="activity" /></aside>
    </section>
    <slot />
  </section>
</template>

<style scoped>
.relay-viewer {
  --arena-board-size: clamp(30rem, calc(100dvh - 15rem), 42rem);
  --arena-content-height: calc(var(--arena-board-size) + 6.35rem);
  position: relative;
  isolation: isolate;
  display: grid;
  overflow-anchor: none;
  width: 100%;
  gap: var(--space-md, 1rem);
  padding: .55rem clamp(.75rem, 1.5vw, 1.75rem) 3rem;
  background: var(--color-bg, #f3f6fa);
  color: var(--color-text, #17202a);
}
.tournament-heading { display: flex; align-items: end; justify-content: space-between; gap: var(--space-xl, 2rem); padding-block-end: .55rem; border-block-end: 1px solid var(--color-border, #d5dbe1); }
.tournament-heading__title { display: grid; align-items: center; min-width: 0; }
.title-line { display: flex; align-items: center; gap: var(--space-sm, .5rem); min-width: 0; }
.tournament-heading h1, .tournament-heading p { margin: 0; }
.tournament-heading h1 { max-width: 54rem; font-size: clamp(1.45rem, 2.7vw, 2.15rem); letter-spacing: -.035em; line-height: 1.05; overflow-wrap: anywhere; }
.tournament-heading__title > p { margin-block-start: .18rem; color: var(--color-text-muted, #607080); font-size: .75rem; }
.relay-status, .relay-spectators { display: inline-flex; align-items: center; gap: .25rem; min-height: 1.45rem; padding-inline: .5rem; border: 1px solid var(--color-border, #d5dbe1); border-radius: 999px; color: var(--color-text-muted, #607080); font-size: .62rem; font-weight: 730; white-space: nowrap; }
.tournament-heading__controls, .relay-broadcast__selectors { display: flex; align-items: end; gap: .65rem; }
.relay-broadcast__selectors :slotted(label) { display: grid; gap: .25rem; min-width: 0; color: var(--color-text-muted, #607080); font-size: .64rem; font-weight: 700; }
.relay-broadcast__selectors :slotted(label:last-of-type) { width: clamp(16rem, 27vw, 27rem); }
.relay-broadcast__selectors :deep(select) { width: 100%; min-height: 2.35rem; padding: .4rem 2rem .4rem .65rem; border: 1px solid var(--color-border, #d5dbe1); border-radius: var(--radius-sm, .35rem); background: var(--color-surface, #fff); color: var(--color-text, #17202a); font: inherit; font-size: .77rem; }
.event-page-link, :slotted(.event-page-link) { display: inline-flex; min-height: 2.35rem; align-items: center; gap: .35rem; padding-inline: .8rem; border: 1px solid var(--color-border, #d5dbe1); border-radius: var(--radius-sm, .35rem); color: var(--color-text-muted, #607080); font-size: .72rem; font-weight: 730; text-decoration: none; white-space: nowrap; }
.arena { display: grid; grid-template-columns: minmax(25rem, 29rem) var(--arena-board-size) minmax(28rem, 1fr); gap: clamp(.7rem, 1.5vw, 1.2rem); align-items: start; height: var(--arena-content-height); min-height: 0; }
.engine-column, .activity-column { display: grid; min-height: 0; gap: var(--space-sm, .5rem); }
.engine-column { grid-template-rows: repeat(2, minmax(0, 1fr)) auto; height: var(--arena-content-height); }
.engine-column > :slotted(.relay-engine-slot) { display: grid; grid-template-rows: minmax(0, 1fr); min-width: 0; min-height: 0; padding: .25rem; border-radius: calc(var(--radius-md, .5rem) + .25rem); background: color-mix(in srgb, var(--relay-primary, transparent) 5%, transparent); box-shadow: 0 0 1.35rem color-mix(in srgb, var(--relay-primary, transparent) 22%, transparent); }
.engine-column :deep(.engine-panel) { min-height: 0; border-color: var(--color-border, #d5dbe1); }
.engine-column :deep(.engine-panel--active) { border-color: var(--color-border-strong, #99a8bb); box-shadow: 0 0 0 1px var(--color-border-strong, #99a8bb); }
.board-column { width: var(--arena-board-size); min-width: 0; justify-self: center; }
.activity-column { grid-template-columns: repeat(2, minmax(0, 1fr)); grid-template-rows: minmax(0, 1fr); height: var(--arena-content-height); }
.activity-column > :slotted(*) { min-width: 0; min-height: 0; height: 100%; }

@media (max-width: 96rem) {
  .arena { grid-template-columns: minmax(25rem, 1fr) var(--arena-board-size); height: auto; }
  .engine-column { grid-column: 1; grid-row: 1; }
  .board-column { grid-column: 2; grid-row: 1; }
  .activity-column { grid-column: 1 / -1; grid-row: 2; grid-template-columns: repeat(2, minmax(0, 1fr)); grid-template-rows: minmax(24rem, 34rem); height: min(34rem, calc(100dvh - 8rem)); }
}
@media (max-width: 58rem) {
  .tournament-heading { align-items: stretch; flex-direction: column; }
  .tournament-heading__controls { justify-content: space-between; }
  .arena { grid-template-columns: 1fr; }
  .engine-column { grid-column: 1; grid-row: 2; grid-template-columns: 1fr 1fr; grid-template-rows: auto auto; height: auto; }
  .engine-column > :slotted(.arena-cheers) { grid-column: 1 / -1; }
  .board-column { grid-column: 1; grid-row: 1; width: min(100%, var(--arena-board-size)); }
  .activity-column { grid-column: 1; grid-row: 3; }
}
@media (max-width: 40rem) {
  .tournament-heading__controls, .relay-broadcast__selectors { align-items: stretch; flex-direction: column-reverse; }
  .relay-broadcast__selectors :slotted(label), .relay-broadcast__selectors :slotted(label:last-of-type) { width: 100%; }
  .engine-column { grid-template-columns: 1fr; }
  .engine-column > :slotted(.arena-cheers) { grid-column: 1; }
  .activity-column { grid-template-columns: 1fr; grid-template-rows: minmax(12rem, 19rem) minmax(18rem, 26rem); }
}
.tournament-heading--separate-nav {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  grid-template-areas: "title navigation" "title controls";
  gap: .4rem 1.5rem;
  align-items: end;
  padding-block: .1rem .65rem;
}
.tournament-heading--separate-nav .tournament-heading__title { grid-area: title; align-self: center; }
.tournament-heading--separate-nav .tournament-heading__title > p { margin-block-start: .35rem; }
.tournament-heading--separate-nav .tournament-heading__controls {
  grid-area: controls;
  min-width: 0;
  justify-content: flex-end;
}
.tournament-heading__navigation {
  grid-area: navigation;
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: .25rem 1rem;
}
.tournament-heading__navigation a,
.tournament-heading__navigation :slotted(a) {
  display: inline-flex;
  align-items: center;
  gap: .35rem;
  padding-block: .14rem;
  color: var(--color-text-muted);
  font-size: .73rem;
  font-weight: 650;
  text-decoration: none;
  white-space: nowrap;
}
.tournament-heading__navigation a:hover,
.tournament-heading__navigation :slotted(a:hover) { color: var(--color-text); text-decoration: underline; }
.tournament-heading--separate-nav .tournament-heading__follow-live {
  min-height: 2.4rem;
  border-color: color-mix(in srgb, var(--color-accent) 30%, var(--color-border));
  background: color-mix(in srgb, var(--color-accent) 10%, var(--color-surface));
  color: var(--color-accent);
  cursor: pointer;
}
.tournament-heading--separate-nav .tournament-heading__follow-live:hover {
  background: color-mix(in srgb, var(--color-accent) 18%, var(--color-surface));
}
@media (max-width: 68rem) {
  .tournament-heading--separate-nav { grid-template-areas: "title navigation" "controls controls"; row-gap: .65rem; }
  .tournament-heading--separate-nav .tournament-heading__controls { justify-content: flex-start; }
}
@media (max-width: 40rem) {
  .tournament-heading--separate-nav {
    grid-template-columns: minmax(0, 1fr);
    grid-template-areas: "title" "navigation" "controls";
  }
  .tournament-heading__navigation { justify-content: flex-start; }
  .tournament-heading--separate-nav .tournament-heading__controls { flex-direction: column; }
  .tournament-heading--separate-nav .tournament-heading__follow-live { align-self: flex-start; }
}
</style>
