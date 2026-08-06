<script setup lang="ts">
import { nextTick, ref, watch, onBeforeUnmount } from "vue";

import { useBoardTheme, type BoardTheme } from "@/composables/useBoardTheme";
import { useViewerSettings } from "@/composables/useViewerSettings";
import AppIcon from "./AppIcon.vue";
import BaseButton from "./BaseButton.vue";

const { theme, boardPresets, previewBoardTheme, revertBoardTheme, setBoardTheme } = useBoardTheme();
const { confettiEnabled, setConfettiEnabled } = useViewerSettings();

const open = ref(false);
const panel = ref<HTMLElement | null>(null);
const draft = ref<BoardTheme>({ ...theme.value });
const draftConfettiEnabled = ref(confettiEnabled.value);

watch(open, async (isOpen) => {
  if (!isOpen) return;
  draft.value = { ...theme.value };
  draftConfettiEnabled.value = confettiEnabled.value;
  await nextTick();
  panel.value?.focus();
});

watch(draft, (next) => {
  if (open.value) previewBoardTheme(next);
}, { deep: true });

function save(): void {
  setBoardTheme(draft.value);
  setConfettiEnabled(draftConfettiEnabled.value);
  open.value = false;
}

function cancel(): void {
  revertBoardTheme();
  open.value = false;
}

function toggle(): void {
  if (open.value) cancel();
  else open.value = true;
}

onBeforeUnmount(() => {
  if (open.value) revertBoardTheme();
});

function isActive(preset: BoardTheme): boolean {
  return preset.light === draft.value.light && preset.dark === draft.value.dark;
}

function update(key: "light" | "dark", event: Event): void {
  draft.value = { ...draft.value, [key]: (event.target as HTMLInputElement).value };
}

function reset(): void {
  draft.value = { ...boardPresets.brown! };
  draftConfettiEnabled.value = true;
}

</script>

<template>
  <BaseButton
    variant="ghost"
    size="small"
    icon-only
    title="Settings"
    aria-label="Settings"
    :aria-expanded="open"
    @click="toggle"
  >
    <template #icon><AppIcon name="settings" :size="18" /></template>
    Settings
  </BaseButton>

  <Teleport to="body">
    <div v-if="open" class="settings-dismiss" @click="cancel" />
    <aside
      v-if="open"
      ref="panel"
      class="settings-drawer"
      role="dialog"
      aria-label="Viewer settings"
      tabindex="-1"
      @keydown.esc="cancel"
    >
      <header class="settings-drawer__header">
        <h2>Settings</h2>
        <BaseButton variant="ghost" size="small" icon-only aria-label="Close settings" @click="cancel">
          <template #icon><AppIcon name="close" :size="18" /></template>
          Close
        </BaseButton>
      </header>

      <div class="settings-drawer__body">
        <h3>Board colours</h3>
        <div class="preset-grid">
          <button
            v-for="(preset, name) in boardPresets"
            :key="name"
            type="button"
            class="preset"
            :class="{ 'preset--active': isActive(preset) }"
            :aria-pressed="isActive(preset)"
            @click="draft = { ...preset }"
          >
            <span
              class="preset__swatch"
              :style="{ '--swatch-light': preset.light, '--swatch-dark': preset.dark }"
            />
            <span class="preset__label">{{ name }}</span>
          </button>
        </div>

        <label class="colour-field">
          <span>Light squares</span>
          <input type="color" :value="draft.light" @input="update('light', $event)" />
          <code>{{ draft.light }}</code>
        </label>
        <label class="colour-field">
          <span>Dark squares</span>
          <input type="color" :value="draft.dark" @input="update('dark', $event)" />
          <code>{{ draft.dark }}</code>
        </label>

        <h3>Celebrations</h3>
        <label class="setting-toggle">
          <input v-model="draftConfettiEnabled" type="checkbox" />
          <span><strong>Show confetti</strong><small>Display team celebrations when spectators cheer.</small></span>
        </label>
      </div>

      <footer class="settings-drawer__footer">
        <BaseButton variant="ghost" size="small" @click="reset">Reset</BaseButton>
        <div class="settings-drawer__actions">
          <BaseButton variant="secondary" size="small" @click="cancel">Cancel</BaseButton>
          <BaseButton variant="primary" size="small" @click="save">Save</BaseButton>
        </div>
      </footer>
    </aside>
  </Teleport>
</template>

<style scoped>
.settings-dismiss {
  position: fixed;
  inset: 0;
  z-index: 899;
}

.settings-drawer {
  position: fixed;
  inset-block: 0;
  inset-inline-end: 0;
  z-index: 900;
  display: flex;
  flex-direction: column;
  inline-size: min(21rem, 100vw);
  background: var(--color-surface);
  border-inline-start: 1px solid var(--color-border);
  box-shadow: var(--shadow-md);
}

.settings-drawer__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-4);
  border-block-end: 1px solid var(--color-border);
}

.settings-drawer__body {
  flex: 1;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  padding: var(--space-4);
}

.settings-drawer__body h3 {
  font-size: 0.8125rem;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--color-text-muted);
}

.settings-drawer__footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-4);
  border-block-start: 1px solid var(--color-border);
}

.settings-drawer__actions {
  display: flex;
  gap: var(--space-2);
}

.preset-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: var(--space-2);
}

.preset {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-2);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface);
  cursor: pointer;
}

.preset:hover { background: var(--color-surface-hover); }

.preset--active {
  border-color: var(--color-accent);
  box-shadow: 0 0 0 1px var(--color-accent);
}

.preset__swatch {
  inline-size: 2.25rem;
  block-size: 2.25rem;
  border-radius: var(--radius-sm);
  background-image: repeating-conic-gradient(
    var(--swatch-dark) 0 25%,
    var(--swatch-light) 0 50%
  );
  background-size: 50% 50%;
}

.preset__label {
  font-size: 0.75rem;
  color: var(--color-text-secondary);
  text-transform: capitalize;
}

.colour-field {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  color: var(--color-text-secondary);
}

.colour-field code {
  font-family: var(--font-mono);
  font-size: 0.8125rem;
  color: var(--color-text-muted);
  margin-inline-start: auto;
}

.colour-field input[type="color"] {
  inline-size: 2.25rem;
  block-size: 2.25rem;
  padding: 0;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: none;
  cursor: pointer;
}

.setting-toggle {
  display: flex;
  align-items: flex-start;
  gap: var(--space-3);
  padding: var(--space-3);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  cursor: pointer;
}

.setting-toggle input {
  inline-size: 1rem;
  block-size: 1rem;
  margin-block-start: 0.15rem;
}

.setting-toggle span {
  display: grid;
  gap: var(--space-1);
}

.setting-toggle strong { font-size: 0.8125rem; }
.setting-toggle small { color: var(--color-text-muted); font-size: 0.75rem; line-height: 1.4; }
</style>
