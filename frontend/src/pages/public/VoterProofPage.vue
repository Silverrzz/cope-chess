<script setup lang="ts">
import { ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import { ballotTool, downloadJson, errorText, type VoterProof } from '@/api/ballots'
import { generateVoterProof } from '@/api/ballot-protocol'
import AppIcon from '@/components/ui/AppIcon.vue'
import BaseButton from '@/components/ui/BaseButton.vue'

const props = defineProps<{ slug: string }>()
const proof = ref<VoterProof | null>(null)
const busy = ref(false)
const error = ref('')
const copied = ref(false)

watch(() => props.slug, () => { proof.value = null; copied.value = false; error.value = '' })

async function create() {
  busy.value = true
  error.value = ''
  try {
    proof.value = await generateVoterProof(props.slug)
    save()
  } catch (cause) {
    error.value = errorText(cause) + ' You can also use the offline voting tool.'
  } finally {
    busy.value = false
  }
}

function save() {
  if (proof.value) downloadJson(proof.value.slug + '-voter-proof', proof.value)
}

async function copy() {
  if (!proof.value) return
  try {
    await navigator.clipboard.writeText(proof.value.public)
    copied.value = true
  } catch {
    error.value = 'Select the enrollment code and copy it manually.'
  }
}
</script>

<template>
  <div class="ballot-shell page-container">
    <RouterLink class="ballot-back" to="/forms"><AppIcon name="arrow-left" :size="16" /> Forms</RouterLink>
    <header class="ballot-hero">
      <div><h1>Get a voter proof</h1><p>Enrollment for <strong>{{ slug }}</strong></p></div>
    </header>
    <section class="ballot-panel ballot-narrow">
      <div class="ballot-section-heading"><AppIcon name="key" /><h2>Register your eligibility</h2></div>
      <p>Generate a file on this device. Give the organizer the public enrollment code so they can register you before voting opens. Keep the downloaded file private and bring it back to cast your ballot.</p>
      <p v-if="error" class="ballot-message ballot-message--error" role="alert">{{ error }}</p>
      <div v-if="!proof" class="ballot-actions">
        <BaseButton variant="primary" :loading="busy" @click="create">Generate and download proof</BaseButton>
        <BaseButton :href="ballotTool">Use the offline tool</BaseButton>
      </div>
      <div v-else class="ballot-stack">
        <label class="ballot-field">Public enrollment code<input class="input ballot-code" :value="proof.public" readonly @focus="($event.target as HTMLInputElement).select()"></label>
        <div class="ballot-actions">
          <BaseButton variant="primary" @click="copy">
            <template #icon><AppIcon name="copy" :size="16" /></template>
            {{ copied ? 'Copied' : 'Copy enrollment code' }}
          </BaseButton>
          <BaseButton @click="save">Download proof again</BaseButton>
        </div>
        <RouterLink :to="{ name: 'secret-ballot', params: { slug } }">Open the ballot form</RouterLink>
      </div>
    </section>
  </div>
</template>
