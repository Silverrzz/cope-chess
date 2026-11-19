<script setup lang="ts">
import { ref, watch, onBeforeUnmount } from "vue";

import { useBoardTheme, type BoardTheme } from "@/composables/useBoardTheme";
import { useTheme } from "@/composables/useTheme";
import { useViewerSettings } from "@/composables/useViewerSettings";
import { discordLogin, useDiscordSession } from "@/stores/discord";
import InlineFeedback from "@/components/admin/InlineFeedback.vue";
import AppIcon from "./AppIcon.vue";
import BaseButton from "./BaseButton.vue";
import OptionPicker from "./OptionPicker.vue";

const { theme, boardPresets, previewBoardTheme, revertBoardTheme, setBoardTheme } = useBoardTheme();
const { resolvedTheme, setTheme } = useTheme();
const {
  evaluationPerspective, setEvaluationPerspective,
  kibitzerEnabled, setKibitzerEnabled,
  boardAnnotationsEnabled, setBoardAnnotationsEnabled,
  chatGameLinksVisible, setChatGameLinksVisible,
  confettiEnabled, setConfettiEnabled,
  eventMusicEnabled, setEventMusicEnabled,
} = useViewerSettings();

const open = ref(false);
const discord = useDiscordSession();
const discordError = ref("");
const signingOut = ref(false);

async function signOut(): Promise<void> {
  signingOut.value = true;
  discordError.value = "";
  try {
    await discord.signOut();
  } catch {
    discordError.value = "Could not sign out of Discord. Please try again.";
  } finally {
    signingOut.value = false;
  }
}
let originalDarkMode = resolvedTheme.value === "dark";
let originalPerspective = evaluationPerspective.value;
const dialog = ref<HTMLDialogElement | null>(null);
const draft = ref<BoardTheme>({ ...theme.value });
const draftConfettiEnabled = ref(confettiEnabled.value);
const draftEventMusicEnabled = ref(eventMusicEnabled.value);
const draftPerspective = ref(evaluationPerspective.value);
const draftKibitzer = ref(kibitzerEnabled.value);
const draftBoardAnnotations = ref(boardAnnotationsEnabled.value);
const draftChatGameLinks = ref(chatGameLinksVisible.value);
const draftDarkMode = ref(resolvedTheme.value === "dark");

watch(open, (isOpen) => {
  if (!isOpen) {
    dialog.value?.close();
    return;
  }
  discordError.value = "";
  void discord.load().catch(() => { discordError.value = "Could not load your Discord sign-in."; });
  originalPerspective = evaluationPerspective.value;
  draftPerspective.value = originalPerspective;
  draftKibitzer.value = kibitzerEnabled.value;
  draftBoardAnnotations.value = boardAnnotationsEnabled.value;
  draftChatGameLinks.value = chatGameLinksVisible.value;
  draft.value = { ...theme.value };
  draftConfettiEnabled.value = confettiEnabled.value;
  draftEventMusicEnabled.value = eventMusicEnabled.value;
  originalDarkMode = resolvedTheme.value === "dark";
  draftDarkMode.value = originalDarkMode;
  dialog.value?.showModal();
});

watch(draftPerspective, (perspective) => {
  if (open.value) evaluationPerspective.value = perspective;
});

watch(draftDarkMode, (dark) => {
  if (open.value) setTheme(dark ? "dark" : "light");
});

watch(draft, (next) => {
  if (open.value) previewBoardTheme(next);
}, { deep: true });

function save(): void {
  setEvaluationPerspective(draftPerspective.value);
  setKibitzerEnabled(draftKibitzer.value);
  setBoardAnnotationsEnabled(draftBoardAnnotations.value);
  setChatGameLinksVisible(draftChatGameLinks.value);
  setBoardTheme(draft.value);
  setConfettiEnabled(draftConfettiEnabled.value);
  setEventMusicEnabled(draftEventMusicEnabled.value);
  setTheme(draftDarkMode.value ? "dark" : "light");
  open.value = false;
}

function cancel(): void {
  evaluationPerspective.value = originalPerspective;
  setTheme(originalDarkMode ? "dark" : "light");
  revertBoardTheme();
  open.value = false;
}

