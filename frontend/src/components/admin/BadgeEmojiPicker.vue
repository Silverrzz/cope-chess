<script setup lang="ts">
import 'emoji-picker-element'
import emojiDataUrl from 'emoji-picker-element-data/en/emojibase/data.json?url'
import { ref } from 'vue'

import AppIcon from '@/components/ui/AppIcon.vue'

const model = defineModel<string>({ required: true })
const dialog = ref<HTMLDialogElement | null>(null)

function open(): void {
  dialog.value?.showModal()
}

function close(): void {
  dialog.value?.close()
}

function selectEmoji(event: Event): void {
  const detail = (event as CustomEvent<{ unicode: string }>).detail
  if (!detail?.unicode) return
  model.value = detail.unicode
  close()
}
</script>

<template>
  <div class="emoji-field">
    <button class="emoji-trigger" type="button" :aria-label="model ? `Change emoji ${model}` : 'Choose an emoji'" @click="open">
      <span :class="{ 'emoji-trigger__empty': !model }">{{ model || '＋' }}</span>
      <span>{{ model ? 'Change emoji' : 'Choose emoji' }}</span>
      <AppIcon name="chevron-down" :size="15" />
    </button>
    <dialog ref="dialog" class="emoji-dialog" aria-labelledby="emoji-dialog-title" @click.self="close">
      <header>
        <div>
          <h2 id="emoji-dialog-title">Choose an emoji</h2>
          <p>Search or browse every emoji.</p>
        </div>
        <button class="icon-button" type="button" aria-label="Close emoji picker" @click="close"><AppIcon name="close" :size="18" /></button>
      </header>
      <emoji-picker class="emoji-picker" :data-source="emojiDataUrl" @emoji-click="selectEmoji" />
    </dialog>
  </div>
</template>

<style scoped>
.emoji-field{min-width:0}.emoji-trigger{align-items:center;background:var(--color-surface-raised);border:1px solid var(--color-border-strong);border-radius:var(--radius-md);color:var(--color-text);cursor:pointer;display:grid;gap:.65rem;grid-template-columns:auto minmax(0,1fr) auto;min-height:var(--control-height);padding:.45rem .7rem;text-align:left;width:100%}.emoji-trigger:hover{border-color:var(--color-accent)}.emoji-trigger:focus-visible{border-color:var(--color-focus);box-shadow:0 0 0 3px color-mix(in srgb,var(--color-focus) 22%,transparent);outline:0}.emoji-trigger>span:first-child{font-family:"Apple Color Emoji","Segoe UI Emoji","Noto Color Emoji",sans-serif;font-size:1.35rem;line-height:1}.emoji-trigger>span:nth-child(2){font-size:.8rem;font-weight:640}.emoji-trigger__empty{color:var(--color-text-muted)}.emoji-dialog{background:var(--color-surface-raised);border:1px solid var(--color-border-strong);border-radius:var(--radius-lg);box-shadow:var(--shadow-lg);color:var(--color-text);margin:auto;max-height:calc(100dvh - 2rem);max-width:calc(100vw - 2rem);padding:0;width:min(26rem,calc(100vw - 2rem))}.emoji-dialog::backdrop{background:var(--color-overlay);backdrop-filter:blur(2px)}.emoji-dialog>header{align-items:start;border-bottom:1px solid var(--color-border);display:flex;justify-content:space-between;padding:.9rem 1rem}.emoji-dialog h2{font-size:.95rem;margin:0}.emoji-dialog p{color:var(--color-text-muted);font-size:.7rem;margin:.18rem 0 0}.emoji-picker{--background:var(--color-surface-raised);--border-color:transparent;--indicator-color:var(--color-accent);--input-border-color:var(--color-border-strong);--input-font-color:var(--color-text);--input-placeholder-color:var(--color-text-muted);--outline-color:var(--color-focus);--text-color:var(--color-text);height:min(27rem,calc(100dvh - 9rem));width:100%}
</style>
