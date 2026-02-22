<template>
  <div>
    <div v-if="loading" class="text-zinc-600 text-sm font-body">Loading API keys...</div>
    <div v-else-if="error" class="text-red-400 text-xs font-body">{{ error }}</div>
    <div v-else class="card rounded-md overflow-hidden">
      <div class="px-5 py-3 flex items-center justify-between" style="border-bottom:1px solid var(--border-subtle)">
        <span class="section-label">My API Keys</span>
        <div class="flex items-center gap-3">
          <span class="text-[10px] text-zinc-600 font-body">Your keys are encrypted and only used for your benchmarks</span>
          <button
            @click="addCustomKey()"
            class="text-[10px] font-display tracking-wider uppercase px-2 py-0.5 rounded-sm transition-colors"
            style="color:var(--lime);border:1px solid rgba(191,255,0,0.2)"
          >+ Custom Key</button>
        </div>
      </div>
      <div class="divide-y" style="border-color:var(--border-subtle)">
        <div v-if="keys.length === 0" class="px-5 py-3 text-zinc-700 text-xs font-body">
          No providers configured.
        </div>
        <div
          v-for="k in keys"
          :key="k.provider_key"
          class="px-5 py-3 flex items-center justify-between group"
        >
          <div class="flex items-center gap-3">
            <span class="text-xs font-body text-zinc-300">{{ k.display_name }}</span>
            <span class="text-[10px] font-mono text-zinc-600">{{ k.key_env_name || k.provider_key }}</span>
            <span
              v-if="k.standalone"
              class="text-[9px] font-display tracking-wider uppercase px-1.5 py-0.5 rounded-sm"
              style="background:rgba(251,146,60,0.08);color:#FB923C;border:1px solid rgba(251,146,60,0.2)"
            >STANDALONE</span>
            <span
              v-if="k.has_user_key"
              class="text-[9px] font-display tracking-wider uppercase px-1.5 py-0.5 rounded-sm"
              style="background:rgba(191,255,0,0.08);color:var(--lime);border:1px solid rgba(191,255,0,0.2)"
            >YOUR KEY</span>
            <span
              v-else-if="k.has_global_key"
              class="text-[9px] font-display tracking-wider uppercase px-1.5 py-0.5 rounded-sm"
              style="background:rgba(56,189,248,0.08);color:#38BDF8;border:1px solid rgba(56,189,248,0.2)"
            >SHARED</span>
            <span
              v-else
              class="text-[9px] font-display tracking-wider uppercase px-1.5 py-0.5 rounded-sm"
              style="background:rgba(255,59,92,0.08);color:var(--coral);border:1px solid rgba(255,59,92,0.2)"
            >NOT SET</span>
          </div>
          <div class="flex gap-2">
            <button
              @click="setKey(k)"
              class="text-[10px] font-display tracking-wider uppercase px-2 py-0.5 rounded-sm transition-colors"
              :class="k.has_user_key ? 'text-zinc-500 hover:text-zinc-300' : 'hover:text-zinc-200'"
              :style="k.has_user_key ? '' : 'color:var(--lime);border:1px solid rgba(191,255,0,0.2)'"
            >{{ k.has_user_key ? 'Update' : 'Set Key' }}</button>
            <button
              v-if="k.has_user_key"
              @click="removeKey(k)"
              class="text-[10px] font-display tracking-wider uppercase text-zinc-600 hover:text-red-400 transition-colors"
            >Remove</button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { apiFetch } from '../../utils/api.js'
import { useToast } from '../../composables/useToast.js'
import { useModal } from '../../composables/useModal.js'

const { showToast } = useToast()
const { inputModal, multiFieldModal, confirm } = useModal()

const keys = ref([])
const loading = ref(true)
const error = ref('')

async function loadKeys() {
  loading.value = true
  error.value = ''
  try {
    const res = await apiFetch('/api/keys')
    const data = await res.json()
    keys.value = data.keys || []
  } catch (e) {
    error.value = 'Failed to load API keys.'
  } finally {
    loading.value = false
  }
}

async function setKey(k) {
  const result = await inputModal(
    'Set API Key for ' + k.display_name,
    'Paste your API key...',
    { type: 'password' }
  )
  if (result === null || result === undefined) return
  const value = typeof result === 'object' ? result.value : result
  if (!value) return
  try {
    await apiFetch('/api/keys', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ provider_key: k.provider_key, value }),
    })
    showToast('Key saved', 'success')
    await loadKeys()
  } catch {
    showToast('Failed to save key', 'error')
  }
}

async function removeKey(k) {
  const ok = await confirm(
    'Remove Key',
    'Remove your personal key for <strong>' + k.display_name + '</strong>? You will fall back to the shared key (if one exists).',
    { danger: true, confirmLabel: 'Remove' }
  )
  if (!ok) return
  try {
    await apiFetch('/api/keys', {
      method: 'DELETE',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ provider_key: k.provider_key }),
    })
    showToast('Key removed', 'success')
    await loadKeys()
  } catch {
    showToast('Failed to remove key', 'error')
  }
}

async function addCustomKey() {
  const result = await multiFieldModal('Add Custom API Key', [
    { key: 'provider_key', label: 'Provider Key', placeholder: 'e.g. my_provider', type: 'text' },
    { key: 'key_name', label: 'Key Name (optional)', placeholder: 'e.g. My OpenAI Key', type: 'text' },
    { key: 'value', label: 'API Key Value', placeholder: 'sk-...', type: 'password' },
  ], { confirmLabel: 'Save Key' })
  if (!result) return
  const { provider_key, value } = result
  if (!provider_key || !value) {
    showToast('Provider key and API key value are required', 'error')
    return
  }
  try {
    const body = { provider_key: provider_key.trim(), value }
    if (result.key_name && result.key_name.trim()) {
      body.key_name = result.key_name.trim()
    }
    await apiFetch('/api/keys', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    showToast('Custom key saved', 'success')
    await loadKeys()
  } catch {
    showToast('Failed to save key', 'error')
  }
}

onMounted(loadKeys)
</script>
