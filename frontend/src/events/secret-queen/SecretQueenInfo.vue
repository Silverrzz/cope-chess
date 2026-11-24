<script setup lang="ts">
import { ref, useId } from "vue";

import AppIcon from "@/components/ui/AppIcon.vue";
import BaseButton from "@/components/ui/BaseButton.vue";
import { secretQueenTheme } from "./theme";

defineProps<{ compact?: boolean }>();

const panel = ref<HTMLDialogElement | null>(null);
const open = ref(false);
const id = useId();

function show(): void {
  panel.value?.showModal();
  open.value = panel.value?.open ?? false;
}
</script>

<template>
  <div class="secret-queen-info" :style="secretQueenTheme">
    <BaseButton variant="ghost" size="small" :icon-only="compact" aria-label="Event info"
      aria-haspopup="dialog" :aria-controls="id" :aria-expanded="open" @click="show">
      <template #icon><AppIcon name="info" :size="17" /></template>
      Info
    </BaseButton>
    <dialog :id="id" ref="panel" :aria-labelledby="id + '-title'" @close="open = false" @click.self="panel?.close()">
      <div class="info-content">
        <header>
          <h2 :id="id + '-title'">Secret Queen</h2>
          <BaseButton variant="ghost" size="small" icon-only aria-label="Close event info" autofocus @click="panel?.close()">
            <template #icon><AppIcon name="close" :size="18" /></template>
            Close
          </BaseButton>
        </header>
        <p>Each side starts with a queen hidden in one of its pawns. It stays hidden while it moves like a pawn. Moving it like a queen reveals it. A hidden queen can win by taking the king.</p>
        <p>Each engine knows where its own queen is. The wrapper searches the possible positions for the other queen in parallel, compares the results and sends one move back to Cope.</p>
        <p>The groups play Swiss. The top engines from each group go through to the knockout bracket. To advance, an engine has to win a pair of games, one with each colour. If the pair is tied, they play another.</p>
      </div>
    </dialog>
  </div>
</template>

<style scoped>
.secret-queen-info { display: inline-flex; flex: 0 0 auto; color: var(--event-view-highlight); }
.secret-queen-info :deep(.base-button--ghost) { color: inherit; }
dialog {
  position: fixed;
  inset-block: 0;
  inset-inline: 0 auto;
  width: min(25rem, 100%);
  max-width: none;
  height: 100dvh;
  max-height: none;
  margin: 0;
  padding: 0;
  border: 0;
  border-inline-end: 1px solid var(--event-view-border);
  background: var(--event-view-surface);
  color: var(--event-view-text);
}
dialog[open] { animation: slide-in .18s ease-out; }
dialog::backdrop { background: rgb(0 0 0 / .58); }
.info-content { min-height: 100%; padding: 1.25rem 1.5rem; }
.info-content header { display: flex; justify-content: space-between; align-items: center; gap: 1rem; }
.info-content h2 { margin: 0; font-size: 1.35rem; }
.info-content p { margin: 1rem 0 0; color: var(--event-view-schedule); line-height: 1.6; }
@keyframes slide-in { from { transform: translateX(-100%); } to { transform: translateX(0); } }
@media (prefers-reduced-motion: reduce) { dialog[open] { animation: none; } }
</style>
