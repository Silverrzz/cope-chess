<script setup lang="ts">
import { onMounted, ref } from 'vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import { applicationQuery } from '@/api/application'
import { ApiError } from '@/api/client'
import { useApplicationMutation } from '@/composables/useApplicationMutation'

const props = defineProps<{ formId: string; disabled?: boolean }>()
const emit = defineEmits<{ changed: [] }>()
type Permission = 'read' | 'edit' | 'review' | 'responses'
interface Access { state_version: number; owner_actor: string; grants: { actor: string; permissions: Permission[] }[]; identities: { actor: string; label: string }[] }
const access = ref<Access | null>(null)
const actor = ref('')
const permissions = ref<Permission[]>([])
const mutation = useApplicationMutation()
const error = ref('')
const message = ref('')
const options: { value: Permission; label: string }[] = [{ value: 'read', label: 'Respond to an invited form' }, { value: 'edit', label: 'Edit the form definition' }, { value: 'review', label: 'Review responses' }, { value: 'responses', label: 'Read all responses' }]
async function load(): Promise<void> {
  try { access.value = await applicationQuery<Access>('forms.permissions.v1', {}, props.formId) }
  catch (cause) { if (!(cause instanceof ApiError && cause.status === 403)) error.value = cause instanceof Error ? cause.message : 'Could not load form access.' }
}
function select(): void { permissions.value = [...(access.value?.grants.find((grant) => grant.actor === actor.value)?.permissions ?? [])] }
function label(id: string): string { return access.value?.identities.find((identity) => identity.actor === id)?.label ?? id }
async function save(): Promise<void> {
  if (!access.value || !actor.value) return
  error.value = ''; message.value = ''
  try {
    await mutation.execute<'forms.permissions.set.v1'>({ command: 'forms.permissions.set.v1', target: props.formId, expected_version: access.value.state_version, input: { actor: actor.value, permissions: permissions.value } })
    await load(); emit('changed'); message.value = 'Access updated. Current account and credential scopes still apply.'
  } catch (cause) { error.value = cause instanceof Error ? cause.message : 'Could not update access.' }
}
onMounted(() => load())
</script>

<template>
  <details v-if="access || error" class="access-editor"><summary>Invitations and response access</summary><p v-if="error" role="alert" class="error">{{ error }}</p><template v-if="access"><p>Inviting someone to respond does not grant access to other people’s answers. Each grant also requires the account or credential’s matching scope.</p><ul><li v-for="grant in access.grants" :key="grant.actor"><strong>{{ label(grant.actor) }}</strong><span>{{ grant.permissions.map((permission) => options.find((option) => option.value === permission)?.label).join(', ') }}</span><BaseButton type="button" variant="ghost" size="small" :disabled="disabled || mutation.busy.value || mutation.uncertain.value" @click="actor = grant.actor; select()">Change access</BaseButton></li></ul><form @submit.prevent="save"><fieldset :disabled="disabled || mutation.busy.value || mutation.uncertain.value"><label>Account or credential<select v-model="actor" class="input" required @change="select"><option value="" disabled>Choose an identity</option><option v-for="identity in access.identities.filter((identity) => identity.actor !== access?.owner_actor)" :key="identity.actor" :value="identity.actor">{{ identity.label }}{{ identity.actor.startsWith('token:') ? ` · ${identity.actor.slice(-8)}` : '' }}</option><option v-if="actor && !access.identities.some((identity) => identity.actor === actor)" :value="actor">{{ label(actor) }}</option></select></label><label v-for="option in options" :key="option.value" class="check"><input v-model="permissions" type="checkbox" :value="option.value">{{ option.label }}</label></fieldset><p class="hint">Clear all permissions to revoke this identity’s form access. The owner retains access through ownership.</p><BaseButton type="submit" :disabled="disabled || mutation.busy.value || !actor">{{ mutation.uncertain.value ? 'Retry access update' : 'Save access' }}</BaseButton></form><p v-if="message" role="status">{{ message }}</p></template></details>
</template>

<style scoped>
.access-editor{border:1px solid var(--color-border);border-radius:.7rem;padding:1rem;font-size:.85rem}.access-editor summary{cursor:pointer;font-weight:650}.access-editor ul{list-style:none;padding:0}.access-editor li{border-top:1px solid var(--color-border);display:grid;gap:.4rem;padding:.8rem 0}.access-editor li .base-button{justify-self:start}.access-editor fieldset{border:0;display:grid;gap:.7rem;margin:0;padding:0}.access-editor label{display:grid;gap:.4rem}.access-editor .check{align-items:center;display:flex;gap:.6rem}.hint{color:var(--color-text-muted);font-size:.75rem}.error{color:var(--color-danger)}
</style>
