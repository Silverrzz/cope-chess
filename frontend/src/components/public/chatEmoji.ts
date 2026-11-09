import emojiDataUrl from 'emoji-picker-element-data/en/joypixels/data.json?url'

export { emojiDataUrl }

interface EmojiEntry {
  emoji: string
  annotation: string
  shortcodes?: string[]
  skins?: { emoji: string; tone: number | number[] }[]
}

let emojiIndex: Promise<Map<string, string>> | undefined

export function loadChatEmoji(): Promise<Map<string, string>> {
  return emojiIndex ??= fetch(emojiDataUrl).then(async (response) => {
    if (!response.ok) throw new Error('Could not load emojis.')
    const entries: EmojiEntry[] = await response.json()
    const names = new Map<string, string>()
    for (const entry of entries) {
      for (const name of entry.shortcodes ?? []) names.set(name, entry.emoji)
    }
    for (const entry of entries) {
      const annotation = entry.annotation.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_|_$/g, '')
      if (annotation && !names.has(annotation)) names.set(annotation, entry.emoji)
      for (const name of [...(entry.shortcodes ?? []), annotation]) {
        if (names.get(name) !== entry.emoji) continue
        for (const skin of entry.skins ?? []) {
          const tone = Array.isArray(skin.tone) ? skin.tone.join('_') : skin.tone
          names.set(name + '_tone' + tone, skin.emoji)
        }
      }
    }
    return names
  }).catch((error: unknown) => {
    emojiIndex = undefined
    throw error
  })
}

export function formatChatEmoji(text: string, names: ReadonlyMap<string, string>): string {
  return text.replace(/:([a-z0-9_+-]+):(?::skin-tone-([2-6]):)?/gi, (match: string, name: string, tone?: string) => {
    const key = name.toLowerCase()
    const emoji = names.get(key)
    if (!emoji) return match
    if (!tone) return emoji
    return names.get(key + '_tone' + (Number(tone) - 1)) ?? emoji + ':skin-tone-' + tone + ':'
  })
}