onBeforeUnmount(() => {
  if (open.value) { evaluationPerspective.value = originalPerspective; revertBoardTheme(); setTheme(originalDarkMode ? "dark" : "light"); }
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
  draftEventMusicEnabled.value = false;
  draftDarkMode.value = true;
  draftPerspective.value = "white";
  draftKibitzer.value = true;
  draftBoardAnnotations.value = true;
  draftChatGameLinks.value = true;
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
    @click="open = true"
  >
    <template #icon><AppIcon name="settings" :size="18" /></template>
    Settings
  </BaseButton>

  <dialog
    ref="dialog"
    class="settings-drawer"
    aria-label="Viewer settings"
    @close="open && cancel()"
    @click.self="cancel"
  >
    <header class="settings-drawer__header">
      <h2>Settings</h2>
      <BaseButton variant="ghost" size="small" icon-only aria-label="Close settings" @click="cancel">
        <template #icon><AppIcon name="close" :size="18" /></template>
        Close
      </BaseButton>
    </header>

    <div class="settings-drawer__body">
      <section class="section-stack" aria-labelledby="discord-settings-heading">
        <h3 id="discord-settings-heading">Discord account</h3>
        <div v-if="discord.account" class="section-stack">
          <p><strong>{{ discord.account.display_name }}</strong> <small class="muted">@{{ discord.account.username }}</small></p>
          <div class="button-row"><BaseButton size="small" :loading="signingOut" @click="signOut">Sign out</BaseButton></div>
        </div>
        <div v-else-if="discord.enabled" class="button-row">
          <BaseButton :href="discordLogin()" size="small"><template #icon><AppIcon name="discord" :size="17" /></template>Sign in with Discord</BaseButton>
        </div>
        <p v-else-if="discord.loaded" class="muted">Discord sign-in is currently unavailable.</p>
        <InlineFeedback :message="discordError" />
      </section>

      <h3>Appearance</h3>
      <label class="setting-toggle">
        <span><strong>Dark mode</strong><small>Use the darker colour theme across the site.</small></span>
        <input v-model="draftDarkMode" type="checkbox" role="switch" />
      </label>

      <h4 class="settings-subheading">Board colours</h4>
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

      <div class="colour-field">
        <span>Custom</span>
        <input type="color" aria-label="Light squares" :value="draft.light" @input="update('light', $event)" />
        <input type="color" aria-label="Dark squares" :value="draft.dark" @input="update('dark', $event)" />
      </div>

      <h3>Analysis</h3>
      <div class="perspective-setting">
        <span>Engine evaluations</span>
        <OptionPicker :model-value="draftPerspective" :options="[{ value: 'white', label: 'White side', description: 'Positive scores favour White' }, { value: 'side-to-move', label: 'Side to move', description: 'Positive scores favour the searching side' }]" label="Evaluation perspective" @update:model-value="draftPerspective = $event === 'side-to-move' ? 'side-to-move' : 'white'" />
      </div>
      <label class="setting-toggle">
        <span><strong>Local kibitzer</strong><small>Show a local Stockfish Lite evaluation bar beside the board.</small></span>
        <input v-model="draftKibitzer" type="checkbox" role="switch" />
      </label>
      <label class="setting-toggle">
        <span><strong>Board annotations</strong><small>Right-click to highlight squares and drag to draw arrows.</small></span>
        <input v-model="draftBoardAnnotations" type="checkbox" role="switch" />
      </label>

      <h3>Chat & events</h3>
      <label class="setting-toggle">
        <span><strong>Show game links</strong><small>See which game someone was watching when they sent a message.</small></span>
        <input v-model="draftChatGameLinks" type="checkbox" role="switch" />
      </label>
      <label class="setting-toggle">
        <span><strong>Show confetti</strong><small>Display team celebrations when spectators cheer.</small></span>
        <input v-model="draftConfettiEnabled" type="checkbox" role="switch" />
      </label>
      <label class="setting-toggle">
        <span><strong>Countdown music</strong><small>Play event music during the final minute of a countdown.</small></span>
        <input v-model="draftEventMusicEnabled" type="checkbox" role="switch" />
      </label>
    </div>

    <footer class="settings-drawer__footer">
      <BaseButton variant="ghost" size="small" @click="reset">Reset</BaseButton>
      <div class="settings-drawer__actions">
        <BaseButton variant="secondary" size="small" @click="cancel">Cancel</BaseButton>
        <BaseButton variant="primary" size="small" @click="save">Save</BaseButton>
      </div>
    </footer>
  </dialog>
</template>

<style scoped>
.settings-drawer {
  inline-size: min(21rem, 100vw);
  max-inline-size: none;
  block-size: 100%;
  max-block-size: none;
  margin: 0 0 0 auto;
  padding: 0;
  border: 0;
  border-inline-start: 1px solid var(--color-border);
  background: var(--color-surface);
  color: var(--color-text);
  box-shadow: var(--shadow-md);
}

.settings-drawer[open] {
  display: flex;
  flex-direction: column;
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
  scrollbar-width: none;
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

.settings-drawer__body > h3 {
  margin-block-start: var(--space-2);
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
  font-size: 0.8125rem;
  font-weight: 640;
}

.colour-field span {
  flex: 1;
}

.colour-field input[type="color"] {
  inline-size: 2.25rem;
  block-size: 2.25rem;
  padding: 0;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: none;
}

.setting-toggle {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-3);
}

.setting-toggle input {
  appearance: none;
  flex: none;
  inline-size: 2.25rem;
  block-size: 1.25rem;
  margin: 0;
  border-radius: var(--radius-round);
  background: radial-gradient(circle closest-side, var(--color-on-primary) 70%, transparent 76%) 0 / 1.25rem no-repeat var(--color-border-strong);
  transition: background var(--transition-fast);
}

.setting-toggle input:checked {
  background-color: var(--color-primary);
  background-position: 100%;
}

.setting-toggle span {
  display: grid;
  gap: var(--space-1);
}

.settings-subheading { margin: 0; color: var(--color-text-secondary); font-size: .8125rem; }
.perspective-setting { display: grid; gap: var(--space-2); min-width: 0; }
.perspective-setting > span { color: var(--color-text-secondary); font-size: .8125rem; font-weight: 640; }

.setting-toggle strong { font-size: 0.8125rem; }
.setting-toggle small { color: var(--color-text-muted); font-size: 0.75rem; line-height: 1.4; }
</style>
