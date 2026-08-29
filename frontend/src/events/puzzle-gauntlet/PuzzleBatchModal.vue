<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from "vue";

import AppIcon from "@/components/ui/AppIcon.vue";

const props = defineProps<{
  open: boolean;
  modelValue: string;
  pending: boolean;
  error: string;
}>();

const emit = defineEmits<{
  "update:modelValue": [value: string];
  close: [];
  submit: [];
}>();

const dialog = ref<HTMLElement | null>(null);
const textarea = ref<HTMLTextAreaElement | null>(null);
let previousFocus: HTMLElement | null = null;

const lineCount = computed(() => props.modelValue.split(/\r?\n/).filter((line) => line.trim()).length);

watch(
  () => props.open,
  async (open, wasOpen) => {
    if (open) {
      previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
      document.body.style.overflow = "hidden";
      document.addEventListener("keydown", onKeydown);
      await nextTick();
      textarea.value?.focus();
    } else if (wasOpen) {
      restorePage();
    }
  },
);

onBeforeUnmount(restorePage);

function focusableElements(): HTMLElement[] {
  if (!dialog.value) return [];
  return Array.from(dialog.value.querySelectorAll<HTMLElement>(
    'button:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
  ));
}

function onKeydown(event: KeyboardEvent): void {
  if (!props.open) return;
  if (event.key === "Escape" && !props.pending) {
    event.preventDefault();
    emit("close");
    return;
  }
  if (event.key !== "Tab") return;
  const elements = focusableElements();
  const first = elements[0];
  const last = elements[elements.length - 1];
  if (!first || !last) return;
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}

function restorePage(): void {
  document.body.style.overflow = "";
  document.removeEventListener("keydown", onKeydown);
  previousFocus?.focus();
  previousFocus = null;
}

function updateText(event: Event): void {
  emit("update:modelValue", (event.target as HTMLTextAreaElement).value);
}
</script>

<template>
  <Teleport to="body">
    <Transition name="batch-modal">
      <div v-if="open" class="batch-backdrop" @mousedown.self="!pending && emit('close')">
        <section
          ref="dialog"
          class="batch-modal"
          role="dialog"
          aria-modal="true"
          aria-labelledby="batch-modal-title"
          aria-describedby="batch-modal-description"
        >
          <header class="batch-modal__header">
            <span class="batch-modal__icon"><AppIcon name="upload" :size="20" /></span>
            <div>
              <p>Batch addition</p>
              <h2 id="batch-modal-title">Add puzzle batch</h2>
              <span id="batch-modal-description">Paste one puzzle per line. Every valid line is added in the order shown.</span>
            </div>
            <button type="button" aria-label="Close batch addition" :disabled="pending" @click="emit('close')">
              <AppIcon name="close" :size="18" />
            </button>
          </header>

          <form @submit.prevent="emit('submit')">
            <div class="batch-modal__body">
              <label for="batch-puzzles">Puzzle data</label>
              <div class="batch-modal__format"><code>fen|solution</code><span>Blank lines are ignored.</span></div>
              <textarea
                id="batch-puzzles"
                ref="textarea"
                :value="modelValue"
                rows="12"
                placeholder="rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1|e4&#10;rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1|c5"
                :disabled="pending"
                :aria-invalid="!!error"
                :aria-describedby="error ? 'batch-modal-description batch-modal-error' : 'batch-modal-description'"
                @input="updateText"
              ></textarea>
              <p v-if="error" id="batch-modal-error" class="batch-modal__error" role="alert">
                <AppIcon name="alert-circle" :size="15" />{{ error }}
              </p>
            </div>

            <footer class="batch-modal__footer">
              <span>{{ lineCount }} {{ lineCount === 1 ? "puzzle" : "puzzles" }} ready</span>
              <div>
                <button class="button button--ghost" type="button" :disabled="pending" @click="emit('close')">Cancel</button>
                <button class="button button--primary" type="submit" :disabled="pending || lineCount === 0">
                  <AppIcon name="upload" :size="15" />{{ pending ? "Adding…" : `Add ${lineCount} ${lineCount === 1 ? "puzzle" : "puzzles"}` }}
                </button>
              </div>
            </footer>
          </form>
        </section>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.batch-backdrop { position: fixed; z-index: 1150; inset: 0; display: grid; place-items: center; overflow-y: auto; padding: clamp(.75rem, 3vw, 2rem); background: var(--color-overlay); }
