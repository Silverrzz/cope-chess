<script setup lang="ts">
import MarkdownIt from 'markdown-it'
import { computed } from 'vue'
import FormEmbed from './FormEmbed.vue'

const props = defineProps<{ source?: string | undefined }>()

const parser = new MarkdownIt({ html: false, breaks: true, maxNesting: 12 }).disable('image')
const defaultValidate = parser.validateLink.bind(parser)
parser.validateLink = (url) => defaultValidate(url) && /^(?:https?:\/\/|\/(?!\/)|#)/i.test(url)
const kinds = { 'cope-game': 'game', 'cope-board': 'board', 'cope-ratings': 'ratings' } as const
type Block = { html: string } | { kind: 'game' | 'board' | 'ratings'; value: string }
const blocks = computed(() => {
  const environment = {}
  const tokens = parser.parse(props.source ?? '', environment)
  const output: Block[] = []
  let start = 0
  let embeds = 0
  for (const [index, token] of tokens.entries()) {
    if (token.type !== 'fence' || token.level !== 0 || !Object.hasOwn(kinds, token.info.trim()) || embeds >= 10) continue
    const kind = kinds[token.info.trim() as keyof typeof kinds]
    if (index > start) output.push({ html: parser.renderer.render(tokens.slice(start, index), parser.options, environment) })
    output.push({ kind, value: token.content.trim() })
    embeds++
    start = index + 1
  }
  if (start < tokens.length) output.push({ html: parser.renderer.render(tokens.slice(start), parser.options, environment) })
  return output
})
</script>

<template>
  <div v-if="blocks.length" class="form-markdown">
    <template v-for="(block, index) in blocks" :key="index">
      <FormEmbed v-if="'kind' in block" :kind="block.kind" :value="block.value" />
      <div v-else class="form-prose" v-html="block.html" />
    </template>
  </div>
</template>

<style scoped>
.form-markdown { min-width: 0; overflow-wrap: anywhere; line-height: 1.65; }
.form-prose :deep(:first-child) { margin-top: 0; }
.form-prose :deep(:last-child) { margin-bottom: 0; }
.form-prose :deep(blockquote) { margin-inline: 0; border-inline-start: 3px solid var(--color-accent); padding-inline-start: 1rem; color: var(--color-text-muted); }
.form-prose :deep(pre) { overflow-x: auto; padding: .85rem; border-radius: var(--radius-md); background: var(--color-surface-sunken); }
.form-prose :deep(table) { display: block; overflow-x: auto; border-collapse: collapse; }
.form-prose :deep(th), .form-prose :deep(td) { border: 1px solid var(--color-border); padding: .4rem .7rem; }
</style>
