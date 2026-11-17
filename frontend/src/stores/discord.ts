import { defineStore } from 'pinia'
import { ref } from 'vue'
import api from '@/api/client'

interface DiscordSession {
  enabled: boolean
  account: { username: string; display_name: string } | null
}

export const discordHeaders = { 'X-Cope-Discord': '1' }

export function discordLogin(destination = location.pathname + location.search): string {
  return '/api/discord/oauth/start?' + new URLSearchParams({ next: destination }).toString()
}

export const useDiscordSession = defineStore('discord-session', () => {
  const account = ref<DiscordSession['account']>(null)
  const enabled = ref(false)
  const loaded = ref(false)
  let pending: Promise<void> | undefined

  function load(): Promise<void> {
    if (pending) return pending
    pending = api.get<DiscordSession>('/api/discord/session')
      .then(value => {
        account.value = value.account
        enabled.value = value.enabled
        loaded.value = true
      })
      .finally(() => { pending = undefined })
    return pending
  }

  async function signOut(): Promise<void> {
    await api.delete('/api/discord/session')
    account.value = null
  }

  return { account, enabled, loaded, load, signOut }
})