.batch-modal { width: min(100%, 42rem); max-height: 92vh; overflow: hidden; border: 1px solid var(--color-border-strong); border-radius: var(--radius-xl); background: var(--color-surface-raised); box-shadow: var(--shadow-md); }
.batch-modal__header { display: grid; grid-template-columns: auto minmax(0, 1fr) auto; align-items: start; gap: .85rem; padding: 1.2rem 1.3rem 1.05rem; border-bottom: 1px solid var(--color-border); }
.batch-modal__icon { display: grid; width: 2.4rem; height: 2.4rem; place-items: center; border-radius: .7rem; background: color-mix(in srgb, #8b5cf6 12%, var(--color-surface-subtle)); color: #7c5ce0; }
.batch-modal__header p { margin: 0; color: #7c5ce0; font-size: .6rem; font-weight: 800; letter-spacing: .09em; text-transform: uppercase; }
.batch-modal__header h2 { margin: .08rem 0 0; font-size: 1.15rem; }
.batch-modal__header div > span { display: block; margin-top: .18rem; color: var(--color-text-muted); font-size: .72rem; }
.batch-modal__header button { display: grid; width: 2rem; height: 2rem; place-items: center; border: 0; border-radius: .45rem; background: transparent; color: var(--color-text-muted); cursor: pointer; }
.batch-modal__header button:hover { background: var(--color-surface-hover); color: var(--color-text); }
.batch-modal__header button:disabled { cursor: default; opacity: .45; }
.batch-modal form { display: flex; min-height: 0; max-height: calc(92vh - 5.6rem); flex-direction: column; }
.batch-modal__body { display: grid; gap: .55rem; overflow-y: auto; padding: 1.2rem 1.3rem 1.3rem; background: color-mix(in srgb, var(--color-surface-sunken) 42%, var(--color-surface-raised)); }
.batch-modal__body > label { font-size: .68rem; font-weight: 750; }
.batch-modal__format { display: flex; align-items: center; justify-content: space-between; gap: 1rem; color: var(--color-text-muted); font-size: .62rem; }
.batch-modal__format code { padding: .25rem .4rem; border-radius: .3rem; background: color-mix(in srgb, #8b5cf6 10%, var(--color-surface-subtle)); color: #6943cf; font-size: .62rem; font-weight: 700; }
.batch-modal textarea { width: 100%; min-height: 15rem; resize: vertical; border: 1px solid var(--color-border); border-radius: .55rem; background: var(--color-surface); color: var(--color-text); font: .68rem/1.55 ui-monospace, monospace; padding: .75rem; }
.batch-modal textarea:focus { border-color: #8b5cf6; outline: 2px solid color-mix(in srgb, #8b5cf6 20%, transparent); outline-offset: 1px; }
.batch-modal__error { display: flex; align-items: center; gap: .4rem; margin: 0; color: var(--color-danger); font-size: .65rem; }
.batch-modal__footer { display: flex; align-items: center; justify-content: space-between; gap: 1rem; padding: .85rem 1.3rem; border-top: 1px solid var(--color-border); }
.batch-modal__footer > span { color: var(--color-text-muted); font-size: .68rem; }
.batch-modal__footer > div { display: flex; gap: .45rem; }
.batch-modal-enter-active, .batch-modal-leave-active { transition: opacity var(--transition-base); }
.batch-modal-enter-active .batch-modal, .batch-modal-leave-active .batch-modal { transition: transform var(--transition-base); }
.batch-modal-enter-from, .batch-modal-leave-to { opacity: 0; }
.batch-modal-enter-from .batch-modal, .batch-modal-leave-to .batch-modal { transform: translateY(.55rem) scale(.985); }
@media (max-width: 34rem) { .batch-backdrop { align-items: end; padding: 0; }.batch-modal { width: 100%; max-height: 94vh; border-bottom: 0; border-radius: var(--radius-xl) var(--radius-xl) 0 0; }.batch-modal__header, .batch-modal__body { padding-right: 1rem; padding-left: 1rem; }.batch-modal__format { align-items: flex-start; flex-direction: column; gap: .35rem; }.batch-modal textarea { min-height: 12rem; }.batch-modal__footer { align-items: stretch; flex-direction: column; padding: .75rem 1rem 1rem; }.batch-modal__footer > div { display: grid; grid-template-columns: 1fr 1fr; }.batch-modal__footer .button { justify-content: center; } }
</style>
